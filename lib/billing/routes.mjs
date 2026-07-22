// lib/billing/routes.mjs
// Express router for the billing surface. Mount in server.mjs:
//
//   import { createBillingRouter } from './lib/billing/routes.mjs';
//   app.use('/api/billing', createBillingRouter({ requireAuth, findUserById, updateUser }));
//
// The router accepts a tiny dependency object so we don't import the user
// store directly here — keeps the test surface small and lets server.mjs
// stay the single integration point.
//
// Routes:
//   GET  /config    — public; reports {configured, hasWebhookSecret, ...}
//   GET  /me        — auth; current tier, sub status, usage, caps
//   POST /checkout  — auth; { tier: 'pro'|'proIntel' } → {url}
//   POST /portal    — auth; → {url}
//   POST /webhook   — Stripe; signature-verified; updates user records
//
// All four billing endpoints (everything except /config and /webhook)
// gracefully 503 when STRIPE_SECRET_KEY is unset. The webhook 503s the
// same way; Stripe will retry and the operator will see retries piling
// up, which is the loudest possible "you forgot to flip the env var"
// signal.

import express from 'express';
import {
  billingConfigured,
  billingConfigStatus,
  getStripeClient,
  verifyWebhookSignature,
  createCheckoutSession,
  createPortalSession,
  BillingNotConfigured,
  BillingDependencyMissing,
} from './stripe.mjs';
import {
  TIERS,
  TIER_IDS,
  DEFAULT_TIER,
  priceIdForTier,
  tierForPriceId,
  subscriptionGrantsAccess,
} from './tiers.mjs';
import { readUsage } from './quotas.mjs';
import { errorTracker } from '../observability/errorTracker.mjs'; // R-F2605 — brain-wire billing failures

export function createBillingRouter({ requireAuth, findUserById, updateUser, listUsers }) {
  if (!requireAuth || !findUserById || !updateUser || !listUsers) {
    throw new Error('createBillingRouter: missing requireAuth/findUserById/updateUser/listUsers');
  }
  const router = express.Router();

  // ── Config (public; the FE uses this to decide whether to show
  // "Upgrade" buttons or render a graceful "billing coming soon" state). ──
  router.get('/config', (req, res) => {
    const status = billingConfigStatus();
    res.json({
      ...status,
      // Surface tier metadata so the FE doesn't have to hardcode prices /
      // labels. Returns even when Stripe is unconfigured so the pricing
      // page can render in a preview state.
      tiers: TIER_IDS.map(id => {
        const t = TIERS[id];
        return {
          id: t.id,
          label: t.label,
          priceAmount: t.priceAmount,
          currency: t.currency,   // R-F2880 — explicit, so a price can be checked not assumed
          messagesPerDay: t.messagesPerDay,
          ddRunsPerMonth: t.ddRunsPerMonth,
          uploadBytesMax: t.uploadBytesMax,
          deepResearchEnabled: t.deepResearchEnabled,
          autonomousEnabled: t.autonomousEnabled,
          publicApiEnabled: t.publicApiEnabled,
          // Per-tier readiness: if the price ID env is unset, the FE can
          // grey out that tier with "configuration pending".
          checkoutReady: !!priceIdForTier(t.id),
        };
      }),
    });
  });

  // ── Auth-required helpers ─────────────────────────────────────────────
  router.get('/me', requireAuth, (req, res) => {
    const userId = req.user?.userId;
    if (!userId) return res.status(401).json({ error: 'auth required' });
    const user = findUserById(userId);
    if (!user) return res.status(404).json({ error: 'user not found' });
    const tierId = user.tier || DEFAULT_TIER;
    const tier = TIERS[tierId] || TIERS[DEFAULT_TIER];
    readUsage(userId, tierId).then(usage => {
      res.json({
        tier: tierId,
        tierLabel: tier.label,
        priceAmount: tier.priceAmount,
        currency: tier.currency,   // R-F2880
        subscriptionStatus: user.subscriptionStatus || null,
        subscriptionPeriodEnd: user.subscriptionPeriodEnd || null,
        cancelAtPeriodEnd: !!user.cancelAtPeriodEnd,
        billingActive: subscriptionGrantsAccess(user.subscriptionStatus),
        usage,
        capabilities: {
          deepResearch: tier.deepResearchEnabled,
          autonomous: tier.autonomousEnabled,
          publicApi: tier.publicApiEnabled,
          monthlyCostCapUsd: tier.monthlyCostCapUsd,
        },
      });
    }).catch(err => {
      console.error('[Billing] /me usage read failed:', err);
      res.status(500).json({ error: 'usage read failed' });
    });
  });

  router.post('/checkout', requireAuth, async (req, res) => {
    if (!billingConfigured()) {
      return res.status(503).json({ error: 'billing not configured' });
    }
    const userId = req.user?.userId;
    if (!userId) return res.status(401).json({ error: 'auth required' });
    const user = findUserById(userId);
    if (!user) return res.status(404).json({ error: 'user not found' });

    const requestedTier = (req.body?.tier || '').toString().trim();
    if (!TIER_IDS.includes(requestedTier) || requestedTier === DEFAULT_TIER) {
      return res.status(400).json({ error: 'invalid tier — must be a paid tier id' });
    }
    const priceId = priceIdForTier(requestedTier);
    if (!priceId) {
      return res.status(503).json({
        error: `tier ${requestedTier} not configured (set ${TIERS[requestedTier].stripePriceEnv})`,
      });
    }

    const successUrl = (process.env.STRIPE_CHECKOUT_RETURN_URL || '').trim()
      || `${req.protocol}://${req.get('host')}/account.html?checkout=ok`;
    const cancelUrl = (process.env.STRIPE_CHECKOUT_CANCEL_URL || '').trim()
      || `${req.protocol}://${req.get('host')}/account.html?checkout=cancelled`;

    try {
      const session = await createCheckoutSession(user, {
        priceId,
        successUrl,
        cancelUrl,
        updateUser,
      });
      res.json({ url: session.url, sessionId: session.id });
    } catch (err) {
      if (err instanceof BillingNotConfigured || err instanceof BillingDependencyMissing) {
        return res.status(503).json({ error: err.message, code: err.code });
      }
      console.error('[Billing] checkout error:', err);
      errorTracker.record('billing', 'checkout_error', err, null, { userId }); // R-F2605
      res.status(500).json({ error: 'checkout failed' });
    }
  });

  router.post('/portal', requireAuth, async (req, res) => {
    if (!billingConfigured()) {
      return res.status(503).json({ error: 'billing not configured' });
    }
    const userId = req.user?.userId;
    if (!userId) return res.status(401).json({ error: 'auth required' });
    const user = findUserById(userId);
    if (!user) return res.status(404).json({ error: 'user not found' });
    if (!user.stripeCustomerId) {
      return res.status(400).json({ error: 'no active subscription — start with /checkout first' });
    }

    const returnUrl = (process.env.STRIPE_PORTAL_RETURN_URL || '').trim()
      || `${req.protocol}://${req.get('host')}/account.html`;

    try {
      const session = await createPortalSession(user, { returnUrl });
      res.json({ url: session.url });
    } catch (err) {
      if (err.code === 'NO_STRIPE_CUSTOMER') {
        return res.status(400).json({ error: err.message });
      }
      if (err instanceof BillingNotConfigured || err instanceof BillingDependencyMissing) {
        return res.status(503).json({ error: err.message, code: err.code });
      }
      console.error('[Billing] portal error:', err);
      errorTracker.record('billing', 'portal_error', err, null, { userId }); // R-F2605
      res.status(500).json({ error: 'portal failed' });
    }
  });

  // ── Webhook ───────────────────────────────────────────────────────────
  // Mounted with express.raw() body parser by server.mjs BEFORE the global
  // express.json() — Stripe signs the raw body, so a JSON-parsed body will
  // fail signature verification. server.mjs handles the parser ordering.
  router.post('/webhook', async (req, res) => {
    if (!billingConfigured()) {
      return res.status(503).send('billing not configured');
    }
    const sig = req.headers['stripe-signature'];
    if (!sig) return res.status(400).send('missing stripe-signature');
    let event;
    try {
      event = await verifyWebhookSignature(req.body, sig);
    } catch (err) {
      console.warn('[Billing] webhook signature verification failed:', err.message);
      errorTracker.record('billing', 'webhook_signature_invalid', err); // R-F2605 — security-relevant (spoofed/misconfigured webhook)
      return res.status(400).send(`signature verification failed: ${err.message}`);
    }

    try {
      await _handleEvent(event, { findUserById, updateUser, listUsers });
      res.json({ received: true });
    } catch (err) {
      console.error('[Billing] webhook handler error:', err);
      errorTracker.record('billing', 'webhook_handler_error', err, null, { eventType: event?.type }); // R-F2605
      // 500 → Stripe retries with backoff. That's the right behaviour for
      // transient failures (e.g. Redis unavailable). Permanent failures
      // will retry and persist in the Stripe dashboard for the operator.
      res.status(500).send('handler error');
    }
  });

  return router;
}

// ── Event dispatcher ────────────────────────────────────────────────────
// Handles the subset of Stripe events we care about. Other events are
// acknowledged silently — adding more handlers later is a small change.
//
// Source-of-truth fields written to the user record:
//   tier                        — 'free' | 'pro' | 'proIntel'
//   stripeCustomerId            — string
//   stripeSubscriptionId        — string | null
//   subscriptionStatus          — Stripe sub status string
//   subscriptionPeriodEnd       — ISO timestamp
//   cancelAtPeriodEnd           — boolean
async function _handleEvent(event, { findUserById, updateUser, listUsers }) {
  const type = event.type;
  const obj = event.data?.object || {};

  switch (type) {
    case 'checkout.session.completed': {
      // Customer just finished signup — link the customer to our user.
      const userId = obj.metadata?.crucix_user_id;
      if (!userId) {
        console.warn('[Billing] checkout.session.completed without crucix_user_id metadata');
        return;
      }
      const user = findUserById(userId);
      if (!user) {
        console.warn('[Billing] checkout.session.completed for unknown user:', userId);
        return;
      }
      const updates = {};
      if (obj.customer && !user.stripeCustomerId) updates.stripeCustomerId = obj.customer;
      if (obj.subscription) updates.stripeSubscriptionId = obj.subscription;
      if (Object.keys(updates).length) updateUser(userId, updates);
      console.log(`[Billing] checkout.session.completed for user ${userId}`);
      return;
    }

    case 'customer.subscription.created':
    case 'customer.subscription.updated': {
      const userId = obj.metadata?.crucix_user_id
        || _userIdForCustomer(obj.customer, { listUsers });
      if (!userId) {
        console.warn(`[Billing] ${type} could not resolve crucix_user_id`);
        return;
      }
      const priceId = obj.items?.data?.[0]?.price?.id;
      const tier = tierForPriceId(priceId) || DEFAULT_TIER;
      const periodEnd = obj.current_period_end
        ? new Date(obj.current_period_end * 1000).toISOString()
        : null;
      updateUser(userId, {
        tier: subscriptionGrantsAccess(obj.status) ? tier : DEFAULT_TIER,
        stripeSubscriptionId: obj.id,
        subscriptionStatus: obj.status,
        subscriptionPeriodEnd: periodEnd,
        cancelAtPeriodEnd: !!obj.cancel_at_period_end,
      });
      console.log(`[Billing] ${type} → user ${userId} tier=${tier} status=${obj.status}`);
      return;
    }

    case 'customer.subscription.deleted': {
      const userId = obj.metadata?.crucix_user_id
        || _userIdForCustomer(obj.customer, { listUsers });
      if (!userId) return;
      updateUser(userId, {
        tier: DEFAULT_TIER,
        stripeSubscriptionId: null,
        subscriptionStatus: 'canceled',
        cancelAtPeriodEnd: false,
      });
      console.log(`[Billing] customer.subscription.deleted → user ${userId} → free`);
      return;
    }

    case 'invoice.payment_failed': {
      const userId = obj.metadata?.crucix_user_id
        || _userIdForCustomer(obj.customer, { listUsers });
      if (!userId) return;
      // Don't immediately downgrade — Stripe handles retries and will fire
      // customer.subscription.updated with status=past_due / unpaid /
      // canceled depending on retry policy. Just log the signal.
      console.warn(`[Billing] invoice.payment_failed for user ${userId} — Stripe will retry`);
      errorTracker.record('billing', 'invoice_payment_failed', null, null, { userId, customerId: obj.customer, invoiceId: obj.id }); // R-F2605
      return;
    }

    default:
      // Unhandled events are normal; Stripe sends many. Log at debug only.
      return;
  }
}

// Reverse-lookup a user from a stripeCustomerId. Slow (linear scan over
// users) but only fires on the rare event types where metadata is missing
// (e.g. customers created in the Stripe dashboard rather than via our
// checkout flow). Returns null if no user matches.
function _userIdForCustomer(customerId, { listUsers }) {
  if (!customerId) return null;
  try {
    const users = listUsers();
    const hit = users.find(u => u.stripeCustomerId === customerId);
    return hit ? hit.id : null;
  } catch (err) {
    console.warn('[Billing] _userIdForCustomer scan failed:', err.message);
    return null;
  }
}

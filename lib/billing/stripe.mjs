// lib/billing/stripe.mjs
// Stripe client wrapper — lazily imports `stripe` ONLY when the billing
// surface is enabled (STRIPE_SECRET_KEY set). When disabled, this module
// loads with zero side effects and every call returns a `{configured:false}`
// shape so the rest of the codebase can branch cleanly without try/catch.
//
// The lazy-import pattern matches the soft-rollout strategy used elsewhere
// in this codebase (e.g. ARIA_API_TOKEN auth, Anthropic billing, etc):
//   - default state: feature is OFF, behaviour identical to today
//   - operator sets STRIPE_SECRET_KEY (+ webhook + price IDs) → feature ON
//   - no migration step, no big-bang switch, no risk if the keys leak
//
// REQUIRED env vars when enabling:
//   STRIPE_SECRET_KEY           — sk_live_… or sk_test_…
//   STRIPE_WEBHOOK_SECRET       — whsec_… (from the Stripe webhook config)
//   STRIPE_PRICE_PRO            — price_… for the $20/mo Pro plan
//   STRIPE_PRICE_PROINTEL       — price_… for the $199/mo Pro Intelligence
//   STRIPE_CHECKOUT_RETURN_URL  — e.g. https://aria.app/account?checkout=ok
//   STRIPE_PORTAL_RETURN_URL    — e.g. https://aria.app/account
//
// All five must be set for full functionality. /api/billing/config reports
// which are present so the FE can show a degraded-but-honest "billing not
// configured" state during partial rollouts.

let _client = null;
let _clientInitFailed = false;

export function billingConfigured() {
  return !!(process.env.STRIPE_SECRET_KEY || '').trim();
}

export function billingConfigStatus() {
  const env = process.env;
  return {
    configured: billingConfigured(),
    hasWebhookSecret: !!(env.STRIPE_WEBHOOK_SECRET || '').trim(),
    hasPricePro: !!(env.STRIPE_PRICE_PRO || '').trim(),
    hasPriceProIntel: !!(env.STRIPE_PRICE_PROINTEL || '').trim(),
    hasCheckoutReturnUrl: !!(env.STRIPE_CHECKOUT_RETURN_URL || '').trim(),
    hasPortalReturnUrl: !!(env.STRIPE_PORTAL_RETURN_URL || '').trim(),
  };
}

// Lazy stripe import. Throws BillingNotConfigured if STRIPE_SECRET_KEY is
// unset; throws BillingDependencyMissing if STRIPE_SECRET_KEY is set but
// the `stripe` npm package isn't installed. Callers should treat both as
// 503 conditions.
export class BillingNotConfigured extends Error {
  constructor() { super('Stripe is not configured (STRIPE_SECRET_KEY unset)'); this.code = 'BILLING_NOT_CONFIGURED'; }
}
export class BillingDependencyMissing extends Error {
  constructor(detail) { super('Stripe SDK not installed: ' + detail); this.code = 'BILLING_DEPENDENCY_MISSING'; }
}

export async function getStripeClient() {
  if (!billingConfigured()) throw new BillingNotConfigured();
  if (_client) return _client;
  if (_clientInitFailed) {
    throw new BillingDependencyMissing(
      'previous import failed; install with `npm install stripe` and restart',
    );
  }
  let StripeMod;
  try {
    StripeMod = (await import('stripe')).default;
  } catch (err) {
    _clientInitFailed = true;
    throw new BillingDependencyMissing(err.message);
  }
  _client = new StripeMod(process.env.STRIPE_SECRET_KEY.trim(), {
    apiVersion: '2024-06-20',
    typescript: false,
    appInfo: { name: 'crucix-aria', version: '1.0.0' },
  });
  return _client;
}

// Verify a webhook signature. Throws on bad signature or when stripe isn't
// configured. Returns the parsed event object.
export async function verifyWebhookSignature(rawBody, signatureHeader) {
  const client = await getStripeClient();
  const secret = (process.env.STRIPE_WEBHOOK_SECRET || '').trim();
  if (!secret) {
    throw new Error('STRIPE_WEBHOOK_SECRET unset — cannot verify webhook');
  }
  // constructEvent throws StripeSignatureVerificationError on tampering
  return client.webhooks.constructEvent(rawBody, signatureHeader, secret);
}

// Create or retrieve a Stripe customer for this user. We persist the
// stripeCustomerId on the user record so we don't create duplicates.
export async function ensureCustomer(user, { updateUser }) {
  const client = await getStripeClient();
  if (user.stripeCustomerId) {
    try {
      const existing = await client.customers.retrieve(user.stripeCustomerId);
      if (existing && !existing.deleted) return existing;
    } catch (err) {
      // Fall through and create a new customer if the old one was deleted.
      console.warn('[Billing] stripe.customers.retrieve failed, creating fresh:', err.message);
    }
  }
  const created = await client.customers.create({
    email: user.email,
    name: user.fullName || user.username,
    metadata: { crucix_user_id: user.id },
  });
  await updateUser(user.id, { stripeCustomerId: created.id });
  return created;
}

export async function createCheckoutSession(user, { priceId, successUrl, cancelUrl, updateUser }) {
  const client = await getStripeClient();
  const customer = await ensureCustomer(user, { updateUser });
  return client.checkout.sessions.create({
    customer: customer.id,
    mode: 'subscription',
    payment_method_types: ['card'],
    line_items: [{ price: priceId, quantity: 1 }],
    success_url: successUrl,
    cancel_url: cancelUrl,
    allow_promotion_codes: true,
    billing_address_collection: 'auto',
    metadata: { crucix_user_id: user.id },
    subscription_data: {
      metadata: { crucix_user_id: user.id },
    },
  });
}

export async function createPortalSession(user, { returnUrl }) {
  const client = await getStripeClient();
  if (!user.stripeCustomerId) {
    const err = new Error('user has no stripe customer — cannot open portal');
    err.code = 'NO_STRIPE_CUSTOMER';
    throw err;
  }
  return client.billingPortal.sessions.create({
    customer: user.stripeCustomerId,
    return_url: returnUrl,
  });
}

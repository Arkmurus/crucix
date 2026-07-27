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
//   STRIPE_PRICE_PRO            — price_… for the £79/mo Essentials plan
//   STRIPE_PRICE_PROINTEL       — price_… for the £199/mo Pro Intelligence
//   STRIPE_CHECKOUT_RETURN_URL  — e.g. https://aria.app/account?checkout=ok
//   STRIPE_CHECKOUT_CANCEL_URL  — e.g. https://aria.app/account?checkout=cancel
//   STRIPE_PORTAL_RETURN_URL    — e.g. https://aria.app/account
//
// All seven must be set for full functionality. /api/billing/config reports
// which are present so the FE can show a degraded-but-honest "billing not
// configured" state during partial rollouts.

let _client = null;
let _clientInitFailed = false;

export function billingConfigured() {
  return !!(process.env.STRIPE_SECRET_KEY || '').trim();
}

export function billingConfigStatus() {
  const env = process.env;
  const required = {
    STRIPE_SECRET_KEY: !!(env.STRIPE_SECRET_KEY || '').trim(),
    STRIPE_WEBHOOK_SECRET: !!(env.STRIPE_WEBHOOK_SECRET || '').trim(),
    STRIPE_PRICE_PRO: !!(env.STRIPE_PRICE_PRO || '').trim(),
    STRIPE_PRICE_PROINTEL: !!(env.STRIPE_PRICE_PROINTEL || '').trim(),
    STRIPE_CHECKOUT_RETURN_URL: !!(env.STRIPE_CHECKOUT_RETURN_URL || '').trim(),
    STRIPE_CHECKOUT_CANCEL_URL: !!(env.STRIPE_CHECKOUT_CANCEL_URL || '').trim(),
    STRIPE_PORTAL_RETURN_URL: !!(env.STRIPE_PORTAL_RETURN_URL || '').trim(),
  };
  const missing = Object.entries(required)
    .filter(([, present]) => !present)
    .map(([name]) => name);
  return {
    configured: missing.length === 0,
    hasSecretKey: required.STRIPE_SECRET_KEY,
    hasWebhookSecret: required.STRIPE_WEBHOOK_SECRET,
    hasPricePro: required.STRIPE_PRICE_PRO,
    hasPriceProIntel: required.STRIPE_PRICE_PROINTEL,
    hasCheckoutReturnUrl: required.STRIPE_CHECKOUT_RETURN_URL,
    hasCheckoutCancelUrl: required.STRIPE_CHECKOUT_CANCEL_URL,
    hasPortalReturnUrl: required.STRIPE_PORTAL_RETURN_URL,
    missing,
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
export async function ensureCustomer(user, { updateUser, client: suppliedClient = null }) {
  const client = suppliedClient || await getStripeClient();
  if (user.stripeCustomerId) {
    try {
      const existing = await client.customers.retrieve(user.stripeCustomerId);
      if (existing && !existing.deleted) return existing;
    } catch (err) {
      // Only a confirmed missing Customer permits replacement. Treating a
      // timeout or Stripe outage as deletion creates duplicate customers.
      if (err?.code !== 'resource_missing' && err?.statusCode !== 404) throw err;
    }
  }
  const created = await client.customers.create(
    {
      email: user.email,
      name: user.fullName || user.username,
      metadata: { crucix_user_id: user.id },
    },
    { idempotencyKey: `crucix-customer-${user.id}` },
  );
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

// Stripe is the authority for what a Price actually charges. Environment
// variable names are not proof that an ID points at the advertised amount.
export async function validateRecurringPrice(priceId, tier) {
  const client = await getStripeClient();
  const price = await client.prices.retrieve(priceId);
  const expectedAmount = tier.priceAmount * 100;
  const expectedCurrency = tier.currency.toLowerCase();
  if (!price.active
      || price.type !== 'recurring'
      || price.recurring?.interval !== 'month'
      || price.unit_amount !== expectedAmount
      || price.currency !== expectedCurrency) {
    const err = new Error(
      `Stripe price ${priceId} does not match ${tier.id}: expected `
      + `${expectedCurrency.toUpperCase()} ${tier.priceAmount}/month`,
    );
    err.code = 'STRIPE_PRICE_MISMATCH';
    throw err;
  }
  return price;
}

export async function retrieveSubscription(subscriptionId) {
  const client = await getStripeClient();
  return client.subscriptions.retrieve(subscriptionId);
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

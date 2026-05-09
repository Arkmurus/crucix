// lib/billing/tiers.mjs
// Tier definitions — single source of truth for what each subscription tier
// can access. Maps to docs/chat_ui_launch_decisions.md §1+§2.
//
// Three tiers at launch:
//   free     — funnel; signup required; 10 msg/day; no DD; no public API.
//   pro      — $20/mo; 200 msg/day; 20 DD/month; 5 MB upload; no autonomous.
//   proIntel — $199/mo; 2000 msg/day; 100 DD/month; 50 MB; full autonomous.
//
// The price IDs are NOT hardcoded — they come from STRIPE_PRICE_PRO and
// STRIPE_PRICE_PROINTEL env vars. This module never imports stripe; it is
// pure config that the rest of the billing surface and the chat-quota
// middleware read from.
//
// Field semantics:
//   messagesPerDay        — chat turns / 24h rolling window
//   ddRunsPerMonth        — counterparty DD orchestrator runs / calendar month
//   uploadBytesMax        — single-file upload size limit
//   uploadsPerDay         — total uploads / 24h
//   deepResearchEnabled   — gate on /chat with deep_research tool
//   autonomousEnabled     — gate on autonomous engine task subscriptions
//   publicApiEnabled      — /api/v1/* surface (Lifter #5; not built yet)
//   monthlyCostCapUsd     — per-account spend cap for autonomous tier
//
// The free-tier defaults are deliberately conservative; the launch-decisions
// doc lists 'no DD' explicitly because each DD costs ~$0.50 in LLM spend.

export const TIERS = Object.freeze({
  free: {
    id: 'free',
    label: 'Free',
    priceUsd: 0,
    stripePriceEnv: null,
    messagesPerDay: 10,
    ddRunsPerMonth: 0,
    uploadBytesMax: 1 * 1024 * 1024,        // 1 MB
    uploadsPerDay: 3,
    deepResearchEnabled: false,
    autonomousEnabled: false,
    publicApiEnabled: false,
    monthlyCostCapUsd: 0,
  },
  pro: {
    id: 'pro',
    label: 'Pro',
    priceUsd: 20,
    stripePriceEnv: 'STRIPE_PRICE_PRO',
    messagesPerDay: 200,
    ddRunsPerMonth: 20,
    uploadBytesMax: 5 * 1024 * 1024,        // 5 MB
    uploadsPerDay: 30,
    deepResearchEnabled: false,
    autonomousEnabled: false,
    publicApiEnabled: false,
    monthlyCostCapUsd: 0,
  },
  proIntel: {
    id: 'proIntel',
    label: 'Professional Intelligence',
    priceUsd: 199,
    stripePriceEnv: 'STRIPE_PRICE_PROINTEL',
    messagesPerDay: 2000,
    ddRunsPerMonth: 100,
    uploadBytesMax: 50 * 1024 * 1024,       // 50 MB
    uploadsPerDay: 200,
    deepResearchEnabled: true,
    autonomousEnabled: true,
    publicApiEnabled: true,
    monthlyCostCapUsd: 100,
  },
});

export const TIER_IDS = Object.freeze(Object.keys(TIERS));

// Default tier for any user without an explicit subscription. Existing users
// created before this scaffold landed do not have a `tier` field; treat them
// as free at read time without persisting until they interact with billing.
export const DEFAULT_TIER = 'free';

export function getTier(tierId) {
  return TIERS[tierId] || TIERS[DEFAULT_TIER];
}

// Map a Stripe price ID back to our internal tier id. Returns null if the
// price doesn't correspond to a configured tier (defensive against webhook
// events for legacy / experimental prices).
export function tierForPriceId(priceId) {
  if (!priceId) return null;
  for (const tier of Object.values(TIERS)) {
    const env = tier.stripePriceEnv;
    if (!env) continue;
    const configured = (process.env[env] || '').trim();
    if (configured && configured === priceId) return tier.id;
  }
  return null;
}

// Map a tier id to its currently configured Stripe price ID. Returns null
// when STRIPE_PRICE_<TIER> is unset (which is also the soft-rollout state
// where Stripe is disabled entirely).
export function priceIdForTier(tierId) {
  const tier = TIERS[tierId];
  if (!tier || !tier.stripePriceEnv) return null;
  return (process.env[tier.stripePriceEnv] || '').trim() || null;
}

// Is the user's subscription active for capability decisions? `active`,
// `trialing`, and `past_due` (with grace period) all grant access; `unpaid`,
// `canceled`, `incomplete`, etc do not. This matches Stripe's recommended
// sub-status semantics.
export function subscriptionGrantsAccess(status) {
  if (!status) return false;
  return ['active', 'trialing', 'past_due'].includes(status);
}

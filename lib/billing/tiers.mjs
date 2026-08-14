// lib/billing/tiers.mjs
// Tier definitions — single source of truth for what each subscription tier
// can access. Maps to docs/chat_ui_launch_decisions.md §1+§2.
//
// Three tiers at launch:
//   free     — acquisition funnel; signup required; 50 msg/day; 5 DD/month so a
//              prospect can experience the flagship DD product; deep research on;
//              no autonomous; no public API. (R-F2753)
//   pro      — £79/mo; 200 msg/day; 20 DD/month; 25 MB upload; no autonomous.
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
// R-F2753 — free tier repurposed as the customer-acquisition funnel. The prior
// defaults (10 msg/day, 0 DD) blocked prospects from ever experiencing the
// flagship DD product, which is self-defeating for acquisition. Each DD costs
// ~$0.50 in LLM spend, so 5 DD/month is ~$2.50 max per free account; the global
// $300/mo LLM cap (CLAUDE.md §17) and the ddRunsPerMonth quota bound total
// exposure. deep research is on to showcase depth; autonomous stays off (that is
// the paid moat).

export const TIERS = Object.freeze({
  free: {
    id: 'free',
    label: 'Free',
    priceAmount: 0,
    currency: 'GBP',
    stripePriceEnv: null,
    messagesPerDay: 50,
    ddRunsPerMonth: 5,
    // R-F4020 (C-94) - raised 5 MB to 25 MB. This RESTORES what was already
    // possible: before R-F3988 made the cap tier-aware the route enforced a flat
    // 25 MB for every caller, so free accounts have been able to upload 25 MB all
    // along. Enforcing the advertised 5 MB would have been the first change in
    // this workstream to take a capability AWAY from users, which is why the
    // operator chose to raise the figure instead.
    //
    // No new exposure: the load this permits is the load the platform has been
    // carrying. The commercial lever on uploads is uploadsPerDay (15/30/200), not
    // file size - size is a technical capability, and gating it below what the
    // system already serves would be an invented restriction.
    uploadBytesMax: 25 * 1024 * 1024,       // 25 MB
    uploadsPerDay: 15,
    deepResearchEnabled: true,
    // R-F3995 (C-76) — operator direction 2026-08-14: autonomous is available
    // ACROSS ALL USERS. See the note on proIntel below for why this is a
    // correction rather than a giveaway.
    autonomousEnabled: true,
    publicApiEnabled: false,
    monthlyCostCapUsd: 0,
  },
  pro: {
    id: 'pro',
    // R-F2314 — label/price match the landing page (public/index.html): Essentials £79.
    // R-F2880 — the amount was previously held in a field named `priceUsd` while
    // containing GBP, and account.html rendered it with a hardcoded '£'. The
    // currency is now EXPLICIT, so the Stripe Price and the advertised figure can
    // be checked against each other instead of assumed.
    // ⚠️ STRIPE_PRICE_PRO must be a GBP recurring price. A USD price here charges
    // ~20% less than the site advertises, on every subscription.
    label: 'Essentials',
    priceAmount: 79,
    currency: 'GBP',
    stripePriceEnv: 'STRIPE_PRICE_PRO',
    messagesPerDay: 200,
    ddRunsPerMonth: 20,
    // R-F4020 (C-94) - raised 5 MB to 25 MB, matching free. See the note there.
    // Deliberately NOT differentiated by size: pro's advantage over free on
    // uploads is the daily COUNT (30 vs 15). Setting pro below 25 MB would reduce
    // what pro users could already do, and setting free below it would do the
    // same to them. Monotonicity holds (free <= pro <= proIntel).
    uploadBytesMax: 25 * 1024 * 1024,       // 25 MB
    uploadsPerDay: 30,
    // R-F3990 (C-75) — was `false`, so upgrading from FREE to Essentials REMOVED
    // deep research: a paying customer got less than a free one. Nothing caught
    // it because nothing compared the tiers to each other.
    //
    // Corrected UPWARDS deliberately. Free is documented two blocks above as
    // having deep research on "to showcase depth", and the landing page sells
    // Essentials as "For focused research and due diligence" — so the £79 tier
    // lacking it was the outlier, not the intent. Levelling the other way would
    // have withdrawn a capability from existing free accounts to make a test
    // green, which is a commercial decision and not one a fix may take.
    //
    // It also makes the flag UNIFORM across all three tiers, which matters: a
    // capability that varies is a promise, and this one has no enforcement call
    // site anywhere (only publicApiEnabled does). A uniform flag gates nothing
    // by construction, so it can no longer misdescribe what a customer bought.
    deepResearchEnabled: true,
    // R-F3995 (C-76) — see free/proIntel. Autonomous is available to every tier.
    autonomousEnabled: true,
    publicApiEnabled: false,
    monthlyCostCapUsd: 0,
  },
  proIntel: {
    id: 'proIntel',
    label: 'Pro Intel',   // R-F2314 — match landing (Pro Intel £199)
    priceAmount: 199,
    currency: 'GBP',   // R-F2880 — STRIPE_PRICE_PROINTEL must be a GBP price
    stripePriceEnv: 'STRIPE_PRICE_PROINTEL',
    messagesPerDay: 2000,
    ddRunsPerMonth: 100,
    uploadBytesMax: 50 * 1024 * 1024,       // 50 MB
    uploadsPerDay: 200,
    deepResearchEnabled: true,
    // R-F3995 (C-76) — autonomous is now TRUE on every tier, so this flag no
    // longer varies. That is a correction, not a giveaway, and the distinction
    // matters because the comment above still calls autonomous "the paid moat".
    //
    // It never gated anything. R-F3990 established that the ONLY enforced
    // capability flag in the tree is publicApiEnabled; autonomousEnabled was
    // read solely to be DISPLAYED by /api/billing/me and rendered by
    // account.html. So free and pro users were shown "autonomous: ✗" describing
    // a restriction that did not exist, while proIntel customers were shown a
    // differentiator they were not actually being given — the entitlement
    // matrix was wrong in both directions at once, which is the same shape as
    // the upload cap in C-73.
    //
    // It also could not have been enforced as written. The autonomous engine is
    // ONE GLOBAL LOOP (verified live 2026-08-14: enabled, running, L3, 98 tasks
    // loaded), not a per-account subscription — there is no per-user unit to
    // gate. Gating it per tier would have meant either building per-account
    // autonomy or degrading the shared loop for some users, and the second would
    // limit what ARIA can do for everyone in order to honour a label.
    //
    // Operator direction 2026-08-14: make it available across all users. That
    // resolves the honesty problem in the direction that removes no capability
    // from anyone: nobody loses access, the displayed matrix becomes true, and
    // a uniform flag cannot misdescribe what a customer bought. Cost exposure is
    // unchanged — spend is bounded by the §17 monthly cap and by the per-tier
    // message/DD/upload counters, which ARE enforced.
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

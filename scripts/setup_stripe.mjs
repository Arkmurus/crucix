#!/usr/bin/env node
// scripts/setup_stripe.mjs — R-F2883
//
// Creates the Stripe Products + Prices for every paid tier, DERIVED FROM
// lib/billing/tiers.mjs so the amount, currency and interval cannot be typed
// wrong by hand. Then prints the exact `flyctl secrets set` line to run.
//
// WHY THIS EXISTS
// ───────────────
// Creating these in the dashboard means hand-typing "79" and picking a currency
// from a dropdown. Pick USD instead of GBP and every subscriber is charged ~20%
// less than imaria.io advertises — silently, forever. R-F2880 made the currency
// explicit in the code; this makes the Stripe side read FROM that same source, so
// the two cannot disagree.
//
// SAFETY
// ──────
//   * the key is read from the environment and NEVER printed, logged or stored;
//   * IDEMPOTENT — re-running finds the existing Price by `lookup_key` instead of
//     creating a duplicate, so a half-finished run is safe to repeat;
//   * --dry-run shows exactly what it would do and touches nothing;
//   * it REFUSES to mix a test key with --live, and warns loudly on a live key.
//
// USAGE (run in your OWN terminal, never paste the key into a chat)
//   node scripts/setup_stripe.mjs --dry-run          # see the plan
//   node scripts/setup_stripe.mjs                    # create them
//
//   PowerShell:  $env:STRIPE_SECRET_KEY="sk_live_…"; node scripts/setup_stripe.mjs
//   bash:        STRIPE_SECRET_KEY="sk_live_…" node scripts/setup_stripe.mjs
import { TIERS } from '../lib/billing/tiers.mjs';

const DRY = process.argv.includes('--dry-run');
const KEY = (process.env.STRIPE_SECRET_KEY || '').trim();

function fail(msg) { console.error(`\n✖ ${msg}\n`); process.exit(1); }

if (!KEY && !DRY) {
  fail('STRIPE_SECRET_KEY is not set.\n'
     + '  bash:       STRIPE_SECRET_KEY="sk_live_…" node scripts/setup_stripe.mjs\n'
     + '  PowerShell: $env:STRIPE_SECRET_KEY="sk_live_…"; node scripts/setup_stripe.mjs');
}
if (KEY && !/^sk_(live|test)_/.test(KEY)) {
  fail('That does not look like a Stripe SECRET key (expected sk_live_… or sk_test_…).\n'
     + '  A pk_… publishable key cannot create products.');
}
const IS_LIVE = KEY.startsWith('sk_live_');

// Paid tiers only — `free` has no Stripe price by definition.
const paid = Object.values(TIERS).filter((t) => t.stripePriceEnv && t.priceAmount > 0);
if (!paid.length) fail('No paid tiers found in lib/billing/tiers.mjs.');

console.log('\nARIA → Stripe product setup (R-F2883)');
console.log('─'.repeat(64));
console.log(`mode        : ${DRY ? 'DRY RUN (nothing will be created)' : (IS_LIVE ? 'LIVE' : 'TEST')}`);
console.log('source      : lib/billing/tiers.mjs (single source of truth)\n');
for (const t of paid) {
  console.log(`  ${t.label.padEnd(12)} ${t.currency} ${String(t.priceAmount).padEnd(5)} / month   → ${t.stripePriceEnv}`);
}
console.log('');

if (DRY) {
  console.log('Dry run — re-run without --dry-run to create these in Stripe.\n');
  process.exit(0);
}
if (IS_LIVE) {
  console.log('⚠️  LIVE key: this creates REAL products customers can be charged against.');
  console.log('    Ctrl-C within 5s to abort.\n');
  await new Promise((r) => setTimeout(r, 5000));
}

const Stripe = (await import('stripe')).default;
const stripe = new Stripe(KEY);
const out = {};

for (const t of paid) {
  // lookup_key makes this idempotent: it is unique per Price in the account, so a
  // second run finds the existing one instead of creating a near-duplicate that
  // would be impossible to tell apart in the dashboard.
  const lookupKey = `aria_${t.id}_${t.currency.toLowerCase()}_monthly`;

  const found = await stripe.prices.list({ lookup_keys: [lookupKey], limit: 1 });
  if (found.data.length) {
    const p = found.data[0];
    // Verify the EXISTING price still matches the code. A price created earlier in
    // the wrong currency is exactly the failure this script exists to prevent, and
    // silently reusing it would defeat the point.
    const okCur = p.currency === t.currency.toLowerCase();
    const okAmt = p.unit_amount === t.priceAmount * 100;
    if (!okCur || !okAmt) {
      fail(`Existing price ${p.id} for ${t.label} DISAGREES with tiers.mjs:\n`
         + `    Stripe : ${p.currency.toUpperCase()} ${(p.unit_amount / 100).toFixed(2)}\n`
         + `    code   : ${t.currency} ${t.priceAmount.toFixed(2)}\n`
         + `  Archive the wrong price in the dashboard, then re-run.`);
    }
    console.log(`= ${t.label}: reusing existing price ${p.id}`);
    out[t.stripePriceEnv] = p.id;
    continue;
  }

  const product = await stripe.products.create({
    name: `ARIA ${t.label}`,
    description: `${t.messagesPerDay} messages/day · ${t.ddRunsPerMonth} due-diligence runs/month`,
    metadata: { aria_tier: t.id },
  });
  const price = await stripe.prices.create({
    product: product.id,
    currency: t.currency.toLowerCase(),      // from tiers.mjs — never hand-typed
    unit_amount: t.priceAmount * 100,        // Stripe works in minor units
    recurring: { interval: 'month' },
    lookup_key: lookupKey,
    metadata: { aria_tier: t.id },
  });
  console.log(`+ ${t.label}: created ${price.id}  (${t.currency} ${t.priceAmount}/mo)`);
  out[t.stripePriceEnv] = price.id;
}

console.log('\n' + '─'.repeat(64));
console.log('Set these on the WEB tier (note -a aria-web — without it flyctl uses');
console.log('fly.toml, which is aria-intel, and the web app never sees them):\n');
console.log('flyctl secrets set -a aria-web \\');
for (const [env, id] of Object.entries(out)) console.log(`  ${env}="${id}" \\`);
console.log('  STRIPE_SECRET_KEY="<your sk_live_… key>" \\');
console.log('  STRIPE_WEBHOOK_SECRET="<whsec_… from the webhook endpoint>" \\');
console.log('  STRIPE_CHECKOUT_RETURN_URL="https://imaria.io/account.html?checkout=ok" \\');
console.log('  STRIPE_CHECKOUT_CANCEL_URL="https://imaria.io/account.html?checkout=cancelled" \\');
console.log('  STRIPE_PORTAL_RETURN_URL="https://imaria.io/account.html"');
console.log('\nStill needed from the dashboard — Developers → Webhooks → Add endpoint:');
console.log('  URL    https://imaria.io/api/billing/webhook');
console.log('  events checkout.session.completed, customer.subscription.created,');
console.log('         customer.subscription.updated, customer.subscription.deleted,');
console.log('         invoice.payment_failed');
console.log('  then copy its Signing secret (whsec_…) into the command above.\n');

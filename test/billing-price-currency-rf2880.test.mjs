// test/billing-price-currency-rf2880.test.mjs
//
// R-F2880 — the billing amount field was named `priceUsd` and held GBP.
//
// tiers.mjs carried the contradiction explicitly (R-F2314):
//     "`priceUsd` is the display amount in the landing's currency (GBP) ...
//      When Stripe is enabled, STRIPE_PRICE_PRO must be a GBP price."
// and public/account.html rendered it as `'£' + meResp.priceUsd`.
//
// Harmless while billing was free-tier only. The moment real Stripe Price IDs are
// set it stops being harmless: the site advertises £79/£199, and if the Stripe
// Prices are created in USD the customer is charged ~20% less than advertised, on
// every subscription. A field whose NAME contradicts its CONTENTS is the same
// unverified-claim class ratcheted shut on 2026-07-22 (R-F2867/2869/2873/2875/2876)
// — the payload asserts a currency nobody checked.
//
// Fix: `priceAmount` + an explicit `currency`, and the UI derives its symbol FROM
// that field instead of hardcoding one.
//
// DELIBERATELY NO `priceUsd` COMPATIBILITY ALIAS: an alias named priceUsd holding
// GBP would preserve the very lie being removed. Safe to drop because
//   - aria-app (the other consumer) has NO fly config — it is not deployed, and
//     its retirement runbook is still "awaiting operator go";
//   - public/account.html is served Cache-Control: no-cache and ships in the same
//     aria-web image as the API, so there is no skew window.
// Any consumer missed would read `undefined` — loudly wrong, not silently
// mis-priced, which is the correct failure direction for money.
//
// Run: node --test test/billing-price-currency-rf2880.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { TIERS } from '../lib/billing/tiers.mjs';

const read = (p) => readFileSync(new URL('../' + p, import.meta.url), 'utf8');
const stripComments = (s) => s
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .split(/\r?\n/).filter((l) => !l.trim().startsWith('//')).join('\n');

// ── the source of truth ──────────────────────────────────────────────────────

test('R-F2880: every tier carries priceAmount + an explicit currency', () => {
  for (const [id, t] of Object.entries(TIERS)) {
    assert.equal(typeof t.priceAmount, 'number', `${id}.priceAmount must be a number`);
    assert.equal(t.currency, 'GBP', `${id}.currency must be explicit`);
    assert.ok(!('priceUsd' in t), `${id} must not keep the misnamed field`);
  }
});

test('R-F2880: the advertised amounts are unchanged', () => {
  // This ticket renames a field. It must NOT reprice anything — the landing page
  // says £79 / £199 and those are contractual.
  assert.equal(TIERS.free.priceAmount, 0);
  assert.equal(TIERS.pro.priceAmount, 79);
  assert.equal(TIERS.proIntel.priceAmount, 199);
});

// ── the API surface ──────────────────────────────────────────────────────────

test('R-F2880: /api/billing/config emits priceAmount + currency, not priceUsd', () => {
  const src = stripComments(read('lib/billing/routes.mjs'));
  assert.match(src, /priceAmount/, 'the config payload must expose priceAmount');
  assert.match(src, /currency/, 'the config payload must expose the currency');
  assert.ok(!/priceUsd/.test(src), 'the misnamed field must be gone from the API');
});

// ── the UI ───────────────────────────────────────────────────────────────────

test('R-F2880: account.html derives the symbol from currency, never hardcodes it', () => {
  const src = stripComments(read('public/account.html'));
  assert.ok(!/priceUsd/.test(src), 'the UI must read priceAmount');
  assert.ok(!/'£'\s*\+/.test(src) && !/£'\s*\+/.test(src),
    'the £ must come from the currency field, not a literal');
  assert.match(src, /currencySymbol|_sym\(/,
    'a symbol resolver must exist so a currency change cannot silently mislabel');
});

test('R-F2880: an unknown currency shows the ISO code rather than guessing a symbol', async () => {
  // NEGATIVE CONTROL. Guessing a symbol for an unmapped currency is exactly the
  // failure this ticket removes — better to render "SEK 79" than a wrong "£79".
  const src = read('public/account.html');
  const m = src.match(/function _sym\(([\s\S]*?)\n {2}\}/);
  assert.ok(m, 'the symbol resolver must be a findable function');
  const body = m[0];
  assert.match(body, /GBP/, 'GBP must map to £');
  // The fallback must yield the ISO code itself, e.g. `MAP[c] || (c + ' ')`.
  assert.ok(/\|\|\s*\(?\s*[`(]?\s*c\b/.test(body),
    'an unmapped currency must fall back to the ISO code, not to a default symbol');
  assert.ok(!/\|\|\s*['"`]\s*[£$€]/.test(body),
    'the fallback must NOT be a hardcoded currency symbol');
});

// ── the other consumer ───────────────────────────────────────────────────────

test('R-F2880: aria-app is updated too, so reviving it cannot resurrect the bug', () => {
  // Strip comments: the file's own note cites the OLD name to explain the bug,
  // and matching that makes the guard fire on its own documentation — the fifth
  // time that trap bit this session (R-F2868/2873/2875/2876).
  const src = stripComments(read('aria-app/app/(customer)/account/page.tsx'));
  assert.ok(!/priceUsd/.test(src),
    'aria-app is not deployed today, but leaving it stale would be a trap');
  assert.match(src, /priceAmount/, 'it must read the renamed field');
});

// ── ratchet ──────────────────────────────────────────────────────────────────

test('R-F2880: RATCHET — priceUsd appears nowhere in billing source', () => {
  const files = ['lib/billing/tiers.mjs', 'lib/billing/routes.mjs',
                 'public/account.html', 'aria-app/app/(customer)/account/page.tsx'];
  const offenders = files.filter((f) => /priceUsd/.test(stripComments(read(f))));
  assert.deepEqual(offenders, [],
    'a field name that contradicts its contents must not come back: ' + offenders.join(', '));
});

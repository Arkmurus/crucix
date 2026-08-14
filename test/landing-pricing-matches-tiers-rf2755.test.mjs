// R-F2755 — landing page pricing must match the billing source of truth.
//
// The 360° review (2026-07-18) found the landing page advertised "10 DD runs"
// for Essentials while tiers.mjs enforced 20, and "Unlimited DD runs" for Pro
// Intel while tiers.mjs enforced 100 — so a paying customer would be 429'd
// against an "Unlimited" promise. Commercial-integrity contract: the landing
// page's DD-run claims must equal lib/billing/tiers.mjs (the SoT that quota
// enforcement + /api/billing/config both read). This test fails if they drift.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { TIERS } from '../lib/billing/tiers.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(__dirname, '..', 'public', 'index.html'), 'utf8');

test('Essentials (pro) DD limit on landing matches tiers.mjs', () => {
  const want = `${TIERS.pro.ddRunsPerMonth} DD runs / month`;
  assert.ok(html.includes(want), `landing page must advertise "${want}" for Essentials`);
});

test('Pro Intel DD limit on landing matches tiers.mjs (and is finite)', () => {
  const want = `${TIERS.proIntel.ddRunsPerMonth} DD runs / month`;
  assert.ok(html.includes(want), `landing page must advertise "${want}" for Pro Intel`);
});

test('landing page makes no "Unlimited DD" promise the quota cannot honour', () => {
  assert.ok(!/unlimited\s+dd/i.test(html),
    'landing page must not advertise "Unlimited DD" — every paid tier has a finite ddRunsPerMonth cap');
});

// ── R-F4020 (C-94) — EVERY figure on the landing page, not just DD runs ──────
//
// THE DRIFT CLASS THIS CLOSES. R-F2755 pinned `ddRunsPerMonth` and nothing else,
// so the landing page's message and upload claims were free to diverge from the
// tier table — and the upload one did: it advertised "5 MB document uploads"
// while the route enforced a flat 25 MB for everyone (C-73). The figure was wrong
// for over a year and no test could have noticed, because no test looked.
//
// Pinning one number and leaving its neighbours unguarded is how a commercial
// contract rots: the guarded claim stays true and quietly certifies the others by
// association. These derive every advertised figure from the same source of truth
// the enforcement reads, so a tier change that forgets the copy fails here.

test('R-F4020 every advertised figure derives from the tier table', () => {
  const CLAIMS = [
    ['pro',      `${TIERS.pro.messagesPerDay} messages per day`],
    ['proIntel', `${TIERS.proIntel.messagesPerDay.toLocaleString('en-US')} messages per day`],
    ['pro',      `${Math.round(TIERS.pro.uploadBytesMax / 1048576)} MB document uploads`],
    ['proIntel', `${Math.round(TIERS.proIntel.uploadBytesMax / 1048576)} MB document uploads`],
  ];
  for (const [tier, want] of CLAIMS) {
    assert.ok(html.includes(want),
      `landing page must advertise "${want}" for ${tier} — the figure customers `
      + 'read has to be the figure the platform enforces');
  }
});

test('R-F4020 the landing states no upload size the platform would refuse', () => {
  // The direction that costs money: advertising MORE than the tier allows means a
  // paying customer hits a wall they were told did not exist. Every "N MB
  // document uploads" on the page must correspond to a real tier value.
  const advertised = [...html.matchAll(/(\d+)\s*MB document uploads/g)].map((m) => Number(m[1]));
  const real = Object.values(TIERS).map((t) => Math.round(t.uploadBytesMax / 1048576));
  assert.ok(advertised.length >= 2, 'the pricing table should state upload sizes');
  for (const a of advertised) {
    assert.ok(real.includes(a),
      `landing advertises ${a} MB uploads, which matches no tier (${real.join(', ')})`);
  }
});

test('R-F4020 prices on the landing match the tier table', () => {
  for (const id of ['pro', 'proIntel']) {
    assert.ok(html.includes(`£${TIERS[id].priceAmount}`),
      `landing must show £${TIERS[id].priceAmount} for ${id}`);
    assert.equal(TIERS[id].currency, 'GBP',
      `${id} is advertised in £ on the landing, so its Stripe price must be GBP`);
  }
});

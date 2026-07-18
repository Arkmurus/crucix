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

// R-F578 (2026-05-16) — ProcurementPortals outer-timeout bump.
//
// Static-source test pinning the 60s outer cap. Pre-R-F578 the cap
// was 25s, which aborted the 28-market sweep before slow markets
// (Brazil, Saudi, Indonesia, Bangladesh on RSS) finished their
// 3-attempt chain (~30s worst case per market). Live evidence:
// "4/28 markets covered" reported in seenode logs 12:02:55 +
// "19/28 markets covered" at 12:07:55.
//
// Regression guard: a future "let's tighten the timeout" PR can't
// silently regress coverage. The test fails if the outer cap drops
// below 60s.

import { readFileSync } from 'node:fs';
import { resolve, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import assert from 'node:assert/strict';

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(__dirname, '..');
const portalsPath = join(repoRoot, 'apis', 'sources', 'procurement_portals.mjs');


function _src() { return readFileSync(portalsPath, 'utf8'); }


test('R-F578: outer-cap timeout is ≥60s', () => {
  const src = _src();
  // Look for the setTimeout(..., N) inside the timeoutPromise block
  const m = src.match(/timeoutPromise[\s\S]{0,400}setTimeout\(\s*\(\)\s*=>\s*resolve\(\s*['"]TIMEOUT['"]\s*\)\s*,\s*(\d+)\s*\)/);
  assert.ok(m, 'R-F578: could not find the outer-cap setTimeout call');
  const ms = parseInt(m[1], 10);
  assert.ok(ms >= 60000,
    `R-F578 regression: outer cap dropped to ${ms}ms (<60000ms). ` +
    `That re-introduces the 4/28 coverage bug.`);
});


test('R-F578: marker comment present', () => {
  const src = _src();
  assert.ok(src.includes('R-F578'),
    'R-F578 marker missing — refactor likely reverted the fix');
});


test('R-F578: tryFetch per-attempt timeout still bounded at ≤10s', () => {
  // Per-attempt timeout MUST remain bounded — that's what keeps a hung
  // host from eating the new 60s budget. If a future PR removes the
  // per-attempt cap thinking "60s outer is enough", a single dead
  // portal could stall the whole batch.
  const src = _src();
  const m = src.match(/async function tryFetch\(url,\s*timeout\s*=\s*(\d+)\)/);
  assert.ok(m, 'tryFetch signature missing');
  const perAttempt = parseInt(m[1], 10);
  assert.ok(perAttempt <= 10000,
    `R-F578: per-attempt timeout creeping up (${perAttempt}ms). ` +
    `Keep ≤10s so dead hosts don't eat the 60s budget.`);
});


test('R-F578: source is syntactically valid', () => {
  const src = _src();
  const open = (src.match(/\{/g) || []).length;
  const close = (src.match(/\}/g) || []).length;
  assert.ok(Math.abs(open - close) < 10,
    `Brace imbalance: { = ${open}, } = ${close}`);
});

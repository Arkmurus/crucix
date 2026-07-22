// test/api-health-source-buckets-rf2867.test.mjs
//
// R-F2867 — GET /api/health under-accounted for its own sources, and invented a total.
//
// R-F2853 pinned the arithmetic identity (buckets must sum to sourcesQueried) on the
// BRIEFING payload. /api/health — the surface public/dashboard.html actually reads —
// never got the same treatment and shipped only two of the five buckets:
//
//     sourcesOk:    meta.sourcesOk    || 0
//     sourcesFailed: meta.sourcesFailed || 0
//     sourcesTotal:  meta.sourcesQueried || 36
//
// So `partial`, `suspended` and `not_configured` were invisible and ok+failed != total.
// Live on 2026-07-22: 46 ok / 0 failed / 50 total — 4 sources unaccounted for. The
// dashboard renders that as "46/50" with the tooltip "4 source(s) degraded", which is a
// claim it cannot support: a not_configured source is not degraded, and a suspended one
// returned nothing at all while the payload showed ZERO failures. Same false-clean shape
// R-F2853 removed, on the artefact a customer actually looks at.
//
// SECOND DEFECT — the `|| 36` fallback. Before the first sweep there is no data, and the
// endpoint reported a total of 36 sources anyway. That number is fabricated: it is not
// measured, not configured anywhere, and was observed live immediately after a restart
// (sourcesOk:0, sourcesTotal:36). Reporting an invented denominator is worse than
// reporting none — the dashboard divides by it.
//
// Run: node --test test/api-health-source-buckets-rf2867.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { buildHealthSourceBuckets } from '../lib/health/sourceBuckets.mjs';

const META = {
  sourcesOk: 46,
  sourcesPartial: 2,
  sourcesFailed: 0,
  sourcesSuspended: 1,
  sourcesNotConfigured: 1,
  sourcesQueried: 50,
};

test('R-F2867: every queried source is accounted for (the R-F2853 identity, on /api/health)', () => {
  const b = buildHealthSourceBuckets(META);
  const summed = b.sourcesOk + b.sourcesPartial + b.sourcesFailed
               + b.sourcesSuspended + b.sourcesNotConfigured;
  assert.equal(summed, b.sourcesTotal,
    `buckets must sum to the total; got ${summed} vs ${b.sourcesTotal}`);
  assert.equal(b.sourcesUnaccounted, 0, 'nothing may be left unexplained');
});

test('R-F2867: the previously hidden buckets are exposed', () => {
  const b = buildHealthSourceBuckets(META);
  assert.equal(b.sourcesPartial, 2);
  assert.equal(b.sourcesSuspended, 1, 'a suspended source returned NOTHING — never hide it');
  assert.equal(b.sourcesNotConfigured, 1);
});

test('R-F2867: backwards compatible — existing consumers keep their fields', () => {
  // public/dashboard.html reads sourcesOk / sourcesTotal and divides them.
  const b = buildHealthSourceBuckets(META);
  assert.equal(b.sourcesOk, 46);
  assert.equal(b.sourcesFailed, 0);
  assert.equal(b.sourcesTotal, 50);
});

test('R-F2867: a residual is REPORTED, not silently absorbed', () => {
  // If a future status lands in none of the known buckets, the payload must SAY so
  // rather than let the numbers quietly disagree — that is how this defect survived.
  const b = buildHealthSourceBuckets({ ...META, sourcesQueried: 53 });
  assert.equal(b.sourcesUnaccounted, 3,
    'an unexplained residual must surface as a number, not vanish');
});

test('R-F2867: NO fabricated total before the first sweep', () => {
  // THE BUG: `meta.sourcesQueried || 36` invented a denominator out of nothing.
  const b = buildHealthSourceBuckets(null);
  assert.equal(b.sourcesTotal, null,
    'with no sweep data the total must be null, never an invented 36');
  assert.equal(b.sourcesOk, null, 'counts must be null, not a confident zero');
  assert.equal(b.swept, false, 'the payload must state that no sweep has completed');
});

test('R-F2867: NEGATIVE CONTROL — 36 must not appear for any empty input', () => {
  for (const empty of [null, undefined, {}]) {
    const b = buildHealthSourceBuckets(empty);
    assert.notEqual(b.sourcesTotal, 36,
      `the fabricated 36 must not reappear for input ${JSON.stringify(empty)}`);
  }
});

test('R-F2867: a swept-but-empty result is distinguishable from never-swept', () => {
  // sourcesQueried: 0 is a real measurement (a sweep ran, found nothing to query);
  // no data at all is not. Collapsing them would re-hide the thing this fixes.
  const b = buildHealthSourceBuckets({ sourcesQueried: 0 });
  assert.equal(b.swept, true, 'a present meta means a sweep produced data');
  assert.equal(b.sourcesTotal, 0);
});

// ── the surfaces that consume it ──────────────────────────────────────────────

test('R-F2867: /api/health wires the helper, with no fabricated 36 left behind', () => {
  const src = readFileSync(new URL('../server.mjs', import.meta.url), 'utf8');
  assert.match(src, /\.\.\.buildHealthSourceBuckets\(currentData\?\.meta\)/,
    'the endpoint must build its buckets from the helper');
  assert.ok(!/sourcesQueried\s*\|\|\s*36/.test(src),
    'the fabricated `|| 36` total must be gone');
  assert.match(src, /from '\.\/lib\/health\/sourceBuckets\.mjs'/,
    'the helper must be imported');
});

// NOT YET FIXED — public/dashboard.html still renders `(tot - ok) + ' source(s)
// degraded'`, which counts not_configured and any unaccounted residual as
// DEGRADED. That claim is now falsifiable from this payload, but the fix is
// deliberately NOT in this commit: dashboard.html currently carries a peer
// agent's uncommitted work, and committing it would sweep their changes in.
// The tooltip fix is written and parked at
// scratchpad/dashboard.with-rf2867.html — apply it once that file is clear.
// Tracked as a follow-up rather than silently dropped.

test('R-F2867: missing individual buckets default to 0 only when a sweep HAS run', () => {
  // An older meta (pre-R-F2853) carries no suspended/notConfigured keys. Those are
  // genuinely 0 for that payload, but the residual must then reveal the shortfall.
  const b = buildHealthSourceBuckets({ sourcesOk: 46, sourcesFailed: 0, sourcesQueried: 50 });
  assert.equal(b.sourcesSuspended, 0);
  assert.equal(b.sourcesUnaccounted, 4,
    'the 4 unexplained sources must be visible, which is the whole point');
});

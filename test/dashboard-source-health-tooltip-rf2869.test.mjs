// test/dashboard-source-health-tooltip-rf2869.test.mjs
//
// R-F2869 — the dashboard called sources DEGRADED that were not degraded.
//
// The Source Health tile rendered:
//
//     elSh.title = (tot - ok) + ' source(s) degraded';
//
// (tot - ok) is every source that is not OK — which lumps together four very
// different states. R-F2867 exposed the real buckets on /api/health, and the
// LIVE data immediately disproved the claim:
//
//     46 ok · 2 partial · 0 failed · 0 suspended · 2 not_configured  =  50
//
// The tile said "4 source(s) degraded". Only 2 were. The other 2 are
// not_configured — a configuration state, not a health fault. R-F2719 already
// established that distinction for the source-health page ("unconfigured is not
// healthy and not degraded"); the dashboard tile never got it.
//
// Calling a source degraded when it is merely unconfigured overstates a problem;
// doing it to an UNACCOUNTED source asserts a state we do not know at all. Both
// are claims the payload cannot support — and overclaiming a fault is the same
// class of error as hiding one, just pointing the other way.
//
// These assertions read the shipped HTML, the same technique R-F2617 used to pin
// the model card's live-hydrated values.
//
// Run: node --test test/dashboard-source-health-tooltip-rf2869.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const HTML = readFileSync(new URL('../public/dashboard.html', import.meta.url), 'utf8');

// The source-health tile block only, so these assertions cannot accidentally
// match similar code elsewhere on a 1000-line page.
const BLOCK = (() => {
  const start = HTML.indexOf("getElementById('kpi-srchealth')");
  assert.ok(start > 0, 'the source-health tile must exist');
  // End at the tile's own catch so the window tracks the block instead of a
  // fixed character count — a fixed count silently truncated the else branch
  // once explanatory comments were added, and the guard then fired on its own
  // blind spot rather than on a real regression.
  const end = HTML.indexOf('health is best-effort', start);
  assert.ok(end > start, 'the tile block must end at its catch');
  return HTML.slice(start, end);
})();

test('R-F2869: the (tot - ok) degraded claim is gone', () => {
  assert.ok(!/\(tot - ok\)\s*\+\s*' source\(s\) degraded'/.test(BLOCK),
    'THE BUG: every non-OK source was reported as degraded');
});

test('R-F2869: degraded is built from delivery FAILURES only', () => {
  // partial + failed + suspended = a source that did not deliver.
  for (const bucket of ['sourcesPartial', 'sourcesFailed', 'sourcesSuspended']) {
    assert.ok(BLOCK.includes(bucket), `${bucket} must contribute to the degraded count`);
  }
});

test('R-F2869: not_configured is NOT counted as degraded (the R-F2719 line)', () => {
  const degradedExpr = BLOCK.match(/const degraded[^;]+;/s);
  assert.ok(degradedExpr, 'a degraded count must be computed explicitly');
  assert.ok(!/sourcesNotConfigured/.test(degradedExpr[0]),
    'not_configured is a configuration state, not a health fault');
  assert.ok(!/sourcesUnaccounted/.test(degradedExpr[0]),
    'an unaccounted source has an UNKNOWN state — never assert it is degraded');
});

test('R-F2869: every non-OK bucket is still named in the tooltip', () => {
  // Excluding them from "degraded" must not make them invisible — that would
  // trade an overclaim for a hidden gap, which is the R-F2867 defect again.
  for (const bucket of ['sourcesPartial', 'sourcesFailed', 'sourcesSuspended',
                        'sourcesNotConfigured', 'sourcesUnaccounted']) {
    assert.ok(BLOCK.includes(bucket), `${bucket} must be surfaced to the operator`);
  }
});

test('R-F2869: the tile distinguishes "no sweep yet" from "unavailable"', () => {
  // R-F2867 made /api/health report swept:false with null counts. A bare "-"
  // could mean either; the tile must say which.
  assert.ok(/swept/.test(BLOCK), 'the tile must read the swept flag');
  assert.ok(/no sweep/i.test(BLOCK), 'a never-swept state must say so');
});

test('R-F2869: NEGATIVE CONTROL — the tile still renders ok/total for consumers', () => {
  assert.ok(/elSh\.textContent\s*=\s*ok\s*\+\s*'\/'\s*\+\s*tot/.test(BLOCK),
    'the X/Y reading must survive — this ticket changes the CLAIM, not the count');
});

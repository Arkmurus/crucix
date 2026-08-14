// test/landing-proof-of-work-rf4015.test.mjs
//
// R-F4015 (C-92) — the landing page showed no proof of work.
//
// The copy is careful and honest — hedged claims, no invented metrics, and the
// testimonial carousel was repurposed into inspectable trust commitments rather
// than fake customer quotes. That integrity is right and is not being traded away
// here. But a prospect saw nothing they could weigh: no sample report, no scale,
// no evidence that the thing exists beyond its own description.
//
// Meanwhile `/api/public/metrics` has been live, unauthenticated and working the
// whole time — 531,137 records when measured 2026-08-14 — and NOTHING rendered
// it. Same "built, correct, never surfaced" pattern as the tier flags, the DD
// sharing control and the sanctions coverage: the asset existed and the customer
// could not see it.
//
// THE FIGURE MUST DEGRADE HONESTLY. A hardcoded fallback number would be exactly
// the fabrication the rest of this page avoids, and model-card.html already
// carries the scar: R-F221 removed hardcoded counts that "lied the moment a
// clause was added". If the endpoint cannot answer, the line does not appear.
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const INDEX = fs.readFileSync(new URL('../public/index.html', import.meta.url), 'utf8');

/**
 * The IIFE that fetches the figure, bounded by the fetch call rather than by the
 * first mention of the path.
 *
 * `indexOf('/api/public/metrics')` finds the path inside the MARKUP COMMENT that
 * explains the change, hundreds of lines from the script, so a window measured
 * from there missed the code entirely. Anchor to the call.
 */
function proofScript() {
  const at = INDEX.indexOf("fetch('/api/public/metrics'");
  assert.ok(at > 0, 'the landing page should fetch the metrics endpoint');
  const end = INDEX.indexOf('</script>', at);
  return INDEX.slice(at - 900, end > at ? end : at + 1600);
}

describe('R-F4015 — the landing page shows a live, honest figure', () => {

  it('THE DEFECT: the page renders the public metrics endpoint', () => {
    assert.match(INDEX, /\/api\/public\/metrics/,
      'the endpoint has been live and unrendered — a prospect saw no evidence of scale');
  });

  it('there is an element for it with a neutral initial state', () => {
    assert.match(INDEX, /id="proof-records"/, 'the figure needs a stable hook');
    const at = INDEX.indexOf('id="proof-records"');
    const el = INDEX.slice(at, INDEX.indexOf('</', at));
    assert.doesNotMatch(el, /\d{3,}/,
      'the markup must not ship a baked-in number — it would be visible before '
      + 'the fetch resolves and would be a fabricated claim if the fetch failed');
  });

  it('a failed or empty measurement renders NOTHING, never a placeholder number', () => {
    const block = proofScript();
    assert.match(block, /records/, 'it must read the records field');
    // The honest-degradation guard: some branch must hide the line rather than
    // invent a value.
    assert.match(block, /display\s*=\s*'none'|hidden|return;/,
      'a null measurement must hide the line rather than show a stand-in');
    assert.doesNotMatch(block, /\|\|\s*\d{3,}/,
      'no numeric fallback — that is a fabricated corpus size');
  });

  it('the number is formatted for a human', () => {
    assert.match(INDEX, /toLocaleString\(/,
      '531137 is not a figure anyone reads; group it');
  });

  it('it does not block the page or throw on failure', () => {
    const block = proofScript();
    assert.match(block, /catch/,
      'a marketing page must never surface a fetch error to a visitor');
  });

  it('the claim wording stays defensible', () => {
    // The figure is a COUNT OF RECORDS in the evidence base. It must not be
    // dressed up as customers, reports, or anything the number does not measure —
    // that would be the invented-metric failure this page has so far avoided.
    const at = INDEX.indexOf('id="proof-records"');
    const around = INDEX.slice(Math.max(0, at - 400), at + 400);
    assert.doesNotMatch(around, /customers|clients|companies screened|reports delivered/i,
      'the metric counts evidence records and must not imply customer traction');
  });
});

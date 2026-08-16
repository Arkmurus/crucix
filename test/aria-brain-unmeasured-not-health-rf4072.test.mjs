// R-F4072 (C-114) / R-F4061 (C-116) / R-F4062 (C-119)
//
// Three ways the brain page rendered a NON-measurement as health. Same file,
// same class, so they are guarded together; the register keeps them separate.
//
// Source-level assertions on purpose: each defect is a rendering RULE (which
// class is chosen, which placeholder is matched, which reason is shown), and
// the rule is what regresses — a one-word edit puts any of them back.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { pageHtml } from './helpers/aria_brain_page.mjs';

const html = pageHtml();

function slice(fromNeedle, toNeedle) {
  const a = html.indexOf(fromNeedle);
  assert.ok(a > 0, `anchor not found: ${fromNeedle}`);
  const b = html.indexOf(toNeedle, a);
  assert.ok(b > a, `end anchor not found: ${toNeedle}`);
  return html.slice(a, b);
}

// ── C-114 ──────────────────────────────────────────────────────────────────

test('R-F4072 grounded rate is not coloured as a verdict below the sample floor', () => {
  const body = slice("'Grounded Rate'", "metricRow('Adversarial Score'");
  assert.match(body, /grounded_rate_samples/,
    'the rate must carry its sample size: live it was a flat red 0% from n=1, '
    + 'which autonomy_scorer and operating_modes both refuse to act on');
  assert.match(body, /'neutral'/,
    'below the floor the rate must be neutral, not scored as a failure');
});

test('R-F4072 verification zeros are not hardcoded green', () => {
  const body = slice("metricRow('Verification verified 24h'", "Blocking disagreements 24h");
  assert.doesNotMatch(
    body,
    /metricRow\('Verification verified 24h',\s*vg\.verified_24h \?\? 0,\s*'good'\)/,
    "the class was the literal 'good', so zero verifications rendered in the "
    + 'same green as a hundred while the gate had not run in three days',
  );
  assert.match(body, /noVerificationSample/,
    'zero-with-no-runs must be neutral, not healthy');
});

test('R-F4072 an excluded composite signal keeps its reason', () => {
  const body = slice('const excludedNote', 'const note =');
  assert.match(body, /excluded from score: \$\{provenance\}/,
    'insufficient_samples_n1 / no_data_neutral_prior / error all printed the '
    + 'same sentence; "could not measure" and "measured nothing" differ');
});

// ── C-116 ──────────────────────────────────────────────────────────────────

test('R-F4061 DEFERRED is not painted as a failure', () => {
  const body = slice('const deferred = m.worst_status', 'html += `<div style="padding:8px');
  assert.match(body, /deferred \? '⏸' : '✗'/,
    'DEFERRED fell through the ternary to a red ✗, identical to a real failure');
  assert.match(body, /deferReason/,
    'the API carries a reason for each deferral and the page showed neither '
    + 'the reason nor the state');
});

test('R-F4061 the tally accounts for deferred modules', () => {
  const body = slice('const deferredCount = counts.deferred', 'Critical failures');
  assert.match(body, /deferredCount/,
    'the summary read "76 / 0 / 0" against modules_checked 78 — it did not add up');
});

// ── C-119 ──────────────────────────────────────────────────────────────────

test('R-F4062 the stuck-loading sweeper matches the ellipsis form too', () => {
  const body = slice('const _STUCK_PLACEHOLDERS', 'function clearStuckLoading');
  assert.match(body, /loading…/,
    'six placeholders on this page emit U+2026 and the sweeper only matched '
    + 'three ASCII periods, so a dead panel read "Loading…" forever');
  assert.match(body, /loading\.\.\./, 'the original ASCII form must still match');
});

test('R-F4062 every placeholder on the page is one the sweeper can clear', () => {
  // The real guard: enumerate what the page actually ships and check the
  // sweeper's set covers it. A seventh placeholder spelt differently would
  // otherwise reintroduce the defect silently.
  const placeholders = [...html.matchAll(/class="loading"[^>]*>([^<]{0,40})</g)]
    .map(m => m[1].trim().toLowerCase())
    .filter(Boolean);
  assert.ok(placeholders.length >= 6, `found only ${placeholders.length}`);
  const setBody = slice('const _STUCK_PLACEHOLDERS', ';');
  for (const p of new Set(placeholders)) {
    assert.ok(setBody.includes(p),
      `placeholder ${JSON.stringify(p)} is not in _STUCK_PLACEHOLDERS, so a `
      + 'panel showing it would never be cleared');
  }
});

test('R-F4062 the hallucination failure branch clears every surface it owns', () => {
  const body = slice("el.innerHTML = '<span class=\"bad\">Failed to load. Endpoint unavailable.",
                     'const sum = d.summary');
  assert.match(body, /sumEl/,
    'the failure branch left the summary line at "Loading…" above an error');
  assert.match(body, /badge\.textContent = 'UNKNOWN'/,
    'the badge kept its placeholder "-" on a failed load');
});

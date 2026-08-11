// test/degraded-reason-rendered-rf3892.test.mjs
//
// C-28 / R-F3892 — the Command Control Centre showed a verdict and withheld the
// reason it was already holding.
//
// ── THE DEFECT ───────────────────────────────────────────────────────────────
// aria-brain.html renders `ECOSYSTEM: ${d.status}` from aria-intel's /health.
// Measured live 2026-08-11: the badge read DEGRADED while the SAME response
// carried
//
//   degraded_reasons: ["ecosystem_red_nodes_1", "ecosystem_degraded_nodes_22"]
//
// and a probe confirmed `renderedAnywhereOnPage: false`. The operator's main
// control page therefore said "something is wrong" and made him go to the API to
// find out what — while the answer had already been fetched and thrown away.
//
// This is C-27's family: not "is it recorded?" but "WHO READS IT, and from
// where?" A verdict without its reason is not actionable, and an unactionable
// alert is the one that gets ignored — which is how a real degradation hides
// among the ones you have learned to scroll past.
//
// It also cost a wrong inference during the review that found it: with only the
// badge visible, the obvious candidate was the one open circuit breaker
// (search:duckduckgo, the §27 datacenter IP block). That was wrong — the actual
// reasons are ecosystem node counts, and the breaker is unrelated and already
// displayed. Rendering the reason removes the guesswork rather than relocating it.
//
// ── WHAT THIS PINS ───────────────────────────────────────────────────────────
// That the reason is rendered, and rendered ESCAPED. It is server-supplied text
// reaching innerHTML, so it must go through the page's escaper — metricRow()
// escapes label, value and class, which is why the fix routes through it rather
// than building its own row.
//
// Run: node --test test/degraded-reason-rendered-rf3892.test.mjs

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, it } from 'node:test';

import { stripLineComments } from './helpers/html_interpolations.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
// Analyse CODE, not commentary. The first draft searched the raw source and
// found `degraded_reasons` inside the explanatory comment above the fix, so its
// window landed before the code it meant to inspect and it failed on a correct
// implementation. stripLineComments is length-preserving (comments become
// spaces), so every offset below still indexes the real file.
const PAGE = stripLineComments(readFileSync(join(ROOT, 'public', 'aria-brain.html'), 'utf8'));

/** The body of loadHealth(), where the ECOSYSTEM badge is set. */
function loadHealthBody() {
  const start = PAGE.indexOf('async function loadHealth(');
  assert.ok(start > 0, 'loadHealth() not found — this test has gone blind, fix it rather than deleting it');
  // Walk to the matching close brace so the assertions cannot drift onto a
  // neighbouring function if the file grows (the R-F3597 line-number lesson).
  let depth = 0; let i = PAGE.indexOf('{', start);
  const from = i;
  for (; i < PAGE.length; i++) {
    if (PAGE[i] === '{') depth++;
    else if (PAGE[i] === '}') { depth--; if (depth === 0) break; }
  }
  return PAGE.slice(from, i + 1);
}

describe('C-28 a DEGRADED verdict is shown with its reason', () => {
  it('loadHealth() reads degraded_reasons from the health payload', () => {
    const body = loadHealthBody();
    assert.match(body, /degraded_reasons/,
      'the badge reports status but never reads degraded_reasons — the operator is '
      + 'told something is wrong and not what, while the answer is already in the '
      + 'response that set the badge');
  });

  it('the reason reaches the page through the escaping row builder', () => {
    const body = loadHealthBody();
    const idx = body.indexOf('degraded_reasons');
    const region = body.slice(idx, idx + 700);
    assert.match(region, /metricRow\(/,
      'degraded_reasons is server-supplied text heading for innerHTML; render it '
      + 'via metricRow(), which escapes label, value and class');
    assert.doesNotMatch(region, /innerHTML\s*=\s*`[^`]*\$\{[^}]*degraded_reasons/,
      'degraded_reasons must never be interpolated straight into innerHTML');
  });

  it('an empty or missing reasons list does not render an empty row', () => {
    // A blank "Degraded because:" row is worse than none — it reads as a
    // rendering bug and teaches the operator to distrust the panel.
    const body = loadHealthBody();
    const idx = body.indexOf('degraded_reasons');
    const region = body.slice(idx, idx + 700);
    assert.match(region, /\.length|\?\.|&&/,
      'guard the row on the list being non-empty');
  });
});

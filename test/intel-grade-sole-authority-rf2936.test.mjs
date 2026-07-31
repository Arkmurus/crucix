// R-F2936 — intel_grade is THE customer decision authority.
//
// Operator, 2026-07-23: "intel grade is important because it will support our customer
// to take actions and decisions." There were two disagreeing quality measures —
// intel_grade (evidence: tier, corroboration, URL, entity, relevance) and
// customer_value.score (a rubric score; live, every signal scoring 96/100 was also
// Grade B). A customer cannot act on two verdicts that disagree.
//
// The gate already keys on intel_grade (R-F2714) + earned provenance (R-F2930), and
// /brief was moved onto the same gate (R-F2908). This locks it: the selector must not
// consult a customer_value / distribution score, and the dead helpers that read one
// must stay gone so they cannot be re-wired into a decision.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const HOOKS = readFileSync(new URL('../lib/telegram/channelServerHooks.mjs', import.meta.url), 'utf8');
const { selectTelegramGoldenIntel, selectPublishableGoldenIntel } =
  await import('../lib/telegram/channelServerHooks.mjs');

const fresh = { stale: false, stale_reasons: [], blocking_stale_reasons: [], publishable: true };

function sig(over = {}) {
  return {
    id: 'x', intel_grade: 'A', signal_type: 'sanctions_change', confidence: 'HIGH',
    title: 'Germany - Fighter aircraft procurement notice',
    why_it_matters: 'Bundesamt (Germany) — deadline 2026-08-11. Matched products: ammunition.',
    recommended_action: 'Assess bid/no-bid — review scope, eligibility and deadline.',
    url: 'https://ted.europa.eu/en/notice/1', detected_at: '2026-07-23T06:00:00Z',
    why_action_provenance: 'source_adapter',
    ...over,
  };
}

// ── the decision is intel_grade, regardless of the score ───────────────────

test('R-F2936: a Grade A with a LOW customer_value score still publishes', () => {
  // If the score were an authority, a low score would suppress a Grade A. It must not.
  const picked = selectTelegramGoldenIntel({
    ok: true, freshness: fresh,
    signals: [sig({ customer_value: { score: 3, rejection_reasons: ['weak'] }, distribution_score: 3 })],
  });
  assert.ok(picked, 'a Grade A was suppressed by a low customer_value score — the score is acting as an authority');
});

test('R-F2936: a high customer_value score does NOT rescue a non-publishable grade', () => {
  // A REJECT/C grade with a 100 score must not publish — grade is the gate, not the score.
  const picked = selectTelegramGoldenIntel({
    ok: true, freshness: fresh,
    signals: [sig({ intel_grade: 'REJECT', customer_value: { score: 100 }, distribution_score: 100 })],
  });
  assert.equal(picked, null, 'a non-publishable grade published because of a high score');
});

test('R-F2936: with two candidates, grade+provenance decides, not the score', () => {
  const strong = sig({ id: 'strong', intel_grade: 'A', distribution_score: 10 });
  const weakGradeHighScore = sig({ id: 'weak', intel_grade: 'B', distribution_score: 100 });
  const picked = selectTelegramGoldenIntel({ ok: true, freshness: fresh, signals: [weakGradeHighScore, strong] }, { grade: 'A' });
  assert.equal(picked?.id, 'strong', 'the higher score won over the higher grade');
});

// ── the competing measure is gone from the decision code ───────────────────

test('R-F2936: the selector source does not read a customer_value / distribution score', () => {
  const start = HOOKS.indexOf('function _selectGoldenCandidates');
  assert.ok(start > -1);
  const end = HOOKS.indexOf('\n}', HOOKS.indexOf('return candidates;', start));
  // Strip // comment lines — the function carries a comment EXPLAINING it does not use
  // the score, and a bare substring check would flag that prose (the same false
  // positive the DownloadFile/record_outcome bans hit). Assert on executed code only.
  const body = HOOKS.slice(start, end)
    .split('\n').filter(l => !l.trim().startsWith('//')).join('\n');
  assert.doesNotMatch(body, /customer_value/, 'the selector reads customer_value in code');
  assert.doesNotMatch(body, /distribution_score/, 'the selector reads distribution_score in code');
  assert.match(body, /grade === wantGrade/, 'the selector must gate on intel_grade');
  assert.match(body, /_hasItemSpecificAnalysis/, 'the selector must require earned provenance');
});

test('R-F2936: the dead customer_value gate helpers stay removed', () => {
  assert.doesNotMatch(HOOKS, /function _customerValueScore\b/,
    'the customer_value score helper is back — it can be re-wired into a decision');
  assert.doesNotMatch(HOOKS, /function _customerValueHardRejections\b/);
});

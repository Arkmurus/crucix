// test/bd-no-fabrication-rf2002.test.mjs
//
// R-F2002 — BD & Strategy honesty pass. A 4-step verification of the BD section
// found fabrication vectors: a hardcoded "$2.44T global spending" macro stat fed
// to the LLM as live data; benchmarks/OEM lists labeled "verified"/"recent
// comparable deals"; an autonomous-brain prompt that FORCED a named ministry/
// OEM/value/deadline for every lead (manufacturing fabrication when signals were
// thin); and a heuristic score rendered to users as "Win %".
//
// These assert the source-level guarantees of the fix (the codebase convention
// for the WA listener / BD modules: read source + assert, since importing runs
// network/LLM code).
//
// Run: node test/bd-no-fabrication-rf2002.test.mjs

import { readFileSync } from 'node:fs';
import assert from 'node:assert';

let failures = 0;
function check(name, fn) {
  try { fn(); console.log(`  ok - ${name}`); }
  catch (e) { failures++; console.error(`  FAIL - ${name}\n     ${e.message}`); }
}

const ENGINE = readFileSync(new URL('../lib/self/bd_intelligence.mjs', import.meta.url), 'utf8');
const PAGE   = readFileSync(new URL('../public/bd-intelligence.html', import.meta.url), 'utf8');

// ── fabrication removed ───────────────────────────────────────────────────────
check('hardcoded $2.44T macro stat is gone from the LLM context', () => {
  assert.ok(!/\$2\.44T/.test(ENGINE), 'the static "$2.44T" market stat must be removed');
  assert.ok(!/Top importers: Saudi Arabia \(8\.4%\)/.test(ENGINE));
});

check('benchmarks no longer claim "verified"/"recent comparable deals"', () => {
  assert.ok(!/recent comparable deals/.test(ENGINE));
  assert.ok(!/verified sales to Africa/.test(ENGINE));
  assert.ok(/ILLUSTRATIVE REFERENCE RANGES/.test(ENGINE), 'must reframe as illustrative/not-verified');
  assert.ok(/NOT verified/i.test(ENGINE));
});

// ── grounding / anti-fabrication rule present in BOTH prompts ─────────────────
check('autonomous-brain prompt carries the NO-FABRICATION grounding rule', () => {
  assert.ok(/NO FABRICATION/.test(ENGINE), 'the brain prompt must forbid inventing facts');
  assert.ok(/not in current signals/.test(ENGINE), 'must instruct an honest "not in current signals" fallback');
  // the forced "invent a ministry" exemplar must be gone
  assert.ok(!/Angola FAA Equipment Directorate/.test(ENGINE),
    'the fabricated exemplar ministry name must be removed');
});

check('strategy prompt also constrains naming to provided signals', () => {
  // both prompts should reference signals-only naming
  const hits = (ENGINE.match(/not in current signals/g) || []).length;
  assert.ok(hits >= 2, `expected the grounding fallback in both prompts (found ${hits})`);
});

// ── honest labelling on the page ──────────────────────────────────────────────
check('page no longer labels the heuristic as "Win %"', () => {
  assert.ok(!/'Win: '\+t\.winProbability/.test(PAGE), 'tender chip must not say "Win: N%"');
  assert.ok(!/>Win Prob</.test(PAGE), 'pipeline column must not be headed "Win Prob"');
  assert.ok(/Fit '\+t\.winProbability\+'\/100'/.test(PAGE), 'must relabel to a /100 fit score');
  assert.ok(/>Fit \(est\.\)</.test(PAGE), 'pipeline column must read "Fit (est.)"');
});

check('brain panel shows an AI-generated honesty banner', () => {
  assert.ok(/AI-generated from live signals/.test(PAGE));
  assert.ok(/verify independently/.test(PAGE));
});

if (failures) { console.error(`\n${failures} test(s) FAILED`); process.exit(1); }
console.log('\nAll R-F2002 BD no-fabrication tests passed.');

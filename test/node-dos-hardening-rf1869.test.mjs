// test/node-dos-hardening-rf1869.test.mjs
//
// Capability test for R-F1869 (audit DD-20 / DD-13 / DD-14) — server.mjs DoS
// hardening.
//
// DD-20: trivialReply() had a meta-probe regex with nested optional groups +
//        \s* that backtracks catastrophically on crafted near-match input.
//        Fix: collapse internal whitespace + gate the probe behind a length cap.
// DD-13/14: /api/opportunities and the admin source endpoints had no try/catch,
//        so a thrown promise became an unhandledRejection — the response never
//        sent and the request hung. Fix: try/catch that always responds.
//
// Run: node test/node-dos-hardening-rf1869.test.mjs

import { readFileSync } from 'node:fs';
import assert from 'node:assert';

let failures = 0;
function check(name, fn) {
  try { fn(); console.log(`  ok - ${name}`); }
  catch (e) { failures++; console.error(`  FAIL - ${name}\n     ${e.message}`); }
}

const src = readFileSync(new URL('../server.mjs', import.meta.url), 'utf8');

// ── DD-20 runtime: the guarded regex completes fast on a pathological input ──
check('DD-20: meta-probe regex with length guard completes in <50ms on attack input', () => {
  // The exact (post-fix) regex from trivialReply.
  const re = /^(can\s+you\s+(confirm|tell\s+me|verify)\s+)?(you('?re|\s+are)?\s+|are\s+you\s+)?(still|actually|really)?\s*(working|processing|running|on\s+it|there|alive)(\s+on\s+(it|that|this))?(\s+in\s+the\s+background)?$/;
  // Catastrophic-backtracking trigger: many "are you " repeats then a non-match.
  const attack = 'are you '.repeat(200) + '!';
  const guarded = (s) => (s.length <= 80 && re.test(s));
  const t0 = process.hrtime.bigint();
  const out = guarded(attack);              // length>80 → short-circuits, no regex run
  const ms = Number(process.hrtime.bigint() - t0) / 1e6;
  assert.strictEqual(out, false);
  assert.ok(ms < 50, `guarded check took ${ms.toFixed(1)}ms — expected <50ms`);
});

check('DD-20: a real short status probe still matches after whitespace-collapse', () => {
  const re = /^(can\s+you\s+(confirm|tell\s+me|verify)\s+)?(you('?re|\s+are)?\s+|are\s+you\s+)?(still|actually|really)?\s*(working|processing|running|on\s+it|there|alive)(\s+on\s+(it|that|this))?(\s+in\s+the\s+background)?$/;
  // collapse whitespace the way trivialReply now does
  const norm = (q) => q.trim().toLowerCase().replace(/[?.! ]+$/g, '').replace(/\s+/g, ' ');
  assert.ok((s => s.length <= 80 && re.test(s))(norm('are you   still    working?')));
});

// ── DD-20 static: server.mjs has the collapse + length guard ─────────────────
check('DD-20: server.mjs collapses whitespace + length-guards the probe', () => {
  assert.ok(/s = s\.replace\(\/\\s\+\/g, ' '\)/.test(src), 'whitespace-collapse missing in trivialReply');
  assert.ok(/s\.length <= 80 && \(\//.test(src), 'length guard on the meta-probe regex missing');
});

// ── DD-13/14 static: the async routes are wrapped so they always respond ─────
check('DD-13: /api/opportunities handler is wrapped in try/catch', () => {
  const i = src.indexOf("'/api/opportunities'");
  const block = src.slice(i, i + 900);
  assert.ok(/try\s*\{/.test(block) && /catch\s*\(/.test(block), '/api/opportunities not wrapped');
  assert.ok(/res\.status\(500\)/.test(block), '/api/opportunities has no 500 fallback');
});

check('DD-14: admin source endpoints are wrapped in try/catch', () => {
  for (const route of ["'/api/admin/source-prune-report'", "'/api/admin/sources/:name/enable'", "'/api/admin/sources/:name/disable'"]) {
    const i = src.indexOf(route);
    assert.ok(i >= 0, `route ${route} not found`);
    const block = src.slice(i, i + 400);
    assert.ok(/catch\s*\(/.test(block), `route ${route} not wrapped in try/catch`);
  }
});

if (failures) { console.error(`\n${failures} check(s) FAILED`); process.exit(1); }
console.log('\nAll R-F1869 node DoS-hardening checks passed');

// test/dd-delete-optimistic-ghost-rf2612.test.mjs
//
// Capability test for R-F2612 — deleting a DD report must ALSO drop its optimistic
// 'running' row, else it lingers in _optimisticRunning (20-min TTL) and loadReports
// re-adds it on the next poll → the deleted report "reappears" on the page (the
// operator-reported symptom). removeDeletedReport now filters _optimisticRunning too.
//
// Run: node test/dd-delete-optimistic-ghost-rf2612.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const HTML = readFileSync(join(__dirname, '..', 'public', 'dd-reports.html'), 'utf8');

let failures = 0;
function check(label, cond, detail = '') {
  if (cond) console.log(`  ✓ ${label}`);
  else { console.error(`  ✗ ${label}${detail ? '\n     ' + detail : ''}`); failures += 1; }
}

console.log('R-F2612 delete clears optimistic ghost\n');

// ── 1. Locate the real function ──────────────────────────────────────────────
// R-F3532 — this section used to pin the exact filter EXPRESSION
// (`.filter(r => r && r.run_id !== runId)`). It went red when the filter was
// widened to `!_locallyDeletedRunIds.has(r.run_id)` — a strict SUPERSET that
// suppresses the clicked run AND every other version the cascade deleted. The
// guard was asserting a spelling; the property it exists for was strengthened.
// It now LIFTS AND RUNS the real function, so it cannot go red on a rewrite that
// keeps the behaviour, and cannot go green on one that breaks it.
const fnStart = HTML.indexOf('function removeDeletedReport(');
check('removeDeletedReport found', fnStart !== -1);
const open = HTML.indexOf('{', fnStart);
let depth = 0, end = open;
for (; end < HTML.length; end++) {
  if (HTML[end] === '{') depth++;
  else if (HTML[end] === '}') { depth--; if (depth === 0) break; }
}
const fnSrc = HTML.slice(fnStart, end + 1);

// ── 2. BEHAVIOURAL: run the REAL function and prove the ghost is gone ────────
console.log('\nDeleting a report clears it from BOTH lists (real function):');
function runRemoval(runId, result) {
  const harness = `
    let _locallyDeletedRunIds = new Set();
    let _allReports = [{run_id:'dd_X'},{run_id:'dd_Y'}];
    let _optimisticRunning = [{run_id:'dd_X',_optimistic:true},{run_id:'dd_Z',_optimistic:true}];
    let _expandedRow = null;
    function applySearch(){}
    ${fnSrc}
    removeDeletedReport(${JSON.stringify(runId)}, ${JSON.stringify(result || null)});
    return { allReports:_allReports, optimisticRunning:_optimisticRunning,
             locallyDeleted:[..._locallyDeletedRunIds] };
  `;
  return new Function(harness)();
}

const state = runRemoval('dd_X');
check('deleted run_id removed from _allReports', !state.allReports.some(r => r.run_id === 'dd_X'));
check('deleted run_id removed from _optimisticRunning (no ghost)', !state.optimisticRunning.some(r => r.run_id === 'dd_X'));
check('other running re-run (dd_Z) preserved', state.optimisticRunning.some(r => r.run_id === 'dd_Z'));
check('other report (dd_Y) preserved', state.allReports.some(r => r.run_id === 'dd_Y'));
check('run_id tracked as locally-deleted', state.locallyDeleted.includes('dd_X'));

// R-F3532 — the ghost rule must cover the WHOLE deleted version chain, not just
// the clicked run: the row carries the LATEST run's id, so a surviving sibling
// resurfaces as the row on the next poll.
const chain = runRemoval('dd_X', { deleted_run_ids: ['dd_X', 'dd_Y'] });
check('every cascaded run is cleared from _allReports',
  chain.allReports.length === 0, 'got ' + JSON.stringify(chain.allReports));
check('every cascaded run is tracked as locally-deleted',
  chain.locallyDeleted.includes('dd_X') && chain.locallyDeleted.includes('dd_Y'));
check('an unrelated running re-run still survives a cascade',
  chain.optimisticRunning.some(r => r.run_id === 'dd_Z'));

console.log(`\n${failures === 0 ? 'PASS' : 'FAIL'} — ${failures} failure(s)`);
process.exit(failures === 0 ? 0 : 1);

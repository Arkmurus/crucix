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

// ── 1. STATIC: removeDeletedReport clears _optimisticRunning by run_id ────────
const fnStart = HTML.indexOf('function removeDeletedReport(');
const fnBody = fnStart !== -1 ? HTML.slice(fnStart, fnStart + 500) : '';
check('removeDeletedReport found', fnStart !== -1);
check('it filters _optimisticRunning by run_id',
  /_optimisticRunning\s*=\s*\(_optimisticRunning\s*\|\|\s*\[\]\)\.filter\(r\s*=>\s*r\s*&&\s*r\.run_id\s*!==\s*runId\)/.test(fnBody));
check('it still filters _allReports + tracks the deleted run_id',
  /_allReports\s*=\s*\(_allReports\s*\|\|\s*\[\]\)\.filter\(r\s*=>\s*r\s*&&\s*r\.run_id\s*!==\s*runId\)/.test(fnBody)
    && /_locallyDeletedRunIds\.add\(runId\)/.test(fnBody));

// ── 2. BEHAVIOURAL: mirror the logic and prove the ghost is gone ─────────────
console.log('\nDeleting a report clears it from BOTH lists (lockstep mirror):');
function removeDeletedReport(state, runId) {
  state.locallyDeleted.add(runId);
  state.allReports = state.allReports.filter(r => r && r.run_id !== runId);
  state.optimisticRunning = state.optimisticRunning.filter(r => r && r.run_id !== runId);
  return state;
}
const state = {
  locallyDeleted: new Set(),
  allReports: [{ run_id: 'dd_X' }, { run_id: 'dd_Y' }],
  optimisticRunning: [{ run_id: 'dd_X', _optimistic: true }, { run_id: 'dd_Z', _optimistic: true }],
};
removeDeletedReport(state, 'dd_X');
check('deleted run_id removed from _allReports', !state.allReports.some(r => r.run_id === 'dd_X'));
check('deleted run_id removed from _optimisticRunning (no ghost)', !state.optimisticRunning.some(r => r.run_id === 'dd_X'));
check('other running re-run (dd_Z) preserved', state.optimisticRunning.some(r => r.run_id === 'dd_Z'));
check('other report (dd_Y) preserved', state.allReports.some(r => r.run_id === 'dd_Y'));
check('run_id tracked as locally-deleted', state.locallyDeleted.has('dd_X'));

console.log(`\n${failures === 0 ? 'PASS' : 'FAIL'} — ${failures} failure(s)`);
process.exit(failures === 0 ? 0 : 1);

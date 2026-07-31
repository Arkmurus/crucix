// test/dd-delete-version-chain-rf3532.test.mjs
//
// R-F3532 — the DD library surface half of the delete fix.
//
// The brain now deletes the whole version chain the row represents. This guards
// what only the page can get wrong:
//   * an already-absent run must count as deleted, or the row can never clear
//   * every run the cascade removed must be suppressed locally — suppressing
//     only the clicked id let the PREVIOUS version surface as a "new" row, which
//     is precisely what made the report look undeletable
//   * a partial delete (earlier versions owned by another account) must not be
//     reported as a clean one
//
// The pure functions are lifted out of the page and EXECUTED, so this tests
// behaviour rather than the presence of a regex.
//
// Run: node test/dd-delete-version-chain-rf3532.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const PAGE = readFileSync(join(__dirname, '..', 'public/dd-reports.html'), 'utf8');

let failures = 0;
function check(label, cond, detail = '') {
  if (cond) console.log(`  ✓ ${label}`);
  else { console.error(`  ✗ ${label}${detail ? '\n     ' + detail : ''}`); failures += 1; }
}

function lift(name) {
  const start = PAGE.indexOf(`function ${name}(`);
  if (start === -1) throw new Error(`${name} not found in dd-reports.html`);
  // walk braces to the end of the declaration
  const open = PAGE.indexOf('{', start);
  let depth = 0, i = open;
  for (; i < PAGE.length; i++) {
    if (PAGE[i] === '{') depth++;
    else if (PAGE[i] === '}') { depth--; if (depth === 0) break; }
  }
  return PAGE.slice(start, i + 1);
}

console.log('R-F3532 — DD delete: version chain + honest receipt\n');

// ── deleteVerified ───────────────────────────────────────────────────────────
const deleteVerified = new Function(lift('deleteVerified') + '; return deleteVerified;')();

check('a real removal verifies', deleteVerified({ ok: true, index_entries_removed: 3 }));
check('a vault-only removal verifies', deleteVerified({ ok: true, vault_deleted: true }));
check('an already-absent run verifies (second click must clear the row)',
  deleteVerified({ ok: true, already_absent: true, index_entries_removed: 0 }),
  'without this the row can never be cleared — the operator-reported symptom');
check('ok:false never verifies', !deleteVerified({ ok: false, already_absent: true }));
check('an unreadable store never verifies',
  !deleteVerified({ ok: false, store_error: 'StoreReadError', index_entries_removed: 0 }),
  '"I could not look" must not be reported as "it is gone"');
check('an empty receipt never verifies', !deleteVerified({}) && !deleteVerified(null));

// ── deleteOutcomeMessage ─────────────────────────────────────────────────────
const deleteOutcomeMessage = new Function(lift('deleteOutcomeMessage') + '; return deleteOutcomeMessage;')();

const many = deleteOutcomeMessage({ ok: true, versions_deleted: 3, deleted_run_ids: ['a', 'b', 'c'] });
check('a multi-version delete says how many went', /3 versions/.test(many.msg));
check('a full delete reads as success', many.type === 'success');

const partial = deleteOutcomeMessage({
  ok: true, versions_deleted: 2, deleted_run_ids: ['a', 'b'], skipped_run_ids: ['c'],
});
check('a partial delete names the skipped versions', /another account/.test(partial.msg));
check('a partial delete is NOT reported as a clean success', partial.type !== 'success',
  'the row may legitimately remain for its other owner — never imply it is gone');

const absent = deleteOutcomeMessage({ ok: true, already_absent: true, versions_deleted: 0 });
check('an already-absent run does not claim a deletion happened',
  /already removed/i.test(absent.msg) && !/\d+ versions/.test(absent.msg));

const single = deleteOutcomeMessage({ ok: true, versions_deleted: 1, deleted_run_ids: ['a'] });
check('a single-version delete stays plain', single.msg === 'Report deleted');

// ── removeDeletedReport suppresses the whole group ───────────────────────────
console.log('\nLocal suppression — the previous version must not resurface:');
const harness = `
  let _locallyDeletedRunIds = new Set();
  let _allReports = [{run_id:'v3'},{run_id:'v2'},{run_id:'v1'},{run_id:'other'}];
  let _optimisticRunning = [{run_id:'v2'}];
  let _expandedRow = 'x';
  function applySearch(){}
  ${lift('removeDeletedReport')}
  removeDeletedReport('v3', {deleted_run_ids:['v3','v2','v1']});
  return { remaining:_allReports.map(r=>r.run_id), suppressed:[..._locallyDeletedRunIds].sort(), optimistic:_optimisticRunning.length };
`;
const out = new Function(harness)();
check('every cascaded run is removed from the local list',
  JSON.stringify(out.remaining) === JSON.stringify(['other']),
  'got ' + JSON.stringify(out.remaining));
check('every cascaded run is suppressed against the next poll',
  JSON.stringify(out.suppressed) === JSON.stringify(['v1', 'v2', 'v3']),
  'suppressing only the clicked id is what let v2 come back as the row');
check('an optimistic running row for a cascaded run is dropped too', out.optimistic === 0);

// ── the promise the dialog makes is the one the API keeps ────────────────────
console.log('\nThe confirm dialog and the request must agree:');
check('the dialog promises full version history',
  /removes the report and its full version history/.test(PAGE));
check('the request does NOT opt out of the cascade',
  !/cascade=false/.test(PAGE),
  'the dialog would then promise something the request disabled');
check('both delete call sites pass the receipt to the suppressor',
  (PAGE.match(/removeDeletedReport\((runId|rid), deleted\)/g) || []).length === 2);

console.log(`\n${failures === 0 ? 'PASS' : 'FAIL'} — ${failures} failure(s)`);
process.exit(failures === 0 ? 0 : 1);

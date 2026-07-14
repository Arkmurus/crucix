// test/low-pass-empty-vs-error-rf2619.test.mjs
//
// Capability test for R-F2619 (LOW pass) — honest empty-vs-error states.
// API.get() returns null on ANY failure (401/timeout/5xx/error-envelope) and a
// real (possibly empty) payload on success. Rendering "No X yet" on a null is
// the §22 empty-vs-error lie: a broken fetch masquerades as an honest empty
// state. bd-intelligence (load + loadPipeline) and opportunities now
// distinguish the two. Also asserts the dead runSweep() is gone.
//
// Run: node test/low-pass-empty-vs-error-rf2619.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const rd = (p) => readFileSync(join(__dirname, '..', 'public', p), 'utf8');
const BD = rd('bd-intelligence.html');
const OPP = rd('opportunities.html');
const DASH = rd('dashboard.html');

let failures = 0;
function check(label, cond, detail = '') {
  if (cond) console.log(`  ✓ ${label}`);
  else { console.error(`  ✗ ${label}${detail ? '\n     ' + detail : ''}`); failures += 1; }
}

console.log('R-F2619 LOW pass — honest empty-vs-error + dead-code removal\n');

// ── 1. dead runSweep() removed ───────────────────────────────────────────────
check('dashboard.html no longer defines runSweep()', !/function runSweep\s*\(/.test(DASH));
check('dashboard.html no longer references btn-sweep', !/getElementById\('btn-sweep'\)/.test(DASH));

// ── 2. opportunities distinguishes null (error) from [] (empty) ──────────────
check('opportunities has a data === null error branch', /data === null/.test(OPP));
check('opportunities error branch says "Couldn\'t load" (not "No opportunities available")',
  /Couldn\\?'t load opportunities/.test(OPP));
check('opportunities empty state is separate + still present',
  /No opportunities available yet/.test(OPP));

// ── 3. bd-intelligence load() + loadPipeline() both distinguish ──────────────
check('bd-intelligence load() null branch is an honest load-failure message',
  /Couldn\\?'t load BD intelligence/.test(BD));
check('bd-intelligence loadPipeline() has a data === null branch', /data === null/.test(BD));
check('bd-intelligence pipeline error != "No deals in pipeline yet"',
  /Couldn\\?'t load the pipeline/.test(BD) && /No deals in pipeline yet/.test(BD));

// ── 4. BEHAVIOURAL: mirror the empty-vs-error decision ───────────────────────
console.log('\nEmpty-vs-error decision mirror:');
function renderState(apiResult, emptyMsg, errorMsg) {
  // mirror: null => error; array/obj => empty-or-data
  if (apiResult === null) return errorMsg;
  const items = Array.isArray(apiResult) ? apiResult : (apiResult.items || []);
  return items.length ? 'DATA' : emptyMsg;
}
const EMPTY = 'No deals in pipeline yet';
const ERROR = "Couldn't load the pipeline";
check('null (failed fetch) -> error message', renderState(null, EMPTY, ERROR) === ERROR);
check('[] (success, empty) -> empty message', renderState([], EMPTY, ERROR) === EMPTY);
check('populated -> data', renderState([{ id: 1 }], EMPTY, ERROR) === 'DATA');
check('a failed fetch NEVER shows the empty message',
  renderState(null, EMPTY, ERROR) !== EMPTY);

console.log(`\n${failures === 0 ? 'PASS' : 'FAIL'} — ${failures} failure(s)`);
process.exit(failures === 0 ? 0 : 1);

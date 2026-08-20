// test/precommit-gate-performance-rf3556.test.mjs
//
// R-F3556 — the pre-commit gate hung CI for hours; the workflow had no timeout.
//
// Measured on the real tree BEFORE the fix: 588 files to scan, only 37 done in
// 230s. aria_engine.py alone took 94.6s for its 844 call sites (~112ms each),
// main.py 88.2s for 876. Cause: `function_exists` ran a full ast.parse() of the
// TARGET module per call site, and `resolve_module` re-parsed the SCANNED file
// per call site — quadratic, not a deadlock. Runs stacked up and none finished.

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = join(__dirname, '..');

let failures = 0;
function check(label, cond, detail = '') {
  if (cond) console.log(`  ✓ ${label}`);
  else { console.error(`  ✗ ${label}${detail ? '\n     ' + detail : ''}`); failures += 1; }
}

console.log('R-F3556 — pre-commit gate cannot hang CI again\n');

const checks = readFileSync(join(root, 'scripts/pre_commit_checks.py'), 'utf8');

check('the per-call AST re-parse is memoised',
  /@lru_cache\([\s\S]{0,40}\)\s*\ndef _module_function_names/.test(checks),
  'without the cache the target module is re-parsed once per call site');
check('the scanned file\'s import map is memoised',
  /@lru_cache\([\s\S]{0,40}\)\s*\ndef _import_map/.test(checks));
check('function_exists no longer parses anything itself',
  !/def function_exists[\s\S]{0,400}ast\.parse/.test(checks),
  'it must delegate to the cached lookup');

console.log('\nFalse-positive classes that made the gate cry wolf:');
check('imported CLASSES count as defined names',
  /ast\.ClassDef/.test(checks),
  'else instantiating an imported class reads as a missing function');
check('container/str methods are not treated as module API',
  /_BUILTIN_METHOD_NAMES/.test(checks) && /"upper"/.test(checks) && /"get"/.test(checks));
check('the import map is scope-aware',
  /end_lineno/.test(checks) && /def resolve_module\(obj_name: str, file_path: Path, line_num/.test(checks),
  'a function-local alias must not leak across the whole file');
check('hasattr-guarded optional calls are exempt',
  /def _is_capability_guarded/.test(checks));

console.log('\nThe workflow must fail fast rather than block the pipeline:');
// Parsed textually rather than with a YAML library: the repo has no js-yaml
// dependency and adding one to assert four lines is not worth the supply chain.
const ciText = readFileSync(join(root, '.github/workflows/ci.yml'), 'utf8');
const jobNames = [...ciText.matchAll(/^  ([a-z][a-z0-9_-]*):$/gm)]
  .map(m => m[1])
  .filter(n => !['push', 'pull_request', 'workflow_dispatch', 'schedule'].includes(n));
check('ci.yml declares jobs (guard is not vacuous)', jobNames.length >= 2,
  'found: ' + JSON.stringify(jobNames));
const timeouts = [...ciText.matchAll(/^    timeout-minutes:\s*(\d+)\s*$/gm)].map(m => Number(m[1]));
check('every job declares a timeout', timeouts.length >= jobNames.length,
  timeouts.length + ' timeout(s) for ' + jobNames.length + ' job(s). No timeout means '
  + "GitHub's 6-hour default, so one hang blocks every queued run.");
const jobTimeouts = Object.fromEntries(jobNames.map(name => {
  const start = ciText.indexOf(`  ${name}:`);
  const nextJob = ciText.slice(start + 1).search(/^  [a-z][a-z0-9_-]*:$/m);
  const body = nextJob < 0 ? ciText.slice(start) : ciText.slice(start, start + 1 + nextJob);
  const match = body.match(/^    timeout-minutes:\s*(\d+)\s*$/m);
  return [name, match ? Number(match[1]) : null];
}));
check('every timeout is bounded at 90 minutes',
  Object.values(jobTimeouts).every(t => t != null && t > 0 && t <= 90),
  JSON.stringify(jobTimeouts));
check('only the measured exhaustive suite may exceed 60 minutes',
  Object.entries(jobTimeouts).every(([name, timeout]) => timeout <= 60 || name === 'suite-baseline-gate'),
  JSON.stringify(jobTimeouts));

console.log(`\n${failures === 0 ? 'PASS' : 'FAIL'} — ${failures} failure(s)`);
process.exit(failures === 0 ? 0 : 1);

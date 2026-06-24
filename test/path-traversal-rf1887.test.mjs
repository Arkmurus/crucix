// test/path-traversal-rf1887.test.mjs
//
// R-F1887 (review Class D) — moduleName path-traversal. R-F1867 sanitized
// /api/self/apply + /rollback, but the review found unsanitized moduleName on
// the paths it missed: POST /api/self/generate and the Telegram /update apply/
// discard/preview/rollback/fix branches. The discard branch was the worst —
// parts[1] was join()'d into runs/staged/${parts[1]}.mjs.staged and unlinkSync'd
// → a "../../<x>" path-traversal file delete.
//
// Run: node test/path-traversal-rf1887.test.mjs

import { readFileSync } from 'node:fs';
import assert from 'node:assert';

let failures = 0;
function check(name, fn) {
  try { fn(); console.log(`  ok - ${name}`); }
  catch (e) { failures++; console.error(`  FAIL - ${name}\n     ${e.message}`); }
}
const SRC = readFileSync(new URL('../server.mjs', import.meta.url), 'utf8');

// ── runtime: the sanitizer strips traversal sequences ────────────────────────
check('sanitizer strips path-traversal + separators', () => {
  const sanitize = (name) => String(name || '').toLowerCase().replace(/[^a-z0-9_]/g, '').substring(0, 40);
  assert.strictEqual(sanitize('../../etc/passwd'), 'etcpasswd');
  assert.strictEqual(sanitize('runs/staged/../../x'), 'runsstagedx');
  assert.strictEqual(sanitize('..%2f..%2fx'), '2f2fx');
  assert.strictEqual(sanitize('good_module1'), 'good_module1');
  assert.strictEqual(sanitize(''), '');
});

// ── static: every module-name entry point sanitizes ──────────────────────────
function branchBlock(marker, len = 600) {
  const i = SRC.indexOf(marker);
  assert.ok(i >= 0, `marker not found: ${marker}`);
  return SRC.slice(i, i + len);
}

check('Telegram /update apply sanitizes the module name', () => {
  const b = branchBlock("subCmd === 'apply' && parts[1]");
  assert.ok(/_sanitizeModuleName\(parts\[1\]\)/.test(b), 'apply does not sanitize parts[1]');
});
check('Telegram /update discard sanitizes BEFORE building the staged path', () => {
  const b = branchBlock("subCmd === 'discard' && parts[1]", 800);
  const sanIdx = b.indexOf('_sanitizeModuleName(parts[1])');
  const joinIdx = b.indexOf("join(ROOT, 'runs', 'staged'");
  assert.ok(sanIdx >= 0 && joinIdx > sanIdx, 'discard must sanitize before join()/unlinkSync');
  assert.ok(!/`\$\{parts\[1\]\}\.mjs\.staged`/.test(b), 'discard still joins raw parts[1] into the path');
});
check('Telegram /update preview + rollback + fix sanitize', () => {
  assert.ok(/_sanitizeModuleName\(parts\[1\]\)/.test(branchBlock("subCmd === 'preview' && parts[1]")), 'preview unsanitized');
  assert.ok(/_sanitizeModuleName\(parts\[1\]\)/.test(branchBlock("subCmd === 'rollback' && parts[1]")), 'rollback unsanitized');
  assert.ok(/_sanitizeModuleName\(parts\[1\]\)/.test(branchBlock("subCmd === 'fix' && parts[1]")), 'fix unsanitized');
});
check('POST /api/self/generate sanitizes moduleName', () => {
  const b = branchBlock("'/api/self/generate'", 500);
  assert.ok(/_sanitizeModuleName\(\(req\.body \|\| \{\}\)\.moduleName\)/.test(b), '/generate does not sanitize moduleName');
});

if (failures) { console.error(`\n${failures} check(s) FAILED`); process.exit(1); }
console.log('\nAll R-F1887 path-traversal checks passed');

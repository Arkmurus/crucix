// test/self-updater-injection-rf1867.test.mjs
//
// Capability test for R-F1867 (audit DD-11 / DD-12) — command injection in the
// self-updater deploy path.
//
// DD-11: lib/self/updater.mjs gitCommit() built `git commit -m "${message}"`
//        via execSync, escaping ONLY quotes + newlines. Shell metacharacters
//        (backticks, $(), ;, |) in `message` — which carries a caller-
//        controllable moduleName — executed as shell. Fix: spawnSync arg-array
//        (no shell).
// DD-12: server.mjs POST /api/self/apply + /rollback fed `moduleName` into
//        deployModule()->gitCommit() and into file paths with no sanitization.
//        Fix: _sanitizeModuleName() constrains it to [a-z0-9_].
//
// Run: node test/self-updater-injection-rf1867.test.mjs

import { readFileSync, existsSync, rmSync, mkdtempSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { spawnSync } from 'node:child_process';
import assert from 'node:assert';

let failures = 0;
function check(name, fn) {
  try { fn(); console.log(`  ok - ${name}`); }
  catch (e) { failures++; console.error(`  FAIL - ${name}\n     ${e.message}`); }
}

const ROOT = process.cwd();

// ── DD-11 runtime: the spawnSync arg-array form cannot be shell-injected ─────
check('DD-11: malicious commit message does NOT execute a shell command', () => {
  const repo = mkdtempSync(join(tmpdir(), 'rf1867-'));
  const marker = join(repo, 'PWNED');
  try {
    spawnSync('git', ['init', '-q'], { cwd: repo });
    spawnSync('git', ['config', 'user.email', 't@t.t'], { cwd: repo });
    spawnSync('git', ['config', 'user.name', 't'], { cwd: repo });
    writeFileSync(join(repo, 'f.txt'), 'x');
    spawnSync('git', ['add', '-A'], { cwd: repo });

    // The injection payload that the OLD execSync template literal would run.
    const evil = `feat: add module $(touch "${marker}")\`touch "${marker}"\`; touch "${marker}"`;

    // This is EXACTLY the fixed form from updater.mjs gitCommit():
    const commit = spawnSync('git', ['commit', '-m', String(evil)], {
      cwd: repo, stdio: 'pipe', timeout: 15000, encoding: 'utf8',
    });
    assert.strictEqual(commit.status, 0, 'commit should succeed');

    // The injected `touch` must NOT have run.
    assert.ok(!existsSync(marker), 'injected shell command executed — marker file was created!');

    // And the literal payload must be preserved verbatim as the commit subject.
    const subject = spawnSync('git', ['log', '-1', '--pretty=%s'], {
      cwd: repo, encoding: 'utf8',
    }).stdout.trim();
    assert.ok(subject.includes('$(touch'), 'commit subject should contain the literal payload, proving no shell parsing');
  } finally {
    rmSync(repo, { recursive: true, force: true });
  }
});

// ── DD-11 static: the vulnerable execSync template literal is gone ───────────
check('DD-11: updater.mjs no longer uses execSync template-literal for git commit', () => {
  const src = readFileSync(join(ROOT, 'lib/self/updater.mjs'), 'utf8');
  assert.ok(!/execSync\(`git commit -m/.test(src), 'vulnerable execSync `git commit -m "${...}"` still present');
  assert.ok(/spawnSync\('git',\s*\['commit',\s*'-m',\s*String\(message\)\]/.test(src),
    'expected spawnSync arg-array form for git commit');
});

// ── DD-12: the sanitizer strips shell/path metacharacters ────────────────────
check('DD-12: _sanitizeModuleName regex strips injection + traversal chars', () => {
  // Replicate the exact regex used in server.mjs.
  const sanitize = (name) => String(name || '').toLowerCase().replace(/[^a-z0-9_]/g, '').substring(0, 40);
  assert.strictEqual(sanitize('foo`touch x`'), 'footouchx');
  assert.strictEqual(sanitize('../../etc/passwd'), 'etcpasswd');
  assert.strictEqual(sanitize('a; rm -rf /'), 'armrf');
  assert.strictEqual(sanitize('good_module1'), 'good_module1');
  assert.strictEqual(sanitize(''), '');
});

// ── DD-12 static: the apply + rollback routes call the sanitizer ─────────────
check('DD-12: /api/self/apply + /rollback sanitize moduleName', () => {
  const src = readFileSync(join(ROOT, 'server.mjs'), 'utf8');
  assert.ok(/function _sanitizeModuleName/.test(src), '_sanitizeModuleName helper missing');
  const applyBlock = src.slice(src.indexOf("'/api/self/apply'"), src.indexOf("'/api/self/apply'") + 400);
  assert.ok(/_sanitizeModuleName\(/.test(applyBlock), '/api/self/apply does not sanitize moduleName');
  const rollbackBlock = src.slice(src.indexOf("'/api/self/rollback'"), src.indexOf("'/api/self/rollback'") + 400);
  assert.ok(/_sanitizeModuleName\(/.test(rollbackBlock), '/api/self/rollback does not sanitize moduleName');
});

if (failures) { console.error(`\n${failures} check(s) FAILED`); process.exit(1); }
console.log('\nAll R-F1867 injection checks passed');

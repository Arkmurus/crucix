// R-F2729 — Batch 4 quality gate: a real, runnable, TERMINATING Node lint (Prospector #3/#4:
// "npm run lint: no lint script exists"; the full static gate timed out). This is the minimum
// honest gate — `node --check` over every tracked .mjs, catching the exact class that caused the
// R-F2119/2120 outage (31 syntax errors → un-importable tree). Tests the mechanism + the wiring.

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync, rmSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');

describe('R-F2729 Node lint gate', () => {
  it('is wired into package.json (was: no lint script)', () => {
    const pkg = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'));
    assert.equal(pkg.scripts.lint, 'node scripts/lint.mjs');
    assert.equal(pkg.scripts['lint:changed'], 'node scripts/lint.mjs --changed');
  });

  it('scopes to .mjs and excludes vendored / workflow-body scripts (no false positives)', () => {
    const src = readFileSync(join(root, 'scripts', 'lint.mjs'), 'utf8');
    assert.match(src, /'\*\.mjs'/, 'lints .mjs');
    for (const ex of ['public/vendor/', 'scripts/workflows/', 'node_modules/']) {
      assert.ok(src.includes(ex), `must exclude ${ex}`);
    }
  });

  it('MECHANISM: node --check passes clean ESM and FAILS a syntax error', () => {
    const dir = mkdtempSync(join(tmpdir(), 'rf2729-'));
    try {
      const good = join(dir, 'good.mjs');
      const bad = join(dir, 'bad.mjs');
      writeFileSync(good, 'export const x = 1;\n');
      writeFileSync(bad, 'export const x = ;\n');  // syntax error
      // clean file → node --check succeeds (no throw)
      execFileSync(process.execPath, ['--check', good]);
      // broken file → node --check throws (the gate would flag it)
      assert.throws(() => execFileSync(process.execPath, ['--check', bad], { stdio: 'ignore' }),
        'node --check must reject a syntax error');
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it('the real gate runs and PASSES on the current tree (green + terminates)', () => {
    // drives scripts/lint.mjs end-to-end; exit 0 proves the whole .mjs tree parses.
    const out = execFileSync(process.execPath, [join(root, 'scripts', 'lint.mjs')], { encoding: 'utf8', cwd: root });
    assert.match(out, /\[lint\] OK — \d+ file\(s\) parse cleanly/);
  });
});

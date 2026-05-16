// R-F570 (2026-05-16) — sync.mjs runtime-GitHub-dependency removal.
//
// Tests prove:
//   1. The script contains no api.github.com / raw.githubusercontent.com
//      references (the dependency we're removing).
//   2. It reads HEAD from local git via `git rev-parse`.
//   3. It writes build_rev.txt with the live commit short-sha.
//   4. The script's exit code is 0 even when git is unavailable
//      (fallback path), so seenode's build doesn't abort.
//
// These are static + runtime checks. Static because the goal is to
// stop a future change reintroducing the GitHub-API dep; runtime to
// confirm the local-git replacement actually works on this box.

import { execSync, spawnSync } from 'node:child_process';
import { readFileSync, existsSync, unlinkSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import assert from 'node:assert/strict';

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(__dirname, '..');
const syncPath = join(repoRoot, 'sync.mjs');

// ── Static-source guards ───────────────────────────────────────────

test('R-F570: sync.mjs exists and is a Node module', () => {
  assert.ok(existsSync(syncPath), 'sync.mjs missing from repo root');
  const src = readFileSync(syncPath, 'utf8');
  assert.ok(src.startsWith('//') || src.startsWith('import'),
    'sync.mjs should be ES module — first line is comment or import');
});

// Strip block + line comments so we only check actual code, not
// historical commentary that may legitimately mention the removed
// URLs.
function _stripComments(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '')        // block comments
    .replace(/^\s*\/\/.*$/gm, '');           // full-line comments
}

test('R-F570: no api.github.com fetch call in code (R-F542 dep removed)', () => {
  const code = _stripComments(readFileSync(syncPath, 'utf8'));
  assert.ok(!code.includes('api.github.com'),
    'R-F570 regression: api.github.com reference reintroduced in code. ' +
    '/api/health build_rev must come from local git, not GitHub API.');
});

test('R-F570: no raw-content host fetch in code (file-fetch loop removed)', () => {
  const code = _stripComments(readFileSync(syncPath, 'utf8'));
  assert.ok(!code.includes('raw.githubusercontent.com'),
    'R-F570 regression: file-fetch loop reintroduced in code. ' +
    'Seenode clones the repo itself; we never fetch source via HTTP.');
});

test('R-F570: no fetch() call anywhere in sync.mjs', () => {
  const code = _stripComments(readFileSync(syncPath, 'utf8'));
  // `fetch(` would indicate a network call. The local-git replacement
  // uses execSync only.
  assert.ok(!/\bfetch\s*\(/.test(code),
    'R-F570 regression: sync.mjs must not make any fetch() calls. ' +
    'Build-time network deps are forbidden per [[aria_mirrors_claude]].');
});

test('R-F570: reads HEAD via local git', () => {
  const src = readFileSync(syncPath, 'utf8');
  assert.ok(src.includes('git rev-parse'),
    'R-F570: sync.mjs must compute build_rev from `git rev-parse`');
  assert.ok(src.includes('git log -1'),
    'R-F570: sync.mjs should pull date + subject via `git log -1`');
});

test('R-F570: marker comment present so future audits find it', () => {
  const src = readFileSync(syncPath, 'utf8');
  assert.ok(src.includes('R-F570'),
    'R-F570: marker comment missing — likely reverted');
});

// ── Runtime: exec the script + check artefact ──────────────────────

test('R-F570: running sync.mjs writes build_rev.txt with local sha', () => {
  // Run sync.mjs from a tmp cwd so we don't disturb the real file in
  // repo root (and so we can clean up the artefact afterwards).
  const tmpCwd = mkdtempSync(join(tmpdir(), 'rf570-'));
  try {
    // Init a tiny git repo with one commit so the script has something
    // to read.
    execSync('git init -q', { cwd: tmpCwd });
    execSync('git config user.email rf570@test.local', { cwd: tmpCwd });
    execSync('git config user.name rf570', { cwd: tmpCwd });
    execSync('git commit --allow-empty -q -m "rf570: seed commit"', { cwd: tmpCwd });

    const result = spawnSync('node', [syncPath], {
      cwd: tmpCwd,
      encoding: 'utf8',
      timeout: 15000,
    });
    assert.equal(result.status, 0,
      `sync.mjs exited ${result.status}; stderr: ${result.stderr}`);

    const artefact = join(tmpCwd, 'build_rev.txt');
    assert.ok(existsSync(artefact), 'build_rev.txt not written');
    const content = readFileSync(artefact, 'utf8');
    assert.ok(content.length > 0, 'build_rev.txt empty');
    // Local seed-commit sha is short (7-8 hex chars) followed by ' · '.
    assert.match(content, /^[0-9a-f]{7,8} · \d{4}-\d{2}-\d{2} · /,
      `Expected '<sha> · <date> · <subject>' shape, got: ${content}`);
  } finally {
    // tmp dir is OS-managed, don't bother cleaning
  }
});

test('R-F570: sync.mjs exits 0 even when git is unavailable', () => {
  // We can't easily "remove git from PATH" cross-platform. Instead,
  // run the script from a directory with NO .git context — the script
  // should hit the fallback path and still exit 0.
  const tmpCwd = mkdtempSync(join(tmpdir(), 'rf570-nogit-'));
  const result = spawnSync('node', [syncPath], {
    cwd: tmpCwd,
    encoding: 'utf8',
    timeout: 15000,
  });
  assert.equal(result.status, 0,
    `sync.mjs must exit 0 in the no-git fallback path. ` +
    `Got ${result.status}; stderr: ${result.stderr}`);
  // Fallback marker should be in build_rev.txt
  const artefact = join(tmpCwd, 'build_rev.txt');
  if (existsSync(artefact)) {
    const content = readFileSync(artefact, 'utf8');
    assert.ok(
      content.includes('git-unavailable-at-build') ||
      content.match(/^[0-9a-f]{7,8} · /),
      `Expected fallback or live build_rev; got: ${content}`,
    );
  }
});

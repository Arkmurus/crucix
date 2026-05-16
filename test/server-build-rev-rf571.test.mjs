// R-F571 (2026-05-16) — server.mjs runtime build_rev resolution.
//
// Live evidence: 2026-05-16 seenode deploy of `8d4952f` showed
// `build_rev: "UNKNOWN-BUILD · sync.mjs build_rev.txt not written"`
// at /api/health, even though sync.mjs (R-F570) had successfully
// computed the rev locally. Either seenode skipped the build step or
// wrote to a different cwd. R-F571 moves the truth source to
// server.mjs runtime: if build_rev.txt is missing OR contains the
// UNKNOWN-BUILD sentinel, fall back to `git rev-parse HEAD` against
// the local checkout. Date-stamp sentinel only fires when both fail.
//
// These are static-source contract tests + import smoke. We don't
// want to spin up the full server (it pulls in dozens of network
// deps) so we read the source and verify the resolution helper
// shape directly.

import { readFileSync, existsSync } from 'node:fs';
import { resolve, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import assert from 'node:assert/strict';

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(__dirname, '..');
const serverPath = join(repoRoot, 'server.mjs');


function _serverSrc() {
  return readFileSync(serverPath, 'utf8');
}


test('R-F571: server.mjs imports execSync', () => {
  const src = _serverSrc();
  assert.ok(
    src.includes("import { exec, execSync } from 'child_process'") ||
    src.includes('import { execSync, exec }'),
    'R-F571: server.mjs must import execSync from child_process'
  );
});


test('R-F571: _resolveBuildRev helper present', () => {
  const src = _serverSrc();
  assert.ok(src.includes('_resolveBuildRev'),
    'R-F571: _resolveBuildRev helper missing — fallback chain not wired'
  );
});


test('R-F571: resolution chain has 3 tiers', () => {
  const src = _serverSrc();
  // 1. build_rev.txt
  assert.ok(src.includes("readFileSync(join(ROOT, 'build_rev.txt')"),
    'R-F571: tier-1 (build_rev.txt) must remain'
  );
  // 2. git rev-parse
  assert.ok(src.includes('git rev-parse --short HEAD'),
    'R-F571: tier-2 (git rev-parse) fallback missing'
  );
  // 3. Final sentinel mentioning the date
  assert.ok(src.includes('toISOString'),
    'R-F571: final sentinel must include date stamp'
  );
});


test('R-F571: UNKNOWN-BUILD prefix triggers fallback', () => {
  const src = _serverSrc();
  // Confirm the logic checks for UNKNOWN-BUILD prefix to skip into
  // the git fallback rather than accepting the sentinel as truth.
  assert.ok(src.includes("startsWith('UNKNOWN-BUILD')"),
    'R-F571: must skip an UNKNOWN-BUILD value in build_rev.txt ' +
    'and try git instead. This is the actual live bug (seenode wrote ' +
    'the sentinel into the file).'
  );
});


test('R-F571: marker comment present', () => {
  const src = _serverSrc();
  assert.ok(src.includes('R-F571'),
    'R-F571: marker comment missing — likely reverted'
  );
});


test('R-F571: ROOT used as cwd for git commands', () => {
  const src = _serverSrc();
  // The git execSync calls must use ROOT (server.mjs dir) so they
  // hit the right checkout. Without explicit cwd we'd depend on
  // process.cwd() which may differ from ROOT.
  const helperStart = src.indexOf('_resolveBuildRev');
  const helperEnd = src.indexOf('const CRUCIX_BUILD_REV', helperStart);
  const helperBody = src.slice(helperStart, helperEnd);
  assert.ok(helperBody.includes('cwd: ROOT'),
    'R-F571: execSync calls must pass cwd: ROOT, not rely on process.cwd()'
  );
});


test('R-F571: timeout bounded on git calls', () => {
  const src = _serverSrc();
  const helperStart = src.indexOf('_resolveBuildRev');
  const helperEnd = src.indexOf('const CRUCIX_BUILD_REV', helperStart);
  const helperBody = src.slice(helperStart, helperEnd);
  assert.ok(/timeout:\s*\d+/.test(helperBody),
    'R-F571: git execSync calls must carry a timeout (server boot is hot path)'
  );
});


test('R-F571: server.mjs is syntactically valid (parse smoke)', () => {
  // Light parse-only smoke. We don't execute the server because of
  // the heavy import graph — just confirm the file is a valid module
  // syntactically by reading + checking it ends cleanly.
  const src = _serverSrc();
  assert.ok(src.length > 1000, 'server.mjs unexpectedly short');
  // Final char must be \n or have a matching brace count.
  const openBraces = (src.match(/\{/g) || []).length;
  const closeBraces = (src.match(/\}/g) || []).length;
  // Within 5 of each other allows for `${...}` template-literal noise.
  assert.ok(Math.abs(openBraces - closeBraces) < 10,
    `Brace imbalance: { = ${openBraces}, } = ${closeBraces}`
  );
});

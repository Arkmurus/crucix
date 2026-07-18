// R-F2734 — CI must ENFORCE the Node gates built in R-F2729/R-F2731:
//  - the node syntax lint (`npm run lint`) — blocks the R-F2119/2120 un-importable-tree class;
//  - the SAFE + TERMINATING test run (`npm test` = node --test --test-force-exit --import net_guard)
//    so CI never hangs on an open handle and never hits a live service.
// This pins the wiring so it can't silently regress back to a bare, unguarded `node --test`.

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const ci = readFileSync(join(root, '.github', 'workflows', 'ci.yml'), 'utf8');
const pkg = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'));

describe('R-F2734 CI enforces the Node gates', () => {
  it('package.json defines the gate scripts they reference', () => {
    assert.equal(pkg.scripts.lint, 'node scripts/lint.mjs');
    assert.ok(pkg.scripts.test.includes('--test-force-exit'), 'npm test must force-exit (terminate)');
    assert.ok(pkg.scripts.test.includes('net_guard'), 'npm test must import the network guard (no live calls)');
  });

  it('CI runs the node syntax lint as a hard gate', () => {
    assert.match(ci, /run: npm run lint/, 'CI must run `npm run lint`');
  });

  it('CI runs the SAFE+terminating `npm test`, NOT a bare unguarded `node --test test/`', () => {
    assert.match(ci, /run: npm test/, 'CI must run `npm test` (guarded + force-exit)');
    assert.doesNotMatch(ci, /run: node --test test\/\s*$/m, 'CI must not run a bare unguarded node --test test/');
  });
});

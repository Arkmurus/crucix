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

  it('CI runs the SAFE+terminating suite, NOT a bare unguarded `node --test test/`', () => {
    // R-F3862 replaced the bare `run: npm test` step with the failure-SET gate,
    // which invokes `npm test` itself and additionally fails the build on NEW
    // failures — the bare step could not, because continue-on-error was needed
    // for the standing set. The PROPERTY this test defends (the suite runs
    // guarded and force-exiting) is unchanged; only the wrapper moved, so the
    // assertion follows it to where `npm test` is actually invoked.
    const gate = readFileSync(join(root, 'scripts', 'admin', 'node_suite_baseline.mjs'), 'utf8');
    const runsSuite = /run: npm test/.test(ci)
      || (/node scripts\/admin\/node_suite_baseline\.mjs/.test(ci) && /'npm'.*'test'/s.test(gate));
    assert.ok(runsSuite, 'CI must run the guarded, force-exiting suite (directly or via the gate)');
    assert.doesNotMatch(ci, /run: node --test test\/\s*$/m, 'CI must not run a bare unguarded node --test test/');
  });

  it('every node --test step in CI imports the network guard', () => {
    // R-F3862 — a step that runs node --test directly (the DOM-XSS guards) must
    // still be unable to reach a live service, the R-F2731 property.
    // Split into STEP BLOCKS rather than regex-windowing around the match.
    // A window is wrong twice over here: YAML comments mention `node --test` in
    // prose, and a folded `run: >` command continues across lines, so any
    // lookahead that stops at the next line-initial `-` cuts the command off at
    // its own `--import` flag and never sees the guard it is looking for.
    const NL = String.fromCharCode(10);
    const blocks = ci.split(new RegExp(`${NL}(?=      - (?:name|uses):)`));
    let checked = 0;
    for (const block of blocks) {
      const code = block.split(NL).filter((l) => !/^\s*#/.test(l)).join(NL);
      if (!/node --test/.test(code)) continue;
      checked += 1;
      assert.match(code, /net_guard\.mjs/,
        `a node --test step runs without the network guard:${NL}${code.slice(0, 200)}`);
    }
    assert.ok(checked > 0, 'found no node --test step at all — this check has gone blind');
  });
});

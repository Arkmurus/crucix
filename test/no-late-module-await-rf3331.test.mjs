// R-F3331 — no module-scope `await` after the first test() registration.
//
// THE FAILURE THIS PREVENTS, measured not theorised:
//
// `npm test` (package.json) runs `node --test --test-force-exit`. The runner
// starts draining as soon as tests are registered. A test registered AFTER a
// module-scope `await` can miss that window and is reported
//
//     'Promise resolution is still pending but the event loop has already resolved'
//
// which is a CANCEL, not an assertion failure — and the file passes under a bare
// `node --test`, so it reads as flakiness. test/admin-suspend-revokes-session
// -rf2986.test.mjs sat like that: 2 tests above its `await import`, 3 below, and
// exactly those 3 were red in every suite run for weeks. Nothing about R-F2986
// was broken. R-F3328's new test hit the identical trap while being written.
//
// SCOPE, deliberately narrow — this is the discriminator, and it was measured
// rather than assumed. A first sweep flagged 13 files on "has an await import
// after a test()"; running all 13 under the real runner flags showed 12 of them
// cancel NOTHING, because their awaits are INSIDE test bodies, where awaiting is
// the entire point. Only a MODULE-SCOPE await defers registration. So the rule
// keys on column 0: an unindented `await` line is at module scope, an indented
// one is inside a function. That narrowing takes the guard from 13 hits (12 of
// them noise, and a guard that cries wolf gets switched off) to exactly the 1
// file that actually broke.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

const TEST_DIR = 'test';

// A registration at module scope: `test(`, `it(`, `describe(`, or `test.skip(`
// starting at column 0.
const REGISTRATION = /^(test|it|describe)[\s.(]/;
// A module-scope await STATEMENT. Three real shapes, and no more:
//   await something();
//   const { x } = await import('...');
//   } = await import('...');        <- closing line of a multi-line destructure
//
// The first cut here was `/^[^\s].*\bawait\b/` — "an unindented line containing
// the word await" — and it immediately reported
// sec-edgar-parallel-cik-rf2268.test.mjs, whose line 47 is
//
//     test('R-F2268: the sequential await-in-for-loop is gone from the source', ...
//
// The word "await" in a TEST TITLE. That file cancels nothing. Acting on it
// would have "fixed" a healthy file to satisfy a broken detector, which is the
// same failure as any other instrument that was never checked against a case it
// should NOT fire on. Hence the negative fixtures below: this guard has to prove
// it stays silent as well as prove it can speak.
const MODULE_AWAIT = /^(await\s|(?:const|let|var)\s[^=]*=\s*await\s|\}\s*=\s*await\s)/;

function offenders(source) {
  const lines = source.split('\n');
  let firstRegistration = -1;
  const late = [];
  lines.forEach((line, i) => {
    if (firstRegistration < 0 && REGISTRATION.test(line)) firstRegistration = i;
    if (firstRegistration >= 0 && MODULE_AWAIT.test(line)) {
      late.push({ line: i + 1, text: line.trim().slice(0, 90) });
    }
  });
  return late;
}

test('R-F3331: no test file awaits at module scope after registering a test', () => {
  const files = readdirSync(TEST_DIR).filter((f) => f.endsWith('.test.mjs'));
  assert.ok(files.length > 0, 'sanity: the scan found test files');

  const bad = [];
  for (const f of files) {
    const late = offenders(readFileSync(join(TEST_DIR, f), 'utf8'));
    if (late.length) bad.push(`  ${f}: module-scope await at line(s) ${late.map((l) => l.line).join(', ')}`);
  }

  assert.deepEqual(bad, [],
    'These files register a test BEFORE a module-scope await. Under '
    + '--test-force-exit the tests below that await are silently CANCELLED '
    + '("Promise resolution is still pending"), which reads as flakiness. Move '
    + 'every module-scope await (and the env setup it depends on) ABOVE the '
    + `first test() call:\n${bad.join('\n')}`);
});

// The guard must be able to fail. Rather than trusting the regexes against the
// tree — where a green result is equally consistent with "the tree is clean" and
// "the detector is broken" — assert them against literal fixtures.
test('R-F3331: the detector fires on the shape that actually broke', () => {
  const broken = [
    "import test from 'node:test';",
    "test('a', () => {});",
    "const { x } = await import('../lib/x.mjs');",
    "test('b', () => {});",
  ].join('\n');
  assert.equal(offenders(broken).length, 1, 'must flag a module-scope await after a test()');
});

test('R-F3331: the detector fires on a multi-line destructured import', () => {
  const broken = [
    "test('a', () => {});",
    'const {',
    '  createUser, createToken,',
    "} = await import('../lib/auth/users.mjs');",
  ].join('\n');
  assert.equal(offenders(broken).length, 1,
    'the closing line of a multi-line destructure is the rf2986 shape exactly');
});

test('R-F3331: the detector ignores awaits inside test bodies', () => {
  const fine = [
    "import test from 'node:test';",
    "const { x } = await import('../lib/x.mjs');",   // before any test: correct
    "test('a', async () => {",
    "  const fresh = await import('../lib/x.mjs?restart=1');",  // indented: fine
    "  await fresh.doThing();",
    "});",
  ].join('\n');
  assert.deepEqual(offenders(fine), [],
    'awaiting inside a test body is normal and must not be flagged — 12 of the '
    + '13 files a naive scan reported were this shape');
});

test('R-F3331: the detector ignores the word "await" in a test title', () => {
  const fine = [
    "test('a', () => {});",
    "test('R-F2268: the sequential await-in-for-loop is gone', () => {});",
  ].join('\n');
  assert.deepEqual(offenders(fine), [],
    'this exact line was the guard\'s own first false positive '
    + '(sec-edgar-parallel-cik-rf2268.test.mjs:47)');
});

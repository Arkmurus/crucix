// C-33 completion / R-F3918 — the "durable" learning store was on the EPHEMERAL app dir.
//
// R-F3917 pointed /api/source-health at `source_history.json` instead of a volatile
// in-memory object, on the strength of that file being persistent. It was not.
//
// MEASURED on live aria-web immediately after the R-F3917 deploy:
//
//   /app/runs/learning/  -> every file stamped Aug 12 07:21  (the deploy minute)
//   /data/               -> files from Jul  3, Jul 11, Aug  1  (genuinely persistent)
//
//   sources: 50
//   GDELT   sweeps=1 totalOk=1 totalFail=0
//   oldest sweep: 2026-08-12T07:21:30Z          <- post-deploy; the history was WIPED
//
// `LEARNING_DIR = join(process.cwd(), 'runs', 'learning')` resolves inside the
// container image, which Fly replaces wholesale on every deploy. The volume is mounted
// at /data (fly.web.toml). So the store survived an in-container process restart but
// NOT a deploy — and deploys are the dominant restart cause, which is exactly the
// window C-33 exists to close.
//
// This is the same lesson as C-29 one turn later: "durable" was asserted, not measured.
// The fix follows the convention already used at four sites in this tier
// (`existsSync('/data') ? '/data' : <local>`), rather than inventing a new mechanism.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'fs';
import { resolveLearningDir } from '../lib/self/learning_store.mjs';

const SRC = readFileSync(new URL('../lib/self/learning_store.mjs', import.meta.url), 'utf8');

// path.join uses a backslash on Windows; production is Linux. Compare
// separator-agnostically so this asserts the CHOICE of root, not host path syntax.
const norm = (v) => v.replace(/\\/g, '/');

test('C-33: prefers the mounted /data volume when it exists', () => {
  const dir = resolveLearningDir({ exists: (p) => p === '/data', cwd: () => '/app' });
  assert.ok(
    norm(dir).startsWith('/data'),
    `learning store must live on the durable volume, got ${dir} — on the app dir it ` +
      'is wiped by every deploy, so "reliability history" resets exactly when it matters',
  );
});

test('C-33: falls back to the working tree off-Fly (local dev keeps working)', () => {
  const dir = resolveLearningDir({ exists: () => false, cwd: () => '/home/dev/aria' });
  assert.ok(
    norm(dir).includes('runs/learning'),
    `expected the local runs/learning fallback, got ${dir}`,
  );
  assert.ok(!norm(dir).startsWith('/data'), 'must not point at a volume that is not mounted');
});

test('C-33: the resolver is what the module actually uses', () => {
  // Guards the shape that made this defect invisible: a correct helper that nothing
  // calls is worth nothing (C-27's "instrument with no reader", R-F3889).
  assert.ok(
    /const\s+LEARNING_DIR\s*=\s*resolveLearningDir\(/.test(SRC),
    'LEARNING_DIR must be produced by resolveLearningDir(), or the tested logic and ' +
      'the shipped logic are two different things',
  );
  assert.ok(
    !/const\s+LEARNING_DIR\s*=\s*join\(process\.cwd\(\)/.test(SRC),
    'the ephemeral app-dir path is back — it is wiped by every deploy',
  );
});

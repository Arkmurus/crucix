// test/quota-durability-rf2829.test.mjs
//
// R-F2829 — quota counters must survive a restart.
//
// THE DEFECT. lib/billing/quotas.mjs persists through redisGet/redisSet from
// lib/persist/store.mjs. R-F383 retired Upstash and left those exports as
// deliberate NO-OPS — `redisConfigured()` returns a hardcoded `false`, `redisGet`
// returns null. Its header says the ~20 importing modules were safe because
// "their `if (redisConfigured()) { … }` branches naturally short-circuit", which is
// true for the FILE-FIRST stores: they fall back to a JSON file on the /data volume.
//
// quotas.mjs has no file fallback. Its short-circuit lands on in-process Maps
// (_memCounters / _memReset), so every counter resets to zero on every restart and
// every deploy. The DD cap that lib/billing/tiers.mjs sets at 5/month was in
// practice "5 per deploy" — and aria-web was deployed nine times in one day.
//
// This is the same disease lib/auth/users.mjs:18-21 already documents: state that
// looked persisted, lived in a process, and silently reset — there it churned user
// ids and orphaned conversation history. The fix is the same: the /data volume.
//
// Run: node --test test/quota-durability-rf2829.test.mjs

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, existsSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

/** Fresh module instance, as if the process had just restarted. */
async function freshQuotas(persistDir) {
  process.env.PERSIST_DIR = persistDir;
  // Cache-bust the ESM import so we genuinely re-execute module init.
  const mod = await import(`../lib/billing/quotas.mjs?restart=${Math.random()}`);
  return mod;
}

describe('R-F2829 — quota counters survive a restart', () => {
  test('CAPABILITY: consumption persists across a simulated deploy', async () => {
    const dir = mkdtempSync(path.join(tmpdir(), 'quota-'));
    try {
      const q1 = await freshQuotas(dir);
      // Free tier: 5 ddRun/month (lib/billing/tiers.mjs).
      for (let i = 0; i < 3; i++) {
        const r = await q1.checkAndConsume('user-123', 'free', 'ddRun');
        assert.equal(r.allowed, true, `run ${i + 1} should be allowed`);
      }
      const before = await q1.checkAndConsume('user-123', 'free', 'ddRun');
      assert.equal(before.current, 4, 'four runs consumed in this process');

      // ── the deploy ──
      const q2 = await freshQuotas(dir);
      const after = await q2.checkAndConsume('user-123', 'free', 'ddRun');
      assert.equal(after.current, 5,
        `counter reset across restart (saw ${after.current}, expected 5) — ` +
        'the cap is per-deploy, not per-month');

      const blocked = await q2.checkAndConsume('user-123', 'free', 'ddRun');
      assert.equal(blocked.allowed, false,
        'the 6th run in a month must be blocked even though a deploy intervened');
      assert.match(blocked.reason, /cap reached/i);
    } finally { rmSync(dir, { recursive: true, force: true }); }
  });

  test('the counter is written to the persistent volume, not the repo', async () => {
    const dir = mkdtempSync(path.join(tmpdir(), 'quota-'));
    try {
      const q = await freshQuotas(dir);
      await q.checkAndConsume('user-abc', 'free', 'ddRun');
      const f = path.join(dir, 'quotas.json');
      assert.ok(existsSync(f), 'quota state must live on PERSIST_DIR (/data in prod)');
      const raw = JSON.parse(readFileSync(f, 'utf8'));
      const keys = Object.keys(raw);
      assert.ok(keys.some((k) => k.includes('user-abc') && k.includes('dd')),
        `expected a ddRun key for user-abc, got ${JSON.stringify(keys)}`);
    } finally { rmSync(dir, { recursive: true, force: true }); }
  });

  test('expired periods do not resurrect, and do not grow the file forever', async () => {
    const dir = mkdtempSync(path.join(tmpdir(), 'quota-'));
    try {
      const q = await freshQuotas(dir);
      await q.checkAndConsume('u1', 'free', 'message');
      const f = path.join(dir, 'quotas.json');
      const withExpired = JSON.parse(readFileSync(f, 'utf8'));
      withExpired['crucix:quota:dd:stale-user:1970-01'] = { count: 99, exp: 1 };
      const { writeFileSync } = await import('node:fs');
      writeFileSync(f, JSON.stringify(withExpired));

      const q2 = await freshQuotas(dir);
      const r = await q2.checkAndConsume('stale-user', 'free', 'ddRun');
      assert.equal(r.current, 1,
        'an expired period must not carry its count into the new period');
      const after = JSON.parse(readFileSync(f, 'utf8'));
      assert.ok(!Object.keys(after).includes('crucix:quota:dd:stale-user:1970-01'),
        'expired entries must be pruned so the file cannot grow without bound');
    } finally { rmSync(dir, { recursive: true, force: true }); }
  });

  test('a broken persistence layer degrades to memory, never blocks the request', async () => {
    // Telemetry/persistence must never take down the paid path. Pointing
    // PERSIST_DIR at a file (not a directory) makes every write fail.
    const dir = mkdtempSync(path.join(tmpdir(), 'quota-'));
    const notADir = path.join(dir, 'wall');
    const { writeFileSync } = await import('node:fs');
    writeFileSync(notADir, 'x');
    try {
      const q = await freshQuotas(notADir);
      const r = await q.checkAndConsume('u9', 'free', 'ddRun');
      assert.equal(r.allowed, true, 'a persistence fault must not deny a paying user');
      assert.equal(r.current, 1, 'in-memory counting still works');
    } finally { rmSync(dir, { recursive: true, force: true }); }
  });

  test('ANTI-REGRESSION: quotas no longer rely solely on the retired redis shim', async () => {
    const src = readFileSync(
      path.resolve(path.dirname(new URL(import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, '$1'),
        '..', 'lib', 'billing', 'quotas.mjs'), 'utf8');
    assert.ok(/PERSIST_DIR|quotas\.json/.test(src),
      'quotas.mjs must persist to the /data volume — redisConfigured() is a ' +
      'hardcoded false since R-F383, so the redis branch is dead code');
  });
});

// test/persist-store-upstash-free-rf383.test.mjs
// Capability test for R-F383 + R-F384.
//
// Asserts the user-visible symptoms after Upstash retirement on seenode:
//   1. PersistStore reads + writes work end-to-end with NO Upstash env vars
//      set (the env vars are intentionally cleared before import).
//   2. The redisGet / redisSet / redisDel / redisPush exports are no-ops,
//      so the 20+ legacy callers' "if (redisConfigured()) { … }" branches
//      naturally short-circuit without hitting the network.
//   3. redisLockAcquire / redisLockRelease still provide single-process
//      mutual exclusion via the new local Map — BD-sweep cron contention
//      is the only real caller and it must not be silently disabled.
//   4. Data survives a "restart" (a second PersistStore instance against
//      the same file path picks up the previously-written value).
//
// Run: node test/persist-store-upstash-free-rf383.test.mjs

import { mkdtempSync, rmSync, existsSync, readFileSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';

// Clear Upstash env vars BEFORE importing the store. If any code path tries
// to hit Upstash, it will trip on the missing URL and we'll see network errors.
delete process.env.UPSTASH_REDIS_URL;
delete process.env.UPSTASH_REDIS_TOKEN;

// Intercept fetch so we can assert nothing tries to call Upstash.
const _origFetch = globalThis.fetch;
let _fetchCallCount = 0;
globalThis.fetch = async (...args) => {
  _fetchCallCount += 1;
  throw new Error(`unexpected fetch() call: ${args[0]}`);
};

const {
  PersistStore,
  redisGet,
  redisSet,
  redisDel,
  redisPush,
  redisConfigured,
  redisLockAcquire,
  redisLockRelease,
} = await import('../lib/persist/store.mjs');

let failures = 0;
function check(label, cond) {
  if (cond) {
    console.log(`  ✓ ${label}`);
  } else {
    console.error(`  ✗ ${label}`);
    failures += 1;
  }
}

const tmp = mkdtempSync(join(tmpdir(), 'rf383-'));
try {
  console.log('R-F383 capability tests\n');

  // 1. redisConfigured + redis helpers are no-ops (no network)
  console.log('1. Redis helpers are no-ops');
  check('redisConfigured() returns false', redisConfigured() === false);
  check('redisGet returns null', (await redisGet('any')) === null);
  check('redisSet returns without throwing', (await redisSet('k', 'v')) === undefined);
  check('redisDel returns without throwing', (await redisDel('k')) === undefined);
  check('redisPush returns without throwing', (await redisPush('k', 'v')) === undefined);
  check('no fetch() calls fired so far', _fetchCallCount === 0);

  // 2. PersistStore writes + reads from file only
  console.log('\n2. PersistStore file-only round-trip');
  const path1 = join(tmp, 'subdir', 'a.json');
  const s1 = new PersistStore('crucix:test:a', path1, () => ({ count: 0 }));
  await s1.init();
  check('starts with default when no file exists', s1.read().count === 0);

  s1.write({ count: 7, label: 'hello' });
  check('write() creates the file', existsSync(path1));
  check('cached read matches what was written', s1.read().count === 7);

  const raw = JSON.parse(readFileSync(path1, 'utf8'));
  check('file contents match cache', raw.count === 7 && raw.label === 'hello');

  // 3. Survives "restart" — second instance loads from file
  console.log('\n3. Survives restart (second instance on same path)');
  const s2 = new PersistStore('crucix:test:a', path1, () => ({ count: 0 }));
  await s2.init();
  check('second instance reads value from disk', s2.read().count === 7);
  check('second instance reads label from disk', s2.read().label === 'hello');

  // 4. Process-local lock provides single-process mutex
  console.log('\n4. Process-local lock (replaces former Redis SET NX EX)');
  const ok1 = await redisLockAcquire('bd:sweep', 60);
  check('first acquire succeeds', ok1 === true);
  const ok2 = await redisLockAcquire('bd:sweep', 60);
  check('second acquire on same key fails (mutex held)', ok2 === false);
  await redisLockRelease('bd:sweep');
  const ok3 = await redisLockAcquire('bd:sweep', 60);
  check('acquire succeeds after release', ok3 === true);
  await redisLockRelease('bd:sweep');

  // Lock for a different key is independent
  const okA = await redisLockAcquire('bd:sweep', 60);
  const okB = await redisLockAcquire('telegram:throttle', 60);
  check('different keys are independent', okA === true && okB === true);
  await redisLockRelease('bd:sweep');
  await redisLockRelease('telegram:throttle');

  // Lock TTL expiry — short TTL should auto-release
  console.log('\n5. Lock TTL auto-expiry');
  const okShort = await redisLockAcquire('short-ttl-test', 1); // 1s
  check('short-TTL acquire succeeds', okShort === true);
  // The lock implementation clamps to min 5s for safety. Verify clamp is in
  // effect by trying to acquire again right away (should still be held).
  const stillHeld = await redisLockAcquire('short-ttl-test', 1);
  check('lock still held within clamped 5s window', stillHeld === false);
  await redisLockRelease('short-ttl-test');

  // 6. NO fetch() call fired in the entire test (= truly Upstash-free)
  console.log('\n6. Network independence');
  check('zero fetch() calls across the whole suite', _fetchCallCount === 0);

  console.log(`\n${failures === 0 ? '✓ ALL PASS' : `✗ ${failures} FAILURES`}`);
  process.exitCode = failures === 0 ? 0 : 1;
} finally {
  globalThis.fetch = _origFetch;
  try { rmSync(tmp, { recursive: true, force: true }); } catch {}
}

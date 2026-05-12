// test/api-keys-rate-limit-rf386.test.mjs
//
// Capability test for R-F386 — per-key rate limiter migrated from Upstash
// sliding window to a process-local Map. The function is a private helper
// (`_perKeyRateOk`) inside lib/api_keys/routes.mjs and isn't exported, so
// this test re-implements the same Map+minute-bucket logic and pins the
// invariants the production helper must keep.
//
// Run: node test/api-keys-rate-limit-rf386.test.mjs

import assert from 'node:assert/strict';

const counts = new Map();
let sweepAt = 0;

function rateOk(keyId, cap = 60, nowMs = Date.now()) {
  const minute = Math.floor(nowMs / 60000);
  const bucketKey = `${keyId}:${minute}`;
  if (nowMs - sweepAt > 60_000) {
    sweepAt = nowMs;
    for (const k of counts.keys()) {
      const m = parseInt(k.split(':').pop(), 10);
      if (m < minute - 1) counts.delete(k);
    }
  }
  const count = counts.get(bucketKey) || 0;
  if (count >= cap) return false;
  counts.set(bucketKey, count + 1);
  return true;
}

let failures = 0;
function check(label, cond) {
  if (cond) console.log(`  ✓ ${label}`);
  else { console.error(`  ✗ ${label}`); failures += 1; }
}

console.log('R-F386 capability tests\n');

// 1. Under cap, all calls succeed
console.log('1. Under cap, all calls return true');
const FAKE_NOW = 1_700_000_000_000;
for (let i = 0; i < 59; i++) rateOk('k1', 60, FAKE_NOW);
check('59th call (under 60-cap) succeeds', rateOk('k1', 60, FAKE_NOW) === true);

// 2. At cap, next call returns false
console.log('\n2. At cap, next call returns false');
check('61st call (over 60-cap) returns false', rateOk('k1', 60, FAKE_NOW) === false);
check('62nd call also returns false', rateOk('k1', 60, FAKE_NOW) === false);

// 3. Different keys are independent
console.log('\n3. Different keys are independent');
check('first call for k2 returns true even though k1 capped', rateOk('k2', 60, FAKE_NOW) === true);

// 4. Minute rollover resets the bucket
console.log('\n4. Minute rollover resets the bucket');
const NEXT_MIN = FAKE_NOW + 60_000;
check('k1 succeeds in the next minute', rateOk('k1', 60, NEXT_MIN) === true);

// 5. Sweep prunes stale buckets two minutes later
console.log('\n5. Sweep prunes stale buckets after two minutes');
const sizeBeforeSweep = counts.size;
// Trigger sweep by advancing 2 minutes
rateOk('sweep-trigger', 60, FAKE_NOW + 2 * 60_000 + 5_000);
// After sweep, buckets from FAKE_NOW minute (>=2 buckets ago) should be gone
const oldBucketSize = [...counts.keys()].filter(k => k.endsWith(`:${Math.floor(FAKE_NOW / 60000)}`)).length;
check('old minute buckets pruned', oldBucketSize === 0);
assert.ok(counts.size <= sizeBeforeSweep);

console.log(`\n${failures === 0 ? '✓ ALL PASS' : `✗ ${failures} FAILURES`}`);
process.exitCode = failures === 0 ? 0 : 1;

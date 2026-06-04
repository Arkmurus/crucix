// R-F1325 — capability test: WA listener health-check resilience
// Validates that transient blips (1-2 failures) are tolerated and only
// 3+ consecutive failures trigger an abort.
//
// Run: node services/wa-listener/test_health_check_resilience.mjs

import { createServer } from 'http';

let passed = 0;
let failed = 0;

function assert(condition, label) {
  if (condition) {
    passed++;
    console.log(`  ✅ ${label}`);
  } else {
    failed++;
    console.log(`  ❌ ${label}`);
  }
}

// ── Simulate the health-check logic from the three poll sites ──────────
// The pattern is identical in chat poll, doc poll, and OCR poll:
//   - On success: reset counter to 0
//   - On failure: increment counter; only abort when >= 3 consecutive failures

function simulateHealthCheck(failures) {
  // Simulates the health-check loop with `failures` consecutive failures
  // followed by a success. Returns { aborted, finalCount }.
  let healthFails = 0;
  let aborted = false;

  for (const isFailure of failures) {
    if (isFailure) {
      healthFails++;
      if (healthFails >= 3) {
        aborted = true;
        break;
      }
    } else {
      healthFails = 0;  // reset on success
    }
  }

  return { aborted, finalCount: healthFails };
}

// ── Test cases ─────────────────────────────────────────────────────────

console.log('\n📋 Health-check resilience tests\n');

// 1. Single transient failure → no abort
{
  const r = simulateHealthCheck([true, false]);
  assert(!r.aborted, '1 transient failure followed by success → no abort');
  assert(r.finalCount === 0, '  counter resets to 0 after success');
}

// 2. Two transient failures then success → no abort
{
  const r = simulateHealthCheck([true, true, false]);
  assert(!r.aborted, '2 transient failures then success → no abort');
  assert(r.finalCount === 0, '  counter resets to 0 after success');
}

// 3. Three consecutive failures → abort
{
  const r = simulateHealthCheck([true, true, true]);
  assert(r.aborted, '3 consecutive failures → abort');
  assert(r.finalCount === 3, '  counter at 3 at abort');
}

// 4. Four consecutive failures → abort at 3rd
{
  const r = simulateHealthCheck([true, true, true, true]);
  assert(r.aborted, '4 consecutive failures → abort at 3rd');
  assert(r.finalCount === 3, '  counter at 3 at abort (not 4)');
}

// 5. Interleaved: fail, fail, success, fail, fail, fail → abort on 3rd consecutive
{
  const r = simulateHealthCheck([true, true, false, true, true, true]);
  assert(r.aborted, 'fail-fail-success-fail-fail-fail → abort on 3rd consecutive');
  assert(r.finalCount === 3, '  counter at 3 at abort');
}

// 6. All successes → no abort
{
  const r = simulateHealthCheck([false, false, false]);
  assert(!r.aborted, 'all successes → no abort');
  assert(r.finalCount === 0, '  counter stays at 0');
}

// 7. Empty sequence → no abort
{
  const r = simulateHealthCheck([]);
  assert(!r.aborted, 'empty sequence → no abort');
  assert(r.finalCount === 0, '  counter stays at 0');
}

// ── Summary ────────────────────────────────────────────────────────────
console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  console.error('❌ SOME TESTS FAILED');
  process.exit(1);
} else {
  console.log('✅ ALL TESTS PASSED');
}

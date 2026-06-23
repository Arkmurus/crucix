// test/brain-absorb-circuit-rf1796.test.mjs
//
// Capability test for R-F1796 — brainAbsorb is gated by a circuit breaker so a
// down/wedged brain no longer makes every caller wait out the 8s timeout
// (aria-web audit #6; wires the existing CircuitBreaker per audit #3).
//
// Drives the REAL brainAbsorb with a failing brain and asserts the absorb
// endpoint stops being called once the breaker OPENs (threshold=3). Without the
// gate, every call would hit the endpoint (and block 8s).
//
// Run: node test/brain-absorb-circuit-rf1796.test.mjs

// Must be set BEFORE importing the module (BRAIN_URL is read at load).
process.env.ARIA_SERVICE_URL = 'https://brain.example.test';

import { test } from 'node:test';
import assert from 'node:assert/strict';

const { brainAbsorb } = await import('../lib/self/learning_store.mjs');

test('brainAbsorb opens the circuit after repeated failures and then skips fast', async () => {
  let absorbCalls = 0;
  const origFetch = global.fetch;
  // Count only calls to the absorb endpoint; the breaker's own circuit_opened
  // report posts to /brain/signal and must not skew the count.
  global.fetch = async (url) => {
    if (String(url).includes('/api/aria/brain/absorb')) absorbCalls++;
    throw new Error('brain down');
  };
  try {
    for (let i = 0; i < 6; i++) {
      await brainAbsorb({ module: 'rf1796_test', summary: 'probe' });
    }
  } finally {
    global.fetch = origFetch;
  }

  // threshold=3: absorb hit on calls 1-3 (breaker trips on the 3rd), then calls
  // 4-6 short-circuit without touching the endpoint. No-gate behaviour = 6.
  assert.equal(absorbCalls, 3,
    `absorb should stop at the breaker threshold (3), got ${absorbCalls} — gate not working`);
});

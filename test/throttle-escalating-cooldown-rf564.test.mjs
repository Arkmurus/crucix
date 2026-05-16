// R-F564 — escalating cooldown for chronic-broken sources.
//
// Pre-R-F564 the per-feed circuit breaker used a fixed 5-min cooldown.
// Sweeps fire every 5 min, so the cooldown matched the cadence: a feed
// that failed in sweep N would auto-reset before sweep N+1 and be
// re-attempted. DefenseWeb / UN News Africa / Breaking Defense had
// failed every sweep for hours on 2026-05-16 morning with no real
// throttling effect — each one still cost a 3-attempt × 14s fallback
// chain per sweep.
//
// R-F564 — when opts.escalating === true, each consecutive circuit trip
// doubles the cooldown up to 2^3 = 8× (40 min cap). recordSuccess
// clears the history.

import test from 'node:test';
import assert from 'node:assert/strict';
import { shouldSkip, recordFailure, recordSuccess } from '../lib/util/throttle.mjs';

// Time travel helper — the circuit reads Date.now() so we stub it
// rather than waiting wall-clock 5+ minutes.
let _now = 1_700_000_000_000;
const _origDateNow = Date.now;
function setNow(t) { _now = t; }
function advanceMs(ms) { _now += ms; }
function patchDateNow() { Date.now = () => _now; }
function restoreDateNow() { Date.now = _origDateNow; }

test('R-F564: legacy (non-escalating) circuit auto-resets after fixed cooldown', () => {
  patchDateNow();
  try {
    const key = 'test:rf556:legacy';
    recordSuccess(key); // clean start

    // 3 failures trip the circuit
    recordFailure(key);
    recordFailure(key);
    recordFailure(key);
    assert.equal(shouldSkip(key), true, 'circuit must be open after 3 failures');

    // 5 min later (default cooldown) — circuit auto-resets
    advanceMs(5 * 60 * 1000 + 1);
    assert.equal(shouldSkip(key), false, 'legacy cooldown is exactly 5 min');

    // 3 more failures trip again with the SAME 5-min cooldown (no escalation)
    recordFailure(key);
    recordFailure(key);
    recordFailure(key);
    advanceMs(5 * 60 * 1000 + 1);
    assert.equal(shouldSkip(key), false, 'legacy stays at fixed 5-min — no escalation');

    recordSuccess(key);
  } finally {
    restoreDateNow();
  }
});

test('R-F564: escalating cooldown doubles each consecutive trip', () => {
  patchDateNow();
  try {
    const key = 'test:rf556:escalating';
    const opts = { escalating: true };
    recordSuccess(key);

    // Trip 1 → cooldown = 5 min × 1 = 5 min
    recordFailure(key, opts);
    recordFailure(key, opts);
    recordFailure(key, opts);
    assert.equal(shouldSkip(key, opts), true, 'open after first trip');

    // 4 min later → still open
    advanceMs(4 * 60 * 1000);
    assert.equal(shouldSkip(key, opts), true, 'open at 4 min on first trip (5-min window)');

    // 6 min later → auto-reset (window expired)
    advanceMs(2 * 60 * 1000);
    assert.equal(shouldSkip(key, opts), false, 'first cooldown is 5 min');

    // Trip 2 → cooldown = 10 min
    recordFailure(key, opts);
    recordFailure(key, opts);
    recordFailure(key, opts);
    assert.equal(shouldSkip(key, opts), true);
    advanceMs(6 * 60 * 1000);
    assert.equal(shouldSkip(key, opts), true, 'still open at 6 min on 2nd trip (10-min window)');
    advanceMs(5 * 60 * 1000);
    assert.equal(shouldSkip(key, opts), false, '2nd trip cooldown is 10 min');

    // Trip 3 → cooldown = 20 min
    recordFailure(key, opts);
    recordFailure(key, opts);
    recordFailure(key, opts);
    advanceMs(15 * 60 * 1000);
    assert.equal(shouldSkip(key, opts), true, 'still open at 15 min on 3rd trip (20-min window)');
    advanceMs(6 * 60 * 1000);
    assert.equal(shouldSkip(key, opts), false, '3rd trip cooldown is 20 min');

    recordSuccess(key);
  } finally {
    restoreDateNow();
  }
});

test('R-F564: escalation caps at 8× (40 min)', () => {
  patchDateNow();
  try {
    const key = 'test:rf556:cap';
    const opts = { escalating: true };
    recordSuccess(key);

    // Drive 5 consecutive trips — without cap this would be 5min × 2^4 = 80 min.
    // With cap of 8× = 40 min.
    for (let i = 0; i < 5; i++) {
      recordFailure(key, opts);
      recordFailure(key, opts);
      recordFailure(key, opts);
      advanceMs(50 * 60 * 1000); // long enough to clear any cooldown
      shouldSkip(key, opts); // triggers the reset
    }

    // Now trip one more time — cooldown should be capped at 40 min
    recordFailure(key, opts);
    recordFailure(key, opts);
    recordFailure(key, opts);
    advanceMs(39 * 60 * 1000);
    assert.equal(shouldSkip(key, opts), true, 'still open at 39 min — confirms ≥40-min window');
    advanceMs(2 * 60 * 1000);
    assert.equal(shouldSkip(key, opts), false, 'cap is 40 min — must be open at 41 min');

    recordSuccess(key);
  } finally {
    restoreDateNow();
  }
});

test('R-F564: recordSuccess clears consecutiveTrips history', () => {
  patchDateNow();
  try {
    const key = 'test:rf556:success-clears';
    const opts = { escalating: true };
    recordSuccess(key);

    // 3 trips → cooldown should be 20 min on next trip
    for (let i = 0; i < 3; i++) {
      recordFailure(key, opts);
      recordFailure(key, opts);
      recordFailure(key, opts);
      advanceMs(50 * 60 * 1000);
      shouldSkip(key, opts);
    }

    // Success → history clear
    recordSuccess(key);

    // Trip once → cooldown should be back to 5 min (not 40 min)
    recordFailure(key, opts);
    recordFailure(key, opts);
    recordFailure(key, opts);
    advanceMs(6 * 60 * 1000);
    assert.equal(
      shouldSkip(key, opts),
      false,
      'after recordSuccess the next trip restarts from base 5-min cooldown',
    );
  } finally {
    restoreDateNow();
  }
});

test('R-F564: backward compat — recordFailure without opts still works', () => {
  patchDateNow();
  try {
    const key = 'test:rf556:backward-compat';
    recordSuccess(key);
    recordFailure(key);
    recordFailure(key);
    recordFailure(key);
    assert.equal(shouldSkip(key), true);
    advanceMs(5 * 60 * 1000 + 1);
    assert.equal(shouldSkip(key), false);
    recordSuccess(key);
  } finally {
    restoreDateNow();
  }
});

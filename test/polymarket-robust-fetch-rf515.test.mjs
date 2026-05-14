// R-F515 — Polymarket migrated to robustFetch.
//
// User-visible symptom (live evidence 2026-05-14 09:25, 09:30, 09:40):
//   [Polymarket] Error: fetch failed
//   …and the source drops out of the sweep entirely until the next cycle.
//
// Root cause is shared-pool / DNS pressure when 48 sources fire at once.
// Per-source robustFetch + retry-with-jitter restores the source to the
// sweep on transient transport errors. Fail-fast still applies for real
// rate-limit / forbidden responses.
//
// This test stubs globalThis.fetch — it does not hit gamma-api.polymarket.com.

import test from 'node:test';
import assert from 'node:assert/strict';
import { _testResetRobustFetch } from '../apis/utils/fetch.mjs';
import { briefing } from '../apis/sources/polymarket.mjs';

const ORIGINAL_FETCH = globalThis.fetch;

function makeMarketsResponse(markets) {
  return new Response(JSON.stringify(markets), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

function fixtureMarkets() {
  return [
    {
      question: 'Will Russia escalate strikes on Ukraine grid this winter?',
      outcomes: ['Yes', 'No'],
      outcomePrices: ['0.82', '0.18'],
      volume24hr: 75000,
      volume: 500000,
      slug: 'russia-ukraine-grid-winter',
      endDate: '2026-03-01',
    },
    {
      question: 'Will OPEC announce additional oil production cuts?',
      outcomes: ['Yes', 'No'],
      outcomePrices: ['0.45', '0.55'],
      volume24hr: 30000,
      volume: 200000,
      slug: 'opec-cuts-2026',
      endDate: '2026-06-01',
    },
  ];
}

function makeTransportError(code) {
  const err = new TypeError('fetch failed');
  err.cause = { code };
  return err;
}

function resetFetchAndState() {
  globalThis.fetch = ORIGINAL_FETCH;
  _testResetRobustFetch();
}

test('R-F515: Polymarket recovers from transient ENOTFOUND on first attempt', async (t) => {
  t.after(resetFetchAndState);
  // Pre-R-F515: this single transport hiccup killed the source for the
  // whole sweep. Post-R-F515: robustFetch retries and the source returns
  // its normal market list.
  let calls = 0;
  globalThis.fetch = async () => {
    calls++;
    if (calls === 1) throw makeTransportError('ENOTFOUND');
    return makeMarketsResponse(fixtureMarkets());
  };

  const result = await briefing();
  assert.equal(result.error, null, 'briefing must not record a fatal error');
  assert.ok(result.markets.length >= 1, 'must return parsed markets after retry');
  assert.equal(calls, 2, '1 ENOTFOUND + 1 success = 2 underlying fetches');
});

test('R-F515: Polymarket recovers from UND_ERR_CONNECT_TIMEOUT mid-burst', async (t) => {
  t.after(resetFetchAndState);
  let calls = 0;
  globalThis.fetch = async () => {
    calls++;
    if (calls < 3) throw makeTransportError('UND_ERR_CONNECT_TIMEOUT');
    return makeMarketsResponse(fixtureMarkets());
  };

  const result = await briefing();
  assert.equal(result.error, null);
  assert.ok(result.markets.length >= 1);
  assert.equal(calls, 3, 'two transient timeouts then recovery');
});

test('R-F515: Polymarket still reports error when transport fails terminally', async (t) => {
  t.after(resetFetchAndState);
  // Honesty check: if every retry fails, Polymarket must still log a
  // structured error rather than silently returning empty markets.
  let calls = 0;
  globalThis.fetch = async () => { calls++; throw makeTransportError('ENOTFOUND'); };

  const result = await briefing();
  assert.ok(result.error, 'terminal failure must populate result.error');
  assert.equal(result.markets.length, 0);
  // robustFetch default retries=2, plus 2 retries inside polymarket.mjs options → 3 attempts total
  assert.equal(calls, 3, '1 initial + 2 retries from polymarket call site');
});

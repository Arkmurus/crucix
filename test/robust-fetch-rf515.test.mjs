// R-F515 — robustFetch wrapper for sweep-burst resilience.
//
// Live evidence 2026-05-14 09:25, 09:30, 09:40 sweeps: 9 unrelated upstream
// domains (Polymarket, NVD, ransomwatch, AU PSC, GRIP, BICC, ISS Africa,
// EU TED, World Bank, UK GOV) failed in the same ~100ms window with
// `fetch failed` / `UND_ERR_CONNECT_TIMEOUT`. Signature of shared-undici-
// pool / DNS-resolver pressure when briefing.mjs fires all 48 sources in
// one Promise.allSettled.
//
// robustFetch adds: per-host in-flight cap, exp-backoff retry on transient
// transport errors, fail-fast on rate-limit / forbidden statuses.
//
// These tests stub globalThis.fetch — they do not hit the network.

import test from 'node:test';
import assert from 'node:assert/strict';
import {
  robustFetch,
  _testResetRobustFetch,
  _testInspectHost,
} from '../apis/utils/fetch.mjs';

const ORIGINAL_FETCH = globalThis.fetch;

function makeResponse(status = 200, body = '{}') {
  return new Response(body, { status, headers: { 'Content-Type': 'application/json' } });
}

// Build an Error that mimics undici's `fetch failed` with a `cause.code`,
// which is what Node 22 / undici 7 produce on ENOTFOUND / ECONNRESET etc.
function makeTransportError(code) {
  const err = new TypeError('fetch failed');
  err.cause = { code };
  return err;
}

function resetFetchAndState() {
  globalThis.fetch = ORIGINAL_FETCH;
  _testResetRobustFetch();
}

test('R-F515: 200 response is returned unchanged on first attempt', async (t) => {
  t.after(resetFetchAndState);
  let calls = 0;
  globalThis.fetch = async () => { calls++; return makeResponse(200, '{"ok":true}'); };
  const res = await robustFetch('https://example.test/x');
  assert.equal(res.status, 200);
  assert.equal(calls, 1, 'must not retry on success');
  const body = await res.json();
  assert.deepEqual(body, { ok: true });
});

test('R-F515: ENOTFOUND retries with backoff and eventually succeeds', async (t) => {
  t.after(resetFetchAndState);
  let calls = 0;
  globalThis.fetch = async () => {
    calls++;
    if (calls < 3) throw makeTransportError('ENOTFOUND');
    return makeResponse(200, '{"recovered":true}');
  };
  const res = await robustFetch('https://example.test/y', {
    retries: 2,
    retryBackoffMs: 1, // keep test fast
  });
  assert.equal(res.status, 200);
  assert.equal(calls, 3, 'must retry twice then succeed on 3rd');
  const body = await res.json();
  assert.deepEqual(body, { recovered: true });
});

test('R-F515: UND_ERR_CONNECT_TIMEOUT is retryable', async (t) => {
  t.after(resetFetchAndState);
  let calls = 0;
  globalThis.fetch = async () => {
    calls++;
    if (calls === 1) throw makeTransportError('UND_ERR_CONNECT_TIMEOUT');
    return makeResponse(200, '{}');
  };
  const res = await robustFetch('https://example.test/z', {
    retries: 1,
    retryBackoffMs: 1,
  });
  assert.equal(res.status, 200);
  assert.equal(calls, 2);
});

test('R-F515: exhausted retries throw the last transport error', async (t) => {
  t.after(resetFetchAndState);
  let calls = 0;
  globalThis.fetch = async () => { calls++; throw makeTransportError('ECONNRESET'); };
  await assert.rejects(
    robustFetch('https://example.test/fail', { retries: 2, retryBackoffMs: 1 }),
    (err) => {
      // The error code may be on err.cause.code (undici style) or err.code.
      const code = (err.cause && err.cause.code) || err.code;
      return code === 'ECONNRESET';
    },
    'must throw the last transport error after exhausting retries',
  );
  assert.equal(calls, 3, '1 initial + 2 retries = 3 attempts');
});

test('R-F515: 429 fails fast — does not retry', async (t) => {
  t.after(resetFetchAndState);
  let calls = 0;
  globalThis.fetch = async () => { calls++; return makeResponse(429, 'Too Many Requests'); };
  const res = await robustFetch('https://example.test/rate', {
    retries: 3,
    retryBackoffMs: 1,
  });
  assert.equal(res.status, 429);
  assert.equal(calls, 1, 'must NOT retry on 429 — would compound rate-limit');
});

test('R-F515: 403 fails fast — does not retry', async (t) => {
  t.after(resetFetchAndState);
  let calls = 0;
  globalThis.fetch = async () => { calls++; return makeResponse(403); };
  const res = await robustFetch('https://example.test/forbidden', {
    retries: 3,
    retryBackoffMs: 1,
  });
  assert.equal(res.status, 403);
  assert.equal(calls, 1);
});

test('R-F515: 503 retries (transient server error)', async (t) => {
  t.after(resetFetchAndState);
  let calls = 0;
  globalThis.fetch = async () => {
    calls++;
    if (calls === 1) return makeResponse(503);
    return makeResponse(200, '{"ok":1}');
  };
  const res = await robustFetch('https://example.test/flaky', {
    retries: 1,
    retryBackoffMs: 1,
  });
  assert.equal(res.status, 200);
  assert.equal(calls, 2);
});

test('R-F515: non-retryable error (e.g. URL parse) propagates immediately', async (t) => {
  t.after(resetFetchAndState);
  let calls = 0;
  // A SyntaxError / generic Error with no retryable code should NOT be retried.
  globalThis.fetch = async () => {
    calls++;
    const err = new TypeError('bad URL');
    err.cause = { code: 'ERR_INVALID_URL' };
    throw err;
  };
  await assert.rejects(
    robustFetch('https://example.test/bad', { retries: 3, retryBackoffMs: 1 }),
  );
  assert.equal(calls, 1, 'must not retry non-retryable codes');
});

test('R-F515: per-host concurrency cap queues excess requests', async (t) => {
  t.after(resetFetchAndState);
  // Block all fetches until we explicitly release them.
  const gates = [];
  let inFlight = 0;
  let peakInFlight = 0;
  globalThis.fetch = async () => {
    inFlight++;
    if (inFlight > peakInFlight) peakInFlight = inFlight;
    await new Promise(resolve => gates.push(resolve));
    inFlight--;
    return makeResponse(200, '{}');
  };

  // Fire 6 concurrent requests to the same host with cap=2.
  const promises = Array.from({ length: 6 }, () =>
    robustFetch('https://example.test/concurrent', { maxConcurrentPerHost: 2 })
  );

  // Yield a few microtasks so queued waiters take their place in line.
  for (let i = 0; i < 10; i++) await Promise.resolve();

  // Only 2 should be actively fetching at this point — the other 4 queued.
  assert.equal(inFlight, 2, 'cap should limit concurrent to 2');
  const inspect1 = _testInspectHost('example.test');
  assert.equal(inspect1.active, 2);
  assert.equal(inspect1.waiters, 4, '4 requests must be queued');

  // Release one — exactly one waiter should be admitted.
  gates.shift()();
  for (let i = 0; i < 10; i++) await Promise.resolve();
  assert.equal(inFlight, 2, 'should still be at cap');
  const inspect2 = _testInspectHost('example.test');
  assert.equal(inspect2.waiters, 3);

  // Drain the rest.
  while (gates.length > 0) {
    gates.shift()();
    for (let i = 0; i < 5; i++) await Promise.resolve();
  }
  await Promise.all(promises);

  assert.equal(peakInFlight, 2, 'peak concurrent must never exceed cap');
});

test('R-F515: different hosts have independent concurrency budgets', async (t) => {
  t.after(resetFetchAndState);
  const gates = [];
  let inFlight = 0;
  globalThis.fetch = async () => {
    inFlight++;
    await new Promise(r => gates.push(r));
    inFlight--;
    return makeResponse(200, '{}');
  };

  // 2 to host-a + 2 to host-b, cap=2 each. All 4 should start concurrently.
  const promises = [
    robustFetch('https://host-a.test/x', { maxConcurrentPerHost: 2 }),
    robustFetch('https://host-a.test/y', { maxConcurrentPerHost: 2 }),
    robustFetch('https://host-b.test/x', { maxConcurrentPerHost: 2 }),
    robustFetch('https://host-b.test/y', { maxConcurrentPerHost: 2 }),
  ];
  for (let i = 0; i < 10; i++) await Promise.resolve();

  assert.equal(inFlight, 4, 'all 4 should run in parallel — different hosts');

  while (gates.length > 0) gates.shift()();
  await Promise.all(promises);
});

test('R-F515: external abort signal propagates (no retry)', async (t) => {
  t.after(resetFetchAndState);
  let calls = 0;
  globalThis.fetch = async (_url, opts) => {
    calls++;
    // Simulate the user aborting in flight.
    await new Promise((resolve, reject) => {
      opts.signal?.addEventListener('abort', () => {
        const err = new Error('aborted');
        err.name = 'AbortError';
        reject(err);
      });
    });
  };
  const controller = new AbortController();
  setTimeout(() => controller.abort(), 5);
  await assert.rejects(
    robustFetch('https://example.test/abort', {
      signal: controller.signal,
      retries: 3,
      retryBackoffMs: 1,
    }),
  );
  assert.equal(calls, 1, 'external abort is terminal — must not retry');
});

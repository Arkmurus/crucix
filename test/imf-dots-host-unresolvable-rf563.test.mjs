// R-F563 — IMF DOTS DNS-aware short-circuit
//
// Pre-R-F563 every sweep produced:
//   [Comtrade] 0 IMF DOTS trade flows · 0 anomalies · per-pair:
//   [China → Russia=fetch_ENOTFOUND, Russia → China=fetch_ENOTFOUND,
//    China → Iran=fetch_ENOTFOUND, US → China=fetch_ENOTFOUND]
// dataservices.imf.org no longer resolves from the production node. The
// 4 pairs each spent up to 41s (20s × 2 retries + 1s backoff) failing
// with the same DNS error, then the function returned an empty result.
// Operator's sweep budget was wasted four times over.
//
// R-F563 adds:
//   (a) intra-sweep: the first ENOTFOUND/EAI_AGAIN flips a flag and the
//       remaining pairs short-circuit with `skipped_host_unresolvable`
//   (b) cross-sweep: 3 consecutive total-fail sweeps trip the throttle
//       circuit so subsequent sweeps return in <1ms for 5 minutes

import test from 'node:test';
import assert from 'node:assert/strict';
import { fetchTradeFLows } from '../apis/sources/intel-feeds.mjs';
import { recordFailure, recordSuccess } from '../lib/util/throttle.mjs';

const _IMF_DOTS_CIRCUIT = 'imf_dots:dataservices_unresolvable';
const _origFetch = globalThis.fetch;

function mockFetchAlwaysENOTFOUND() {
  globalThis.fetch = async () => {
    const cause = new Error('getaddrinfo ENOTFOUND dataservices.imf.org');
    cause.code = 'ENOTFOUND';
    cause.errno = -3008;
    const err = new TypeError('fetch failed');
    err.cause = cause;
    throw err;
  };
}

function restoreFetch() {
  globalThis.fetch = _origFetch;
}

test('R-F563: first ENOTFOUND short-circuits remaining pairs in same sweep', async () => {
  // Reset throttle state so the circuit is closed at test start.
  recordSuccess(_IMF_DOTS_CIRCUIT);
  mockFetchAlwaysENOTFOUND();
  // Capture the per-pair line — we want to see at least one pair logged
  // as `skipped_host_unresolvable` rather than every pair re-doing the
  // doomed DNS lookup.
  const logs = [];
  const origLog = console.log;
  console.log = (...args) => logs.push(args.join(' '));
  try {
    const result = await fetchTradeFLows();
    assert.equal(result.flows.length, 0, 'no flows when host is unresolvable');
    const perPairLine = logs.find(l => l.includes('per-pair'));
    assert.ok(perPairLine, 'per-pair status line must still appear');
    assert.match(
      perPairLine,
      /skipped_host_unresolvable/,
      'at least one pair must be marked skipped (proves the intra-sweep short-circuit fired)',
    );
  } finally {
    console.log = origLog;
    restoreFetch();
    recordSuccess(_IMF_DOTS_CIRCUIT);
  }
});

test('R-F563: 3 consecutive total-fail sweeps trip the cross-sweep circuit', async () => {
  // Reset, then simulate 3 failure records (one per sweep where every
  // pair failed with ENOTFOUND). The 4th invocation should short-circuit
  // BEFORE doing any fetches.
  recordSuccess(_IMF_DOTS_CIRCUIT);
  recordFailure(_IMF_DOTS_CIRCUIT);
  recordFailure(_IMF_DOTS_CIRCUIT);
  recordFailure(_IMF_DOTS_CIRCUIT);

  let fetchCallCount = 0;
  globalThis.fetch = async () => {
    fetchCallCount += 1;
    throw new TypeError('fetch failed');
  };

  try {
    const result = await fetchTradeFLows();
    assert.equal(fetchCallCount, 0, 'R-F563: open circuit must short-circuit before any fetch');
    assert.ok(result.error && result.error.includes('R-F563'), 'result.error names R-F563 so diagnostics are unambiguous');
  } finally {
    restoreFetch();
    recordSuccess(_IMF_DOTS_CIRCUIT);
  }
});

test('R-F563: a successful flow resets the failure counter', async () => {
  // Stage: trip the breaker close to threshold, then mock a successful
  // fetch and confirm subsequent sweeps re-open the call path.
  recordSuccess(_IMF_DOTS_CIRCUIT);
  recordFailure(_IMF_DOTS_CIRCUIT);
  recordFailure(_IMF_DOTS_CIRCUIT);

  // Construct a stub SDMX response that looks like ≥1 observation.
  const okJson = {
    CompactData: {
      DataSet: {
        Series: {
          Obs: [
            { '@TIME_PERIOD': '2023', '@OBS_VALUE': '1000' },
            { '@TIME_PERIOD': '2024', '@OBS_VALUE': '1200' },
          ],
        },
      },
    },
  };
  globalThis.fetch = async () => ({
    ok: true,
    status: 200,
    json: async () => okJson,
  });

  try {
    const result = await fetchTradeFLows();
    assert.ok(result.flows.length > 0, 'flows must populate from the stub series');
    // Now mock all-fail again and confirm we still get the unconditional
    // per-pair status, NOT a circuit-open short-circuit (proving the
    // success above cleared the counter).
    let fetchCallCount = 0;
    globalThis.fetch = async () => {
      fetchCallCount += 1;
      throw new TypeError('fetch failed');
    };
    const result2 = await fetchTradeFLows();
    assert.ok(fetchCallCount > 0, 'R-F563: counter must reset on success so the next sweep actually fetches');
    assert.equal(result2.flows.length, 0);
  } finally {
    restoreFetch();
    recordSuccess(_IMF_DOTS_CIRCUIT);
  }
});

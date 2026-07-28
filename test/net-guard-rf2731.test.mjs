// R-F2731 — the test network guard must block LIVE network so no test can silently hit prod
// (Prospector #3: a briefing test was POSTing real signals to the production brain).

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { allowRealNetwork, allowLoopbackNetwork, blockRealNetwork } from './helpers/net_guard.mjs';
// importing net_guard replaces global.fetch with the block guard (module top-level side effect)

describe('R-F2731 test network guard', () => {
  it('blocks a live fetch by default, loudly and with the URL named', async () => {
    await assert.rejects(
      () => globalThis.fetch('https://aria-intel.fly.dev/health'),
      /net_guard.*LIVE network blocked.*aria-intel\.fly\.dev/,
    );
  });

  it('allowRealNetwork() restores the real fetch; blockRealNetwork() re-blocks', async () => {
    allowRealNetwork();
    assert.equal(globalThis.fetch, globalThis.__realFetch, 'restores the captured real fetch');
    blockRealNetwork();
    await assert.rejects(() => globalThis.fetch('http://localhost:8000/x'), /net_guard/);
    allowRealNetwork(); // leave the process in a clean state
  });

  it('names the URL from a Request-like object too', async () => {
    blockRealNetwork();
    await assert.rejects(() => globalThis.fetch({ url: 'https://example.com/z' }), /example\.com/);
    allowRealNetwork();
  });
});

// ── R-F3348: the loopback hatch must STAY loopback-only ─────────────────────
//
// R-F2739 added allowLoopbackNetwork() for capability tests that boot an
// isolated server, with the stated property that it "can never reach production
// or the LAN". Nothing asserted that. Measured: widening the hatch to call the
// real fetch unconditionally still passed this whole file, so the one safety
// property the hatch has could be deleted silently — while other tests
// (stripe-lifecycle-rf3279 from R-F3348 onward) rely on it.
describe('R-F3348 allowLoopbackNetwork is loopback-ONLY', () => {
  it('still blocks a non-loopback host', async () => {
    allowLoopbackNetwork();
    await assert.rejects(
      () => globalThis.fetch('https://example.com/should-be-blocked'),
      /net_guard/,
      'the loopback hatch must not become a general internet hatch',
    );
    blockRealNetwork();
  });

  it('lets a loopback URL through to the real fetch', async () => {
    allowLoopbackNetwork();
    // Port 1 on loopback has nothing listening, so the REAL fetch fails with a
    // connection error. What matters is WHICH error: anything other than
    // net_guard proves the request was routed to the real fetch rather than
    // blocked. Asserting on a live server would need one booted here.
    await assert.rejects(
      () => globalThis.fetch('http://127.0.0.1:1/nothing-listening'),
      (err) => !/net_guard/.test(String(err && err.message)),
      'a loopback URL must reach the real fetch, not the guard',
    );
    blockRealNetwork();
  });
});

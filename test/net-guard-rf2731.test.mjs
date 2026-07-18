// R-F2731 — the test network guard must block LIVE network so no test can silently hit prod
// (Prospector #3: a briefing test was POSTing real signals to the production brain).

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { allowRealNetwork, blockRealNetwork } from './helpers/net_guard.mjs';
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

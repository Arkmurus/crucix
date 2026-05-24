// R-F850 — brain-bridge verdict periodic re-check + `quiet` flag.
//
// Context: verifyBrainBridge() was only ever run as a one-shot at boot. If the
// brain was mid-cold-start when it fired, the 8s probe timed out and the cached
// verdict stuck at healthy:false forever, so /api/status (R-F844) reported
// "degraded" indefinitely even after the brain recovered. The fix adds a 60s
// periodic re-check in server.mjs that calls runAndCacheBridgeVerdict({quiet:true}).
// The `quiet` flag must (a) suppress all console output so a healthy brain
// doesn't log every minute, and (b) NEVER alter the returned verdict.
//
// BRAIN_URL / BRAIN_TOKEN are import-time consts derived from env, so env is set
// BEFORE the dynamic import below.

import { describe, it, before, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';

process.env.ARIA_API_TOKEN = 'test-bridge-token';
process.env.BRAIN_DIRECT_URL = 'http://brain.test';

let verifyBrainBridge;
before(async () => {
  ({ verifyBrainBridge } = await import('../lib/self/learning_store.mjs'));
});

// --- console + fetch capture harness ---------------------------------------
let _origFetch, _origLog, _origWarn, _origError, _lines;
beforeEach(() => {
  _origFetch = global.fetch;
  _origLog = console.log; _origWarn = console.warn; _origError = console.error;
  _lines = [];
  const cap = (...a) => { _lines.push(a.join(' ')); };
  console.log = cap; console.warn = cap; console.error = cap;
});
afterEach(() => {
  global.fetch = _origFetch;
  console.log = _origLog; console.warn = _origWarn; console.error = _origError;
});
const bridgeLines = () => _lines.filter(l => l.includes('[brainBridge]'));

describe('verifyBrainBridge quiet flag (R-F850)', () => {
  it('returns healthy verdict on 200 regardless of quiet', async () => {
    global.fetch = async () => ({ status: 200, ok: true });
    const v = await verifyBrainBridge({ quiet: true });
    assert.equal(v.healthy, true);
    assert.equal(v.reason, 'ok');
    assert.equal(v.status, 200);
    assert.equal(typeof v.timestamp, 'string');
  });

  it('quiet:true suppresses the healthy console line', async () => {
    global.fetch = async () => ({ status: 200, ok: true });
    await verifyBrainBridge({ quiet: true });
    assert.equal(bridgeLines().length, 0, `expected no [brainBridge] logs, got: ${bridgeLines().join(' | ')}`);
  });

  it('verbose (default) DOES emit the healthy console line', async () => {
    global.fetch = async () => ({ status: 200, ok: true });
    await verifyBrainBridge();
    assert.ok(bridgeLines().some(l => l.includes('healthy')), 'expected a [brainBridge] healthy log');
  });

  it('timeout yields healthy:false reason:timeout, and quiet stays silent', async () => {
    global.fetch = async () => { const e = new Error('timed out'); e.name = 'TimeoutError'; throw e; };
    const v = await verifyBrainBridge({ quiet: true });
    assert.equal(v.healthy, false);
    assert.equal(v.reason, 'timeout');
    assert.equal(bridgeLines().length, 0, 'quiet must suppress the timeout warning too');
  });

  it('a non-200 (e.g. 503) is reported unhealthy with the HTTP reason', async () => {
    global.fetch = async () => ({ status: 503, ok: false });
    const v = await verifyBrainBridge({ quiet: true });
    assert.equal(v.healthy, false);
    assert.equal(v.reason, 'HTTP 503');
  });
});

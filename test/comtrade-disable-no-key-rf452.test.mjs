// R-F452 — Comtrade source short-circuits when no API key is set.
//
// Pre-R-F452 the briefing() function attempted ~20 v1 /preview calls
// per sweep even when COMTRADE_API_KEY was unset, all of which now
// fail with ENOTFOUND (v1 deprecated late 2025) or return empty.
// Per-sweep cost: ~200s of timeout budget for zero data.
//
// R-F452: when the env var is unset, briefing() returns immediately
// with status="disabled_no_key" and empty tradeFlows/signals.

import test from 'node:test';
import assert from 'node:assert/strict';
import { briefing } from '../apis/sources/comtrade.mjs';

test('R-F452: briefing returns disabled_no_key when COMTRADE_API_KEY unset', async () => {
  const original = process.env.COMTRADE_API_KEY;
  delete process.env.COMTRADE_API_KEY;
  try {
    const result = await briefing();
    assert.equal(result.status, 'disabled_no_key');
    assert.equal(result.source, 'UN Comtrade');
    assert.ok(
      Array.isArray(result.tradeFlows) && result.tradeFlows.length === 0,
      'R-F452: tradeFlows must be empty',
    );
    assert.ok(
      Array.isArray(result.signals) && result.signals.length === 0,
      'R-F452: signals must be empty',
    );
    assert.ok(
      typeof result.reason === 'string' && result.reason.length > 0,
      'R-F452: reason string must explain why the source is disabled',
    );
    assert.ok(
      result.reason.includes('COMTRADE_API_KEY'),
      'R-F452: reason must name the env var so operator knows what to set',
    );
  } finally {
    if (original !== undefined) process.env.COMTRADE_API_KEY = original;
  }
});

test('R-F452: briefing does NOT short-circuit when COMTRADE_API_KEY is set', async () => {
  // We can't actually call the v2 API without a real key, but we can
  // confirm the short-circuit branch is NOT taken — the function must
  // proceed past the env-var check. Set the key and confirm the
  // returned shape is NOT the disabled_no_key sentinel. We expect the
  // actual API call to fail (no real key, network blocked) but the
  // path should differ.
  const original = process.env.COMTRADE_API_KEY;
  process.env.COMTRADE_API_KEY = 'fake-test-key-for-r-f452';
  try {
    const result = await briefing();
    // Should NOT short-circuit — status should be different from
    // disabled_no_key (it'll be undefined or 'ok' with empty flows
    // because the fake key won't actually fetch data).
    assert.notEqual(
      result.status, 'disabled_no_key',
      'R-F452 REGRESSION: short-circuit fired even with key set'
    );
  } finally {
    if (original === undefined) delete process.env.COMTRADE_API_KEY;
    else process.env.COMTRADE_API_KEY = original;
  }
});

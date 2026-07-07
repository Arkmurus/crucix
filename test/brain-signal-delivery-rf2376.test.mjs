// R-F2376 — production log fix: Crucix sweep signals must reach ARIA brain.
//
// Live evidence 2026-07-06: aria-web logged
// "signals skipped (redisPush is no-op since Upstash retirement)" while the
// wiring monitor reported M4 brain signal path issues. This test drives the
// real pushSignalsToBrain export and verifies it POSTs selected signals to the
// existing /api/aria/brain/signal sink.

import test from 'node:test';
import assert from 'node:assert/strict';

import { buildSourceHealthSummary, pushSignalsToBrain } from '../apis/briefing.mjs';

test('R-F2376: pushSignalsToBrain posts selected sweep signals to brain/signal', async () => {
  const oldUrl = process.env.ARIA_SERVICE_URL;
  const oldBrainUrl = process.env.ARIA_BRAIN_URL;
  const oldToken = process.env.ARIA_INTERNAL_TOKEN;
  const oldFetch = globalThis.fetch;
  const calls = [];

  process.env.ARIA_SERVICE_URL = 'https://aria-intel.example';
  delete process.env.ARIA_BRAIN_URL;
  process.env.ARIA_INTERNAL_TOKEN = 'test-token';
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options, body: JSON.parse(options.body) });
    return { ok: true, status: 200 };
  };

  try {
    const result = await pushSignalsToBrain({
      sources: {
        ProcurementTenders: {
          updates: [{
            title: 'Defence tender opened',
            description: 'Armoured vehicle support package',
            url: 'https://example.test/tender',
            market: 'Angola',
            priority: 'high',
            tags: ['defence'],
          }],
        },
        FRED: {
          updates: [{ title: 'Ignored macro signal' }],
        },
      },
    });

    assert.deepEqual(result, { delivered: 1, failed: 0 });
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, 'https://aria-intel.example/api/aria/brain/signal');
    assert.equal(calls[0].options.method, 'POST');
    assert.equal(calls[0].options.headers.Authorization, 'Bearer test-token');
    assert.equal(calls[0].body.signal_type, 'crucix_briefing_signal');
    assert.equal(calls[0].body.source, 'briefing:ProcurementTenders');
    assert.match(calls[0].body.content, /Defence tender opened/);
    assert.equal(calls[0].body.metadata.market, 'Angola');
  } finally {
    if (oldUrl === undefined) delete process.env.ARIA_SERVICE_URL;
    else process.env.ARIA_SERVICE_URL = oldUrl;
    if (oldBrainUrl === undefined) delete process.env.ARIA_BRAIN_URL;
    else process.env.ARIA_BRAIN_URL = oldBrainUrl;
    if (oldToken === undefined) delete process.env.ARIA_INTERNAL_TOKEN;
    else process.env.ARIA_INTERNAL_TOKEN = oldToken;
    globalThis.fetch = oldFetch;
  }
});

test('R-F2376: missing brain URL is honest and bounded', async () => {
  const oldUrl = process.env.ARIA_SERVICE_URL;
  const oldBrainUrl = process.env.ARIA_BRAIN_URL;
  const oldFetch = globalThis.fetch;

  delete process.env.ARIA_SERVICE_URL;
  delete process.env.ARIA_BRAIN_URL;
  globalThis.fetch = async () => {
    throw new Error('fetch must not run without a brain URL');
  };

  try {
    const result = await pushSignalsToBrain({
      sources: {
        DefenseEvents: {
          signals: [{ text: 'Exercise announced', source: 'DefenseEvents' }],
        },
      },
    });
    assert.deepEqual(result, { delivered: 0, failed: 1, reason: 'missing_brain_url' });
  } finally {
    if (oldUrl === undefined) delete process.env.ARIA_SERVICE_URL;
    else process.env.ARIA_SERVICE_URL = oldUrl;
    if (oldBrainUrl === undefined) delete process.env.ARIA_BRAIN_URL;
    else process.env.ARIA_BRAIN_URL = oldBrainUrl;
    globalThis.fetch = oldFetch;
  }
});

test('R-F2396: source health summary preserves partial sub-source failures for brain workers', () => {
  const health = buildSourceHealthSummary([
    { name: 'ReliefWeb', status: 'ok', durationMs: 120 },
    {
      name: 'ProcurementTenders',
      status: 'partial',
      durationMs: 25000,
      subStatus: { ok: 4, total: 6, failed: ['EU TED', 'UN Procurement'] },
    },
    { name: 'ExportControlIntel', status: 'error', durationMs: 30000, error: 'all 3 sub-sources failed' },
    { name: 'LegacyFeed', status: 'suspended', error: 'auto-suspended' },
  ]);

  assert.equal(health.total, 4);
  assert.equal(health.ok, 1);
  assert.equal(health.partial, 1);
  assert.equal(health.failed, 1);
  assert.equal(health.suspended, 1);
  assert.equal(health.available, 2);
  assert.equal(health.unavailable, 2);
  assert.equal(health.severity, 'critical');
  assert.deepEqual(health.degraded[0].failedSubsources, ['EU TED', 'UN Procurement']);
});

test('R-F2396: degraded source health is posted to brain even without user-facing signals', async () => {
  const oldUrl = process.env.ARIA_SERVICE_URL;
  const oldBrainUrl = process.env.ARIA_BRAIN_URL;
  const oldFetch = globalThis.fetch;
  const calls = [];

  process.env.ARIA_SERVICE_URL = 'https://aria-intel.example';
  delete process.env.ARIA_BRAIN_URL;
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options, body: JSON.parse(options.body) });
    return { ok: true, status: 200 };
  };

  try {
    const sourceHealth = buildSourceHealthSummary([
      { name: 'GDELT', status: 'ok', durationMs: 300 },
      {
        name: 'Lusophone',
        status: 'partial',
        durationMs: 30000,
        subStatus: { ok: 2, total: 5, failed: ['UN News Africa PT', 'RFI Portuguese Africa'] },
      },
    ]);
    const result = await pushSignalsToBrain({ sources: {}, sourceHealth });

    assert.deepEqual(result, { delivered: 1, failed: 0, healthQueued: true });
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, 'https://aria-intel.example/api/aria/brain/signal');
    assert.equal(calls[0].body.signal_type, 'crucix_source_health');
    assert.equal(calls[0].body.source, 'briefing:source_health');
    assert.match(calls[0].body.content, /Intel source health is degraded/);
    assert.match(calls[0].body.content, /Lusophone=partial/);
    assert.equal(calls[0].body.metadata.source_health.partial, 1);
  } finally {
    if (oldUrl === undefined) delete process.env.ARIA_SERVICE_URL;
    else process.env.ARIA_SERVICE_URL = oldUrl;
    if (oldBrainUrl === undefined) delete process.env.ARIA_BRAIN_URL;
    else process.env.ARIA_BRAIN_URL = oldBrainUrl;
    globalThis.fetch = oldFetch;
  }
});

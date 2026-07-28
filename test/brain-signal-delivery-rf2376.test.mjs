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
    // R-F3345 — the endpoint and the payload shape both moved. R-F2505 replaced
    // N CONCURRENT posts to /brain/signal with ONE bulk post to
    // /brain/signal/bulk: aria-intel has a single SQLite writer, and the
    // concurrent burst was serialised so badly that only 5 of 31 signals were
    // delivered under load. The bulk endpoint drains them sequentially in one
    // task with writer breathing room. Verified live before touching this test —
    // both endpoints answer 401 (auth), not 404, so the bulk route genuinely
    // exists on aria-intel.
    assert.equal(calls[0].url, 'https://aria-intel.example/api/aria/brain/signal/bulk');
    assert.equal(calls[0].options.method, 'POST');
    assert.equal(calls[0].options.headers.Authorization, 'Bearer test-token');
    assert.ok(Array.isArray(calls[0].body.signals), 'bulk payload carries a signals array');
    assert.equal(calls[0].body.signals.length, 1);
    const sig = calls[0].body.signals[0];
    assert.equal(sig.signal_type, 'crucix_briefing_signal');
    assert.equal(sig.source, 'briefing:ProcurementTenders');
    assert.match(sig.content, /Defence tender opened/);
    assert.equal(sig.metadata.market, 'Angola');
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
    assert.equal(calls[0].url, 'https://aria-intel.example/api/aria/brain/signal/bulk');
    const health = calls[0].body.signals[0];   // R-F3345: bulk payload
    assert.equal(health.signal_type, 'crucix_source_health');
    assert.equal(health.source, 'briefing:source_health');
    assert.match(health.content, /Intel source health is degraded/);
    assert.match(health.content, /Lusophone=partial/);
    assert.equal(health.metadata.source_health.partial, 1);
  } finally {
    if (oldUrl === undefined) delete process.env.ARIA_SERVICE_URL;
    else process.env.ARIA_SERVICE_URL = oldUrl;
    if (oldBrainUrl === undefined) delete process.env.ARIA_BRAIN_URL;
    else process.env.ARIA_BRAIN_URL = oldBrainUrl;
    globalThis.fetch = oldFetch;
  }
});

// ── R-F3345: the fallback R-F2505 documents but nothing tested ───────────────
//
// R-F2505's own comment says the bulk path "falls back to the per-signal
// concurrency path if the bulk endpoint 404s (older aria-intel not yet
// deployed)". That branch is the one that matters most: if it is broken, a
// web tier talking to an aria-intel without /brain/signal/bulk drops EVERY
// sweep signal, and the only symptom is a brain that stops learning — the
// §21a dark-path failure, with no error anyone sees.
//
// The whole point of the fallback is that it fires on a 404 specifically, so
// the stub answers 404 to /bulk and 200 to the per-signal sink.
test('R-F3345: a 404 on the bulk endpoint falls back to per-signal posts', async () => {
  const oldUrl = process.env.ARIA_SERVICE_URL;
  const oldBrainUrl = process.env.ARIA_BRAIN_URL;
  const oldToken = process.env.ARIA_INTERNAL_TOKEN;
  const oldFetch = globalThis.fetch;
  const calls = [];

  process.env.ARIA_SERVICE_URL = 'https://aria-intel.example';
  delete process.env.ARIA_BRAIN_URL;
  process.env.ARIA_INTERNAL_TOKEN = 'test-token';
  globalThis.fetch = async (url, options) => {
    calls.push({ url: String(url), options, body: JSON.parse(options.body) });
    if (String(url).endsWith('/brain/signal/bulk')) return { ok: false, status: 404 };
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
      },
    });

    const bulk = calls.filter((c) => c.url.endsWith('/brain/signal/bulk'));
    const single = calls.filter((c) => c.url.endsWith('/brain/signal'));

    assert.equal(bulk.length, 1, 'bulk is tried first');
    assert.ok(single.length >= 1,
      'a 404 on bulk must fall back to the per-signal sink, not drop the signals');
    assert.equal(single[0].body.signal_type, 'crucix_briefing_signal',
      'the fallback posts the per-signal shape, not the bulk envelope');
    assert.equal(result.delivered, 1,
      'the caller is told the signal was delivered by the fallback, not that it failed');
    assert.equal(result.failed, 0);
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

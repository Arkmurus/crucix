// R-F2610 — activation-disabled sources are not runtime failures.

import test from 'node:test';
import assert from 'node:assert/strict';

import { buildSourceHealthSummary, runSource } from '../apis/briefing.mjs';

test('runSource maps disabled_no_key source to not_configured, not error', async () => {
  const out = await runSource('CSL', async () => ({
    source: 'trade.gov Consolidated Screening List',
    status: 'disabled_no_key',
    reason: 'TRADE_GOV_API_KEY or CSL_API_KEY not configured',
    updates: [],
    signals: [],
    recent: [],
    _subStatus: { ok: 0, total: 1, failed: ['TRADE_GOV_API_KEY'] },
  }));

  assert.equal(out.status, 'not_configured');
  assert.equal(out.subStatus.ok, 0);
  assert.equal(out.subStatus.failed[0], 'TRADE_GOV_API_KEY');
});

test('source health does not count not_configured as failed or degraded', () => {
  const health = buildSourceHealthSummary([
    { name: 'ProcurementTenders', status: 'ok', durationMs: 100 },
    {
      name: 'CSL',
      status: 'not_configured',
      durationMs: 5,
      data: { reason: 'CSL_WATCHLIST or ARIA_CSL_WATCHLIST not configured' },
      subStatus: { ok: 0, total: 1, failed: ['CSL_WATCHLIST'] },
    },
  ]);

  assert.equal(health.ok, 1);
  assert.equal(health.failed, 0);
  assert.equal(health.partial, 0);
  assert.equal(health.notConfigured, 1);
  assert.equal(health.severity, 'healthy');
  assert.equal(health.unavailable, 0);
});

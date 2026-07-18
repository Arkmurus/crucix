// R-F2719 — Codex source-health audit #6: GET /api/source-health counted unconfigured
// (reliability === null) sources as HEALTHY, so it reported "47 healthy of 50" when 2
// were unconfigured (Comtrade/CSL — never feeding anything). This drives the pure
// classifier the endpoint now uses and asserts the honest buckets.

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { classifySourceHealth } from '../lib/source/healthBuckets.mjs';

// A representative live shape: 45 healthy, 3 degraded, 2 unconfigured (the real breakdown
// Codex measured behind the false "47 healthy").
function sample() {
  const s = [];
  for (let i = 0; i < 45; i++) s.push({ name: `ok${i}`, ok: 10, fail: 0, disabled: 0, reliability: 100 });
  for (let i = 0; i < 3; i++)  s.push({ name: `deg${i}`, ok: 3, fail: 7, disabled: 0, reliability: 30 });
  // unconfigured: never runs — disabled samples, no ok/fail → reliability null
  s.push({ name: 'Comtrade', ok: 0, fail: 0, disabled: 255, reliability: null });
  s.push({ name: 'CSL',      ok: 0, fail: 0, disabled: 255, reliability: null });
  return s;
}

describe('R-F2719 honest source-health buckets', () => {
  it('does NOT count unconfigured (reliability null) as healthy', () => {
    const b = classifySourceHealth(sample(), 80);
    assert.equal(b.counts.healthy, 45, 'only reliability >= 80 is healthy (was 47 incl. the 2 unconfigured)');
    assert.equal(b.counts.degraded, 3);
    assert.equal(b.counts.unconfigured, 2, 'Comtrade + CSL are unconfigured, not healthy');
    assert.deepEqual(b.unconfiguredNames.sort(), ['CSL', 'Comtrade']);
    // the old bug: 45 + 2 unconfigured = 47 "healthy" — must NOT happen now
    assert.notEqual(b.counts.healthy, 47);
  });

  it('separates never-checked (configured, unswept) from unconfigured', () => {
    const b = classifySourceHealth([
      { name: 'fresh', ok: 0, fail: 0, disabled: 0, reliability: null }, // configured, not yet swept
      { name: 'nokey', ok: 0, fail: 0, disabled: 5, reliability: null }, // unconfigured
    ], 80);
    assert.equal(b.counts.notChecked, 1);
    assert.equal(b.counts.unconfigured, 1);
    assert.equal(b.counts.healthy, 0, 'neither is healthy');
    assert.deepEqual(b.notCheckedNames, ['fresh']);
    assert.deepEqual(b.unconfiguredNames, ['nokey']);
  });

  it('the threshold boundary is inclusive (>=80 healthy, 79 degraded)', () => {
    const b = classifySourceHealth([
      { name: 'edge80', reliability: 80, disabled: 0 },
      { name: 'edge79', reliability: 79, disabled: 0 },
    ], 80);
    assert.deepEqual(b.healthyNames, ['edge80']);
    assert.deepEqual(b.degradedNames, ['edge79']);
  });
});

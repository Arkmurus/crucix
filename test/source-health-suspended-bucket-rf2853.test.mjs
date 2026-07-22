// R-F2853 — every queried source must be accounted for in the public payload.
//
// A 2026-07-22 external review read the briefing meta as
// "sourcesOk=46, sourcesFailed=0, sourcesTotal=50" and could not account for 4
// sources. buildSourceHealthSummary has always counted a 'suspended' bucket
// (the pruner short-circuited the call) and counts it as UNAVAILABLE when
// computing severity — but the output object never exposed it. A source that
// returned nothing while the payload shows zero failures is a false clean.
//
// These tests pin the arithmetic identity: the exposed buckets must sum to
// sourcesQueried, for any mix of statuses.

import test from 'node:test';
import assert from 'node:assert/strict';

import { buildSourceHealthSummary } from '../apis/briefing.mjs';

test('R-F2853: buildSourceHealthSummary buckets account for every source', () => {
  const sources = [
    { name: 'a', status: 'ok' },
    { name: 'b', status: 'ok' },
    { name: 'c', status: 'partial' },
    { name: 'd', status: 'error' },
    { name: 'e', status: 'suspended' },
    { name: 'f', status: 'suspended' },
    { name: 'g', status: 'not_configured' },
  ];

  const h = buildSourceHealthSummary(sources);

  assert.equal(h.total, 7);
  assert.equal(h.ok, 2);
  assert.equal(h.partial, 1);
  assert.equal(h.failed, 1);
  assert.equal(h.suspended, 2, 'suspended sources must be counted');
  assert.equal(h.notConfigured, 1);

  const summed = h.ok + h.partial + h.failed + h.suspended + h.notConfigured;
  assert.equal(
    summed,
    h.total,
    `buckets ${summed} do not account for all ${h.total} sources`,
  );
});

test('R-F2853: omitting suspended would leave sources unaccounted for', () => {
  // Negative control — reproduces the reviewer's arithmetic on the pre-fix
  // payload shape, proving the gap was real rather than a misreading.
  const sources = [
    ...Array.from({ length: 46 }, (_, i) => ({ name: `ok${i}`, status: 'ok' })),
    ...Array.from({ length: 4 }, (_, i) => ({ name: `sus${i}`, status: 'suspended' })),
  ];

  const h = buildSourceHealthSummary(sources);

  // What the pre-fix payload exposed:
  const exposedPreFix = h.ok + h.partial + h.failed + h.notConfigured;
  assert.equal(h.total, 50);
  assert.equal(exposedPreFix, 46, 'pre-fix shape should under-account');
  assert.equal(
    h.total - exposedPreFix,
    4,
    'the 4 sources the reviewer could not account for are the suspended bucket',
  );

  // What the fixed payload exposes:
  assert.equal(exposedPreFix + h.suspended, h.total);
});

test('R-F2853: suspended sources count as unavailable, not healthy', () => {
  const h = buildSourceHealthSummary([
    { name: 'a', status: 'ok' },
    { name: 'b', status: 'suspended' },
  ]);

  assert.equal(h.unavailable, 1, 'suspended must not be treated as available');
  assert.equal(h.available, 1);
  assert.notEqual(
    h.severity,
    'healthy',
    'a suspended source must not report a healthy severity',
  );
});

test('R-F2853: an all-ok sweep still reports healthy and sums correctly', () => {
  // Over-strictness control: the guard must not make clean sweeps look broken.
  const h = buildSourceHealthSummary([
    { name: 'a', status: 'ok' },
    { name: 'b', status: 'ok' },
  ]);

  assert.equal(h.suspended, 0);
  assert.equal(h.severity, 'healthy');
  assert.equal(h.ok + h.partial + h.failed + h.suspended + h.notConfigured, h.total);
});

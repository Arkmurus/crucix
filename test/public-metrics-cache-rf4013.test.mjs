// test/public-metrics-cache-rf4013.test.mjs
//
// R-F4013 (C-90) — bound the public metrics endpoint's upstream calls.
//
// PROVENANCE, because it matters. The aria-web audit recorded a finding (F-08)
// that `/api/public/metrics` hung twice on a cold cache with no explanation from
// the 8s upstream bound or the 5s slow-down ramp. On re-measurement it did NOT
// reproduce: a cold-cache request completed in 2,478ms. Reviewing the original
// observation, TWO unrelated endpoints returned 000 in the same probe loop and
// both succeeded on individual retry — which points at the probing client, not
// the server. That finding is retracted rather than "fixed".
//
// Reading the handler to reach that conclusion surfaced a DIFFERENT and provable
// defect, which is what this closes: the route cached a success for ten minutes
// and never cached a failure, so while the brain is slow, restarting (~10 minute
// boot) or down, EVERY anonymous request made its own upstream call, waited the
// full 8-second timeout and wrote an errorTracker record. One visitor is one
// upstream call; a crawler is thousands.
//
// This is not a timeout bump. The 8-second bound is untouched. What changes is
// that a failure is remembered briefly instead of rediscovered by every caller.
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const {
  isCacheFresh, nextCacheEntry, shouldQueryUpstream,
  SUCCESS_TTL_MS, FAILURE_TTL_MS,
} = await import('../lib/metrics/publicMetricsCache.mjs');

const SERVER = fs.readFileSync(new URL('../server.mjs', import.meta.url), 'utf8');
const T0 = 1_700_000_000_000;

describe('R-F4013 — a failed measurement is remembered, briefly', () => {

  it('THE DEFECT: a failure is cached at all', () => {
    const entry = nextCacheEntry(null, T0);
    assert.equal(entry.value.records, null, 'a failure stays honestly null');
    assert.equal(isCacheFresh(entry, T0 + 1_000), true,
      'a failure one second old must be served from cache — otherwise every '
      + 'anonymous request re-runs the 8s upstream call during an outage');
    assert.equal(shouldQueryUpstream(entry, T0 + 1_000), false);
  });

  it('but it expires FAST, so a recovery is not hidden', () => {
    const entry = nextCacheEntry(null, T0);
    assert.equal(isCacheFresh(entry, T0 + FAILURE_TTL_MS + 1), false,
      'a remembered failure must expire quickly or a recovered brain keeps '
      + 'showing an empty figure');
    assert.equal(shouldQueryUpstream(entry, T0 + FAILURE_TTL_MS + 1), true);
  });

  it('the two TTLs are deliberately asymmetric', () => {
    // The design, pinned. Someone "tidying" these to one constant would either
    // hammer the brain during an outage or hide a recovery for ten minutes.
    assert.ok(FAILURE_TTL_MS < SUCCESS_TTL_MS / 4,
      'the failure TTL must be much shorter than the success TTL');
    assert.ok(FAILURE_TTL_MS >= 10_000,
      'and long enough to actually bound the call rate');
  });

  it('a real measurement still caches for the long TTL', () => {
    const entry = nextCacheEntry(531137, T0);
    assert.equal(entry.value.records, 531137);
    assert.equal(isCacheFresh(entry, T0 + SUCCESS_TTL_MS - 1), true);
    assert.equal(isCacheFresh(entry, T0 + SUCCESS_TTL_MS + 1), false);
  });

  it('a zero or nonsense count is treated as no measurement, never as zero records', () => {
    // "0 records" on the landing page would be a fabricated fact about the
    // corpus. Absence renders as an em dash; zero would render as a claim.
    for (const bad of [0, -5, NaN, undefined, null, 'lots']) {
      assert.equal(nextCacheEntry(bad, T0).value.records, null,
        `${String(bad)} must not become a published record count`);
    }
  });

  it('an empty cache always queries', () => {
    assert.equal(shouldQueryUpstream(null, T0), true);
    assert.equal(shouldQueryUpstream({ at: T0, value: null }, T0), true);
  });

  it('the route uses the shared decision and keeps its 8s upstream bound', () => {
    const at = SERVER.indexOf("app.get('/api/public/metrics'");
    assert.ok(at > 0, 'the metrics route should exist');
    const route = SERVER.slice(at, SERVER.indexOf('\n});', at));
    assert.match(route, /shouldQueryUpstream\(|isCacheFresh\(/,
      'the route must ask the shared cache decision');
    assert.match(route, /nextCacheEntry\(/,
      'and store BOTH outcomes through it');
    assert.match(route, /AbortSignal\.timeout\(8000\)/,
      'the 8-second upstream bound must remain — this change is not a timeout bump');
    assert.doesNotMatch(route, /if \(records !== null\) _publicMetricsCache/,
      'the success-only cache write was the defect');
  });
});

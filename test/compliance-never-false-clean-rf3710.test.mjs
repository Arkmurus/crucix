// test/compliance-never-false-clean-rf3710.test.mjs
//
// R-F3710 — CAPABILITY: the Node compliance tier cannot report PERMITTED over
// a list it never consulted.
//
// THE DEFECT (360 DD sweep, 2026-08-04). `screenEntity` looped the source
// registry and did:
//
//     const raw = await redis.get(`${PREFIX}${key}_entries`);
//     if (!raw) continue;                    // <- silently skipped
//     ...
//     result: isHit ? 'PROHIBITED' : 'PERMITTED'
//
// So a list that was NEVER LOADED — a failed first fetch, an evicted key, a
// parse error at boot — produced exactly the same answer as a list that was
// searched and had no match. A designated party screened PERMITTED because
// OFAC had not been read.
//
// The Python tier already refuses this shape everywhere (R-F2159 / R-F2373: an
// empty or unreadable store yields INSUFFICIENT_DATA, never CLEAR). This is the
// same rule on the tier that answers screenEntity.
//
// Run: node --test test/compliance-never-false-clean-rf3710.test.mjs

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { screenEntity } from '../lib/compliance/listRefresher.mjs';

const PREFIX = 'crucix:compliance:';

/** Minimal redis stand-in: only .get is used by screenEntity. */
function fakeRedis(map) {
  return { get: async (k) => (k in map ? map[k] : null) };
}

const DESIGNATED = JSON.stringify([
  { uid: '1', names: ['Rosoboronexport'], type: 'entity', programs: ['UKRAINE-EO14024'] },
]);
const UNRELATED = JSON.stringify([
  { uid: '2', names: ['Some Other Entity'], type: 'entity', programs: ['X'] },
]);
const ALL_LOADED = {
  [`${PREFIX}ofac_entries`]: UNRELATED,
  [`${PREFIX}ofsi_entries`]: UNRELATED,
  [`${PREFIX}unsc_entries`]: UNRELATED,
  [`${PREFIX}versions`]: JSON.stringify({ ofac: { date: '2026-08-01' } }),
};

describe('R-F3710 an unconsulted list is never a clearance', () => {
  it('reports INSUFFICIENT_DATA when NO list is loaded', async () => {
    const out = await screenEntity('Rosoboronexport', fakeRedis({}));
    assert.notEqual(out.result, 'PERMITTED',
      'nothing was consulted — PERMITTED would be a fabricated clearance');
    assert.equal(out.result, 'INSUFFICIENT_DATA');
    assert.equal(out.complete_coverage, false);
    assert.equal(out.sources_screened.length, 0);
  });

  it('reports INSUFFICIENT_DATA when only SOME lists are loaded', async () => {
    const out = await screenEntity('Rosoboronexport', fakeRedis({
      [`${PREFIX}ofac_entries`]: UNRELATED,
      [`${PREFIX}versions`]: '{}',
    }));
    assert.equal(out.result, 'INSUFFICIENT_DATA',
      'partial coverage is not a clearance — the missing list is exactly where '
      + 'the designation might have been');
    assert.ok(out.sources_unavailable.length > 0);
    assert.ok(/INCOMPLETE SCREEN/.test(out.note));
  });

  it('reports INSUFFICIENT_DATA when a stored list is unparseable', async () => {
    const out = await screenEntity('Rosoboronexport', fakeRedis({
      ...ALL_LOADED,
      [`${PREFIX}ofac_entries`]: '{not json',
    }));
    assert.equal(out.result, 'INSUFFICIENT_DATA');
    assert.ok(out.sources_unavailable.some(u => /unparseable/.test(u.reason)));
  });

  it('reports INSUFFICIENT_DATA when a stored list is EMPTY', async () => {
    const out = await screenEntity('Rosoboronexport', fakeRedis({
      ...ALL_LOADED,
      [`${PREFIX}ofac_entries`]: '[]',
    }));
    assert.equal(out.result, 'INSUFFICIENT_DATA',
      'an empty list is indistinguishable from a wiped one — it has not screened');
    assert.ok(out.sources_unavailable.some(u => u.reason === 'empty_list'));
  });
});

describe('R-F3710 the gate is not over-broad', () => {
  it('still reports PERMITTED when every list was searched and matched nothing', async () => {
    const out = await screenEntity('Greggs Bakery Limited', fakeRedis(ALL_LOADED));
    assert.equal(out.result, 'PERMITTED',
      'a complete screen with no match must still clear, or the product is useless');
    assert.equal(out.complete_coverage, true);
    assert.equal(out.sources_unavailable.length, 0);
    assert.equal(out.sources_screened.length, 3);
  });

  it('still reports PROHIBITED on a hit', async () => {
    const out = await screenEntity('Rosoboronexport', fakeRedis({
      ...ALL_LOADED,
      [`${PREFIX}ofac_entries`]: DESIGNATED,
    }));
    assert.equal(out.result, 'PROHIBITED');
    assert.ok(out.hits.ofac.length > 0);
  });

  it('a HIT outranks incomplete coverage', async () => {
    // Finding a designation on ONE list is conclusive; a missing second list
    // cannot un-find it.
    const out = await screenEntity('Rosoboronexport', fakeRedis({
      [`${PREFIX}ofac_entries`]: DESIGNATED,
      [`${PREFIX}versions`]: '{}',
    }));
    assert.equal(out.result, 'PROHIBITED',
      'a confirmed designation must not be downgraded to INSUFFICIENT_DATA');
  });
});

describe('R-F3710 coverage is part of the answer', () => {
  it('names which sources were screened and which were not', async () => {
    const out = await screenEntity('X', fakeRedis({
      [`${PREFIX}ofac_entries`]: UNRELATED,
      [`${PREFIX}versions`]: '{}',
    }));
    assert.ok(Array.isArray(out.sources_screened));
    assert.ok(Array.isArray(out.sources_unavailable));
    assert.ok(out.sources_screened.some(s => /OFAC/.test(s)),
      'a consumer must be able to tell WHICH lists were actually searched');
    for (const u of out.sources_unavailable) {
      assert.ok(u.source && u.reason, 'each unavailable source must carry a reason');
    }
  });
});

describe('R-F3710 anti-regression: the dark paths are wired', () => {
  it('parse and fetch failures reach the brain, not just the console', async () => {
    const fs = await import('node:fs');
    const path = await import('node:path');
    const root = path.resolve(
      path.dirname(new URL(import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, '$1'), '..',
    );
    const src = fs.readFileSync(
      path.join(root, 'lib', 'compliance', 'listRefresher.mjs'), 'utf8');

    assert.ok(src.includes('_wireComplianceFailure'),
      'a silently-empty OFAC parse must reach the brain — the console line was '
      + 'the only trace, and nothing could learn ARIA had stopped screening');
    for (const kind of ['parse_error', 'fetch_failed', 'coverage_drift']) {
      assert.ok(src.includes(kind), `${kind} must be wired`);
    }
  });

  it('a thin parse cannot overwrite a good list', async () => {
    const fs = await import('node:fs');
    const path = await import('node:path');
    const root = path.resolve(
      path.dirname(new URL(import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, '$1'), '..',
    );
    const src = fs.readFileSync(
      path.join(root, 'lib', 'compliance', 'listRefresher.mjs'), 'utf8');

    assert.ok(src.includes('coverage_drift'),
      'the 0-entry guard catches a TOTAL parse failure; a schema drift yielding '
      + '3 entries from ~19,000 passes it and would be committed over the good list');
    const driftIdx = src.indexOf('coverage_drift');
    const storeIdx = src.indexOf('_entries`, JSON.stringify(entries)');
    assert.ok(driftIdx > -1 && storeIdx > -1 && driftIdx < storeIdx,
      'the floor must be checked BEFORE the store write');
  });
});

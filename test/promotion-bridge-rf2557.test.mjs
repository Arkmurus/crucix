// R-F2557 — Node->brain promotion push: honest mappings, no BD-strategy leak.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { _test } from '../apis/promotion_bridge.mjs';

const { _mapOpportunity, _mapSanctions, _mapCSLHit } = _test;

test('opportunity: strong sourced non-blocked -> HIGH programme_signal, no OEM/score leak', () => {
  const f = _mapOpportunity({
    id: 'AO-1', market: 'Angola', iso2: 'AO', score: 70,
    complianceStatus: 'NOT_SCREENED',
    conflict: { events: 12 },
    procurementNeeds: ['armoured vehicles', 'radios'],
    matchedOEMs: ['SecretOEM-X', 'SecretOEM-Y'],       // must NOT leak to public
    sources: [{ title: 'TED', url: 'https://ted.europa.eu/n/1', isProcurement: true }],
    detectedAt: '2026-07-12T00:00:00Z',
  });
  assert.equal(f.signal_type, 'programme_signal');
  assert.equal(f.priority, 'HIGH');
  assert.equal(f.source_tier, 'tier_2');
  assert.match(f.evidence_url, /^https:\/\/ted\.europa\.eu/);
  // privacy: Arkmurus OEM matches must not appear anywhere public-facing
  const blob = JSON.stringify(f);
  assert.ok(!blob.includes('SecretOEM'), 'OEM matches leaked into the public finding');
  assert.deepEqual(f.entities.oems, []);
  assert.deepEqual(f.entities.products, []);
});

test('opportunity: export-control review required -> downgraded + flagged, never HIGH', () => {
  const f = _mapOpportunity({
    market: 'Somewhere', iso2: 'SW', score: 90,
    complianceStatus: 'REVIEW_REQUIRED',
    conflict: { events: 3 },
    sources: [{ url: 'https://x/1', isProcurement: true }],
  });
  assert.notEqual(f.priority, 'HIGH');
  assert.match(f.why_it_matters, /export-control review/i);
  assert.match(f.recommended_action, /Compliance review required/i);
});

test('opportunity without a real source url -> not HIGH (cannot be distribution-ready)', () => {
  const f = _mapOpportunity({ market: 'X', score: 80, complianceStatus: 'NOT_SCREENED', sources: [] });
  assert.notEqual(f.priority, 'HIGH');   // no evidence url
  assert.equal(f.evidence_url, '');
});

test('sanctions entry -> sanctions_change in capped MEDIUM/tier_2 shape', () => {
  const f = _mapSanctions({
    name: 'ACME Corp', datasets: ['OFAC', 'EU'], country: 'RU',
    lastChange: '2026-07-12T00:00:00Z', text: 'PRE-DESIGNATION: ACME added to 2 lists',
    citation_url: 'https://sanctionssearch.ofac.treas.gov/Details.aspx?id=1',
  });
  assert.equal(f.signal_type, 'sanctions_change');
  assert.equal(f.priority, 'MEDIUM');
  assert.equal(f.source_tier, 'tier_2');
  assert.equal(f.target, 'ACME Corp');
  assert.match(f.evidence_url, /^https:/);
  assert.match(f.recommended_action, /screen counterparties/i);
});

test('nameless sanctions entry is dropped', () => {
  assert.equal(_mapSanctions({ datasets: ['OFAC'] }), null);
  assert.equal(_mapSanctions(null), null);
});

test('CSL hit -> official HIGH sanctions_change with customer value metadata', () => {
  const f = _mapCSLHit({
    id: 'csl-1',
    term: 'ACME',
    name: 'ACME Defence LLC',
    lists: ['BIS Entity List'],
    sourceList: 'BIS Entity List',
    country: 'AE',
    url: 'https://www.bis.gov/entity-list',
  });
  assert.equal(f.signal_type, 'sanctions_change');
  assert.equal(f.priority, 'HIGH');
  assert.equal(f.confidence, 'HIGH');
  assert.equal(f.source_tier, 'tier_1a');
  assert.equal(f.target, 'ACME Defence LLC');
  assert.match(f.why_it_matters, /official US export\/sanctions screening source/);
  assert.match(f.recommended_action, /pause export or bid activity/i);
  assert.equal(f.customer_value.score, 90);
  assert.ok(f.customer_value.problems.includes('export_control_risk'));
});

test('nameless CSL hit is dropped', () => {
  assert.equal(_mapCSLHit({ sourceList: 'BIS Entity List' }), null);
  assert.equal(_mapCSLHit(null), null);
});

// R-F2557 review #1/#2 — the dedup contract: two sweeps of the SAME market opportunity
// mint different opp.id (iso2-Date.now()) but MUST produce the same promotion ref, or
// the Python dedup is defeated and the signal list floods every sweep.
test('opportunity ref is STABLE across sweeps (no volatile id/timestamp)', () => {
  const src = [{ url: 'https://ted.europa.eu/n/1', isProcurement: true }];
  const a = _mapOpportunity({ market: 'Angola', iso2: 'AO', id: 'AO-1700000000000', score: 70, complianceStatus: 'NOT_SCREENED', sources: src });
  const b = _mapOpportunity({ market: 'Angola', iso2: 'AO', id: 'AO-1800000000000', score: 70, complianceStatus: 'NOT_SCREENED', sources: src });
  assert.equal(a.ref, b.ref, 'ref must be identical across sweeps');
  assert.ok(!/\d{10,}/.test(a.ref), `ref must not embed a timestamp: ${a.ref}`);
});

test('opportunity ref is stable even without a source url', () => {
  const a = _mapOpportunity({ market: 'Chad', iso2: 'TD', id: 'TD-111', score: 50, complianceStatus: 'NOT_SCREENED', sources: [] });
  const b = _mapOpportunity({ market: 'Chad', iso2: 'TD', id: 'TD-999', score: 50, complianceStatus: 'NOT_SCREENED', sources: [] });
  assert.equal(a.ref, b.ref);
  assert.ok(!/\d{7,}/.test(a.ref), `ref must not embed a timestamp: ${a.ref}`);
});

test('opportunity no longer forwards the raw internal composite score', () => {
  const f = _mapOpportunity({ market: 'X', iso2: 'XX', id: 'X-1', score: 88, complianceStatus: 'NOT_SCREENED', sources: [{ url: 'https://x/1', isProcurement: true }] });
  assert.equal(f.score, undefined, 'internal Arkmurus score must not reach the public signal');
});

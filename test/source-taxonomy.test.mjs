// Tests for lib/intel/source_taxonomy.mjs
// Verifies the declarative source-class table matches expected behaviour
// for the per-(source × region) quality gate. Replaces the hardcoded
// keyword/source-list dampener from af9b50f.

import test from 'node:test';
import assert from 'node:assert/strict';
import {
  classifySource,
  sourceQualityMultiplierForRegion,
  dampenedSourceLabel,
  SOURCE_CLASSES,
  SOURCE_TAG_TO_CLASS,
  REGION_USE_CASE,
} from '../lib/intel/source_taxonomy.mjs';

test('classifySource maps known tags correctly', () => {
  assert.equal(classifySource('osint'), 'primary_geopolitical');
  assert.equal(classifySource('conflict'), 'primary_geopolitical');
  assert.equal(classifySource('defence_news'), 'commercial_defence');
  assert.equal(classifySource('arms_transfer'), 'commercial_defence');
  assert.equal(classifySource('procurement'), 'commercial_defence');
  assert.equal(classifySource('export_control'), 'commercial_defence');
  assert.equal(classifySource('analytical'), 'analytical');
  assert.equal(classifySource('financial'), 'analytical');
  assert.equal(classifySource('trade'), 'trade_finance');
  assert.equal(classifySource('explorer'), 'exploration');
});

test('classifySource defaults unknown tags to osint_aggregator', () => {
  assert.equal(classifySource('mystery_source'), 'osint_aggregator');
  assert.equal(classifySource(''), 'osint_aggregator');
  assert.equal(classifySource(null), 'osint_aggregator');
  assert.equal(classifySource(undefined), 'osint_aggregator');
});

test('Damen-feed → Middle East dampens to 0.5 (the original bug)', () => {
  // The exact case that motivated this refactor: a defence_news signal
  // (Damen feed) hitting the Middle East geopolitical correlation must
  // now be dampened, because commercial_defence is NOT in the
  // appropriate_uses set for geopolitical_correlation.
  const m = sourceQualityMultiplierForRegion('defence_news', 'Middle East');
  assert.equal(m, 0.5);
});

test('Damen-feed → Defence Procurement passes at full weight', () => {
  // The same defence_news signal hitting the Defence Procurement
  // correlation IS appropriate — commercial_defence supports
  // commercial_correlation. Full weight, no dampening.
  const m = sourceQualityMultiplierForRegion('defence_news', 'Defence Procurement');
  assert.equal(m, 1);
});

test('OSINT → any geopolitical region passes at full weight', () => {
  // Primary geopolitical sources (OSINT, conflict trackers) feed
  // geopolitical correlations at full weight — that's their lane.
  for (const region of ['Middle East', 'Eastern Europe', 'East Asia',
                         'East / Central Africa', 'Lusophone Africa']) {
    assert.equal(sourceQualityMultiplierForRegion('osint', region), 1,
      `osint → ${region} should be full weight`);
    assert.equal(sourceQualityMultiplierForRegion('conflict', region), 1,
      `conflict → ${region} should be full weight`);
  }
});

test('analytical sources support multiple correlation types', () => {
  // Think-tank / central-bank sources feed geopolitical, policy, and
  // commercial correlations all at full weight.
  for (const region of ['Middle East', 'Defence Procurement']) {
    assert.equal(sourceQualityMultiplierForRegion('analytical', region), 1);
    assert.equal(sourceQualityMultiplierForRegion('financial', region), 1);
  }
});

test('procurement → Middle East dampens (commercial_defence on geopolitical)', () => {
  // Same logic as Damen — procurement is a commercial_defence class
  // source, dampened on geopolitical.
  assert.equal(sourceQualityMultiplierForRegion('procurement', 'Middle East'), 0.5);
});

test('arms_transfer → Energy/Maritime dampens (commercial_defence on threat)', () => {
  // arms_transfer is commercial_defence, NOT appropriate for
  // threat_correlation (Energy/Maritime). Should dampen.
  assert.equal(
    sourceQualityMultiplierForRegion('arms_transfer', 'Energy / Maritime'),
    0.5,
  );
});

test('exploration class is appropriate for all four use-cases', () => {
  // Spider/explorer outputs are general-purpose and should never be
  // dampened by the source-quality gate (reliability gate handles
  // bad spider sources separately).
  for (const region of Object.keys(REGION_USE_CASE)) {
    assert.equal(
      sourceQualityMultiplierForRegion('explorer', region),
      1,
      `explorer → ${region} should be full weight`,
    );
  }
});

test('unknown region returns 1 (pass-through, no penalty)', () => {
  // If a future correlation region is added without updating
  // REGION_USE_CASE, we should NOT silently start dampening every
  // signal — that's worse than passing through.
  assert.equal(
    sourceQualityMultiplierForRegion('defence_news', 'New Made-Up Region'),
    1,
  );
});

test('dampenedSourceLabel includes class name and region use-case', () => {
  const label = dampenedSourceLabel('defence_news', 'Middle East');
  assert.match(label, /defence_news/);
  assert.match(label, /commercial_defence/);
  assert.match(label, /geopolitical_correlation/);
  assert.match(label, /commentary, not primary reporting/);
});

test('every SOURCE_TAG_TO_CLASS entry references a real class', () => {
  for (const [tag, cls] of Object.entries(SOURCE_TAG_TO_CLASS)) {
    assert.ok(SOURCE_CLASSES[cls],
      `Tag ${tag} maps to undefined class ${cls}`);
  }
});

test('every REGION_USE_CASE value is referenced by at least one class', () => {
  // Sanity check: a use-case that no source class supports would
  // mean the correlator dampens every signal in that region.
  const allUseCases = new Set();
  for (const def of Object.values(SOURCE_CLASSES)) {
    for (const u of def.appropriate_uses) allUseCases.add(u);
  }
  for (const [region, useCase] of Object.entries(REGION_USE_CASE)) {
    assert.ok(allUseCases.has(useCase),
      `Region ${region} maps to use-case ${useCase} which no source class supports — every signal would be dampened`);
  }
});

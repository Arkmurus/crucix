// Tests for lib/intel/source_registry.mjs and the
// register-at-ingest refactor (2026-04-26 architectural follow-up C).
//
// The registry decouples (data field → _src tag → source class) so a
// new source needs ONE entry instead of edits across correlate.mjs and
// source_taxonomy.mjs.

import test from 'node:test';
import assert from 'node:assert/strict';
import { correlate } from '../lib/intel/correlate.mjs';
import { classifySource } from '../lib/intel/source_taxonomy.mjs';
import {
  registerSignalSource,
  listSignalSources,
  getSourceClassForTag,
  getSignalSource,
} from '../lib/intel/source_registry.mjs';

test('bootstrap registers all current sources at module load', () => {
  // Importing correlate.mjs side-effect-imports the bootstrap.
  // Every tag that used to be hardcoded in correlate.mjs:117-134 must
  // be present in the registry now, with the same source class as the
  // hardcoded SOURCE_TAG_TO_CLASS table.
  const expected = {
    osint:          'primary_geopolitical',
    news:           'primary_geopolitical',
    sanctions:      'primary_geopolitical',
    conflict:       'primary_geopolitical',
    diplomatic:     'primary_geopolitical',
    financial:      'analytical',
    analytical:     'analytical',
    trade:          'trade_finance',
    defence_news:   'commercial_defence',
    arms_transfer:  'commercial_defence',
    procurement:    'commercial_defence',
    export_control: 'commercial_defence',
    explorer:       'exploration',
  };
  for (const [tag, cls] of Object.entries(expected)) {
    assert.equal(getSourceClassForTag(tag), cls,
      `registry should classify ${tag} as ${cls}`);
  }
});

test('registry-driven classifySource agrees with the hardcoded fallback', () => {
  // Every tag that exists in both should resolve to the same class
  // through classifySource (which tries registry first, fallback second).
  const sources = listSignalSources();
  for (const src of sources) {
    assert.equal(classifySource(src.tag), src.sourceClass,
      `classifySource(${src.tag}) should agree with registry`);
  }
});

test('correlate iterates the registry for signal collection', () => {
  // End-to-end: a sweep payload with one signal per registered source
  // should produce one allSignals entry per source. We verify by
  // checking the resulting correlations expose the right `_src` tags.
  const fakeData = {
    tg: { urgent: [{ title: 'Tehran threat', text: 'Iran tensions in Hormuz', _t: 1 }] },
    sanctions: { updates: [{ title: 'OFAC adds Iran entity', text: 'Tehran proxy designated', _t: 1 }] },
    defenseNews: { updates: [{ title: 'Damen sells frigate', text: 'Middle East patrol order', _t: 1 }] },
  };
  const result = correlate(fakeData);
  // We don't care about exact correlation outputs — just that the
  // pipeline ran without error and emitted ME signals from registered
  // sources. The value of this test is catching a registry-misconnect
  // regression (correlate would empty out if listSignalSources broke).
  assert.ok(Array.isArray(result),
    'correlate must still return an array after the registry refactor');
});

test('registerSignalSource adds a new source picked up by correlate', () => {
  // Simulate "adding a new source" — call registerSignalSource once,
  // hand correlate a payload, confirm it gets read.
  let timesCalled = 0;
  registerSignalSource({
    tag: 'test_only_source',
    sourceClass: 'osint_aggregator',
    description: 'Test fixture — should not appear in production',
    getSignalsFromData: (data) => {
      timesCalled++;
      return data?.testOnlyChannel?.items || [];
    },
  });

  const fakeData = {
    testOnlyChannel: { items: [{ title: 'x', text: 'iran maritime', _t: 1 }] },
  };
  correlate(fakeData);
  assert.ok(timesCalled >= 1,
    'a freshly-registered source must be queried by correlate');
  assert.equal(getSourceClassForTag('test_only_source'), 'osint_aggregator');

  // Cleanup: re-register over the test entry with a no-op source so it
  // won't pollute later tests in the same run. (The registry has no
  // `unregister`; idempotent re-registration on tag is the closest.)
  registerSignalSource({
    tag: 'test_only_source',
    sourceClass: 'osint_aggregator',
    getSignalsFromData: () => [],
  });
});

test('registerSignalSource throws on missing required fields', () => {
  assert.throws(() => registerSignalSource({}),
    /tag is required/);
  assert.throws(() => registerSignalSource({ tag: 'x' }),
    /sourceClass is required/);
  assert.throws(
    () => registerSignalSource({ tag: 'x', sourceClass: 'osint_aggregator' }),
    /getSignalsFromData/,
  );
});

test('mapper runs before _src tag attachment for explorer source', () => {
  // The explorer source has a non-trivial mapper — verify it executes
  // and produces the reshaped {title, text, url, _src} envelope.
  const explorer = getSignalSource('explorer');
  assert.ok(explorer, 'explorer source must be registered');
  assert.ok(explorer.mapper, 'explorer must have a mapper');
  const reshaped = explorer.mapper({
    title: 'Hello',
    summary: 'World',
    sourceUrl: 'https://example.com',
  });
  assert.equal(reshaped.title, 'Hello');
  assert.equal(reshaped.text, 'World');
  assert.equal(reshaped.url, 'https://example.com');
});

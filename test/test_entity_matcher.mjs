/**
 * Tests for lib/aria/entityMatcher.mjs
 *
 * Run: node --test test/test_entity_matcher.mjs
 */
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { normalizeEntity, fuzzyMatch, screenEntity } from '../lib/aria/entityMatcher.mjs';

// ── normalizeEntity ──────────────────────────────────────────────────────────

describe('normalizeEntity', () => {
  it('transliterates Cyrillic to Latin', () => {
    const result = normalizeEntity('\u0420\u043e\u0441\u043d\u0435\u0444\u0442\u044c');
    assert.ok(result.includes('rosneft'), `Expected "rosneft" in "${result}"`);
  });

  it('normalises Arabic name variants to the same form', () => {
    const a = normalizeEntity('Al-Qaeda');
    const b = normalizeEntity('Al Qaida');
    assert.strictEqual(a, b, `"${a}" should equal "${b}"`);
  });

  it('strips diacritics', () => {
    const result = normalizeEntity('Fran\u00e7ois M\u00fcller');
    assert.ok(!result.includes('\u00e7'), 'Should strip cedilla');
    assert.ok(!result.includes('\u00fc'), 'Should strip umlaut');
  });

  it('removes legal suffixes', () => {
    const result = normalizeEntity('Rosneft Oil Ltd');
    assert.ok(!result.includes('ltd'), 'Should remove "ltd"');
  });

  it('returns empty string for falsy input', () => {
    assert.strictEqual(normalizeEntity(''), '');
    assert.strictEqual(normalizeEntity(null), '');
    assert.strictEqual(normalizeEntity(undefined), '');
  });
});

// ── fuzzyMatch ───────────────────────────────────────────────────────────────

describe('fuzzyMatch', () => {
  const candidates = [
    { name: 'Islamic Revolutionary Guard Corps' },
    { name: 'Rosneft Oil' },
    { name: 'Baykar Defence' },
  ];

  it('matches acronym IRGC to full name', () => {
    const hits = fuzzyMatch('IRGC', candidates);
    assert.ok(hits.length > 0, 'IRGC should match Islamic Revolutionary Guard Corps');
    assert.ok(hits[0].name.includes('Islamic Revolutionary Guard'), `Top hit: ${hits[0].name}`);
  });

  it('matches "rosneft" to Rosneft Oil', () => {
    const hits = fuzzyMatch('rosneft', candidates);
    assert.ok(hits.length > 0, 'rosneft should match Rosneft Oil');
  });

  it('returns empty array for unrelated query', () => {
    const hits = fuzzyMatch('xyzzy corporation', candidates);
    assert.strictEqual(hits.length, 0);
  });

  it('handles string candidates', () => {
    const hits = fuzzyMatch('rosneft', ['Rosneft Oil', 'Boeing']);
    assert.ok(hits.length > 0);
  });
});

// ── screenEntity ─────────────────────────────────────────────────────────────

describe('screenEntity', () => {
  const lists = [
    {
      name: 'OFAC SDN',
      entries: [
        { name: 'Rosneft', type: 'entity', uid: 'SDN-12345' },
        { name: 'Islamic Revolutionary Guard Corps', type: 'entity', uid: 'SDN-99999' },
      ],
    },
  ];

  it('flags a matching entity', () => {
    const result = screenEntity('Rosneft Oil Company', lists);
    assert.strictEqual(result.matched, true, 'Rosneft Oil Company should match');
    assert.ok(result.matches.length > 0);
    assert.ok(['critical', 'high', 'medium'].includes(result.risk_level),
      `Risk level "${result.risk_level}" should be non-clear`);
  });

  it('returns clear for unknown entity', () => {
    const result = screenEntity('Innocent Bakery Ltd', lists);
    assert.strictEqual(result.matched, false);
    assert.strictEqual(result.risk_level, 'clear');
  });

  it('returns safe result for missing input', () => {
    const result = screenEntity('', null);
    assert.strictEqual(result.matched, false);
  });

  it('includes screened_at timestamp', () => {
    const result = screenEntity('Rosneft', lists);
    assert.ok(result.screened_at, 'Should have screened_at');
  });
});

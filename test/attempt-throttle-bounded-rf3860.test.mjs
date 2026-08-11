// test/attempt-throttle-bounded-rf3860.test.mjs
//
// R-F3860 — the three per-email brute-force throttles in server.mjs
// (_loginAttempts, _verifyAttempts, _resetAttempts) pruned only the key they
// were touching, so an attacker cycling addresses grew each one without bound.
// Unauthenticated reachability + a long-lived fly machine = a slow leak, three
// times over. Bounded by ONE shared sweep, because fixing them separately is how
// the third came to be written with the same defect as the first two.
//
// Run: node --test test/attempt-throttle-bounded-rf3860.test.mjs

import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { describe, it } from 'node:test';

import { pruneAttemptMap, MAX_ATTEMPT_ENTRIES } from '../lib/util/attemptThrottle.mjs';

const TTL = 15 * 60 * 1000;
const NOW = 1_700_000_000_000;

describe('R-F3860 pruneAttemptMap bounds growth', () => {
  it('drops entries whose window has aged out', () => {
    const m = new Map([
      ['old@x', { count: 1, firstAt: NOW - TTL - 1 }],
      ['fresh@x', { count: 1, firstAt: NOW - 1000 }],
    ]);
    assert.equal(pruneAttemptMap(m, TTL, NOW), 1);
    assert.deepEqual([...m.keys()], ['fresh@x']);
  });

  it('NEVER evicts an address still serving a lockout', () => {
    // Evicting a locked-out entry would hand the attacker a free reset — the
    // throttle would forget the lockout it just imposed.
    const m = new Map([
      ['locked@x', { count: 9, firstAt: NOW - TTL * 10, lockedUntil: NOW + 60_000 }],
      ['stale@x', { count: 1, firstAt: NOW - TTL * 10 }],
    ]);
    pruneAttemptMap(m, TTL, NOW);
    assert.ok(m.has('locked@x'), 'a live lockout must survive the sweep');
    assert.ok(!m.has('stale@x'));
  });

  it('an expired lockout is collectable again', () => {
    const m = new Map([['was@x', { count: 9, firstAt: NOW - TTL * 10, lockedUntil: NOW - 1 }]]);
    pruneAttemptMap(m, TTL, NOW);
    assert.equal(m.size, 0);
  });

  it('enforces the hard ceiling even when every entry is fresh', () => {
    const m = new Map();
    for (let i = 0; i < MAX_ATTEMPT_ENTRIES + 500; i += 1) {
      m.set(`u${i}@x`, { count: 1, firstAt: NOW - i });   // later i == older
    }
    pruneAttemptMap(m, TTL, NOW);
    assert.ok(m.size <= MAX_ATTEMPT_ENTRIES,
      `map still holds ${m.size} entries — the ceiling did not apply`);
    assert.ok(m.has('u0@x'), 'the NEWEST entry must survive');
  });

  it('the ceiling still respects live lockouts', () => {
    const m = new Map();
    m.set('locked@x', { count: 9, firstAt: NOW - 999_999, lockedUntil: NOW + 60_000 });
    for (let i = 0; i < MAX_ATTEMPT_ENTRIES + 200; i += 1) {
      m.set(`u${i}@x`, { count: 1, firstAt: NOW - i });
    }
    pruneAttemptMap(m, TTL, NOW);
    assert.ok(m.has('locked@x'), 'a lockout must outrank the ceiling');
  });

  it('tolerates junk without throwing', () => {
    assert.equal(pruneAttemptMap(null, TTL, NOW), 0);
    assert.equal(pruneAttemptMap(undefined, TTL, NOW), 0);
    const m = new Map([['a', null], ['b', {}], ['c', { firstAt: 'nope' }]]);
    assert.doesNotThrow(() => pruneAttemptMap(m, TTL, NOW));
  });
});

describe('R-F3860 all three maps are actually swept', () => {
  const src = fs.readFileSync(
    path.join(
      path.resolve(path.dirname(new URL(import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, '$1'), '..'),
      'server.mjs',
    ), 'utf8',
  );

  it('server.mjs imports the shared bound', () => {
    assert.ok(src.includes("from './lib/util/attemptThrottle.mjs'"),
      'a per-map ad-hoc cap is how these three drifted apart in the first place');
  });

  for (const [map, ttl] of [
    ['_loginAttempts', '_LOGIN_WINDOW_MS'],
    ['_verifyAttempts', '_VERIFY_WINDOW_MS'],
    ['_resetAttempts', '_RESET_WINDOW_MS'],
  ]) {
    it(`${map} is swept on the write that grows it`, () => {
      const at = src.indexOf(`${map}.set(`);
      assert.ok(at > -1, `${map}.set() not found`);
      const after = src.slice(at, at + 500);
      assert.ok(new RegExp(`pruneAttemptMap\\(${map},\\s*${ttl}\\)`).test(after),
        `${map} is written without a sweep — it grows one entry per distinct email`);
    });
  }

  it('no throttle map is left unbounded', () => {
    // Catches a FOURTH map being added with the same shape and no sweep.
    const maps = [...src.matchAll(/const (_\w*Attempts) = new Map\(\)/g)].map((m) => m[1]);
    assert.ok(maps.length >= 3, `expected the three known throttles, found ${maps.length}`);
    for (const m of maps) {
      assert.ok(src.includes(`pruneAttemptMap(${m},`),
        `${m} is an attempt map with no pruneAttemptMap() call`);
    }
  });
});

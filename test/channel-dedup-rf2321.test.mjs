// R-F2321 — cross-day post dedup: a key posted within the window is suppressed;
// persists across restarts; prunes outside the window.
import { test } from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const TMP = path.join(os.tmpdir(), 'ef-rf2321-dedup.json');
process.env.CHANNEL_POST_DEDUP_PATH = TMP;
process.env.CHANNEL_DEDUP_WINDOW_DAYS = '45';
const { dedupKey, wasRecentlyPosted, recordPosted } = await import('../lib/telegram/postDedup.mjs');

test('dedupKey is stable + case-insensitive for a signal', () => {
  assert.equal(dedupKey({ title: '⚖️ Sanctions spotlight: Rosoboronexport' }),
               dedupKey({ title: '⚖️ SANCTIONS SPOTLIGHT: ROSOBORONEXPORT' }));
});

test('dedupKey suppresses the same evidence URL even when backend ids change', () => {
  const a = dedupKey({
    id: 'sig-1',
    signal_type: 'active_tender',
    target: 'Angola',
    decision_summary: 'Angola launches armoured vehicle tender',
    url: 'https://Example.com/path/tender?utm_source=telegram&utm_campaign=x#section',
    detected_at: '2026-07-07T10:00:00Z',
  });
  const b = dedupKey({
    id: 'sig-2',
    signal_type: 'active_tender',
    target: 'angola',
    title: 'ANGOLA launches armoured vehicle tender',
    url: 'https://example.com/path/tender',
    detected_at: '2026-07-07T18:30:00Z',
  });
  assert.equal(a, b);
});

test('unseen → posts; after record → suppressed within window', () => {
  fs.rmSync(TMP, { force: true });
  const k = dedupKey({ title: 'Sanctions spotlight: Acme' });
  assert.equal(wasRecentlyPosted(k), false);
  recordPosted(k);
  assert.equal(wasRecentlyPosted(k), true);
});

test('persists to disk (survives a restart/redeploy)', () => {
  const raw = JSON.parse(fs.readFileSync(TMP, 'utf8'));
  assert.ok(raw.entries.some(e => e.k.includes('acme')));
});

test('entries inside the extended window are suppressed', () => {
  const k = 'weekly-repeat';
  fs.writeFileSync(TMP, JSON.stringify({ entries: [{ k, d: '2026-07-01' }] }));
  const realDate = Date;
  global.Date = class extends realDate {
    constructor(...args) { return args.length ? new realDate(...args) : new realDate('2026-07-08T12:00:00Z'); }
    static now() { return new realDate('2026-07-08T12:00:00Z').getTime(); }
    static parse(value) { return realDate.parse(value); }
    static UTC(...args) { return realDate.UTC(...args); }
  };
  try {
    assert.equal(wasRecentlyPosted(k), true);
  } finally {
    global.Date = realDate;
    fs.rmSync(TMP, { force: true });
  }
});

test('entries outside the window are pruned (not seen)', () => {
  const k = 'stale-entity';
  // write an entry dated 30 days ago directly
  fs.writeFileSync(TMP, JSON.stringify({ entries: [{ k, d: '2000-01-01' }] }));
  assert.equal(wasRecentlyPosted(k), false);
  fs.rmSync(TMP, { force: true });
});

test('recordPosted updates an existing key instead of appending duplicates', () => {
  fs.rmSync(TMP, { force: true });
  recordPosted('same-key');
  recordPosted('same-key');
  const raw = JSON.parse(fs.readFileSync(TMP, 'utf8'));
  assert.equal(raw.entries.filter(e => e.k === 'same-key').length, 1);
  fs.rmSync(TMP, { force: true });
});

test('recordPosted accepts an array', () => {
  fs.rmSync(TMP, { force: true });
  recordPosted(['a-entity', 'b-entity']);
  assert.equal(wasRecentlyPosted('a-entity'), true);
  assert.equal(wasRecentlyPosted('b-entity'), true);
  fs.rmSync(TMP, { force: true });
});

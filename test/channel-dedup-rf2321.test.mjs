// R-F2321 — cross-day post dedup: a key posted within the window is suppressed;
// persists across restarts; prunes outside the window.
import { test } from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const TMP = path.join(os.tmpdir(), 'ef-rf2321-dedup.json');
process.env.CHANNEL_POST_DEDUP_PATH = TMP;
process.env.CHANNEL_DEDUP_WINDOW_DAYS = '7';
const { dedupKey, wasRecentlyPosted, recordPosted } = await import('../lib/telegram/postDedup.mjs');

test('dedupKey is stable + case-insensitive for a signal', () => {
  assert.equal(dedupKey({ title: '⚖️ Sanctions spotlight: Rosoboronexport' }),
               dedupKey({ title: '⚖️ SANCTIONS SPOTLIGHT: ROSOBORONEXPORT' }));
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

test('entries outside the window are pruned (not seen)', () => {
  const k = 'stale-entity';
  // write an entry dated 30 days ago directly
  fs.writeFileSync(TMP, JSON.stringify({ entries: [{ k, d: '2000-01-01' }] }));
  assert.equal(wasRecentlyPosted(k), false);
  fs.rmSync(TMP, { force: true });
});

test('recordPosted accepts an array', () => {
  fs.rmSync(TMP, { force: true });
  recordPosted(['a-entity', 'b-entity']);
  assert.equal(wasRecentlyPosted('a-entity'), true);
  assert.equal(wasRecentlyPosted('b-entity'), true);
  fs.rmSync(TMP, { force: true });
});

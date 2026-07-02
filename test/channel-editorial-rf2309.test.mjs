// R-F2309 — editorial queue drains ONE curated post/day (FIFO), persists progress
// so a redeploy never reposts or skips, and reverts to live signals when drained.
import { test } from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const TMP = path.join(os.tmpdir(), 'ef-rf2309-test.json');
process.env.CHANNEL_EDITORIAL_STATE_PATH = TMP;
const { EDITORIAL_POSTS, peekNextEditorial, markEditorialPosted, editorialStatus } =
  await import('../lib/telegram/editorialQueue.mjs');

test('FIFO: peek returns first unposted, advances after marking', () => {
  fs.rmSync(TMP, { force: true });
  const first = peekNextEditorial();
  assert.equal(first.id, EDITORIAL_POSTS[0].id);
  markEditorialPosted(first.id);
  assert.equal(peekNextEditorial().id, EDITORIAL_POSTS[1].id);
});

test('progress persists to disk (survives a redeploy)', () => {
  const raw = JSON.parse(fs.readFileSync(TMP, 'utf8'));
  assert.ok(raw.posted.includes(EDITORIAL_POSTS[0].id));
});

test('status reports total/posted/remaining', () => {
  const s = editorialStatus();
  assert.equal(s.total, EDITORIAL_POSTS.length);
  assert.ok(s.posted >= 1 && s.remaining === s.total - s.posted);
});

test('drains to null once every post is published (→ live-signal fallback)', () => {
  for (const p of EDITORIAL_POSTS) markEditorialPosted(p.id);
  assert.equal(peekNextEditorial(), null);
  fs.rmSync(TMP, { force: true });
});

test('every post is substantial and carries a consult CTA', () => {
  for (const p of EDITORIAL_POSTS) {
    assert.ok(p.text.length > 150, `${p.id} too short`);
    assert.ok(/SCREEN|Consult|HELP/.test(p.text), `${p.id} must have a consult CTA`);
    assert.ok(/Source|Sources/.test(p.text), `${p.id} must cite a source`);
  }
});

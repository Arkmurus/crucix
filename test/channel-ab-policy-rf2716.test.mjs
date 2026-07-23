// R-F2716 — A→B publish policy (operator-chosen: publish labelled Grade B).
//   07:00  best Grade A else HOLD (never a fallback).
//   17:00  Grade A else best LABELLED Grade B else "no qualifying intelligence".
// Grade B must be explicit single-source / corroboration-pending — never implies
// confirmation (USP). A always beats B.

import { after, before, beforeEach, describe, it } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'aria-ab-'));
process.env.CHANNEL_POST_DEDUP_PATH = path.join(tempDir, 'posted.json');
process.env.CHANNEL_EDITORIAL_STATE_PATH = path.join(tempDir, 'editorial.json');

const {
  handleMorningSignalCron, selectTelegramGoldenIntel, selectTelegramGradeB,
  formatGradeBChannelPost,
} = await import('../lib/telegram/channelServerHooks.mjs');

function sig(grade, overrides = {}) {
  return {
    id: `sig-${grade}`, signal_type: 'active_tender', priority: 'HIGH', confidence: 'HIGH',
    intel_grade: grade, source_tier: grade === 'A' ? 'tier_1a' : 'tier_2', score: 85,
    // R-F2899 — both lanes require real per-item analysis, not a classifier template.
    why_action_provenance: 'source_adapter',
    decision_summary: `${grade} tender in Poland`, why_it_matters: 'Commercial window.',
    recommended_action: 'Qualify opportunity', target: 'Poland',
    source: 'monitored source', url: 'https://example.com/x', detected_at: '2026-07-18T09:00:00Z',
    ...overrides,
  };
}

let FEED_SIGNALS = [];
const calls = [];
const originalFetch = global.fetch;

before(() => {
  global.fetch = async (url, opts = {}) => {
    calls.push({ url: String(url), opts });
    if (String(url).includes('/getChat')) {
      return new Response(JSON.stringify({ ok: true, result: { id: -1001, type: 'channel', title: 'ARIA Intelligence' } }), { status: 200 });
    }
    if (String(url).includes('/api/aria/intel/signals/recent')) {
      return new Response(JSON.stringify({
        ok: true, signals: FEED_SIGNALS, freshness: { stale: false, stale_reasons: [], backfilled: false },
      }), { status: 200 });
    }
    const ct = String(opts.headers?.['Content-Type'] || opts.headers?.['content-type'] || '');
    if (String(url).includes('/sendPhoto') && ct.includes('multipart/form-data')) {
      return new Response(JSON.stringify({ ok: true, result: { photo: [{ file_id: 'card' }] } }), { status: 200 });
    }
    return new Response(JSON.stringify({ ok: true, result: { message_id: calls.length } }), { status: 200 });
  };
});
after(() => {
  global.fetch = originalFetch;
  fs.rmSync(tempDir, { recursive: true, force: true });
});
beforeEach(() => {
  calls.length = 0;
  fs.writeFileSync(process.env.CHANNEL_POST_DEDUP_PATH, '{}');
});

const bot = { botToken: 't', chatId: '123', channelId: '123' };
const sentMessages = () => calls.filter(c => c.url.includes('/sendMessage'));

describe('R-F2716 selectors', () => {
  it('selectTelegramGradeB picks a Grade B; the Golden (A) selector does not', () => {
    const feed = { ok: true, freshness: { stale: false, stale_reasons: [] }, signals: [sig('B')] };
    assert.equal(selectTelegramGoldenIntel(feed), null, 'no Grade A present');
    assert.equal(selectTelegramGradeB(feed)?.id, 'sig-B');
  });
  it('the Grade B post is labelled single-source / corroboration pending', () => {
    const text = formatGradeBChannelPost(sig('B'));
    assert.match(text, /GRADE B/);
    assert.match(text, /corroboration pending/i);
    assert.doesNotMatch(text, /GOLDEN INTEL/);
  });
});

describe('R-F2716 A→B slot policy', () => {
  it('07:00 with only Grade B → HOLD, no publish', async () => {
    FEED_SIGNALS = [sig('B')];
    const r = await handleMorningSignalCron({}, bot, { hour: 7 });
    assert.equal(r?.reason, 'held_for_corroboration');
    assert.equal(sentMessages().length, 0);
  });

  it('17:00 with only Grade B → publishes the LABELLED Grade B', async () => {
    FEED_SIGNALS = [sig('B')];
    const r = await handleMorningSignalCron({}, bot, { hour: 17 });
    assert.equal(r?.grade, 'B');
    const msgs = sentMessages();
    assert.equal(msgs.length, 1);
    assert.match(String(msgs[0].opts.body), /GRADE B/);
    assert.match(String(msgs[0].opts.body), /corroboration pending/i);
  });

  it('17:00 with a Grade A present → publishes A (Golden), NOT B', async () => {
    FEED_SIGNALS = [sig('A'), sig('B')];
    const r = await handleMorningSignalCron({}, bot, { hour: 17 });
    assert.equal(r?.grade, 'A');
    const body = String(sentMessages()[0]?.opts.body || '');
    assert.match(body, /GOLDEN INTEL/);
    assert.doesNotMatch(body, /GRADE B/);
  });

  it('17:00 with nothing qualifying → records no_qualifying_intelligence, no publish', async () => {
    FEED_SIGNALS = [sig('REJECT')];
    const r = await handleMorningSignalCron({}, bot, { hour: 17 });
    assert.equal(r?.reason, 'no_qualifying_intelligence');
    assert.equal(sentMessages().length, 0);
  });
});

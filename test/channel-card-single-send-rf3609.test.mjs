// test/channel-card-single-send-rf3609.test.mjs
//
// R-F3609 — capability test: a Golden Intel publish must post the card ONCE.
//
// THE DEFECT THIS PINS
// --------------------
// `uploadSvgAsPhoto` was named as an upload, but the Telegram Bot API has no
// upload-without-send method — `sendPhoto` publishes. Both callers believed they
// held an unpublished file_id and sent it AGAIN to attach a caption, so every
// carded post published the image twice (the second copy being `photo[0]`, the
// SMALLEST rendition, and the one carrying the caption).
//
// WHY NOTHING CAUGHT IT
// ---------------------
// `channel-card-evidence-rf2903` exercises the media function in ISOLATION, so it
// can only ever see one send. Every publish-path test — golden-intel-rf2469,
// ab-policy-rf2716, morning-card-rf2468 — filters `/sendMessage` and never counts
// `/sendPhoto`, or asserts only on the HOLD path where nothing is sent at all. 205
// Node tests were green the whole time it was live on the public channel.
//
// So this test counts the PHOTO calls on the real cron path. It fails against the
// pre-R-F3609 tree with `sendPhoto 2 !== 1`.

import { after, before, beforeEach, describe, it } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'aria-rf3609-'));
process.env.CHANNEL_POST_DEDUP_PATH = path.join(tempDir, 'posted.json');
process.env.CHANNEL_EDITORIAL_STATE_PATH = path.join(tempDir, 'editorial.json');
process.env.CHANNEL_OUTCOME_LEDGER_PATH = path.join(tempDir, 'outcomes.json');

const { handleMorningSignalCron, getRecentChannelOutcomes } =
  await import('../lib/telegram/channelServerHooks.mjs');

/** A signal that passes every clause of the R-F2908 publish gate. */
function gradeASignal() {
  return {
    id: 'rf3609-A',
    signal_type: 'sanctions_change',
    intel_grade: 'A',
    priority: 'HIGH',
    confidence: 'HIGH',
    score: 90,
    source_tier: 'tier_1a',
    why_action_provenance: 'source_adapter',   // R-F2899
    decision_summary: 'Entity designated under the EU consolidated list',
    why_it_matters: 'A counterparty in an active pipeline is now blocked.',
    recommended_action: 'Freeze onboarding and re-screen the group',
    target: 'Example Trading LLC',
    source: 'EU Official Journal',
    url: 'https://example.org/oj/designation',
    corroboration: 'corroborated',
    detected_at: '2026-08-01T04:00:00Z',
  };
}

const bot = { botToken: 'test-token', chatId: '1234567890', channelId: '1234567890' };
const originalFetch = global.fetch;
let calls = [];

before(() => {
  global.fetch = async (url, opts = {}) => {
    calls.push({ url: String(url), opts });
    const u = String(url);
    if (u.includes('/getChat')) {
      return new Response(JSON.stringify({
        ok: true,
        result: { id: -1001, type: 'supergroup', title: 'ARIA Intelligence' },
      }), { status: 200 });
    }
    if (u.includes('/api/aria/intel/signals/recent')) {
      return new Response(JSON.stringify({
        ok: true,
        signals: [gradeASignal()],
        freshness: { stale: false, stale_reasons: [], blocking_stale_reasons: [], backfilled: false },
      }), { status: 200 });
    }
    if (u.includes('/sendPhoto')) {
      return new Response(JSON.stringify({
        ok: true,
        // Telegram returns PhotoSize ASCENDING. The old code re-sent [0] — the
        // thumbnail — so this fixture keeps several sizes to pin that too.
        result: { message_id: 5001, photo: [{ file_id: 'thumb' }, { file_id: 'mid' }, { file_id: 'full' }] },
      }), { status: 200 });
    }
    return new Response(JSON.stringify({ ok: true, result: { message_id: 5002 } }), { status: 200 });
  };
});

after(() => {
  global.fetch = originalFetch;
  delete process.env.CHANNEL_POST_DEDUP_PATH;
  delete process.env.CHANNEL_EDITORIAL_STATE_PATH;
  delete process.env.CHANNEL_OUTCOME_LEDGER_PATH;
  fs.rmSync(tempDir, { recursive: true, force: true });
});

beforeEach(() => {
  calls = [];
  fs.writeFileSync(process.env.CHANNEL_POST_DEDUP_PATH, '{}');
  fs.writeFileSync(process.env.CHANNEL_OUTCOME_LEDGER_PATH, '[]');
});

const photoCalls = () => calls.filter(c => c.url.includes('/sendPhoto'));
const textCalls = () => calls.filter(c => c.url.includes('/sendMessage'));

describe('R-F3609 — the Golden Intel card is published exactly once', () => {
  it('a Grade A publish sends ONE photo and ONE text', async () => {
    const res = await handleMorningSignalCron({}, bot, { hour: 7 });

    assert.equal(res?.ok, true, 'the Grade A signal should publish');
    assert.equal(photoCalls().length, 1,
      `the card must be sent ONCE — ${photoCalls().length} sendPhoto calls means the channel got duplicates`);
    assert.equal(textCalls().length, 1, 'exactly one text post accompanies the card');
  });

  it('the single photo send carries the caption — there is no second, captioning send', async () => {
    await handleMorningSignalCron({}, bot, { hour: 7 });

    const photo = photoCalls()[0];
    assert.ok(photo, 'a card should have been sent');
    const ct = String(photo.opts.headers?.['Content-Type'] || photo.opts.headers?.['content-type'] || '');
    assert.match(ct, /multipart\/form-data/, 'the card is uploaded as multipart PNG');
    const body = Buffer.isBuffer(photo.opts.body) ? photo.opts.body.toString('latin1') : String(photo.opts.body);
    assert.match(body, /name="caption"/,
      'the caption must ride along with the image; a separate captioning send is the duplicate-post bug');
    assert.match(body, /Content-Type: image\/png/, 'Telegram rejects SVG — must be rasterised PNG');
  });

  it('the delivery ledger records the CARD id, not only the text id (§25)', async () => {
    await handleMorningSignalCron({}, bot, { hour: 7 });

    const latest = getRecentChannelOutcomes(5)[0];
    assert.equal(latest?.outcome, 'delivered');
    // The ledger recording only `text#` is precisely why an extra photo per post
    // stayed invisible: it showed up merely as an unexplained gap between
    // consecutive text message ids.
    assert.match(String(latest.detail), /card#5001/, 'the card message id must be recorded');
    assert.match(String(latest.detail), /text#5002/, 'the text message id must still be recorded');
  });

  it('a card that cannot rasterise still lets the text post through', async () => {
    // Rasterisation is best-effort polish; the intel is the post. Force the failure
    // by making the photo endpoint reject, and assert the text still goes.
    const saved = global.fetch;
    global.fetch = async (url, opts = {}) => {
      if (String(url).includes('/sendPhoto')) {
        calls.push({ url: String(url), opts });
        return new Response('nope', { status: 400 });
      }
      return saved(url, opts);
    };
    try {
      const res = await handleMorningSignalCron({}, bot, { hour: 7 });
      assert.equal(res?.ok, true, 'the text post must still succeed');
      assert.equal(textCalls().length, 1);
    } finally { global.fetch = saved; }
  });
});

describe('R-F3609 — the misnamed symbol is gone, not aliased', () => {
  it('channelMedia no longer exports uploadSvgAsPhoto', async () => {
    const media = await import('../lib/telegram/channelMedia.mjs');
    // A comment asking callers not to send twice cannot stop the next caller.
    // Absence of the symbol can (the R-F2936 precedent).
    assert.equal(media.uploadSvgAsPhoto, undefined,
      'the name claimed an upload while it published — it must not come back');
    assert.equal(typeof media.sendSvgCard, 'function');
  });

  it('sendSvgCard returns the LARGEST rendition, not the thumbnail', async () => {
    const { generateInfographicCard, sendSvgCard } = await import('../lib/telegram/channelMedia.mjs');
    const saved = global.fetch;
    global.fetch = async () => new Response(JSON.stringify({
      ok: true, result: { message_id: 77, photo: [{ file_id: 'thumb' }, { file_id: 'full' }] },
    }), { status: 200 });
    try {
      const svg = generateInfographicCard({ title: 'T', subtitle: 'S', source: 'X', type: 'daily' });
      const res = await sendSvgCard({ botToken: 'T', chatId: '-100' }, svg, { caption: 'c' });
      assert.equal(res.ok, true);
      assert.equal(res.messageId, 77, 'the caller needs the sent message id for the §25 ledger');
      assert.equal(res.fileId, 'full', 'PhotoSize is ascending — [0] is the thumbnail');
    } finally { global.fetch = saved; }
  });
});

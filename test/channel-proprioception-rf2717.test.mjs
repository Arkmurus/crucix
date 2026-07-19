// R-F2717 — TG batch T-D: delivery durability + §25 proprioception.
//   #13 reportChannelOutcome now persists a durable, queryable outcome ledger
//       (was fire-and-forget, no response check, no persistence) so ARIA can
//       answer "did I deliver X?" even when the brain is unreachable.
//   #11 a PARTIAL delivery (photo sent, text failed) records the dedup key so the
//       next slot does not re-send the photo (source-contract on the code path).
//   #12 /api/admin/channel/state includes the CRON scheduler + the outcome ledger.

import { after, before, beforeEach, describe, it } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'aria-prop-'));
process.env.CHANNEL_POST_DEDUP_PATH = path.join(tempDir, 'posted.json');
process.env.CHANNEL_OUTCOME_LEDGER_PATH = path.join(tempDir, 'outcomes.json');

const { handleMorningSignalCron, getRecentChannelOutcomes } =
  await import('../lib/telegram/channelServerHooks.mjs');

const HOOKS_SRC = fs.readFileSync(
  path.join(path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1')),
    '..', 'lib', 'telegram', 'channelServerHooks.mjs'), 'utf8');

const originalFetch = global.fetch;
before(() => {
  global.fetch = async (url) => {
    if (String(url).includes('/getChat')) {
      return new Response(JSON.stringify({ ok: true, result: { id: -1001, type: 'channel', title: 'ARIA Intelligence' } }), { status: 200 });
    }
    // empty feed → the cron records an outcome but sends nothing
    if (String(url).includes('/api/aria/intel/signals/recent')) {
      return new Response(JSON.stringify({ ok: true, signals: [], freshness: { stale: false, stale_reasons: [] } }), { status: 200 });
    }
    return new Response(JSON.stringify({ ok: true }), { status: 200 }); // brain signal acks
  };
});
after(() => {
  global.fetch = originalFetch;
  fs.rmSync(tempDir, { recursive: true, force: true });
});
beforeEach(() => { try { fs.unlinkSync(process.env.CHANNEL_OUTCOME_LEDGER_PATH); } catch {} });

describe('R-F2717 #13 durable outcome ledger', () => {
  it('an outcome is persisted and queryable via getRecentChannelOutcomes', async () => {
    await handleMorningSignalCron({}, { botToken: 't', chatId: '1', channelId: '1' }, { hour: 17 });
    // give the fire-and-forget reportChannelOutcome a tick to persist + ack
    await new Promise(r => setTimeout(r, 50));
    const outcomes = getRecentChannelOutcomes(10);
    assert.ok(outcomes.length >= 1, 'the run must persist at least one outcome');
    const rec = outcomes[0];
    assert.equal(rec.surface, 'telegram_channel');
    assert.equal(rec.action, 'daily_golden_intel');
    assert.ok(typeof rec.ts === 'string' && rec.ts.length > 0, 'outcome carries a timestamp');
    assert.equal(typeof rec.brain_ack, 'boolean');
  });

  it('the ledger survives a brain outage (persist happens before the POST)', async () => {
    // brain POST throws; the local record must still exist.
    const f = global.fetch;
    global.fetch = async (url) => {
      if (String(url).includes('/getChat')) {
        return new Response(JSON.stringify({ ok: true, result: { id: -1001, type: 'channel', title: 'ARIA Intelligence' } }), { status: 200 });
      }
      if (String(url).includes('/intel/signals/recent')) {
        return new Response(JSON.stringify({ ok: true, signals: [], freshness: { stale: false, stale_reasons: [] } }), { status: 200 });
      }
      throw new Error('brain unreachable');
    };
    try {
      await handleMorningSignalCron({}, { botToken: 't', chatId: '1', channelId: '1' }, { hour: 17 });
      await new Promise(r => setTimeout(r, 50));
      const outcomes = getRecentChannelOutcomes(10);
      assert.ok(outcomes.length >= 1, 'outcome persists even when the brain is down');
      assert.equal(outcomes[0].brain_ack, false, 'brain_ack is false when the brain never acked');
    } finally { global.fetch = f; }
  });
});

describe('R-F2717 #11 partial-delivery dedup (source contract)', () => {
  it('_channelSendWithCard tracks photoSent and the Golden path dedups a partial', () => {
    assert.match(HOOKS_SRC, /photoSent:\s*_photoSent/, 'card sender must return photoSent');
    assert.match(HOOKS_SRC, /else if \(res\.photoSent\)/, 'Golden path must handle the photo-sent/text-failed partial');
    // the partial branch records the dedup key so the photo is not re-sent
    const partial = HOOKS_SRC.slice(HOOKS_SRC.indexOf('else if (res.photoSent)'), HOOKS_SRC.indexOf('else if (res.photoSent)') + 800);
    assert.match(partial, /recordPosted\(_postDedupKey\(golden\)\)/,
      'a partial must record the dedup key (no re-send)');
    assert.match(partial, /'partial'/, 'a partial must report the partial outcome');
  });
});

// test/channel-morning-card-rf2468.test.mjs
// Capability test for R-F2468 Telegram morning cards.

import { after, before, describe, it } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'aria-channel-rf2468-'));
process.env.CHANNEL_EDITORIAL_STATE_PATH = path.join(tempDir, 'posted.json');
// R-F2479 added cross-day dedup to the editorial path; isolate the dedup state
// to a fresh temp file so this test does not read shared/prod posted-keys (which
// would pre-dedup the editorial and make the assertion non-deterministic).
process.env.CHANNEL_POST_DEDUP_PATH = path.join(tempDir, 'dedup.json');

const { handleMorningSignalCron } = await import('../lib/telegram/channelServerHooks.mjs');

describe('Channel morning cron cards', () => {
  const originalFetch = global.fetch;
  const calls = [];

  before(() => {
    global.fetch = async (url, opts = {}) => {
      calls.push({ url: String(url), opts });
      if (String(url).includes('/getChat')) {
        return new Response(JSON.stringify({ ok: true, result: { id: -1001, type: 'channel', title: 'ARIA Intelligence' } }), { status: 200 });
      }
      const contentType = String(opts.headers?.['Content-Type'] || opts.headers?.['content-type'] || '');
      if (String(url).includes('/sendPhoto') && contentType.includes('multipart/form-data')) {
        return new Response(JSON.stringify({ ok: true, result: { photo: [{ file_id: 'card-file-id' }] } }), { status: 200 });
      }
      return new Response(JSON.stringify({ ok: true, result: { message_id: calls.length } }), { status: 200 });
    };
  });

  after(() => {
    global.fetch = originalFetch;
    delete process.env.CHANNEL_EDITORIAL_STATE_PATH;
    delete process.env.CHANNEL_POST_DEDUP_PATH;
    fs.rmSync(tempDir, { recursive: true, force: true });
  });

  it('morning (07:00) holds — no publish — when no Grade A Golden Intel exists', async () => {
    // R-F2716: the morning slot never publishes a fallback; it holds for
    // corroboration (a Grade B may firm up to A by the evening slot).
    const result = await handleMorningSignalCron({}, { botToken: 'test-token', chatId: '1234567890', channelId: '1234567890' }, { hour: 7 });

    const telegramCalls = calls.filter(c => c.url.includes('/sendMessage') || c.url.includes('/sendPhoto'));
    assert.equal(result?.reason, 'held_for_corroboration');
    assert.equal(telegramCalls.length, 0);
  });
});

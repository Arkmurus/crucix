// test/channel-morning-card-rf2468.test.mjs
// Capability test for R-F2468 Telegram morning cards.

import { after, before, describe, it } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'aria-channel-rf2468-'));
process.env.CHANNEL_EDITORIAL_STATE_PATH = path.join(tempDir, 'posted.json');

const { handleMorningSignalCron } = await import('../lib/telegram/channelServerHooks.mjs');

describe('Channel morning cron cards', () => {
  const originalFetch = global.fetch;
  const calls = [];

  before(() => {
    global.fetch = async (url, opts = {}) => {
      calls.push({ url: String(url), opts });
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
    fs.rmSync(tempDir, { recursive: true, force: true });
  });

  it('publishes an editorial card and then the full morning text', async () => {
    await handleMorningSignalCron({}, { botToken: 'test-token', chatId: '1234567890', channelId: '1234567890' });

    const telegramCalls = calls.filter(c => c.url.includes('api.telegram.org'));
    assert.equal(telegramCalls.length, 3);
    assert.ok(telegramCalls[0].url.includes('/sendPhoto'));
    assert.ok(String(telegramCalls[0].opts.body).includes('aria_intel_'));
    assert.ok(telegramCalls[1].url.includes('/sendPhoto'));
    assert.ok(String(telegramCalls[1].opts.body).includes('card-file-id'));
    assert.ok(telegramCalls[2].url.includes('/sendMessage'));
    assert.ok(String(telegramCalls[2].opts.body).includes('Hidden in the supply chain'));
  });
});

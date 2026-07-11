// R-F2552 — public Telegram Golden Intel must use TELEGRAM_CHANNEL_ID only.

import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import assert from 'node:assert/strict';

const CONFIG = readFileSync(new URL('../crucix.config.mjs', import.meta.url), 'utf8');
const SERVER = readFileSync(new URL('../server.mjs', import.meta.url), 'utf8');

test('public channel config does not fall back to private TELEGRAM_CHAT_ID', () => {
  assert.match(CONFIG, /channelId:\s*process\.env\.TELEGRAM_CHANNEL_ID \|\| null/);
  assert.doesNotMatch(CONFIG, /channelId:\s*process\.env\.TELEGRAM_CHANNEL_ID \|\| process\.env\.TELEGRAM_CHAT_ID/);
});

test('public channel routes require TELEGRAM_CHANNEL_ID before publishing', () => {
  assert.match(SERVER, /function telegramChannelBotOrResponse/);
  assert.match(SERVER, /reason: 'telegram_channel_id_required'/);
  assert.match(SERVER, /TELEGRAM_CHAT_ID is reserved for private ops\/admin bot messages/);
});

test('legacy manual public-channel text sends target channelId, not private chatId', () => {
  const start = SERVER.indexOf('async function sendTelegramChannelText');
  assert.ok(start > -1, 'sendTelegramChannelText helper exists');
  const block = SERVER.slice(start, start + 520);
  assert.match(block, /chat_id: bot\.channelId/);
  assert.doesNotMatch(block, /chat_id: bot\.chatId/);
});

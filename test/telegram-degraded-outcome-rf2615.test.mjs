// test/telegram-degraded-outcome-rf2615.test.mjs
// R-F2615 §25 — a Telegram command that FAILS still sends a reply ("Brief failed: …"),
// and the pre-R-F2615 sendMessage wire logged that to the brain as 'delivered' because
// the HTTP send succeeded. Now a failed handler returns degradedReply(text) and the
// delivery outcome is reported honestly as 'error'. These tests drive the real mechanism.
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { TelegramAlerter, degradedReply } from '../lib/alerts/telegram.mjs';

function makeBot() {
  const bot = new TelegramAlerter({ botToken: 't', chatId: '123' });
  const outcomes = [];
  bot._sendMessageRaw = async () => ({ ok: true, messageId: 'm1' }); // pretend the send succeeds
  bot._reportBotOutcome = async (outcome) => { outcomes.push(outcome); };
  return { bot, outcomes };
}

describe('R-F2615 — telegram command-reply degraded delivery outcome', () => {
  it('degradedReply wraps failure text with the marker', () => {
    const d = degradedReply('Brief failed: boom');
    assert.equal(d.__degradedReply, true);
    assert.equal(d.text, 'Brief failed: boom');
    assert.equal(degradedReply(null).text, ''); // null-safe
  });

  it('sendMessage reports the caller-overridden outcome on a successful send', async () => {
    const { bot, outcomes } = makeBot();
    await bot.sendMessage('a good answer');                          // default path
    await bot.sendMessage('Brief failed: x', { outcome: 'error' });  // degraded reply
    assert.deepEqual(outcomes, ['delivered', 'error'],
      'a degraded reply that sends fine must be reported as error, not delivered');
  });

  it('a failed command reply flows through _handleMessage as an error outcome', async () => {
    const { bot, outcomes } = makeBot();
    bot._allowedUsers = new Set(['999']);
    bot.onCommand('/boom', async () => degradedReply('Boom failed: kaboom'));
    await bot._handleMessage({ text: '/boom', chat: { id: 555 }, from: { id: 999 }, message_id: 1 });
    assert.deepEqual(outcomes, ['error'],
      'a command whose handler returns degradedReply must report error via _handleMessage');
  });

  it('a successful command reply still reports delivered', async () => {
    const { bot, outcomes } = makeBot();
    bot._allowedUsers = new Set(['999']);
    bot.onCommand('/ok', async () => 'here is your answer');
    await bot._handleMessage({ text: '/ok', chat: { id: 555 }, from: { id: 999 }, message_id: 2 });
    assert.deepEqual(outcomes, ['delivered'], 'a normal reply must stay delivered');
  });
});

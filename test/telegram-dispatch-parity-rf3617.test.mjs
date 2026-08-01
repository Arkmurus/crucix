// test/telegram-dispatch-parity-rf3617.test.mjs
//
// R-F3617 — the two Telegram transports must make the SAME routing decision.
//
// Telegram delivers updates by polling OR by webhook. The routing decision — operator
// command / public keyword / drop — lived INLINE in `_pollUpdates`, while `/webhook`
// (server.mjs) called `_handleMessage` directly and therefore skipped
// `_handleChannelKeyword` altogether. Switching this deployment to a webhook would
// have silently reinstated R-F3610: every subscriber reply dropped again.
//
// It is dormant today (no webhook set, TELEGRAM_WEBHOOK_SECRET unset so production
// refuses deliveries) — which is precisely why nothing would have caught it. This is
// CLAUDE.md §13 (stream-bypass): one surface forked from another drifts. The fix
// removes the fork; these tests pin that it stays removed.

import { afterEach, beforeEach, describe, it } from 'node:test';
import assert from 'node:assert/strict';

const { TelegramAlerter } = await import('../lib/alerts/telegram.mjs');

const ADMIN = '-5280434891';
const PUBLIC_CHANNEL = '-1003836086295';
const SUBSCRIBER = '99887766';

let sent = [];
const originalFetch = global.fetch;

function alerter() {
  return new TelegramAlerter({
    botToken: 'test-token', chatId: ADMIN, channelId: PUBLIC_CHANNEL, channelDiscussionId: null,
  });
}

function message(chatId, text, { userId = SUBSCRIBER, type = 'supergroup' } = {}) {
  return { message_id: 7, chat: { id: Number(chatId), type }, from: { id: Number(userId), is_bot: false }, text };
}

beforeEach(() => {
  sent = [];
  global.fetch = async (url, opts = {}) => {
    const u = String(url);
    if (u.includes('/sendMessage')) {
      sent.push(JSON.parse(String(opts.body || '{}')));
      return new Response(JSON.stringify({ ok: true, result: { message_id: sent.length } }), { status: 200 });
    }
    if (u.includes('/sanctions/') || u.includes('/compliance/') || u.includes('/screen')) {
      return new Response(JSON.stringify({ screened: true, blocked: false, matches: [] }), { status: 200 });
    }
    return new Response(JSON.stringify({ ok: true, result: {} }), { status: 200 });
  };
});
afterEach(() => { global.fetch = originalFetch; });

describe('R-F3617 — one dispatcher, both transports', () => {
  it('dispatchMessage is a public method the webhook can call', () => {
    assert.equal(typeof alerter().dispatchMessage, 'function',
      'server.mjs /webhook routes through this; renaming it re-forks the transports');
  });

  it('a public-channel keyword is served through dispatchMessage (the webhook path)', async () => {
    // Pre-R-F3617 the webhook called _handleMessage, which drops a non-allow-listed
    // sender outright - so this reply would never have been answered.
    const r = await alerter().dispatchMessage(message(PUBLIC_CHANNEL, 'HELP'));
    assert.equal(r.handled, true);
    assert.equal(r.via, 'channel_keyword');
    assert.equal(sent.length, 1);
  });

  it('an operator command in the ADMIN group is still served', async () => {
    const r = await alerter().dispatchMessage(message(ADMIN, '/status', { userId: '592471775', type: 'group' }));
    assert.equal(r.handled, true);
    assert.equal(r.via, 'operator_command');
    assert.match(String(sent[0]?.text), /ARIA ONLINE/);
  });

  it('a public member still cannot run an operator command over EITHER transport', async () => {
    const a = alerter();
    let swept = false;
    a.triggerManualSweep = async () => { swept = true; return 'swept'; };

    const r = await a.dispatchMessage(message(PUBLIC_CHANNEL, '/sweep'));

    assert.equal(swept, false, 'the webhook must not become the weaker door');
    assert.equal(r.handled, false);
    assert.equal(sent.length, 0);
  });

  it('the reason distinguishes "declined by the router" from "unknown sender"', async () => {
    // These are different operational facts and must not collapse into one: the first
    // is a correctly-configured surface ignoring chatter, the second is a destination
    // nobody is listening on. Conflating them is what made the dead surface invisible.
    const a = alerter();
    const chatter = await a.dispatchMessage(message(PUBLIC_CHANNEL, 'nice post'));
    const stranger = await a.dispatchMessage(message('-100777', 'nice post'));

    assert.equal(chatter.reason, 'declined_by_keyword_router');
    assert.equal(stranger.reason, 'sender_not_allowed');
  });

  it('a message with no text is a no-op, not a throw', async () => {
    const r = await alerter().dispatchMessage({ chat: { id: Number(PUBLIC_CHANNEL) }, from: { id: 1 } });
    assert.equal(r.handled, false);
    assert.equal(r.reason, 'no_text');
  });
});

describe('R-F3617 — the webhook no longer calls _handleMessage directly', () => {
  it('server.mjs routes /webhook through dispatchMessage', async () => {
    const fs = await import('node:fs');
    const src = fs.readFileSync(new URL('../server.mjs', import.meta.url), 'utf8');
    const webhookBlock = src.slice(src.indexOf("app.post('/webhook'"), src.indexOf("app.get('/webhook'"));
    assert.ok(webhookBlock.length > 0, 'the /webhook handler should be findable');
    assert.match(webhookBlock, /telegramAlerter\.dispatchMessage\(/,
      'the webhook must use the shared dispatcher');
    assert.doesNotMatch(webhookBlock, /telegramAlerter\._handleMessage\(/,
      'calling _handleMessage directly re-forks the transports and skips the public keyword path');
  });
});

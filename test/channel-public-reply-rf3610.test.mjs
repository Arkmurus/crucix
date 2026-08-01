// test/channel-public-reply-rf3610.test.mjs
//
// R-F3610 — capability test: a subscriber replying in the PUBLIC channel gets served.
//
// THE DEFECT THIS PINS
// --------------------
// Every Golden Intel post ends with a reply call-to-action. `_handleChannelKeyword`
// accepted only a SEPARATE linked discussion group (`TELEGRAM_CHANNEL_DISCUSSION_ID`,
// read straight from env) or a private DM. That env var is not set in production, and
// this deployment has no linked discussion group — Golden Intel goes to a SUPERGROUP
// (getChat: type supergroup, can_send_messages true, no linked_chat_id) and members
// reply there. Those messages matched neither branch and `_pollUpdates` dropped them
// at a bare `continue`: no reply, no log, no outcome record.
//
// The root was one level up: `TelegramAlerter`'s constructor destructured only
// { botToken, chatId, port }, so the class never knew its own public channel id —
// even though server.mjs already passes `config.telegram`, which has carried
// `channelId` and `channelDiscussionId` all along.
//
// These tests drive `_pollUpdates` — the live path (getWebhookInfo confirms no
// webhook is set in production, so polling is authoritative).

import { afterEach, beforeEach, describe, it } from 'node:test';
import assert from 'node:assert/strict';

const { TelegramAlerter } = await import('../lib/alerts/telegram.mjs');

const ADMIN_CHAT = '-5280434891';        // private ops group (TELEGRAM_CHAT_ID)
const PUBLIC_CHANNEL = '-1003836086295'; // public Golden Intel supergroup (TELEGRAM_CHANNEL_ID)
const SUBSCRIBER = '99887766';           // a member who is NOT the operator

let updates = [];
let sent = [];
const originalFetch = global.fetch;

function mockFetch() {
  global.fetch = async (url, opts = {}) => {
    const u = String(url);
    if (u.includes('/getUpdates')) {
      const batch = updates;
      updates = [];   // deliver once, like Telegram's offset semantics
      return new Response(JSON.stringify({ ok: true, result: batch }), { status: 200 });
    }
    if (u.includes('/sendMessage')) {
      const body = JSON.parse(String(opts.body || '{}'));
      sent.push(body);
      return new Response(JSON.stringify({ ok: true, result: { message_id: sent.length } }), { status: 200 });
    }
    // aria-intel: the sanctions screen behind the SCREEN keyword.
    if (u.includes('/sanctions/') || u.includes('/compliance/') || u.includes('/screen')) {
      return new Response(JSON.stringify({
        screened: true, blocked: true,
        matches: [{ name: 'EXAMPLE TRADING LLC', list: 'eu_consolidated' }],
      }), { status: 200 });
    }
    // brain signal (§25 proprioception) and anything else — never fail the send.
    return new Response(JSON.stringify({ ok: true, result: {} }), { status: 200 });
  };
}

function alerter(overrides = {}) {
  // Exactly the shape server.mjs passes: `new TelegramAlerter(config.telegram)`.
  return new TelegramAlerter({
    botToken: 'test-token',
    chatId: ADMIN_CHAT,
    channelId: PUBLIC_CHANNEL,
    channelDiscussionId: null,
    ...overrides,
  });
}

function message(chatId, text, { userId = SUBSCRIBER, type = 'supergroup', updateId } = {}) {
  return {
    update_id: updateId ?? Math.floor(Math.random() * 1e9),
    message: {
      message_id: 42,
      chat: { id: Number(chatId), type },
      from: { id: Number(userId), is_bot: false },
      text,
    },
  };
}

beforeEach(() => { updates = []; sent = []; mockFetch(); });
afterEach(() => { global.fetch = originalFetch; });

describe('R-F3610 — the public channel is a reply surface', () => {
  it('the alerter knows its own public channel id', () => {
    const a = alerter();
    assert.equal(a.channelId, PUBLIC_CHANNEL,
      'without this the gate cannot recognise the channel the bot posts to');
    assert.equal(a.chatId, ADMIN_CHAT, 'the private ops chat is unchanged');
  });

  it('SCREEN from a subscriber IN THE PUBLIC SUPERGROUP is answered', async () => {
    const a = alerter();
    updates = [message(PUBLIC_CHANNEL, 'SCREEN Example Trading LLC')];
    await a._pollUpdates();

    assert.equal(sent.length, 1,
      'the reply CTA printed on every Golden Intel post must actually be served');
    assert.equal(String(sent[0].chat_id), PUBLIC_CHANNEL, 'the answer goes back to the channel');
    assert.match(String(sent[0].text), /Example Trading LLC/i);
  });

  it('a country keyword from the public supergroup is answered', async () => {
    const a = alerter();
    updates = [message(PUBLIC_CHANNEL, 'ARIA Ukraine')];
    await a._pollUpdates();

    assert.equal(sent.length, 1);
    assert.match(String(sent[0].text), /Ukraine/i);
  });

  it('a DM still works — the pre-existing surface must not regress', async () => {
    const a = alerter();
    updates = [message(SUBSCRIBER, 'HELP', { type: 'private' })];
    await a._pollUpdates();

    assert.equal(sent.length, 1);
  });

  it('a separate linked discussion group still works when configured', async () => {
    const DISCUSSION = '-1009999999999';
    const a = alerter({ channelDiscussionId: DISCUSSION });
    updates = [message(DISCUSSION, 'HELP')];
    await a._pollUpdates();

    assert.equal(sent.length, 1, 'the broadcast-channel-with-linked-group topology is still supported');
  });
});

describe('R-F3610 — opening the public surface must not widen privilege', () => {
  it('an operator slash command from the public supergroup is NOT executed', async () => {
    const a = alerter();
    let swept = false;
    a.triggerManualSweep = async () => { swept = true; return 'swept'; };

    updates = [message(PUBLIC_CHANNEL, '/sweep')];
    await a._pollUpdates();

    assert.equal(swept, false, 'a public member must never be able to run operator commands');
    assert.equal(sent.length, 0, 'and must get no command output');
  });

  it('ordinary chatter in the public supergroup is ignored, not answered', async () => {
    const a = alerter();
    updates = [message(PUBLIC_CHANNEL, 'nice post, thanks for sharing')];
    await a._pollUpdates();

    assert.equal(sent.length, 0, 'the bot must not spam the channel on every message');
  });

  it('a message from another bot is ignored (no echo loop on the public surface)', async () => {
    const a = alerter();
    const upd = message(PUBLIC_CHANNEL, 'HELP');
    upd.message.from.is_bot = true;
    updates = [upd];
    await a._pollUpdates();

    assert.equal(sent.length, 0);
  });

  it('the private ADMIN group still runs operator commands', async () => {
    const a = alerter();
    updates = [message(ADMIN_CHAT, '/status', { userId: '592471775', type: 'group' })];
    await a._pollUpdates();

    assert.equal(sent.length, 1, 'the ops path must be untouched');
    assert.match(String(sent[0].text), /ARIA ONLINE/);
  });
});

describe('R-F3610 — an unrecognised destination is no longer silent', () => {
  it('a drop from an unknown chat is logged once, naming the id and the fix', async () => {
    const a = alerter();
    const warnings = [];
    const originalWarn = console.warn;
    console.warn = (...args) => warnings.push(args.join(' '));
    try {
      updates = [message('-100777', 'HELP', { updateId: 1 })];
      await a._pollUpdates();
      updates = [message('-100777', 'HELP', { updateId: 2 })];
      await a._pollUpdates();
    } finally { console.warn = originalWarn; }

    const drops = warnings.filter(w => w.includes('dropping messages from chat=-100777'));
    assert.equal(drops.length, 1, 'log once per chat — discoverable without spamming');
    assert.match(drops[0], /TELEGRAM_CHANNEL_ID/,
      'the log must name what an operator has to set; a bare drop is how this defect hid');
  });

  it('a declined message from a RECOGNISED surface is not blamed on the config', async () => {
    // The first version of this warning re-derived "is this a known chat?" and got
    // it wrong: it told the operator a message from the public channel was "not the
    // public channel (<that exact id>)". A log that names a wrong cause is worse
    // than no log — it sends someone to change a setting that is already correct.
    const a = alerter();
    const warnings = [];
    const originalWarn = console.warn;
    console.warn = (...args) => warnings.push(args.join(' '));
    try {
      updates = [message(PUBLIC_CHANNEL, 'just chatting', { updateId: 11 })];
      await a._pollUpdates();
    } finally { console.warn = originalWarn; }

    assert.equal(warnings.filter(w => w.includes('dropping messages from chat=')).length, 0,
      'ordinary chatter on a correctly-configured channel must not warn about the channel id');
  });
});

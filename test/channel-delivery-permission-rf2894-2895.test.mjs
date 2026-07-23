// R-F2894/R-F2895/R-F2893/R-F2896 — Telegram Golden Intel delivery integrity.
//
// BEHAVIOURAL tests: they call the real exported functions with a stubbed transport.
// A source-grep assertion proves SHAPE, not BEHAVIOUR — and the defect these lock
// down was precisely a component that looked correct and behaved wrongly:
// validatePublicChannelDestination returned ok for four days while every send died
// with HTTP 403, because getChat proves a channel EXISTS, not that we may POST.
//
// Live evidence (2026-07-23, /data/channel_outcomes.json on aria-web):
//   last delivered 2026-07-18T16:00Z, then 6 consecutive failures, operator never told.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, readFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const TMP = mkdtempSync(join(tmpdir(), 'aria-chan-'));
process.env.CHANNEL_OUTCOME_LEDGER_PATH = join(TMP, 'outcomes.json');
process.env.CHANNEL_ESCALATION_STATE_PATH = join(TMP, 'escalation.json');
process.env.TELEGRAM_CHAT_ID = '-100999';

const hooks = await import('../lib/telegram/channelServerHooks.mjs');

const BOT = { botToken: 'T', channelId: '-1002311460199', chatId: '-100999' };

function stubFetch(handler) {
  const original = globalThis.fetch;
  globalThis.fetch = async (url, opts) => handler(String(url), opts);
  return () => { globalThis.fetch = original; };
}

const jsonRes = (body, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
});

// ── R-F2894 ────────────────────────────────────────────────────────────────

test('R-F2898: "member list is inaccessible" is UNDETERMINED — it must NOT block', async () => {
  // R-F2894 originally treated this as proof the bot was not an admin. It is an
  // inference from the absence of an admin-only capability, and live 2026-07-23 the
  // operator confirmed the bot WAS a channel admin while Telegram kept returning it.
  // Blocking on it would suppress a working channel and raise a false outage alert.
  const restore = stubFetch(async (url) => {
    if (url.includes('/getChat?')) {
      return jsonRes({ ok: true, result: { id: -1002311460199, type: 'channel', title: 'Aria Intelligence ($ARIA)' } });
    }
    if (url.includes('/getMe')) return jsonRes({ ok: true, result: { id: 8776095808 } });
    if (url.includes('/getChatMember')) {
      return jsonRes({ ok: false, error_code: 400, description: 'Bad Request: member list is inaccessible' }, 400);
    }
    throw new Error(`unexpected call: ${url}`);
  });
  try {
    const res = await hooks.validatePublicChannelDestination(BOT);
    assert.equal(res.ok, true, 'an ambiguous probe result blocked a publishable channel');
  } finally { restore(); }
});

test('R-F2894: an UNAMBIGUOUS negative (bot kicked) is still caught before the send', async () => {
  const restore = stubFetch(async (url) => {
    if (url.includes('/getChat?')) return jsonRes({ ok: true, result: { id: -100, type: 'channel', title: 'C' } });
    if (url.includes('/getMe')) return jsonRes({ ok: true, result: { id: 42 } });
    if (url.includes('/getChatMember')) return jsonRes({ ok: true, result: { status: 'kicked' } });
    throw new Error(`unexpected call: ${url}`);
  });
  try {
    const res = await hooks.validatePublicChannelDestination(BOT);
    assert.equal(res.reason, 'bot_cannot_post_to_channel');
    assert.match(String(res.detail), /kicked/i);
  } finally { restore(); }
});

test('R-F2894: an administrator bot passes', async () => {
  const restore = stubFetch(async (url) => {
    if (url.includes('/getChat?')) return jsonRes({ ok: true, result: { id: -100, type: 'channel', title: 'C' } });
    if (url.includes('/getMe')) return jsonRes({ ok: true, result: { id: 42 } });
    if (url.includes('/getChatMember')) {
      return jsonRes({ ok: true, result: { status: 'administrator', can_post_messages: true } });
    }
    throw new Error(`unexpected call: ${url}`);
  });
  try {
    assert.equal((await hooks.validatePublicChannelDestination(BOT)).ok, true);
  } finally { restore(); }
});

test('R-F2894: an admin WITHOUT can_post_messages is blocked', async () => {
  const restore = stubFetch(async (url) => {
    if (url.includes('/getChat?')) return jsonRes({ ok: true, result: { id: -100, type: 'channel', title: 'C' } });
    if (url.includes('/getMe')) return jsonRes({ ok: true, result: { id: 42 } });
    if (url.includes('/getChatMember')) {
      return jsonRes({ ok: true, result: { status: 'administrator', can_post_messages: false } });
    }
    throw new Error(`unexpected call: ${url}`);
  });
  try {
    const res = await hooks.validatePublicChannelDestination(BOT);
    assert.equal(res.reason, 'bot_cannot_post_to_channel');
  } finally { restore(); }
});

test('R-F2894: the probe FAILS OPEN — "could not measure" is not "measured and failed"', async () => {
  // An unrelated Telegram outage must not suppress a publishable post. The send
  // itself stays the final authority.
  const restore = stubFetch(async (url) => {
    if (url.includes('/getChat?')) return jsonRes({ ok: true, result: { id: -100, type: 'channel', title: 'C' } });
    if (url.includes('/getMe')) return jsonRes({ ok: false, description: 'gateway timeout' }, 504);
    throw new Error(`unexpected call: ${url}`);
  });
  try {
    assert.equal((await hooks.validatePublicChannelDestination(BOT)).ok, true);
  } finally { restore(); }
});

// ── R-F2893 ────────────────────────────────────────────────────────────────

test('R-F2893: the channel asks the SERVER for the grades it can publish', async () => {
  let seen = '';
  const restore = stubFetch(async (url) => {
    seen = url;
    return jsonRes({ signals: [], freshness: {} });
  });
  try {
    await hooks.fetchGoldenIntelSignals({ limit: 60, grades: 'A' });
    assert.match(seen, /[?&]limit=60/);
    assert.match(seen, /[?&]grades=A/);
  } finally { restore(); }
});

// ── R-F2896 ────────────────────────────────────────────────────────────────

const GRADE_A = {
  id: 'a1', intel_grade: 'A', signal_type: 'active_tender', confidence: 'MEDIUM',
  title: 'Germany - Fighter aircraft - procurement notice', why_it_matters: 'why',
  recommended_action: 'Assess bid/no-bid', url: 'https://ted.europa.eu/en/notice/1',
  detected_at: '2026-07-23T06:02:55Z', score: 60,
};

test('R-F2896: ambient source-health noise does not suppress a fresh candidate', () => {
  // The live 2026-07-23 state: the ONLY stale reason was source_failure_degraded.
  const picked = hooks.selectTelegramGoldenIntel({
    ok: true,
    signals: [GRADE_A],
    freshness: {
      stale: true,
      stale_reasons: ['source_failure_degraded'],
      blocking_stale_reasons: [],
      publishable: true,
    },
  });
  assert.ok(picked, 'a fresh Grade A was suppressed by unrelated feed failures');
  assert.equal(picked.id, 'a1');
});

test('R-F2896: genuine staleness still blocks — the gate can say NO', () => {
  const picked = hooks.selectTelegramGoldenIntel({
    ok: true,
    signals: [GRADE_A],
    freshness: {
      stale: true,
      stale_reasons: ['signals_stale', 'source_failure_degraded'],
      blocking_stale_reasons: ['signals_stale'],
      publishable: false,
    },
  });
  assert.equal(picked, null);
});

test('R-F2896: falls back to the local rule when the backend omits the field', () => {
  const picked = hooks.selectTelegramGoldenIntel({
    ok: true,
    signals: [GRADE_A],
    freshness: { stale: true, stale_reasons: ['source_failure_degraded'] },
  });
  assert.ok(picked, 'older-backend fallback changed behaviour');
});

// ── R-F2895 ────────────────────────────────────────────────────────────────

test('R-F2895: an unambiguous permission block escalates to the PRIVATE chat', async () => {
  writeFileSync(process.env.CHANNEL_ESCALATION_STATE_PATH, '{}');
  writeFileSync(process.env.CHANNEL_OUTCOME_LEDGER_PATH, '[]');

  const sends = [];
  const restore = stubFetch(async (url, opts) => {
    if (url.includes('/getChat?')) return jsonRes({ ok: true, result: { id: -100, type: 'channel', title: 'C' } });
    if (url.includes('/getMe')) return jsonRes({ ok: true, result: { id: 42 } });
    // R-F2898 — an unambiguous negative, not the ambiguous "member list" reply.
    if (url.includes('/getChatMember')) return jsonRes({ ok: true, result: { status: 'left' } });
    if (url.includes('/sendMessage')) {
      sends.push(JSON.parse(String(opts?.body || '{}')));
      return jsonRes({ ok: true, result: { message_id: 1 } });
    }
    return jsonRes({ ok: true });
  });

  try {
    await hooks.handleMorningSignalCron({}, BOT, { hour: 7 });
  } finally { restore(); }

  assert.equal(sends.length, 1, 'the operator was not notified of a publishing outage');
  // It must go to the PRIVATE admin chat, never the public channel.
  assert.equal(String(sends[0].chat_id), '-100999');
  assert.match(sends[0].text, /BLOCKED/);
  assert.match(sends[0].text, /ACTION NEEDED/i);

  // And the outage is recorded durably, so it survives a brain outage.
  assert.ok(existsSync(process.env.CHANNEL_OUTCOME_LEDGER_PATH));
  const ledger = JSON.parse(readFileSync(process.env.CHANNEL_OUTCOME_LEDGER_PATH, 'utf8'));
  const last = ledger[ledger.length - 1];
  assert.equal(last.outcome, 'failed');
  assert.match(String(last.detail), /bot_cannot_post_to_channel/);
});

test('R-F2895: escalation is rate-limited so an outage nags, not floods', async () => {
  // Second consecutive run with the same fault must NOT send again inside cooldown.
  const sends = [];
  const restore = stubFetch(async (url, opts) => {
    if (url.includes('/getChat?')) return jsonRes({ ok: true, result: { id: -100, type: 'channel', title: 'C' } });
    if (url.includes('/getMe')) return jsonRes({ ok: true, result: { id: 42 } });
    if (url.includes('/getChatMember')) return jsonRes({ ok: true, result: { status: 'left' } });
    if (url.includes('/sendMessage')) {
      sends.push(JSON.parse(String(opts?.body || '{}')));
      return jsonRes({ ok: true, result: { message_id: 2 } });
    }
    return jsonRes({ ok: true });
  });
  try {
    await hooks.handleMorningSignalCron({}, BOT, { hour: 7 });
  } finally { restore(); }
  assert.equal(sends.length, 0, 'escalation flooded the operator inside the cooldown');
});

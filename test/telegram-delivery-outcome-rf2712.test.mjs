// R-F2712 — §25a: the interactive Telegram chat must report its delivery outcome.
//
// Symptom: telegramCommands.mjs `ariaChatProxy` → `sendTelegramMessage`/`sendLongMessage`
// only console.error'd a failed send — ZERO outcome reached the brain, so a Telegram
// API reject / network drop was INVISIBLE to the §25 self-heal loop (the TG limb was DARK).
//
// This drives the REAL handler (handleTelegramWebhook → the /aria ask path) with a mocked
// global.fetch and asserts a "tg" delivery outcome is POSTed to /api/aria/outcome — on
// success (delivered_real_answer) AND on a Telegram send failure (send_failed).

import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

// env MUST be set before the module is imported (it reads these at load time)
process.env.TELEGRAM_BOT_TOKEN = 'test:token';
process.env.BRAIN_SERVICE_URL  = 'http://brain.test';
process.env.ARIA_SERVICE_URL   = 'http://brain.test';

const { sendTelegramMessage, reportTgOutcome, handleTelegramWebhook } =
  await import('../lib/telegram/telegramCommands.mjs');

const __dirname = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(join(__dirname, '..', 'lib', 'telegram', 'telegramCommands.mjs'), 'utf-8');
const tick = () => new Promise((r) => setTimeout(r, 25));

let _origFetch;
beforeEach(() => { _origFetch = global.fetch; });

describe('R-F2712 Telegram delivery-outcome (§25a)', () => {
  it('sendTelegramMessage returns a success boolean (was silent void)', async () => {
    global.fetch = async () => ({ ok: true, json: async () => ({}) });
    assert.equal(await sendTelegramMessage(1, 'hi'), true);
    global.fetch = async () => ({ ok: false, json: async () => ({ description: 'chat not found' }) });
    assert.equal(await sendTelegramMessage(1, 'hi'), false);
    global.fetch = async () => { throw new Error('network'); };
    assert.equal(await sendTelegramMessage(1, 'hi'), false);
    global.fetch = _origFetch;
  });

  it('reportTgOutcome POSTs surface=tg with a valid outcome to /api/aria/outcome', async () => {
    let captured = null;
    global.fetch = async (url, opts) => {
      if (String(url).includes('/api/aria/outcome')) captured = { url: String(url), body: JSON.parse(opts.body) };
      return { ok: true, json: async () => ({}) };
    };
    reportTgOutcome('tg_1_123', 'chat_response', 'delivered_real_answer', 50, '');
    await tick();
    assert.ok(captured, 'an outcome POST was made');
    assert.ok(captured.url.endsWith('/api/aria/outcome'));
    assert.equal(captured.body.surface, 'tg');
    assert.equal(captured.body.request_id, 'tg_1_123');
    assert.equal(captured.body.intended_result, 'chat_response');
    assert.ok(['delivered_real_answer', 'timeout_fallback', 'error', 'send_failed'].includes(captured.body.actual_outcome));
    global.fetch = _origFetch;
  });

  it('END-TO-END: /aria ask reports delivered_real_answer when the send succeeds', async () => {
    let outcome = null;
    global.fetch = async (url, opts) => {
      const u = String(url);
      if (u.includes('/api/aria/chat'))    return { ok: true, json: async () => ({ response: 'a real answer' }) };
      if (u.includes('/api/aria/outcome')) { outcome = JSON.parse(opts.body); return { ok: true, json: async () => ({}) }; }
      return { ok: true, json: async () => ({}) };   // Telegram sendMessage OK
    };
    const req = { body: { message: { chat: { id: 42 }, from: { id: 7 }, text: '/aria ask: hello there' } } };
    await handleTelegramWebhook(req, { sendStatus() {} });
    await tick();
    assert.ok(outcome, 'the interactive TG chat reported a delivery outcome');
    assert.equal(outcome.surface, 'tg');
    assert.equal(outcome.actual_outcome, 'delivered_real_answer');
    assert.match(outcome.request_id, /^tg_42_/);
    global.fetch = _origFetch;
  });

  it('END-TO-END: /aria ask reports send_failed when Telegram rejects the send', async () => {
    let outcome = null;
    global.fetch = async (url, opts) => {
      const u = String(url);
      if (u.includes('/api/aria/chat'))    return { ok: true, json: async () => ({ response: 'a real answer' }) };
      if (u.includes('/api/aria/outcome')) { outcome = JSON.parse(opts.body); return { ok: true, json: async () => ({}) }; }
      return { ok: false, json: async () => ({ description: 'bot was blocked by the user' }) };  // Telegram send FAILS
    };
    const req = { body: { message: { chat: { id: 99 }, from: { id: 7 }, text: '/aria ask: hello' } } };
    await handleTelegramWebhook(req, { sendStatus() {} });
    await tick();
    assert.ok(outcome, 'a delivery outcome was reported even though the send failed');
    assert.equal(outcome.surface, 'tg');
    assert.equal(outcome.actual_outcome, 'send_failed');
    global.fetch = _origFetch;
  });

  it('STRUCTURAL: the interactive chat path wires the outcome (not just console.error)', () => {
    assert.match(SRC, /reportTgOutcome\(/, 'reportTgOutcome must be called');
    assert.match(SRC, /classifyDeliveryOutcome\(data\)/, 'the ask path classifies the brain result');
    assert.match(SRC, /surface: 'tg'/, 'reports on the tg surface');
  });
});

// test/telegram-hardening-rf1798.test.mjs
//
// Capability test for R-F1798 — Telegram hardening (aria-web audit #7/#8):
//   #7 per-user, per-command rate limit (LLM commands throttled harder)
//   #8 /aria input cap + control-char sanitization before it reaches the LLM
//
// Run: node test/telegram-hardening-rf1798.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { checkTelegramRateLimit } from '../lib/alerts/telegram.mjs';
import { capAriaInput, MAX_ARIA_INPUT } from '../lib/telegram/telegramCommands.mjs';

// ── #7 rate limiting ────────────────────────────────────────────────────────
test('rate limit: LLM command blocked within the heavy (8s) window, allowed after', () => {
  const m = new Map();
  const t0 = 1_000_000;
  assert.equal(checkTelegramRateLimit(m, 'u1', '/aria', t0), false);          // first allowed
  assert.equal(checkTelegramRateLimit(m, 'u1', '/aria', t0 + 1000), true);    // 1s later — blocked
  assert.equal(checkTelegramRateLimit(m, 'u1', '/aria', t0 + 9000), false);   // 9s later — allowed
});

test('rate limit: isolated per-user and per-command', () => {
  const m = new Map();
  const t = 5_000_000;
  assert.equal(checkTelegramRateLimit(m, 'u1', '/aria', t), false);
  assert.equal(checkTelegramRateLimit(m, 'u2', '/aria', t), false);   // other user not affected
  assert.equal(checkTelegramRateLimit(m, 'u1', '/status', t), false); // other command not affected
});

test('rate limit: light command uses the short (1.5s) window', () => {
  const m = new Map();
  const t = 9_000_000;
  assert.equal(checkTelegramRateLimit(m, 'u1', '/status', t), false);
  assert.equal(checkTelegramRateLimit(m, 'u1', '/status', t + 1000), true);  // within 1.5s
  assert.equal(checkTelegramRateLimit(m, 'u1', '/status', t + 2000), false); // after 1.5s
});

// ── #8 input cap + sanitization ─────────────────────────────────────────────
test('capAriaInput: rejects over-long input', () => {
  const r = capAriaInput('a'.repeat(MAX_ARIA_INPUT + 1));
  assert.equal(r.ok, false);
  assert.match(r.message, /too long/i);
});

test('capAriaInput: accepts and trims normal input', () => {
  const r = capAriaInput('  who owns Acme Corp?  ');
  assert.equal(r.ok, true);
  assert.equal(r.value, 'who owns Acme Corp?');
});

test('capAriaInput: strips ASCII control characters', () => {
  const dirty = 'hel' + String.fromCharCode(0) + 'lo' + String.fromCharCode(7);
  const r = capAriaInput(dirty);
  assert.equal(r.ok, true);
  assert.equal(r.value, 'hello');
});

test('capAriaInput: non-string input is safe', () => {
  assert.equal(capAriaInput(undefined).ok, true);
  assert.equal(capAriaInput(undefined).value, '');
});

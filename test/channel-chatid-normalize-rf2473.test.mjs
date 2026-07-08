// R-F2473 — _channelChatId must normalize every accepted TELEGRAM_CHAT_ID shape
// to Telegram's canonical -100<internal> form. Verified live via getChat:
//   -1003836086295 => ok:true, title @ARIAIntelligence  (the reachable chat)
//   -1001003836086295 / 1003836086295 => "chat not found"
// R-F2319 blanket-prefixed '-100' and corrupted the abs form (1003836086295)
// into -1001003836086295, taking the whole channel dark. This locks the contract.
//
// Run: node --test test/channel-chatid-normalize-rf2473.test.mjs

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { _channelChatId } from '../lib/telegram/channelServerHooks.mjs';

const REACHABLE = '-1003836086295';

test('abs value with dropped minus -> canonical (the live bug)', () => {
  assert.equal(_channelChatId({ chatId: '1003836086295' }), REACHABLE);
  assert.equal(_channelChatId({ channelId: '1003836086295' }), REACHABLE);
});

test('already-canonical signed id is left untouched', () => {
  assert.equal(_channelChatId({ chatId: '-1003836086295' }), REACHABLE);
});

test('bare internal id gets the full -100 prefix', () => {
  assert.equal(_channelChatId({ chatId: '3836086295' }), REACHABLE);
});

test('does NOT double-prefix an abs id (the regression guard)', () => {
  assert.notEqual(_channelChatId({ chatId: '1003836086295' }), '-1001003836086295');
});

test('@username handle passes through', () => {
  assert.equal(_channelChatId({ chatId: '@ARIAIntelligence' }), '@ARIAIntelligence');
});

test('channelId takes precedence over chatId', () => {
  assert.equal(_channelChatId({ channelId: '1003836086295', chatId: '999' }), REACHABLE);
});

test('empty / missing -> empty string (no crash)', () => {
  assert.equal(_channelChatId({}), '');
  assert.equal(_channelChatId(null), '');
});

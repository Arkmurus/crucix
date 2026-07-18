import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';

const dir = mkdtempSync(join(tmpdir(), 'aria-network-'));
process.env.ARIA_MESSAGES_FILE = join(dir, 'messages.json');
const messages = await import(`../lib/messages.mjs?rf2732=${Date.now()}`);

test('R-F2732: group lifecycle stores once, lists immediately, and fans history to members', () => {
  const group = messages.createGroup('owner', 'Operations', ['analyst', 'legal']);
  assert.equal(group.members.length, 3);
  assert.equal(messages.getConversationSummaries('legal')[0].name, 'Operations');
  const sent = messages.storeConversationMessage(group.id, 'analyst', 'Signal confirmed', 'client-1');
  const replay = messages.storeConversationMessage(group.id, 'analyst', 'Signal confirmed', 'client-1');
  assert.equal(replay.id, sent.id, 'client id makes a retried send idempotent');
  assert.equal(messages.getConversationById(group.id, 'owner').messages.length, 1);
});

test('R-F2732: non-members cannot read, write, type through, or mark a group read', () => {
  const group = messages.createGroup('a', 'Restricted', ['b', 'c']);
  assert.equal(messages.getConversationById(group.id, 'intruder'), null);
  assert.throws(() => messages.storeConversationMessage(group.id, 'intruder', 'exfiltrate'));
  assert.equal(messages.markConversationRead('intruder', group.id), false);
});

test('R-F2732: exact membership prevents substring conversation disclosure', () => {
  messages.storeMessage('user-123', 'peer', 'classified');
  assert.deepEqual(messages.getConversationSummaries('user-12'), []);
  assert.equal(messages.unreadCount('user-12'), 0);
});

test('R-F2732: unread and read receipts are per group member', () => {
  const group = messages.createGroup('lead', 'Read state', ['one', 'two']);
  messages.storeConversationMessage(group.id, 'lead', 'briefing');
  assert.equal(messages.getConversationSummaries('one')[0].unread, 1);
  assert.equal(messages.markConversationRead('one', group.id), true);
  assert.equal(messages.getConversationSummaries('one')[0].unread, 0);
  assert.equal(messages.getConversationSummaries('two')[0].unread, 1);
});

test('R-F2732: malformed storage fails closed instead of erasing history', () => {
  writeFileSync(process.env.ARIA_MESSAGES_FILE, '{broken', 'utf8');
  assert.throws(() => messages.getConversationSummaries('lead'), /unreadable/);
  assert.equal(readFileSync(process.env.ARIA_MESSAGES_FILE, 'utf8'), '{broken');
});

test('R-F2732: production socket and UI expose acknowledged group wiring', () => {
  const server = readFileSync(new URL('../server.mjs', import.meta.url), 'utf8');
  const ui = readFileSync(new URL('../public/js/network.js', import.meta.url), 'utf8');
  assert.match(server, /socket\.on\('send_message',[\s\S]{0,160}ack/);
  assert.match(server, /getConversationById\(conversationId, uid/);
  assert.match(server, /user\.networkVisible/);
  assert.match(server, /conversation_created/);
  const chatBlock = server.slice(server.indexOf('// ── Chat REST API'), server.indexOf('// ── R-F2349'));
  assert.equal((chatBlock.match(/if \(!req\.user\?\.userId\) return res\.status\(401\)/g) || []).length, 6,
    'every chat REST route fails closed when the localhost auth bypass has no user');
  assert.match(ui, /\/api\/chat\/groups/);
  assert.match(ui, /conversationId: activeId/);
  assert.match(ui, /result\?\.ok/);
});

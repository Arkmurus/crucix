// lib/messages.mjs — crash-safe direct and group conversation store (R-F2732)

import { readFileSync, writeFileSync, mkdirSync, existsSync, renameSync, copyFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { randomBytes } from 'node:crypto';

const __dirname = dirname(fileURLToPath(import.meta.url));
const RUNS_DIR = join(__dirname, '..', 'runs');
const PERSIST_DIR = process.env.PERSIST_DIR || RUNS_DIR;
const LEGACY_MESSAGES_FILE = join(RUNS_DIR, 'messages.json');
const MESSAGES_FILE = process.env.ARIA_MESSAGES_FILE || join(PERSIST_DIR, 'messages.json');

// Move existing DM history onto the same durable Fly volume as users.json.
if (MESSAGES_FILE !== LEGACY_MESSAGES_FILE && !existsSync(MESSAGES_FILE) && existsSync(LEGACY_MESSAGES_FILE)) {
  try { mkdirSync(dirname(MESSAGES_FILE), { recursive: true }); copyFileSync(LEGACY_MESSAGES_FILE, MESSAGES_FILE); }
  catch (error) { throw new Error(`Message store migration failed: ${error.message}`); }
}

function emptyStore() { return { version: 2, conversations: {}, groups: {} }; }
function ensureDir() { if (!existsSync(dirname(MESSAGES_FILE))) mkdirSync(dirname(MESSAGES_FILE), { recursive: true }); }
function directId(a, b) {
  const members = [String(a), String(b)].sort();
  return `dm:${Buffer.from(JSON.stringify(members)).toString('base64url')}`;
}
function normalise(raw) {
  const out = emptyStore();
  if (!raw || typeof raw !== 'object') return out;
  if (raw.version === 2) return { ...out, ...raw, conversations: raw.conversations || {}, groups: raw.groups || {} };
  for (const msgs of Object.values(raw.conversations || {})) {
    if (!Array.isArray(msgs) || !msgs.length) continue;
    const first = msgs[0];
    const id = directId(first.from, first.to);
    out.conversations[id] = { id, type: 'direct', members: [String(first.from), String(first.to)].sort(), messages: msgs };
  }
  return out;
}
function load() {
  ensureDir();
  if (!existsSync(MESSAGES_FILE)) return emptyStore();
  try { return normalise(JSON.parse(readFileSync(MESSAGES_FILE, 'utf8'))); }
  catch (error) { throw new Error(`Message store unreadable: ${error.message}`); }
}
function save(data) {
  ensureDir();
  const tmp = `${MESSAGES_FILE}.${process.pid}.${randomBytes(4).toString('hex')}.tmp`;
  writeFileSync(tmp, JSON.stringify(data, null, 2), { encoding: 'utf8', flag: 'wx' });
  renameSync(tmp, MESSAGES_FILE);
}
function mutate(fn) { const store = load(); const result = fn(store); save(store); return result; }
function memberOf(conversation, userId) { return !!conversation?.members?.includes(String(userId)); }

export function generateMsgId() { return randomBytes(12).toString('hex'); }
export function getDirectConversationId(a, b) { return directId(a, b); }

export function createGroup(ownerId, name, memberIds) {
  const cleanName = String(name || '').trim().slice(0, 80);
  if (!cleanName) throw new Error('Group name is required');
  const members = [...new Set([String(ownerId), ...(memberIds || []).map(String)])];
  if (members.length < 3) throw new Error('A group requires at least three members');
  if (members.length > 100) throw new Error('A group supports at most 100 members');
  return mutate(store => {
    const id = `grp:${generateMsgId()}`;
    const now = new Date().toISOString();
    const group = { id, type: 'group', name: cleanName, ownerId: String(ownerId), admins: [String(ownerId)], members, createdAt: now, updatedAt: now, messages: [] };
    store.conversations[id] = group;
    store.groups[id] = true;
    return structuredClone(group);
  });
}

export function getConversationById(conversationId, userId, limit = 100) {
  const conversation = load().conversations[String(conversationId)];
  if (!conversation || !memberOf(conversation, userId)) return null;
  return { ...structuredClone(conversation), messages: conversation.messages.slice(-Math.max(1, Math.min(Number(limit) || 100, 200))) };
}

export function storeConversationMessage(conversationId, fromId, text, clientId = null) {
  return mutate(store => {
    const conversation = store.conversations[String(conversationId)];
    if (!conversation || !memberOf(conversation, fromId)) throw new Error('Conversation not found');
    if (clientId) {
      const existing = conversation.messages.find(m => m.from === String(fromId) && m.clientId === String(clientId));
      if (existing) return structuredClone(existing);
    }
    const msg = { id: generateMsgId(), clientId: clientId ? String(clientId).slice(0, 80) : null, conversationId: conversation.id, from: String(fromId), text: String(text).trim().slice(0, 2000), ts: new Date().toISOString(), readBy: [String(fromId)] };
    if (conversation.type === 'direct') msg.to = conversation.members.find(id => id !== String(fromId));
    conversation.messages.push(msg);
    conversation.updatedAt = msg.ts;
    return structuredClone(msg);
  });
}

export function storeMessage(fromId, toId, text, clientId = null) {
  const id = directId(fromId, toId);
  return mutate(store => {
    if (!store.conversations[id]) store.conversations[id] = { id, type: 'direct', members: [String(fromId), String(toId)].sort(), messages: [], createdAt: new Date().toISOString() };
    const conversation = store.conversations[id];
    if (clientId) {
      const existing = conversation.messages.find(m => m.from === String(fromId) && m.clientId === String(clientId));
      if (existing) return structuredClone(existing);
    }
    const msg = { id: generateMsgId(), clientId: clientId ? String(clientId).slice(0, 80) : null, conversationId: id, from: String(fromId), to: String(toId), text: String(text).trim().slice(0, 2000), ts: new Date().toISOString(), readBy: [String(fromId)], read: false };
    conversation.messages.push(msg); conversation.updatedAt = msg.ts;
    return structuredClone(msg);
  });
}

export function getConversation(a, b, limit = 100) { return getConversationById(directId(a, b), a, limit)?.messages || []; }

export function markConversationRead(userId, conversationId) {
  return mutate(store => {
    const conversation = store.conversations[String(conversationId)];
    if (!conversation || !memberOf(conversation, userId)) return false;
    for (const message of conversation.messages) {
      if (!message.readBy) message.readBy = message.read ? [...conversation.members] : [message.from];
      if (!message.readBy.includes(String(userId))) message.readBy.push(String(userId));
      if (conversation.type === 'direct' && message.to === String(userId)) message.read = true;
    }
    return true;
  });
}
export function markRead(myId, otherId) { return markConversationRead(myId, directId(myId, otherId)); }

export function unreadCount(userId, fromId = null) {
  return getConversationSummaries(userId).reduce((sum, item) => sum + (!fromId || item.userId === String(fromId) ? item.unread : 0), 0);
}

export function getConversationSummaries(myId) {
  const uid = String(myId); const summaries = [];
  for (const conversation of Object.values(load().conversations)) {
    if (!memberOf(conversation, uid)) continue;
    if (!conversation.messages.length && conversation.type !== 'group') continue;
    const last = conversation.messages.at(-1) || { text: '', ts: conversation.createdAt, from: null };
    const unread = conversation.messages.filter(m => m.from !== uid && !(m.readBy || (m.read ? conversation.members : [m.from])).includes(uid)).length;
    summaries.push({ conversationId: conversation.id, type: conversation.type, userId: conversation.type === 'direct' ? conversation.members.find(id => id !== uid) : null, name: conversation.name || null, members: conversation.type === 'group' ? conversation.members : undefined, ownerId: conversation.ownerId, admins: conversation.admins, lastMessage: last, unread });
  }
  return summaries.sort((a, b) => new Date(b.lastMessage.ts || 0) - new Date(a.lastMessage.ts || 0));
}

export function listGroupsForUser(userId) {
  return Object.values(load().conversations).filter(c => c.type === 'group' && memberOf(c, userId)).map(c => ({ ...structuredClone(c), messages: undefined }));
}

// lib/messages.mjs
// Simple JSON-file message store for internal platform chat

import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { randomBytes } from 'node:crypto';

const __dirname = dirname(fileURLToPath(import.meta.url));
const RUNS_DIR = join(__dirname, '..', 'runs');
const MESSAGES_FILE = join(RUNS_DIR, 'messages.json');

function ensureDir() {
  if (!existsSync(RUNS_DIR)) mkdirSync(RUNS_DIR, { recursive: true });
}

function load() {
  ensureDir();
  if (!existsSync(MESSAGES_FILE)) return { conversations: {}, direct: [] };
  try {
    return JSON.parse(readFileSync(MESSAGES_FILE, 'utf8'));
  } catch {
    return { conversations: {}, direct: [] };
  }
}

function save(data) {
  ensureDir();
  writeFileSync(MESSAGES_FILE, JSON.stringify(data, null, 2));
}

function conversationKey(userIdA, userIdB) {
  return [userIdA, userIdB].sort().join(':');
}

export function generateMsgId() {
  return randomBytes(8).toString('hex');
}

/**
 * Store a direct message between two users.
 * Returns the stored message object.
 */
export function storeMessage(fromId, toId, text) {
  const store = load();
  const key = conversationKey(fromId, toId);
  if (!store.conversations[key]) store.conversations[key] = [];

  const msg = {
    id: generateMsgId(),
    from: fromId,
    to: toId,
    text: text.trim().slice(0, 2000), // cap at 2000 chars
    ts: new Date().toISOString(),
    read: false
  };

  store.conversations[key].push(msg);

  // Keep only last 500 messages per conversation
  if (store.conversations[key].length > 500) {
    store.conversations[key] = store.conversations[key].slice(-500);
  }

  save(store);
  return msg;
}

/**
 * Get conversation history between two users (newest last, up to `limit`).
 */
export function getConversation(userIdA, userIdB, limit = 100) {
  const store = load();
  const key = conversationKey(userIdA, userIdB);
  const msgs = store.conversations[key] || [];
  return msgs.slice(-limit);
}

/**
 * Mark all messages in a conversation as read for a given recipient.
 */
export function markRead(myId, otherId) {
  const store = load();
  const key = conversationKey(myId, otherId);
  if (!store.conversations[key]) return;
  store.conversations[key].forEach(m => {
    if (m.to === myId) m.read = true;
  });
  save(store);
}

/**
 * Count unread messages directed to `userId` from `fromId` (or all if fromId omitted).
 */
export function unreadCount(userId, fromId = null) {
  const store = load();
  let count = 0;
  for (const [key, msgs] of Object.entries(store.conversations)) {
    if (!key.includes(userId)) continue;
    for (const m of msgs) {
      if (m.to === userId && !m.read) {
        if (!fromId || m.from === fromId) count++;
      }
    }
  }
  return count;
}

/**
 * Get a summary of all conversations for a user:
 * [{ userId, lastMessage, unread }]
 */
export function getConversationSummaries(myId) {
  const store = load();
  const summaries = [];

  for (const [key, msgs] of Object.entries(store.conversations)) {
    if (!key.includes(myId)) continue;
    if (msgs.length === 0) continue;

    const parts = key.split(':');
    const otherId = parts[0] === myId ? parts[1] : parts[0];
    const last = msgs[msgs.length - 1];
    const unread = msgs.filter(m => m.to === myId && !m.read).length;

    summaries.push({ userId: otherId, lastMessage: last, unread });
  }

  summaries.sort((a, b) => new Date(b.lastMessage.ts) - new Date(a.lastMessage.ts));
  return summaries;
}

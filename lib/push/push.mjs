// lib/push/push.mjs
// Web Push notifications via VAPID — sends intel alerts to registered browser clients

import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, '..', '..');
const RUNS_DIR = join(ROOT, 'runs');
const VAPID_FILE = join(RUNS_DIR, 'vapid.json');
const SUBS_FILE  = join(RUNS_DIR, 'push-subscriptions.json');

const VAPID_SUBJECT = process.env.VAPID_SUBJECT || 'mailto:admin@arkmurus.com';

// Lazy-load web-push — graceful degradation if not installed
let webPush = null;
let webPushAvailable = false;

async function getWebPush() {
  if (webPush !== null) return webPush;
  try {
    webPush = (await import('web-push')).default;
    webPushAvailable = true;
    return webPush;
  } catch {
    console.warn('[Push] web-push package not installed — push notifications disabled');
    webPush = false;
    return null;
  }
}

// ── File helpers ──────────────────────────────────────────────────────────────

function ensureDir() {
  if (!existsSync(RUNS_DIR)) mkdirSync(RUNS_DIR, { recursive: true });
}

function loadVapid() {
  if (!existsSync(VAPID_FILE)) return null;
  try { return JSON.parse(readFileSync(VAPID_FILE, 'utf8')); } catch { return null; }
}

function saveVapid(keys) {
  ensureDir();
  writeFileSync(VAPID_FILE, JSON.stringify(keys, null, 2));
}

function loadSubs() {
  if (!existsSync(SUBS_FILE)) return {};
  try { return JSON.parse(readFileSync(SUBS_FILE, 'utf8')); } catch { return {}; }
}

function saveSubs(subs) {
  ensureDir();
  writeFileSync(SUBS_FILE, JSON.stringify(subs, null, 2));
}

// ── VAPID ─────────────────────────────────────────────────────────────────────

let vapidKeys = null;

export async function initVapid() {
  const wp = await getWebPush();
  if (!wp) return;

  vapidKeys = loadVapid();
  if (!vapidKeys) {
    vapidKeys = wp.generateVAPIDKeys();
    saveVapid(vapidKeys);
    console.log('[Push] Generated new VAPID keys');
  }

  wp.setVapidDetails(VAPID_SUBJECT, vapidKeys.publicKey, vapidKeys.privateKey);
  console.log('[Push] VAPID initialized');
}

export function getVapidPublicKey() {
  return vapidKeys?.publicKey || null;
}

// ── Subscriptions ─────────────────────────────────────────────────────────────

export function saveSubscription(userId, subscription, opts = {}) {
  const subs = loadSubs();
  subs[userId] = {
    subscription,
    userId,
    notifyDigest: opts.notifyDigest !== undefined ? opts.notifyDigest : true,
    notifyFlash:  opts.notifyFlash  !== undefined ? opts.notifyFlash  : true,
    createdAt: subs[userId]?.createdAt || new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  };
  saveSubs(subs);
}

export function removeSubscription(userId) {
  const subs = loadSubs();
  delete subs[userId];
  saveSubs(subs);
}

export function getSubscription(userId) {
  const subs = loadSubs();
  return subs[userId] || null;
}

// ── Push helpers ──────────────────────────────────────────────────────────────

export function pushPayload(title, body, url = '/', badge = '/assets/images/app/favicon-32x32.png', icon = '/assets/images/app/favicon-32x32.png') {
  return JSON.stringify({
    title,
    body,
    icon,
    badge,
    data: { url },
    actions: [
      { action: 'open',    title: 'View'    },
      { action: 'dismiss', title: 'Dismiss' },
    ],
  });
}

export async function pushToUser(userId, payload) {
  const wp = await getWebPush();
  if (!wp || !vapidKeys) return { sent: false, reason: 'Push not initialized' };

  const entry = getSubscription(userId);
  if (!entry) return { sent: false, reason: 'No subscription' };

  try {
    await wp.sendNotification(entry.subscription, payload);
    return { sent: true };
  } catch (err) {
    if (err.statusCode === 410 || err.statusCode === 404) {
      // Subscription expired or gone — clean up
      removeSubscription(userId);
      return { sent: false, reason: 'Subscription expired — removed' };
    }
    console.warn(`[Push] pushToUser(${userId}) failed: ${err.message}`);
    return { sent: false, reason: err.message };
  }
}

export async function pushToAll(payload, filter = null) {
  const wp = await getWebPush();
  if (!wp || !vapidKeys) return;

  const subs = loadSubs();
  const entries = Object.values(subs).filter(e => filter ? filter(e) : true);
  const results = await Promise.allSettled(entries.map(e => pushToUser(e.userId, payload)));
  const sent = results.filter(r => r.status === 'fulfilled' && r.value?.sent).length;
  console.log(`[Push] pushToAll — ${sent}/${entries.length} delivered`);
}

export async function pushDigest(title, body, url = '/') {
  const payload = pushPayload(title, body, url);
  return pushToAll(payload, e => e.notifyDigest !== false);
}

export async function pushFlash(title, body, url = '/') {
  const payload = pushPayload(title, body, url);
  return pushToAll(payload, e => e.notifyFlash !== false);
}

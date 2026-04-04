/**
 * ARIA — WhatsApp Group Listener
 * ═══════════════════════════════════════════════════════════════════════════
 * Uses a normal WhatsApp number (your Portuguese SIM) to join groups and
 * listen to conversations. ARIA learns silently — she never sends anything
 * through this number.
 *
 * HOW IT WORKS:
 *   1. You register your Portuguese SIM on WhatsApp Business App (one-time)
 *   2. This service connects to that account as a linked device (like WhatsApp Web)
 *   3. It receives every group message silently
 *   4. Each message is stored in ARIA's memory for learning
 *   5. Nothing is ever sent back through this number
 *
 * ─────────────────────────────────────────────────────────────────────────
 * SEENODE ENV VARS (only 3 new ones — everything else already set)
 * ─────────────────────────────────────────────────────────────────────────
 *
 *   WA_LISTENER_GROUP_IDS    Comma-separated WhatsApp group IDs to listen to
 *                            e.g. 351912345678-1234567890@g.us,351...
 *                            (how to find: see STEP 4 in setup below)
 *
 *   WA_LISTENER_AUTH_DIR     Path to store auth session files
 *                            Set to: /data/wa-listener-auth
 *
 *   WA_LISTENER_PORT         Port for this service
 *                            Set to: 5070
 *
 *   Already set — no changes needed:
 *   BRAIN_SERVICE_URL, ARIA_INTERNAL_TOKEN, REDIS_URL
 *
 * ─────────────────────────────────────────────────────────────────────────
 * SETUP STEPS — DO THESE BEFORE DEPLOYING
 * ─────────────────────────────────────────────────────────────────────────
 *
 * STEP 1 — Install WhatsApp Business App on your Portuguese SIM
 *   - Download "WhatsApp Business" (free) on any phone
 *   - Register using your Portuguese number (+351 ...)
 *   - Set profile name: "ARIA — Arkmurus Intelligence"
 *   - Set profile picture (optional — ARIA logo or Arkmurus logo)
 *   - Set business description: "Arkmurus Research Intelligence Agent"
 *
 * STEP 2 — Add ARIA to your WhatsApp group
 *   - Open your Arkmurus WhatsApp group
 *   - Group info → Add participant
 *   - Add your Portuguese number
 *   - ARIA is now a member of the group
 *
 * STEP 3 — Deploy this service to Seenode
 *   - Create a new Seenode service from this file
 *   - Set the 3 env vars above
 *   - Deploy — it will print a QR code in the logs
 *
 * STEP 4 — Scan the QR code (one-time only)
 *   - Open Seenode logs for this service
 *   - On the phone with your Portuguese SIM:
 *     WhatsApp Business → Settings → Linked Devices → Link a Device
 *   - Scan the QR code shown in the logs
 *   - Done — ARIA is connected. QR code never needed again.
 *
 * STEP 5 — Find your group IDs (for WA_LISTENER_GROUP_IDS)
 *   - After scanning, call: GET https://[your-service]/groups
 *     (include header: Authorization: Bearer <ARIA_INTERNAL_TOKEN>)
 *   - It lists all groups the number is in with their IDs
 *   - Copy the IDs of the groups you want ARIA to listen to
 *   - Add them to WA_LISTENER_GROUP_IDS in Seenode, comma-separated
 *   - Restart the service
 *
 * ─────────────────────────────────────────────────────────────────────────
 * INSTALL (if running standalone)
 * ─────────────────────────────────────────────────────────────────────────
 *   npm install @whiskeysockets/baileys@latest qrcode-terminal pino
 *   npm install express redis
 * ═══════════════════════════════════════════════════════════════════════════
 */

import makeWASocket, {
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
  makeCacheableSignalKeyStore,
  Browsers,
} from '@whiskeysockets/baileys';

import qrcode   from 'qrcode-terminal';
import pino     from 'pino';
import express  from 'express';
import fs       from 'fs';
import { createClient } from 'redis';

// ── Config — all from Seenode env vars ───────────────────────────────────────
const GROUP_IDS_RAW = process.env.WA_LISTENER_GROUP_IDS || '';
const AUTH_DIR      = process.env.WA_LISTENER_AUTH_DIR  || './wa-listener-auth';
const PORT          = parseInt(process.env.WA_LISTENER_PORT || '5070');
const BRAIN_URL     = process.env.BRAIN_SERVICE_URL      || 'http://localhost:3117';
const INT_TOKEN     = process.env.ARIA_INTERNAL_TOKEN    || 'aria-internal';
const REDIS_URL     = process.env.REDIS_URL              || '';

// Parse group IDs — can be set after first run once you know your group IDs
const TARGET_GROUPS = GROUP_IDS_RAW
  ? GROUP_IDS_RAW.split(',').map(g => g.trim()).filter(Boolean)
  : [];   // empty = listen to ALL groups the number is in

// ── Logging — silent by default, errors only ──────────────────────────────────
const logger = pino({ level: 'silent' });

// ── Redis (optional — for persistent memory) ─────────────────────────────────
let redis = null;
if (REDIS_URL) {
  try {
    redis = createClient({ url: REDIS_URL });
    await redis.connect();
    console.log('[ARIA Listener] ✓ Redis connected');
  } catch(e) {
    console.warn('[ARIA Listener] Redis unavailable — using in-memory only');
    redis = null;
  }
}

// ── API authentication ───────────────────────────────────────────────────────
function requireAuth(req, res, next) {
  const auth = req.headers.authorization || '';
  const token = auth.startsWith('Bearer ') ? auth.slice(7) : '';
  if (token && token === INT_TOKEN) return next();
  return res.status(401).json({ error: 'Unauthorized — include Authorization: Bearer <ARIA_INTERNAL_TOKEN>' });
}

// ── In-memory message store (rolling 500 messages across all groups) ─────────
const messageStore = [];
const MAX_STORE    = 500;

function store(groupId, groupName, sender, senderName, text, ts) {
  const entry = { groupId, groupName, sender, senderName, text, ts };
  messageStore.push(entry);
  if (messageStore.length > MAX_STORE) messageStore.shift();

  // Persist to Redis for ARIA to access across restarts
  if (redis) {
    const key = `crucix:wa_listener:messages:${Date.now()}`;
    redis.setEx(key, 7 * 86400, JSON.stringify(entry)).catch(() => {});
  }
}

// ── Feed message to ARIA brain ─────────────────────────────────────────────────
async function feedToARIA(groupName, senderName, text) {
  try {
    await fetch(`${BRAIN_URL}/api/brain/signal`, {
      method:  'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${INT_TOKEN}`,
      },
      body: JSON.stringify({
        content:     text,
        source:      `whatsapp_group:${groupName}:${senderName}`,
        signal_type: 'whatsapp_group_message',
        metadata: {
          group:     groupName,
          sender:    senderName,
          timestamp: new Date().toISOString(),
          channel:   'whatsapp_listener',
        },
      }),
      signal: AbortSignal.timeout(5000),
    });
  } catch(e) {
    // Brain unavailable — message already stored in Redis above
  }
}

// ── Group name cache ──────────────────────────────────────────────────────────
const groupNames = new Map();   // groupId → display name

// ── Connection state ──────────────────────────────────────────────────────────
let sock           = null;
let isConnected    = false;
let qrPrinted      = false;
let messagesHeard  = 0;
let startedAt      = null;
let reconnectDelay = 5000;  // exponential backoff: 5s → 10s → 20s → max 60s

// ── Start the WhatsApp connection ─────────────────────────────────────────────
async function startListener() {
  fs.mkdirSync(AUTH_DIR, { recursive: true });

  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  const { version }          = await fetchLatestBaileysVersion();

  console.log(`[ARIA Listener] Starting — Baileys v${version.join('.')}`);
  if (TARGET_GROUPS.length) {
    console.log(`[ARIA Listener] Listening to ${TARGET_GROUPS.length} group(s)`);
  } else {
    console.log('[ARIA Listener] Listening to ALL groups (set WA_LISTENER_GROUP_IDS to filter)');
  }

  sock = makeWASocket({
    version,
    auth: {
      creds: state.creds,
      keys:  makeCacheableSignalKeyStore(state.keys, logger),
    },
    logger,
    browser:          Browsers.macOS('ARIA'),   // appears as a Mac browser — less suspicious
    markOnlineOnConnect: false,                 // ARIA stays "offline" — just listening
    generateHighQualityLinkPreview: false,
    syncFullHistory: false,                     // only new messages from now on
  });

  // ── Save credentials whenever they update ─────────────────────────────────
  sock.ev.on('creds.update', saveCreds);

  // ── Connection lifecycle ───────────────────────────────────────────────────
  sock.ev.on('connection.update', ({ connection, lastDisconnect, qr }) => {

    // QR code — print once to logs for scanning
    if (qr && !qrPrinted) {
      qrPrinted = true;
      console.log('\n[ARIA Listener] ══════════════════════════════════════════');
      console.log('[ARIA Listener] SCAN THIS QR CODE with your Portuguese number:');
      console.log('[ARIA Listener]   WhatsApp Business → Settings → Linked Devices → Link a Device');
      console.log('[ARIA Listener] ══════════════════════════════════════════\n');
      qrcode.generate(qr, { small: true });
      console.log('\n[ARIA Listener] Waiting for scan...\n');
    }

    if (connection === 'open') {
      isConnected = true;
      startedAt   = new Date().toISOString();
      qrPrinted   = false;
      reconnectDelay = 5000;  // reset backoff on successful connect
      console.log('[ARIA Listener] ✓ Connected to WhatsApp — ARIA is listening');
      console.log('[ARIA Listener] Call GET /groups to find your group IDs');
    }

    if (connection === 'close') {
      isConnected = false;
      const code  = lastDisconnect?.error?.output?.statusCode;
      const logout = code === DisconnectReason.loggedOut;

      if (logout) {
        // Auth was invalidated — need to re-scan QR
        console.log('[ARIA Listener] ⚠ Logged out — delete auth folder and restart to re-scan QR');
        console.log(`[ARIA Listener]   rm -rf ${AUTH_DIR} && restart service`);
      } else {
        // Network issue — reconnect with exponential backoff
        console.log(`[ARIA Listener] Disconnected (code ${code}) — reconnecting in ${reconnectDelay/1000}s...`);
        setTimeout(startListener, reconnectDelay);
        reconnectDelay = Math.min(reconnectDelay * 2, 60000);
      }
    }
  });

  // ── Group metadata cache ───────────────────────────────────────────────────
  sock.ev.on('groups.upsert', (groups) => {
    for (const g of groups) {
      groupNames.set(g.id, g.subject);
    }
  });

  sock.ev.on('groups.update', (updates) => {
    for (const u of updates) {
      if (u.subject) groupNames.set(u.id, u.subject);
    }
  });

  // ── THE CORE: receive every group message ──────────────────────────────────
  sock.ev.on('messages.upsert', async ({ messages, type }) => {

    // Only process new incoming messages, not history
    if (type !== 'notify') return;

    for (const msg of messages) {
      // Skip messages sent by ARIA herself
      if (msg.key.fromMe) continue;

      const chatId = msg.key.remoteJid || '';

      // Only process group messages (group IDs end in @g.us)
      if (!chatId.endsWith('@g.us')) continue;

      // Filter to target groups if specified
      if (TARGET_GROUPS.length && !TARGET_GROUPS.includes(chatId)) continue;

      // Extract message text
      const text =
        msg.message?.conversation                              ||
        msg.message?.extendedTextMessage?.text                 ||
        msg.message?.imageMessage?.caption                     ||
        msg.message?.videoMessage?.caption                     ||
        msg.message?.documentMessage?.caption                  ||
        msg.message?.buttonsResponseMessage?.selectedDisplayText ||
        '';

      if (!text.trim()) continue;   // skip media-only messages with no caption

      // Get sender name
      const senderJid  = msg.key.participant || msg.key.remoteJid || '';
      const senderName =
        msg.pushName ||
        senderJid.replace('@s.whatsapp.net','').replace('@g.us','') ||
        'Unknown';

      // Get group name
      let groupName = groupNames.get(chatId);
      if (!groupName) {
        try {
          const meta = await sock.groupMetadata(chatId);
          groupName  = meta.subject;
          groupNames.set(chatId, groupName);
        } catch(e) {
          groupName = chatId;
        }
      }

      const ts = new Date(
        (msg.messageTimestamp ? Number(msg.messageTimestamp) * 1000 : Date.now())
      ).toISOString();

      // Log to console
      console.log(`[${groupName}] ${senderName}: ${text.slice(0, 100)}`);
      messagesHeard++;

      // Store in memory + Redis
      store(chatId, groupName, senderJid, senderName, text, ts);

      // Feed to ARIA brain (non-blocking)
      feedToARIA(groupName, senderName, text).catch(() => {});
    }
  });
}

// ── Express status API ────────────────────────────────────────────────────────
const app = express();
app.use(express.json());

// Health — unauthenticated (for Seenode health checks)
app.get('/health', (_req, res) => {
  res.json({ status: isConnected ? 'connected' : 'disconnected' });
});

// Status — shows if listener is connected
app.get('/status', requireAuth, (_req, res) => {
  res.json({
    connected:      isConnected,
    started_at:     startedAt,
    messages_heard: messagesHeard,
    target_groups:  TARGET_GROUPS.length ? TARGET_GROUPS : 'ALL',
    group_names:    Object.fromEntries(groupNames),
    memory_store:   messageStore.length,
    redis:          !!redis,
    auth_dir:       AUTH_DIR,
    note:           isConnected
      ? 'ARIA is listening to WhatsApp groups'
      : 'Not connected — check logs for QR code',
  });
});

// List all groups the number is in — call this to find your group IDs
app.get('/groups', requireAuth, async (_req, res) => {
  if (!sock || !isConnected) {
    return res.status(503).json({ error: 'Not connected yet — scan QR code first' });
  }
  try {
    const groups = await sock.groupFetchAllParticipating();
    const list   = Object.entries(groups).map(([id, meta]) => ({
      id,
      name:         meta.subject,
      participants: meta.participants?.length || 0,
      add_to_env:   `Add "${id}" to WA_LISTENER_GROUP_IDS in Seenode`,
    }));
    res.json({ count: list.length, groups: list });
  } catch(e) {
    res.status(500).json({ error: e.message });
  }
});

// Recent messages ARIA has heard
app.get('/messages', requireAuth, (req, res) => {
  const n    = Math.min(parseInt(req.query.n || '20'), 100);
  const grp  = req.query.group || '';
  const msgs = grp
    ? messageStore.filter(m => m.groupName === grp || m.groupId === grp)
    : messageStore;
  res.json({
    count:    msgs.length,
    messages: msgs.slice(-n).reverse(),
  });
});

// Reset auth — forces re-scan of QR code (protected — dangerous operation)
app.post('/reset-auth', requireAuth, (_req, res) => {
  try {
    fs.rmSync(AUTH_DIR, { recursive: true, force: true });
    res.json({ message: 'Auth cleared — restart service to get new QR code' });
  } catch(e) {
    res.status(500).json({ error: e.message });
  }
});

app.listen(PORT, () => {
  console.log(`[ARIA Listener] API on port ${PORT}`);
  console.log(`[ARIA Listener] GET /health   — health check (no auth)`);
  console.log(`[ARIA Listener] GET /status   — connection status`);
  console.log(`[ARIA Listener] GET /groups   — list groups + their IDs`);
  console.log(`[ARIA Listener] GET /messages — recent messages heard`);
});

// ── Start ─────────────────────────────────────────────────────────────────────
startListener().catch(e => {
  console.error('[ARIA Listener] Fatal error:', e);
  process.exit(1);
});


/*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SEENODE ENV VARS — add these 3, everything else already set
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

WA_LISTENER_GROUP_IDS    (leave blank first time — fill in after Step 5)
WA_LISTENER_AUTH_DIR     /data/wa-listener-auth
WA_LISTENER_PORT         5070

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
package.json — create this in the same folder
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{
  "name": "aria-wa-listener",
  "version": "1.0.0",
  "type": "module",
  "main": "aria_wa_listener.mjs",
  "scripts": { "start": "node aria_wa_listener.mjs" },
  "dependencies": {
    "@whiskeysockets/baileys": "latest",
    "qrcode-terminal": "^0.12.0",
    "pino": "^9.0.0",
    "express": "^4.18.0",
    "redis": "^4.6.0"
  }
}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Dockerfile (deploy as separate Seenode service)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FROM node:20-alpine
RUN apk add --no-cache python3 make g++
WORKDIR /app
COPY package.json .
RUN npm install
COPY aria_wa_listener.mjs .
RUN mkdir -p /data/wa-listener-auth
VOLUME ["/data/wa-listener-auth"]
EXPOSE 5070
CMD ["node", "aria_wa_listener.mjs"]
*/

/**
 * ARIA — WhatsApp Group Listener (Baileys)
 * ═══════════════════════════════════════════════════════════════════════════
 * Connects to WhatsApp as a linked device using your Portuguese SIM.
 * ARIA listens silently to group messages and feeds them to the brain.
 * She never sends anything through this number.
 *
 * Runs inside the main server.mjs process — no separate service needed.
 *
 * ─────────────────────────────────────────────────────────────────────────
 * SEENODE ENV VARS (add to your existing Crucix app)
 * ─────────────────────────────────────────────────────────────────────────
 *   WA_LISTENER_ENABLED      Set to "true" to activate
 *   WA_LISTENER_GROUP_IDS    Comma-separated group IDs (blank = all groups)
 *   WA_LISTENER_AUTH_DIR     /data/wa-listener-auth
 *
 * ─────────────────────────────────────────────────────────────────────────
 * SETUP
 * ─────────────────────────────────────────────────────────────────────────
 *   1. Install WhatsApp Business on your Portuguese SIM
 *   2. Add that number to your Arkmurus WhatsApp group
 *   3. Set WA_LISTENER_ENABLED=true on Seenode and deploy
 *   4. Check Seenode logs for QR code
 *   5. WhatsApp Business → Settings → Linked Devices → Scan QR
 *   6. GET /api/wa-listener/groups to find group IDs
 *   7. Set WA_LISTENER_GROUP_IDS and redeploy
 * ═══════════════════════════════════════════════════════════════════════════
 */

import fs from 'fs';

const ENABLED       = process.env.WA_LISTENER_ENABLED === 'true';
const GROUP_IDS_RAW = process.env.WA_LISTENER_GROUP_IDS || '';
const AUTH_DIR      = process.env.WA_LISTENER_AUTH_DIR  || './wa-listener-auth';
const INT_TOKEN     = process.env.ARIA_INTERNAL_TOKEN   || 'aria-internal';

const TARGET_GROUPS = GROUP_IDS_RAW
  ? GROUP_IDS_RAW.split(',').map(g => g.trim()).filter(Boolean)
  : [];

// ── State ────────────────────────────────────────────────────────────────────
let sock           = null;
let isConnected    = false;
let qrPrinted      = false;
let messagesHeard  = 0;
let startedAt      = null;
let reconnectDelay = 5000;
const groupNames   = new Map();
const messageStore = [];
const MAX_STORE    = 500;

function store(groupId, groupName, sender, senderName, text, ts) {
  messageStore.push({ groupId, groupName, sender, senderName, text, ts });
  if (messageStore.length > MAX_STORE) messageStore.shift();
}

// ── Feed to brain via local server ───────────────────────────────────────────
async function feedToARIA(groupName, senderName, text) {
  const port = process.env.PORT || 3117;
  try {
    await fetch(`http://localhost:${port}/api/brain/signal`, {
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
    // Brain not ready yet or unavailable — message stored in memory
  }
}

// ── Start the WhatsApp connection ────────────────────────────────────────────
async function startListener() {
  let makeWASocket, useMultiFileAuthState, DisconnectReason,
      fetchLatestBaileysVersion, makeCacheableSignalKeyStore, Browsers;
  let qrcode, pino;

  try {
    const baileys = await import('@whiskeysockets/baileys');
    makeWASocket              = baileys.default;
    useMultiFileAuthState     = baileys.useMultiFileAuthState;
    DisconnectReason          = baileys.DisconnectReason;
    fetchLatestBaileysVersion = baileys.fetchLatestBaileysVersion;
    makeCacheableSignalKeyStore = baileys.makeCacheableSignalKeyStore;
    Browsers                  = baileys.Browsers;
    qrcode = (await import('qrcode-terminal')).default;
    pino   = (await import('pino')).default;
  } catch(e) {
    console.warn('[WA Listener] Baileys not installed — run: npm install @whiskeysockets/baileys qrcode-terminal pino');
    console.warn('[WA Listener] WhatsApp listener disabled');
    return;
  }

  const logger = pino({ level: 'silent' });

  fs.mkdirSync(AUTH_DIR, { recursive: true });

  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  const { version }          = await fetchLatestBaileysVersion();

  console.log(`[WA Listener] Starting — Baileys v${version.join('.')}`);
  if (TARGET_GROUPS.length) {
    console.log(`[WA Listener] Listening to ${TARGET_GROUPS.length} group(s)`);
  } else {
    console.log('[WA Listener] Listening to ALL groups (set WA_LISTENER_GROUP_IDS to filter)');
  }

  sock = makeWASocket({
    version,
    auth: {
      creds: state.creds,
      keys:  makeCacheableSignalKeyStore(state.keys, logger),
    },
    logger,
    browser:                Browsers.macOS('ARIA'),
    markOnlineOnConnect:    false,
    generateHighQualityLinkPreview: false,
    syncFullHistory:        false,
  });

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', ({ connection, lastDisconnect, qr }) => {
    if (qr && !qrPrinted) {
      qrPrinted = true;
      console.log('\n[WA Listener] ══════════════════════════════════════════');
      console.log('[WA Listener] SCAN THIS QR CODE with your Portuguese number:');
      console.log('[WA Listener]   WhatsApp Business → Settings → Linked Devices → Link a Device');
      console.log('[WA Listener] ══════════════════════════════════════════\n');
      qrcode.generate(qr, { small: true });
      console.log('\n[WA Listener] Waiting for scan...\n');
    }

    if (connection === 'open') {
      isConnected    = true;
      startedAt      = new Date().toISOString();
      qrPrinted      = false;
      reconnectDelay = 5000;
      console.log('[WA Listener] ✓ Connected to WhatsApp — ARIA is listening');
      console.log('[WA Listener] GET /api/wa-listener/groups to find your group IDs');
    }

    if (connection === 'close') {
      isConnected = false;
      const code  = lastDisconnect?.error?.output?.statusCode;
      const logout = code === DisconnectReason.loggedOut;

      if (logout) {
        console.log('[WA Listener] ⚠ Logged out — delete auth folder and restart to re-scan QR');
        console.log(`[WA Listener]   rm -rf ${AUTH_DIR} && restart service`);
      } else {
        console.log(`[WA Listener] Disconnected (code ${code}) — reconnecting in ${reconnectDelay/1000}s...`);
        setTimeout(startListener, reconnectDelay);
        reconnectDelay = Math.min(reconnectDelay * 2, 60000);
      }
    }
  });

  sock.ev.on('groups.upsert', (groups) => {
    for (const g of groups) groupNames.set(g.id, g.subject);
  });

  sock.ev.on('groups.update', (updates) => {
    for (const u of updates) if (u.subject) groupNames.set(u.id, u.subject);
  });

  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    if (type !== 'notify') return;

    for (const msg of messages) {
      if (msg.key.fromMe) continue;

      const chatId = msg.key.remoteJid || '';
      if (!chatId.endsWith('@g.us')) continue;
      if (TARGET_GROUPS.length && !TARGET_GROUPS.includes(chatId)) continue;

      const text =
        msg.message?.conversation                              ||
        msg.message?.extendedTextMessage?.text                 ||
        msg.message?.imageMessage?.caption                     ||
        msg.message?.videoMessage?.caption                     ||
        msg.message?.documentMessage?.caption                  ||
        msg.message?.buttonsResponseMessage?.selectedDisplayText ||
        '';

      if (!text.trim()) continue;

      const senderJid  = msg.key.participant || msg.key.remoteJid || '';
      const senderName =
        msg.pushName ||
        senderJid.replace('@s.whatsapp.net','').replace('@g.us','') ||
        'Unknown';

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

      console.log(`[WA] [${groupName}] ${senderName}: ${text.slice(0, 100)}`);
      messagesHeard++;

      store(chatId, groupName, senderJid, senderName, text, ts);
      feedToARIA(groupName, senderName, text).catch(() => {});
    }
  });
}

// ── Mount Express routes onto main app ───────────────────────────────────────
export function mountWAListener(app) {
  if (!ENABLED) {
    console.log('[WA Listener] Disabled — set WA_LISTENER_ENABLED=true to activate');
    return;
  }

  // Start the Baileys connection (async, non-blocking)
  startListener().catch(e => {
    console.error('[WA Listener] Failed to start:', e.message);
  });

  // Status
  app.get('/api/wa-listener/status', (_req, res) => {
    res.json({
      connected:      isConnected,
      started_at:     startedAt,
      messages_heard: messagesHeard,
      target_groups:  TARGET_GROUPS.length ? TARGET_GROUPS : 'ALL',
      group_names:    Object.fromEntries(groupNames),
      memory_store:   messageStore.length,
      note:           isConnected
        ? 'ARIA is listening to WhatsApp groups'
        : 'Not connected — check logs for QR code',
    });
  });

  // List groups — for finding group IDs
  app.get('/api/wa-listener/groups', async (_req, res) => {
    if (!sock || !isConnected) {
      return res.status(503).json({ error: 'Not connected — scan QR code first (check logs)' });
    }
    try {
      const groups = await sock.groupFetchAllParticipating();
      const list = Object.entries(groups).map(([id, meta]) => ({
        id,
        name:         meta.subject,
        participants: meta.participants?.length || 0,
      }));
      res.json({ count: list.length, groups: list });
    } catch(e) {
      res.status(500).json({ error: e.message });
    }
  });

  // Recent messages
  app.get('/api/wa-listener/messages', (req, res) => {
    const n   = Math.min(parseInt(req.query.n || '20'), 100);
    const grp = req.query.group || '';
    const msgs = grp
      ? messageStore.filter(m => m.groupName === grp || m.groupId === grp)
      : messageStore;
    res.json({ count: msgs.length, messages: msgs.slice(-n).reverse() });
  });

  console.log('[WA Listener] Routes mounted — /api/wa-listener/*');
}

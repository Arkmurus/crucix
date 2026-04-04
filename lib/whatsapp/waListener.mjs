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
async function feedToARIA(groupName, senderName, text, signalType = 'whatsapp_group_message', extra = {}) {
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
        signal_type: signalType,
        metadata: {
          group:     groupName,
          sender:    senderName,
          timestamp: new Date().toISOString(),
          channel:   'whatsapp_listener',
          ...extra,
        },
      }),
      signal: AbortSignal.timeout(5000),
    });
  } catch(e) {
    // Brain not ready yet or unavailable — message stored in memory
  }
}

// ── Media processing — download and extract text from shared files ────────────
let _downloadMediaMessage = null;

async function processMedia(msg, sock, groupName, senderName) {
  try {
    // Lazy-load downloadMediaMessage from Baileys
    if (!_downloadMediaMessage) {
      const baileys = await import('@whiskeysockets/baileys');
      _downloadMediaMessage = baileys.downloadMediaMessage;
    }

    const m = msg.message;
    if (!m) return null;

    // ── PDF documents ──────────────────────────────────────────────────────
    const docMsg = m.documentMessage || m.documentWithCaptionMessage?.message?.documentMessage;
    if (docMsg) {
      const mime = (docMsg.mimetype || '').toLowerCase();
      const fileName = docMsg.fileName || 'unknown';
      const caption  = docMsg.caption || '';

      if (mime.includes('pdf')) {
        try {
          const buffer = await _downloadMediaMessage(msg, 'buffer', {});
          const pdfParse = (await import('pdf-parse')).default;
          const pdf = await pdfParse(buffer);
          const text = (pdf.text || '').trim().slice(0, 8000);
          if (text.length > 50) {
            const content = `[PDF: ${fileName}] ${caption ? caption + '\n\n' : ''}${text}`;
            console.log(`[WA] [${groupName}] ${senderName}: 📄 PDF "${fileName}" (${text.length} chars extracted)`);
            store(msg.key.remoteJid, groupName, msg.key.participant, senderName, content, new Date().toISOString());
            await feedToARIA(groupName, senderName, content, 'whatsapp_document', { file_name: fileName, file_type: 'pdf', pages: pdf.numpages });
            return content;
          }
        } catch(e) {
          console.warn(`[WA] PDF parse failed for "${fileName}":`, e.message);
        }
      }

      // Non-PDF documents — store metadata
      if (fileName && fileName !== 'unknown') {
        const content = `[Document shared: ${fileName}] ${caption || ''}`.trim();
        console.log(`[WA] [${groupName}] ${senderName}: 📎 Document "${fileName}" (${mime})`);
        store(msg.key.remoteJid, groupName, msg.key.participant, senderName, content, new Date().toISOString());
        await feedToARIA(groupName, senderName, content, 'whatsapp_document', { file_name: fileName, file_type: mime });
        return content;
      }
    }

    // ── vCard contacts ─────────────────────────────────────────────────────
    const contactMsg = m.contactMessage;
    const contactsMsg = m.contactsArrayMessage;

    if (contactMsg) {
      const parsed = parseVCard(contactMsg.vcard || '', contactMsg.displayName);
      if (parsed) {
        const content = `[Contact shared] ${parsed}`;
        console.log(`[WA] [${groupName}] ${senderName}: 👤 Contact shared — ${parsed.slice(0, 80)}`);
        store(msg.key.remoteJid, groupName, msg.key.participant, senderName, content, new Date().toISOString());
        await feedToARIA(groupName, senderName, content, 'whatsapp_contact', { contact_type: 'single' });
        return content;
      }
    }

    if (contactsMsg?.contacts?.length) {
      const parsed = contactsMsg.contacts.map(c =>
        parseVCard(c.vcard || '', c.displayName)
      ).filter(Boolean);
      if (parsed.length) {
        const content = `[${parsed.length} contacts shared]\n${parsed.join('\n')}`;
        console.log(`[WA] [${groupName}] ${senderName}: 👥 ${parsed.length} contacts shared`);
        store(msg.key.remoteJid, groupName, msg.key.participant, senderName, content, new Date().toISOString());
        await feedToARIA(groupName, senderName, content, 'whatsapp_contact', { contact_type: 'multiple', count: parsed.length });
        return content;
      }
    }

    // ── Images with no caption — log that image was shared ─────────────────
    if (m.imageMessage && !m.imageMessage.caption) {
      console.log(`[WA] [${groupName}] ${senderName}: 🖼 Image shared (no caption)`);
      return null;
    }

    // ── Location sharing ───────────────────────────────────────────────────
    const locMsg = m.locationMessage || m.liveLocationMessage;
    if (locMsg) {
      const lat = locMsg.degreesLatitude;
      const lng = locMsg.degreesLongitude;
      const name = locMsg.name || locMsg.address || '';
      if (lat && lng) {
        const content = `[Location shared] ${name} (${lat.toFixed(4)}, ${lng.toFixed(4)})`;
        console.log(`[WA] [${groupName}] ${senderName}: 📍 Location: ${name || `${lat}, ${lng}`}`);
        store(msg.key.remoteJid, groupName, msg.key.participant, senderName, content, new Date().toISOString());
        await feedToARIA(groupName, senderName, content, 'whatsapp_location', { lat, lng, location_name: name });
        return content;
      }
    }

  } catch(e) {
    console.warn('[WA] Media processing error:', e.message);
  }
  return null;
}

// ── vCard parser — extracts name, phone, email, org from WhatsApp contacts ───
function parseVCard(vcard, displayName) {
  if (!vcard) return displayName || null;
  const lines = vcard.split('\n').map(l => l.trim());

  let name  = displayName || '';
  let phone = '';
  let email = '';
  let org   = '';
  let title = '';

  for (const line of lines) {
    if (line.startsWith('FN:'))   name  = line.slice(3).trim();
    if (line.startsWith('ORG:'))  org   = line.slice(4).trim().replace(/;/g, ' ');
    if (line.startsWith('TITLE:')) title = line.slice(6).trim();
    if (line.startsWith('TEL') && !phone) {
      const m = line.match(/:([\d+\s()-]+)/);
      if (m) phone = m[1].trim();
    }
    if (line.startsWith('EMAIL') && !email) {
      const m = line.match(/:(.+)/);
      if (m) email = m[1].trim();
    }
  }

  const parts = [name];
  if (title) parts.push(title);
  if (org)   parts.push(org);
  if (phone) parts.push(phone);
  if (email) parts.push(email);

  return parts.filter(Boolean).join(' | ') || null;
}

// ── Cached imports (loaded once) ─────────────────────────────────────────────
let _baileys = null, _qrcode = null, _pino = null;

async function loadDeps() {
  if (_baileys) return true;
  try {
    _baileys = await import('@whiskeysockets/baileys');
    _qrcode  = (await import('qrcode-terminal')).default;
    _pino    = (await import('pino')).default;
    return true;
  } catch(e) {
    console.warn('[WA Listener] Baileys not installed — run: npm install @whiskeysockets/baileys qrcode-terminal pino');
    console.warn('[WA Listener] WhatsApp listener disabled');
    return false;
  }
}

// ── Start the WhatsApp connection ────────────────────────────────────────────
async function startListener() {
  if (!await loadDeps()) return;

  const {
    default: makeWASocket,
    useMultiFileAuthState,
    DisconnectReason,
    fetchLatestBaileysVersion,
    makeCacheableSignalKeyStore,
    Browsers,
  } = _baileys;

  const logger = _pino({ level: 'silent' });

  // Reset QR flag — allow new QR on each connection attempt
  qrPrinted = false;

  // Close previous socket if any
  if (sock) {
    try { sock.end(); } catch {}
    sock = null;
  }

  fs.mkdirSync(AUTH_DIR, { recursive: true });

  // Check if auth state exists (previously scanned)
  const authFiles = fs.readdirSync(AUTH_DIR);
  if (authFiles.length > 0) {
    console.log(`[WA Listener] Found saved auth (${authFiles.length} files) — attempting auto-reconnect`);
  } else {
    console.log('[WA Listener] No saved auth — QR code scan required');
  }

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
    connectTimeoutMs:       60000,       // 60s to complete QR scan
    defaultQueryTimeoutMs:  60000,
    retryRequestDelayMs:    500,
  });

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', ({ connection, lastDisconnect, qr }) => {
    // Show EVERY QR code — they refresh every ~20s and each one is different
    if (qr) {
      console.log('\n[WA Listener] ══════════════════════════════════════════');
      if (!qrPrinted) {
        console.log('[WA Listener] SCAN THIS QR CODE with your Portuguese number:');
        console.log('[WA Listener]   WhatsApp Business → Settings → Linked Devices → Link a Device');
      } else {
        console.log('[WA Listener] QR REFRESHED — scan this new one:');
      }
      console.log('[WA Listener] ══════════════════════════════════════════\n');
      _qrcode.generate(qr, { small: true });
      console.log('\n[WA Listener] Waiting for scan...\n');
      qrPrinted = true;
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
        console.log('[WA Listener] ⚠ Logged out — clearing auth and restarting for new QR...');
        try { fs.rmSync(AUTH_DIR, { recursive: true, force: true }); } catch {}
        setTimeout(startListener, 5000);
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

      // ── Extract text from message ───────────────────────────────────────
      const text =
        msg.message?.conversation                              ||
        msg.message?.extendedTextMessage?.text                 ||
        msg.message?.imageMessage?.caption                     ||
        msg.message?.videoMessage?.caption                     ||
        msg.message?.documentMessage?.caption                  ||
        msg.message?.documentWithCaptionMessage?.message?.documentMessage?.caption ||
        msg.message?.buttonsResponseMessage?.selectedDisplayText ||
        '';

      // ── Process media (PDFs, contacts, locations) even without text ─────
      const hasMedia = msg.message && (
        msg.message.documentMessage ||
        msg.message.documentWithCaptionMessage ||
        msg.message.contactMessage ||
        msg.message.contactsArrayMessage ||
        msg.message.locationMessage ||
        msg.message.liveLocationMessage
      );

      if (hasMedia) {
        // Process media in background — don't block text processing
        processMedia(msg, sock, groupName, senderName).then(extracted => {
          if (extracted) messagesHeard++;
        }).catch(() => {});
      }

      // ── Process text messages ───────────────────────────────────────────
      if (!text.trim()) continue;

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

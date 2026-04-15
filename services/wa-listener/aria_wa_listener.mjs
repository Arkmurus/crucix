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
 *   WA_LISTENER_AUTO_RESPOND  Enable/disable smart auto-responses in groups
 *                            Set to: true (default) or false
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
import { logComplianceAction } from '../../lib/aria/complianceAudit.mjs';

// ── Config — all from Seenode env vars ───────────────────────────────────────
const GROUP_IDS_RAW = process.env.WA_LISTENER_GROUP_IDS || '';
const AUTH_DIR      = process.env.WA_LISTENER_AUTH_DIR  || './wa-listener-auth';
const PORT          = parseInt(process.env.WA_LISTENER_PORT || '5070');
const BRAIN_URL     = process.env.BRAIN_SERVICE_URL      || 'http://localhost:3117';
const INT_TOKEN     = process.env.ARIA_INTERNAL_TOKEN    || 'aria-internal';
const REDIS_URL     = process.env.REDIS_URL              || '';
const AUTO_RESPOND  = (process.env.WA_LISTENER_AUTO_RESPOND || 'true').toLowerCase() === 'true';
const MAX_DOC_CHARS = parseInt(process.env.ARIA_MAX_DOC_CHARS || '200000', 10);

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

// ── Smart auto-response: keyword detection ──────────────────────────────────
const COMPLIANCE_TRIGGERS = [
  /export.licen/i, /sanction/i, /embargo/i, /\bitar\b/i, /end.user/i,
  /controlled.goods/i, /\bml.categor/i, /dual.use/i, /export.control/i,
  /\bofac\b/i, /\bofsi\b/i, /brokering.licen/i, /arms.embargo/i,
];
const OPPORTUNITY_TRIGGERS = [
  /\btender\b/i, /\brfp\b/i, /procurement/i, /budget.allocation/i,
  /contract.award/i, /\brfq\b/i, /bid.submission/i,
];
const RISK_TRIGGERS = [
  /diversion/i, /sanctions?.risk/i, /compliance.concern/i, /red.flag/i,
  /end.user.risk/i, /proliferation/i,
];

function detectComplianceTrigger(text) {
  const t = text.slice(0, 2000);
  const matched = [];

  for (const re of COMPLIANCE_TRIGGERS) {
    const m = t.match(re);
    if (m) matched.push({ category: 'compliance', keyword: m[0] });
  }
  for (const re of OPPORTUNITY_TRIGGERS) {
    const m = t.match(re);
    if (m) matched.push({ category: 'opportunity', keyword: m[0] });
  }
  for (const re of RISK_TRIGGERS) {
    const m = t.match(re);
    if (m) matched.push({ category: 'risk', keyword: m[0] });
  }

  if (!matched.length) return { triggered: false, category: null, keywords: [] };

  // Priority: risk > compliance > opportunity
  const cats = matched.map(m => m.category);
  const category = cats.includes('risk') ? 'risk'
    : cats.includes('compliance') ? 'compliance'
    : 'opportunity';

  return {
    triggered: true,
    category,
    keywords: [...new Set(matched.map(m => m.keyword.toLowerCase()))],
  };
}

// ── Auto-response dedup: one response per keyword+chat per hour ─────────────
const autoRespondDedup = new Map();   // key → timestamp
const AUTO_RESPOND_COOLDOWN = 60 * 60 * 1000;  // 1 hour

function shouldAutoRespond(chatId, keywords) {
  const now = Date.now();
  // Check if ANY keyword in this chat was responded to recently
  for (const kw of keywords) {
    const key = `${chatId}:${kw}`;
    const last = autoRespondDedup.get(key);
    if (last && now - last < AUTO_RESPOND_COOLDOWN) return false;
  }
  // Mark all keywords as responded
  for (const kw of keywords) {
    autoRespondDedup.set(`${chatId}:${kw}`, now);
  }
  // Evict old entries periodically
  if (autoRespondDedup.size > 500) {
    for (const [k, ts] of autoRespondDedup) {
      if (now - ts > AUTO_RESPOND_COOLDOWN) autoRespondDedup.delete(k);
    }
  }
  return true;
}

// ── Internal API helpers ─────────────────────────────────────────────────────
async function brainPost(path, body) {
  const timeout = path.includes('/aria/') ? 90000 : 15000;
  const r = await fetch(`${BRAIN_URL}${path}`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${INT_TOKEN}` },
    body:    JSON.stringify(body),
    signal:  AbortSignal.timeout(timeout),
  });
  if (!r.ok) throw new Error(`POST ${path} → ${r.status}`);
  return r.json();
}

async function brainGet(path) {
  const r = await fetch(`${BRAIN_URL}${path}`, {
    headers: { 'Authorization': `Bearer ${INT_TOKEN}` },
    signal:  AbortSignal.timeout(10000),
  });
  if (!r.ok) throw new Error(`GET ${path} → ${r.status}`);
  return r.json();
}

// ── Ask ARIA with persistent per-sender sessions ────────────────────────────
async function askARIA(message, senderJid) {
  const sid = `wa_${senderJid.replace(/[^a-zA-Z0-9_]/g, '')}`;
  try {
    const r = await brainPost('/api/aria/chat', { message, session_id: sid });
    return r.response || r.answer || 'No response.';
  } catch (e) {
    console.error('[ARIA Listener] Chat failed:', e.message);
    return '⚠️ ARIA is temporarily unavailable.';
  }
}

// ── Split long messages into chunks for WhatsApp ────────────────────────────
const WA_MSG_LIMIT = 4000;

function splitMessage(body) {
  if (body.length <= WA_MSG_LIMIT) return [body];
  const chunks = [];
  let remaining = body;
  while (remaining.length > 0) {
    if (remaining.length <= WA_MSG_LIMIT) { chunks.push(remaining); break; }
    let cut = remaining.lastIndexOf('\n', WA_MSG_LIMIT);
    if (cut < WA_MSG_LIMIT * 0.3) cut = remaining.lastIndexOf(' ', WA_MSG_LIMIT);
    if (cut < WA_MSG_LIMIT * 0.3) cut = WA_MSG_LIMIT;
    chunks.push(remaining.slice(0, cut));
    remaining = remaining.slice(cut).replace(/^\n/, '');
  }
  return chunks;
}

async function sendReply(chatId, text) {
  if (!sock || !isConnected || !text) return;
  try {
    const chunks = splitMessage(text);
    for (let i = 0; i < chunks.length; i++) {
      if (i > 0) await new Promise(r => setTimeout(r, 500));
      await sock.sendMessage(chatId, { text: chunks[i] });
    }
  } catch (e) {
    console.error('[ARIA Listener] Reply failed:', e.message);
  }
}

// ── Compliance command handlers ─────────────────────────────────────────────
async function handleCommand(cmd, args, senderJid) {
  const a = (args || '').trim().slice(0, 500);

  switch (cmd.toLowerCase()) {
    case 'screen': {
      if (!a) return '⚠️ Usage: /screen [entity name]';
      const d = await brainPost('/api/aria/compliance/screen', { entity_name: a }).catch(() => ({}));
      const ok = d.result === 'PERMITTED';
      logComplianceAction({ type: 'SCREENING', user: senderJid, query: a, result: d, recommendation: ok ? 'PERMITTED' : 'BLOCKED' }).catch(() => {});
      let msg = `${ok ? '✅' : '⛔'} *COMPLIANCE SCREEN*\nEntity: ${a}\nResult: ${d.result || 'UNKNOWN'}\n\n`;
      Object.entries(d.screened_against || {}).forEach(([l, v]) => {
        msg += `  ✓ ${l}: ${v}\n`;
      });
      msg += ok
        ? '\n_Pre-screen only. Legal review required._'
        : '\n⛔ *MATCH FOUND. Do not proceed without legal review.*';
      return msg;
    }

    case 'classify': {
      if (!a) return '⚠️ Usage: /classify [product description]';
      const d = await brainPost('/api/aria/compliance/classify', { description: a }).catch(() => ({}));
      logComplianceAction({ type: 'CLASSIFICATION', user: senderJid, query: a, result: d, confidence: d.classifications?.[0]?.confidence ? `${(d.classifications[0].confidence * 100).toFixed(0)}%` : '' }).catch(() => {});
      let msg = `*ML CLASSIFICATION*\nProduct: ${a.slice(0, 80)}\n\n`;
      if (d.classifications?.length) {
        d.classifications.forEach(c => {
          msg += `• *${c.code || c.category}* — ${c.description || ''}\n`;
          if (c.confidence) msg += `  Confidence: ${(c.confidence * 100).toFixed(0)}%\n`;
          if (c.controlled) msg += `  ⚠️ Controlled item\n`;
        });
      } else {
        msg += `Result: ${d.result || d.category || 'No classification returned.'}\n`;
      }
      msg += '\n_Classification is advisory only. Verify with compliance team._';
      return msg;
    }

    case 'sanctions': {
      if (!a) return '⚠️ Usage: /sanctions [name]';
      const d = await brainPost('/api/aria/compliance/sanctions', { name: a }).catch(() => ({}));
      const hits = d.matches || d.results || [];
      logComplianceAction({ type: 'SANCTIONS_CHECK', user: senderJid, query: a, result: d, recommendation: hits.length ? 'MATCHES_FOUND' : 'CLEAR' }).catch(() => {});
      let msg = `*SANCTIONS CHECK*\nName: ${a}\n\n`;
      if (hits.length) {
        msg += `⛔ *${hits.length} match(es) found:*\n`;
        hits.slice(0, 5).forEach(h => {
          msg += `• *${h.name || h.entity}* — ${h.list || h.source || 'Unknown list'}\n`;
          if (h.score) msg += `  Match score: ${(h.score * 100).toFixed(0)}%\n`;
          if (h.reason) msg += `  ${h.reason}\n`;
        });
        msg += '\n⛔ *Do not proceed without legal review.*';
      } else {
        msg += '✅ No sanctions matches found.\n_Preliminary check. Full due diligence required._';
      }
      return msg;
    }

    case 'risk': {
      if (!a) return '⚠️ Usage: /risk [country]';
      const d = await brainPost('/api/aria/compliance/risk', { country: a }).catch(() => ({}));
      const level = d.risk_level || d.level || 'UNKNOWN';
      logComplianceAction({ type: 'RISK_ASSESSMENT', user: senderJid, query: a, result: d, recommendation: level }).catch(() => {});
      const emoji = { HIGH: '🔴', MEDIUM: '🟠', LOW: '🟢' }[level.toUpperCase()] || '⚪';
      let msg = `${emoji} *COUNTRY RISK — ${a.toUpperCase()}*\n\n`;
      msg += `Risk level: ${level}\n`;
      if (d.score) msg += `Score: ${d.score}/100\n`;
      if (d.sanctions_regimes?.length) msg += `Sanctions regimes: ${d.sanctions_regimes.join(', ')}\n`;
      if (d.embargoes?.length) msg += `Embargoes: ${d.embargoes.join(', ')}\n`;
      if (d.export_controls) msg += `Export controls: ${d.export_controls}\n`;
      if (d.notes) msg += `\n${d.notes}\n`;
      msg += '\n_Risk assessment is advisory. Consult compliance team._';
      return msg;
    }

    case 'ask': {
      if (!a) return '⚠️ Usage: /ask [question]';
      return await askARIA(a, senderJid);
    }

    case 'teach': {
      if (!a) return '⚠️ Usage: /teach [topic]: [fact]';
      const colonIdx = a.indexOf(':');
      if (colonIdx < 1) return '⚠️ Format: /teach [topic]: [fact]\nExample: /teach ECJU processing: Standard SITCL takes 20 working days';
      const topic = a.slice(0, colonIdx).trim();
      const fact  = a.slice(colonIdx + 1).trim();
      if (!fact) return '⚠️ Please include the fact after the colon.';
      const senderDisplay = senderJid.replace('@s.whatsapp.net', '').replace('@g.us', '');
      try {
        await brainPost('/api/aria/knowledge/fact', {
          topic,
          content: fact,
          source: `taught_by:${senderDisplay}`,
          confidence: 'CONFIRMED',
        });
        return `✅ *Learned!*\n*Topic:* ${topic}\n*Fact:* ${fact}\n*Source:* ${senderDisplay}\n\nThis is now in my knowledge base as [CONFIRMED]. Thank you for teaching me.`;
      } catch (e) {
        return '⚠️ Failed to store — ARIA brain may be unavailable.';
      }
    }

    case 'correct': {
      if (!a) return '⚠️ Usage: /correct [what ARIA got wrong] → [the right answer]';
      const sep = a.includes('→') ? '→' : a.includes('->') ? '->' : null;
      if (!sep) return '⚠️ Format: /correct [wrong] → [right]\nUse → or -> to separate the error from the correction.';
      const parts = a.split(sep);
      const wrong = parts[0].trim();
      const right = parts.slice(1).join(sep).trim();
      if (!wrong || !right) return '⚠️ Both the error and correction are needed.\nExample: /correct ECJU takes 10 days → Standard SITCL takes 20 working days';
      try {
        await brainPost('/api/aria/correct', {
          originalQuery: wrong,
          originalResponse: wrong,
          correction: `User correction: ${wrong} is wrong. Correct answer: ${right}`,
          correctAnswer: right,
        });
        // Also store the correct fact in knowledge base
        await brainPost('/api/aria/knowledge/fact', {
          topic: wrong.slice(0, 60),
          content: right,
          source: `correction_by:${senderJid.replace('@s.whatsapp.net', '')}`,
          confidence: 'CONFIRMED',
        }).catch(() => {});
        return `✅ *Correction recorded.*\n*Was:* ${wrong}\n*Should be:* ${right}\n\nI've updated my knowledge and recorded this as a training correction. Thank you — this makes me better.`;
      } catch (e) {
        return '⚠️ Failed to record correction — ARIA brain may be unavailable.';
      }
    }

    case 'feedback': {
      if (!a) return '⚠️ Usage: /feedback [+/-] [notes]\nExample: /feedback + Great analysis of Angola procurement';
      const positive = a.startsWith('+') || /^positive/i.test(a);
      const negative = a.startsWith('-') || /^negative/i.test(a);
      const notes = a.replace(/^[+-]\s*/, '').replace(/^(positive|negative)\s*/i, '').trim();
      const sentiment = positive ? 'positive' : negative ? 'negative' : 'neutral';
      try {
        await brainPost('/api/brain/signal', {
          content: `Feedback (${sentiment}): ${notes || 'No notes'}`,
          source: `feedback:${senderJid.replace('@s.whatsapp.net', '')}`,
          signal_type: 'user_feedback',
          metadata: { sentiment, notes, sender: senderJid, channel: 'whatsapp_listener' },
        });
        const emoji = positive ? '👍' : negative ? '📝' : '📋';
        return `${emoji} *Feedback recorded.* ${positive ? 'Glad I could help!' : negative ? 'I\'ll work on improving.' : 'Thank you for the feedback.'}`;
      } catch (e) {
        return '⚠️ Failed to record feedback.';
      }
    }

    case 'groupsummary': {
      // Summarise the last 50 messages in this group
      const groupMsgs = messageStore
        .filter(m => m.groupId === (a || '__current__'))
        .slice(-50);
      // If no groupId arg, caller will replace '__current__' with actual chatId
      // This is handled in the message handler below
      if (!groupMsgs.length) return '⚠️ No recent messages stored for this group. I need to listen for a while first.';
      const transcript = groupMsgs
        .map(m => `[${m.senderName}] ${m.text.slice(0, 200)}`)
        .join('\n');
      const prompt = `Here are the last ${groupMsgs.length} messages from the WhatsApp group "${groupMsgs[0]?.groupName || 'Unknown'}":\n\n${transcript}\n\nProvide a concise group summary:\n1. Key topics discussed\n2. Decisions made or pending\n3. Action items mentioned\n4. Any compliance, risk, or regulatory mentions (flag these clearly)\n\nKeep it under 500 words. Use bullet points.`;
      return await askARIA(prompt, senderJid);
    }

    case 'leads': {
      const d = await brainGet('/api/brain/brief').catch(() => ({}));
      const leads = (d.top_leads || []).slice(0, 5);
      if (!leads.length) return 'ℹ️ No active leads. Run /hunt to generate.';
      let msg = `🎯 *LATEST LEADS*\n\n`;
      leads.forEach((l, i) => {
        const e = l.urgency === 'HIGH' ? '🔴' : l.urgency === 'MEDIUM' ? '🟠' : '🟡';
        msg += `${e} *${i+1}. ${l.market || '?'}*\n`;
        msg += `${(l.signal_title || '').slice(0, 80)}\n`;
        if (l.win_probability) msg += `Win: ${((l.win_probability||0)*100).toFixed(0)}%\n`;
        msg += '\n';
      });
      return msg;
    }

    case 'ideas': {
      const r = await brainPost('/api/aria/proactive/strategic-ideas', {}).catch(() => ({}));
      return r?.ideas || '⚠️ Could not generate strategic ideas — ARIA brain may be unavailable.';
    }

    case 'hunt': {
      const r = await brainPost('/api/aria/proactive/lead-hunt', {}).catch(() => ({}));
      return r?.leads || '⚠️ Could not run lead hunt — ARIA brain may be unavailable.';
    }

    case 'help':
      return [
        '*ARIA — WhatsApp Commands*',
        '',
        '*Intelligence:*',
        '/ask [question] — Ask ARIA anything',
        '/brief — Today\'s intelligence digest',
        '/groupsummary — Summarise last 50 group messages',
        '',
        '*Business Development:*',
        '/leads — Latest 5 generated leads',
        '/hunt — Trigger lead hunting cycle',
        '/ideas — Generate strategic ideas',
        '',
        '*Compliance:*',
        '/screen [entity] — Compliance pre-screening',
        '/classify [product] — ML classification',
        '/sanctions [name] — Sanctions list check',
        '/risk [country] — Country risk assessment',
        '',
        '*Learning:*',
        '/teach [topic]: [fact] — Teach ARIA a new fact',
        '/correct [wrong] → [right] — Correct ARIA',
        '/feedback [+/-] [notes] — Rate last response',
        '',
        '*Pipeline:*',
        '/pipeline — View active deals',
        '/deal [title] — Add new deal',
        '',
        '_Or just mention ARIA in any message to chat._',
      ].join('\n');

    default:
      return null;
  }
}

// ── Trigger detection for Baileys listener ───────────────────────────────────
const MENTIONS_RE  = [/\baria\b/i, /@aria/i, /^aria[,:]/i];
const COMMAND_RE   = /^\/(\w+)(.*)/s;

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
      reconnectDelay = 5000;  // reset backoff on successful connect
      console.log('[ARIA Listener] ✓ Connected to WhatsApp — ARIA is listening');
      console.log('[ARIA Listener] Call GET /groups to find your group IDs');
    }

    if (connection === 'close') {
      isConnected = false;
      qrPrinted   = false;  // allow new QR display on reconnect
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

      // Get sender info
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

      // ── Media processing — IMAGES + DOCUMENTS ────────────────────────────
      // Two separate paths because images need OCR (vision) and documents
      // need parsing. Previously these were lumped together and images were
      // sent to /api/aria/read-document which expects PDF/DOCX content,
      // not image bytes — every image was silently dropped.
      const docMsg = msg.message?.documentMessage;
      const imgMsg = msg.message?.imageMessage;

      // ── IMAGE PATH: download → /api/aria/ocr → reply with extraction ──
      if (imgMsg) {
        const caption = imgMsg.caption || '';
        console.log(`[ARIA Listener] Image shared in ${groupName} by ${senderName}${caption ? ` "${caption.slice(0,60)}"` : ' (no caption)'}`);

        // Immediate ack so the group sees ARIA working
        await sendReply(chatId, `📥 Got your image. Reading now…`).catch(() => {});

        try {
          const stream = await sock.downloadMediaMessage(msg);
          const buffer = Buffer.isBuffer(stream) ? stream : Buffer.concat(await (async () => {
            const chunks = []; for await (const c of stream) chunks.push(c); return chunks;
          })());

          if (!buffer || buffer.length === 0) {
            await sendReply(chatId, `⚠️ The image appears to be empty.`).catch(() => {});
          } else {
            // Cap at 8MB and slice BYTES before base64
            const MAX_BYTES = 8 * 1024 * 1024;
            const buf = buffer.length > MAX_BYTES ? buffer.subarray(0, MAX_BYTES) : buffer;
            const b64 = buf.toString('base64');
            const sizeKb = Math.round(buffer.length / 102.4) / 10;
            const filename = `wa_${Date.now()}.jpg`;
            const contextLabel = caption
              ? `Image shared in WhatsApp group "${groupName}" by ${senderName}. Caption: ${caption.slice(0, 300)}`
              : `Image shared in WhatsApp group "${groupName}" by ${senderName} (no caption)`;

            console.log(`[ARIA Listener] OCR request: ${filename} (${sizeKb} KB)`);

            let ocrResult = null;
            let ocrConnectError = null;
            try {
              ocrResult = await brainPost('/api/aria/ocr', {
                image: b64,
                filename,
                context: contextLabel,
              });
            } catch (e) {
              console.warn('[ARIA Listener] OCR call failed:', e.message);
              ocrConnectError = e.message;
            }

            // If the OCR endpoint itself failed (502/504/network), tell the
            // user it's an infrastructure issue not an OCR pipeline failure.
            if (ocrConnectError) {
              await sendReply(chatId, [
                `🛑 *I couldn't reach my OCR service.*`,
                ``,
                `The image is fine, but my Python intelligence service didn't respond:`,
                `\`${ocrConnectError}\``,
                ``,
                `*Check:*`,
                `• ARIA Python service is running (\`flyctl status -a <app>\`)`,
                `• \`BRAIN_SERVICE_URL\` env var points to the live service`,
                `• Network/firewall allows wa-listener → ARIA traffic`,
                `• \`flyctl logs -a <aria-service>\` for crashes`,
                ``,
                `Once the service is back, send the image again — the OCR pipeline itself is working.`,
              ].join('\n')).catch(() => {});
              continue;
            }

            const extracted = (ocrResult?.text || '').trim();
            const autoInst = ocrResult?.auto_installing;

            if (!extracted) {
              // OCR pipeline returned nothing usable. Surface the FULL trace
              // so we can see which backends were tried + why they failed —
              // no need to access fly.io / seenode logs to debug.
              const triedList = ocrResult?.tried || [];
              const note = ocrResult?.note || '';
              const lastMethod = ocrResult?.method || 'none';
              const errorDetail = ocrResult?.error || '';
              const triedLine = triedList.length
                ? `\n_Backends tried (in order):_ ${triedList.join(' → ')}`
                : '';
              const lastLine = lastMethod && lastMethod !== 'none'
                ? `\n_Last method that returned anything:_ \`${lastMethod}\``
                : '';
              const errorLine = errorDetail ? `\n_Error:_ ${errorDetail}` : '';
              await sendReply(chatId, [
                `🖼 *I tried to read the image but the OCR pipeline returned no text.*`,
                ``,
                `The image looks fine to me visually, so this is most likely an infrastructure issue. Diagnostic trace:`,
                triedLine,
                lastLine,
                errorLine,
                note ? `\n_Note:_ ${note}` : '',
                autoInst ? `\n_Background install of local OCR is running — try again in 60s._` : ``,
                ``,
                `_Run */vision-status* for full backend diagnostics._`,
              ].filter(Boolean).join('\n')).catch(() => {});
            } else {
              const method = ocrResult.method || 'vision';
              const charCount = extracted.length;
              console.log(`[ARIA Listener] OCR ${method}: ${charCount} chars${autoInst ? ' (background install triggered)' : ''}`);

              // Feed extraction to the knowledge pipeline so ARIA learns
              let factsLearned = 0;
              try {
                const dr = await brainPost('/api/aria/read-document', {
                  content: extracted.slice(0, MAX_DOC_CHARS),
                  filename,
                  source: `whatsapp_group:${groupName}:${senderName}`,
                  context: caption || `Image OCR from ${groupName}`,
                  encoding: 'utf-8',
                  mimetype: 'text/plain',
                }).catch(() => null);
                factsLearned = dr?.facts_learned || 0;
              } catch (e) {
                console.warn('[ARIA Listener] Image-to-knowledge ingest failed:', e.message);
              }

              // Build the reply — friendly method label
              const preview = extracted.slice(0, 700).replace(/\n{3,}/g, '\n\n');
              const more = extracted.length > 700 ? `\n\n_…+${extracted.length - 700} more chars_` : '';
              const factsLine = factsLearned > 0 ? `\n\n📚 Learned ${factsLearned} new fact(s) — ask me about them.` : '';
              const methodLabel = {
                easyocr:        '🟢 local (EasyOCR)',
                tesseract:      '🟢 local (Tesseract)',
                ocrspace_free:  '🟡 free public OCR — installing local backend now…',
              }[method] || (method.startsWith('ollama:') ? `🟢 local (${method})`
                          : method.startsWith('vision:') ? `☁️ ${method}`
                          : method);
              const installNote = autoInst
                ? `\n\n_⚙️ Auto-installing local image-reading library in the background — your next image will be ~10x faster and fully offline._`
                : '';

              // ── Send the OCR extraction first so the user sees what ARIA read ─
              await sendReply(chatId, `🖼 *Image read* (${methodLabel}, ${charCount} chars):\n\n${preview}${more}${factsLine}${installNote}`).catch(() => {});

              // ── ALWAYS analyse + explain + research after extraction ──────
              // The "extract → explain → research" pattern. ARIA doesn't just
              // dump OCR text — she identifies the document, pulls entities,
              // screens for compliance, and answers any caption question.
              const captionTrimmed = (caption || '').trim();
              const userInstruction = captionTrimmed.length >= 3
                ? `The user attached this caption / instruction: "${captionTrimmed}"`
                : `The user shared the image with no caption — they expect a senior analyst's read.`;

              const analysisPrompt = [
                `An image was just shared in the WhatsApp group "${groupName}" by ${senderName}. I have extracted its text via OCR. ${userInstruction}`,
                ``,
                `Your task — produce a concise intelligence brief on what this image contains:`,
                ``,
                `1. *Document type* — what is this? (invoice / contract / tender notice / business card / screenshot / news article / chart / other)`,
                `2. *Key entities* — companies, people (with roles), countries, military units, products, contract IDs, dates, monetary values`,
                `3. *Compliance flags* — any sanctions, export control, ML category, or embargo concerns`,
                `4. *Arkmurus relevance* — does this touch a market we cover, an OEM we work with, or a contact we know? Cite the relationship tier.`,
                `5. *Recommended next action* — what should the team do with this information? (investigate further, screen entity, contact source, file in pipeline, ignore)`,
                captionTrimmed ? `6. *Direct answer to the user's caption* — answer "${captionTrimmed.slice(0, 200)}" specifically.` : ``,
                ``,
                `[OCR extracted text — ${charCount} chars via ${method}]:`,
                `${extracted.slice(0, 4500)}`,
                ``,
                `Be specific. Cite numbers and names from the extracted text. Mark every claim with confidence: [CONFIRMED] [PROBABLE] [ASSESSED] [UNCERTAIN].`,
              ].filter(Boolean).join('\n');

              await sendReply(chatId, `🔎 _Analysing the image content${captionTrimmed ? ` and answering: "${captionTrimmed.slice(0, 100)}"` : ''}…_`).catch(() => {});

              try {
                const analysis = await askARIA(analysisPrompt, senderJid);
                if (analysis) {
                  await sendReply(chatId, `🧠 *Analysis:*\n\n${analysis}`).catch(() => {});
                }
              } catch (e) {
                console.warn('[ARIA Listener] Image-analysis chat failed:', e.message);
                await sendReply(chatId, `⚠️ I extracted the image but my reasoning step failed: ${e.message}`).catch(() => {});
              }
            }
          }
        } catch (e) {
          console.warn('[ARIA Listener] Image processing failed:', e.message);
          await sendReply(chatId, `⚠️ Image processing error: ${e.message}`).catch(() => {});
        }
      }

      // ── DOCUMENT PATH: PDF / DOCX / Excel / TXT / CSV ─────────────────
      if (docMsg) {
        const filename = docMsg.fileName || 'attachment';
        const mimetype = docMsg.mimetype || '';
        const isProcessable = /pdf|word|spreadsheet|text|csv|octet-stream|msword|officedocument/.test(mimetype);
        if (isProcessable) {
          console.log(`[ARIA Listener] Processing document: ${filename} (${mimetype})`);
          try {
            const stream = await sock.downloadMediaMessage(msg);
            const buffer = Buffer.isBuffer(stream) ? stream : Buffer.concat(await (async () => {
              const chunks = []; for await (const c of stream) chunks.push(c); return chunks;
            })());
            // Slice BYTES (not base64 string!) to avoid mid-character truncation
            const MAX_BYTES = 8 * 1024 * 1024;
            const buf = buffer.length > MAX_BYTES ? buffer.subarray(0, MAX_BYTES) : buffer;
            const docType = mimetype.split('/')[1] || 'document';
            const isBinary = /pdf|word|spreadsheet|octet-stream|msword|officedocument/.test(mimetype);
            const content = isBinary
              ? buf.toString('base64')                  // FULL base64 of byte-sliced buffer
              : buf.toString('utf-8').slice(0, MAX_DOC_CHARS);
            if (content.length > 50) {
              const result = await brainPost('/api/aria/read-document', {
                content,
                filename,
                source: `whatsapp_group:${groupName}:${senderName}`,
                context: text || `Document from ${senderName} in ${groupName}`,
                encoding: isBinary ? 'base64' : 'utf-8',
                mimetype,
              }).catch(() => null);
              if (result) {
                const summary = result.summary || `${docType} file, ${content.length} characters`;
                console.log(`[ARIA Listener] Doc processed: ${filename} → ${result.facts_learned || 0} facts`);
                await sendReply(chatId, `📄 I've read *${filename}*. ${summary}\n\nAsk me anything about it.`);
              }
            }
          } catch (e) {
            console.warn('[ARIA Listener] Document processing failed:', e.message);
          }
        }
      }

      if (!text.trim()) continue;   // skip text routing for media-only messages

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

      // ── Command handling ────────────────────────────────────────────────────
      const cmdMatch = text.match(COMMAND_RE);
      if (cmdMatch) {
        const cmd  = cmdMatch[1];
        // For /groupsummary, pass chatId as the argument so it filters correctly
        const args = cmd.toLowerCase() === 'groupsummary'
          ? chatId
          : (cmdMatch[2] || '').trim();
        try {
          let response = await handleCommand(cmd, args, senderJid);
          if (response === null) {
            // Unknown command — ask ARIA
            response = await askARIA(text, senderJid);
          }
          if (response) await sendReply(chatId, response);
        } catch (e) {
          console.error('[ARIA Listener] Command error:', e.message);
          await sendReply(chatId, '⚠️ Something went wrong. Try /help.');
        }
        continue;
      }

      // ── Mention handling — respond when ARIA is mentioned ──────────────────
      if (MENTIONS_RE.some(p => p.test(text))) {
        const q = text.replace(/^@?aria[,:?\s]*/i, '').trim() || text;
        try {
          const response = await askARIA(q, senderJid);
          if (response) await sendReply(chatId, response);
        } catch (e) {
          console.error('[ARIA Listener] Mention reply error:', e.message);
        }
        continue;
      }

      // ── Smart auto-response — trigger on compliance/opportunity/risk keywords
      if (AUTO_RESPOND) {
        const trigger = detectComplianceTrigger(text);
        if (trigger.triggered && shouldAutoRespond(chatId, trigger.keywords)) {
          const categoryLabel = {
            compliance: 'compliance/export control',
            opportunity: 'business development/procurement',
            risk: 'risk/diversion concern',
          }[trigger.category] || trigger.category;

          const prompt = `A team member said: "${text.slice(0, 800)}"\n\nProvide a brief (under 300 words) intelligence note relevant to this. Focus on ${categoryLabel} implications. Be specific and actionable. Keywords detected: ${trigger.keywords.join(', ')}`;

          try {
            let response = await askARIA(prompt, `auto_${chatId}`);
            if (response) {
              // Enforce 500 char limit and add prefix
              response = response.slice(0, 480);
              response = `_ARIA noticed:_ ${response}`;
              await sendReply(chatId, response);
              console.log(`[ARIA Listener] Auto-response (${trigger.category}): ${trigger.keywords.join(', ')}`);
            }
          } catch (e) {
            console.error('[ARIA Listener] Auto-response error:', e.message);
          }
        }
      }
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

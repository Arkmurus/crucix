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
  // R-F867 — downloadMediaMessage is a STANDALONE export, NOT a socket method.
  // Pre-R-F867 it was invoked as a method on the socket, which threw
  // "not a function" on EVERY document/image → no upload ever downloaded
  // (the 7 failed contract attempts).
  downloadMediaMessage,
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
const BRAIN_URL     = process.env.BRAIN_SERVICE_URL || process.env.ARIA_SERVICE_URL || 'http://localhost:8000';
const INT_TOKEN     = process.env.ARIA_INTERNAL_TOKEN    || 'aria-internal';
const REDIS_URL     = process.env.REDIS_URL              || '';
const AUTO_RESPOND  = (process.env.WA_LISTENER_AUTO_RESPOND || 'true').toLowerCase() === 'true';
// R-F963 (2026-05-28, operator choice) — a voice note is a deliberate act aimed
// at ARIA, but STT keeps dropping/garbling the short leading "Aria" wake-word on
// accented speech, so name-only mode left voice notes unanswered (live 12:51,
// 13:17, 13:48). When on, ANY voice note is treated as an implicit mention →
// routed to ARIA (incl. R-F912 doc re-attach) regardless of the transcript.
// Set ARIA_VOICE_ALWAYS_REPLY=false to revert to wake-word-required for voice.
const VOICE_ALWAYS_REPLY = (process.env.ARIA_VOICE_ALWAYS_REPLY || 'true').toLowerCase() === 'true';
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

// R-F1152 — message dedup set. Baileys can fire the same message twice on
// reconnect. We track message keys (chatId + sender + timestamp) for 60s.
const _seenMessageKeys = new Map();   // key → timestamp
const _MSG_DEDUP_TTL_MS = 60000;

function _isDuplicateMessage(chatId, senderJid, msgTimestamp) {
  const key = `${chatId}:${senderJid}:${msgTimestamp}`;
  const now = Date.now();
  if (_seenMessageKeys.has(key)) return true;
  _seenMessageKeys.set(key, now);
  // Evict stale entries
  if (_seenMessageKeys.size > 1000) {
    for (const [k, ts] of _seenMessageKeys) {
      if (now - ts > _MSG_DEDUP_TTL_MS) _seenMessageKeys.delete(k);
    }
  }
  return false;
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

// ── R-F854 (2026-05-24) — per-sender recent-document cache ──────────────────
// The document path POSTs to /api/aria/read-document (the brain absorbs facts)
// but does NOT keep the extracted text locally. So a follow-up MENTION
// ("Aria, analyse this contract") reached /api/aria/chat with no
// [ATTACHED DOCUMENT] block, and ARIA honestly reported "no document in my
// context" — the recurring 2026-05-24 contract-review failure. Cache the
// extracted text on read, re-attach it on a doc-referencing follow-up.
// (R-F853 fixed the LEGACY lib/whatsapp/waListener.mjs by mistake; this file —
// services/wa-listener/aria_wa_listener.mjs — is the canonical aria-wa entry.)
const _recentDocs = new Map();                 // chatId → [{filename,text,ts,sender}] (most-recent last)
// R-F964 (2026-05-28) — was 1h. Operator: "she should not forget anything." A
// doc follow-up often comes hours after upload, so default 24h (env-tunable).
// The cache is ALSO persisted to disk (below) so a listener restart no longer
// wipes it — today a redeploy erased a contract uploaded 59 min earlier, so the
// review lost the document and the footer read "from memory / training".
const _RECENT_DOC_TTL_MS = parseInt(process.env.ARIA_RECENT_DOC_TTL_MS || String(24 * 60 * 60 * 1000), 10);
const _MAX_DOCS_PER_CHAT = 6;                  // R-F912 — keep several recent docs, not just one
const _RECENT_DOCS_FILE = process.env.ARIA_RECENT_DOCS_FILE || '/data/recent_docs.json';
export const _DOC_REF_PATTERN = /\b(contract|agreement|nda|mou|rfq|tender|document|annex|appendix|clause|terms|paperwork|the\s+file|the\s+pdf|attachment|payment)\b/i;
// R-F912 — collective/plural reference → the follow-up wants ALL recent docs
// ("analyse all contracts", "both agreements", "review the documents").
export const _MULTI_DOC_PATTERN = /\b(all|both|each|every|these|those|three|several|multiple|contracts|agreements|documents|files|paperwork)\b/i;

function _pruneChatDocs(list) {
  const cutoff = Date.now() - _RECENT_DOC_TTL_MS;
  return list.filter(d => d.ts >= cutoff).slice(-_MAX_DOCS_PER_CHAT);
}

// R-F912 — cache recent docs per CHAT as a LIST, not one slot per
// (chat,sender). Two live failures 2026-05-26: (1) three uploads overwrote to
// a single entry so "analyse all contracts" saw only the last; (2) in a GROUP
// the uploader (Antonio) and the questioner (Ari) differ, so the old
// sender-keyed lookup missed the doc entirely. Chat-scoped + multi-doc fixes
// both. (R-F854 introduced the per-sender cache; this generalises it.)
function _cacheRecentDoc(chatId, senderName, filename, text) {
  if (!text || text.length < 200) return;      // ignore placeholders / failed parses
  const fname = filename || 'document';
  const list = _recentDocs.get(chatId) || [];
  const idx = list.findIndex(d => d.filename === fname);   // re-read replaces same file
  const entry = { filename: fname, text, ts: Date.now(), sender: senderName || 'someone' };
  if (idx >= 0) list[idx] = entry; else list.push(entry);
  _recentDocs.set(chatId, _pruneChatDocs(list));
  _persistRecentDocs();                          // R-F964 — survive restarts
}

// R-F964 — persist the recent-doc cache to the aria-wa volume so a listener
// restart (deploy, crash, watchdog) no longer makes ARIA "forget" a document
// the operator shared minutes/hours earlier. Best-effort: any failure is logged
// and ignored — the cache simply falls back to in-memory-only for that write.
function _persistRecentDocs() {
  try {
    fs.writeFileSync(_RECENT_DOCS_FILE, JSON.stringify([..._recentDocs.entries()]));
  } catch (e) {
    console.warn('[ARIA Listener] R-F964 doc-cache save failed:', e.message);
  }
}

function _loadRecentDocs() {
  try {
    const arr = JSON.parse(fs.readFileSync(_RECENT_DOCS_FILE, 'utf-8'));
    let restored = 0;
    for (const [chatId, list] of arr) {
      const pruned = _pruneChatDocs(Array.isArray(list) ? list : []);   // drop expired on load
      if (pruned.length) { _recentDocs.set(chatId, pruned); restored++; }
    }
    if (restored) console.log(`[ARIA Listener] R-F964 restored doc cache for ${restored} chat(s) from ${_RECENT_DOCS_FILE}`);
  } catch (e) {
    if (e.code !== 'ENOENT') console.warn('[ARIA Listener] R-F964 doc-cache load failed:', e.message);
  }
}

// Returns an ARRAY of cached docs relevant to `question` (most-recent last), or
// [] when none. Group-chat aware: ANY member's recent doc in this chat is
// eligible (uploader and questioner are often different people). A plural/
// collective reference returns ALL recent docs; a filename mention returns that
// doc; otherwise the single most-recent.
function _recentDocsForFollowup(chatId, question) {
  if (!question || !_DOC_REF_PATTERN.test(question)) return [];
  const list = _pruneChatDocs(_recentDocs.get(chatId) || []);
  if (list.length === 0) { _recentDocs.delete(chatId); return []; }
  _recentDocs.set(chatId, list);                           // persist the prune
  if (list.length === 1) return list;
  if (_MULTI_DOC_PATTERN.test(question)) return list;      // wants all
  const ql = question.toLowerCase();
  const matched = list.filter(d =>
    d.filename.replace(/\.[a-z0-9]+$/i, '').split(/[\s_\-]+/)
      .some(w => w.length >= 4 && ql.includes(w.toLowerCase())));
  return matched.length ? matched : [list[list.length - 1]];
}

// ── Handle OCR result — shared by sync + async image paths ────────────────────
// R-F1311: extracted from the inline image-processing block so both the sync
// fallback and the async job+poll path use the same analysis pipeline.
async function _handleOcrResult(extracted, ocrResult, filename, caption, groupName, senderName, senderJid, chatId) {
  const method = ocrResult.method || 'vision';
  const charCount = extracted.length;
  const autoInst = ocrResult?.auto_installing;
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

  // Send the OCR extraction first so the user sees what ARIA read
  await sendReply(chatId, `🖼 *Image read* (${methodLabel}, ${charCount} chars):\n\n${preview}${more}${factsLine}${installNote}`).catch(() => {});

  // ALWAYS analyse + explain + research after extraction
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
    // R-F1321: raised caption preview from 200 to 2000 so the LLM sees the
    // user's full instruction, not just the first sentence.
    captionTrimmed ? `6. *Direct answer to the user's caption* — answer "${captionTrimmed.slice(0, 2000)}" specifically.` : ``,
    ``,
    `[OCR extracted text — ${charCount} chars via ${method}]:`,
    // R-F1321: removed 4500-char cap — send the FULL extracted text so the LLM
    // analyses the entire document, not just the first page. The model's context
    // window is the real bound, not an arbitrary slice.
    `${extracted.slice(0, MAX_DOC_CHARS)}`,
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

// ── Feed message to ARIA brain ─────────────────────────────────────────────────
// R-F1151 — emit wa_feed_failed signal when the brain is unreachable, so ARIA
// learns that her WA feed is broken (was dark: the error was silently swallowed).
async function feedToARIA(groupName, senderName, text) {
  try {
    await fetch(`${BRAIN_URL}/api/aria/brain/signal`, {   // R-F887 — was /api/brain/signal (404, no such router)
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
    console.warn('[ARIA Listener] feedToARIA failed (brain unreachable):', e.message);
    // R-F1151 — emit failure signal so ARIA's brain learns the feed is broken
    try {
      fetch(`${BRAIN_URL}/api/aria/brain/signal`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${INT_TOKEN}` },
        body: JSON.stringify({
          content: `WA feed failed for ${groupName}/${senderName}: ${e.message}`,
          source: 'aria-wa',
          signal_type: 'wa_feed_failed',
          metadata: { group: groupName, sender: senderName, error: String(e.message || '').slice(0, 200) },
        }),
        signal: AbortSignal.timeout(3000),
      }).catch(() => {});   // best-effort — the brain may be the thing that's down
    } catch { /* never let observability break the message path */ }
  }
}

// R-F1152 — per-chat rate limit for auto-responses. Prevents a document paste
// with many compliance keywords from firing N LLM calls in rapid succession.
const _autoRespondRateLimit = new Map();   // chatId → last response timestamp
const AUTO_RESPOND_RATE_LIMIT_MS = 120000;  // 2 min between auto-responses per chat

function _checkAutoRespondRateLimit(chatId) {
  const now = Date.now();
  const last = _autoRespondRateLimit.get(chatId);
  if (last && now - last < AUTO_RESPOND_RATE_LIMIT_MS) return false;
  _autoRespondRateLimit.set(chatId, now);
  // Evict old entries periodically
  if (_autoRespondRateLimit.size > 200) {
    for (const [k, ts] of _autoRespondRateLimit) {
      if (now - ts > AUTO_RESPOND_RATE_LIMIT_MS * 2) _autoRespondRateLimit.delete(k);
    }
  }
  return true;
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
  // R-F1321: removed 2000-char cap — ARIA sees the FULL message for trigger detection
  const t = text;
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
let _dedupEvictTimer = null;          // R-F1152 — periodic eviction timer

// R-F1152 — run eviction on a timer so quiet groups don't get permanently
// blocked (previously only ran on insert when size > 500).
function _evictStaleDedupEntries() {
  const now = Date.now();
  let evicted = 0;
  for (const [k, ts] of autoRespondDedup) {
    if (now - ts > AUTO_RESPOND_COOLDOWN) { autoRespondDedup.delete(k); evicted++; }
  }
  if (evicted) console.log(`[ARIA Listener] Evicted ${evicted} stale dedup entries (${autoRespondDedup.size} remaining)`);
}

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
  // R-F1152 — periodic eviction every 5 min (start on first call)
  if (!_dedupEvictTimer) {
    _dedupEvictTimer = setInterval(_evictStaleDedupEntries, 5 * 60 * 1000);
  }
  return true;
}

// ── Internal API helpers ─────────────────────────────────────────────────────
// R-F1153 — classify HTTP status into a structured error category so callers
// can distinguish "fix the token" from "try again later".
function _classifyBrainError(status, path) {
  if (status === 401 || status === 403) return { type: 'auth', retryable: false, msg: `Auth failure (${status}) — check ARIA_INTERNAL_TOKEN` };
  if (status === 429) return { type: 'rate_limit', retryable: true, msg: `Rate limited (429) on ${path}` };
  if (status >= 500) return { type: 'server', retryable: true, msg: `Brain error (${status}) on ${path}` };
  return { type: 'unknown', retryable: status >= 400, msg: `HTTP ${status} on ${path}` };
}

async function brainPost(path, body) {
  // R-F960 — /transcribe needs a long ceiling: a long voice note decoded by the
  // 'small' model with beam-search (beam_size=5) is several× slower than the old
  // base/greedy config, and the very first note after a redeploy also pays the
  // bigger model's cold-load from /data. 300s keeps long, accented notes from
  // aborting mid-decode. (Checked before '/aria/' — /api/aria/transcribe matches both.)
  const timeout = path.includes('/transcribe') ? 300000
                : path.includes('/aria/')       ? 90000
                :                                 15000;
  const r = await fetch(`${BRAIN_URL}${path}`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${INT_TOKEN}` },
    body:    JSON.stringify(body),
    signal:  AbortSignal.timeout(timeout),
  });
  if (!r.ok) {
    const cls = _classifyBrainError(r.status, path);
    const err = new Error(cls.msg);
    err.status = r.status;
    err.errorType = cls.type;
    err.retryable = cls.retryable;
    throw err;
  }
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

// R-F873 — full-document reads run as a BACKGROUND JOB on the brain so a large /
// scanned contract (multi-page OCR — e.g. the Forcados SPA / MT199 / DLC MT700)
// reads to completion even when the autonomous absorb storm slows the event
// loop. The 80s sync cap (R-F869) 504'd on exactly that document. We POST
// async:true, get a job_id immediately, then poll the result endpoint — this
// path has NO server-side cap, so the wedge can only slow it, never time it out.
// Returns the read-document result dict on success; throws on failure/timeout;
// falls back to a legacy sync result if the brain is an older build.
async function readDocumentAsync(payload, chatId, filename) {
  const job = await brainPost('/api/aria/read-document', { ...payload, async: true });
  const jobId = job && job.job_id;
  if (!jobId) {
    // Older brain build without async support — it returned the sync result.
    return job && job.result ? job.result : (job || null);
  }
  await sendReply(chatId,
    `📥 Reading *${filename}* — a large or scanned document takes a minute. `
    + `I'll send the overview as soon as it's ready.`).catch(() => {});
  const POLL_MS = 5000, MAX_POLLS = 180;   // R-F1056: 5s x 180 = up to 15 minutes (was 10) × 120 = up to 10 minutes (was 8)
  // R-F880 — 8-min window (was 4): with document_intelligence deferred server-
  // side, a text-layer doc resolves in seconds, but a LENGTHY SCANNED PDF still
  // needs full multi-page OCR (no shortcuts) which can run several minutes. The
  // job has no server cap; this just bounds how long the listener waits.
  // R-F1152 — brain-health check every 30s so we don't poll for 15 min if brain crashed
  // R-F1325 — tolerate transient blips: only abort after 3 CONSECUTIVE failures (~90s)
  let lastHealthCheck = 0;
  let docHealthFails = 0;
  for (let i = 0; i < MAX_POLLS; i++) {
    await new Promise(r => setTimeout(r, POLL_MS));
    // R-F1152 — abort early if brain is down
    // R-F1325 — tolerate transient blips: only abort after 3 CONSECUTIVE failures (~90s)
    if (Date.now() - lastHealthCheck > 30000) {
      lastHealthCheck = Date.now();
      try {
        const hc = await fetch(`${BRAIN_URL}/health/live`, { signal: AbortSignal.timeout(8000) });
        if (!hc.ok) throw new Error(`health returned ${hc.status}`);
        docHealthFails = 0;  // reset on success
      } catch {
        docHealthFails = (docHealthFails || 0) + 1;
        if (docHealthFails >= 3) {
          console.warn(`[ARIA Listener] Brain unreachable for ${docHealthFails} consecutive checks — aborting doc poll`);
          throw new Error('brain unreachable during doc poll');
        }
        console.warn(`[ARIA Listener] Brain health-check failed (${docHealthFails}/3) — continuing poll`);
      }
    }
    let st;
    try { st = await brainGet(`/api/aria/read-document/result/${jobId}`); }
    catch { continue; }                    // transient poll error — keep waiting
    if (!st) continue;
    if (st.status === 'done')      return st.result || null;
    if (st.status === 'failed')    throw new Error(st.error || 'extraction failed');
    if (st.status === 'not_found') throw new Error('extraction job expired');
    // status === 'processing' → keep polling
  }
  throw new Error('extraction timed out after 15 minutes');
}

// ── Ask ARIA with persistent per-sender sessions ────────────────────────────
// R-F982 (2026-05-28) — route EVERY chat through the async job+poll path.
// History: R-F916 sent URL questions async, R-F940 added doc-grounded + >6k-char
// messages — but a PLAIN question that triggered heavy tool-use (research /
// crawl) still used the 90s sync cap and aborted. Live 2026-05-28: 6× "Chat
// failed: The operation was aborted due to timeout" → users saw "⚠️ ARIA is
// temporarily unavailable." on ordinary questions whose brain trace ran ~143s.
// The async job has NO server-side cap, so going async for ALL chats means a slow
// answer is never an outage; fast chats stay snappy via fast-first polling + a
// deferred "working on it" acknowledgement (see askARIAAsync).

// R-F925 — cross-tier observability (handoff P3). A WA chat failure (brain
// timeout / down) used to be a console line only — invisible to ARIA's brain,
// so the exact 2026-05-26 incident class never became a coder-visible signal.
// Emit a `wa_chat_failed` signal to /api/aria/brain/signal; the endpoint routes
// failure-type signals to capability_gaps (R-F887), which gap_detector's
// CapabilityGapExtractor (R-F884) reads → the coder can finally SEE WA-tier
// chat failures. Fire-and-forget: never blocks or breaks the user reply, and
// brain/signal returns 202 immediately (fast even while the chat path is slow).
function signalChatFailure(message, senderJid, errMsg) {
  try {
    brainPost('/api/aria/brain/signal', {
      content: `WA chat failed (${errMsg}). User asked: "${String(message || '').slice(0, 300)}"`,
      source: 'aria-wa',
      signal_type: 'wa_chat_failed',
      metadata: { sender: String(senderJid || ''), error: String(errMsg || '').slice(0, 200) },
    }).catch(() => {});   // best-effort — the brain may be the thing that's down
  } catch { /* never let observability break the reply path */ }
}

async function askARIA(message, senderJid, chatId = null) {
  // R-F982 — ALL chats go through the async job+poll path (no 90s sync cap).
  try {
    return await askARIAAsync(message, senderJid, chatId);
  } catch (e) {
    console.error('[ARIA Listener] Async chat failed:', e.message);
    signalChatFailure(message, senderJid, `async: ${e.message}`);
    // R-F1170 — helpful error with alternatives
    return '⚠️ I hit a timeout on that one — the research is taking longer than expected. Please try again, or if you have a specific URL or document, share it and I can work from that directly.';
  }
}

// R-F916 — async chat: POST with async_mode:true, get a job_id in <1s, then poll
// /chat/result/{job_id}. Mirrors readDocumentAsync (R-F873). chatId (optional)
// only drives the interim "researching…" acknowledgement; the final answer is
// returned to the caller, which sends it exactly as for a sync reply.
async function askARIAAsync(message, senderJid, chatId = null) {
  const sid = `wa_${senderJid.replace(/[^a-zA-Z0-9_]/g, '')}`;
  let job;
  try {
    job = await brainPost('/api/aria/chat', { message, session_id: sid, async_mode: true });
  } catch (e) {
    // Dispatch itself failed (brain down / network) — fall back to a best-effort
    // sync attempt so a transient blip doesn't silently drop the question.
    console.error('[ARIA Listener] Async dispatch failed, trying sync:', e.message);
    const r = await brainPost('/api/aria/chat', { message, session_id: sid });
    return r.response || r.answer || 'No response.';
  }
  const jobId = job && job.job_id;
  if (!jobId) {
    // Older brain build without async chat support — it returned the sync result.
    return (job && (job.response || job.answer)) || 'No response.';
  }
  // R-F982 — fast-first polling so quick chats stay snappy now that ALL chats are
  // async. Most answers land in a few seconds: poll at 1s for the first 30s, then
  // back off to 5s. The "working on it" acknowledgement is DEFERRED until the job
  // has run past INTERIM_AFTER_MS, so a fast reply isn't preceded by noise (it was
  // sent immediately pre-R-F982, when only known-slow URL/doc chats came here).
  // Total window 10 min (operator: "give her the time she needs"); the brain job
  // has NO server-side cap (line ~338), so this client poll window is the only bound.
  const FAST_MS = 1000, SLOW_MS = 5000, FAST_PHASE_MS = 30000;
  const INTERIM_AFTER_MS = 7000, MAX_MS = 900000;   // R-F1056: 15 min (was 10)
  // R-F1152 — brain-health check interval: every 30s, check if brain is alive
  const BRAIN_HEALTH_CHECK_INTERVAL_MS = 30000;
  const t0 = Date.now();
  let interimSent = false;
  let lastHealthCheck = 0;
  let chatHealthFails = 0;  // R-F1325 — consecutive health-check failures
  // R-F1152 — send typing indicator so users see ARIA is working
  if (chatId && sock && isConnected) {
    sock.sendPresenceUpdate('composing', chatId).catch(() => {});
  }
  while (Date.now() - t0 < MAX_MS) {
    const elapsed = Date.now() - t0;
    await new Promise(r => setTimeout(r, elapsed < FAST_PHASE_MS ? FAST_MS : SLOW_MS));
    // R-F1152 — refresh typing indicator every 10s (WhatsApp times it out)
    if (chatId && sock && isConnected && elapsed > 0 && elapsed % 10000 < 2000) {
      sock.sendPresenceUpdate('composing', chatId).catch(() => {});
    }
    // R-F1152 — abort early if brain is down (don't poll for 15 min)
    // R-F1325 — tolerate transient blips: only abort after 3 CONSECUTIVE failures (~90s)
    if (Date.now() - lastHealthCheck > BRAIN_HEALTH_CHECK_INTERVAL_MS) {
      lastHealthCheck = Date.now();
      try {
        const hc = await fetch(`${BRAIN_URL}/health/live`, { signal: AbortSignal.timeout(8000) });
        if (!hc.ok) throw new Error(`health returned ${hc.status}`);
        chatHealthFails = 0;  // reset on success
      } catch {
        chatHealthFails = (chatHealthFails || 0) + 1;
        if (chatHealthFails >= 3) {
          console.warn(`[ARIA Listener] Brain unreachable for ${chatHealthFails} consecutive checks — aborting chat poll`);
          throw new Error('brain unreachable during chat poll');
        }
        console.warn(`[ARIA Listener] Brain health-check failed (${chatHealthFails}/3) — continuing poll`);
      }
    }
    if (chatId && !interimSent && (Date.now() - t0) >= INTERIM_AFTER_MS) {
      interimSent = true;
      // R-F1170 — engaging interim messages that set expectations
      const _interimMessages = [
        '🔎 Give me a moment — I\'m researching this now. I\'ll post the full briefing here as soon as it\'s ready.',
        '📡 Running the numbers — checking multiple sources. Results coming shortly.',
        '🕵️ Digging into this — I\'ll share what I find the moment I have a complete picture.',
        '⚡ On it — cross-referencing several databases. This usually takes a minute or two.',
      ];
      await sendReply(chatId, _interimMessages[Math.floor(Math.random() * _interimMessages.length)]
      ).catch(() => {});
    }
    // R-F1056 -- send progress updates for long-running jobs (every 2 min)
    // R-F1170 — engaging progress updates that show effort
    if (chatId && interimSent && (Date.now() - t0) > 120000 && Math.floor((Date.now() - t0) / 120000) > Math.floor(((Date.now() - t0) - 5000) / 120000)) {
      const mins = Math.floor((Date.now() - t0) / 60000);
      const _progressMessages = [
        `Still researching (${mins} min) — this is a deep dive. I'm pulling together a thorough briefing.`,
        `Still on it (${mins} min) — some of these sources take time to verify. Quality over speed.`,
        `Still working (${mins} min) — I want to get this right rather than rush it. Nearly there.`,
      ];
      sendReply(chatId, _progressMessages[mins % _progressMessages.length]).catch(() => {});
    }
    let st;
    try { st = await brainGet(`/api/aria/chat/result/${jobId}`); }
    catch { continue; }                    // transient poll error — keep waiting
    if (!st) continue;
    if (st.status === 'done') {
      const res = st.result || {};
      return res.response || res.answer || 'No response.';
    }
    if (st.status === 'failed')    throw new Error(st.error || 'chat job failed');
    if (st.status === 'not_found') throw new Error('chat job expired');
    // status === 'processing' → keep polling
  }
  throw new Error('chat job timed out after 15 minutes');
}

// ── Split long messages into chunks for WhatsApp ────────────────────────────
const WA_MSG_LIMIT = 4000;

// R-F1152 — paragraph-aware splitting. Prefers paragraph boundaries (\n\n)
// over single newlines, and single newlines over spaces, so structured
// responses (tables, lists, code blocks) don't get cut mid-line.
// R-F1321: bulletproof — never cuts mid-word; falls back to last space before
// the limit; if no space found, cuts at limit (pathological single-word case).
function splitMessage(body) {
  if (body.length <= WA_MSG_LIMIT) return [body];
  const chunks = [];
  let remaining = body;
  while (remaining.length > 0) {
    if (remaining.length <= WA_MSG_LIMIT) { chunks.push(remaining); break; }
    // Try paragraph boundary first
    let cut = remaining.lastIndexOf('\n\n', WA_MSG_LIMIT);
    if (cut < WA_MSG_LIMIT * 0.3) cut = remaining.lastIndexOf('\n', WA_MSG_LIMIT);
    if (cut < WA_MSG_LIMIT * 0.3) cut = remaining.lastIndexOf('. ', WA_MSG_LIMIT);
    if (cut < WA_MSG_LIMIT * 0.3) cut = remaining.lastIndexOf(' ', WA_MSG_LIMIT);
    // R-F1321: word-boundary safety — if no space found, find the last space
    // anywhere before the limit (never cut mid-word)
    if (cut < WA_MSG_LIMIT * 0.3) {
      cut = remaining.lastIndexOf(' ', WA_MSG_LIMIT);
      if (cut < 10) cut = WA_MSG_LIMIT;  // pathological: no space at all
    }
    const chunk = remaining.slice(0, cut);
    chunks.push(chunk + (cut < WA_MSG_LIMIT ? '' : '\n[continued]'));
    remaining = remaining.slice(cut).replace(/^[\n\s]+/, '');
  }
  return chunks;
}

// R-F1151 — emit wa_reply_failed signal when a reply fails, so ARIA learns
// that replies are not reaching the group (was dark: console.error only).
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
    try {
      fetch(`${BRAIN_URL}/api/aria/brain/signal`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${INT_TOKEN}` },
        body: JSON.stringify({
          content: `WA reply failed to ${chatId}: ${e.message}`,
          source: 'aria-wa',
          signal_type: 'wa_reply_failed',
          metadata: { chat_id: String(chatId || ''), error: String(e.message || '').slice(0, 200) },
        }),
        signal: AbortSignal.timeout(3000),
      }).catch(() => {});
    } catch { /* never let observability break the reply path */ }
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
        await brainPost('/api/aria/brain/signal', {   // R-F887 — was /api/brain/signal (404)
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
// R-F959 (2026-05-28) — tolerate speech-to-text variants of "Aria". Whisper
// transcribes a spoken "Aria" as "Arya"/"Ariya" (live: a voice note "Hey Aria,
// tell me about Brazil" came through as "Hey Arya … above Brazil", so the
// wake-word missed and she stayed silent). `ar[iy]{1,3}a` matches aria/arya/
// ariya but NOT "area" (the 'e' excludes it) — fuzzy enough for voice, no
// false-positives on common words.
const _ARIA_NAME = /\bar[iy]{1,3}a\b/i;
const MENTIONS_RE  = [_ARIA_NAME, /@ar[iy]{1,3}a/i, /^ar[iy]{1,3}a[,:]/i];
const COMMAND_RE   = /^\/(\w+)(.*)/s;

// ── Group name cache ──────────────────────────────────────────────────────────
const groupNames = new Map();   // groupId → display name

// ── Connection state ──────────────────────────────────────────────────────────
let sock           = null;
let isConnected    = false;
let qrPrinted      = false;
let messagesHeard  = 0;
// R-F1153 — message throughput tracking (messages/min)
let _msgRateTimestamps = [];   // sliding window of message receipt timestamps
let _msgRatePerMin = 0;
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
        // R-F1093 — wire auth-loss to brain so the operator knows WA is down
        brainPost('/api/aria/brain/signal', {
          content: `WA listener logged out — needs QR re-scan. Auth dir: ${AUTH_DIR}`,
          source: 'aria-wa',
          signal_type: 'wa_auth_lost',
          metadata: { code: String(code || ''), authDir: AUTH_DIR },
        }).catch(() => {});
      } else {
        // Network issue — reconnect with exponential backoff
        console.log(`[ARIA Listener] Disconnected (code ${code}) — reconnecting in ${reconnectDelay/1000}s...`);
        // R-F1093 — wire disconnect to brain so the operator sees WA reconnection
        brainPost('/api/aria/brain/signal', {
          content: `WA listener disconnected (code ${code}) — reconnecting in ${reconnectDelay/1000}s`,
          source: 'aria-wa',
          signal_type: 'wa_disconnected',
          metadata: { code: String(code || ''), reconnectDelayMs: reconnectDelay },
        }).catch(() => {});
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

      // Extract message text (R-F957 — `let`, so a transcribed voice note can
      // populate it and flow through the normal capture + wake-word path).
      let text =
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
      // R-F957 — voice notes (PTT) + audio messages.
      const audioMsg = msg.message?.audioMessage;

      // ── IMAGE PATH: download → /api/aria/ocr (async job+ack+poll) → reply ──
      // R-F1311: the previous path used a SYNCHRONOUS brainPost to /api/aria/ocr,
      // which blocked on a single HTTP call with the WA listener's 90s brainPost
      // timeout. Complex images (multi-page scans, dense tables) routinely exceeded
      // that — the 2026-06-03 incident: operator sent a parts-list image for a real
      // OEM-sourcing job; OCR started, then 'operation aborted due to timeout' from
      // the listener→intel call. Now uses the same async job+ack+poll pattern as
      // readDocumentAsync (R-F873): submit → get job_id → send "reading" ack →
      // poll until done. The brain job has NO server-side cap, so slow OCR never
      // times out the WA listener.
      if (imgMsg) {
        const caption = imgMsg.caption || '';
        console.log(`[ARIA Listener] Image shared in ${groupName} by ${senderName}${caption ? ` "${caption.slice(0,60)}"` : ' (no caption)'}`);

        try {
          const stream = await downloadMediaMessage(msg, 'buffer', {}, { reuploadRequest: sock.updateMediaMessage });
          const buffer = Buffer.isBuffer(stream) ? stream : Buffer.concat(await (async () => {
            const chunks = []; for await (const c of stream) chunks.push(c); return chunks;
          })());

          if (!buffer || buffer.length === 0) {
            await sendReply(chatId, `⚠️ The image appears to be empty.`).catch(() => {});
            continue;
          }

          const MAX_BYTES = 8 * 1024 * 1024;
          const buf = buffer.length > MAX_BYTES ? buffer.subarray(0, MAX_BYTES) : buffer;
          const b64 = buf.toString('base64');
          const sizeKb = Math.round(buffer.length / 102.4) / 10;
          const filename = `wa_${Date.now()}.jpg`;
          const contextLabel = caption
            ? `Image shared in WhatsApp group "${groupName}" by ${senderName}. Caption: ${caption.slice(0, 300)}`
            : `Image shared in WhatsApp group "${groupName}" by ${senderName} (no caption)`;

          console.log(`[ARIA Listener] OCR request: ${filename} (${sizeKb} KB)`);

          // ── Async OCR: submit job → get job_id → ack → poll ──────────
          let ocrJob;
          try {
            ocrJob = await brainPost('/api/aria/ocr', {
              image: b64,
              filename,
              context: contextLabel,
              async: true,  // R-F1311: async mode — returns job_id immediately
            });
          } catch (e) {
            console.warn('[ARIA Listener] OCR dispatch failed:', e.message);
            // R-F1311: clean customer-facing error — no internal diagnostics leaked
            await sendReply(chatId, `⚠️ I hit a snag processing that image — my OCR service didn't respond in time. Please try again in a moment, and I'll retry automatically.`).catch(() => {});
            // Report the failure to the brain so it becomes coder-visible
            brainPost('/api/aria/brain/signal', {
              content: `WA image OCR dispatch failed: ${e.message}`,
              source: `whatsapp_group:${groupName}`,
              signal_type: 'wa_ocr_failed',
              metadata: { filename, error: String(e.message || '').slice(0, 200), channel: 'whatsapp_listener' },
            }).catch(() => {});
            continue;
          }

          const jobId = ocrJob && ocrJob.job_id;
          if (!jobId) {
            // Older brain build without async OCR support — use sync result
            const extracted = ((ocrJob && ocrJob.text) || '').trim();
            if (!extracted) {
              await sendReply(chatId, `🖼 I couldn't read any text from that image. It may be blank, very low-res, or in an unsupported format.`).catch(() => {});
              continue;
            }
            await _handleOcrResult(extracted, ocrJob, filename, caption, groupName, senderName, senderJid, chatId);
            continue;
          }

          // Immediate ack — user knows ARIA is working on it
          await sendReply(chatId, `📥 Got your image. Reading now…`).catch(() => {});

          // Poll for result (mirrors readDocumentAsync pattern)
          const POLL_MS = 3000, MAX_POLLS = 200;  // up to 10 min
          let ocrResult = null;
          let lastHealthCheck = 0;
          let ocrHealthFails = 0;  // R-F1325 — consecutive health-check failures
          for (let i = 0; i < MAX_POLLS; i++) {
            await new Promise(r => setTimeout(r, POLL_MS));
            // Brain-health check every 30s
            // R-F1325 — tolerate transient blips: only abort after 3 CONSECUTIVE failures (~90s)
            if (Date.now() - lastHealthCheck > 30000) {
              lastHealthCheck = Date.now();
              try {
                const hc = await fetch(`${BRAIN_URL}/health/live`, { signal: AbortSignal.timeout(8000) });
                if (!hc.ok) throw new Error(`health returned ${hc.status}`);
                ocrHealthFails = 0;  // reset on success
              } catch {
                ocrHealthFails = (ocrHealthFails || 0) + 1;
                if (ocrHealthFails >= 3) {
                  console.warn(`[ARIA Listener] Brain unreachable for ${ocrHealthFails} consecutive checks — aborting OCR poll`);
                  await sendReply(chatId, `⚠️ My OCR service became unavailable while processing your image. Please try again in a moment.`).catch(() => {});
                  ocrResult = null; break;
                }
                console.warn(`[ARIA Listener] Brain health-check failed (${ocrHealthFails}/3) — continuing OCR poll`);
              }
            }
            let st;
            try { st = await brainGet(`/api/aria/ocr/result/${jobId}`); }
            catch { continue; }
            if (!st) continue;
            if (st.status === 'done') {
              ocrResult = st.result || null;
              break;
            }
            if (st.status === 'failed') {
              console.warn('[ARIA Listener] OCR job failed:', st.error);
              await sendReply(chatId, `⚠️ I couldn't read that image — the OCR engine returned an error. Please try again or send the text directly.`).catch(() => {});
              ocrResult = null; break;
            }
            if (st.status === 'not_found') {
              ocrResult = null; break;
            }
            // status === 'processing' → keep polling
          }

          if (!ocrResult) {
            await sendReply(chatId, `⚠️ Reading that image is taking longer than expected. I'll keep working on it — please ask again in a minute.`).catch(() => {});
            continue;
          }

          const extracted = (ocrResult.text || '').trim();
          if (!extracted) {
            await sendReply(chatId, `🖼 I couldn't read any text from that image. It may be blank, very low-res, or in an unsupported format.`).catch(() => {});
            continue;
          }

          await _handleOcrResult(extracted, ocrResult, filename, caption, groupName, senderName, senderJid, chatId);
        } catch (e) {
          console.warn('[ARIA Listener] Image processing failed:', e.message);
          // R-F1311: clean customer-facing error — no internal diagnostics leaked
          await sendReply(chatId, `⚠️ I hit a snag processing that image. Please try again in a moment.`).catch(() => {});
          // Report to brain so it becomes coder-visible
          brainPost('/api/aria/brain/signal', {
            content: `WA image processing failed: ${e.message}`,
            source: `whatsapp_group:${groupName}`,
            signal_type: 'wa_image_processing_failed',
            metadata: { filename: `wa_${Date.now()}.jpg`, error: String(e.message || '').slice(0, 200) },
          }).catch(() => {});
        }
      }

      // ── DOCUMENT PATH: PDF / DOCX / Excel / TXT / CSV ─────────────────
      let _docAnsweredCaption = false;  // R-F955 — doc+caption answered inline below
      let _isVoiceNote = false;         // R-F963 — set when this message is a transcribed voice note
      if (docMsg) {
        const filename = docMsg.fileName || 'attachment';
        const mimetype = docMsg.mimetype || '';
        const isProcessable = /pdf|word|spreadsheet|text|csv|octet-stream|msword|officedocument/.test(mimetype);
        if (isProcessable) {
          console.log(`[ARIA Listener] Processing document: ${filename} (${mimetype})`);
          try {
            const stream = await downloadMediaMessage(msg, 'buffer', {}, { reuploadRequest: sock.updateMediaMessage });  // R-F867 — standalone fn, not a socket method
            const buffer = Buffer.isBuffer(stream) ? stream : Buffer.concat(await (async () => {
              const chunks = []; for await (const c of stream) chunks.push(c); return chunks;
            })());
            // Slice BYTES (not base64 string!) to avoid mid-character truncation
            // R-F862 — track byte-level truncation. A large/scanned contract PDF
            // >8MB is clipped to the first 8MB BEFORE extraction; without a
            // banner ARIA can't tell the tail (later pages — annexes, schedules,
            // signature) is missing and may assert "X is not in the document"
            // about a clipped doc (the R-F849/GESPI failure class, WA byte path).
            const MAX_BYTES = 8 * 1024 * 1024;
            const bytesTruncated = buffer.length > MAX_BYTES;
            const buf = bytesTruncated ? buffer.subarray(0, MAX_BYTES) : buffer;
            const docType = mimetype.split('/')[1] || 'document';
            const isBinary = /pdf|word|spreadsheet|octet-stream|msword|officedocument/.test(mimetype);
            const content = isBinary
              ? buf.toString('base64')                  // FULL base64 of byte-sliced buffer
              : buf.toString('utf-8').slice(0, MAX_DOC_CHARS);
            if (content.length > 50) {
              // R-F856 — capture the failure reason instead of swallowing it.
              let _docErr = null;
              // R-F873 — async background read (no 80s sync cap). readDocumentAsync
              // sends the "📥 Reading…" ack, polls the result, and resolves to the
              // same result dict the sync path returned (or throws → null below).
              const result = await readDocumentAsync({
                content,
                filename,
                source: `whatsapp_group:${groupName}:${senderName}`,
                context: text || `Document from ${senderName} in ${groupName}`,
                encoding: isBinary ? 'base64' : 'utf-8',
                mimetype,
              }, chatId, filename).catch(e => { _docErr = e?.message || 'no response'; return null; });
              if (result) {
                // R-F854 — cache the extracted text so a later "analyse this
                // contract" follow-up mention can re-attach it (read-document
                // returns extracted_text per R-F849; fall back to utf-8 content).
                // R-F862 — if the upload was byte-truncated at the 8MB cap,
                // prepend a partial-extraction banner so the cached text (and
                // every later re-attach / review) knows the tail is missing.
                let _cacheText = (result.extracted_text || (isBinary ? '' : content) || '').trim();
                if (bytesTruncated && _cacheText) {
                  _cacheText = `[!PARTIAL EXTRACTION — "${filename}" exceeded the 8MB upload cap; only the first 8MB was read. Content past that point (later pages — annexes, schedules, signature) is NOT below. Do NOT assert any clause, party or term is absent based on this text; ask the sender to split the file or send the missing sections.]\n\n` + _cacheText;
                }
                _cacheRecentDoc(chatId, senderName, filename, _cacheText);
                const summary = result.summary || `${docType} file, ${content.length} characters`;
                console.log(`[ARIA Listener] Doc processed: ${filename} → ${result.facts_learned || 0} facts (form: ${result.doc_intel?.form_code || '?'})${bytesTruncated ? ' [BYTE-TRUNCATED >8MB]' : ''}`);
                const overview = result.overview_markdown;
                // R-F955 (2026-05-28) — when the document arrives WITH a caption
                // (the user asked something in the SAME message, e.g. "review this
                // contract"), answer it with the freshly-extracted text attached
                // INLINE. Pre-R-F955 the caption routed to askARIA separately and
                // depended on the per-chat cache + async re-attach lining up — which
                // failed live (Korvera Maintenance Services Agreement, 2026-05-28:
                // doc read OK but the review said "no document text reached my
                // context"). Inline attach removes that race entirely.
                if (text.trim() && _cacheText.length >= 200) {
                  const _reviewMsg = `${text.trim()}\n\n[ATTACHED DOCUMENT: ${filename}]\n${_cacheText}\n[END ATTACHED DOCUMENT]`;
                  _docAnsweredCaption = true;   // skip the redundant text-routing below
                  try {
                    const _ans = await askARIA(_reviewMsg, senderJid, chatId);
                    for (const part of splitMessage(_ans)) await sendReply(chatId, part);
                  } catch (e) {
                    console.warn('[ARIA Listener] R-F955 inline doc+caption review failed:', e.message);
                    await sendReply(chatId, `📄 I've read *${filename}* but my analysis step failed (${e.message}). Please ask me again in a moment.`).catch(() => {});
                  }
                } else if (overview && overview.length > 40) {
                  await sendReply(chatId, `🧠 *ARIA — document overview*\n\n${overview}`.slice(0, 3800));
                } else if (_cacheText.length >= 200) {
                  await sendReply(chatId, `📄 I've read *${filename}*. ${summary}\n\nAsk me anything about it.`);
                } else {
                  // R-F955 — extraction returned no usable text; be honest instead
                  // of inviting questions that will fail with "no document".
                  await sendReply(chatId, `⚠️ I received *${filename}* but couldn't extract readable text from it (it may be scanned/image-only or an unsupported layout). Please paste the key clauses as text, or send a text-based copy, and I'll review it.`);
                }
                // R-F862 — tell the user the read was partial so they don't trust
                // a 360 review built on a clipped document.
                if (bytesTruncated) {
                  await sendReply(chatId, `⚠️ *${filename}* is large (>8MB) — I read the first 8MB only. Later pages (annexes, payment schedules, signature) may be missing, so treat any "not in the contract" finding with caution. For a full 360 review, split the file or send the key sections.`).catch(() => {});
                }
              } else {
                // R-F856 — read-document returned null (timeout / aria-intel
                // wedged / extraction error). Pre-R-F856 this was SILENT: the
                // user got no acknowledgment that the file was received, and the
                // R-F854 cache was never populated, so every follow-up
                // ("analyse this contract") honestly said "no document in my
                // context." Surface it so the user can retry or paste the text.
                console.warn(`[ARIA Listener] R-F856 read-document returned null for ${filename}: ${_docErr || 'unknown'}`);
                // R-F887 — report this tier failure to the brain so it becomes
                // coder-visible (capability_gap → R-F884). The contract-504 class
                // of failure was previously invisible to the brain/coder.
                brainPost('/api/aria/brain/signal', {
                  content: `WhatsApp read-document failed for "${filename}": ${_docErr || 'no response'}`,
                  source: `whatsapp_group:${groupName}`,
                  signal_type: 'wa_read_document_failed',
                  metadata: { filename, error: String(_docErr || 'unknown'), channel: 'whatsapp_listener' },
                }).catch(() => {});
                await sendReply(chatId,
                  `⚠️ I received *${filename}* but couldn't read it just now — my document service didn't respond `
                  + `(it may be busy or restarting). Please resend in a minute, or paste the key clauses as text and `
                  + `I'll analyse those right away.`
                ).catch(() => {});
              }
            }
          } catch (e) {
            console.warn('[ARIA Listener] Document processing failed:', e.message);
            await sendReply(chatId,
              `⚠️ I couldn't process *${filename}* (${e.message}). Try resending, or paste the text.`
            ).catch(() => {});
          }
        }
      }

      // ── VOICE PATH: download voice note → /api/aria/transcribe → text ──
      // R-F957 — a voice note carries no caption, so `text` is empty here. We
      // transcribe it (OSS faster-whisper on the brain) and treat the transcript
      // exactly like a typed message: captured for Compliance Watch, and (R-F963)
      // a voice note is treated as an implicit mention (VOICE_ALWAYS_REPLY) since
      // STT can't be trusted to preserve the spoken wake-word. Flag-gated brain-side
      // (ARIA_VOICE_TRANSCRIBE_ENABLED) — when off, /transcribe returns
      // skipped:disabled and we just note the voice note was heard.
      if (audioMsg && !text.trim()) {
        try {
          const stream = await downloadMediaMessage(msg, 'buffer', {}, { reuploadRequest: sock.updateMediaMessage });
          const buffer = Buffer.isBuffer(stream) ? stream : Buffer.concat(await (async () => {
            const chunks = []; for await (const c of stream) chunks.push(c); return chunks;
          })());
          const tr = await brainPost('/api/aria/transcribe', {
            audio_b64: buffer.toString('base64'),
            mime: audioMsg.mimetype || 'audio/ogg',
          });
          if (tr && tr.ok && tr.text) {
            text = tr.text;   // transcript flows through the normal text path below
            _isVoiceNote = true;   // R-F963 — treat as an implicit mention (STT drops the wake-word)
            console.log(`[ARIA Listener] 🎙 Voice note transcribed (${tr.duration_s || '?'}s → ${text.length} chars) in ${groupName} by ${senderName}: ${text.slice(0, 80)}`);
          } else if (tr && tr.skipped === 'disabled') {
            console.log(`[ARIA Listener] 🎙 Voice note received in ${groupName} — transcription disabled (set ARIA_VOICE_TRANSCRIBE_ENABLED=1 on aria-intel).`);
          } else {
            console.warn(`[ARIA Listener] 🎙 Voice transcription failed: ${(tr && tr.error) || 'no response'}`);
          }
        } catch (e) {
          console.warn('[ARIA Listener] Voice processing failed:', e.message);
        }
      }

      // R-F955 — if a doc+caption was already answered inline above (with the
      // doc attached directly), don't re-route the caption through chat again.
      if (!text.trim() || _docAnsweredCaption) continue;   // skip text routing for media-only / already-answered

      const ts = new Date(
        (msg.messageTimestamp ? Number(msg.messageTimestamp) * 1000 : Date.now())
      ).toISOString();

      // R-F1152 — dedup: skip if we've already processed this message
      if (_isDuplicateMessage(chatId, senderJid, msg.messageTimestamp)) {
        console.log(`[ARIA Listener] Dedup skipped message from ${senderName} in ${groupName}`);
        continue;
      }

      // Log to console
      console.log(`[${groupName}] ${senderName}: ${text.slice(0, 100)}`);
      messagesHeard++;
      // R-F1153 — track message rate (sliding 60s window)
      _msgRateTimestamps.push(Date.now());
      if (_msgRateTimestamps.length > 1000) _msgRateTimestamps = _msgRateTimestamps.slice(-500);
      const _now = Date.now();
      while (_msgRateTimestamps.length && _msgRateTimestamps[0] < _now - 60000) _msgRateTimestamps.shift();
      _msgRatePerMin = _msgRateTimestamps.length;

      // Store in memory + Redis
      store(chatId, groupName, senderJid, senderName, text, ts);

      // Feed to ARIA brain (non-blocking)
      feedToARIA(groupName, senderName, text).catch(() => {});

      // ── Command handling ────────────────────────────────────────────────────
      const cmdMatch = text.match(COMMAND_RE);
      if (cmdMatch) {
        const cmd  = cmdMatch[1];
        // R-F1152 — /groupsummary: use explicit arg if provided, fall back to chatId
        const explicitArg = (cmdMatch[2] || '').trim();
        const args = cmd.toLowerCase() === 'groupsummary'
          ? (explicitArg || chatId)
          : explicitArg;
        try {
          let response = await handleCommand(cmd, args, senderJid);
          if (response === null) {
            // Unknown command — ask ARIA
            response = await askARIA(text, senderJid, chatId);
          }
          if (response) await sendReply(chatId, response);
        } catch (e) {
          console.error('[ARIA Listener] Command error:', e.message);
          // R-F1170 — helpful error with alternatives
          await sendReply(chatId, '⚠️ That command didn\'t work as expected. Try /help to see what I can do, or just ask me in plain English — I understand natural language too.');
        }
        continue;
      }

      // ── Mention handling — respond when ARIA is mentioned, OR (R-F963) when
      //    this is a voice note and ARIA_VOICE_ALWAYS_REPLY is on (the wake-word
      //    is unreliable in STT, so a voice note IS the address). ──────────────
      if (MENTIONS_RE.some(p => p.test(text)) || (_isVoiceNote && VOICE_ALWAYS_REPLY)) {
        let q = text.replace(/^@?ar[iy]{1,3}a[,:?\s]*/i, '').trim() || text;  // R-F959 — strip STT-variant name prefix
        // R-F854 — if this is a doc-referencing follow-up and we recently read
        // a document from this sender, re-attach its text as an
        // [ATTACHED DOCUMENT] block so the chat path is document-grounded.
        const _docs = _recentDocsForFollowup(chatId, q);
        if (_docs.length) {
          const blocks = [];
          let budget = MAX_DOC_CHARS;                 // total across ALL attached docs
          for (const _doc of _docs) {
            if (budget <= 0) break;
            let body = _doc.text;
            if (body.length > budget) {
              body = `[!PARTIAL EXTRACTION — "${_doc.filename}" was ${_doc.text.length} chars; only the first ${budget} are below. Do NOT assert any clause/annex/term is absent based on this slice.]\n\n`
                + body.slice(0, budget);
            }
            budget -= Math.min(_doc.text.length, budget);
            blocks.push(`[ATTACHED DOCUMENT — "${_doc.filename}" recently shared by ${_doc.sender}; per CONSTITUTION clause 12 you MUST quote verbatim from this text and MUST NOT review based on prior conversation context]\n${body}\n[END ATTACHED DOCUMENT]`);
          }
          q = `${blocks.join('\n\n')}\n\n${q}`;
          console.log(`[ARIA Listener] R-F912 re-attached ${blocks.length}/${_docs.length} recent document(s) to follow-up mention`);
        }
        try {
          const response = await askARIA(q, senderJid, chatId);
          if (response) await sendReply(chatId, response);
        } catch (e) {
          console.error('[ARIA Listener] Mention reply error:', e.message);
          // R-F1170 — helpful error with alternatives
          try { await sendReply(chatId, '⚠️ I hit an error processing that. Could you rephrase or share more context? I work best with specific names, URLs, or documents.'); } catch {}
        }
        continue;
      }

      // ── Smart auto-response — trigger on compliance/opportunity/risk keywords
      if (AUTO_RESPOND) {
        const trigger = detectComplianceTrigger(text);
        // R-F1152 — rate limit: at most one auto-response per chat per 2 min
        if (trigger.triggered && shouldAutoRespond(chatId, trigger.keywords) && _checkAutoRespondRateLimit(chatId)) {
          const categoryLabel = {
            compliance: 'compliance/export control',
            opportunity: 'business development/procurement',
            risk: 'risk/diversion concern',
          }[trigger.category] || trigger.category;

          const prompt = `A team member said: "${text.slice(0, 800)}"\n\nProvide a brief (under 300 words) intelligence note relevant to this. Focus on ${categoryLabel} implications. Be specific and actionable. Keywords detected: ${trigger.keywords.join(', ')}`;

          try {
            let response = await askARIA(prompt, `auto_${chatId}`, chatId);
            if (response) {
              // Enforce 500 char limit and add prefix
              response = response.slice(0, 480);
              response = `_ARIA noticed:_ ${response}`;
              await sendReply(chatId, response);
              console.log(`[ARIA Listener] Auto-response (${trigger.category}): ${trigger.keywords.join(', ')}`);
            }
          } catch (e) {
            console.error('[ARIA Listener] Auto-response error:', e.message);
            // R-F1170 — helpful auto-response error
            try { await sendReply(chatId, '💡 I spotted something relevant in that message but hit an error analysing it. If you want me to look into a specific topic, just ask me directly — mention @ARIA and I\'ll jump on it.'); } catch {}
          }
        }
      }
    }
  });
}

// ── Express status API ────────────────────────────────────────────────────────
const app = express();
app.use(express.json());

// Health — unauthenticated (for Fly.io health checks)
// R-F1153 — also probes the brain so a disconnected brain is visible in health
app.get('/health', async (_req, res) => {
  let brainOk = false;
  try {
    const hc = await fetch(`${BRAIN_URL}/health/live`, { signal: AbortSignal.timeout(3000) });
    brainOk = hc.ok;
  } catch { /* brain unreachable */ }
  res.json({
    status: isConnected ? 'connected' : 'disconnected',
    brain_reachable: brainOk,
    messages_heard: messagesHeard,
    messages_per_min: _msgRatePerMin,
  });
});

// Status — shows if listener is connected
app.get('/status', requireAuth, async (_req, res) => {
  let brainOk = false;
  try {
    const hc = await fetch(`${BRAIN_URL}/health/live`, { signal: AbortSignal.timeout(3000) });
    brainOk = hc.ok;
  } catch { /* brain unreachable */ }
  res.json({
    connected:          isConnected,
    started_at:         startedAt,
    messages_heard:     messagesHeard,
    messages_per_min:   _msgRatePerMin,
    brain_reachable:    brainOk,
    target_groups:      TARGET_GROUPS.length ? TARGET_GROUPS : 'ALL',
    group_names:        Object.fromEntries(groupNames),
    memory_store:       messageStore.length,
    redis:              !!redis,
    auth_dir:           AUTH_DIR,
    note:               isConnected
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

// ── Outbound send (R-F1288) ────────────────────────────────────────────────
// The brain's proactive/autonomous WhatsApp delivery (wa_notifier.py /
// delivery.py) POSTs here with {group_id, message}. This route was MISSING on
// the canonical isolated app — it only existed on the legacy aria-web listener —
// so every proactive/scheduled send to aria-wa.internal:5070 404'd. §21b: both
// outbound success AND failure are forwarded to the brain.
function _waBrainSignal(signalType, content, metadata) {
  try {
    fetch(`${BRAIN_URL}/api/aria/brain/signal`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${INT_TOKEN}` },
      body:    JSON.stringify({ content, source: 'aria-wa', signal_type: signalType, metadata }),
      signal:  AbortSignal.timeout(3000),
    }).catch(() => {});
  } catch { /* signalling must never throw */ }
}

app.post('/api/wa-listener/send', requireAuth, async (req, res) => {
  const b      = req.body || {};
  const target = b.group_id || b.to || b.chat_id || b.jid || '';
  const text   = b.message  || b.text || '';
  if (!target || !text) {
    return res.status(400).json({ error: 'group_id (or to/chat_id) and message are required' });
  }
  if (!sock || !isConnected) {
    _waBrainSignal('wa_outbound_failed', `WA outbound dropped — not connected (to ${target})`,
      { chat_id: String(target), reason: 'not_connected' });
    return res.status(503).json({ error: 'WhatsApp not connected' });
  }
  try {
    const chunks = splitMessage(text);
    for (let i = 0; i < chunks.length; i++) {
      if (i > 0) await new Promise(r => setTimeout(r, 500));
      await sock.sendMessage(target, { text: chunks[i] });
    }
    _waBrainSignal('wa_outbound_sent', `WA outbound sent to ${target} (${text.length} chars)`,
      { chat_id: String(target), chars: text.length, parts: chunks.length });
    res.json({ sent: true, to: target, parts: chunks.length, chars: text.length });
  } catch (e) {
    _waBrainSignal('wa_outbound_failed', `WA outbound FAILED to ${target}: ${e.message}`,
      { chat_id: String(target), error: String(e.message || '').slice(0, 200) });
    res.status(500).json({ error: e.message });
  }
});

app.listen(PORT, () => {
  console.log(`[ARIA Listener] API on port ${PORT}`);
  console.log(`[ARIA Listener] GET  /health             — health check (no auth)`);
  console.log(`[ARIA Listener] GET  /status             — connection status`);
  console.log(`[ARIA Listener] GET  /groups             — list groups + their IDs`);
  console.log(`[ARIA Listener] GET  /messages           — recent messages heard`);
  console.log(`[ARIA Listener] POST /api/wa-listener/send — outbound (brain proactive sends)`);
});

// ── Start ─────────────────────────────────────────────────────────────────────
_loadRecentDocs();   // R-F964 — restore the doc cache from disk so a restart doesn't forget shared documents
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

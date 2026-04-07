/**
 * ⚠️ DEPRECATED — DO NOT USE ⚠️
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * This file is the legacy Twilio webhook handler for WhatsApp 1-on-1 chats.
 * Twilio's WhatsApp Business API does NOT support group chats, so this file
 * cannot serve the production use case (group intelligence).
 *
 * The active production listener is:
 *     lib/whatsapp/waListener.mjs            (Baileys, embedded)
 *     services/wa-listener/aria_wa_listener.mjs  (Baileys, dockerised)
 *
 * Both Baileys listeners receive ALL the active improvements:
 *   - Image OCR + extract→explain→research pattern
 *   - Self-diagnostic auto-trigger
 *   - Smart message splitting
 *   - Connectivity error reporting
 *   - Free-form NLU + slash commands
 *   - Self-coding via /code
 *
 * This file is kept ONLY for the historical Twilio integration. If you find
 * yourself reading this file expecting it to be live, STOP — go to
 * lib/whatsapp/waListener.mjs instead.
 *
 * The exported router throws on first request to prevent silent misuse.
 * Set ALLOW_LEGACY_TWILIO=1 to bypass the guard if you're genuinely using
 * Twilio for some non-group use case.
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * ROLE SPLIT (legacy)
 * ─────────────────────────────────────────────────────────────────────────
 * WhatsApp  — ARIA listens and builds her knowledge base.
 *             She stores every conversation into memory.
 *             She ONLY responds when directly asked (command or name mention).
 *             She never sends unsolicited alerts, intel reports, or signals here.
 *
 * Telegram  — Where all intelligence output goes: briefs, alerts, debriefs,
 *             compliance flags, pipeline updates, relationship window alerts.
 *             Telegram is ARIA's primary output channel.
 *
 * Twilio provisions a virtual phone number → registers it with Meta as a
 * WhatsApp Business Account → that number IS ARIA on WhatsApp.
 *
 * ─────────────────────────────────────────────────────────────────────────
 * SETUP — TWO STAGES
 * ─────────────────────────────────────────────────────────────────────────
 *
 * STAGE 1 — SANDBOX (today, free, 5 minutes)
 *   1. Go to console.twilio.com
 *   2. Messaging → Try it out → Send a WhatsApp message
 *   3. Each team member sends "join [your-code]" to the sandbox number
 *   4. Set webhook in Twilio console:
 *        URL:    https://[your-crucix-domain]/api/whatsapp/incoming
 *        Method: HTTP POST
 *   5. ARIA responds immediately
 *
 * STAGE 2 — PRODUCTION (dedicated number, ~£3/month)
 *   1. Twilio console → Messaging → Senders → WhatsApp Senders
 *   2. New WhatsApp Sender → Self Sign-up
 *   3. Connect your Meta Business Account (create one free if needed)
 *   4. Twilio provisions a virtual number — no SIM card needed
 *   5. Meta approves your business profile (1–3 business days)
 *   6. Update TWILIO_WHATSAPP_FROM in Seenode env vars
 *   7. Team adds ARIA's number to WhatsApp contacts
 *   8. Group admin adds ARIA to the Arkmurus WhatsApp group
 *
 * ─────────────────────────────────────────────────────────────────────────
 * WIRE INTO server.mjs (2 lines)
 * ─────────────────────────────────────────────────────────────────────────
 *   import ariaWhatsApp from './lib/whatsapp/ariaWhatsApp.mjs';
 *   app.use('/api/whatsapp', ariaWhatsApp);
 *
 * ─────────────────────────────────────────────────────────────────────────
 * ENV VARS (only 3 new ones — everything else already set)
 * ─────────────────────────────────────────────────────────────────────────
 *   TWILIO_ACCOUNT_SID      from console.twilio.com → Account Info
 *   TWILIO_AUTH_TOKEN       from console.twilio.com → Account Info
 *   TWILIO_WHATSAPP_FROM    whatsapp:+14155238886  (sandbox)
 *                           whatsapp:+447700123456 (production number)
 *
 * All other vars already set: BRAIN_SERVICE_URL, ARIA_LLM_URL,
 *   ARIA_INTERNAL_TOKEN, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
 * ═══════════════════════════════════════════════════════════════════════════
 */

import express from 'express';
import crypto  from 'crypto';
import { logComplianceAction } from '../aria/complianceAudit.mjs';

// ⚠️ DEPRECATION GUARD ⚠️
// Loud warning at import time so the next person who wires this in sees it.
const _LEGACY_BYPASS = (process.env.ALLOW_LEGACY_TWILIO || '').toLowerCase() === '1';
if (!_LEGACY_BYPASS) {
  console.warn(
    '⚠️  [ariaWhatsApp.mjs] DEPRECATED Twilio listener loaded. ' +
    'WhatsApp groups go through lib/whatsapp/waListener.mjs (Baileys). ' +
    'Set ALLOW_LEGACY_TWILIO=1 to suppress this warning if you genuinely ' +
    'need the Twilio 1-on-1 path.'
  );
}

const router = express.Router();

// Hard guard on the webhook so nobody silently uses Twilio for groups.
// The user will get a clear 410 Gone with instructions instead of partial behaviour.
router.use((req, res, next) => {
  if (_LEGACY_BYPASS) return next();
  return res.status(410).json({
    error: 'DEPRECATED',
    message: 'The Twilio WhatsApp webhook (ariaWhatsApp.mjs) is deprecated. ' +
             'WhatsApp groups go through lib/whatsapp/waListener.mjs (Baileys). ' +
             'Set ALLOW_LEGACY_TWILIO=1 if you genuinely need the Twilio 1-on-1 path.',
    active_listener: 'lib/whatsapp/waListener.mjs',
  });
});

// ── Config ────────────────────────────────────────────────────────────────────
const TWILIO_SID   = process.env.TWILIO_ACCOUNT_SID    || '';
const TWILIO_TOKEN = process.env.TWILIO_AUTH_TOKEN      || '';
const FROM         = process.env.TWILIO_WHATSAPP_FROM   || '';
const SELF_URL     = `http://localhost:${process.env.PORT || 3117}`;
const INT_TOKEN    = process.env.ARIA_INTERNAL_TOKEN    || 'aria-internal';
const MAX_INPUT    = 500;  // max chars per command argument

if (!TWILIO_SID)  console.warn('[WhatsApp] TWILIO_ACCOUNT_SID not set — webhook will reject requests');
if (!FROM)        console.warn('[WhatsApp] TWILIO_WHATSAPP_FROM not set — cannot send messages');

// Twilio REST API — no SDK needed, uses built-in fetch
const twilioReady = !!(TWILIO_SID && TWILIO_TOKEN && FROM);
let twilioError = '';
if (!TWILIO_SID) twilioError = 'TWILIO_ACCOUNT_SID not set';
else if (!TWILIO_TOKEN) twilioError = 'TWILIO_AUTH_TOKEN not set';
else if (!FROM) twilioError = 'TWILIO_WHATSAPP_FROM not set';
else console.log('[WhatsApp] Twilio ready (REST API) — ARIA can send WhatsApp messages');

// ── Twilio webhook signature validation ──────────────────────────────────────
// Fail closed: if TWILIO_TOKEN is not set, reject all requests
function validateTwilioSignature(req, res, next) {
  if (!TWILIO_TOKEN) {
    console.warn('[WhatsApp] TWILIO_AUTH_TOKEN not set — rejecting webhook');
    return res.status(503).send('WhatsApp not configured');
  }

  const sig  = req.headers['x-twilio-signature'];
  if (!sig) return res.status(403).send('Missing signature');

  const proto = req.headers['x-forwarded-proto'] || req.protocol;
  const url   = `${proto}://${req.headers.host}${req.originalUrl}`;

  const params = Object.keys(req.body || {}).sort().reduce((s, k) => s + k + req.body[k], '');
  const expected = crypto
    .createHmac('sha1', TWILIO_TOKEN)
    .update(url + params)
    .digest('base64');

  if (sig === expected) return next();
  console.warn('[WhatsApp] Invalid Twilio signature — rejecting request');
  return res.status(403).send('Invalid signature');
}

// ── Per-sender rate limiting ─────────────────────────────────────────────────
const rateLimits = new Map();
const RATE_LIMIT  = 10;     // max commands per window
const RATE_WINDOW = 60000;  // 1 minute

function isRateLimited(sender) {
  const now = Date.now();
  const entry = rateLimits.get(sender);
  if (!entry || now - entry.start > RATE_WINDOW) {
    rateLimits.set(sender, { start: now, count: 1 });
    return false;
  }
  entry.count++;
  return entry.count > RATE_LIMIT;
}

// ── Conversation memory with LRU eviction ────────────────────────────────────
const memory = new Map();
const MAX_CONVERSATIONS = 500;

function evictOldest() {
  if (memory.size <= MAX_CONVERSATIONS) return;
  // Map iterates in insertion order — first key is oldest
  const oldest = memory.keys().next().value;
  memory.delete(oldest);
}

function remember(chatId, sender, text) {
  if (!memory.has(chatId)) memory.set(chatId, []);
  const hist = memory.get(chatId);
  hist.push({ sender, text, ts: new Date().toISOString() });
  if (hist.length > 60) hist.splice(0, hist.length - 60);
  evictOldest();

  // Persist to brain asynchronously
  brainPost('/api/brain/signal', {
    content:     text,
    source:      `whatsapp:${sender}:${chatId}`,
    signal_type: 'whatsapp_conversation',
    trigger:     'message',
    metadata:    { sender_name: sender },
  }).catch(e => console.warn('[WhatsApp] Signal persist failed:', e.message));
}

function recall(chatId, n = 10) {
  return (memory.get(chatId) || []).slice(-n)
    .map(m => `[${m.sender}]: ${m.text}`)
    .join('\n');
}

// ── Trigger detection ─────────────────────────────────────────────────────────
const MENTIONS   = [/\baria\b/i, /@aria/i, /^aria[,:]/i];
const COMMAND_RE = /^\/(\w+)(.*)/s;

// Direct/indirect imperative patterns — free-form requests that should reach ARIA
// even without an explicit "@aria" mention or slash command. This is what gives ARIA
// the "smart agent" feel: any clearly-actionable request gets routed to her.
const URL_RE = /https?:\/\/[^\s<>"'\]\)]+/i;
const REQUEST_PATTERNS = [
  // Investigate / research
  /\b(investigate|research|look\s+into|dig\s+into|find\s+out\s+about|deep[\-\s]?dive(?:\s+on)?|tell\s+me\s+(?:about|everything\s+about)|what\s+do\s+you\s+know\s+about|background\s+(?:check|on))\b/i,
  // Crawl / scrape / read URL
  /\b(crawl|spider|scrape|harvest)\b/i,
  /\b(read|summari[sz]e|fetch|grab|pull\s+in|ingest)\s+(?:this|that|the)?\s*(?:url|page|article|link|site|website|document|pdf)?\b/i,
  // Compliance / screening
  /\b(screen|sanction|sanctions\s+check|compliance\s+check|check\s+(?:if|whether)|run\s+(?:a\s+)?compliance|due\s+diligence\s+on)\b/i,
  // Profile / classify
  /\b(profile|classify|build\s+a\s+profile|risk\s+(?:assess(?:ment)?|level)\s+(?:for|of))\b/i,
  // Direct asks
  /\b(can\s+you|could\s+you|please|i\s+need\s+you\s+to|would\s+you|help\s+me)\s+(?:investigate|research|crawl|read|screen|check|profile|classify|find|look|find\s+out)/i,
];

function isDirectRequest(text) {
  if (!text) return false;
  const t = text.slice(0, 2000);
  // A bare URL with no other context is also a request
  if (URL_RE.test(t) && t.length < 300) return true;
  return REQUEST_PATTERNS.some(p => p.test(t));
}

const COMPLIANCE_KW = [
  /sanction/i, /embargo/i, /\bofac\b/i, /\bofsi\b/i, /\bitar\b/i,
  /dual.use/i, /export.control/i, /export.licen/i, /debarment/i,
  /due.diligence/i, /\bkyc\b/i, /\baml\b/i, /politically.exposed/i,
  /end.user.cert/i, /\bsitcl\b/i, /brokering.licen/i, /arms.embargo/i,
];

const INTEL_KW = [
  /procurement/i, /\btender\b/i, /\brfq\b/i, /\brfp\b/i,
  /\bcontract\b/i, /armed.forces/i, /ministry.of.defence/i,
  /angola|mozambique|guinea.bissau|nigeria|kenya/i,
  /\bcplp\b/i, /simportex/i, /\bfadm\b/i, /\bfaa\b/i,
  /\boem\b/i, /paramount|elbit|baykar|norinco/i,
  /counter.ied|c.ied/i, /\buav\b/i, /\bdrone\b/i,
];

function classify(text) {
  // Only classify first 2000 chars to avoid regex perf issues on long messages
  const t = text.slice(0, 2000);
  if (COMMAND_RE.test(t))                    return 'command';
  if (MENTIONS.some(p => p.test(t)))         return 'mention';
  // Direct/indirect imperative requests get routed to ARIA even without @aria
  if (isDirectRequest(t))                    return 'request';
  if (COMPLIANCE_KW.some(p => p.test(t)))    return 'compliance';
  if (INTEL_KW.some(p => p.test(t)))         return 'intel';
  return 'observe';
}

// ── Send WhatsApp message via Twilio REST API ───────────────────────────────
const TWILIO_MSG_LIMIT = 4096;

async function sendSingle(to, body) {
  const url = `https://api.twilio.com/2010-04-01/Accounts/${TWILIO_SID}/Messages.json`;
  const auth = Buffer.from(`${TWILIO_SID}:${TWILIO_TOKEN}`).toString('base64');
  const params = new URLSearchParams({ From: FROM, To: to, Body: body.slice(0, TWILIO_MSG_LIMIT) });
  const r = await fetch(url, {
    method: 'POST',
    headers: {
      'Authorization': `Basic ${auth}`,
      'Content-Type': 'application/x-www-form-urlencoded',
    },
    body: params.toString(),
    signal: AbortSignal.timeout(10000),
  });
  if (!r.ok) {
    const err = await r.text();
    console.error('[WhatsApp] Send failed:', r.status, err.slice(0, 200));
  }
}

function splitMessage(body, limit = TWILIO_MSG_LIMIT) {
  if (body.length <= limit) return [body];
  const chunks = [];
  let remaining = body;
  while (remaining.length > 0) {
    if (remaining.length <= limit) { chunks.push(remaining); break; }
    // Try to split at last newline within limit
    let cut = remaining.lastIndexOf('\n', limit);
    if (cut < limit * 0.3) cut = remaining.lastIndexOf(' ', limit);
    if (cut < limit * 0.3) cut = limit;
    chunks.push(remaining.slice(0, cut));
    remaining = remaining.slice(cut).replace(/^\n/, '');
  }
  return chunks;
}

async function send(to, body) {
  if (!twilioReady || !to || !body) {
    if (!twilioReady) console.warn('[WhatsApp] Twilio not configured — see setup instructions at top of file');
    return;
  }
  try {
    const chunks = splitMessage(body);
    for (let i = 0; i < chunks.length; i++) {
      if (i > 0) await new Promise(r => setTimeout(r, 500));
      await sendSingle(to, chunks[i]);
    }
  } catch (e) {
    console.error('[WhatsApp] Send failed:', e.message);
  }
}

// ── CRUCIX API (calls own server.mjs endpoints) ──────────────────────────────
async function brainGet(path) {
  const r = await fetch(`${SELF_URL}${path}`, {
    headers: { 'Authorization': `Bearer ${INT_TOKEN}` },
    signal: AbortSignal.timeout(10000),
  });
  if (!r.ok) throw new Error(`GET ${path} → ${r.status}`);
  return r.json();
}

async function brainPost(path, body) {
  const timeout = path.includes('/aria/') ? 90000 : 15000; // 90s for LLM calls
  const r = await fetch(`${SELF_URL}${path}`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${INT_TOKEN}` },
    body:    JSON.stringify(body),
    signal:  AbortSignal.timeout(timeout),
  });
  if (!r.ok) throw new Error(`POST ${path} → ${r.status}`);
  return r.json();
}

// ── ARIA LLM — uses /api/aria/chat (JSON, not streaming) ────────────────────
// Note: the engine maintains its own per-session memory in Redis (7 days, 50 turns).
// We do NOT pre-pend local recall — that duplicates context and confuses the LLM.
// Tool-use (investigate/crawl/read/screen) is auto-detected server-side via auto_tools.
async function askARIA(message, _unused = '', sender = 'whatsapp') {
  const sid = `twilio_${sender.replace(/[^a-zA-Z0-9_+]/g, '')}`;
  try {
    const r = await brainPost('/api/aria/chat', {
      message,
      session_id: sid,
      auto_tools: true,
    });
    let body = r.response || r.answer || 'No response.';
    // If ARIA actually invoked a tool, hint the user (helps build trust in indirect commands)
    if (r.tool_used) {
      const toolLabel = {
        investigate: '🔎 deep investigation',
        investigate_url: '🔎 URL investigation',
        crawl: '🕷 site crawl',
        read: '📖 article read',
        profile: '🪪 entity profile',
        screen: '⛔ compliance screen',
      }[r.tool_used] || `🛠 ${r.tool_used}`;
      body = `_${toolLabel} complete_\n\n${body}`;
    }
    return body;
  } catch (e) {
    console.error('[WhatsApp] ARIA chat failed:', e.message);
    return '⚠️ ARIA is temporarily unavailable. Try again in a moment, or use /help for direct commands.';
  }
}

// ── Command handlers ──────────────────────────────────────────────────────────
async function handleCommand(cmd, args, sender) {
  const a = (args || '').trim().slice(0, MAX_INPUT);

  switch (cmd.toLowerCase()) {

    case 'brief': {
      const [b, id] = await Promise.all([
        brainGet('/api/brain/brief').catch(() => ({})),
        brainGet('/api/aria/identity').catch(() => ({})),
      ]);
      const leads = (b.top_leads || []).slice(0, 4);
      const now   = new Date().toISOString().slice(0, 16).replace('T', ' ') + ' UTC';
      let msg = `*ARIA INTEL BRIEF*\n${now}\n\n`;
      if (leads.length) {
        msg += `*Priority leads:*\n`;
        leads.forEach((l, i) => {
          const e = l.urgency === 'HIGH' ? '🔴' : l.urgency === 'MEDIUM' ? '🟠' : '🟡';
          msg += `${e} *${i+1}. ${l.market || '?'}*\n`;
          msg += `${(l.signal_title || '').slice(0, 60)}\n`;
          msg += `Win: ${((l.win_probability||0)*100).toFixed(0)}%\n\n`;
        });
      } else {
        msg += `_No active leads. Run /sweep._\n\n`;
      }
      msg += `ARIA v${id.version||'3.0'} | ${id.markets||80} markets`;
      return msg;
    }

    case 'sweep':
      await brainPost('/api/brain/sweep', {}).catch(() => {});
      return '🔄 Sweep triggered. Results in 2–5 min. Use /brief for summary.';

    case 'screen': {
      if (!a) return '⚠️ Usage: */screen* [entity name]';
      const d = await brainPost('/api/aria/compliance/screen', { entity_name: a }).catch(() => ({}));
      const ok = d.result === 'PERMITTED';
      logComplianceAction({ type: 'SCREENING', user: sender, query: a, result: d, recommendation: ok ? 'PERMITTED' : 'BLOCKED' }).catch(() => {});
      let msg = `${ok ? '✅' : '⛔'} *COMPLIANCE SCREEN*\n`;
      msg += `*Entity:* ${a}\n*Result:* ${d.result || 'UNKNOWN'}\n\n`;
      Object.entries(d.screened_against || {}).forEach(([l, v]) => {
        msg += `  ✓ ${l}: ${v}\n`;
      });
      msg += ok
        ? `\n_Pre-screen only. Legal review required._`
        : `\n⛔ *MATCH FOUND. Do not proceed without legal review.*`;
      return msg;
    }

    case 'oem': {
      const parts = a.split(' ');
      const cap   = parts[0];
      const mkt   = parts.slice(1).join(' ');
      if (!cap) return '⚠️ Usage: */oem* [capability] [market]';
      const params = new URLSearchParams({ capability: cap, limit: 4, ...(mkt && { destination: mkt }) });
      const d = await brainGet(`/api/brain/oem/search?${params}`).catch(() => ({}));
      let msg = `*OEM SEARCH — ${cap.toUpperCase()}*${mkt ? ` | ${mkt}` : ''}\n\n`;
      (d.results || []).forEach((o, i) => {
        const rel = { active_partner:'🤝', mou:'🤝', contacted:'📞', aware:'👁', none:'—' }[o.arkmurus_relationship] || '—';
        msg += `*${i+1}. ${o.name}* (${o.country}) ${rel}\n`;
        if (o.lusophone_experience) msg += `✅ Lusophone track record\n`;
        if (o.itar_controlled)      msg += `⚠️ ITAR controlled\n`;
        msg += '\n';
      });
      return msg || 'No OEM results found.';
    }

    case 'approach': {
      const parts  = a.split(' ');
      const market = parts[0];
      const req    = parts.slice(1).join(' ');
      if (!market) return '⚠️ Usage: */approach* [market] [requirement]';
      const params = new URLSearchParams({ market, urgency: 'HIGH', ...(req && { capability: req }) });
      const s = await brainGet(`/api/brain/approach/quick?${params}`).catch(() => ({}));
      const t = s.target || {};
      const o = (s.oem_recommendation || {}).primary;
      let msg = `*APPROACH — ${market.toUpperCase()}*\nGrade: *${s.grade || '?'}*\n\n`;
      msg += `*Contact:* ${t.name || 'TBC'}\n${t.role || ''}\n${t.contact_route || 'Unknown'}\n\n`;
      if (o) msg += `*OEM:* ${o.name} (${o.country})\n\n`;
      const flags = (s.compliance || {}).flags || [];
      if (flags.length) msg += `⚠️ *Compliance flags:* ${flags.join(', ')}\n\n`;
      const steps = ((s.actions || {}).first_three_steps || []).slice(0, 3);
      if (steps.length) {
        msg += `*First steps:*\n`;
        steps.forEach(st => { msg += `  ${st.step}. ${(st.action || '').slice(0, 90)}\n`; });
      }
      return msg;
    }

    case 'pipeline': {
      const d = await brainGet('/api/brain/pipeline/summary').catch(() => ({}));
      let msg = `*ARKMURUS PIPELINE*\n\n`;
      msg += `Open: *${d.open_deals||0}* | Won: *${d.won_deals||0}* | Lost: *${d.lost_deals||0}*\n`;
      msg += `Pipeline value: *£${(d.total_pipeline_value||0).toLocaleString()}*\n`;
      msg += `Win rate: *${((d.win_rate||0)*100).toFixed(0)}%*\n\n`;
      if (d.stale_alerts?.length) {
        msg += `⚠️ *Stale deals (${d.stale_alerts.length}):*\n`;
        d.stale_alerts.slice(0, 3).forEach(dl => {
          msg += `  • ${dl.id} | ${dl.market} | ${dl.days_stale} days\n`;
        });
        msg += '\n';
      }
      (d.top_deals || []).slice(0, 4).forEach(dl => {
        msg += `*${dl.id}* | ${dl.market} | ${dl.stage}\n${(dl.opportunity||'').slice(0,55)}\n\n`;
      });
      return msg;
    }

    case 'humint': {
      if (!a) return '⚠️ Usage: */humint* [market]';
      const d = await brainGet(`/api/brain/humint/contacts?market=${encodeURIComponent(a)}`).catch(() => ({}));
      const cs = d.contacts || [];
      if (!cs.length) return `ℹ️ No contacts found for ${a}.`;
      let msg = `*CONTACTS — ${a.toUpperCase()}*\n\n`;
      cs.slice(0, 5).forEach(c => {
        const w = c.relationship_window_active ? ' 🟢 WINDOW OPEN' : '';
        msg += `*${c.full_name || c.name}*${w}\n${c.role || ''}\n${c.contact_route || ''}\n\n`;
      });
      return msg;
    }

    case 'windows': {
      const d = await brainGet('/api/brain/humint/windows').catch(() => ({}));
      const ws = d.windows || [];
      if (!ws.length) return 'ℹ️ No active relationship windows.';
      let msg = `*🟢 RELATIONSHIP WINDOWS (${ws.length})*\n\n`;
      ws.slice(0, 5).forEach(w => {
        const e = w.urgency === 'CRITICAL' ? '🔴' : w.urgency === 'HIGH' ? '🟠' : '🟡';
        msg += `${e} *${w.full_name}*\n${w.role} | ${w.market}\n`;
        msg += `${w.days_in_role}d in role | ${w.days_remaining}d window remaining\n\n`;
      });
      return msg;
    }

    case 'deal': {
      const parts = a.split(' ');
      const sub   = parts[0]?.toLowerCase();
      if (sub === 'new') {
        const market = parts[1] || '';
        const opp    = parts.slice(2).join(' ');
        if (!market || !opp) return '⚠️ Usage: /deal new [market] [opportunity]';
        const r = await brainPost('/api/brain/pipeline/create', { market, opportunity: opp }).catch(() => ({}));
        return r.id
          ? `✅ Deal *${r.id}* created\n${market} | ${opp.slice(0, 60)}`
          : '⚠️ Could not create deal.';
      }
      if (sub === 'advance') {
        const id    = parts[1]?.toUpperCase();
        const stage = parts[2]?.toUpperCase();
        if (!id || !stage) return '⚠️ Usage: /deal advance [ID] [STAGE]';
        await brainPost('/api/brain/pipeline/advance', { deal_id: id, stage }).catch(() => {});
        return `✅ Deal *${id}* → *${stage}*`;
      }
      const id = a.toUpperCase();
      const d  = await brainGet(`/api/brain/pipeline/deal/${id}`).catch(() => null);
      if (!d) return `⚠️ Deal ${id} not found. Use /pipeline to list.`;
      let msg = `*DEAL ${d.id}* | ${d.market} | *${d.stage}*\n${(d.opportunity||'').slice(0,80)}\n\n`;
      msg += `Value: £${(d.pipeline_value||0).toLocaleString()} | Win: ${((d.win_probability||0)*100).toFixed(0)}%\n`;
      if (d.target_person) msg += `Contact: ${d.target_person}\n`;
      if (d.stale)         msg += `⚠️ Stale — ${d.days_in_stage} days without movement\n`;
      return msg;
    }

    case 'conf': {
      if (!a) {
        const d = await brainGet('/api/brain/conference/calendar').catch(() => ({}));
        const upcoming = (d.upcoming || []).slice(0, 6);
        if (!upcoming.length) return 'ℹ️ No upcoming conferences in calendar.';
        let msg = `*📅 CONFERENCE CALENDAR*\n\n`;
        upcoming.forEach(c => {
          const e = (c.days_until||999) < 30 ? '🔴' : (c.days_until||999) < 90 ? '🟠' : '🟢';
          msg += `${e} *${c.name}*\n${c.dates} | ${c.location}\n\n`;
        });
        return msg + `_Use /conf [name] for pre-event brief_`;
      }
      const b_ = await brainGet(`/api/brain/conference/brief?name=${encodeURIComponent(a)}`).catch(() => ({}));
      let msg = `*${b_.name || a} BRIEF*\n${b_.dates||''} | ${b_.location||''}\n\n`;
      (b_.arkmurus_objectives||[]).slice(0,3).forEach(o => { msg += `  • ${o}\n`; });
      if (b_.must_meet?.length) {
        msg += `\n*Must meet:*\n`;
        b_.must_meet.slice(0, 4).forEach(p => { msg += `  🎯 ${p.full_name} — ${p.role}\n`; });
      }
      return msg;
    }

    case 'classify': {
      if (!a) return '⚠️ Usage: */classify* [product description]';
      const d = await brainPost('/api/aria/compliance/classify', { description: a }).catch(() => ({}));
      logComplianceAction({ type: 'CLASSIFICATION', user: sender, query: a, result: d, confidence: d.classifications?.[0]?.confidence ? `${(d.classifications[0].confidence * 100).toFixed(0)}%` : '' }).catch(() => {});
      let msg = `*ML CLASSIFICATION*\n*Product:* ${a.slice(0, 80)}\n\n`;
      if (d.classifications?.length) {
        d.classifications.forEach(c => {
          msg += `• *${c.code || c.category}* — ${c.description || ''}\n`;
          if (c.confidence) msg += `  Confidence: ${(c.confidence * 100).toFixed(0)}%\n`;
          if (c.controlled) msg += `  ⚠️ Controlled item\n`;
        });
      } else {
        msg += `Result: ${d.result || d.category || 'No classification returned.'}\n`;
      }
      msg += `\n_Classification is advisory only. Verify with compliance team._`;
      return msg;
    }

    case 'sanctions': {
      if (!a) return '⚠️ Usage: */sanctions* [name]';
      const d = await brainPost('/api/aria/compliance/sanctions', { name: a }).catch(() => ({}));
      const hits = d.matches || d.results || [];
      logComplianceAction({ type: 'SANCTIONS_CHECK', user: sender, query: a, result: d, recommendation: hits.length ? 'MATCHES_FOUND' : 'CLEAR' }).catch(() => {});
      let msg = `*SANCTIONS CHECK*\n*Name:* ${a}\n\n`;
      if (hits.length) {
        msg += `⛔ *${hits.length} match(es) found:*\n`;
        hits.slice(0, 5).forEach(h => {
          msg += `• *${h.name || h.entity}* — ${h.list || h.source || 'Unknown list'}\n`;
          if (h.score) msg += `  Match score: ${(h.score * 100).toFixed(0)}%\n`;
          if (h.reason) msg += `  ${h.reason}\n`;
        });
        msg += `\n⛔ *Do not proceed without legal review.*`;
      } else {
        msg += `✅ No sanctions matches found.\n_This is a preliminary check. Full due diligence required._`;
      }
      return msg;
    }

    case 'risk': {
      if (!a) return '⚠️ Usage: */risk* [country]';
      const d = await brainPost('/api/aria/compliance/risk', { country: a }).catch(() => ({}));
      const level = d.risk_level || d.level || 'UNKNOWN';
      logComplianceAction({ type: 'RISK_ASSESSMENT', user: sender, query: a, result: d, recommendation: level }).catch(() => {});
      const emoji = { HIGH: '🔴', MEDIUM: '🟠', LOW: '🟢' }[level.toUpperCase()] || '⚪';
      let msg = `${emoji} *COUNTRY RISK — ${a.toUpperCase()}*\n\n`;
      msg += `*Risk level:* ${level}\n`;
      if (d.score) msg += `*Score:* ${d.score}/100\n`;
      if (d.sanctions_regimes?.length) msg += `*Sanctions regimes:* ${d.sanctions_regimes.join(', ')}\n`;
      if (d.embargoes?.length) msg += `*Embargoes:* ${d.embargoes.join(', ')}\n`;
      if (d.export_controls) msg += `*Export controls:* ${d.export_controls}\n`;
      if (d.notes) msg += `\n${d.notes}\n`;
      msg += `\n_Risk assessment is advisory. Consult compliance team for decisions._`;
      return msg;
    }

    case 'ask': {
      if (!a) return '⚠️ Usage: /ask [question]';
      return await askARIA(a, '', sender);
    }

    case 'teach': {
      if (!a) return '⚠️ Usage: /teach [topic]: [fact]';
      const colonIdx = a.indexOf(':');
      if (colonIdx < 1) return '⚠️ Format: /teach [topic]: [fact]\nExample: /teach ECJU processing: Standard SITCL takes 20 working days';
      const topic = a.slice(0, colonIdx).trim();
      const fact  = a.slice(colonIdx + 1).trim();
      if (!fact) return '⚠️ Please include the fact after the colon.';
      const senderDisplay = sender.replace('whatsapp:', '').replace(/\+/g, '');
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
          source: `correction_by:${sender.replace('whatsapp:', '')}`,
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
          source: `feedback:${sender.replace('whatsapp:', '')}`,
          signal_type: 'user_feedback',
          metadata: { sentiment, notes, sender, channel: 'whatsapp_twilio' },
        });
        const emoji = positive ? '👍' : negative ? '📝' : '📋';
        return `${emoji} *Feedback recorded.* ${positive ? 'Glad I could help!' : negative ? 'I\'ll work on improving.' : 'Thank you for the feedback.'}`;
      } catch (e) {
        return '⚠️ Failed to record feedback.';
      }
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

    // ── Self-coding: ARIA writes a brand-new module on demand ────────────
    case 'code': {
      if (!a || a.length < 10) {
        return [
          '⚠️ *Usage:* /code [describe the module you want]',
          '',
          'Examples:',
          '• _/code track Saudi MoD procurement notices every hour_',
          '• _/code monitor Janes RSS for K9 Thunder mentions_',
          '• _/code check OFSI updates and alert on Russia matches_',
          '',
          'I will design and write a complete Python module, syntax-validate it, and stage it for your review. Use /staged to see all pending modules and /deploy [id] to ship one.',
        ].join('\n');
      }
      const ariaServiceUrl = process.env.ARIA_SERVICE_URL;
      if (!ariaServiceUrl) return '⚠️ Self-coding requires ARIA_SERVICE_URL to be configured.';
      try {
        const resp = await fetch(`${ariaServiceUrl}/api/aria/self/code`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ request: a }),
          signal: AbortSignal.timeout(180000),
        });
        if (!resp.ok) {
          const errText = (await resp.text()).slice(0, 300);
          return `⚠️ Code generation failed: ${resp.status} ${errText}`;
        }
        const data = await resp.json();
        if (!data.ok) {
          return `⚠️ I couldn't write that module: ${data.error || 'unknown error'}\n\nTry being more specific about what the module should do, what inputs it takes, and what output you need.`;
        }
        return [
          `✅ *New module staged*`,
          ``,
          `*File:* \`${data.file}\``,
          `*Lines:* ${data.lines}`,
          `*Staged ID:* \`${data.staged_id}\``,
          ``,
          `*Preview:*`,
          '```',
          (data.preview || '').slice(0, 500),
          '```',
          ``,
          `Review it: */staged*`,
          `Deploy it: */deploy ${data.staged_id}*`,
          `Or reject by ignoring — staged modules expire in 14 days.`,
        ].join('\n');
      } catch (e) {
        return `⚠️ Code generation error: ${e.message}`;
      }
    }

    case 'staged': {
      const ariaServiceUrl = process.env.ARIA_SERVICE_URL;
      if (!ariaServiceUrl) return '⚠️ ARIA_SERVICE_URL not configured.';
      try {
        const resp = await fetch(`${ariaServiceUrl}/api/aria/self/staged`, {
          signal: AbortSignal.timeout(15000),
        });
        if (!resp.ok) return `⚠️ Could not fetch staged improvements: ${resp.status}`;
        const data = await resp.json();
        const items = Array.isArray(data) ? data : (data.staged || data.items || []);
        if (!items.length) return 'ℹ️ No staged improvements awaiting review.';
        let msg = `*🛠 STAGED IMPROVEMENTS (${items.length})*\n\n`;
        items.slice(0, 8).forEach((s, i) => {
          const auto = s.auto_deployable ? '⚡ auto' : '🔒 manual';
          msg += `*${i + 1}. \`${s.id}\`* — ${s.change_type} ${auto}\n`;
          msg += `_${(s.file || '').slice(-60)}_\n`;
          msg += `${(s.description || '').slice(0, 120)}\n\n`;
        });
        msg += `Use */deploy [id]* to ship one to production.`;
        return msg;
      } catch (e) {
        return `⚠️ Failed to list staged: ${e.message}`;
      }
    }

    case 'deploy': {
      if (!a) return '⚠️ Usage: /deploy [staged_id]\nUse /staged to see IDs.';
      const id = a.trim().split(/\s+/)[0].slice(0, 12);
      const ariaServiceUrl = process.env.ARIA_SERVICE_URL;
      if (!ariaServiceUrl) return '⚠️ ARIA_SERVICE_URL not configured.';
      try {
        const resp = await fetch(`${ariaServiceUrl}/api/aria/self/deploy/${encodeURIComponent(id)}`, {
          method: 'POST',
          signal: AbortSignal.timeout(30000),
        });
        if (!resp.ok) {
          const errText = (await resp.text()).slice(0, 200);
          return `⚠️ Deploy failed: ${resp.status} ${errText}`;
        }
        const data = await resp.json();
        if (data.error) return `⚠️ ${data.error}`;
        return `✅ *Deployed*\n*File:* \`${data.file}\`\n*ID:* \`${id}\`\n${data.backup ? `Backup: \`${data.backup}\`\n` : ''}\nTo undo: */rollback ${id}*`;
      } catch (e) {
        return `⚠️ Deploy error: ${e.message}`;
      }
    }

    case 'mastery':
    case 'student': {
      const ariaServiceUrl = process.env.ARIA_SERVICE_URL;
      if (!ariaServiceUrl) return '⚠️ ARIA_SERVICE_URL not configured.';
      try {
        const resp = await fetch(`${ariaServiceUrl}/api/aria/student/stats`, {
          signal: AbortSignal.timeout(15000),
        });
        if (!resp.ok) return `⚠️ Student stats failed: ${resp.status}`;
        const d = await resp.json();
        const m = d.mastery || {};
        const c = d.curriculum || {};
        const overall = ((m.overall_mastery || 0) * 100).toFixed(0);
        let msg = `*🎓 ARIA student report*\n\n`;
        msg += `*Overall mastery:* ${overall}%\n`;
        msg += `*Total samples:* ${m.total_samples || 0}\n`;
        msg += `*Quizzes taken:* ${d.quiz_count || 0}`;
        if (d.recent_quiz_score) msg += ` | recent score: ${(d.recent_quiz_score * 100).toFixed(0)}%`;
        msg += `\n*Reading sessions:* ${d.reading_sessions || 0}\n`;
        msg += `*Articles studied:* ${d.articles_studied_total || 0}\n`;
        msg += `*Divergences logged:* ${d.divergences_recorded || 0}`;
        if (d.divergences_needing_study) msg += ` (${d.divergences_needing_study} need study)`;
        msg += `\n\n*Strong topics:*\n`;
        const strong = m.strong_topics || [];
        if (strong.length) {
          strong.slice(0, 5).forEach(t => {
            const score = ((m.topics?.[t]?.score || 0) * 100).toFixed(0);
            msg += `  ✅ ${t}: ${score}%\n`;
          });
        } else {
          msg += `  _none yet — keep studying_\n`;
        }
        msg += `\n*Weak topics (study priority):*\n`;
        const weak = m.weak_topics || [];
        if (weak.length) {
          weak.slice(0, 5).forEach(t => {
            const score = ((m.topics?.[t]?.score || 0) * 100).toFixed(0);
            const samples = m.topics?.[t]?.samples || 0;
            msg += `  📖 ${t}: ${score}% (${samples} samples)\n`;
          });
        } else {
          msg += `  _no weak topics — well done_\n`;
        }
        msg += `\nUse */quiz* to test her now or */study* to trigger a reading session.`;
        return msg;
      } catch (e) {
        return `⚠️ Student stats error: ${e.message}`;
      }
    }

    case 'quiz': {
      const ariaServiceUrl = process.env.ARIA_SERVICE_URL;
      if (!ariaServiceUrl) return '⚠️ ARIA_SERVICE_URL not configured.';
      try {
        const resp = await fetch(`${ariaServiceUrl}/api/aria/student/quiz`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ num_questions: 5 }),
          signal: AbortSignal.timeout(60000),
        });
        if (!resp.ok) return `⚠️ Quiz failed: ${resp.status}`;
        const d = await resp.json();
        const score = ((d.score || 0) * 100).toFixed(0);
        let msg = `*🎯 Self-quiz result*\n\n`;
        msg += `Score: *${d.passed || 0} / ${d.quizzed || 0}* (${score}%)\n\n`;
        if (d.results?.length) {
          msg += `*Questions attempted:*\n`;
          d.results.slice(0, 5).forEach((r, i) => {
            const tick = r.passed ? '✅' : '❌';
            msg += `${i + 1}. ${tick} ${r.question?.slice(0, 80) || '?'}\n`;
            if (r.local_source) msg += `   _via ${r.local_source}, similarity ${r.similarity_to_original}_\n`;
          });
        }
        msg += `\nMastery has been updated. Use */mastery* to see her current scores.`;
        return msg;
      } catch (e) {
        return `⚠️ Quiz error: ${e.message}`;
      }
    }

    case 'study': {
      const ariaServiceUrl = process.env.ARIA_SERVICE_URL;
      if (!ariaServiceUrl) return '⚠️ ARIA_SERVICE_URL not configured.';
      try {
        const resp = await fetch(`${ariaServiceUrl}/api/aria/student/study`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ num_articles: 4 }),
          signal: AbortSignal.timeout(120000),
        });
        if (!resp.ok) return `⚠️ Study session failed: ${resp.status}`;
        const d = await resp.json();
        let msg = `*📚 Reading session complete*\n\n`;
        msg += `*Focus topics:* ${(d.weak_topics_studied || []).join(', ')}\n`;
        msg += `*Articles studied:* ${d.articles_read || 0}\n\n`;
        if (d.studied?.length) {
          msg += `*What she read:*\n`;
          d.studied.slice(0, 5).forEach((a, i) => {
            msg += `${i + 1}. ${a.title?.slice(0, 90) || '?'}\n`;
            msg += `   _${a.feed} | ${a.chars_read} chars | topics: ${(a.topics || []).join(', ')}_\n`;
          });
        }
        msg += `\nFacts indexed into knowledge base + neural memory.`;
        return msg;
      } catch (e) {
        return `⚠️ Study error: ${e.message}`;
      }
    }

    case 'curriculum': {
      const ariaServiceUrl = process.env.ARIA_SERVICE_URL;
      if (!ariaServiceUrl) return '⚠️ ARIA_SERVICE_URL not configured.';
      try {
        const resp = await fetch(`${ariaServiceUrl}/api/aria/student/curriculum`, {
          signal: AbortSignal.timeout(15000),
        });
        if (!resp.ok) return `⚠️ Curriculum check failed: ${resp.status}`;
        const d = await resp.json();
        let msg = `*📋 ARIA curriculum*\n\n`;
        msg += `Library size: ${d.library_size || 0} cases\n`;
        msg += `Weak topics: ${d.total_weak || 0}\n`;
        msg += `Stale topics: ${d.total_stale || 0}\n\n`;
        if (d.items?.length) {
          msg += `*Study priority list:*\n`;
          d.items.slice(0, 8).forEach((item, i) => {
            const score = (item.score * 100).toFixed(0);
            msg += `${i + 1}. *${item.topic}* — ${score}% (${item.samples} samples, ${item.days_since_practice}d since practice)\n`;
            msg += `   actions: ${(item.actions || []).join(', ')}\n`;
          });
        } else {
          msg += `_All topics are at acceptable mastery._`;
        }
        return msg;
      } catch (e) {
        return `⚠️ Curriculum error: ${e.message}`;
      }
    }

    case 'independence':
    case 'reasoning': {
      const ariaServiceUrl = process.env.ARIA_SERVICE_URL;
      if (!ariaServiceUrl) return '⚠️ ARIA_SERVICE_URL not configured.';
      try {
        const resp = await fetch(`${ariaServiceUrl}/api/aria/independence`, {
          signal: AbortSignal.timeout(15000),
        });
        if (!resp.ok) return `⚠️ Independence check failed: ${resp.status}`;
        const d = await resp.json();
        const ratio = (d.independence_ratio * 100).toFixed(0);
        const bs = d.by_source || {};
        const lib = d.components?.reasoning_library || {};
        const ollama = d.components?.local_ollama || {};
        let msg = `*🧠 ARIA reasoning independence*\n\n`;
        msg += `*Independence ratio:* ${ratio}% local\n`;
        msg += `*Trajectory:* ${d.trajectory}\n`;
        msg += `*Total queries:* ${d.total_queries}\n\n`;
        msg += `*By source:*\n`;
        msg += `  symbolic_reasoner: ${bs.symbolic_reasoner || 0}\n`;
        msg += `  reasoning_library: ${bs.reasoning_library || 0}\n`;
        msg += `  local_brain: ${bs.local_brain || 0}\n`;
        msg += `  local_ollama: ${bs.local_ollama || 0}\n`;
        msg += `  cloud_llm: ${bs.cloud_llm || 0}\n\n`;
        msg += `*Library:* ${lib.total_cases || 0} cases stored | ${((lib.hit_rate || 0) * 100).toFixed(0)}% hit rate\n`;
        msg += `*Local Ollama reasoning:* ${ollama.available ? '✅ ' + ollama.model : '❌ not loaded'}\n`;
        if (!ollama.available) {
          msg += `\n_To enable local reasoning model:_\n\`ollama pull qwen2.5:7b\`\n_or_ \`ollama pull deepseek-r1:7b\``;
        }
        return msg;
      } catch (e) {
        return `⚠️ Independence error: ${e.message}`;
      }
    }

    case 'vision':
    case 'vision-status': {
      const ariaServiceUrl = process.env.ARIA_SERVICE_URL;
      if (!ariaServiceUrl) return '⚠️ ARIA_SERVICE_URL not configured.';
      try {
        const resp = await fetch(`${ariaServiceUrl}/api/aria/vision-status`, {
          signal: AbortSignal.timeout(10000),
        });
        if (!resp.ok) return `⚠️ Vision status check failed: ${resp.status}`;
        const d = await resp.json();
        const tick = d.ok ? '✅' : '❌';
        const indep = d.independent ? '🟢 INDEPENDENT' : '🟡 cloud-dependent';
        let msg = `${tick} *Vision OCR status* — ${indep}\n\n`;
        msg += `*Active backend:* \`${d.active_backend}\`\n`;
        msg += `*Main LLM:* ${d.main_llm || '?'}\n\n`;
        msg += `*Local backends (independent):*\n`;
        msg += `  EasyOCR: ${d.local_backends?.easyocr ? '✅' : '❌'}\n`;
        msg += `  Tesseract: ${d.local_backends?.tesseract ? '✅' : '❌'}\n`;
        msg += `  Ollama vision: ${d.local_backends?.ollama_vision_model ? '✅ ' + d.local_backends.ollama_vision_model : '❌'}\n`;
        msg += `\n*Cloud fallback (optional):*\n`;
        msg += `  Dedicated: ${d.cloud_backend?.dedicated_configured ? '✅ ' + d.cloud_backend.dedicated_provider : '❌ not set'}\n`;
        msg += `  Main-chain fallback: ${d.cloud_backend?.fallback_available ? '✅ ' + d.cloud_backend.fallback_provider : '❌'}\n`;
        if (d.setup_instructions?.length) {
          msg += `\n*Setup:*\n` + d.setup_instructions.map(s => `• ${s}`).join('\n');
        }
        if (d.ok) {
          msg += `\n\n_Send an image to test it._`;
        }
        return msg;
      } catch (e) {
        return `⚠️ Vision status error: ${e.message}`;
      }
    }

    case 'rollback': {
      if (!a) return '⚠️ Usage: /rollback [staged_id]';
      const id = a.trim().split(/\s+/)[0].slice(0, 12);
      const ariaServiceUrl = process.env.ARIA_SERVICE_URL;
      if (!ariaServiceUrl) return '⚠️ ARIA_SERVICE_URL not configured.';
      try {
        const resp = await fetch(`${ariaServiceUrl}/api/aria/self/rollback/${encodeURIComponent(id)}`, {
          method: 'POST',
          signal: AbortSignal.timeout(30000),
        });
        if (!resp.ok) return `⚠️ Rollback failed: ${resp.status}`;
        const data = await resp.json();
        return data.error ? `⚠️ ${data.error}` : `↩️ *Rolled back* \`${id}\``;
      } catch (e) {
        return `⚠️ Rollback error: ${e.message}`;
      }
    }

    case 'help':
      return [
        '*ARIA — WhatsApp Commands*',
        '',
        '*Intelligence:*',
        '*/ask* [question] — Ask ARIA anything',
        '*/brief*  — Intelligence summary',
        '*/sweep*  — Trigger intel sweep',
        '*/oem* [capability] [market]',
        '*/approach* [market] [requirement]',
        '',
        '*Business Development:*',
        '*/leads* — Latest 5 generated leads',
        '*/hunt* — Trigger lead hunting cycle',
        '*/ideas* — Generate strategic ideas',
        '',
        '*Compliance:*',
        '*/screen* [entity] — Compliance pre-screening',
        '*/classify* [product] — ML classification',
        '*/sanctions* [name] — Sanctions list check',
        '*/risk* [country] — Country risk assessment',
        '',
        '*Learning:*',
        '*/teach* [topic]: [fact] — Teach ARIA a new fact',
        '*/correct* [wrong] → [right] — Correct ARIA',
        '*/feedback* [+/-] [notes] — Rate last response',
        '',
        '*Pipeline & Contacts:*',
        '*/pipeline*  — BD pipeline',
        '*/deal* [ID | new | advance]',
        '*/humint* [market] — Key contacts',
        '*/windows*  — Relationship windows',
        '*/conf* [name] — Conference brief',
        '',
        '*Self-coding (ARIA writes new modules):*',
        '*/code* [description] — Scaffold a new intel module',
        '*/staged* — List pending modules awaiting review',
        '*/deploy* [id] — Ship a staged module to production',
        '*/rollback* [id] — Undo a deployed change',
        '*/vision-status* — Check if image OCR is configured',
        '*/independence* — See ARIA\'s reasoning independence ratio',
        '',
        '*Student mode (ARIA actively learns):*',
        '*/mastery* — Per-topic competence scores',
        '*/curriculum* — What she should study next',
        '*/quiz* — Trigger an immediate self-quiz',
        '*/study* — Trigger a focused reading session',
        '',
        '*Free-form (no slash needed):*',
        '• _ARIA, investigate https://example.com_',
        '• _Crawl acmedefence.com and tell me what you find_',
        '• _Research Angola FADM procurement 2026_',
        '• _Screen ACME Ltd for sanctions_',
        '• _Profile General João Nunes_',
        '• _Read this article and summarise: <url>_',
        '• _ARIA, write a module that monitors Saudi tenders hourly_',
        '',
        '_Or mention_ *ARIA* _in any message_',
      ].join('\n');

    default:
      return null;  // unknown — pass to ARIA chat
  }
}

// ── Incoming webhook (Twilio calls this for every message) ───────────────────
router.post(
  '/incoming',
  express.urlencoded({ extended: false }),
  validateTwilioSignature,
  async (req, res) => {

    // Acknowledge immediately — Twilio requires a response within 15 seconds
    res.type('text/xml').send('<Response></Response>');

    const {
      From:        sender      = '',
      Body:        rawBody     = '',
      ProfileName: senderName  = 'Team member',
      NumMedia:    numMedia    = '0',
    } = req.body;

    const text = rawBody.trim().slice(0, 4000);  // cap input length
    const ariaServiceUrl = process.env.ARIA_SERVICE_URL;
    const numMediaInt = Math.min(parseInt(numMedia, 10) || 0, 5);

    console.log(`[WhatsApp] ${(senderName || '?').slice(0, 30)}: ${text.slice(0, 100)}${numMediaInt ? ` [+${numMediaInt} media]` : ''}`);

    // Persist any text into ARIA's signal stream
    if (text) remember(sender, senderName, text);

    // ── ARIA READS MEDIA — images, PDFs, docs, voice notes ─────────────────
    // This must run BEFORE the no-text return, because images often arrive without captions.
    //
    // BUG-FIX: previously the entire media block was guarded by
    //   `if (numMediaInt > 0 && ariaServiceUrl)`
    // which silently dropped every image when ARIA_SERVICE_URL was unset —
    // the user would send an image and get NOTHING back. Now we always
    // acknowledge the media even when the ARIA service is not reachable so
    // the user gets a clear, actionable error message.
    if (numMediaInt > 0 && !ariaServiceUrl) {
      send(sender, [
        `📥 *Got your media but ARIA service is not reachable.*`,
        ``,
        `\`ARIA_SERVICE_URL\` is not set on this server, so I can't process the file.`,
        `Set the env var pointing to the Python ARIA service (e.g. \`https://aria.your-domain.com\`) and try again.`,
      ].join('\n')).catch(() => {});
    }

    if (numMediaInt > 0 && ariaServiceUrl) {
      // Immediate ack so the user knows ARIA is working on it
      const firstType = (req.body['MediaContentType0'] || '').split(';')[0];
      const ackKind = firstType.startsWith('image/') ? 'image' :
                      firstType.startsWith('audio/') ? 'voice note' :
                      firstType.startsWith('video/') ? 'video' :
                      /pdf|word|spreadsheet|officedocument|msword|excel/.test(firstType) ? 'document' :
                      'file';
      send(sender, `📥 Got your ${ackKind}${numMediaInt > 1 ? `s (${numMediaInt})` : ''}. Reading now…`).catch(() => {});

      const auth = Buffer.from(`${TWILIO_SID}:${TWILIO_TOKEN}`).toString('base64');
      const MAX_MEDIA_BYTES = 8 * 1024 * 1024; // 8 MB cap per file

      for (let i = 0; i < numMediaInt; i++) {
        const mediaUrl  = req.body[`MediaUrl${i}`];
        const mediaType = (req.body[`MediaContentType${i}`] || '').split(';')[0];
        if (!mediaUrl) continue;

        const isImage     = /^image\//.test(mediaType);
        const isPdf       = /pdf/.test(mediaType);
        const isOffice    = /word|spreadsheet|officedocument|msword|excel|powerpoint|presentation/.test(mediaType);
        const isPlainText = /^text\//.test(mediaType) || /csv/.test(mediaType);
        const isAudio     = /^audio\//.test(mediaType);
        const isVideo     = /^video\//.test(mediaType);

        if (isAudio || isVideo) {
          // Voice notes / videos not yet supported (no transcription pipeline wired in)
          send(sender, `🔇 I can't process ${isAudio ? 'voice notes' : 'videos'} yet. Send a screenshot or text message instead.`).catch(() => {});
          continue;
        }

        if (!isImage && !isPdf && !isOffice && !isPlainText) {
          send(sender, `❓ Unsupported file type: ${mediaType || 'unknown'}.`).catch(() => {});
          continue;
        }

        try {
          // Download the media from Twilio
          const mediaResp = await fetch(mediaUrl, {
            headers: { 'Authorization': `Basic ${auth}` },
            signal: AbortSignal.timeout(30000),
          });
          if (!mediaResp.ok) {
            console.warn(`[WhatsApp] Media download failed: ${mediaResp.status}`);
            send(sender, `⚠️ Couldn't download your file from Twilio (${mediaResp.status}).`).catch(() => {});
            continue;
          }

          const fullBuf = Buffer.from(await mediaResp.arrayBuffer());
          if (fullBuf.length === 0) {
            send(sender, `⚠️ The file appears to be empty.`).catch(() => {});
            continue;
          }
          // Slice the BYTES (not the base64 string!) before encoding to avoid mid-character corruption
          const buf = fullBuf.length > MAX_MEDIA_BYTES ? fullBuf.subarray(0, MAX_MEDIA_BYTES) : fullBuf;
          if (fullBuf.length > MAX_MEDIA_BYTES) {
            console.log(`[WhatsApp] Media truncated: ${fullBuf.length} → ${MAX_MEDIA_BYTES} bytes`);
          }

          const ext = (mediaType.split('/')[1] || 'bin').replace(/[^a-z0-9]/gi, '');
          const filename = `whatsapp_${Date.now()}_${i}.${ext}`;
          const sizeKb = Math.round(buf.length / 102.4) / 10;

          // ── IMAGES → /api/aria/ocr (LLM vision) → /api/aria/read-document for fact extraction
          if (isImage) {
            const b64 = buf.toString('base64');
            console.log(`[WhatsApp] OCR: ${filename} (${sizeKb} KB, ${mediaType})`);

            let ocrResult = null;
            try {
              const ocrResp = await fetch(`${ariaServiceUrl}/api/aria/ocr`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                  image: b64,
                  filename,
                  context: text
                    ? `Image shared via WhatsApp by ${senderName}. Caption: ${text.slice(0, 300)}`
                    : `Image shared via WhatsApp by ${senderName} (no caption)`,
                }),
                signal: AbortSignal.timeout(120000),
              });
              if (ocrResp.ok) ocrResult = await ocrResp.json();
              else {
                const errText = (await ocrResp.text()).slice(0, 200);
                console.warn(`[WhatsApp] OCR failed: ${ocrResp.status} ${errText}`);
              }
            } catch (e) {
              console.warn('[WhatsApp] OCR request error:', e.message);
            }

            const extracted = (ocrResult?.text || '').trim();
            if (!extracted) {
              // Distinguish "no text in image" from "no OCR backend installed"
              // by hitting /api/aria/vision-status — true if ANY backend is wired up
              // (local EasyOCR/Tesseract/Ollama OR optional cloud fallback)
              let visionConfigured = false;
              try {
                const vs = await fetch(`${ariaServiceUrl}/api/aria/vision-status`, {
                  signal: AbortSignal.timeout(5000),
                });
                if (vs.ok) {
                  const vd = await vs.json();
                  visionConfigured = !!vd.ok;
                }
              } catch {}

              // ARIA service has triggered background auto-install of easyocr
              // and falls back to OCR.space free public API on the next call,
              // so the user just needs to know the image was unreadable —
              // no install instructions wall.
              send(sender, [
                `🖼 I looked at *${filename}* but couldn't extract readable text.`,
                ``,
                `It may be blank, low-resolution, contain only diagrams, or be partially obscured.`,
                ``,
                `_If this is unexpected, my local image-reading library is auto-installing in the background — try sending the image again in a minute._`,
              ].join('\n')).catch(() => {});
              continue;
            }

            const method = ocrResult.method || 'vision';
            const charCount = extracted.length;
            console.log(`[WhatsApp] OCR ${method}: ${charCount} chars from ${filename}`);

            // Feed the extracted text into the knowledge pipeline so ARIA learns from it
            let factsLearned = 0;
            try {
              const docResp = await fetch(`${ariaServiceUrl}/api/aria/read-document`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                  content: extracted.slice(0, 15000),
                  filename,
                  source: `whatsapp:${senderName}`,
                  context: text || `Image OCR from WhatsApp (${senderName})`,
                  encoding: 'utf-8',
                  mimetype: 'text/plain',
                }),
                signal: AbortSignal.timeout(120000),
              });
              if (docResp.ok) {
                const dr = await docResp.json();
                factsLearned = dr.facts_learned || 0;
              }
            } catch (e) {
              console.warn('[WhatsApp] Image-to-knowledge ingest failed:', e.message);
            }

            // Reply with a preview of what ARIA read
            const preview = extracted.slice(0, 600).replace(/\n{3,}/g, '\n\n');
            const more = extracted.length > 600 ? `\n\n_…+${extracted.length - 600} more chars_` : '';
            const factsLine = factsLearned > 0 ? `\n\n📚 Learned ${factsLearned} new fact(s) — ask me about them.` : '';
            send(sender, `🖼 *Image read* (${method}, ${charCount} chars):\n\n${preview}${more}${factsLine}`).catch(() => {});
            continue;
          }

          // ── DOCUMENTS (PDF / DOCX / XLSX / TXT / CSV) → /api/aria/read-document
          const isBinaryDoc = isPdf || isOffice;
          const content = isBinaryDoc
            ? buf.toString('base64')                       // FULL base64 of the (already byte-sliced) buffer
            : buf.toString('utf-8').slice(0, 15000);
          const encoding = isBinaryDoc ? 'base64' : 'utf-8';
          console.log(`[WhatsApp] Document ingest: ${filename} (${sizeKb} KB, encoding=${encoding})`);

          try {
            const docResp = await fetch(`${ariaServiceUrl}/api/aria/read-document`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                content,
                filename,
                source: `whatsapp:${senderName}`,
                context: text || `Document from ${senderName}`,
                encoding,
                mimetype: mediaType,
              }),
              signal: AbortSignal.timeout(180000),
            });
            if (!docResp.ok) {
              const errText = (await docResp.text()).slice(0, 200);
              console.warn(`[WhatsApp] Document ingest failed: ${docResp.status} ${errText}`);
              send(sender, `⚠️ I couldn't read *${filename}* (${docResp.status}). ${errText}`).catch(() => {});
              continue;
            }
            const result = await docResp.json();
            const facts = result.facts_learned || 0;
            const summary = result.summary || `${sizeKb} KB ${ext.toUpperCase()}`;
            send(sender, `📄 *${filename}* read.\n${summary}${facts ? `\n📚 Learned ${facts} fact(s).` : ''}\n\nAsk me anything about it.`).catch(() => {});
          } catch (e) {
            console.warn('[WhatsApp] Document ingest error:', e.message);
            send(sender, `⚠️ Failed to process *${filename}*: ${e.message}`).catch(() => {});
          }
        } catch (e) {
          console.warn('[WhatsApp] Media processing failed:', e.message);
          send(sender, `⚠️ Media processing error: ${e.message}`).catch(() => {});
        }
      }
    }

    // If only media was sent (no text), we're done — media handler already replied.
    if (!text) return;

    // ── ARIA READS — extract and learn from URLs in messages ────────────────
    if (ariaServiceUrl) {
      const urls = text.match(/https?:\/\/[^\s<>"'\]\)]+/gi) || [];
      for (const articleUrl of urls) {
        if (/\.(jpg|jpeg|png|gif|mp4|pdf)$/i.test(articleUrl)) continue;
        if (/^https?:\/\/(wa\.me|chat\.whatsapp|t\.me|twitter|x\.com|facebook|instagram)/i.test(articleUrl)) continue;
        console.log(`[WhatsApp] ARIA reading URL: ${articleUrl.slice(0, 80)}`);
        fetch(`${ariaServiceUrl}/api/aria/read`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url: articleUrl, context: `Shared by ${senderName} on WhatsApp: ${text.slice(0, 200)}` }),
          signal: AbortSignal.timeout(120000),
        }).then(r => r.ok ? r.json() : null).then(result => {
          if (result?.facts_learned > 0) {
            console.log(`[WhatsApp→ARIA] Read: ${articleUrl.slice(0, 60)} → ${result.facts_learned} facts`);
            send(sender, `📖 I read that article and learned ${result.facts_learned} new fact(s). Ask me about it.`).catch(() => {});
          }
        }).catch(() => {});
      }
    }

    const type = classify(text);

    // ARIA RESPONDS for: explicit slash commands, @aria mentions, or clear free-form requests.
    // Everything else (passive observation, group chatter) is silently stored only.
    if (type !== 'command' && type !== 'mention' && type !== 'request') return;

    // Rate limiting — prevent abuse
    if (isRateLimited(sender)) {
      await send(sender, '⚠️ Too many requests. Please wait a moment.');
      return;
    }

    let response = null;

    try {
      if (type === 'command') {
        const m = text.match(COMMAND_RE);
        if (!m) return;
        const cmd = m[1];
        const arg = (m[2] || '').trim();
        response  = await handleCommand(cmd, arg, sender);
        if (!response) {
          // Unknown slash → fall through to ARIA chat (with tool-use)
          response = await askARIA(text, '', sender);
        }

      } else if (type === 'mention') {
        const q = text.replace(/^@?aria[,:?\s]*/i, '').trim() || text;
        response = await askARIA(q, '', sender);

      } else if (type === 'request') {
        // Free-form imperative — "investigate this URL", "crawl example.com",
        // "research Angola tenders", "screen ACME Ltd". Send the full message;
        // the chat endpoint detects intent server-side and runs the right tool.
        response = await askARIA(text, '', sender);
      }
    } catch (e) {
      console.error('[WhatsApp] Handler error:', e.message);
      response = '⚠️ Something went wrong. Try again or use /help for direct commands.';
    }

    if (response) {
      await send(sender, response);
    }
  }
);

// ── Status ────────────────────────────────────────────────────────────────────
router.get('/status', (_req, res) => {
  res.json({
    status:        'active',
    aria_number:   FROM ? FROM.replace('whatsapp:', '') : 'not configured',
    twilio_ready:  twilioReady,
    twilio_error:  twilioError || undefined,
    twilio_from:   FROM || 'not set',
    twilio_sid:    TWILIO_SID ? `${TWILIO_SID.slice(0, 6)}...` : 'not set',
    conversations: memory.size,
    setup_guide: FROM
      ? 'ARIA is active. Add ' + FROM.replace('whatsapp:','') + ' to your WhatsApp contacts.'
      : 'Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM in Seenode env vars.',
    webhook_url: 'https://intel.sursec.co.uk/api/whatsapp/incoming',
  });
});

export default router;

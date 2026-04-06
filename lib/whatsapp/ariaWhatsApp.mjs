/**
 * ARIA — WhatsApp via Twilio
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * ROLE SPLIT
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

const router = express.Router();

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
async function askARIA(message, context = '', sender = 'whatsapp') {
  // Persistent session per sender — ARIA remembers multi-turn conversations
  const sid = `twilio_${sender.replace(/[^a-zA-Z0-9_+]/g, '')}`;
  const fullMessage = context
    ? `[Group context]\n${context}\n\n[Question]\n${message}`
    : message;

  try {
    const r = await brainPost('/api/aria/chat', { message: fullMessage, session_id: sid });
    return r.response || r.answer || 'No response.';
  } catch (e) {
    console.error('[WhatsApp] ARIA chat failed:', e.message);
    return '⚠️ ARIA is temporarily unavailable.';
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
      return await askARIA(a, recall(sender, 10), sender);
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

    // Handle media files gracefully
    if (numMedia !== '0' && !text) {
      const mediaType = (req.body['MediaContentType0'] || 'file').split(';')[0].slice(0, 50);
      const safeName  = (senderName || 'someone').slice(0, 50);
      console.log(`[WhatsApp] Media from ${safeName}: ${mediaType}`);
      await send(sender, `📎 ARIA received a ${mediaType.split('/')[0]} from ${safeName}. Describe what you need help with.`);
      return;
    }

    if (!text) return;

    console.log(`[WhatsApp] ${(senderName || '?').slice(0, 30)}: ${text.slice(0, 100)}`);

    // ── ARIA LISTENS — stores every message into her knowledge base ──────────
    remember(sender, senderName, text);

    // ── ARIA READS — extract and learn from URLs in messages ────────────────
    const ariaServiceUrl = process.env.ARIA_SERVICE_URL;
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

    // ── ARIA READS DOCUMENTS — process media attachments ────────────────────
    if (numMedia !== '0' && text) {
      // Twilio provides media URLs — download and send to ARIA
      for (let i = 0; i < Math.min(parseInt(numMedia) || 0, 3); i++) {
        const mediaUrl = req.body[`MediaUrl${i}`];
        const mediaType = (req.body[`MediaContentType${i}`] || '').split(';')[0];
        if (mediaUrl && /pdf|word|spreadsheet|text|csv/.test(mediaType)) {
          console.log(`[WhatsApp] ARIA processing attachment: ${mediaType}`);
          try {
            const auth = Buffer.from(`${TWILIO_SID}:${TWILIO_TOKEN}`).toString('base64');
            const mediaResp = await fetch(mediaUrl, {
              headers: { 'Authorization': `Basic ${auth}` },
              signal: AbortSignal.timeout(30000),
            });
            if (mediaResp.ok && ariaServiceUrl) {
              const buf = Buffer.from(await mediaResp.arrayBuffer());
              const docFilename = `whatsapp_${mediaType.split('/')[1] || 'doc'}`;
              const docType = mediaType.split('/')[1] || 'document';
              const isBinary = /pdf|word|spreadsheet|octet-stream|msword|officedocument/.test(mediaType);
              const content = isBinary
                ? buf.toString('base64').slice(0, 200000)
                : buf.toString('utf-8').slice(0, 15000);
              if (content.length > 50) {
                fetch(`${ariaServiceUrl}/api/aria/read-document`, {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({
                    content,
                    filename: docFilename,
                    source: `whatsapp:${senderName}`,
                    context: text || `Document from ${senderName}`,
                    encoding: isBinary ? 'base64' : 'utf-8',
                    mimetype: mediaType,
                  }),
                  signal: AbortSignal.timeout(180000),
                }).then(r => r.ok ? r.json() : null).then(result => {
                  if (result) {
                    const facts = result.facts_learned || 0;
                    const summary = result.summary || `${docType} file, ${content.length} characters`;
                    console.log(`[WhatsApp→ARIA] Doc analysed → ${facts} facts`);
                    send(sender, `📄 I've read *${docFilename}*. ${summary} (${content.length} chars, ${docType}). Ask me anything about it.`).catch(() => {});
                  }
                }).catch(() => {});
              }
            }
          } catch (e) {
            console.warn('[WhatsApp] Media processing failed:', e.message);
          }
        }
      }
    }

    const type = classify(text);

    // Everything that is not a direct command or mention → store silently, do nothing
    if (type !== 'command' && type !== 'mention') return;

    // Rate limiting — prevent abuse
    if (isRateLimited(sender)) {
      await send(sender, '⚠️ Too many requests. Please wait a moment.');
      return;
    }

    // ── ARIA RESPONDS — only when directly asked ──────────────────────────────
    let response = null;

    try {
      if (type === 'command') {
        const m = text.match(COMMAND_RE);
        if (!m) return;
        const cmd = m[1];
        const arg = (m[2] || '').trim();
        response  = await handleCommand(cmd, arg, sender);
        if (!response) {
          response = await askARIA(text, recall(sender, 10), sender);
        }

      } else if (type === 'mention') {
        const q = text.replace(/^@?aria[,:?\s]*/i, '').trim() || text;
        response = await askARIA(q, recall(sender, 10), sender);
      }
    } catch (e) {
      console.error('[WhatsApp] Command error:', e.message);
      response = '⚠️ Something went wrong. Try again or use /help.';
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

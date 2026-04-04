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
 * Your team adds ARIA's number to their contacts like any other WhatsApp
 * contact. The group admin adds her to the Arkmurus group. Done.
 *
 * ARIA's WhatsApp profile:
 *   Display name:  ARIA — Arkmurus Intelligence
 *   Category:      Business Services
 *   Description:   Arkmurus Research Intelligence Agent
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

const router = express.Router();

// ── Config ────────────────────────────────────────────────────────────────────
const TWILIO_SID   = process.env.TWILIO_ACCOUNT_SID    || '';
const TWILIO_TOKEN = process.env.TWILIO_AUTH_TOKEN      || '';
const FROM         = process.env.TWILIO_WHATSAPP_FROM   || '';
const BRAIN        = process.env.BRAIN_SERVICE_URL      || 'http://localhost:5001';
const LLM          = process.env.ARIA_LLM_URL           || 'http://localhost:5050';
const INT_TOKEN    = process.env.ARIA_INTERNAL_TOKEN    || 'aria-internal';

let twilioClient = null;
if (TWILIO_SID && TWILIO_TOKEN) {
  try {
    const twilio = (await import('twilio')).default;
    twilioClient = twilio(TWILIO_SID, TWILIO_TOKEN);
  } catch (e) {
    console.warn('[WhatsApp] twilio package not installed — run: npm install twilio');
  }
}

// ── Twilio webhook signature validation ──────────────────────────────────────
function validateTwilioSignature(req, res, next) {
  if (!TWILIO_TOKEN) return next(); // skip in dev when no token set

  const sig  = req.headers['x-twilio-signature'];
  if (!sig) return res.status(403).send('Missing signature');

  // Reconstruct the full URL Twilio signed against
  const proto = req.headers['x-forwarded-proto'] || req.protocol;
  const url   = `${proto}://${req.headers.host}${req.originalUrl}`;

  // Sort POST params alphabetically, concatenate key+value
  const params = Object.keys(req.body || {}).sort().reduce((s, k) => s + k + req.body[k], '');
  const expected = crypto
    .createHmac('sha1', TWILIO_TOKEN)
    .update(url + params)
    .digest('base64');

  if (sig === expected) return next();
  console.warn('[WhatsApp] Invalid Twilio signature — rejecting request');
  return res.status(403).send('Invalid signature');
}

// ── Conversation memory ───────────────────────────────────────────────────────
// In-memory ring buffer per chat — persists across requests, clears on restart.
// Brain service is the durable store; this is just for short-term context.
const memory = new Map();

function remember(chatId, sender, text) {
  if (!memory.has(chatId)) memory.set(chatId, []);
  const hist = memory.get(chatId);
  hist.push({ sender, text, ts: new Date().toISOString() });
  if (hist.length > 60) hist.splice(0, hist.length - 60);

  // Persist to brain asynchronously — fire and forget
  brainPost('/api/brain/signal', {
    content:     text,
    source:      `whatsapp:${sender}:${chatId}`,
    signal_type: 'whatsapp_conversation',
    trigger:     'message',
    metadata:    { sender_name: sender },
  }).catch(() => {});
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
  if (COMMAND_RE.test(text))                    return 'command';
  if (MENTIONS.some(p => p.test(text)))         return 'mention';
  if (COMPLIANCE_KW.some(p => p.test(text)))    return 'compliance';
  if (INTEL_KW.some(p => p.test(text)))         return 'intel';
  return 'observe';
}

// ── Send WhatsApp message ─────────────────────────────────────────────────────
async function send(to, body) {
  if (!twilioClient || !FROM || !to || !body) {
    if (!twilioClient) console.warn('[WhatsApp] Twilio not configured — see setup instructions at top of file');
    return;
  }
  try {
    await twilioClient.messages.create({
      from: FROM,
      to,
      body: body.slice(0, 4096),
    });
  } catch (e) {
    console.error('[WhatsApp] Send failed:', e.message);
  }
}

// ── CRUCIX brain API ──────────────────────────────────────────────────────────
async function brainGet(path) {
  const r = await fetch(`${BRAIN}${path}`);
  if (!r.ok) throw new Error(`Brain GET ${path} → ${r.status}`);
  return r.json();
}

async function brainPost(path, body) {
  const r = await fetch(`${BRAIN}${path}`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`Brain POST ${path} → ${r.status}`);
  return r.json();
}

// ── ARIA LLM ──────────────────────────────────────────────────────────────────
async function askARIA(message, context = '') {
  try {
    // Auth
    const authR = await fetch(`${LLM}/api/auth/login`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ email: 'whatsapp@crucix.internal', password: INT_TOKEN }),
    });
    const { token } = await authR.json();

    const messages = [];
    if (context) messages.push({ role: 'system', content: `Group context:\n${context}` });
    messages.push({ role: 'user', content: message });

    const r = await fetch(`${LLM}/api/aria/chat/stream`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body:    JSON.stringify({
        messages,
        stream:         true,
        temperature:    0.3,
        max_tokens:     700,   // WhatsApp messages should be concise
        tools_enabled:  true,
        memory_enabled: true,
      }),
    });

    let full = '', buf = '';
    for await (const chunk of r.body) {
      buf += chunk.toString();
      const lines = buf.split('\n');
      buf = lines.pop();
      for (const ln of lines) {
        if (!ln.startsWith('data:')) continue;
        try {
          const ev = JSON.parse(ln.slice(5).trim());
          if (ev.type === 'token') full += ev.content;
        } catch (e) {}
      }
    }
    return full.slice(0, 1500) || 'No response.';

  } catch (e) {
    // Fallback to simpler brain chat
    try {
      const r = await brainPost('/api/aria/chat', { message, session_id: 'whatsapp' });
      return (r.response || r.answer || '⚠️ ARIA temporarily unavailable.').slice(0, 1500);
    } catch (e2) {
      return '⚠️ ARIA is offline. Check CRUCIX brain service.';
    }
  }
}

// ── Command handlers ──────────────────────────────────────────────────────────
async function handleCommand(cmd, args, sender) {
  const a = (args || '').trim();

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
      const d = await brainPost('/api/brain/counterparty-risk', { entity_name: a }).catch(() => ({}));
      const ok = d.result === 'PERMITTED';
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
        msg += `Route: ${o.contact_route}\n`;
        if (o.lusophone_experience) msg += `✅ Lusophone track record\n`;
        if (o.itar_controlled)      msg += `⚠️ ITAR controlled\n`;
        msg += '\n';
      });
      const comps = (d.competitors || []).slice(0, 3);
      if (comps.length) msg += `⚠️ *Active competitors:* ${comps.map(c => c.name).join(', ')}\n`;
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
        msg += `*${c.full_name}*${w}\n${c.role}\n${c.contact_route}\n\n`;
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

    case 'help':
      return [
        '*ARIA — WhatsApp Commands*',
        '',
        '*/brief*  — Intelligence summary',
        '*/sweep*  — Trigger intel sweep',
        '*/screen* [entity] — Compliance check',
        '*/oem* [capability] [market]',
        '*/approach* [market] [requirement]',
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

    const text = rawBody.trim();

    // Handle media files gracefully
    if (numMedia !== '0' && !text) {
      const mediaType = req.body['MediaContentType0'] || 'file';
      console.log(`[WhatsApp] Media from ${senderName}: ${mediaType}`);
      await send(sender, `📎 ARIA received a ${mediaType.split('/')[0]} from ${senderName}. Describe what you need help with.`);
      return;
    }

    if (!text) return;

    console.log(`[WhatsApp] ${senderName}: ${text.slice(0, 100)}`);

    // ── ARIA LISTENS — stores every message into her knowledge base ──────────
    remember(sender, senderName, text);

    const type = classify(text);

    // Everything that is not a direct command or mention → store silently, do nothing
    if (type !== 'command' && type !== 'mention') return;

    // ── ARIA RESPONDS — only when directly asked ──────────────────────────────
    let response = null;

    if (type === 'command') {
      const m   = text.match(COMMAND_RE);
      const cmd = m[1];
      const arg = (m[2] || '').trim();
      response  = await handleCommand(cmd, arg, sender);
      if (!response) {
        // Unrecognised command — treat as a direct question to ARIA
        response = await askARIA(text, recall(sender, 10));
      }

    } else if (type === 'mention') {
      // Strip the mention trigger and answer the actual question
      const q = text.replace(/^@?aria[,:?\s]*/i, '').trim() || text;
      response = await askARIA(q, recall(sender, 10));
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
    twilio_ready:  !!twilioClient,
    conversations: memory.size,
    setup_guide: FROM
      ? 'ARIA is active. Add ' + FROM.replace('whatsapp:','') + ' to your WhatsApp contacts.'
      : 'Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM in Seenode env vars.',
  });
});

export default router;

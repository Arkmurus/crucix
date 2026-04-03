/**
 * CRUCIX — Telegram Intelligence Terminal
 * GAP 3 FIX: ARKMURUS 8-section briefing format
 * GAP 10 FIX: /aria command wiring to Python brain
 *
 * Integrates with your existing Telegram bot in server.mjs.
 * Replace your current /brief handler and add the /aria handler.
 */

import { ariaChat as ariaLocalChat, ariaThink as ariaLocalThink } from '../aria/aria.mjs';

const BRAIN_URL     = process.env.BRAIN_SERVICE_URL;  // optional — falls back to local LLM
const LOCAL_PORT    = process.env.PORT || 3117;
const BOT_TOKEN     = process.env.TELEGRAM_BOT_TOKEN;
const TELEGRAM_API  = `https://api.telegram.org/bot${BOT_TOKEN}`;

// Injected by server.mjs after startup via setLLMProvider()
let _llmProvider = null;
export function setLLMProvider(p) { _llmProvider = p; }

// ── ARIA proxy helpers — brain service first, local LLM fallback ──────────────
async function ariaThinkProxy(question, context = {}, fast = false) {
  if (BRAIN_URL) {
    try {
      const r = await fetch(`${BRAIN_URL}/api/aria/think`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question, context, fast }), signal: AbortSignal.timeout(90000),
      });
      if (r.ok) return await r.json();
    } catch {}
  }
  return ariaLocalThink(question, context, _llmProvider);
}

async function ariaChatProxy(message, sessionId) {
  if (BRAIN_URL) {
    try {
      const r = await fetch(`${BRAIN_URL}/api/aria/chat`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message, session_id: sessionId }), signal: AbortSignal.timeout(30000),
      });
      if (r.ok) return await r.json();
    } catch {}
  }
  return ariaLocalChat(message, sessionId, _llmProvider);
}

// ── Telegram send helpers ─────────────────────────────────────────────────────

export async function sendTelegramMessage(chatId, text, parseMode = 'Markdown') {
  if (!BOT_TOKEN) return;
  try {
    const resp = await fetch(`${TELEGRAM_API}/sendMessage`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({
        chat_id:    chatId,
        text:       text.slice(0, 4096),   // Telegram hard limit
        parse_mode: parseMode,
        disable_web_page_preview: true,
      }),
    });
    if (!resp.ok) {
      const err = await resp.json();
      console.error('[Telegram] Send failed:', err.description);
    }
  } catch (e) {
    console.error('[Telegram] Network error:', e.message);
  }
}

async function sendLongMessage(chatId, text) {
  // Split into 4000-char chunks at paragraph boundaries
  const chunks = [];
  let remaining = text;
  while (remaining.length > 0) {
    if (remaining.length <= 4000) {
      chunks.push(remaining);
      break;
    }
    let cutAt = remaining.lastIndexOf('\n\n', 4000);
    if (cutAt === -1) cutAt = remaining.lastIndexOf('\n', 4000);
    if (cutAt === -1) cutAt = 4000;
    chunks.push(remaining.slice(0, cutAt));
    remaining = remaining.slice(cutAt).trim();
  }
  for (const chunk of chunks) {
    await sendTelegramMessage(chatId, chunk);
    await new Promise(r => setTimeout(r, 300));  // avoid flood limits
  }
}

// ── Section Formatters ────────────────────────────────────────────────────────

function formatMarketPosture(brief) {
  const opps   = brief.priority_procurement_windows || [];
  const topMkt = opps[0]?.market || 'N/A';
  const alerts = brief.compliance_alerts || [];

  return (
    `*1️⃣ MARKET POSTURE*\n` +
    `Lead market: *${topMkt}*\n` +
    `Active procurement windows: *${opps.length}*\n` +
    `Compliance alerts: *${alerts.length > 0 ? '⚠ ' + alerts.length + ' active' : '✅ None'}*\n` +
    `Source integrity: *${brief.source_integrity_score ?? 'N/A'}/100*`
  );
}

function formatLusophoneIntel(brief) {
  const items = brief.cplp_lusophone_intelligence || [];
  if (items.length === 0) return '*2️⃣ LUSOPHONE FOCUS*\n_No new Lusophone signals this sweep._';

  const lines = items.slice(0, 4).map((item, i) =>
    `${i + 1}. ${typeof item === 'string' ? item : item.signal || JSON.stringify(item)}`
  );
  return `*2️⃣ LUSOPHONE FOCUS*\n${lines.join('\n')}`;
}

function formatProcurementSignals(brief) {
  const windows = brief.priority_procurement_windows || [];
  if (windows.length === 0) return '*3️⃣ PROCUREMENT SIGNALS*\n_No active procurement windows._';

  const lines = windows.slice(0, 5).map((w, i) => {
    const market   = w.market || 'Unknown';
    const desc     = w.description || w.tender || w.signal || 'See brief';
    const value    = w.value ? ` | ~$${Number(w.value).toLocaleString()}` : '';
    const deadline = w.deadline ? ` | Due: ${w.deadline}` : '';
    return `${i + 1}. *${market}* — ${String(desc).slice(0, 120)}${value}${deadline}`;
  });
  return `*3️⃣ PROCUREMENT SIGNALS*\n${lines.join('\n')}`;
}

function formatComplianceAlerts(brief) {
  const alerts = brief.compliance_alerts || [];
  if (alerts.length === 0) return '*4️⃣ COMPLIANCE ALERTS*\n✅ No new compliance flags.';

  const lines = alerts.slice(0, 4).map((a, i) => {
    const text = typeof a === 'string' ? a : a.alert || a.description || JSON.stringify(a);
    return `⚠ ${i + 1}. ${String(text).slice(0, 200)}`;
  });
  return `*4️⃣ COMPLIANCE ALERTS*\n${lines.join('\n')}`;
}

function formatThreatAssessment(brief) {
  const conflicts = brief.conflict_security_situation || {};
  const markets   = Object.entries(conflicts);
  if (markets.length === 0) return '*5️⃣ THREAT ASSESSMENT*\n_No conflict signals._';

  const lines = markets.slice(0, 4).map(([mkt, data]) => {
    const summary = typeof data === 'string' ? data : data.summary || data.level || 'Monitor';
    return `• *${mkt}*: ${String(summary).slice(0, 120)}`;
  });
  return `*5️⃣ THREAT ASSESSMENT*\n${lines.join('\n')}`;
}

function formatOpportunities(brief) {
  const actions = brief.recommended_actions_this_week || [];
  const intel   = brief.political_access_intelligence || [];

  const opps = [...intel, ...actions].slice(0, 4).map((item, i) => {
    const text = typeof item === 'string' ? item : item.action || item.signal || JSON.stringify(item);
    return `${i + 1}. ${String(text).slice(0, 200)}`;
  });

  if (opps.length === 0) return '*6️⃣ OPPORTUNITIES*\n_No active opportunity signals._';
  return `*6️⃣ OPPORTUNITIES*\n${opps.join('\n')}`;
}

function formatRecommendedActions(brief) {
  const actions = brief.recommended_actions_this_week || [];
  if (actions.length === 0) return '*7️⃣ ACTIONS THIS WEEK*\n_No specific actions generated._';

  const lines = actions.slice(0, 5).map((a, i) => {
    const text = typeof a === 'string' ? a : a.action || a.what || JSON.stringify(a);
    return `${i + 1}. ${String(text).slice(0, 250)}`;
  });
  return `*7️⃣ ACTIONS THIS WEEK*\n${lines.join('\n')}`;
}

function formatARIASummary(brief, ariaIdentity) {
  const age    = ariaIdentity?.age_days   ?? '?';
  const sweeps = ariaIdentity?.total_sweeps ?? '?';
  const score  = brief.source_integrity_score ?? '?';

  return (
    `*8️⃣ ARIA SUMMARY*\n` +
    `_ARIA — ${age} days operational | ${sweeps} sweeps completed_\n` +
    `Source integrity this sweep: *${score}/100*\n` +
    `Admitted weakness: _${ariaIdentity?.admitted_weakness || 'Tracking...'}._\n` +
    `_"${ariaIdentity?.strongest_skill || 'Lusophone Africa intelligence.'}"_`
  );
}

// ── ARKMURUS 8-Section Brief Builder ─────────────────────────────────────────

export async function buildArkmursBrief(chatId) {
  // Fetch brief from brain service
  let brief = null;
  let ariaIdentity = null;

  try {
    const [briefResp, identityResp] = await Promise.all([
      fetch(`${BRAIN_URL}/api/brain/brief`, { signal: AbortSignal.timeout(15000) }),
      fetch(`${BRAIN_URL}/api/aria/identity`, { signal: AbortSignal.timeout(10000) }),
    ]);
    if (briefResp.ok)    brief       = await briefResp.json();
    if (identityResp.ok) ariaIdentity = await identityResp.json();
  } catch (e) {
    console.error('[Telegram] Brief fetch failed:', e.message);
  }

  if (!brief) {
    await sendTelegramMessage(chatId,
      '⚠ *CRUCIX Brief Unavailable*\nBrain service is offline or no sweep has completed yet. ' +
      'Run `/sweep` to generate intelligence.');
    return;
  }

  const now     = new Date().toUTCString().slice(0, 25);
  const header  = `🧠 *ARKMURUS INTELLIGENCE BRIEF*\n_${now} UTC_\n${'─'.repeat(30)}`;
  const footer  = `\n${'─'.repeat(30)}\n_Powered by CRUCIX | ARIA v${ariaIdentity?.age_days ?? '?'}d_`;

  const sections = [
    formatMarketPosture(brief),
    formatLusophoneIntel(brief),
    formatProcurementSignals(brief),
    formatComplianceAlerts(brief),
    formatThreatAssessment(brief),
    formatOpportunities(brief),
    formatRecommendedActions(brief),
    formatARIASummary(brief, ariaIdentity),
  ];

  const fullBrief = [header, ...sections, footer].join('\n\n');
  await sendLongMessage(chatId, fullBrief);
}

// ── ARIA Telegram Commands ────────────────────────────────────────────────────
// GAP 10: Wire these into your existing Telegram webhook handler

export async function handleAriaCommand(chatId, userId, args) {
  if (!args || args.trim().length === 0) {
    await sendTelegramMessage(chatId,
      '🧠 *ARIA Commands*\n\n' +
      '`/aria who are you` — Identity\n' +
      '`/aria status` — Current status\n' +
      '`/aria curiosity` — Open investigation threads\n' +
      '`/aria think: [question]` — Deep analysis (60s)\n' +
      '`/aria ask: [question]` — Quick question\n' +
      '`/aria reflect` — Weekly self-improvement\n\n' +
      '_ARIA is the Arkmurus Research Intelligence Agent._'
    );
    return;
  }

  const argsLower = args.toLowerCase().trim();

  // ── /aria status ──────────────────────────────────────────────────────────
  if (argsLower === 'status') {
    try {
      const resp = await fetch(`${BRAIN_URL}/api/aria/identity`, { signal: AbortSignal.timeout(10000) });
      if (!resp.ok) throw new Error('Brain offline');
      const identity = await resp.json();
      const msg = (
        `🧠 *ARIA Status*\n\n` +
        `Age: *${identity.age_days} days*\n` +
        `Total sweeps: *${identity.total_sweeps}*\n` +
        `Total leads: *${identity.total_leads}*\n` +
        `Strongest skill: _${identity.strongest_skill}_\n` +
        `Admitted weakness: _${identity.admitted_weakness}_\n\n` +
        `Known biases:\n${(identity.known_biases || []).map(b => `• ${b}`).join('\n')}`
      );
      await sendTelegramMessage(chatId, msg);
    } catch (e) {
      await sendTelegramMessage(chatId, '⚠ ARIA brain service is offline.');
    }
    return;
  }

  // ── /aria curiosity ───────────────────────────────────────────────────────
  if (argsLower === 'curiosity') {
    try {
      const resp = await fetch(`${BRAIN_URL}/api/aria/curiosity`, { signal: AbortSignal.timeout(10000) });
      if (!resp.ok) throw new Error('Brain offline');
      const data    = await resp.json();
      const threads = data.open_threads || [];
      if (threads.length === 0) {
        await sendTelegramMessage(chatId, '🧠 ARIA has no open investigation threads. Run a sweep first.');
        return;
      }
      const lines = threads.slice(0, 5).map((t, i) =>
        `${i + 1}. _${t.question}_\n   Raised: ${t.raised_at}`
      );
      await sendTelegramMessage(chatId,
        `🧠 *ARIA Open Intelligence Questions*\n\n${lines.join('\n\n')}\n\n` +
        `_To resolve: \`/aria resolve: [question] | [answer]\`_`
      );
    } catch (e) {
      await sendTelegramMessage(chatId, '⚠ ARIA brain service is offline.');
    }
    return;
  }

  // ── /aria reflect ─────────────────────────────────────────────────────────
  if (argsLower === 'reflect') {
    await sendTelegramMessage(chatId, '🧠 _ARIA is reflecting on her performance... (30-60s)_');
    try {
      const resp = await fetch(`${BRAIN_URL}/api/aria/reflect`, {
        method: 'POST', signal: AbortSignal.timeout(90000),
      });
      if (!resp.ok) throw new Error('Reflection failed');
      const r = await resp.json();
      const msg = (
        `🧠 *ARIA Weekly Self-Reflection*\n\n` +
        `Self-grade: *${r.self_grade || '?'}*\n` +
        `_${r.honest_assessment || ''}_\n\n` +
        `*Working well:*\n${(r.working_well || []).slice(0, 3).map(x => `• ${x}`).join('\n')}\n\n` +
        `*Consistently missing:*\n${(r.consistently_missing || []).slice(0, 3).map(x => `• ${x}`).join('\n')}\n\n` +
        `*Changes next week:*\n${(r.approach_changes_next_week || []).slice(0, 3).map(x => `• ${x}`).join('\n')}`
      );
      await sendTelegramMessage(chatId, msg);
    } catch (e) {
      await sendTelegramMessage(chatId, `⚠ Reflection failed: ${e.message}`);
    }
    return;
  }

  // ── /aria resolve: [question] | [answer] ──────────────────────────────────
  if (argsLower.startsWith('resolve:')) {
    const parts    = args.slice(8).split('|');
    const question = parts[0]?.trim();
    const answer   = parts[1]?.trim();
    if (!question || !answer) {
      await sendTelegramMessage(chatId, '⚠ Format: `/aria resolve: [question] | [answer]`');
      return;
    }
    try {
      await fetch(`${BRAIN_URL}/api/aria/curiosity/resolve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question, answer }),
        signal: AbortSignal.timeout(10000),
      });
      await sendTelegramMessage(chatId, `✅ ARIA has logged the answer to: _"${question.slice(0, 100)}"_`);
    } catch (e) {
      await sendTelegramMessage(chatId, `⚠ Could not resolve thread: ${e.message}`);
    }
    return;
  }

  // ── /aria think: [question] — DEEP REASONING ─────────────────────────────
  if (argsLower.startsWith('think:')) {
    const question = args.slice(6).trim();
    if (question.length < 5) {
      await sendTelegramMessage(chatId, '⚠ Please provide a substantive question.');
      return;
    }
    await sendTelegramMessage(chatId, `🧠 _ARIA is thinking deeply..._\n_"${question.slice(0, 80)}"_\n_(This takes 30-90 seconds)_`);
    try {
      const thought = await ariaThinkProxy(question, {}, false);
      const c = thought.conclusion || {};
      const m = thought.metacognition || {};
      const a = thought.action || c.action || {};

      const msg = (
        `🧠 *ARIA Deep Analysis*\n\n` +
        `*Question:* _${question.slice(0, 150)}_\n\n` +
        `*Conclusion* [${c.epistemic_status || 'ASSESSED'}]\n` +
        `${String(c.statement || c).slice(0, 600)}\n\n` +
        `*Confidence:* ${thought.confidence || c.confidence || '?'}/100\n` +
        `*Key assumption:* _${c.key_assumption || 'Not stated'}_\n\n` +
        `*Action:* ${String(a.what || a || 'None specified').slice(0, 300)}\n\n` +
        `*Self-grade:* ${m.self_grade || '?'} — _${String(m.self_grade_rationale || m.biggest_gap || '').slice(0, 200)}_\n\n` +
        `*Compliance flags:* ${(c.compliance_flags || []).join(', ') || '✅ None'}`
      );
      await sendTelegramMessage(chatId, msg);
    } catch (e) {
      await sendTelegramMessage(chatId, `⚠ ARIA think failed: ${e.message}`);
    }
    return;
  }

  // ── /aria ask: [question] or /aria [question] — QUICK CHAT ───────────────
  const question = args.startsWith('ask:') ? args.slice(4).trim() : args.trim();
  await sendTelegramMessage(chatId, `🧠 _ARIA is thinking..._`);
  try {
    const data = await ariaChatProxy(question, `telegram_${chatId}`);
    await sendLongMessage(chatId, `🧠 *ARIA*\n\n${data.response}`);
  } catch (e) {
    await sendTelegramMessage(chatId, `⚠ ARIA unavailable: ${e.message}`);
  }
}

// ── Per-User Telegram Rate Limiting ──────────────────────────────────────────
// Simple in-memory rate limiter for Telegram (no Redis needed here)

const telegramRateLimits = new Map();

export function isTelegramRateLimited(userId, command) {
  const key      = `${userId}:${command}`;
  const now      = Date.now();
  const windowMs = command === 'think' ? 60000 : 10000;  // 1/min for think, 1/10s for others
  const last     = telegramRateLimits.get(key) || 0;

  if (now - last < windowMs) return true;
  telegramRateLimits.set(key, now);
  // Clean up old entries periodically
  if (telegramRateLimits.size > 1000) {
    for (const [k, v] of telegramRateLimits) {
      if (now - v > 120000) telegramRateLimits.delete(k);
    }
  }
  return false;
}

// ── Main Telegram Webhook Handler ─────────────────────────────────────────────
// Wire this into your existing webhook in server.mjs:
//
// import { handleTelegramWebhook } from './lib/telegram/telegramCommands.mjs';
// app.post('/api/telegram/webhook', express.json(), handleTelegramWebhook);

export async function handleTelegramWebhook(req, res) {
  res.sendStatus(200);  // always ACK Telegram immediately

  const update  = req.body;
  const message = update?.message || update?.callback_query?.message;
  if (!message) return;

  const chatId  = message.chat.id;
  const userId  = message.from?.id?.toString() || chatId.toString();
  const text    = message.text?.trim() || '';

  if (!text.startsWith('/')) return;

  const spaceIdx = text.indexOf(' ');
  const command  = spaceIdx === -1 ? text.toLowerCase() : text.slice(0, spaceIdx).toLowerCase();
  const args     = spaceIdx === -1 ? '' : text.slice(spaceIdx + 1).trim();

  try {
    switch (command) {
      case '/brief':
        if (isTelegramRateLimited(userId, 'brief')) {
          await sendTelegramMessage(chatId, '⚠ Brief rate limit: one per minute.');
          break;
        }
        await buildArkmursBrief(chatId);
        break;

      case '/aria':
        if (args.toLowerCase().startsWith('think') && isTelegramRateLimited(userId, 'think')) {
          await sendTelegramMessage(chatId, '⚠ /aria think is limited to once per minute per user.');
          break;
        }
        await handleAriaCommand(chatId, userId, args);
        break;

      // Your existing commands (/risk, /oem, /search, /bd, etc.) stay unchanged below:
      // case '/risk':   ... break;
      // case '/oem':    ... break;

      default:
        // Pass to your existing command router
        break;
    }
  } catch (e) {
    console.error('[Telegram] Command handler error:', e);
    await sendTelegramMessage(chatId, '⚠ An error occurred. Please try again.');
  }
}

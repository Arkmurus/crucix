/**
 * ARIA — WhatsApp Group Listener + Responder (Baileys)
 * ═══════════════════════════════════════════════════════════════════════════
 * Connects to WhatsApp as a linked device using your Portuguese SIM.
 * ARIA listens to group messages, feeds them to the brain, and REPLIES
 * when mentioned (@aria, "aria," or direct questions containing "aria").
 * She can also send proactive messages via the API.
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
import path from 'path';
import { redisGet, redisSet, redisConfigured } from '../persist/store.mjs';

const ENABLED       = process.env.WA_LISTENER_ENABLED === 'true';
const GROUP_IDS_RAW = process.env.WA_LISTENER_GROUP_IDS || '';
const AUTH_DIR      = process.env.WA_LISTENER_AUTH_DIR  || './wa-listener-auth';
const INT_TOKEN     = process.env.ARIA_INTERNAL_TOKEN   || 'aria-internal';
const AUTH_REDIS_KEY = 'crucix:wa_listener:auth_state';

const WA_REPLY_ENABLED = process.env.WA_REPLY_ENABLED !== 'false'; // default ON
const AUTO_RESPOND     = (process.env.WA_LISTENER_AUTO_RESPOND || 'true').toLowerCase() === 'true';
const TARGET_GROUPS = GROUP_IDS_RAW
  ? GROUP_IDS_RAW.split(',').map(g => g.trim()).filter(Boolean)
  : [];

// ── ARIA mention detection ──────────────────────────────────────────────────
const ARIA_MENTIONS = [/\baria\b/i, /@aria/i, /^aria[,:]/i];
function isAriaMentioned(text) {
  const t = (text || '').slice(0, 2000);
  return ARIA_MENTIONS.some(p => p.test(t));
}

// ── Command regex ──────────────────────────────────────────────────────────
const COMMAND_RE = /^\/(\w+)(.*)/s;

// ── Compliance trigger detection ────────────────────────────────────────────
const COMPLIANCE_KW = /export\s*licen[cs]e|sanctions?\b|embargo|itar\b|end.user|controlled\s*goods|ml\s*category|dual.use|ofac|ofsi|wassenaar/i;
const OPPORTUNITY_KW = /\btender\b|rfp\b|procurement|budget\s*allocat|contract\s*award|rfq\b|bid\s*submission/i;
const RISK_KW = /diversion\s*risk|sanctions?\s*risk|compliance\s*concern|red\s*flag|end.user\s*risk|proliferation/i;

const autoRespondDedup = new Map();
function shouldAutoRespond(keyword, chatId) {
  const key = `${keyword}:${chatId}`;
  const last = autoRespondDedup.get(key) || 0;
  if (Date.now() - last < 3600000) return false;
  autoRespondDedup.set(key, Date.now());
  return true;
}

function detectComplianceTrigger(text) {
  const t = (text || '').slice(0, 2000);
  const cMatch = t.match(COMPLIANCE_KW);
  if (cMatch) return { triggered: true, category: 'compliance', keyword: cMatch[0] };
  const oMatch = t.match(OPPORTUNITY_KW);
  if (oMatch) return { triggered: true, category: 'opportunity', keyword: oMatch[0] };
  const rMatch = t.match(RISK_KW);
  if (rMatch) return { triggered: true, category: 'risk', keyword: rMatch[0] };
  return { triggered: false };
}

// ── Command handler ─────────────────────────────────────────────────────────
async function handleCommand(cmd, args, senderName, senderJid) {
  const a = (args || '').trim().slice(0, 500);
  const port = process.env.PORT || 3117;
  const base = `http://localhost:${port}`;
  const headers = { 'Content-Type': 'application/json', 'Authorization': `Bearer ${INT_TOKEN}` };

  async function post(path, body) {
    const r = await fetch(`${base}${path}`, { method: 'POST', headers, body: JSON.stringify(body), signal: AbortSignal.timeout(90000) });
    return r.ok ? r.json() : {};
  }

  switch (cmd.toLowerCase()) {
    case 'screen': {
      if (!a) return 'Usage: /screen [entity name]';
      const d = await post('/api/aria/compliance/screen', { entity_name: a }).catch(() => ({}));
      const ok = d.result === 'PERMITTED';
      return `${ok ? 'CLEAR' : 'BLOCKED'} *COMPLIANCE SCREEN*\nEntity: ${a}\nResult: ${d.result || 'UNKNOWN'}\n${ok ? '_Pre-screen only. Legal review required._' : '*MATCH FOUND. Do not proceed without legal review.*'}`;
    }
    case 'classify': {
      if (!a) return 'Usage: /classify [product description]';
      const d = await post('/api/aria/compliance/classify', { description: a }).catch(() => ({}));
      let msg = `*ML CLASSIFICATION*\nProduct: ${a.slice(0, 80)}\n`;
      if (d.classifications?.length) {
        d.classifications.forEach(c => { msg += `- *${c.code || c.category}* ${c.description || ''}\n`; });
      } else { msg += `Result: ${d.result || d.category || 'No classification.'}\n`; }
      return msg + '_Classification advisory only._';
    }
    case 'sanctions': {
      if (!a) return 'Usage: /sanctions [name]';
      const d = await post('/api/aria/compliance/sanctions', { name: a }).catch(() => ({}));
      const hits = d.matches || d.results || [];
      if (hits.length) {
        return `*SANCTIONS CHECK — ${a}*\n${hits.length} match(es):\n${hits.slice(0, 5).map(h => `- *${h.name || h.entity}* (${h.list || 'Unknown list'})`).join('\n')}\n*Do not proceed without legal review.*`;
      }
      return `*SANCTIONS CHECK — ${a}*\nNo matches found.\n_Full due diligence required._`;
    }
    case 'risk': {
      if (!a) return 'Usage: /risk [country]';
      const d = await post('/api/aria/compliance/risk', { country: a }).catch(() => ({}));
      const level = d.risk_level || d.level || 'UNKNOWN';
      return `*COUNTRY RISK — ${a.toUpperCase()}*\nRisk: ${level}\n${d.notes || ''}`;
    }
    case 'teach': {
      const sep = a.indexOf(':');
      if (sep < 1) return 'Usage: /teach [topic]: [fact]';
      const topic = a.slice(0, sep).trim();
      const fact = a.slice(sep + 1).trim();
      await post('/api/aria/knowledge/fact', { topic, content: fact, confidence: 'CONFIRMED', source: senderName }).catch(() => {});
      return `Learned: *${topic}*\n"${fact.slice(0, 150)}"\nStored as CONFIRMED. Thanks, ${senderName}!`;
    }
    case 'correct': {
      const sep = a.match(/\s*(?:->|→)\s*/);
      if (!sep) return 'Usage: /correct [wrong] -> [right answer]';
      const wrong = a.slice(0, sep.index).trim();
      const right = a.slice(sep.index + sep[0].length).trim();
      await post('/api/aria/correct', { originalResponse: wrong, correction: right, correctAnswer: right }).catch(() => {});
      return `Correction recorded.\nWrong: "${wrong.slice(0, 100)}"\nRight: "${right.slice(0, 100)}"\nThank you — this makes me smarter.`;
    }
    case 'feedback': {
      const positive = a.startsWith('+');
      const notes = a.replace(/^[+-]\s*/, '').trim();
      await post('/api/brain/signal', { content: notes, source: `feedback:${senderName}`, signal_type: 'user_feedback', metadata: { sentiment: positive ? 'positive' : 'negative', sender: senderName } }).catch(() => {});
      return positive ? 'Thanks for the positive feedback!' : 'Noted — I\'ll improve. Thanks for letting me know.';
    }
    case 'leads': {
      const d = await fetch(`${base}/api/brain/brief`, { headers, signal: AbortSignal.timeout(10000) }).then(r => r.ok ? r.json() : {}).catch(() => ({}));
      const leads = d.leads || d.brief?.leads || [];
      if (!leads.length) return 'No leads generated yet. Run /hunt to trigger a lead search.';
      return `*LATEST LEADS*\n${leads.slice(0, 5).map((l, i) => `${i + 1}. *${l.title || l.market || 'Lead'}* — ${(l.summary || l.content || '').slice(0, 150)}`).join('\n')}`;
    }
    case 'ideas': {
      await post('/api/aria/proactive/strategic-ideas', {}).catch(() => {});
      return 'Generating strategic ideas — check Telegram in a moment.';
    }
    case 'hunt': {
      await post('/api/aria/proactive/lead-hunt', {}).catch(() => {});
      return 'Lead hunt triggered — ARIA is searching. Results will appear on Telegram.';
    }
    case 'ask': {
      if (!a) return 'Usage: /ask [question]';
      return _unwrapAriaReply(await askARIA(a, '', senderName));
    }
    case 'groupsummary': {
      const recent = messageStore.slice(-50).map(m => `[${m.senderName}]: ${m.text.slice(0, 200)}`).join('\n');
      if (!recent) return 'No messages stored yet.';
      return _unwrapAriaReply(await askARIA(
        `Summarise this group conversation. Highlight key topics, decisions, action items, and any compliance or risk mentions:\n\n${recent}`,
        '', senderName,
      ));
    }
    case 'investigate': {
      if (!a) return 'Usage: /investigate [person or company name]';
      const investigatePrompt = `INVESTIGATION REQUEST: Conduct a thorough investigation on "${a}". Follow the INVESTIGATION METHODOLOGY protocol. Determine if this is a person or company, then apply the appropriate protocol (PERSON INVESTIGATION or COMPANY INVESTIGATION). Cross-reference all findings. Flag any red flags, sanctions proximity, PEP status, or compliance concerns. Provide a structured report with confidence levels and source quality assessment.`;
      return _unwrapAriaReply(await askARIA(investigatePrompt, '', senderName));
    }
    case 'network': {
      if (!a) return 'Usage: /network [name1, name2, name3]';
      const names = a.split(',').map(n => n.trim()).filter(Boolean);
      if (names.length < 2) return 'Please provide at least 2 entities separated by commas.\nUsage: /network [name1, name2, name3]';
      const networkPrompt = `NETWORK ANALYSIS REQUEST: Map the relationships between these entities: ${names.join(', ')}. Follow the NETWORK ANALYSIS protocol. Build a relationship graph, identify gatekeepers, find hidden connections (shared directorships, same addresses, overlapping beneficial owners, family ties, military academy cohorts). Assess influence flows and flag any risk nodes (sanctioned entities, PEP connections, conflict of interest patterns). Provide a structured network map with confidence levels.`;
      return _unwrapAriaReply(await askARIA(networkPrompt, '', senderName));
    }
    case 'rag': {
      if (!a) return 'Usage: /rag [search query] — searches ARIA\'s persistent knowledge index across every document, article, image, and crawl she has ever read.';
      const ariaServiceUrl = process.env.ARIA_SERVICE_URL;
      if (!ariaServiceUrl) return '⚠️ ARIA_SERVICE_URL not configured.';
      try {
        const resp = await fetch(`${ariaServiceUrl}/api/aria/rag/search`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query: a, top_k: 8 }),
          signal: AbortSignal.timeout(30000),
        });
        if (!resp.ok) return `⚠️ RAG search failed: ${resp.status}`;
        const d = await resp.json();
        const results = d.results || [];
        if (!results.length) {
          return `🔍 *RAG search:* "${a}"\n\nNothing found. ARIA hasn't indexed anything matching this yet — share a document, image, or URL and try again.`;
        }
        let msg = `🔍 *RAG search:* "${a}"\n*${results.length} result(s)*\n\n`;
        results.slice(0, 6).forEach((r, i) => {
          const score = (r.score * 100).toFixed(0);
          const cite = r.title || r.source || 'unknown';
          const date = (r.ingested_at || '').slice(0, 10);
          msg += `*${i + 1}. [${score}%]* _${cite}${date ? ' · ' + date : ''}_\n`;
          msg += `${(r.text || '').slice(0, 320)}\n\n`;
        });
        return msg;
      } catch (e) {
        return `⚠️ RAG search error: ${e.message}`;
      }
    }
    case 'feedback': {
      // Two modes:
      //   /feedback           → summary stats (counts + quality score)
      //   /feedback bad       → list recent negative feedback with original Q&A
      //   /feedback good      → list recent positive feedback
      //   /feedback all       → list everything regardless of sentiment
      const ariaServiceUrl = process.env.ARIA_SERVICE_URL;
      if (!ariaServiceUrl) return '⚠️ ARIA_SERVICE_URL not configured.';
      const mode = (a || '').trim().toLowerCase();
      try {
        // Specific feedback ID lookup
        if (mode.startsWith('fb_')) {
          const r = await fetch(`${ariaServiceUrl}/api/aria/feedback/${encodeURIComponent(mode)}`, {
            signal: AbortSignal.timeout(10000),
          });
          if (r.status === 404) return `⚠️ Feedback \`${mode}\` not found (may have expired — 90 day TTL).`;
          if (!r.ok) return `⚠️ Feedback lookup failed: ${r.status}`;
          const rec = await r.json();
          const ago = rec.ts ? `${Math.round((Date.now() / 1000 - rec.ts) / 60)}m ago` : '';
          let msg = `${rec.emoji || '•'} *Feedback ${rec.id}*\n`;
          msg += `*Sentiment:* ${rec.sentiment}\n`;
          msg += `*Reactor:* ${rec.reactor || '?'} · ${ago}\n\n`;
          if (rec.has_context) {
            msg += `*Original question:*\n${(rec.question || '').slice(0, 1000)}\n\n`;
            msg += `*ARIA answered:*\n${(rec.answer || '').slice(0, 1500)}`;
          } else {
            msg += `_(snapshot expired — only sentiment recorded)_`;
          }
          return msg;
        }
        if (!mode || mode === 'stats' || mode === 'summary') {
          const r = await fetch(`${ariaServiceUrl}/api/aria/feedback/stats`, {
            signal: AbortSignal.timeout(10000),
          });
          if (!r.ok) return `⚠️ Feedback stats failed: ${r.status}`;
          const s = await r.json();
          if (s.error) return `⚠️ Feedback stats error: ${s.error}`;
          const bs = s.by_sentiment || {};
          const score = s.quality_score === null || s.quality_score === undefined
            ? '—' : `${(s.quality_score * 100).toFixed(0)}%`;
          let msg = `📊 *ARIA Feedback*\n\n`;
          msg += `*Quality score:* ${score}\n`;
          msg += `*Total reactions:* ${s.total || 0}\n`;
          msg += `*Last 24h:* ${s.recent_24h || 0}\n\n`;
          msg += `👍 positive: ${bs.positive || 0}\n`;
          msg += `👎 negative: ${bs.negative || 0}\n`;
          msg += `🤔 uncertain: ${bs.uncertain || 0}\n`;
          msg += `• neutral: ${bs.neutral || 0}\n\n`;
          msg += `_React to ARIA's replies with 👍/👎 to teach her._\n`;
          msg += `_Use */feedback bad* to see recent negatives._`;
          return msg;
        }
        const sentimentMap = { bad: 'negative', good: 'positive', neg: 'negative', pos: 'positive' };
        const sentiment = sentimentMap[mode] || (mode === 'all' ? null : mode);
        const url = `${ariaServiceUrl}/api/aria/feedback/list?limit=10${sentiment ? '&sentiment=' + encodeURIComponent(sentiment) : ''}`;
        const r = await fetch(url, { signal: AbortSignal.timeout(10000) });
        if (!r.ok) return `⚠️ Feedback list failed: ${r.status}`;
        const d = await r.json();
        const items = d.feedback || [];
        if (!items.length) return `📋 No ${sentiment || ''} feedback yet.`;
        let msg = `📋 *Recent ${sentiment || 'feedback'}* (${items.length})\n\n`;
        items.forEach((it, i) => {
          const ago = it.ts ? `${Math.round((Date.now() / 1000 - it.ts) / 60)}m ago` : '';
          const ctx = it.has_context ? '' : ' _(no context)_';
          msg += `${i + 1}. ${it.emoji || '•'} *${it.sentiment}* · _${it.reactor || '?'}_ · ${ago}${ctx}\n`;
          if (it.question_preview) msg += `   Q: ${it.question_preview}\n`;
          msg += `   _id: ${it.id}_\n\n`;
        });
        msg += `_Use */feedback <id>* for the full Q&A snapshot._`;
        return msg;
      } catch (e) {
        return `⚠️ Feedback error: ${e.message}`;
      }
    }

    case 'tasks': {
      const ariaServiceUrl = process.env.ARIA_SERVICE_URL;
      if (!ariaServiceUrl) return '⚠️ ARIA_SERVICE_URL not configured.';
      try {
        const status = a.trim().toLowerCase() || null;
        const url = `${ariaServiceUrl}/api/aria/research/list?limit=20${status ? '&status=' + encodeURIComponent(status) : ''}`;
        const resp = await fetch(url, { signal: AbortSignal.timeout(15000) });
        if (!resp.ok) return `⚠️ Tasks list failed: ${resp.status}`;
        const d = await resp.json();
        const tasks = d.tasks || [];
        if (!tasks.length) return `📋 No research tasks${status ? ' with status ' + status : ''} yet.`;
        let msg = `📋 *Research tasks* (${tasks.length})\n\n`;
        tasks.slice(0, 12).forEach((t, i) => {
          const icon = {
            queued: '⏳', running: '🔄', complete: '✅',
            failed: '❌', rejected: '🚫',
          }[t.status] || '•';
          const ago = t.updated_at ? `${Math.round((Date.now() / 1000 - t.updated_at) / 60)}m ago` : '';
          msg += `${icon} *${t.id}* — ${t.type}\n  ${(t.title || '').slice(0, 80)}\n  _${t.status} · ${ago}_\n\n`;
        });
        msg += `_Use */task <id>* for details._`;
        return msg;
      } catch (e) {
        return `⚠️ Tasks error: ${e.message}`;
      }
    }

    case 'task': {
      if (!a) return 'Usage: /task <task_id>';
      const ariaServiceUrl = process.env.ARIA_SERVICE_URL;
      if (!ariaServiceUrl) return '⚠️ ARIA_SERVICE_URL not configured.';
      const id = a.trim().split(/\s+/)[0];
      try {
        const resp = await fetch(`${ariaServiceUrl}/api/aria/research/task/${encodeURIComponent(id)}`, {
          signal: AbortSignal.timeout(15000),
        });
        if (resp.status === 404) return `⚠️ Task \`${id}\` not found.`;
        if (!resp.ok) return `⚠️ Task lookup failed: ${resp.status}`;
        const t = await resp.json();
        const icon = {
          queued: '⏳', running: '🔄', complete: '✅',
          failed: '❌', rejected: '🚫',
        }[t.status] || '•';
        let msg = `${icon} *Task ${t.id}*\n`;
        msg += `*Type:* ${t.type}\n`;
        msg += `*Title:* ${t.title || '(no title)'}\n`;
        msg += `*Status:* ${t.status}\n`;
        msg += `*Progress:* ${t.progress || '-'}\n`;
        if (t.eta_seconds) msg += `*ETA:* ${Math.round(t.eta_seconds / 60)}m\n`;
        if (t.duration_ms) msg += `*Duration:* ${Math.round(t.duration_ms / 1000)}s\n`;
        if (t.error) msg += `\n⚠️ *Error:* ${t.error.slice(0, 300)}\n`;
        if (t.status === 'complete' && t.result) {
          msg += `\n*Result preview:*\n`;
          if (t.result.synthesis) {
            msg += `${(t.result.synthesis || '').slice(0, 1500)}`;
          } else if (t.result.facts_learned !== undefined) {
            msg += `Facts learned: ${t.result.facts_learned}\n`;
            msg += `Pages: ${t.result.pages_crawled || t.result.articles_read || 0}\n`;
            const synth = t.result.synthesis;
            if (synth && typeof synth === 'object' && synth.strategic_implications) {
              msg += `\n*Implications*: ${synth.strategic_implications.slice(0, 800)}`;
            }
          } else {
            msg += JSON.stringify(t.result, null, 2).slice(0, 1500);
          }
        }
        return msg;
      } catch (e) {
        return `⚠️ Task lookup error: ${e.message}`;
      }
    }

    case 'sources': {
      const ariaServiceUrl = process.env.ARIA_SERVICE_URL;
      if (!ariaServiceUrl) return '⚠️ ARIA_SERVICE_URL not configured.';
      try {
        const resp = await fetch(`${ariaServiceUrl}/api/aria/rag/sources?limit=30`, {
          signal: AbortSignal.timeout(15000),
        });
        if (!resp.ok) return `⚠️ Sources check failed: ${resp.status}`;
        const d = await resp.json();
        if (!d.available) return `⚠️ RAG store unavailable: ${d.error || 'init failed'}`;
        let msg = `📚 *ARIA's RAG store*\n\n`;
        msg += `Total unique sources: ${d.total_unique_sources}\n`;
        if (d.by_type) {
          msg += `\n*By type:*\n`;
          Object.entries(d.by_type).forEach(([t, n]) => {
            msg += `  • ${t}: ${n}\n`;
          });
        }
        msg += `\n*Recent sources:*\n`;
        (d.sources || []).slice(0, 10).forEach((s, i) => {
          const title = (s.title || s.source || 'unknown').slice(0, 60);
          msg += `${i + 1}. ${title} (${s.chunks} chunks)\n`;
        });
        return msg;
      } catch (e) {
        return `⚠️ Sources error: ${e.message}`;
      }
    }
    case 'help':
      return [
        '*ARIA — WhatsApp Commands*',
        '',
        '*Intelligence*',
        '/ask [question] — Ask ARIA anything',
        '/brief — Today\'s digest',
        '/groupsummary — Summarise recent group chat',
        '',
        '*Investigation*',
        '/investigate [name] — Deep investigation (person or company)',
        '/network [name1, name2, ...] — Map relationships between entities',
        '',
        '*Compliance*',
        '/screen [entity] — Pre-screening',
        '/classify [product] — ML classification',
        '/sanctions [name] — Sanctions check',
        '/risk [country] — Country risk',
        '',
        '*Learning*',
        '/teach [topic]: [fact] — Teach ARIA',
        '/correct [wrong] -> [right] — Fix a mistake',
        '/feedback +/- [notes] — Rate response',
        '',
        '*Business Development*',
        '/leads — Latest leads',
        '/ideas — Strategic ideas',
        '/hunt — Trigger lead search',
        '',
        '_Or mention ARIA in any message._',
      ].join('\n');
    default:
      return null;
  }
}

// ── Rate limiting for replies (prevent spam) ────────────────────────────────
const replyLimits = new Map();
const REPLY_LIMIT  = 5;     // max replies per group per window
const REPLY_WINDOW = 60000; // 1 minute

function isReplyLimited(groupId) {
  const now = Date.now();
  const entry = replyLimits.get(groupId);
  if (!entry || now - entry.start > REPLY_WINDOW) {
    replyLimits.set(groupId, { start: now, count: 1 });
    return false;
  }
  entry.count++;
  return entry.count > REPLY_LIMIT;
}

// ── State ────────────────────────────────────────────────────────────────────
let sock           = null;
let isConnected    = false;
let qrPrinted      = false;
let suppressAuthBackup = false;
let authInvalidatedAt  = 0;  // timestamp of last 440/logout — skip restore if recent
let messagesHeard  = 0;
let messagesSent   = 0;
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

// ── Smart message splitter — never breaks mid-word or mid-sentence ──────────
// WhatsApp's hard limit is ~65000 chars but messages over ~4000 stop being
// readable. We split on paragraph → sentence → word boundaries in that order
// so a long ARIA analysis arrives as a sequence of clean chunks instead of
// being chopped mid-syllable.
function splitMessageSmart(text, maxLen = 3800) {
  if (!text) return [];
  if (text.length <= maxLen) return [text];

  const chunks = [];
  let remaining = text;

  while (remaining.length > 0) {
    if (remaining.length <= maxLen) {
      chunks.push(remaining);
      break;
    }

    // Find the best split point — try paragraph break first, then sentence,
    // then word, only fall back to hard split as a last resort.
    let cut = -1;

    // 1. Paragraph break (\n\n) within the last 30% of the chunk
    const minSplit = Math.floor(maxLen * 0.7);
    cut = remaining.lastIndexOf('\n\n', maxLen);
    if (cut < minSplit) cut = -1;

    // 2. Single newline
    if (cut < 0) {
      cut = remaining.lastIndexOf('\n', maxLen);
      if (cut < minSplit) cut = -1;
    }

    // 3. Sentence boundary (. ! ?) followed by a space
    if (cut < 0) {
      for (const punct of ['. ', '! ', '? ']) {
        const idx = remaining.lastIndexOf(punct, maxLen);
        if (idx >= minSplit && idx > cut) cut = idx + 1; // include the punct
      }
    }

    // 4. Word boundary (space)
    if (cut < 0) {
      cut = remaining.lastIndexOf(' ', maxLen);
      if (cut < minSplit) cut = -1;
    }

    // 5. Hard split as last resort
    if (cut < 0) cut = maxLen;

    chunks.push(remaining.slice(0, cut).trim());
    remaining = remaining.slice(cut).replace(/^\s+/, '');
  }

  return chunks;
}

// ── Send WhatsApp message via Baileys socket ────────────────────────────────
// Returns the key of the FIRST chunk on success (truthy), or null on failure.
// The first chunk's key is what users react to with emojis, and what we hand
// to the feedback snapshot store. Existing call sites that did `if (ok)` /
// `if (!ok)` still work because a key object is truthy and null is falsy.
async function sendMessage(chatId, text, quotedMsg = null) {
  if (!sock || !isConnected) {
    console.warn('[WA Listener] Cannot send — not connected');
    return null;
  }
  if (!text || !chatId) return null;

  const chunks = splitMessageSmart(text, 3800);
  let firstKey = null;

  try {
    for (let i = 0; i < chunks.length; i++) {
      if (!chunks[i]) continue;
      const opts = {};
      // Only quote the original message on the first chunk
      if (i === 0 && quotedMsg) opts.quoted = quotedMsg;
      // Add a continuation marker for multi-chunk messages so the user
      // sees this is part of a sequence, not a stop-and-start
      const body = (chunks.length > 1 && i < chunks.length - 1)
        ? `${chunks[i]}\n\n_…continued (${i + 1}/${chunks.length})_`
        : (chunks.length > 1
           ? `${chunks[i]}\n\n_…end (${i + 1}/${chunks.length})_`
           : chunks[i]);
      const sent = await sock.sendMessage(chatId, { text: body }, opts);
      if (i === 0 && sent && sent.key) firstKey = sent.key;
      // Small delay between chunks to avoid flood
      if (chunks.length > 1 && i < chunks.length - 1) {
        await new Promise(r => setTimeout(r, 500));
      }
    }
    messagesSent++;
    console.log(`[WA Listener] Sent message to ${groupNames.get(chatId) || chatId} (${text.length} chars in ${chunks.length} chunk${chunks.length > 1 ? 's' : ''})`);
    return firstKey;
  } catch (e) {
    console.error('[WA Listener] Send failed:', e.message);
    return null;
  }
}

// ── Snapshot an ARIA reply for later feedback correlation ──────────────────
// Called after sendMessage() for substantive ARIA replies (mention answers,
// OCR results, image analysis). Stores the Q→A pair under the message key
// so when a user reacts with an emoji, the reaction handler can recover the
// original question. Fire-and-forget — never blocks the chat path.
async function snapshotAriaReply(key, question, answer, user, groupName, metadata = {}) {
  if (!key || !key.id || !key.remoteJid) return;
  const ariaServiceUrl = process.env.ARIA_SERVICE_URL;
  if (!ariaServiceUrl) return;
  try {
    await fetch(`${ariaServiceUrl}/api/aria/feedback/snapshot`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        chat_id: key.remoteJid,
        msg_id: key.id,
        question: (question || '').slice(0, 4000),
        answer: (answer || '').slice(0, 6000),
        user: user || '',
        group_name: groupName || '',
        metadata,
      }),
      signal: AbortSignal.timeout(5000),
    }).catch(() => {});
  } catch {/* fire-and-forget */}
}

// ── Helper: unwrap askARIA result + trigger self-diagnostic on failure ─────
// askARIA() returns either a string OR { error: true, message } so we can
// handle failures intelligently instead of silently swallowing them.
//
// BUG-FIX: previously when callers passed `null` as the fallback (meaning
// "I'll handle the null case myself") and the result was an error object,
// the function would interpolate the string "null" into the output:
//   `${null}\n\n_Diagnostic: ...` → "null\n\n_Diagnostic: ..."
// Then the user would see "*ARIA* — null" in WhatsApp. Now we use a real
// human-readable string for the error body and only return null when both
// the result is null/empty AND the caller explicitly requested null fallback.
function _unwrapAriaReply(result, fallbackMsg = 'ARIA is temporarily unavailable. Please try again shortly.') {
  if (typeof result === 'string' && result) return result;
  if (result && result.error) {
    // Fire-and-forget self-diagnostic so ARIA learns from her own failures
    _triggerSelfDiagnostic('chat_failure', result.message).catch(() => {});
    // Use a real string for the error body — never let "null" leak through
    const head = (typeof fallbackMsg === 'string' && fallbackMsg)
      ? fallbackMsg
      : '⚠️ I hit an error processing that — running self-diagnostic now.';
    const detail = (result.message || 'unknown error').slice(0, 300);
    // Friendlier hint for the most common failure (timeout)
    const hint = /timeout|aborted/i.test(detail)
      ? '\n\n_That request involves deep research / multi-step reasoning which can take 2-4 minutes. I am still working on it in the background — you can ask me again in 60s and I should have it ready in my reasoning library by then._'
      : '';
    return `${head}\n\n_Diagnostic: ${detail}_${hint}`;
  }
  return fallbackMsg;
}

// ── Self-diagnostic trigger — fires whenever ARIA fails at a task ───────────
// Posts the failure to /api/aria/self/diagnose so the Python side can:
//   1. Classify the failure (LLM error / missing tool / data gap / network)
//   2. Search the reasoning library for prior similar failures
//   3. Auto-stage a code fix if it's a known pattern
//   4. Alert the team if it's novel
// Cheap, async, fire-and-forget. Never blocks the user reply.
const _diagnosticDedupe = new Map();
const _DIAG_DEDUPE_WINDOW = 5 * 60 * 1000;  // 5 min — don't spam on repeated failures

async function _triggerSelfDiagnostic(failureType, errorMessage, context = {}) {
  if (!errorMessage) return;
  // Dedupe — don't fire 10 diagnostics for the same error in a row
  const key = `${failureType}:${errorMessage.slice(0, 100)}`;
  const last = _diagnosticDedupe.get(key);
  if (last && Date.now() - last < _DIAG_DEDUPE_WINDOW) return;
  _diagnosticDedupe.set(key, Date.now());

  const ariaUrl = process.env.ARIA_SERVICE_URL;
  if (!ariaUrl) return;

  try {
    await fetch(`${ariaUrl}/api/aria/self/diagnose`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        failure_type: failureType,
        error_message: errorMessage.slice(0, 1000),
        context: { ...context, source: 'wa_listener', ts: Date.now() },
      }),
      signal: AbortSignal.timeout(15000),
    });
    console.log(`[WA Listener] Self-diagnostic fired: ${failureType} — ${errorMessage.slice(0, 80)}`);
  } catch (e) {
    // Even the diagnostic call can fail — log but don't recurse
    console.debug('[WA Listener] Self-diagnostic call failed:', e.message);
  }
}

// ── Proactive alert poller — pulls alerts ARIA pushes during background work
// Every 90 seconds the listener polls /api/aria/proactive/alerts. Any unseen
// alerts get pushed to a configured "alerts group" so the team sees them
// in real time without ARIA having to be asked. This is the SUPER ACTIVE
// behaviour: ARIA proactively surfaces things she notices.
//
// Configure WA_ALERT_GROUP_ID = the WhatsApp group JID to push to. If unset,
// alerts are still drained from the Python queue but logged-only on this side.
let _alertPollerStarted = false;
let _alertPollerInterval = null;

function _startProactiveAlertPoller() {
  if (_alertPollerStarted) return;
  _alertPollerStarted = true;

  const ariaUrl = process.env.ARIA_SERVICE_URL;
  const alertGroup = process.env.WA_ALERT_GROUP_ID || '';
  const intervalMs = parseInt(process.env.WA_ALERT_POLL_MS || '90000', 10);

  if (!ariaUrl) {
    console.warn('[WA Listener] Proactive poller: ARIA_SERVICE_URL not set, skipping');
    return;
  }

  console.log(`[WA Listener] Proactive alert poller started — every ${Math.round(intervalMs / 1000)}s, target group: ${alertGroup || 'NONE (log only)'}`);

  _alertPollerInterval = setInterval(async () => {
    if (!isConnected) return;
    try {
      const r = await fetch(`${ariaUrl}/api/aria/proactive/alerts?mark_seen=true`, {
        signal: AbortSignal.timeout(15000),
      });
      if (!r.ok) return;
      const data = await r.json();
      const alerts = data.alerts || [];
      if (!alerts.length) return;

      console.log(`[WA Listener] Proactive: ${alerts.length} new alert(s) to push`);
      for (const alert of alerts) {
        const sevIcon = {
          critical: '🔴', high: '🟠', medium: '🟡', low: '🟢', info: 'ℹ️',
        }[alert.severity] || 'ℹ️';
        const body = [
          `${sevIcon} *ARIA Proactive Alert*`,
          `*${alert.title || 'Untitled'}*`,
          ``,
          alert.body || '',
        ].join('\n');

        if (alertGroup) {
          await sendMessage(alertGroup, body).catch(e => {
            console.warn('[WA Listener] Alert push failed:', e.message);
          });
        } else {
          console.log(`[WA Listener] (no alert group set) ${alert.title}`);
        }
        // Pace alerts so we don't spam the group on a big batch
        await new Promise(r => setTimeout(r, 1500));
      }
    } catch (e) {
      console.debug('[WA Listener] Alert poll cycle failed:', e.message);
    }
  }, intervalMs);
}

// ── Detect if a message will likely trigger long research operations ───────
// Used to decide whether to bump the timeout and send a "working on it"
// progress message before the chat call. Research / crawl / investigate
// operations routinely take 60-240 seconds, way beyond a normal chat.
const _LONG_RESEARCH_RE = /\b(research|investigate|crawl|deep[\s\-]?dive|look\s+into|dig\s+into|find\s+out\s+about|profile|due\s+diligence|background\s+check|each\s+(?:one|of|supplier|company|entity|person)|compare\s+(?:these|each|all)|analy[sz]e\s+(?:each|these|all))\b/i;

function _isLongResearchPrompt(text) {
  return _LONG_RESEARCH_RE.test(text || '');
}

// ── Ask ARIA and get a response ─────────────────────────────────────────────
// BUG-FIX: previously this slice'd the response to 3000 chars (truncation
// bug) AND the timeout was 120s which is too short for research operations.
// Now: 16000 cap + dynamic timeout (4 min for research, 90s for routine).
async function askARIA(message, groupContext = '', sender = 'whatsapp') {
  const port = process.env.PORT || 3117;
  const sid  = `wa_group_${sender.replace(/[^a-zA-Z0-9]/g, '').slice(-10)}`;
  const fullMessage = groupContext
    ? `[WhatsApp group context]\n${groupContext}\n\n[Question from ${sender}]\n${message}`
    : message;

  // Dynamic timeout — research operations need much longer
  const isLong = _isLongResearchPrompt(message);
  const timeoutMs = isLong ? 240000 : 90000;  // 4min for research, 90s otherwise

  try {
    const r = await fetch(`http://localhost:${port}/api/aria/chat`, {
      method:  'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${INT_TOKEN}`,
      },
      body:   JSON.stringify({ message: fullMessage, session_id: sid }),
      signal: AbortSignal.timeout(timeoutMs),
    });
    if (!r.ok) {
      const errBody = await r.text().catch(() => '');
      throw new Error(`ARIA ${r.status}${errBody ? ': ' + errBody.slice(0, 200) : ''}`);
    }
    const data = await r.json();
    const text = data.response || data.answer || '';
    if (!text) return null;
    // Sanity cap: 16000 chars = roughly 4 WhatsApp chunks. Anything over
    // is probably runaway prompt-injection or a hung loop.
    return text.slice(0, 16000);
  } catch (e) {
    console.error('[WA Listener] ARIA chat failed:', e.message);
    // Surface the error so the upstream caller can decide whether to retry,
    // self-fix, or notify. Returning null was hiding the failure mode.
    return { error: true, message: e.message };
  }
}

// ── Handle ARIA mention — generate and send reply ───────────────────────────
async function handleAriaMention(msg, chatId, groupName, senderName, text) {
  if (!WA_REPLY_ENABLED) return;
  if (isReplyLimited(chatId)) {
    console.log(`[WA Listener] Reply rate-limited for ${groupName}`);
    return;
  }

  // Get recent group context (last 5 messages)
  const recentMsgs = messageStore
    .filter(m => m.groupId === chatId)
    .slice(-5)
    .map(m => `[${m.senderName}]: ${m.text.slice(0, 200)}`)
    .join('\n');

  console.log(`[WA Listener] ARIA mentioned by ${senderName} in ${groupName} — generating reply...`);

  // For long-running research prompts, send a "working on it" progress message
  // BEFORE the actual call so the user knows ARIA is engaged + understands
  // the wait. The 4-minute timeout in askARIA accommodates these operations.
  const isLong = _isLongResearchPrompt(text);
  if (isLong) {
    await sendMessage(chatId, `*ARIA* — 🔎 _On it. This involves research across multiple sources — give me up to 4 minutes. I'll come back with a structured brief and citations._`, msg).catch(() => {});
  }

  const rawReply = await askARIA(text, recentMsgs, senderName);
  const reply = _unwrapAriaReply(rawReply, isLong
    ? '⏱️ The deep research is still running in the background — ask me again in 60s and I should have the answer cached.'
    : null);
  if (!reply) return;

  // Prefix with ARIA identity
  const response = `*ARIA* — ${reply}`;
  const sentKey = await sendMessage(chatId, response, msg);
  // Snapshot the Q→A so a later 👍/👎 reaction can be tied back to the
  // original question. Fire-and-forget — failure to snapshot must not
  // affect the chat experience.
  if (sentKey) {
    snapshotAriaReply(sentKey, text, reply, senderName, groupName, {
      source: 'mention',
      is_long_research: isLong,
    }).catch(() => {});
  }
}

// ── Public API: send message to any group/contact ───────────────────────────
export function getWASock() { return { sock, isConnected, sendMessage, groupNames }; }

// ── Document download helper ─────────────────────────────────────────────────
let _downloadContentFromMessage = null;

async function downloadBuffer(docMsg, docType) {
  if (!_downloadContentFromMessage) {
    const baileys = await import('@whiskeysockets/baileys');
    _downloadContentFromMessage = baileys.downloadContentFromMessage;
  }
  const stream = await _downloadContentFromMessage(docMsg, docType);
  const chunks = [];
  for await (const chunk of stream) chunks.push(chunk);
  return Buffer.concat(chunks);
}

// ── Media processing — download and extract text from shared files ────────────
async function processMedia(msg, sock, groupName, senderName) {
  try {
    const m = msg.message;
    if (!m) return null;

    // ── Documents (PDF, DOCX, TXT, CSV, etc.) ─────────────────────────────
    const docMsg = m.documentMessage || m.documentWithCaptionMessage?.message?.documentMessage;
    if (docMsg) {
      const mime     = (docMsg.mimetype || '').toLowerCase();
      const fileName = docMsg.fileName || 'unknown';
      const caption  = docMsg.caption || '';
      let extractedText = '';
      let fileType = mime;

      try {
        const buffer = await downloadBuffer(docMsg, 'document');
        console.log(`[WA] Downloaded: ${fileName} (${buffer.length} bytes)`);

        // ── PDF ──────────────────────────────────────────────────────────
        if (mime.includes('pdf') || fileName.toLowerCase().endsWith('.pdf')) {
          fileType = 'pdf';
          const pdfParse = await import('pdf-parse').then(m => m.default).catch(() => null);
          if (pdfParse) {
            const pdf = await pdfParse(buffer);
            extractedText = (pdf.text || '').trim().slice(0, 15000);
            console.log(`[WA] [${groupName}] ${senderName}: 📄 PDF "${fileName}" (${extractedText.length} chars, ${pdf.numpages} pages)`);
          }
        }

        // ── Word DOCX ────────────────────────────────────────────────────
        else if (mime.includes('wordprocessingml') || fileName.toLowerCase().endsWith('.docx')) {
          fileType = 'docx';
          const mammoth = await import('mammoth').then(m => m.default).catch(() => null);
          if (mammoth) {
            const result = await mammoth.extractRawText({ buffer });
            extractedText = (result.value || '').trim().slice(0, 15000);
            console.log(`[WA] [${groupName}] ${senderName}: 📝 DOCX "${fileName}" (${extractedText.length} chars)`);
          }
        }

        // ── Plain text files (txt, csv, md, json, xml) ───────────────────
        else if (mime.startsWith('text/') || fileName.toLowerCase().match(/\.(txt|csv|md|json|xml|log)$/)) {
          fileType = 'text';
          extractedText = buffer.toString('utf-8').trim().slice(0, 15000);
          console.log(`[WA] [${groupName}] ${senderName}: 📃 Text "${fileName}" (${extractedText.length} chars)`);
        }

      } catch(e) {
        console.warn(`[WA] Document extraction failed for "${fileName}":`, e.message);
      }

      // Store and feed whatever we got
      const content = extractedText.length > 50
        ? `[Document: ${fileName}] ${caption ? caption + '\n\n' : ''}${extractedText}`
        : `[Document shared: ${fileName}] ${caption || ''}`.trim();

      if (content) {
        console.log(`[WA] [${groupName}] ${senderName}: 📎 ${fileName} → ${extractedText.length > 50 ? 'content extracted' : 'metadata only'}`);
        store(msg.key.remoteJid, groupName, msg.key.participant, senderName, content, new Date().toISOString());
        await feedToARIA(groupName, senderName, content, 'whatsapp_document', { file_name: fileName, file_type: fileType });

        // Send extracted document text to ARIA research engine for deep learning
        const ariaUrl = process.env.ARIA_SERVICE_URL;
        if (ariaUrl && extractedText.length > 100) {
          fetch(`${ariaUrl}/api/aria/read-document`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              content: extractedText,
              filename: fileName,
              source: `whatsapp:${groupName}:${senderName}`,
              context: caption || `Shared by ${senderName} in ${groupName}`,
            }),
            signal: AbortSignal.timeout(180000),
          }).then(r => r.ok ? r.json() : null).then(result => {
            if (result?.facts_learned > 0) {
              console.log(`[WA→ARIA] Document analysed: ${fileName} → ${result.facts_learned} facts, ${result.hypotheses_generated || 0} hypotheses`);
            }
          }).catch(() => {});
        }

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

    // ── Images — full OCR pipeline (handles both captioned and bare images) ─
    // Downloads via Baileys, base64-encodes, sends to /api/aria/ocr which runs
    // the local-first chain (EasyOCR → Tesseract → Ollama vision → cloud).
    // Replies to the GROUP with a preview of what ARIA read so the team can
    // see her extraction live.
    if (m.imageMessage) {
      const imgMsg = m.imageMessage;
      const caption = imgMsg.caption || '';
      const chatId = msg.key.remoteJid;
      const ariaUrl = process.env.ARIA_SERVICE_URL;

      console.log(`[WA] [${groupName}] ${senderName}: 🖼 Image shared${caption ? ` "${caption.slice(0,60)}"` : ' (no caption)'} — running OCR…`);

      if (!ariaUrl) {
        console.warn('[WA] ARIA_SERVICE_URL not set — image OCR skipped');
        await sendMessage(chatId, `*ARIA* — 📥 Got the image but ARIA_SERVICE_URL is not configured on this server. I can't process media until that env var points to the Python ARIA service.`, msg).catch(() => {});
        return null;
      }

      // Immediate ack so the group knows ARIA is working
      await sendMessage(chatId, `*ARIA* — 📥 Got your image. Reading now…`, msg).catch(() => {});

      try {
        const buffer = await downloadBuffer(imgMsg, 'image');
        if (!buffer || buffer.length === 0) {
          await sendMessage(chatId, `*ARIA* — ⚠️ The image appears to be empty.`, msg).catch(() => {});
          return null;
        }

        // Cap at 8MB. Slice BYTES before base64 to avoid mid-character corruption.
        const MAX_BYTES = 8 * 1024 * 1024;
        const buf = buffer.length > MAX_BYTES ? buffer.subarray(0, MAX_BYTES) : buffer;
        const b64 = buf.toString('base64');
        const sizeKb = Math.round(buffer.length / 102.4) / 10;
        const filename = `wa_${Date.now()}.jpg`;
        const contextLabel = caption
          ? `Image shared in WhatsApp group "${groupName}" by ${senderName}. Caption: ${caption.slice(0, 300)}`
          : `Image shared in WhatsApp group "${groupName}" by ${senderName} (no caption)`;

        console.log(`[WA] OCR request: ${filename} (${sizeKb} KB)`);

        let ocrResult = null;
        let ocrConnectError = null;
        try {
          const ocrResp = await fetch(`${ariaUrl}/api/aria/ocr`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ image: b64, filename, context: contextLabel }),
            signal: AbortSignal.timeout(120000),
          });
          if (ocrResp.ok) {
            ocrResult = await ocrResp.json();
          } else {
            const errText = (await ocrResp.text()).slice(0, 200);
            console.warn(`[WA] OCR failed: ${ocrResp.status} ${errText}`);
            ocrConnectError = `HTTP ${ocrResp.status}${errText ? ': ' + errText : ''}`;
          }
        } catch (e) {
          console.warn('[WA] OCR request error:', e.message);
          ocrConnectError = `Network/timeout: ${e.message}`;
        }

        // ── If the OCR endpoint itself failed (502, 504, network), tell the
        // user clearly that this is an infrastructure issue, NOT an OCR
        // pipeline failure. The image never even reached the Python service.
        if (ocrConnectError) {
          await sendMessage(chatId, [
            `*ARIA* — 🛑 *I couldn't reach my OCR service.*`,
            ``,
            `The image is fine, but my Python intelligence service didn't respond:`,
            `\`${ocrConnectError}\``,
            ``,
            `*Check:*`,
            `• The ARIA Python service is running (\`flyctl status -a <app>\`)`,
            `• \`ARIA_SERVICE_URL\` env var on this WhatsApp listener points to the live service`,
            `• Network/firewall allows Seenode → fly.io traffic`,
            `• \`flyctl logs -a <aria-service>\` for any crash on the Python side`,
            ``,
            `Once the service is back, send the image again — the OCR pipeline itself is working.`,
          ].join('\n'), msg).catch(() => {});
          return null;
        }

        const extracted = (ocrResult?.text || '').trim();

        if (!extracted) {
          // OCR pipeline returned nothing. Surface the full diagnostic trace
          // so we can see EXACTLY which backends were tried and why each failed —
          // no need to access fly.io logs to debug.
          const autoInst = ocrResult?.auto_installing;
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
          await sendMessage(chatId, [
            `*ARIA* — 🖼 *I tried to read the image but the OCR pipeline returned no text.*`,
            ``,
            `The image looks fine to me visually, so this is most likely an infrastructure issue. Diagnostic trace:`,
            triedLine,
            lastLine,
            errorLine,
            note ? `\n_Note:_ ${note}` : '',
            autoInst ? `\n_Background install of local OCR is running — try again in 60s._` : ``,
            ``,
            `_Run */vision-status* for full backend diagnostics._`,
          ].filter(Boolean).join('\n'), msg).catch(() => {});
          return null;
        }

        const method = ocrResult.method || 'vision';
        const charCount = extracted.length;
        const autoInst = ocrResult?.auto_installing;
        console.log(`[WA] OCR ${method}: ${charCount} chars from ${filename}${autoInst ? ' (background install triggered)' : ''}`);

        // Feed the extracted text into ARIA's research pipeline so she learns from it
        let factsLearned = 0;
        try {
          const docResp = await fetch(`${ariaUrl}/api/aria/read-document`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              content: extracted.slice(0, 15000),
              filename,
              source: `whatsapp:${groupName}:${senderName}`,
              context: caption || `Image OCR from ${groupName}`,
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
          console.warn('[WA] Image-to-knowledge ingest failed:', e.message);
        }

        // Build the reply — friendly method label + installing note when applicable
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
        await sendMessage(chatId, `*ARIA* — 🖼 *Image read* (${methodLabel}, ${charCount} chars):\n\n${preview}${more}${factsLine}${installNote}`, msg).catch(() => {});

        // ── ALWAYS analyse + explain + research after extraction ──────────
        // This is the "extract → explain → research" pattern. ARIA doesn't
        // just dump OCR text and stop — she identifies what the document is,
        // pulls out entities (companies, people, products, amounts, dates,
        // contract numbers), screens for compliance risks, and answers any
        // question the user attached as a caption. Local reasoning router
        // handles routine queries for free; only novel ones hit DeepSeek.
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

        await sendMessage(chatId, `*ARIA* — 🔎 _Analysing the image content${captionTrimmed ? ` and answering: "${captionTrimmed.slice(0, 100)}"` : ''}…_`, msg).catch(() => {});

        try {
          const rawAnalysis = await askARIA(analysisPrompt, '', senderName);
          const analysis = _unwrapAriaReply(rawAnalysis, null);
          if (analysis) {
            await sendMessage(chatId, `*ARIA* — 🧠 *Analysis:*\n\n${analysis}`, msg).catch(() => {});
          } else if (rawAnalysis && rawAnalysis.error) {
            await sendMessage(chatId, `*ARIA* — ⚠️ I extracted the image but my reasoning step failed: ${rawAnalysis.message}\n\n_Self-diagnostic triggered — I'll learn from this._`, msg).catch(() => {});
          }
        } catch (e) {
          console.warn('[WA] Image-analysis chat failed:', e.message);
          await sendMessage(chatId, `*ARIA* — ⚠️ I extracted the image but my reasoning step failed: ${e.message}`, msg).catch(() => {});
          _triggerSelfDiagnostic('image_analysis_failure', e.message, { stage: 'post_ocr_chat' }).catch(() => {});
        }

        // Store + feed to ARIA conversational memory
        const content = `[Image OCR from ${senderName}] ${caption ? caption + '\n\n' : ''}${extracted.slice(0, 2000)}`;
        store(chatId, groupName, msg.key.participant, senderName, content, new Date().toISOString());
        await feedToARIA(groupName, senderName, content, 'whatsapp_image', {
          ocr_method: method,
          chars_extracted: charCount,
          has_caption: !!caption,
        }).catch(() => {});

        return content;
      } catch (e) {
        console.warn('[WA] Image processing failed:', e.message);
        await sendMessage(chatId, `*ARIA* — ⚠️ Image processing error: ${e.message}`, msg).catch(() => {});
        return null;
      }
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

// ── Auth state persistence — survives Seenode deploys via Upstash Redis ──────
// Uses direct Upstash REST calls (not store.mjs) to ensure read/write consistency

const UPSTASH_URL   = () => process.env.UPSTASH_REDIS_URL;
const UPSTASH_TOKEN = () => process.env.UPSTASH_REDIS_TOKEN;
const upstashOk     = () => !!(UPSTASH_URL() && UPSTASH_TOKEN());

async function upstashCmd(...args) {
  if (!upstashOk()) return null;
  const res = await fetch(`${UPSTASH_URL()}`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${UPSTASH_TOKEN()}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(args),
    signal: AbortSignal.timeout(10000),
  });
  if (!res.ok) throw new Error(`Upstash ${res.status}`);
  const data = await res.json();
  return data.result ?? null;
}

async function backupAuthToRedis() {
  if (suppressAuthBackup) return;  // skip during 440 cleanup
  if (!upstashOk()) return;
  try {
    const allFiles = fs.readdirSync(AUTH_DIR);
    const files = allFiles.filter(f => f === 'creds.json' || f.startsWith('app-state') || f.startsWith('pre-key') || f.startsWith('sender-key') || f.startsWith('session-'));
    if (!files.length) return;
    const authBundle = {};
    let totalSize = 0;
    for (const f of files) {
      const content = fs.readFileSync(path.join(AUTH_DIR, f), 'utf8');
      totalSize += content.length;
      if (totalSize > 500000) break;
      authBundle[f] = content;
    }
    if (!Object.keys(authBundle).length) return;
    const value = JSON.stringify(authBundle);
    await upstashCmd('SET', AUTH_REDIS_KEY, value, 'EX', 90 * 24 * 3600);
    // Verify the write by reading back
    const check = await upstashCmd('STRLEN', AUTH_REDIS_KEY);
    if (check && check > 100) {
      console.log(`[WA Listener] Auth backed up to Redis (${Object.keys(authBundle).length}/${allFiles.length} files, ${Math.round(totalSize/1024)}KB, verified: ${check} bytes in Redis)`);
    } else {
      console.warn(`[WA Listener] Auth backup verification FAILED — STRLEN returned ${check}`);
    }
  } catch(e) {
    console.warn('[WA Listener] Auth backup failed:', e.message);
  }
}

async function restoreAuthFromRedis() {
  if (!upstashOk()) {
    console.warn('[WA Listener] Upstash not configured — cannot restore auth');
    return false;
  }
  try {
    console.log('[WA Listener] Attempting auth restore from Redis...');
    // Check if key exists first
    const len = await upstashCmd('STRLEN', AUTH_REDIS_KEY);
    console.log(`[WA Listener] Redis key ${AUTH_REDIS_KEY} STRLEN: ${len}`);
    if (!len || len < 10) {
      console.warn('[WA Listener] No auth data in Redis (empty or missing)');
      return false;
    }
    // Read the value
    const raw = await upstashCmd('GET', AUTH_REDIS_KEY);
    if (!raw) {
      console.warn('[WA Listener] Redis GET returned null');
      return false;
    }
    console.log(`[WA Listener] Redis returned ${typeof raw}, length: ${typeof raw === 'string' ? raw.length : 'N/A'}`);
    const authBundle = typeof raw === 'string' ? JSON.parse(raw) : raw;
    if (!authBundle || typeof authBundle !== 'object') {
      console.warn('[WA Listener] Auth bundle invalid type:', typeof authBundle);
      return false;
    }
    const files = Object.keys(authBundle);
    if (!files.length) {
      console.warn('[WA Listener] Auth bundle has no files');
      return false;
    }
    fs.mkdirSync(AUTH_DIR, { recursive: true });
    let restored = 0;
    for (const [f, content] of Object.entries(authBundle)) {
      try {
        fs.writeFileSync(path.join(AUTH_DIR, f), content, 'utf8');
        restored++;
      } catch(e) {
        console.warn(`[WA Listener] Failed to write ${f}:`, e.message);
      }
    }
    console.log(`[WA Listener] Auth restored from Redis (${restored}/${files.length} files) — no QR scan needed`);
    return restored > 0;
  } catch(e) {
    console.warn('[WA Listener] Auth restore failed:', e.message);
    return false;
  }
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

  // Skip restore if auth was recently invalidated (prevents 440 loop)
  const recentlyInvalidated = (Date.now() - authInvalidatedAt) < 120000;  // 2 min
  if (recentlyInvalidated) {
    console.log('[WA Listener] Auth was recently invalidated — skipping restore, will show QR');
    try { fs.rmSync(AUTH_DIR, { recursive: true, force: true }); } catch {}
    fs.mkdirSync(AUTH_DIR, { recursive: true });
    if (upstashOk()) { try { await upstashCmd('DEL', AUTH_REDIS_KEY); } catch {} }
  }

  // Check if auth state exists locally (previously scanned)
  let authFiles = fs.readdirSync(AUTH_DIR).filter(f => f.endsWith('.json'));
  if (authFiles.length > 0 && !recentlyInvalidated) {
    console.log(`[WA Listener] Found saved auth (${authFiles.length} files) — attempting auto-reconnect`);
  } else if (!recentlyInvalidated) {
    // Try restoring from Redis (survives Seenode deploys)
    const restored = await restoreAuthFromRedis();
    if (restored) {
      authFiles = fs.readdirSync(AUTH_DIR).filter(f => f.endsWith('.json'));
    } else {
      console.log('[WA Listener] No saved auth — QR code scan required');
    }
  } else {
    console.log('[WA Listener] Waiting for QR code scan...');
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

  sock.ev.on('creds.update', async () => {
    await saveCreds();
    // Only backup to Redis when connected — prevents stale auth loop
    if (isConnected && !suppressAuthBackup) {
      backupAuthToRedis().catch(() => {});
    }
  });

  sock.ev.on('connection.update', async ({ connection, lastDisconnect, qr }) => {
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
      suppressAuthBackup = false;  // safe to backup again
      console.log('[WA Listener] ✓ Connected to WhatsApp — ARIA is listening');
      console.log('[WA Listener] GET /api/wa-listener/groups to find your group IDs');
      // Delay backup — wait 5s to confirm connection is stable
      setTimeout(() => {
        if (isConnected) backupAuthToRedis().catch(() => {});
      }, 5000);
      // Start the proactive alert poller — only once per connection
      _startProactiveAlertPoller();
    }

    if (connection === 'close') {
      isConnected = false;
      const code  = lastDisconnect?.error?.output?.statusCode;
      const logout = code === DisconnectReason.loggedOut;

      if (logout || code === 440) {
        // Auth is invalid — nuke everything and require fresh QR
        suppressAuthBackup = true;
        authInvalidatedAt = Date.now();  // prevents restore on next startListener()
        console.log(`[WA Listener] Auth invalid (code ${code}) — full reset for QR scan...`);
        // 1. Kill socket immediately to stop all events
        if (sock) { try { sock.ev.removeAllListeners(); sock.end(); } catch {} sock = null; }
        // 2. Delete from Redis
        if (upstashOk()) {
          try {
            await upstashCmd('DEL', AUTH_REDIS_KEY);
            console.log('[WA Listener] Redis auth deleted');
          } catch (e) { console.warn('[WA Listener] Redis delete failed:', e.message); }
        }
        // 3. Delete local auth
        try { fs.rmSync(AUTH_DIR, { recursive: true, force: true }); } catch {}
        console.log('[WA Listener] Auth fully cleared — QR required');
        // 4. Restart after delay (suppressAuthBackup stays true until successful connect)
        setTimeout(startListener, 15000);
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

      // ── Reaction detection — feedback signal on ARIA replies ───────────
      // Baileys delivers reactions via messages.upsert with a reactionMessage
      // payload. The reactionMessage.key points to the original message that
      // was reacted to. We only care about reactions on messages ARIA sent
      // (key.fromMe === true on the target). Empty emoji = reaction removed.
      // This block must run BEFORE the text-extraction + empty-text skip
      // because reaction messages carry no normal text content.
      const reactionMsg = msg.message?.reactionMessage;
      if (reactionMsg && reactionMsg.key && reactionMsg.key.fromMe) {
        const targetChatId = reactionMsg.key.remoteJid || chatId;
        const targetMsgId = reactionMsg.key.id;
        const emoji = reactionMsg.text || '';
        const ariaUrlR = process.env.ARIA_SERVICE_URL;
        console.log(`[WA Reaction] ${senderName} reacted ${emoji || '(removed)'} to ARIA msg ${targetMsgId}`);
        if (ariaUrlR && targetMsgId) {
          fetch(`${ariaUrlR}/api/aria/feedback`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              chat_id: targetChatId,
              msg_id: targetMsgId,
              emoji,
              reactor: senderName,
              reactor_jid: senderJid,
            }),
            signal: AbortSignal.timeout(8000),
          }).then(r => r.ok ? r.json() : null).then(result => {
            if (result && result.ok && result.record) {
              console.log(`[WA Reaction] Recorded ${result.record.sentiment} feedback ${result.record.id}`);
            }
          }).catch(e => console.warn('[WA Reaction] feedback POST failed:', e.message));
        }
        continue;  // Reactions don't carry text — nothing else to process
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

      // ── Process media (images, PDFs, contacts, locations) even without text ─
      // BUG-FIX: imageMessage was previously NOT in this check, so images
      // shared in groups were completely ignored — processMedia() never even
      // ran for them. Now images go through the full OCR pipeline.
      const hasMedia = msg.message && (
        msg.message.imageMessage ||
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

      // ── Command detection (/screen, /classify, /teach, etc.) ──────────
      const cmdMatch = text.match(COMMAND_RE);
      if (cmdMatch) {
        try {
          const reply = await handleCommand(cmdMatch[1], cmdMatch[2], senderName, senderJid);
          if (reply) {
            await sendMessage(chatId, `*ARIA* — ${reply}`, msg);
            continue;
          }
        } catch (cmdErr) {
          console.error(`[WA Listener] Command /${cmdMatch[1]} crashed:`, cmdErr.message);
          await sendMessage(chatId, `*ARIA* — Command failed. Please try again.`, msg).catch(() => {});
          continue;
        }
      }

      // ── ARIA mention detection — reply when mentioned ─────────────────
      if (isAriaMentioned(text)) {
        handleAriaMention(msg, chatId, groupName, senderName, text).catch(e => {
          console.warn('[WA Listener] ARIA reply failed:', e.message);
        });
        continue;
      }

      // ── Smart auto-respond on compliance/deal/risk keywords ───────────
      if (AUTO_RESPOND) {
        const trigger = detectComplianceTrigger(text);
        if (trigger.triggered && shouldAutoRespond(trigger.keyword, chatId)) {
          askARIA(
            `A team member said: "${text.slice(0, 500)}". Provide a brief (under 300 words) intelligence note relevant to this ${trigger.category} topic. Be specific and actionable.`,
            '', senderName
          ).then(reply => {
            if (reply) sendMessage(chatId, `_ARIA noticed:_ ${reply.slice(0, 500)}`, msg);
          }).catch(() => {});
        }
      }

      // ── Detect URLs and send to ARIA for article reading ───────────────
      const urls = text.match(/https?:\/\/[^\s<>"'\]\)]+/gi) || [];
      const ariaUrl = process.env.ARIA_SERVICE_URL;
      if (ariaUrl && urls.length > 0) {
        for (const articleUrl of urls) {
          // Skip non-article URLs (images, social media posts, etc.)
          if (/\.(jpg|jpeg|png|gif|mp4|mp3|pdf)$/i.test(articleUrl)) continue;
          if (/^https?:\/\/(wa\.me|chat\.whatsapp|t\.me|twitter|x\.com|facebook|instagram)/i.test(articleUrl)) continue;
          fetch(`${ariaUrl}/api/aria/read`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url: articleUrl, context: `Shared by ${senderName} in ${groupName}: ${text.slice(0, 200)}` }),
            signal: AbortSignal.timeout(120000),
          }).then(r => r.ok ? r.json() : null).then(result => {
            if (result && result.facts_learned > 0) {
              console.log(`[WA→ARIA] Read article: ${articleUrl.slice(0, 60)} → ${result.facts_learned} facts learned`);
            }
          }).catch(() => {});
        }
      }
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
      messages_sent:  messagesSent,
      reply_enabled:  WA_REPLY_ENABLED,
      target_groups:  TARGET_GROUPS.length ? TARGET_GROUPS : 'ALL',
      group_names:    Object.fromEntries(groupNames),
      memory_store:   messageStore.length,
      note:           isConnected
        ? 'ARIA is listening and replying to WhatsApp groups'
        : 'Not connected — check logs for QR code',
    });
  });

  // Send a message to a group or contact (API-driven)
  app.post('/api/wa-listener/send', async (req, res) => {
    if (!sock || !isConnected) {
      return res.status(503).json({ error: 'WhatsApp not connected' });
    }
    const { group_id, chat_id, message } = req.body || {};
    const target = group_id || chat_id;
    if (!target || !message) {
      return res.status(400).json({ error: 'group_id (or chat_id) and message required' });
    }
    const ok = await sendMessage(target, message);
    if (ok) {
      res.json({ ok: true, sent_to: groupNames.get(target) || target, length: message.length });
    } else {
      res.status(500).json({ error: 'Failed to send message' });
    }
  });

  // Ask ARIA a question and send her reply to a group
  app.post('/api/wa-listener/ask-aria', async (req, res) => {
    if (!sock || !isConnected) {
      return res.status(503).json({ error: 'WhatsApp not connected' });
    }
    const { group_id, question } = req.body || {};
    if (!group_id || !question) {
      return res.status(400).json({ error: 'group_id and question required' });
    }
    const rawReply = await askARIA(question, '', 'api');
    const reply = _unwrapAriaReply(rawReply, null);
    if (!reply) {
      return res.status(502).json({
        error: 'ARIA did not respond',
        detail: rawReply?.message || 'unknown',
      });
    }
    const response = `*ARIA* — ${reply}`;
    const ok = await sendMessage(group_id, response);
    res.json({ ok, response: reply });
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

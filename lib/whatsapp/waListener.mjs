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
// brainAbsorb feeds the /teach URL path's success/failure events into
// the learning store. Past incident 2026-04-18: it was called at
// line ~402/412 without being imported, so every successful crawl
// triggered a ReferenceError caught by the outer try/catch which then
// sent the user a bogus "Failed to learn from X — brainAbsorb is not
// defined" message RIGHT AFTER the legitimate "Learned from X" notice.
import { brainAbsorb } from '../self/learning_store.mjs';
import { conversationManager } from './conversationManager.mjs';
import { xlsxSizeOk, MAX_XLSX_BYTES } from '../util/xlsxGuard.mjs'; // R-F2592
// R-F3833 — the ONE localhost-bypass decision, keyed off the real TCP peer.
import { localhostBypassAllowed } from '../auth/localhostBypass.mjs';

const ENABLED       = process.env.WA_LISTENER_ENABLED === 'true';
const GROUP_IDS_RAW = process.env.WA_LISTENER_GROUP_IDS || '';
const AUTH_DIR      = process.env.WA_LISTENER_AUTH_DIR  || './wa-listener-auth';
// Pre-Phase-3 cleanup 2026-04-09: removed the hardcoded 'aria-internal'
// fallback. The constant string was published in the public repo so anyone
// could authenticate as `Bearer aria-internal` if ARIA_INTERNAL_TOKEN was
// unset. Same bug class as the server.mjs fix in commit 9c0830d — second
// copy that slipped through. If the env var is missing we fail closed (the
// listener still boots, but the routes return 500 with an explicit message).
const INT_TOKEN     = process.env.ARIA_INTERNAL_TOKEN   || '';
if (ENABLED && !INT_TOKEN) {
  console.warn(
    '[WA Listener] CRITICAL: ARIA_INTERNAL_TOKEN is not set. ' +
    'Mounted /api/wa-listener/* routes will reject all requests.'
  );
}
// R-F388: AUTH_REDIS_KEY removed — auth bundle now lives in fly.io's
// SQLite via /api/aria/wa-auth/{backup,restore,delete}.

// Local auth middleware for the /api/wa-listener/* routes mounted by
// mountWAListener(). Mirrors the server.mjs requireAuth pattern: localhost
// bypass + bearer-token check against INT_TOKEN. Pre-Phase-3 cleanup
// 2026-04-09: previously these 5 routes were unauthenticated, allowing any
// network-adjacent caller to inject WhatsApp messages or fire ARIA queries.
function _waRequireAuth(req, res, next) {
  // Localhost bypass — internal seenode→listener calls have no auth header.
  //
  // R-F3833 — was `req.ip || req.connection?.remoteAddress`, which aria-web's
  // `trust proxy: 1` makes forgeable: a 6PN peer sending `X-Forwarded-For:
  // 127.0.0.1` reached next() and could inject WhatsApp messages or fire ARIA
  // queries. This gate also had NO ARIA_DISABLE_LOCALHOST_BYPASS check at all;
  // it inherits the operator kill switch by routing through the shared helper.
  if (localhostBypassAllowed(req)) {
    return next();
  }
  if (!INT_TOKEN) {
    return res.status(500).json({ error: 'ARIA_INTERNAL_TOKEN not configured on listener' });
  }
  const auth = req.headers.authorization || '';
  const expected = `Bearer ${INT_TOKEN}`;
  if (auth !== expected) {
    return res.status(401).json({ error: 'Unauthorized' });
  }
  return next();
}

const WA_REPLY_ENABLED = process.env.WA_REPLY_ENABLED !== 'false'; // default ON
const AUTO_RESPOND     = (process.env.WA_LISTENER_AUTO_RESPOND || 'true').toLowerCase() === 'true';
const TARGET_GROUPS = GROUP_IDS_RAW
  ? GROUP_IDS_RAW.split(',').map(g => g.trim()).filter(Boolean)
  : [];

// ── Channel-mirror configuration (prod-hardening) ─────────────────────────────
// Groups to mirror in full (every message → ingest endpoint, silent).
const ARIA_MIRROR_GROUPS = new Set(
  (process.env.ARIA_MIRROR_GROUPS || '')
    .split(',').map(s => s.trim()).filter(Boolean)
);
// Known counterparty JIDs — their messages go through claim ledger + deception.
const ARIA_COUNTERPARTY_CONTACTS = new Set(
  (process.env.ARIA_COUNTERPARTY_CONTACTS || '')
    .split(',').map(s => s.trim()).filter(Boolean)
);
const ARIA_DECEPTION_THRESHOLD = parseFloat(
  process.env.ARIA_DECEPTION_THRESHOLD || '0.50'
);
const ARIA_MIRROR_MIN_LEN = parseInt(
  process.env.ARIA_MIRROR_MIN_LEN || '30', 10
);
const MAX_DOC_CHARS = parseInt(
  process.env.ARIA_MAX_DOC_CHARS || '200000', 10
);

// Prepend a [!PARTIAL EXTRACTION ...] banner when the parser returned
// less than the full document. The banner travels with the extracted
// text into the [ATTACHED DOCUMENT] block so the LLM can never treat
// "X is not in the text" as "X is not in the document". Past incident
// 2026-04-28: ARIA confidently asserted GESPI was not in Annex 1 of
// a signed agreement after pdf-parse silently truncated before Annex 1.
function stampPartialExtraction(extracted, droppedSummary) {
  if (!extracted || !droppedSummary) return extracted;
  return (
    `[!PARTIAL EXTRACTION — ${droppedSummary}. Content past that point ` +
    `— including any annexes, schedules, signature pages, or appendices ` +
    `at the end of the document — is NOT in the text below. You MUST NOT ` +
    `assert that any clause, party, annex item, or term is absent from ` +
    `the document based on this text alone. If you cannot find something, ` +
    `say 'not present in the extracted portion' and ask the user to paste ` +
    `the missing section. Negative claims about document content require ` +
    `the FULL document, not a truncated extract.]\n\n` +
    extracted
  );
}

// R-F853 (2026-05-24) — recent-document lookup for a text follow-up that
// references a document. SYMMETRIC to the image-OCR buffer: users routinely
// send a contract as ONE WhatsApp message, then ask "Aria, analyse this
// contract" in a SEPARATE text message. That second message carries no
// documentMessage, so the attach branch never fires and ARIA honestly reports
// "no document in my context" — the recurring 2026-05-24 contract-review
// failure. This pulls the most recent REAL extracted document from the same
// sender so the follow-up question sees it. Returns the stored doc text, or
// null when the text doesn't reference a document / no recent doc exists (then
// normal chat proceeds and the honest "no document" answer is correct).
// Exported as a pure function for unit testing (no module side effects).
const _DOC_REF_PATTERN = /\b(contract|agreement|nda|mou|rfq|tender|document|annex|appendix|clause|terms|paperwork|the\s+file|the\s+pdf|attachment)\b/i;

export function findRecentDocForReference(store, chatId, senderName, text) {
  if (!text || !_DOC_REF_PATTERN.test(text)) return null;
  if (!Array.isArray(store)) return null;
  const recentDoc = store
    .filter(m => m.groupId === chatId && m.senderName === senderName)
    .slice(-6)
    .reverse()
    .find(m => /^\[(Document|Image)[:\s]/i.test(m.text || '')
            && (m.text || '').length > 200
            && !/Document shared:/i.test(m.text || ''));
  return recentDoc ? recentDoc.text : null;
}

// ── Clause 20 commitment guard (JavaScript port for streaming path) ─────────
// The Python commitment_guard.py runs on /api/aria/chat but the streaming
// endpoint (/api/aria/chat/stream) bypasses all post-processors. This
// lightweight JS version catches the same patterns on the assembled response
// before it goes to WhatsApp.
const _COMMITMENT_PATTERNS = [
  { re: /[Ww]ithin (?:the next )?\d+\s*(?:hour|day|minute)s?,?\s*I (?:will|shall)\s+[^.!?]+[.!?]/g, type: 'time-bound', action: 'rewrite' },
  { re: /You will receive\s+[^.!?]+?(?:within|by|before|in)\s+[^.!?]+[.!?]/gi, type: 'delivery-promise', action: 'rewrite' },
  { re: /(?:Within the next \d+ hours?:|Next Step \(Within[^)]+\):?)\s*[^.!?\n]+[.!?]?/gi, type: 'next-step-deadline', action: 'rewrite' },
  { re: /ARIA (?:Status|is)\s*[:—][^\n]+(?:live|active|running|operational)[^\n]*/gi, type: 'status-footer', action: 'strip' },
  { re: /(?:Deception|Detection|Audit|Protocol|Observability)[^\n]*(?:running|active|enabled|live|operational)[^\n]*/gi, type: 'protocol-claim', action: 'strip' },
  { re: /(?:Autonomy engine|autonomous engine)\s+(?:active|running|enabled|live)[^\n]*/gi, type: 'engine-claim', action: 'strip' },
];
const _CLAUSE20_NOTE = ' [Clause 20: I cannot commit to future deliverables — ask me to do this now.]';
function _guardCommitments(text) {
  if (!text || text.length < 50) return text;
  let guarded = text;
  for (const { re, type, action } of _COMMITMENT_PATTERNS) {
    re.lastIndex = 0; // reset regex state
    guarded = guarded.replace(re, (match) => {
      if (action === 'strip') return '';
      return match.replace(/[.!?]$/, '.') + _CLAUSE20_NOTE;
    });
  }
  // Clean up multiple blank lines left by stripping
  guarded = guarded.replace(/\n{3,}/g, '\n\n').trim();
  return guarded;
}

// ── ARIA mention detection ──────────────────────────────────────────────────
const ARIA_MENTIONS = [/\baria\b/i, /@aria/i, /^aria[,:]/i];
function isAriaMentioned(text) {
  const t = (text || '').slice(0, 2000);
  return ARIA_MENTIONS.some(p => p.test(t));
}

// ── Option B — Social tone + greeting handling ──────────────────────────────
// ARIA_TONE: professional (default) | warm | concise. Passed to brain so the
// chat response picks the right register. Warm = friendlier phrasing for
// counterparty-facing groups; concise = tight answers for team channels.
const ARIA_TONE = (process.env.ARIA_TONE || 'professional').toLowerCase();

// Short social pleasantries directed at ARIA — replied locally without
// hitting the LLM. Matches messages where the ENTIRE intent is a greeting
// or acknowledgement, not a real question. Saves cost + avoids
// over-engineered responses to "hi" and "thanks".
const SOCIAL_GREETING_PATTERNS = [
  /^(hi|hey|hello|hola|olá|ola|bom dia|boa tarde|good (morning|afternoon|evening))[,! ]*aria[.! ]*$/i,
  /^aria[,! ]*(hi|hey|hello|good (morning|afternoon|evening))[.! ]*$/i,
  /^(nice|pleasure|great) to (meet|e-meet) you[,! ]*aria[.! ]*$/i,
  /^(hi|hey|hello)[ ,]*(everyone|all|team)[.! ]*$/i,   // general greetings
  /^(thanks|thank you|cheers|thx)[,! ]*aria[.! ]*$/i,
  /^aria[,! ]*(thanks|thank you|welcome)[.! ]*$/i,
  /^welcome[,! ]*aria[.! ]*$/i,
  /^(how are you|you okay|you good)[,?! ]*aria[.! ]*$/i,
];
function isSocialGreeting(text) {
  const t = (text || '').trim().toLowerCase();
  if (t.length > 80) return false;  // long messages aren't just greetings
  return SOCIAL_GREETING_PATTERNS.some(p => p.test(t));
}

// Short, honest, tone-adjusted social replies — no LLM involved.
// ARIA is honestly labelled; these replies acknowledge, they don't
// impersonate. Covers the common group-intro pleasantries.
function socialReplyFor(text) {
  const t = (text || '').toLowerCase();
  const warm = ARIA_TONE === 'warm';
  const concise = ARIA_TONE === 'concise';

  if (/welcome/.test(t)) {
    return warm
      ? "Thank you — glad to be here. I'm ARIA, an autonomous intelligence assistant. Just tag me with @ARIA if you need anything."
      : concise
        ? "Thanks. I'm ARIA, an autonomous intelligence assistant. @ARIA to engage."
        : "Thank you. I'm ARIA, an autonomous intelligence assistant — tag me with @ARIA when you need research or a compliance check.";
  }
  if (/(nice|pleasure|great) to (meet|e-meet)/.test(t)) {
    return warm
      ? "Likewise — I'm ARIA, an autonomous intelligence assistant. I help the team with research and deal context. Happy to be in the loop."
      : "Pleased to meet you. I'm ARIA, an autonomous intelligence assistant. I help with research and record-keeping — tag me with @ARIA whenever useful.";
  }
  if (/(thanks|thank you|cheers|thx)/.test(t)) {
    return warm ? "You're very welcome." : "You're welcome.";
  }
  if (/(how are you|you okay|you good)/.test(t)) {
    return warm ? "All good — ready to help whenever you need. Tag me with @ARIA." : "Operational — tag me with @ARIA when you need anything.";
  }
  // generic hello fallback
  return warm
    ? "Hi! ARIA here — an autonomous intelligence assistant. Tag me with @ARIA when you need research, a DD check, or a compliance note."
    : "Hi — ARIA, an autonomous intelligence assistant. Tag me with @ARIA when you need anything.";
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
  if (autoRespondDedup.size > 5000) autoRespondDedup.clear();
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

// ── ARIA fetch wrapper ──────────────────────────────────────────────────────
// Round 7 (2026-04-08): added bearer-token auth on the fly.io endpoints.
// This wrapper injects the token automatically so we don't have to patch
// every call site (30+) individually. When ARIA_API_TOKEN is unset, it
// behaves identically to bare fetch — soft rollout pattern.
function _ariaFetch(url, opts = {}) {
  // Token fallback chain matches Python's a0a1d35 — accepts EITHER token.
  // Past gap fix (2026-04-19): ARIA_INTERNAL_TOKEN-only seenode env was
  // failing auth because this helper only checked ARIA_API_TOKEN.
  const token = process.env.ARIA_API_TOKEN || process.env.ARIA_INTERNAL_TOKEN;
  if (!token) return fetch(url, opts);
  const headers = { ...(opts.headers || {}), 'Authorization': `Bearer ${token}` };
  return fetch(url, { ...opts, headers });
}

// ── §25 delivery-outcome reporting ──────────────────────────────────────────
// R-F3652: the two HTTP routes below (/api/wa-listener/send and .../ask-aria)
// call `reportOutcome?.(...)` on five branches, but NOTHING in this module ever
// defined or imported it. Optional CALL syntax does not help here: `foo?.()`
// only guards a null/undefined VALUE — on an undeclared binding it still throws
// `ReferenceError: reportOutcome is not defined`. So both routes threw on every
// path, including their success paths, and the §25 delivery wire they were
// added for (R-F2113/R-F2107) never once reported. server.mjs has its own
// reportOutcome and services/wa-listener/aria_wa_listener.mjs has another, but
// neither is reachable from here — server.mjs imports THIS module, so importing
// it back would be circular. Mirror the canonical implementation instead.
async function reportOutcome(surface, requestId, intendedResult, actualOutcome, latencyMs, detail) {
  if (!requestId) return;
  const base = process.env.ARIA_SERVICE_URL;
  if (!base) return;   // nothing to report to — stay silent, never throw
  try {
    await _ariaFetch(`${base}/api/aria/outcome`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        surface,
        request_id: requestId,
        intended_result: intendedResult,
        actual_outcome: actualOutcome,
        latency_ms: latencyMs || 0,
        detail: detail || '',
      }),
      signal: AbortSignal.timeout(3000),
    }).catch(() => {});
  } catch { /* outcome reporting must never break the reply path */ }
}

// ── Command handler ─────────────────────────────────────────────────────────
async function handleCommand(cmd, args, senderName, senderJid, notify = null) {
  const a = (args || '').trim().slice(0, 500);
  const port = process.env.PORT || 3117;
  const base = `http://localhost:${port}`;
  const headers = { 'Content-Type': 'application/json', 'Authorization': `Bearer ${INT_TOKEN}` };
  // notify(text) sends a follow-up WA message after the initial reply
  const _notify = notify || (() => {});

  async function post(path, body, timeoutMs = 90000) {
    try {
      const r = await fetch(`${base}${path}`, {
        method: 'POST', headers, body: JSON.stringify(body),
        signal: AbortSignal.timeout(timeoutMs),
      });
      if (!r.ok) {
        let detail = '';
        try { detail = (await r.text()).slice(0, 200); } catch {/* ignore */}
        return { error: `HTTP ${r.status}${detail ? ' — ' + detail : ''}` };
      }
      return r.json();
    } catch (e) {
      return { error: e.message || 'fetch failed' };
    }
  }
  // /report runs investigate + LLM fill — long-running, give it 5 min.
  async function postLong(path, body) { return post(path, body, 300000); }
  // GET helper for read-only endpoints (NSN decode, status checks, etc.)
  async function get(path, timeoutMs = 30000) {
    try {
      const r = await fetch(`${base}${path}`, {
        method: 'GET', headers,
        signal: AbortSignal.timeout(timeoutMs),
      });
      if (!r.ok) return { error: `HTTP ${r.status}` };
      return r.json();
    } catch (e) { return { error: e.message || 'fetch failed' }; }
  }

  switch (cmd.toLowerCase()) {
    case 'screen': {
      if (!a) return 'Usage: /screen [entity name]';
      // BUG-FIX 2026-04-08: previously accepted any blob (including 500-char
      // pasted prose with newlines) and reported "MATCH FOUND" whenever the
      // backend returned anything other than PERMITTED — including UNKNOWN.
      // /screen is for short entity names only; long prose belongs in
      // /investigate or a normal @ARIA mention.
      const trimmed = a.trim();
      if (trimmed.length > 120 || /\n/.test(trimmed)) {
        return '`/screen` takes a single short entity name (≤120 chars, no line breaks). For longer text, use `/investigate <name>` or just @-mention me with the context.';
      }
      const d = await post('/api/aria/compliance/screen', { entity_name: trimmed }).catch(() => ({}));
      const result = d.result || 'UNKNOWN';
      if (result === 'PERMITTED') {
        return `CLEAR *COMPLIANCE SCREEN*\nEntity: ${trimmed}\nResult: PERMITTED\n_Pre-screen only. Legal review required._`;
      }
      if (result === 'UNKNOWN' || result === 'INCONCLUSIVE' || result === 'NO_MATCH') {
        return `INCONCLUSIVE *COMPLIANCE SCREEN*\nEntity: ${trimmed}\nResult: ${result}\n_No clear match in pre-screen sources. Run full DD before proceeding._`;
      }
      return `BLOCKED *COMPLIANCE SCREEN*\nEntity: ${trimmed}\nResult: ${result}\n*MATCH FOUND. Do not proceed without legal review.*`;
    }
    case 'classify': {
      if (!a) return 'Usage: /classify [product description]';
      const d = await post('/api/aria/compliance/classify', { description: a }).catch(err => {
        console.warn('[WA /classify] post failed:', err?.message || err);
        return {};
      });
      let msg = `*ML CLASSIFICATION*\nProduct: ${a.slice(0, 80)}\n`;
      if (d.classifications?.length) {
        d.classifications.forEach(c => { msg += `- *${c.code || c.category}* ${c.description || ''}\n`; });
      } else { msg += `Result: ${d.result || d.category || 'No classification.'}\n`; }
      return msg + '_Classification advisory only._';
    }
    case 'sanctions': {
      if (!a) return 'Usage: /sanctions [name]';
      const d = await post('/api/aria/compliance/sanctions', { name: a }).catch(err => {
        console.warn('[WA /sanctions] post failed:', err?.message || err);
        return {};
      });
      const hits = d.matches || d.results || [];
      if (hits.length) {
        return `*SANCTIONS CHECK — ${a}*\n${hits.length} match(es):\n${hits.slice(0, 5).map(h => `- *${h.name || h.entity}* (${h.list || 'Unknown list'})`).join('\n')}\n*Do not proceed without legal review.*`;
      }
      return `*SANCTIONS CHECK — ${a}*\nNo matches found.\n_Full due diligence required._`;
    }
    case 'risk': {
      if (!a) return 'Usage: /risk [country]';
      const d = await post('/api/aria/compliance/risk', { country: a }).catch(err => {
        console.warn('[WA /risk] post failed:', err?.message || err);
        return {};
      });
      const level = d.risk_level || d.level || 'UNKNOWN';
      return `*COUNTRY RISK — ${a.toUpperCase()}*\nRisk: ${level}\n${d.notes || ''}`;
    }
    case 'teach': {
      if (!a) return 'Usage: /teach [topic]: [fact]   OR   /teach <https://url>';
      // BUG-FIX 2026-04-08: a bare URL like "/teach https://www.gjopen.com"
      // used to split on the first ':' giving topic="https" and fact="//...".
      // Detect URLs first and route them to the crawler so the user actually
      // gets the page content ingested instead of a useless "https" fact.
      //
      // BUG-FIX 2026-04-18: the previous URL detector required the URL to be
      // the WHOLE argument string. Users naturally type things like
      //   /teach about https://csg.com/en
      //   /teach me https://example.com
      //   /teach this url https://example.com
      // None of those matched, so they fell through to the `indexOf(':')`
      // path and got parsed as topic="about https", fact="//csg.com/en" —
      // completely useless. Past incident: /teach about https://csg.com/en
      // stored a garbage fact + left CSG Group uncrawled until the user
      // asked again. Fix: route to crawler if ANY http(s) URL appears in
      // the args, regardless of what preposition or filler word is before
      // it.
      const forceMatch = a.match(/^force\s+(https?:\/\/\S+)$/i);
      const anyUrlMatch = a.match(/\b(https?:\/\/\S+)/i);
      const urlMatch = forceMatch || anyUrlMatch;
      if (urlMatch) {
        // Strip common trailing punctuation the user may have added
        // (period, comma, question mark, trailing slash before ? or #).
        const rawUrl = forceMatch ? forceMatch[1] : urlMatch[1];
        const url = rawUrl.replace(/[.,;:!?)\]]+$/, '');
        let host = url;
        try { host = new URL(url).hostname.replace(/^www\./, ''); } catch {}
        const isForce = !!forceMatch;

        // ── Check if already learned (skip if force) ──
        if (!isForce) {
          try {
            const checkR = await fetch(`${base}/api/aria/semantic/search`, {
              method: 'POST', headers,
              body: JSON.stringify({ query: url, top_k: 3 }),
              signal: AbortSignal.timeout(5000),
            });
            if (checkR.ok) {
              const checkData = await checkR.json();
              const hits = (checkData.results || []).filter(r => {
                const text = (r.text || '').toLowerCase();
                const meta = r.metadata || {};
                return text.includes(host.toLowerCase()) || (meta.url || '').includes(host);
              });
              if (hits.length > 0) {
                const topHit = hits[0];
                const snippet = (topHit.text || '').slice(0, 200);
                return `I already know this source (*${host}*).\n\nHere's what I have:\n_"${snippet}…"_\n\nScore: ${topHit.score?.toFixed(2) || '?'}\n\nWant me to re-crawl for updates? Reply: */teach force ${url}*`;
              }
            }
          } catch { /* semantic search unavailable — proceed with crawl */ }
        }

        // Crawl with notification — tell the user what happened
        const crawlAndNotify = async () => {
          try {
            const r = await fetch(`http://localhost:${port}/api/aria/crawl`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${INT_TOKEN}` },
              body: JSON.stringify({ url, max_pages: 10, context: `Taught by ${senderName} via /teach` }),
              signal: AbortSignal.timeout(180000), // 3 min for large pages
            });
            if (r.ok) {
              const data = await r.json().catch(() => ({}));
              const pages = data.pages_crawled || data.pages_read || 0;
              const facts = data.facts_learned || 0;
              const duration = data.duration_ms ? `${Math.round(data.duration_ms / 1000)}s` : '?';
              if (pages === 0) {
                // 2026-04-18: don't lie and say "Learned" when zero pages
                // were actually ingested. This happens on JS-rendered SPAs
                // (Confluence / JIRA / React apps) where static HTML is a
                // shell. Surface the truth so the user knows to either
                // (a) give a direct content URL, or (b) ask us to wire
                // Lightpanda harder for that domain.
                await _notify(`⚠️ *Could not extract from ${host}*\nThe crawl reached the site but found no usable content (${duration}). Common cause: JS-rendered single-page app where the real content loads after browser JS runs. Try: copy the key text and send it as a message, then /teach [topic]: [text].`);
                brainAbsorb({
                  module: 'knowledge_ingestor',
                  summary: `/teach URL crawl returned 0 pages: ${host}`,
                  entity_name: host,
                  success: false,
                  confidence: 'ASSESSED',
                });
              } else {
                await _notify(`✅ *Learned from ${host}*\n${pages} pages crawled, ${facts} facts extracted (${duration}).\nAsk me anything about ${host} now.`);
                brainAbsorb({
                  module: 'knowledge_ingestor',
                  summary: `/teach URL ingested: ${host} — ${pages} pages, ${facts} facts`,
                  entity_name: host,
                  success: true,
                  confidence: 'CONFIRMED',
                });
              }
            } else {
              const err = await r.text().catch(() => '');
              await _notify(`⚠️ *Failed to learn from ${host}*\nHTTP ${r.status}${err ? ': ' + err.slice(0, 150) : ''}\nTry sending a shorter URL or the page content directly.`);
              brainAbsorb({
                module: 'knowledge_ingestor',
                summary: `/teach URL failed: ${host} — HTTP ${r.status}`,
                entity_name: host,
                success: false,
                confidence: 'ASSESSED',
              });
            }
          } catch (err) {
            const reason = err?.message?.includes('timeout') ? 'page took too long to load (>3 min)' : (err?.message || 'unknown error');
            await _notify(`⚠️ *Failed to learn from ${host}*\n${reason}\nTry: copy the page text and send it as a message, then use /teach [topic]: [all clauses]`);
          }
        };
        crawlAndNotify(); // fire in background — user gets immediate reply + follow-up notification
        return `Crawling *${host}* — I'll notify you when it's done (may take 1-3 minutes for large pages).`;
      }
      const sep = a.indexOf(':');
      if (sep < 1) return 'Usage: /teach [topic]: [fact]   OR   /teach <https://url>';
      const topic = a.slice(0, sep).trim();
      let fact = a.slice(sep + 1).trim();
      // Safety net — if we reached this path with a URL-shaped argument
      // it means the regex above didn't catch it. Don't silently store
      // garbage; tell the user the right syntax.
      if (/^https?$/i.test(topic) || /\bhttps?$/i.test(topic) || fact.startsWith('//')) {
        return 'It looks like you pasted a URL. Use:  /teach https://example.com  (no colon prefix needed, and no "about"/"me" in front).';
      }
      // BUG-FIX 2026-04-12: when the fact looks like a placeholder (e.g.
      // "[all clauses]", "the above", "this document"), pull the most recent
      // document from messageStore so /teach actually ingests the file content.
      // Tightened: only match explicit placeholder phrases, not any [text] or
      // common words like "this" which could be legitimate fact content.
      const placeholderRe = /^\[all\s*(?:clauses|content|text|of\s+it)?\]$|^(?:the\s+)?(?:above\s+)?(?:document|file|attachment)$|^all\s+clauses$/i;
      if (placeholderRe.test(fact)) {
        const recentDoc = messageStore.slice().reverse().find(m =>
          m.senderName === senderName && m.text.startsWith('[Document:')
        );
        if (recentDoc) {
          // Extract content after "[Document: filename] optional_caption\n\n"
          const docBody = recentDoc.text.replace(/^\[Document:[^\]]*\]\s*(?:[^\n]*\n\n)?/, '').trim();
          if (docBody.length > 50) {
            fact = docBody.slice(0, MAX_DOC_CHARS);
          }
        }
        if (placeholderRe.test(fact)) {
          return '⚠️ No recent document found from you to attach. Send the document first, then use /teach [topic]: [all clauses]';
        }
      }
      await post('/api/aria/knowledge/fact', { topic, content: fact, confidence: 'CONFIRMED', source: senderName }).catch(err => {
        console.warn('[WA /teach fact] post failed:', err?.message || err);
      });
      const chars = fact.length;
      return `Learned: *${topic}*\n${chars > 200 ? `(${chars} chars from document)` : `"${fact.slice(0, 150)}"`}\nStored as CONFIRMED. Thanks, ${senderName}!`;
    }
    case 'correct': {
      const sep = a.match(/\s*(?:->|→)\s*/);
      if (!sep) return 'Usage: /correct [wrong] -> [right answer]';
      const wrong = a.slice(0, sep.index).trim();
      const right = a.slice(sep.index + sep[0].length).trim();
      await post('/api/aria/correct', { originalResponse: wrong, correction: right, correctAnswer: right }).catch(() => {});
      return `Correction recorded.\nWrong: "${wrong.slice(0, 100)}"\nRight: "${right.slice(0, 100)}"\nThank you — this makes me smarter.`;
    }
    case 'docverify': {
      // /docverify doc_xxxxxx — mark a document extraction as human-verified
      const eid = (a || '').trim().split(/\s+/)[0];
      if (!eid || !eid.startsWith('doc_')) return 'Usage: /docverify doc_xxxxxx';
      const d = await post('/api/aria/document/verify', { extraction_id: eid, by: senderName }).catch(err => ({ error: err?.message }));
      if (d?.error) return `⚠️ Verify failed: ${d.error}`;
      if (!d?.ok) return `⚠️ Verify failed for \`${eid}\` — extraction not found.`;
      return `✅ *Verified* \`${eid}\` by ${senderName}. This extraction now seeds future few-shot examples for the same form type.`;
    }
    case 'docfix':
    case 'doccorrect': {
      // /docfix doc_xxxxxx field.path: new value
      // examples:
      //   /docfix doc_abc123 company_name_en: Acme Holdings Limited
      //   /docfix doc_abc123 allottees.0.name: BAKER IAN ARCHBALD
      //   /docfix doc_abc123 allottees.2.shares: 7400
      const m = (a || '').match(/^(doc_\w+)\s+([A-Za-z0-9_.]+)\s*:\s*(.+)$/s);
      if (!m) {
        return [
          'Usage: /docfix doc_xxxxxx <field.path>: <new value>',
          '',
          'Examples:',
          '  /docfix doc_abc123 company_name_en: Acme Holdings Limited',
          '  /docfix doc_abc123 allottees.0.name: BAKER IAN ARCHBALD',
          '  /docfix doc_abc123 allottees.2.shares: 7400',
        ].join('\n');
      }
      const [, eid, field, value] = m;
      const d = await post('/api/aria/document/correct', {
        extraction_id: eid, field, value: value.trim(), by: senderName,
      }).catch(err => ({ error: err?.message }));
      if (d?.error) return `⚠️ Correction failed: ${d.error}`;
      if (!d?.ok) return `⚠️ Correction failed for \`${eid}\`.`;
      return `✏️ *Corrected* \`${eid}\` — \`${field}\` → "${value.slice(0, 100)}". Total fixes on this extraction: ${d.corrections_total}. Future ${eid.split('_')[0]} extractions of the same form will use this as gold reference.`;
    }
    case 'docs': {
      // /docs              → 10 most recent extractions
      // /docs HK_NSC1      → recent extractions filtered by form code
      const arg = (a || '').trim();
      const qs = arg ? `?limit=10&form_code=${encodeURIComponent(arg)}` : '?limit=10';
      const port = process.env.PORT || 3117;
      const r = await fetch(`http://localhost:${port}/api/aria/document/extractions/recent${qs}`, {
        headers: { 'Authorization': `Bearer ${INT_TOKEN}` },
        signal: AbortSignal.timeout(10000),
      }).then(x => x.ok ? x.json() : null).catch(() => null);
      if (!r || !r.extractions?.length) return arg ? `No recent extractions for form \`${arg}\`.` : 'No recent document extractions on file.';
      const lines = [`📚 *Recent document extractions* (${r.count})`, ''];
      for (const e of r.extractions) {
        const tag = e.verified ? '✅' : (e.corrections ? `✏️×${e.corrections}` : '📝');
        lines.push(`${tag} \`${e.id}\` · ${e.form_code} · ${e.filename || '?'}`);
      }
      lines.push('', '_Use `/docverify <id>` or `/docfix <id> <field>: <value>`._');
      return lines.join('\n');
    }
    // NOTE: the old text-based /feedback command (POST to /api/brain/signal)
    // was removed in favour of the emoji-reaction feedback system added in
    // 14114f2. The richer /feedback case lives further down in this switch
    // and handles stats, list, lookup by id, and integrates with the trace
    // stream. Deleted because JS switch only runs the first matching case,
    // so leaving the old one shadowed the new one and made /feedback look
    // broken (returned the literal "Noted — I'll improve" string).
    case 'leads': {
      const d = await fetch(`${base}/api/brain/brief`, { headers, signal: AbortSignal.timeout(10000) }).then(r => r.ok ? r.json() : {}).catch(() => ({}));
      const leads = d.leads || d.brief?.leads || [];
      if (!leads.length) return 'No leads generated yet. Run /hunt to trigger a lead search.';
      return `*LATEST LEADS*\n${leads.slice(0, 5).map((l, i) => `${i + 1}. *${l.title || l.market || 'Lead'}* — ${(l.summary || l.content || '').slice(0, 150)}`).join('\n')}`;
    }
    case 'pipeline': {
      // Proxy to ARIA pipeline summary endpoint
      const ariaUrl = process.env.ARIA_SERVICE_URL;
      if (!ariaUrl) return '⚠️ ARIA service not configured.';
      try {
        const ariaToken = process.env.ARIA_API_TOKEN || '';
        const h = { 'Content-Type': 'application/json' };
        if (ariaToken) h['Authorization'] = `Bearer ${ariaToken}`;
        const r = await fetch(`${ariaUrl}/api/aria/pipeline/summary`, { headers: h, signal: AbortSignal.timeout(15000) });
        if (!r.ok) return `⚠️ Pipeline error: HTTP ${r.status}`;
        const data = await r.json();
        return data.summary || 'No pipeline data yet. Leads will appear as ARIA scans procurement signals.';
      } catch (e) {
        return `⚠️ Pipeline unavailable: ${e.message}`;
      }
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
    case 'investigate':
    case 'investigation':
    case 'dd':
    case 'duediligence':
    case 'due-diligence':
    case 'background':
    case 'profile': {
      // Slash-command shortcut to fire deep_research on a URL or entity
      // name. Routes through askARIA() with NO group context (clean
      // message body) so the server-side intent detector resolves it
      // cleanly via the deep_research path. Added 2026-04-09 19:38
      // after the user tried `/investigation https://duma-engineering.com`
      // and the listener silently dropped it (was not a registered
      // slash command).
      //
      // Usage:
      //   /investigate https://example.com
      //   /investigate Modirum Gespi
      //   /investigation Modirum Gespi https://modirumgespi.com
      //   /dd Vision International
      //   /background-check Acme Trading Corp
      if (!a) {
        return [
          `Usage: /${cmd.toLowerCase()} [URL or entity name]`,
          '',
          'Examples:',
          '  /investigate https://duma-engineering.com',
          '  /investigate Modirum Gespi',
          '  /investigation Vision International https://example.com',
          '  /dd Acme Trading Corp',
          '',
          'This fires the full deep_research path:',
          '  • 5 parallel web search angles (different facets)',
          '  • Top URLs ranked by source-tier authority',
          '  • Verbatim extraction from the highest-ranked results',
          '  • LLM synthesis with inline citations on every fact',
          '',
          'Expected wall-clock: 90-180 seconds.',
        ].join('\n');
      }
      // R-F3653: input sanitisation lifted from the SECOND `case 'investigate'`
      // that used to sit further down this switch. JS switch runs only the
      // FIRST matching case, so that block was unreachable for the whole time
      // it existed — the same shadowing bug this switch already hit once with
      // /feedback (see the note above `case 'leads'`). Its one piece of unique
      // value was this sanitisation, so it moves here, where it now applies to
      // every alias (/dd, /background, /profile …) rather than /investigate
      // alone. The dead block is deleted rather than left to rot.
      //
      // Strip WhatsApp JID-style mentions ("@201219301748858") that the wire
      // protocol substitutes for display-name @-mentions, then collapse
      // whitespace — without this an investigation is handed an opaque phone
      // number instead of "Arthur Switzerland".
      let _subject = a.replace(/@\d{6,}/g, '').replace(/\s+/g, ' ').trim();
      if (!_subject) {
        return `Usage: /${cmd.toLowerCase()} [name]   (WhatsApp @-mentions are anonymised by the protocol — type the name out)`;
      }
      // Cap to keep the prompt manageable. If the user pasted a long brief,
      // take the first line as the subject and pass the rest as context.
      let _extraContext = '';
      if (_subject.length > 200) {
        const _firstLine = _subject.split(/[\n.]/, 1)[0].trim().slice(0, 200);
        _extraContext = `\n\nAdditional context provided by ${senderName}:\n${_subject.slice(0, 2000)}`;
        _subject = _firstLine;
      }
      // Build a synthetic chat message that the server-side intent
      // detector will route to deep_research. The "Aria, investigate"
      // prefix is what the verb regex matches; the URL (if present)
      // is what the URL regex matches.
      const syntheticMsg = `Aria, investigate ${_subject}${_extraContext}`;
      // Don't await here — deep_research takes 90-180s and we want to
      // give the user a "thinking" acknowledgement immediately. Fire
      // the actual call in a fire-and-forget pattern that posts the
      // result to the same chat once it lands.
      //
      // For the slash command path, we just await directly and let the
      // existing 240s timeout cover us. The handleCommand caller has
      // its own timeout handling.
      try {
        const reply = _unwrapAriaReply(await askARIA(syntheticMsg, '', senderName));
        return reply || `⚠️ ${cmd} returned no usable data. The tool ran but produced nothing actionable. Try refining the query or sharing context about why you're asking.`;
      } catch (e) {
        return `⚠️ ${cmd} failed: ${e?.message || 'unknown error'}`;
      }
    }
    case 'groupsummary': {
      // BUG-FIX 2026-04-12: filter out slash commands, ARIA's auto-respond
      // echoes, and system/document metadata so the summary reflects actual
      // human conversation — not bot noise.
      const humanMessages = messageStore
        .filter(m =>
          m.text &&
          !m.text.startsWith('/') &&             // slash commands
          !m.text.startsWith('*ARIA*') &&        // ARIA replies echoed back
          !m.text.startsWith('_ARIA noticed:_') && // auto-respond echoes
          !m.text.startsWith('[Document:') &&    // raw doc extraction
          !m.text.startsWith('[Image OCR') &&    // OCR dumps
          !m.text.startsWith('[Contact shared]') &&
          !m.text.startsWith('[Location shared]') &&
          !/^🔴|^🟠|^🟡|^🟢/.test(m.text.trim()) // risk monitor auto-posts
        )
        .slice(-50)
        .map(m => `[${m.senderName}]: ${m.text.slice(0, 200)}`)
        .join('\n');
      if (!humanMessages) return 'No human messages stored yet. Messages from ARIA commands and auto-responses are filtered out.';
      return _unwrapAriaReply(await askARIA(
        `Summarise this group conversation. Highlight key topics, decisions, action items, and any compliance or risk mentions:\n\n${humanMessages}`,
        '', senderName,
      ));
    }
    case 'report': {
      // /report dd Acme Trading        — due-diligence report on a counterparty
      // /report compliance <subject>   — compliance assessment
      // /report market <country>       — market entry / opportunity report
      // Generates an ARIA-formatted draft via /api/aria/report which
      // chains investigate + Tier D template retrieval + LLM template fill.
      if (!a) {
        return [
          'Usage: /report [type] [subject]',
          '',
          'Types:',
          '  /report dd <person or company>     — Due diligence draft',
          '  /report compliance <subject>       — Compliance assessment draft',
          '  /report market <country or sector> — Market opportunity draft',
          '',
          'Drafts use any ingested templates ingested as Tier D in the corpus.',
          'If none exist, a built-in skeleton is used and the reply will say so.',
        ].join('\n');
      }
      const parts = a.trim().split(/\s+/);
      const reportType = parts[0].toLowerCase();
      const subject = parts.slice(1).join(' ').replace(/@\d{6,}/g, '').trim();
      if (!['dd', 'compliance', 'market'].includes(reportType)) {
        return `Unknown report type "${reportType}". Use one of: dd, compliance, market.\nExample: /report dd Acme Trading Corp`;
      }
      if (!subject || subject.length < 2) {
        return `Usage: /report ${reportType} [subject]\nExample: /report ${reportType} Acme Trading Corp`;
      }
      // Routes through local server.mjs proxy (added 2026-04-09) so the
      // listener doesn't need ARIA_API_TOKEN in its own process env.
      try {
        const d = await postLong('/api/aria/report', { report_type: reportType, subject });
        if (d.error) return `⚠️ Report build error: ${d.error}`;
        const header = [
          `*ARIA ${reportType.toUpperCase()} REPORT — DRAFT*`,
          `Subject: ${subject}`,
          `Template: ${d.template_source === 'tier_d' ? `Tier D (${d.template_chunks_used} chunks)` : 'fallback skeleton — ingest your real templates with the corpus CLI for better output'}`,
          `Investigation facts: ${d.investigation_facts || 0}`,
          '',
        ].join('\n');
        return header + (d.draft || '(empty draft)');
      } catch (e) {
        return `⚠️ Report build error: ${e.message}`;
      }
    }
    case 'forget': {
      // Wipe THIS sender's conversation history so cached context (including
      // any hallucinated content from a prior turn) doesn't influence the
      // next reply. The session_id construction must mirror askARIA so we
      // delete the same key the chat handler reads/writes.
      //
      // Defense in depth: try TWO paths in sequence so /forget works even
      // when one path is broken.
      //   1. Local server.mjs proxy (uses INT_TOKEN for local auth, then
      //      server.mjs injects ARIA_API_TOKEN to call fly.io). Preferred
      //      because it centralises the env var on one process.
      //   2. Direct fly.io call via _ariaFetch (uses listener-process
      //      ARIA_API_TOKEN env var). Used as a fallback when the proxy
      //      returns 503 (typically because server.mjs's ARIA_API_TOKEN
      //      is missing or fly.io is overloaded).
      //
      // Past incident 2026-04-09: /forget kept returning 503 for hours
      // because the proxy was silently failing on fly.io 401/timeout and
      // there was no fallback path. The session-history bleed kept
      // contaminating every chat reply because Antonio couldn't /forget.
      const sid = `wa_group_${senderName.replace(/[^a-zA-Z0-9]/g, '').slice(-10)}`;

      // Path 1: local proxy
      let proxyErr = '';
      try {
        const resp = await post('/api/aria/session/forget', { session_id: sid });
        if (!resp.error) {
          if (resp.existed) return `🧹 Conversation history wiped for ${senderName} (via local proxy). Next message starts fresh.`;
          return `(No active session found for ${senderName} — nothing to wipe.)`;
        }
        proxyErr = resp.error;
      } catch (e) {
        proxyErr = e.message || 'unknown error';
      }

      // Path 2: direct fly.io fallback
      console.warn(`[WA] /forget local proxy failed (${proxyErr}) — trying direct fly.io fallback`);
      const ariaServiceUrl = process.env.ARIA_SERVICE_URL;
      if (!ariaServiceUrl) {
        return `⚠️ /forget failed: local proxy returned "${proxyErr}" and no ARIA_SERVICE_URL is set for direct fallback`;
      }
      try {
        const resp = await _ariaFetch(`${ariaServiceUrl}/api/aria/session/forget`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ session_id: sid }),
          signal: AbortSignal.timeout(30000),
        });
        if (!resp.ok) {
          return `⚠️ /forget failed at BOTH paths. Proxy: "${proxyErr}". Direct fly.io: HTTP ${resp.status}.`;
        }
        const d = await resp.json();
        if (d.existed) return `🧹 Conversation history wiped for ${senderName} (direct fly.io fallback — proxy was: "${proxyErr}"). Next message starts fresh.`;
        return `(No active session found for ${senderName} — nothing to wipe.)`;
      } catch (e) {
        return `⚠️ /forget failed at BOTH paths. Proxy: "${proxyErr}". Direct fly.io: ${e.message}.`;
      }
    }
    case 'purgecases':
    case 'purge-cases': {
      // One-shot cleanup of polluted reasoning_library cases (correction
      // acknowledgements, feedback exchanges, stale investigation results).
      // Pass `dry` as the arg to preview without deleting.
      // Routes through local server.mjs proxy (added 2026-04-09) so the
      // listener doesn't need ARIA_API_TOKEN in its own process env.
      const dryRun = (a || '').trim().toLowerCase() === 'dry';
      try {
        const d = await post('/api/aria/admin/purge-cases', { dry_run: dryRun });
        if (d.error) return `⚠️ Purge failed: ${d.error}`;
        let msg = `🧹 *Case library ${dryRun ? 'dry-run' : 'purge'}*\n`;
        msg += `Scanned: ${d.scanned}\n`;
        msg += `${dryRun ? 'Would remove' : 'Removed'}: *${d.removed}*\n`;
        if (typeof d.remaining === 'number') msg += `Remaining: ${d.remaining}\n`;
        if (d.removed_samples && d.removed_samples.length) {
          msg += `\n*Sample removed (max 10):*\n`;
          d.removed_samples.slice(0, 10).forEach((s, i) => {
            msg += `${i + 1}. [${s.reason}] ${s.question_preview || '(no preview)'}\n`;
          });
        }
        if (dryRun) msg += `\n_Run \`/purgecases\` (no arg) to actually delete._`;
        return msg;
      } catch (e) {
        return `⚠️ Purge error: ${e.message}`;
      }
    }
    case 'inject': {
      // Seed ARIA's knowledge base + RAG store with curated knowledge modules.
      // Currently supports: 'regional' (regional navigation intelligence).
      // 2026-04-12: added for regional_navigation.py integration.
      const module = (a || '').trim().toLowerCase();
      if (!module) return 'Usage: /inject [module]\n\nAvailable:\n  *regional* — 9-region BD navigation intelligence (85K chars)';
      if (module === 'regional') {
        try {
          const d = await post('/api/aria/knowledge/inject-regional', {});
          if (d.error) return `⚠️ Injection failed: ${d.error}`;
          return `✅ *Regional navigation injected*\n` +
            `Sections: ${d.sections || '?'}\n` +
            `KB facts: ${d.knowledge_base?.facts_stored || 0}\n` +
            `RAG chunks: ${d.rag_store?.total_chunks || 0}`;
        } catch (e) {
          return `⚠️ Injection error: ${e.message}`;
        }
      }
      return `⚠️ Unknown module: ${module}. Available: regional`;
    }
    case 'nsn': {
      // Decode a NATO Stock Number — 2026-04-12
      if (!a) return 'Usage: /nsn [NATO stock number]\n\nExamples:\n  /nsn 5820-01-234-5678\n  /nsn 1305-21-900-1234\n\nDecodes FSC group, codifying nation, and NIIN.';
      try {
        const d = await get(`/api/aria/nsn/decode/${encodeURIComponent(a.trim())}`);
        if (!d.valid) return `⚠️ ${d.error || 'Invalid NSN format'}`;
        let msg = `🔢 *NSN: ${d.nsn}*\n\n`;
        msg += `*Category:* ${d.fsc_description} (FSC ${d.fsc_class})\n`;
        msg += `*Codified by:* ${d.codifying_nation} (NCC ${d.ncc})\n`;
        msg += `*NIIN:* ${d.niin_formatted}\n\n`;
        msg += `_${d.explanation}_`;
        return msg;
      } catch (e) {
        return `⚠️ NSN decode failed: ${e.message}`;
      }
    }
    case 'pmesii': {
      // Forces the PMESII country-assessment template even on otherwise-
      // ambiguous prompts. Routes through the normal chat path so the user
      // gets the full intel layers + RAG citations + verification.
      if (!a) return 'Usage: /pmesii [country]   — structured P/M/E/S/I/I assessment';
      const country = a.replace(/@\d{6,}/g, '').replace(/\s+/g, ' ').trim().slice(0, 80);
      if (!country) return 'Usage: /pmesii [country]';
      // Phrase it so the Python-side detector matches with high confidence.
      return _unwrapAriaReply(await askARIA(`PMESII country assessment: ${country}`, '', senderName));
    }
    // R-F3653: a SECOND `case 'investigate'` sat here and was unreachable —
    // JS switch takes the first match, which is the alias group further up
    // (/investigate /investigation /dd /duediligence /due-diligence /background
    // /profile). Its unique input sanitisation has been moved into that live
    // handler; the dead duplicate is removed so the next reader is not misled
    // into thinking this is what /investigate does.
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
        const resp = await _ariaFetch(`${ariaServiceUrl}/api/aria/rag/search`, {
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
    case 'honesty': {
      // /honesty             → rolling honesty score + counts
      // /honesty bad         → recent suspicious judgments
      // /honesty jdg_<id>    → full record with per-claim verdicts
      const ariaServiceUrl = process.env.ARIA_SERVICE_URL;
      if (!ariaServiceUrl) return '⚠️ ARIA_SERVICE_URL not configured.';
      const arg = (a || '').trim().toLowerCase();
      try {
        if (arg.startsWith('jdg_')) {
          const r = await _ariaFetch(`${ariaServiceUrl}/api/aria/honesty/${encodeURIComponent(arg)}`, {
            signal: AbortSignal.timeout(10000),
          });
          if (r.status === 404) return `⚠️ Judgment \`${arg}\` not found.`;
          if (!r.ok) return `⚠️ Judgment lookup failed: ${r.status}`;
          const j = await r.json();
          const ago = j.ts ? `${Math.round((Date.now() / 1000 - j.ts) / 60)}m ago` : '';
          const score = j.honesty_score === null || j.honesty_score === undefined
            ? '—' : `${(j.honesty_score * 100).toFixed(0)}%`;
          let msg = `🧭 *Honesty judgment ${j.id}*\n\n`;
          msg += `*Status:* ${j.status}\n`;
          msg += `*Score:* ${score}\n`;
          msg += `*Claims:* ${(j.claims || []).length} total · ${j.supported_count || 0} supported\n`;
          msg += `*When:* ${ago}\n`;
          if (j.trace_id) msg += `*Trace:* ${j.trace_id}\n`;
          if (j.question_preview) msg += `\n*Question:*\n${j.question_preview}\n`;
          if (j.error) msg += `\n⚠️ *Error:* ${j.error}\n`;
          if ((j.verdicts || []).length) {
            msg += `\n*Per-claim verdicts:*\n`;
            const claims = j.claims || [];
            j.verdicts.slice(0, 10).forEach(v => {
              const icon = v.supported ? '✅' : '❌';
              const claim = claims[v.claim_index - 1] || `(claim ${v.claim_index})`;
              msg += `${icon} ${claim.slice(0, 120)}\n`;
              if (v.reason) msg += `   _${v.reason.slice(0, 140)}_\n`;
            });
          }
          return msg;
        }

        if (arg === 'bad' || arg === 'fails' || arg === 'suspicious') {
          const r = await _ariaFetch(`${ariaServiceUrl}/api/aria/honesty/list?limit=15&bad_only=true`, {
            signal: AbortSignal.timeout(10000),
          });
          if (!r.ok) return `⚠️ Bad list failed: ${r.status}`;
          const d = await r.json();
          const items = d.judgments || [];
          if (!items.length) return `✅ No suspicious honesty judgments — confidence tags look honest.`;
          let msg = `🚨 *Suspicious honesty* (${items.length})\n_(score < 70%)_\n\n`;
          items.forEach((it, i) => {
            const ago = it.ts ? `${Math.round((Date.now() / 1000 - it.ts) / 60)}m ago` : '';
            const score = it.honesty_score === null || it.honesty_score === undefined
              ? '—' : `${(it.honesty_score * 100).toFixed(0)}%`;
            msg += `${i + 1}. *${it.id}* — ${score} (${it.supported_count}/${it.claims_total}) · _${ago}_\n`;
            if (it.question_preview) msg += `   Q: ${it.question_preview.slice(0, 100)}\n\n`;
          });
          msg += `_Use */honesty jdg_<id>* for per-claim verdicts._`;
          return msg;
        }

        // Default: stats
        const r = await _ariaFetch(`${ariaServiceUrl}/api/aria/honesty/stats`, {
          signal: AbortSignal.timeout(10000),
        });
        if (!r.ok) return `⚠️ Honesty stats failed: ${r.status}`;
        const s = await r.json();
        if (s.error) return `⚠️ Honesty error: ${s.error}`;
        if (!s.total) return `📋 No honesty judgments yet — confidence tags get judged automatically when ARIA marks claims with [CONFIRMED] and a tool fetched sources.`;
        const score = s.rolling_honesty_score === null || s.rolling_honesty_score === undefined
          ? '—' : `${(s.rolling_honesty_score * 100).toFixed(0)}%`;
        let msg = `🧭 *Confidence-tag honesty*\n\n`;
        msg += `*Rolling score:* ${score} _(n=${s.scored_sample_size || 0})_\n`;
        msg += `*Total judged:* ${s.total}\n`;
        msg += `*Last 24h:* ${s.recent_24h || 0}\n`;
        msg += `*Suspicious (<70%):* ${s.suspicious_count || 0}\n\n`;
        const bs = s.by_status || {};
        msg += `✅ ok: ${bs.ok || 0}\n`;
        msg += `• no_claims: ${bs.no_claims || 0}\n`;
        msg += `• no_source: ${bs.no_source || 0}\n`;
        msg += `❌ judge_failed: ${bs.judge_failed || 0}\n\n`;
        msg += `_/honesty bad · /honesty jdg_<id>_`;
        return msg;
      } catch (e) {
        return `⚠️ Honesty error: ${e.message}`;
      }
    }

    case 'trace': {
      // /trace             → stats summary
      // /trace recent      → last 15 trace summaries
      // /trace bad         → traces that errored / ungrounded / 👎'd
      // /trace tr_<id>     → full lifecycle of one ARIA reply
      const ariaServiceUrl = process.env.ARIA_SERVICE_URL;
      if (!ariaServiceUrl) return '⚠️ ARIA_SERVICE_URL not configured.';
      const arg = (a || '').trim().toLowerCase();
      try {
        if (arg.startsWith('tr_')) {
          const r = await _ariaFetch(`${ariaServiceUrl}/api/aria/trace/${encodeURIComponent(arg)}`, {
            signal: AbortSignal.timeout(10000),
          });
          if (r.status === 404) return `⚠️ Trace \`${arg}\` not found (30-day TTL).`;
          if (!r.ok) return `⚠️ Trace lookup failed: ${r.status}`;
          const t = await r.json();
          const ago = t.ts_start ? `${Math.round((Date.now() / 1000 - t.ts_start) / 60)}m ago` : '';
          let msg = `🔬 *Trace ${t.id}*\n\n`;
          msg += `*Status:* ${t.status} · ${ago}\n`;
          msg += `*Latency:* ${t.latency_ms}ms\n`;
          msg += `*Cost:* $${(t.cost_total_usd || 0).toFixed(6)} (${t.tokens_total || 0} tokens, ${(t.llm_calls || []).length} calls)\n`;
          if (t.tool_used) msg += `*Tool:* ${t.tool_used}\n`;

          if (t.question) msg += `\n*Question:*\n${t.question.slice(0, 600)}\n`;
          if (t.response) msg += `\n*Response:*\n${t.response.slice(0, 800)}\n`;

          if (t.verification) {
            msg += `\n*Verification:* ${t.verification.verdict}`;
            if (t.verification.grounded_rate !== null && t.verification.grounded_rate !== undefined) {
              msg += ` (${(t.verification.grounded_rate * 100).toFixed(0)}% grounded)`;
            }
            msg += `\n  cited: ${t.verification.cited} · unverified: ${t.verification.unverified}\n`;
          }
          if (t.judgment) {
            const hs = t.judgment.honesty_score === null || t.judgment.honesty_score === undefined
              ? '—' : `${(t.judgment.honesty_score * 100).toFixed(0)}%`;
            msg += `\n*Honesty:* ${hs}`;
            if (t.judgment.claims_total) {
              msg += ` (${t.judgment.supported_count}/${t.judgment.claims_total} [CONFIRMED] supported)`;
            }
            msg += `\n`;
          }
          if (t.feedback) {
            msg += `\n*Feedback:* ${t.feedback.emoji || ''} ${t.feedback.sentiment} _(${t.feedback.reactor || '?'})_\n`;
          }
          if ((t.llm_calls || []).length) {
            msg += `\n*LLM calls:*\n`;
            t.llm_calls.slice(0, 8).forEach((c, i) => {
              const ok = c.success ? '✅' : '❌';
              msg += `  ${i + 1}. ${ok} ${c.feature} · ${c.total_tokens}tk · $${(c.cost_usd || 0).toFixed(6)} · ${c.latency_ms}ms\n`;
            });
          }
          if (t.error) msg += `\n⚠️ *Error:* ${t.error.slice(0, 300)}\n`;
          return msg;
        }

        if (arg === 'recent' || arg === 'list') {
          const r = await _ariaFetch(`${ariaServiceUrl}/api/aria/trace/recent?limit=15`, {
            signal: AbortSignal.timeout(10000),
          });
          if (!r.ok) return `⚠️ Recent failed: ${r.status}`;
          const d = await r.json();
          const traces = d.traces || [];
          if (!traces.length) return `📋 No traces yet.`;
          let msg = `🔬 *Recent traces* (${traces.length})\n\n`;
          traces.forEach((t, i) => {
            const ago = t.ts_start ? `${Math.round((Date.now() / 1000 - t.ts_start) / 60)}m ago` : '';
            const fb = t.feedback_sentiment === 'positive' ? '👍' : t.feedback_sentiment === 'negative' ? '👎' : '';
            const v = t.verdict === 'ungrounded' ? '⚠️' : t.verdict === 'partial' ? '🟡' : '';
            msg += `${i + 1}. ${fb}${v} *${t.id}* — _${ago}_\n`;
            msg += `   $${(t.cost_total_usd || 0).toFixed(5)} · ${t.calls}c · ${t.latency_ms}ms\n`;
            if (t.question_preview) msg += `   Q: ${t.question_preview.slice(0, 100)}\n\n`;
          });
          return msg;
        }

        if (arg === 'bad' || arg === 'fails') {
          const r = await _ariaFetch(`${ariaServiceUrl}/api/aria/trace/recent?limit=15&bad_only=true`, {
            signal: AbortSignal.timeout(10000),
          });
          if (!r.ok) return `⚠️ Bad list failed: ${r.status}`;
          const d = await r.json();
          const traces = d.traces || [];
          if (!traces.length) return `✅ No bad traces — everything looks healthy.`;
          let msg = `🚨 *Bad traces* (${traces.length})\n_(errored / ungrounded / 👎)_\n\n`;
          traces.forEach((t, i) => {
            const ago = t.ts_start ? `${Math.round((Date.now() / 1000 - t.ts_start) / 60)}m ago` : '';
            const reason = t.status !== 'ok' ? `❌${t.status}` :
                           t.verdict === 'ungrounded' ? '⚠️ungrounded' :
                           t.feedback_sentiment === 'negative' ? '👎' : '?';
            msg += `${i + 1}. ${reason} *${t.id}* · _${ago}_\n`;
            if (t.question_preview) msg += `   Q: ${t.question_preview.slice(0, 100)}\n\n`;
          });
          msg += `_Use */trace tr_<id>* for full lifecycle._`;
          return msg;
        }

        // Default: stats
        const r = await _ariaFetch(`${ariaServiceUrl}/api/aria/trace/stats`, {
          signal: AbortSignal.timeout(10000),
        });
        if (!r.ok) return `⚠️ Trace stats failed: ${r.status}`;
        const s = await r.json();
        if (s.error) return `⚠️ Trace stats error: ${s.error}`;
        if (!s.total) return `📋 No traces recorded yet — ask ARIA something to populate.`;
        let msg = `🔬 *Trace stats*\n\n`;
        msg += `*Total:* ${s.total}\n`;
        msg += `*Healthy:* ${s.ok} (${((s.ok / s.total) * 100).toFixed(0)}%)\n`;
        msg += `*Bad:* ${s.bad}\n\n`;
        msg += `*Mean cost:* $${(s.mean_cost_usd || 0).toFixed(6)}\n`;
        msg += `*Mean tokens:* ${s.mean_tokens}\n`;
        msg += `*Mean latency:* ${s.mean_latency_ms}ms\n`;
        msg += `*Total spend (window):* $${(s.total_cost_usd || 0).toFixed(4)}\n\n`;
        msg += `_/trace recent · /trace bad · /trace tr_<id>_`;
        return msg;
      } catch (e) {
        return `⚠️ Trace error: ${e.message}`;
      }
    }

    case 'cost': {
      // /cost                → 24h summary with by-feature breakdown
      // /cost 1h | 6h | 7d   → custom window
      // /cost recent         → last 15 calls
      // /cost cumulative     → all-time per-feature totals
      // /cost call_<id>      → full record for one call
      const ariaServiceUrl = process.env.ARIA_SERVICE_URL;
      if (!ariaServiceUrl) return '⚠️ ARIA_SERVICE_URL not configured.';
      const arg = (a || '').trim().toLowerCase();
      try {
        if (arg.startsWith('call_')) {
          const r = await _ariaFetch(`${ariaServiceUrl}/api/aria/cost/call/${encodeURIComponent(arg)}`, {
            signal: AbortSignal.timeout(10000),
          });
          if (r.status === 404) return `⚠️ Call \`${arg}\` not found.`;
          if (!r.ok) return `⚠️ Call lookup failed: ${r.status}`;
          const c = await r.json();
          const ago = c.ts ? `${Math.round((Date.now() / 1000 - c.ts) / 60)}m ago` : '';
          let msg = `💰 *Call ${c.id}*\n\n`;
          msg += `*Model:* ${c.model}\n`;
          msg += `*Provider:* ${c.provider || '—'}\n`;
          msg += `*Feature:* ${c.feature}\n`;
          msg += `*Tokens:* ${c.input_tokens} in / ${c.output_tokens} out (${c.total_tokens} total)\n`;
          msg += `*Cost:* $${c.cost_usd.toFixed(6)}\n`;
          msg += `*Latency:* ${c.latency_ms}ms\n`;
          msg += `*Status:* ${c.success ? '✅' : '❌ ' + (c.error || '')}\n`;
          msg += `*When:* ${ago}\n`;
          return msg;
        }

        if (arg === 'recent') {
          const r = await _ariaFetch(`${ariaServiceUrl}/api/aria/cost/recent?limit=15`, {
            signal: AbortSignal.timeout(10000),
          });
          if (!r.ok) return `⚠️ Recent failed: ${r.status}`;
          const d = await r.json();
          const calls = d.calls || [];
          if (!calls.length) return `📋 No LLM calls recorded yet.`;
          let msg = `💰 *Recent LLM calls* (${calls.length})\n\n`;
          calls.forEach((c, i) => {
            const ago = c.ts ? `${Math.round((Date.now() / 1000 - c.ts) / 60)}m ago` : '';
            const ok = c.success ? '✅' : '❌';
            msg += `${i + 1}. ${ok} *${c.feature}* — ${c.total_tokens}tk · $${c.cost_usd.toFixed(5)} · _${ago}_\n`;
            msg += `   ${c.model || '—'} · ${c.id}\n\n`;
          });
          return msg;
        }

        if (arg === 'cumulative' || arg === 'total' || arg === 'lifetime') {
          const r = await _ariaFetch(`${ariaServiceUrl}/api/aria/cost/cumulative`, {
            signal: AbortSignal.timeout(10000),
          });
          if (!r.ok) return `⚠️ Cumulative failed: ${r.status}`;
          const agg = await r.json();
          const features = Object.entries(agg).sort((a, b) => (b[1].cost_usd || 0) - (a[1].cost_usd || 0));
          if (!features.length) return `📋 No cost data accumulated yet.`;
          let msg = `💰 *Cumulative cost by feature*\n\n`;
          let totalCost = 0, totalCalls = 0, totalTokens = 0;
          features.forEach(([name, f]) => {
            totalCost += f.cost_usd || 0;
            totalCalls += f.calls || 0;
            totalTokens += f.total_tokens || 0;
          });
          msg += `*Total:* $${totalCost.toFixed(4)} · ${totalCalls} calls · ${(totalTokens / 1000).toFixed(1)}k tokens\n\n`;
          features.forEach(([name, f]) => {
            const pct = totalCost > 0 ? ((f.cost_usd / totalCost) * 100).toFixed(0) : 0;
            msg += `*${name}* — $${f.cost_usd.toFixed(4)} (${pct}%)\n`;
            msg += `  ${f.calls} calls · ${(f.total_tokens / 1000).toFixed(1)}k tokens`;
            if (f.errors) msg += ` · ❌ ${f.errors} errors`;
            msg += `\n\n`;
          });
          return msg;
        }

        // Default: windowed summary. Parse window arg like "1h", "6h", "7d"
        let windowHours = 24;
        const m = arg.match(/^(\d+)\s*([hd])?$/);
        if (m) {
          const n = parseInt(m[1], 10);
          windowHours = m[2] === 'd' ? n * 24 : n;
        }
        const r = await _ariaFetch(`${ariaServiceUrl}/api/aria/cost/summary?window_hours=${windowHours}`, {
          signal: AbortSignal.timeout(10000),
        });
        if (!r.ok) return `⚠️ Cost summary failed: ${r.status}`;
        const s = await r.json();
        if (s.error) return `⚠️ Cost summary error: ${s.error}`;
        let msg = `💰 *Cost — last ${windowHours}h*\n\n`;
        msg += `*Calls:* ${s.total_calls || 0}\n`;
        msg += `*Tokens:* ${((s.total_tokens || 0) / 1000).toFixed(1)}k\n`;
        msg += `*Spend:* $${(s.total_cost_usd || 0).toFixed(4)}\n`;
        msg += `*Projected monthly:* $${(s.projected_monthly_usd || 0).toFixed(2)}\n\n`;

        const features = Object.entries(s.by_feature || {}).sort((a, b) => (b[1].cost_usd || 0) - (a[1].cost_usd || 0));
        if (features.length) {
          msg += `*By feature:*\n`;
          features.slice(0, 8).forEach(([name, f]) => {
            const pct = s.total_cost_usd > 0 ? ((f.cost_usd / s.total_cost_usd) * 100).toFixed(0) : 0;
            msg += `  ${name}: $${f.cost_usd.toFixed(4)} (${pct}%) · ${f.calls} calls · ${(f.tokens / 1000).toFixed(1)}k tk\n`;
          });
        }
        const models = Object.entries(s.by_model || {}).sort((a, b) => (b[1].cost_usd || 0) - (a[1].cost_usd || 0));
        if (models.length > 1) {
          msg += `\n*By model:*\n`;
          models.slice(0, 5).forEach(([name, m]) => {
            msg += `  ${name}: $${m.cost_usd.toFixed(4)} · ${m.calls} calls\n`;
          });
        }
        msg += `\n_/cost recent · /cost cumulative · /cost 7d_`;
        return msg;
      } catch (e) {
        return `⚠️ Cost error: ${e.message}`;
      }
    }

    case 'verify': {
      // /verify              → summary stats (rolling grounded rate + counts)
      // /verify ungrounded   → list recent ungrounded responses
      // /verify partial      → list recent partial responses
      // /verify vfy_<id>     → full record with actual URLs cited vs fetched
      const ariaServiceUrl = process.env.ARIA_SERVICE_URL;
      if (!ariaServiceUrl) return '⚠️ ARIA_SERVICE_URL not configured.';
      const arg = (a || '').trim();
      try {
        if (arg.startsWith('vfy_')) {
          const r = await _ariaFetch(`${ariaServiceUrl}/api/aria/verify/${encodeURIComponent(arg)}`, {
            signal: AbortSignal.timeout(10000),
          });
          if (r.status === 404) return `⚠️ Verification \`${arg}\` not found.`;
          if (!r.ok) return `⚠️ Verification lookup failed: ${r.status}`;
          const v = await r.json();
          const rate = v.grounded_rate === null || v.grounded_rate === undefined
            ? '—' : `${(v.grounded_rate * 100).toFixed(0)}%`;
          let msg = `🔗 *Verification ${v.id}*\n\n`;
          msg += `*Verdict:* ${v.verdict}\n`;
          msg += `*Grounded rate:* ${rate}\n`;
          msg += `*Tool used:* ${v.tool_used || '—'}\n`;
          if (v.question_preview) msg += `\n*Question:*\n${v.question_preview}\n`;
          if (v.cited_urls && v.cited_urls.length) {
            msg += `\n*Cited (${v.cited_urls.length}):*\n`;
            v.cited_urls.slice(0, 8).forEach(u => msg += `  • ${u.slice(0, 100)}\n`);
          }
          if (v.unverified && v.unverified.length) {
            msg += `\n⚠️ *Unverified (${v.unverified.length}):*\n`;
            v.unverified.slice(0, 8).forEach(u => msg += `  • ${u.slice(0, 100)}\n`);
          }
          if (v.fetched_urls && v.fetched_urls.length) {
            msg += `\n*Tool fetched (${v.fetched_urls.length}):*\n`;
            v.fetched_urls.slice(0, 5).forEach(u => msg += `  • ${u.slice(0, 100)}\n`);
          }
          return msg;
        }

        const validVerdicts = ['grounded', 'partial', 'ungrounded', 'no_citations', 'no_tool'];
        if (validVerdicts.includes(arg)) {
          const r = await _ariaFetch(`${ariaServiceUrl}/api/aria/verify/list?limit=10&verdict=${encodeURIComponent(arg)}`, {
            signal: AbortSignal.timeout(10000),
          });
          if (!r.ok) return `⚠️ Verify list failed: ${r.status}`;
          const d = await r.json();
          const items = d.verifications || [];
          if (!items.length) return `📋 No ${arg} verifications.`;
          let msg = `📋 *${arg}* verifications (${items.length})\n\n`;
          items.forEach((it, i) => {
            const rate = it.grounded_rate === null || it.grounded_rate === undefined
              ? '—' : `${(it.grounded_rate * 100).toFixed(0)}%`;
            const ago = it.ts ? `${Math.round((Date.now() / 1000 - it.ts) / 60)}m ago` : '';
            msg += `${i + 1}. *${it.id}* — ${rate} · _${ago}_\n`;
            msg += `   ${it.cited_count} cited · ${it.unverified_count} unverified\n`;
            if (it.question_preview) msg += `   Q: ${it.question_preview.slice(0, 100)}\n`;
            msg += `\n`;
          });
          msg += `_Use */verify <id>* for full URL lists._`;
          return msg;
        }

        // Default: stats
        const r = await _ariaFetch(`${ariaServiceUrl}/api/aria/verify/stats`, {
          signal: AbortSignal.timeout(10000),
        });
        if (!r.ok) return `⚠️ Verify stats failed: ${r.status}`;
        const s = await r.json();
        if (s.error) return `⚠️ Verify stats error: ${s.error}`;
        const bv = s.by_verdict || {};
        const rate = s.rolling_grounded_rate === null || s.rolling_grounded_rate === undefined
          ? '—' : `${(s.rolling_grounded_rate * 100).toFixed(0)}%`;
        let msg = `🔗 *Citation Verification*\n\n`;
        msg += `*Rolling grounded rate:* ${rate} _(n=${s.rate_sample_size || 0})_\n`;
        msg += `*Total checks:* ${s.total || 0}\n`;
        msg += `*Last 24h:* ${s.recent_24h || 0}\n\n`;
        msg += `✅ grounded: ${bv.grounded || 0}\n`;
        msg += `🟡 partial: ${bv.partial || 0}\n`;
        msg += `❌ ungrounded: ${bv.ungrounded || 0}\n`;
        msg += `• no_citations: ${bv.no_citations || 0}\n`;
        msg += `• no_tool: ${bv.no_tool || 0}\n\n`;
        msg += `_Use */verify ungrounded* to see suspect responses._`;
        return msg;
      } catch (e) {
        return `⚠️ Verify error: ${e.message}`;
      }
    }

    case 'eval': {
      // /eval               → run the golden set, report summary
      // /eval list          → show golden entries
      // /eval runs          → show recent run history
      // /eval add fb_<id>   → promote a feedback record to a golden entry
      // /eval rm gold_<id>  → remove a golden entry
      const ariaServiceUrl = process.env.ARIA_SERVICE_URL;
      if (!ariaServiceUrl) return '⚠️ ARIA_SERVICE_URL not configured.';
      const arg = (a || '').trim();
      const [sub, ...restArr] = arg.split(/\s+/);
      const rest = restArr.join(' ');
      try {
        // Default: run the eval
        if (!arg || sub === 'run') {
          const r = await _ariaFetch(`${ariaServiceUrl}/api/aria/eval/run`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ label: `wa:${senderName}` }),
            // Eval can take a while — 15 entries × ~10s each = ~3 min
            signal: AbortSignal.timeout(360000),
          });
          if (!r.ok) {
            const body = await r.text().catch(() => '');
            return `⚠️ Eval run failed: ${r.status}${body ? ' — ' + body.slice(0, 200) : ''}`;
          }
          const run = await r.json();
          if (run.ok === false) return `⚠️ Eval cannot run: ${run.reason}`;
          const s = run.summary || {};
          const d = run.delta;
          let msg = `🧪 *Eval run ${run.id}*\n\n`;
          msg += `*Pass rate:* ${(s.pass_rate * 100).toFixed(0)}% (${s.pass}/${s.total})\n`;
          msg += `*Mean score:* ${s.mean_score}\n`;
          msg += `✅ pass: ${s.pass}  ⚠️ warn: ${s.warn}  ❌ fail: ${s.fail}\n`;
          msg += `_${Math.round((s.duration_ms || 0) / 1000)}s_\n`;
          if (d) {
            const sign = (n) => n > 0 ? `+${n}` : `${n}`;
            const dir = d.pass_rate_delta > 0 ? '📈' : d.pass_rate_delta < 0 ? '📉' : '➡️';
            msg += `\n*vs previous run:* ${dir}\n`;
            msg += `  pass_rate: ${sign((d.pass_rate_delta * 100).toFixed(1))}%\n`;
            msg += `  mean_score: ${sign(d.mean_score_delta)}\n`;
            msg += `  fails: ${sign(d.fail_delta)}\n`;
            if (d.fail_delta > 0) msg += `\n⚠️ *REGRESSION* — ${d.fail_delta} new failure(s)\n`;
          }
          // Surface the actual fails so the team can act
          const fails = (run.results || []).filter(r => r.bucket === 'fail').slice(0, 5);
          if (fails.length) {
            msg += `\n*Top failures:*\n`;
            fails.forEach((f, i) => {
              msg += `${i + 1}. [${f.score}] ${f.question_preview}\n`;
            });
          }
          return msg;
        }

        if (sub === 'list' || sub === 'golden') {
          const r = await _ariaFetch(`${ariaServiceUrl}/api/aria/eval/golden`, {
            signal: AbortSignal.timeout(10000),
          });
          if (!r.ok) return `⚠️ Golden list failed: ${r.status}`;
          const d = await r.json();
          const items = d.entries || [];
          if (!items.length) return `📋 No golden entries yet. Use */eval add fb_<id>* to promote a 👍-rated answer.`;
          let msg = `📋 *Golden Q&A set* (${d.total})\n\n`;
          items.slice(0, 15).forEach((e, i) => {
            msg += `${i + 1}. *${e.id}* _(${e.category})_\n`;
            msg += `   Q: ${(e.question || '').slice(0, 100)}\n\n`;
          });
          if (items.length > 15) msg += `_+ ${items.length - 15} more — see /api/aria/eval/golden_`;
          return msg;
        }

        if (sub === 'runs' || sub === 'history') {
          const r = await _ariaFetch(`${ariaServiceUrl}/api/aria/eval/runs?limit=10`, {
            signal: AbortSignal.timeout(10000),
          });
          if (!r.ok) return `⚠️ Runs list failed: ${r.status}`;
          const d = await r.json();
          const runs = d.runs || [];
          if (!runs.length) return `📋 No eval runs yet. Use */eval* to start one.`;
          let msg = `📋 *Recent eval runs* (${runs.length})\n\n`;
          runs.forEach((r, i) => {
            const s = r.summary || {};
            const ago = r.started_at ? `${Math.round((Date.now() / 1000 - r.started_at) / 60)}m ago` : '';
            msg += `${i + 1}. *${r.id}* — ${(s.pass_rate * 100).toFixed(0)}% pass · _${ago}_\n`;
          });
          return msg;
        }

        if (sub === 'add' || sub === 'promote') {
          if (!rest.startsWith('fb_')) {
            return `Usage: */eval add fb_<feedback_id>*\n\nUse */feedback* to find a 👍-rated reply to promote.`;
          }
          const r = await _ariaFetch(`${ariaServiceUrl}/api/aria/eval/golden/promote`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ feedback_id: rest, added_by: senderName }),
            signal: AbortSignal.timeout(10000),
          });
          if (!r.ok) return `⚠️ Promote failed: ${r.status}`;
          const result = await r.json();
          if (!result.ok) return `⚠️ Cannot promote: ${result.reason}`;
          return `✅ Promoted feedback ${rest} → golden entry *${result.entry.id}*\nGolden set now has ${result.total} entries.`;
        }

        if (sub === 'rm' || sub === 'remove' || sub === 'delete') {
          if (!rest.startsWith('gold_')) {
            return `Usage: */eval rm gold_<id>*`;
          }
          const r = await _ariaFetch(`${ariaServiceUrl}/api/aria/eval/golden/${encodeURIComponent(rest)}`, {
            method: 'DELETE',
            signal: AbortSignal.timeout(10000),
          });
          if (!r.ok) return `⚠️ Delete failed: ${r.status}`;
          const result = await r.json();
          if (!result.ok) return `⚠️ Cannot remove: ${result.reason}`;
          return `🗑 Removed ${rest}. ${result.remaining} entries remain.`;
        }

        return `Unknown /eval subcommand: ${sub}\n\nUsage:\n  */eval* — run the golden set\n  */eval list* — show entries\n  */eval runs* — run history\n  */eval add fb_<id>* — promote feedback\n  */eval rm gold_<id>* — remove entry`;
      } catch (e) {
        return `⚠️ Eval error: ${e.message}`;
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
          const r = await _ariaFetch(`${ariaServiceUrl}/api/aria/feedback/${encodeURIComponent(mode)}`, {
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
          const r = await _ariaFetch(`${ariaServiceUrl}/api/aria/feedback/stats`, {
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
        const r = await _ariaFetch(url, { signal: AbortSignal.timeout(10000) });
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
        const resp = await _ariaFetch(url, { signal: AbortSignal.timeout(15000) });
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
        const resp = await _ariaFetch(`${ariaServiceUrl}/api/aria/research/task/${encodeURIComponent(id)}`, {
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
        const resp = await _ariaFetch(`${ariaServiceUrl}/api/aria/rag/sources?limit=30`, {
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
        '/pmesii [country] — Structured P/M/E/S/I/I country assessment',
        '/report [dd|compliance|market] [subject] — Branded draft',
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
        '/forget — Wipe your conversation history (start fresh)',
        '/purgecases [dry] — Clean polluted entries from the case library',
        '/feedback +/- [notes] — Rate response',
        '',
        '*Business Development*',
        '/leads — Latest leads',
        '/ideas — Strategic ideas',
        '/hunt — Trigger lead search',
        '',
        '*Tickets*',
        '/ticket [description] — File a dev ticket (GitHub)',
        '/tickets — List open ARIA-raised tickets',
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
// R-F60: latest QR string captured from connection.update so the
// /api/wa-listener/qr.png endpoint can render it for browser scanning.
// Cleared when connection opens or the listener resets.
let latestQrString = null;
let latestQrAt     = 0;
// R-F64: stall detector for QR-pending state. Baileys sometimes emits
// a single QR and never rotates — the operator's window of ~20s
// expires and they can't scan. We watch QR age and auto-restart the
// socket when it stalls. setInterval handle held in module scope so
// startListener can clear it on socket re-init.
let qrStallTimer   = null;
let qrStallRestarts = 0;  // surfaced in /status for diagnosis
let suppressAuthBackup = false;
let authInvalidatedAt  = 0;  // timestamp of last 440/logout — skip restore if recent
let messagesHeard  = 0;
let messagesSent   = 0;
let startedAt      = null;
let reconnectDelay = 5000;
// Watchdog state — detects zombie Baileys sockets (TCP alive, protocol dead).
// Past incidents 2026-04-20 and 2026-04-21: post-restart the WS stays up but
// stops delivering inbound events; `connection.update` never fires 'close',
// so the existing reconnect logic never runs and ARIA silently goes deaf.
// `lastInboundAt` is bumped on every messages.upsert firing regardless of
// filtering, so a chatty group proves the socket is alive without a probe.
let lastInboundAt      = 0;
let watchdogTimer      = null;
let watchdogReconnecting = false;
let watchdogLastProbeAt = 0;
let watchdogLastProbeOk = null;   // null = never probed, true/false = last outcome
// F109 (2026-04-30): inbound-blindspot — `groupFetchAllParticipating` round-trips
// over the same WS that delivers messages, so it succeeds whenever the outbound
// half of the socket works. Observed today: 20+ min of silent inbound while
// every probe returned ok and bumped `lastInboundAt`, masking the dead stream.
// Mitigation: count probe-only keepalives; force reconnect after N in a row
// with no peer messages between them.
let consecutiveProbesWithoutPeer = 0;
const groupNames   = new Map();
const messageStore = [];
const MAX_STORE    = 500;

function store(groupId, groupName, sender, senderName, text, ts) {
  messageStore.push({ groupId, groupName, sender, senderName, text, ts });
  if (messageStore.length > MAX_STORE) messageStore.shift();

  // Track team interaction for engagement analytics (fire-and-forget)
  const ariaUrl = process.env.ARIA_SERVICE_URL;
  if (ariaUrl && senderName) {
    const ariaToken = process.env.ARIA_API_TOKEN || '';
    const h = { 'Content-Type': 'application/json' };
    if (ariaToken) h['Authorization'] = `Bearer ${ariaToken}`;
    fetch(`${ariaUrl}/api/aria/team/interaction`, {
      method: 'POST', headers: h,
      body: JSON.stringify({ sender_name: senderName, message_type: text.startsWith('/') ? 'command' : 'chat' }),
      signal: AbortSignal.timeout(3000),
    }).catch(() => {}); // fire-and-forget
  }
}

// ── Feed to brain via ARIA_SERVICE_URL ───────────────────────────────────────
async function feedToARIA(groupName, senderName, text, signalType = 'whatsapp_group_message', extra = {}) {
  const ariaUrl = process.env.ARIA_SERVICE_URL;
  if (!ariaUrl) {
    console.warn('[WA Listener] feedToARIA: ARIA_SERVICE_URL not set, skipping');
    // Wire failure to brain via self-diagnostic endpoint (best-effort)
    try {
      await fetch(`http://localhost:3117/api/aria/brain/signal`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          content: `feedToARIA skipped: ARIA_SERVICE_URL not set on aria-wa`,
          source: 'wa_listener:feedToARIA',
          signal_type: 'capability_gap',
          metadata: { group: groupName, sender: senderName, channel: 'whatsapp_listener' },
        }),
        signal: AbortSignal.timeout(2000),
      }).catch(() => {});
    } catch(_) { /* best-effort */ }
    return;
  }
  const ariaToken = process.env.ARIA_API_TOKEN || process.env.ARIA_INTERNAL_TOKEN || '';
  try {
    const res = await fetch(`${ariaUrl}/api/aria/brain/signal`, {
      method:  'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${ariaToken}`,
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
    if (!res.ok) {
      console.error(`[WA Listener] feedToARIA: brain signal returned ${res.status}`);
    }
  } catch(e) {
    console.error(`[WA Listener] feedToARIA: brain unreachable — ${e.message}`);
  }
}

// ── R-F3723 — document-extraction outcome must reach the brain ─────────────
// The three extractors (pdf-parse, mammoth, xlsx) load as dynamic imports and
// degrade to `null` when unavailable. Before this, every degraded branch ended
// at console.warn — which CLAUDE.md §21a defines as DARK, not wired. The
// consequence is specific: ARIA answers a "review this contract" question from
// the FILENAME while the brain records a normal document ingest, so §25
// proprioception cannot answer "did I actually read that file?" and the
// self-heal loop never sees a gap it could close.
//
// USABLE_TEXT_CHARS mirrors the `extractedText.length > 50` test at the
// `content:` assignment in the document handler. The two MUST agree: if the
// classifier and the caller disagree, the brain is told a different story than
// the user was given.
const USABLE_TEXT_CHARS = 50;

/**
 * Classify what actually happened to a document extraction. Pure and total —
 * it runs on a user-controlled path, so it never throws on malformed input.
 *
 * @returns {{outcome: 'extracted'|'metadata_only'|'module_missing',
 *            degraded: boolean, reason: string}}
 */
export function classifyExtractionOutcome(info) {
  const i = (info && typeof info === 'object') ? info : {};
  const fileType = typeof i.fileType === 'string' && i.fileType ? i.fileType : 'unknown';
  const chars = Number.isFinite(i.extractedChars) ? i.extractedChars : 0;

  if (i.moduleMissing) {
    return {
      outcome: 'module_missing',
      degraded: true,
      reason: `${fileType} extractor module unavailable — document reduced to metadata`,
    };
  }
  if (chars <= USABLE_TEXT_CHARS) {
    return {
      outcome: 'metadata_only',
      degraded: true,
      reason: `${fileType} extraction yielded ${chars} chars (<= ${USABLE_TEXT_CHARS}) — answered from filename only`,
    };
  }
  return {
    outcome: 'extracted',
    degraded: false,
    reason: `${fileType} extraction produced ${chars} chars`,
  };
}

/**
 * Report a degraded extraction to the brain. Best-effort and non-throwing:
 * an observability failure must never break document handling. Success is not
 * reported — only degradation, so this adds no traffic on the healthy path.
 */
async function reportExtractionOutcome({ fileName, fileType, moduleMissing, extractedChars, groupName, senderName }) {
  try {
    const verdict = classifyExtractionOutcome({ fileType, moduleMissing, extractedChars });
    if (!verdict.degraded) return verdict;

    const ariaUrl = process.env.ARIA_SERVICE_URL;
    if (!ariaUrl) return verdict;
    const ariaToken = process.env.ARIA_API_TOKEN || process.env.ARIA_INTERNAL_TOKEN || '';

    console.warn(`[WA] extraction degraded (${verdict.outcome}) for "${fileName}": ${verdict.reason}`);
    // signal_type 'capability_gap' is what routes this to capability_gaps, which
    // the coder reads on its scan cycle (R-F884/R-F887) — see errorTracker.mjs.
    await fetch(`${ariaUrl}/api/aria/brain/signal`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(ariaToken ? { 'Authorization': `Bearer ${ariaToken}` } : {}),
      },
      body: JSON.stringify({
        content: `WhatsApp document extraction degraded: ${verdict.reason} (file: ${fileName})`,
        source: 'wa_listener:document_extraction',
        signal_type: 'capability_gap',
        metadata: {
          file_name: fileName,
          file_type: fileType,
          outcome: verdict.outcome,
          extracted_chars: Number.isFinite(extractedChars) ? extractedChars : 0,
          module_missing: Boolean(moduleMissing),
          group: groupName,
          sender: senderName,
          channel: 'whatsapp_listener',
          timestamp: new Date().toISOString(),
        },
      }),
      signal: AbortSignal.timeout(5000),
    }).catch(() => {});
    return verdict;
  } catch (_) {
    return { outcome: 'metadata_only', degraded: true, reason: 'classification failed' };
  }
}

// ── Channel mirror — route messages to ARIA's new intelligence endpoints ───
// Runs silently, does NOT reply. Triggers when:
//   - chat is in ARIA_MIRROR_GROUPS → silent ingest to /api/aria/channel/ingest
//   - sender is in ARIA_COUNTERPARTY_CONTACTS → claim ledger + deception screen
// Brain URL is the Python FastAPI at ARIA_SERVICE_URL (fly.io).
async function mirrorChannel({
  chatId, senderJid, groupName, senderName, text, messageId, timestamp
}) {
  if (!text || text.length < ARIA_MIRROR_MIN_LEN) return;

  const ariaUrl = process.env.ARIA_SERVICE_URL;
  if (!ariaUrl) return;
  const ariaToken = process.env.ARIA_API_TOKEN || process.env.ARIA_INTERNAL_TOKEN || '';
  if (!ariaToken) return;

  const headers = {
    'Authorization': `Bearer ${ariaToken}`,
    'Content-Type': 'application/json',
    'X-Source': 'wa-channel-mirror',
  };

  const isInternalGroup = ARIA_MIRROR_GROUPS.has(chatId);
  const isCounterparty = ARIA_COUNTERPARTY_CONTACTS.has(senderJid)
    || ARIA_COUNTERPARTY_CONTACTS.has(chatId);

  if (!isInternalGroup && !isCounterparty) return;

  // 1. Silent intel ingest — always, for both paths.
  fetch(`${ariaUrl}/api/aria/channel/ingest`, {
    method: 'POST',
    headers,
    body: JSON.stringify({
      text,
      jid: chatId,
      sender_jid: senderJid,
      message_id: messageId,
      timestamp,
      source: isInternalGroup ? 'whatsapp_group_mirror' : 'whatsapp_counterparty',
      is_internal: isInternalGroup,
    }),
    signal: AbortSignal.timeout(5000),
  }).catch(() => {});

  // 2. Counterparty path: claim ledger + deception screen in parallel.
  if (isCounterparty) {
    const counterpartyName = senderName || senderJid;
    Promise.all([
      fetch(`${ariaUrl}/api/aria/claims/ingest`, {
        method: 'POST', headers,
        body: JSON.stringify({
          text,
          counterparty: counterpartyName,
          deal_id: '',
          channel: 'whatsapp',
          message_id: messageId,
        }),
        signal: AbortSignal.timeout(10000),
      }).catch(() => null),
      fetch(`${ariaUrl}/api/aria/deception/analyse`, {
        method: 'POST', headers,
        body: JSON.stringify({
          text,
          context_type: 'business_communication',
        }),
        signal: AbortSignal.timeout(10000),
      }).then(r => r?.ok ? r.json() : null).catch(() => null),
    ]).then(([_claims, deception]) => {
      const score = deception?.raw_score || 0;
      if (score >= ARIA_DECEPTION_THRESHOLD) {
        console.warn(
          `[WA Mirror] Elevated deception risk ${score.toFixed(2)} ` +
          `(${deception?.tier}) from ${counterpartyName}: ${text.slice(0, 80)}`
        );
      }
    }).catch(() => {});
  }
}

// ── Smart message splitter — never breaks mid-word or mid-sentence ──────────
// ── Markdown normaliser for WhatsApp ────────────────────────────────────────
// WhatsApp renders ONLY this set: *bold*, _italic_, ~strike~, ```code```.
// LLMs sometimes emit standard Markdown (`**bold**`, `### headings`,
// `[text](url)`) despite the system prompt RESPONSE STYLE telling them
// not to. When that bleeds through, the user sees literal `**` and `###`
// characters which breaks the layout.
//
// This helper rewrites the LLM's Markdown into the WhatsApp dialect at
// the presentation boundary, so the API output stays clean (other clients
// like the web dashboard may want full Markdown) while the WA-bound text
// renders correctly. Past incident 2026-04-09: even after the new
// RESPONSE STYLE block was deployed, ARIA's Modirum Gespi reply used
// `**Entity:** Modirum` and similar — the prompt rule was being violated
// under load. Post-processing is the only reliable fix.
function _normaliseForWhatsApp(text) {
  if (!text || typeof text !== 'string') return text;
  let out = text;
  // **bold** → *bold* (handle the most common LLM bleed)
  out = out.replace(/\*\*([^\n*][^*]*?[^\n*])\*\*/g, '*$1*');
  // Single-asterisk after the conversion may collide; normalise stray ** to *
  out = out.replace(/\*\*\*([^*]+?)\*\*\*/g, '*$1*');
  // ### Heading → *Heading*
  out = out.replace(/^#{1,6}\s+(.+)$/gm, '*$1*');
  // [link text](url) → link text (url)  — WhatsApp doesn't render the link
  // syntax, so the bare format is more readable than literal brackets.
  out = out.replace(/\[([^\]\n]+)\]\((https?:\/\/[^)\s]+)\)/g, '$1 ($2)');
  // Markdown horizontal rules `---` on their own line → box-drawing line
  // (matches the visual separator the new RESPONSE STYLE encourages).
  out = out.replace(/^\s*[-*_]{3,}\s*$/gm, '━━━━━━━━━━━━━━━━━━━━');
  return out;
}

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
// Force libsignal to (re)establish sessions for the participants of a chat.
// Called before retrying a send that failed with "No sessions" — Baileys
// stores per-recipient session keys, and after a reconnect those keys can
// be missing for senders who haven't sent us a fresh inbound message yet.
// `assertSessions(jids, true)` triggers a prekey fetch from the WA server
// and rebuilds the session, after which sendMessage can succeed.
async function _ensureSessions(chatId) {
  if (!sock) return;
  const jids = [];
  try {
    if (chatId.endsWith('@g.us')) {
      // Group: fetch participants from group metadata. Cap at the first 50
      // to avoid an enormous prekey fetch on a 200-person group — for
      // sending replies to ARIA mentions, we only need the participants
      // who can read our message immediately, and Baileys will lazily
      // resolve the rest.
      const meta = await sock.groupMetadata(chatId).catch(() => null);
      if (meta && Array.isArray(meta.participants)) {
        for (const p of meta.participants.slice(0, 50)) {
          if (p && p.id) jids.push(p.id);
        }
      }
    } else {
      // 1:1 chat — chatId IS the recipient JID
      jids.push(chatId);
    }
    if (jids.length === 0) return;
    // assertSessions returns true if any new sessions were created.
    // Force=true bypasses the cache and re-fetches prekeys even if a stale
    // session exists. This is the recovery hammer.
    if (typeof sock.assertSessions === 'function') {
      await sock.assertSessions(jids, true);
      console.log(`[WA Listener] Re-asserted libsignal sessions for ${jids.length} participant(s) in ${chatId}`);
    }
  } catch (e) {
    console.warn(`[WA Listener] _ensureSessions failed for ${chatId}: ${e.message}`);
  }
}

// Track recently-sent ARIA message IDs so the reaction handler can identify
// reactions to our own messages without relying on fromMe — which is
// unreliable when the bot is paired to the user's own WhatsApp account
// (both bot-sent and user-sent messages share the same participant LID),
// and which has flipped meaning across Baileys versions. Bounded ring
// keyed off the Set's insertion-order iteration.
const _sentMsgIds = new Set();
const _SENT_MSG_ID_CAP = 1000;
function _trackSentMsgId(id) {
  if (!id) return;
  _sentMsgIds.add(id);
  if (_sentMsgIds.size > _SENT_MSG_ID_CAP) {
    const oldest = _sentMsgIds.values().next().value;
    _sentMsgIds.delete(oldest);
  }
}

async function sendMessage(chatId, text, quotedMsg = null) {
  if (!sock || !isConnected) {
    console.warn('[WA Listener] Cannot send — not connected');
    return null;
  }
  if (!text || !chatId) return null;

  // Normalise standard Markdown into the WhatsApp dialect at the presentation
  // boundary. The LLM's RESPONSE STYLE block forbids `**bold**`/`### heading`/
  // `[link](url)` but it gets violated under load — this is the deterministic
  // backstop. See _normaliseForWhatsApp() above for the rewriting rules.
  const normalised = _normaliseForWhatsApp(text);
  // Chunk size 1800 (NOT the 4096 WA theoretical limit). WhatsApp silently
  // drops long messages from automated senders to groups once they cross
  // ~2000 chars — server logs a successful send but the message never
  // reaches recipients. Live incident 2026-04-20: a 3,767-char meta_query
  // reply was sent cleanly per logs, zero delivery to the user's phone;
  // a 54-char diagnostic sent seconds later arrived instantly. Capping
  // at 1800 gives safe headroom + still fits most answers in 1-3 chunks.
  const chunks = splitMessageSmart(normalised, 1800);
  let firstKey = null;

  // Inner function so we can call it twice (initial attempt + post-recovery
  // retry) without duplicating the chunk-loop logic.
  async function _sendChunks() {
    let _firstKey = null;
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
      if (sent && sent.key && sent.key.id) _trackSentMsgId(sent.key.id);
      if (i === 0 && sent && sent.key) _firstKey = sent.key;
      // Small delay between chunks to avoid flood
      if (chunks.length > 1 && i < chunks.length - 1) {
        await new Promise(r => setTimeout(r, 500));
      }
    }
    return _firstKey;
  }

  try {
    firstKey = await _sendChunks();
  } catch (e) {
    // 2026-04-08 round 5e: catch the post-reconnect "No sessions" error and
    // recover by forcing libsignal to re-establish sessions for the chat
    // participants. Without this, every redeploy of seenode breaks ARIA's
    // ability to reply for the first 1-2 messages from each sender, until
    // the libsignal session organically warms up — the user sees no reply
    // at all, just listener-side "Send failed: No sessions" log lines.
    const msg = (e && e.message) || '';
    const isSessionError = /no sessions|session not found|sessionerror|libsignal/i.test(msg);
    if (isSessionError) {
      console.warn(`[WA Listener] Send failed with session error (${msg}) — attempting libsignal recovery and retry…`);
      try {
        await _ensureSessions(chatId);
        // Brief pause to let prekey fetch settle before the retry
        await new Promise(r => setTimeout(r, 1500));
        firstKey = await _sendChunks();
        console.log(`[WA Listener] Recovery retry succeeded for ${groupNames.get(chatId) || chatId}`);
      } catch (e2) {
        console.error(`[WA Listener] Send failed (recovery retry also failed): ${e2.message}`);
        return null;
      }
    } else {
      console.error('[WA Listener] Send failed:', msg);
      return null;
    }
  }

  messagesSent++;
  console.log(`[WA Listener] Sent message to ${groupNames.get(chatId) || chatId} (${text.length} chars in ${chunks.length} chunk${chunks.length > 1 ? 's' : ''})`);
  return firstKey;
}

// ── Snapshot an ARIA reply for later feedback correlation ──────────────────
// Called after sendMessage() for substantive ARIA replies (mention answers,
// OCR results, image analysis). Stores the Q→A pair under the message key
// so when a user reacts with an emoji, the reaction handler can recover the
// original question. Fire-and-forget — never blocks the chat path.
async function snapshotAriaReply(key, question, answer, user, groupName, metadata = {}, traceId = '') {
  if (!key || !key.id || !key.remoteJid) return;
  const ariaServiceUrl = process.env.ARIA_SERVICE_URL;
  if (!ariaServiceUrl) return;
  try {
    await _ariaFetch(`${ariaServiceUrl}/api/aria/feedback/snapshot`, {
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
        trace_id: traceId || '',
      }),
      signal: AbortSignal.timeout(5000),
    }).catch(() => {});
  } catch {/* fire-and-forget */}
}

// ── Helper: unwrap askARIA result + trigger self-diagnostic on failure ─────
// askARIA() returns one of:
//   - { text, trace_id }              ← success (object form, current)
//   - { error: true, message }        ← failure
//   - string (legacy)                 ← treated as text-only
//   - null                            ← no response at all
// Callers using `_unwrapAriaReply(result)` get back a plain string for
// the WA reply. Callers needing the trace_id should pull it from the
// raw result object before unwrapping (see handleAriaMention).
function _unwrapAriaReply(result, fallbackMsg = 'ARIA is temporarily unavailable. Please try again shortly.') {
  if (typeof result === 'string' && result) return result;
  if (result && typeof result === 'object' && typeof result.text === 'string' && result.text) {
    return result.text;
  }
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
      ? '\n\n_The reply took longer than expected. Try the same question again — the slower path usually completes within 2-3 minutes. If it keeps timing out, the upstream LLM provider may be degraded._'
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
    await _ariaFetch(`${ariaUrl}/api/aria/self/diagnose`, {
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
// ── Recent OCR extraction buffer — bridges image + text messages ──────────
// When a user sends an image, the OCR result is stored here keyed by chatId.
// When the next text message references a photo/image, the buffer is injected
// into the message context so ARIA can investigate based on the extracted text.
// Entries expire after 5 minutes (300s).
const _recentOCR = new Map();  // chatId → { text, method, sender, ts }
const _OCR_BUFFER_TTL_MS = 300000;

function _storeOCRResult(chatId, extracted, method, sender) {
  _recentOCR.set(chatId, {
    text: extracted,
    method,
    sender,
    ts: Date.now(),
  });
  // Cleanup old entries
  for (const [k, v] of _recentOCR) {
    if (Date.now() - v.ts > _OCR_BUFFER_TTL_MS) _recentOCR.delete(k);
  }
}

function _consumeOCRBuffer(chatId) {
  const entry = _recentOCR.get(chatId);
  if (!entry) return null;
  if (Date.now() - entry.ts > _OCR_BUFFER_TTL_MS) {
    _recentOCR.delete(chatId);
    return null;
  }
  _recentOCR.delete(chatId);  // consume once
  return entry;
}

let _alertPollerStarted = false;
let _alertPollerInterval = null;

// ── Auto-investigation cost guard ─────────────────────────────────────────
// Keeps cost structure intact while enabling autonomous learning.
// Limits: 3 auto-investigations per 24h globally, 1 per country per 6h.
// In-flight guard: prevents overlapping investigations from concurrent poll cycles.
const _autoInvestigateLog = new Map();  // country → timestamp
const _autoInvestigateInFlight = new Set();  // countries currently being investigated
let _autoInvestigateDailyCount = 0;
let _autoInvestigateDayStart = Date.now();
const AUTO_INVESTIGATE_MAX_DAILY = parseInt(process.env.AUTO_INVESTIGATE_MAX_DAILY || '3', 10);
const AUTO_INVESTIGATE_COUNTRY_COOLDOWN_MS = 6 * 3600 * 1000; // 6 hours

function _canAutoInvestigate(country) {
  const now = Date.now();
  // Reset daily counter at midnight
  if (now - _autoInvestigateDayStart > 86400000) {
    _autoInvestigateDailyCount = 0;
    _autoInvestigateDayStart = now;
  }
  // Global daily cap
  if (_autoInvestigateDailyCount >= AUTO_INVESTIGATE_MAX_DAILY) {
    console.log(`[Auto-Investigate] Daily cap reached (${AUTO_INVESTIGATE_MAX_DAILY}). Skipping ${country}.`);
    return false;
  }
  // Per-country cooldown
  const lastRun = _autoInvestigateLog.get(country) || 0;
  if (now - lastRun < AUTO_INVESTIGATE_COUNTRY_COOLDOWN_MS) {
    console.log(`[Auto-Investigate] ${country} investigated ${Math.round((now - lastRun) / 60000)}m ago. Cooldown active.`);
    return false;
  }
  // In-flight guard: prevent overlapping investigations from concurrent poll cycles
  if (_autoInvestigateInFlight.has(country)) {
    console.log(`[Auto-Investigate] ${country} already in-flight. Skipping.`);
    return false;
  }
  return true;
}

function _recordAutoInvestigate(country) {
  _autoInvestigateLog.set(country, Date.now());
  _autoInvestigateDailyCount++;
  // Prune old entries to prevent unbounded growth
  if (_autoInvestigateLog.size > 200) {
    const cutoff = Date.now() - AUTO_INVESTIGATE_COUNTRY_COOLDOWN_MS;
    for (const [k, v] of _autoInvestigateLog) {
      if (v < cutoff) _autoInvestigateLog.delete(k);
    }
  }
}

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
      // Past incident 2026-04-09 18:46 — alert poll cycle failed timing
      // out and surfacing as "Alert poll cycle failed: The operation was
      // aborted due to timeout". Root cause was that this raw fetch call
      // was NOT going through _ariaFetch so it had no Authorization
      // header, fly.io returned 401 (visible in fly logs as
      // "GET /api/aria/proactive/alerts HTTP/1.1 401 Unauthorized"),
      // and the listener was treating the 401 chain as a timeout because
      // the 15s budget was eaten by retries / TLS overhead. Fix: use
      // the _ariaFetch wrapper which auto-injects ARIA_API_TOKEN, plus
      // bump the abort timeout to 30s for cold-start resilience.
      const r = await _ariaFetch(`${ariaUrl}/api/aria/proactive/alerts?mark_seen=true`, {
        signal: AbortSignal.timeout(30000),
      });
      if (!r.ok) {
        if (r.status === 401) {
          console.warn(
            '[WA Listener] Alert poll: 401 from fly.io — ARIA_API_TOKEN missing or wrong on this listener process'
          );
        }
        return;
      }
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

        // ── AUTO-INVESTIGATE on CRITICAL escalation alerts ───────────
        // 2026-04-12: when the conflict tracker detects CRITICAL escalation,
        // automatically fire a background investigation. ARIA learns from
        // every investigation (distillation into reasoning library + KB),
        // so this builds her knowledge autonomously.
        // Cost guard: max 3/day globally, 1/country/6h.
        if (alert.metadata?.auto_investigate && alert.severity === 'critical') {
          const country = alert.metadata.country;
          if (country && alertGroup && _canAutoInvestigate(country)) {
            _recordAutoInvestigate(country);
            _autoInvestigateInFlight.add(country);
            const esc = alert.metadata.escalation_score || '?';
            const events = alert.metadata.total_events || '?';
            const fatalities = alert.metadata.fatalities || 0;
            console.log(`[Auto-Investigate] Firing background investigation for ${country} (escalation ${esc}/100)`);

            // Fire-and-forget: runs 90-180s in background, posts result when done.
            // Uses askARIA which routes through the full deep_research pipeline.
            // In-flight guard cleared in finally block to prevent overlapping runs.
            (async () => {
              try {
                const investigatePrompt =
                  `Aria, investigate the security and defence procurement situation in ${country}. ` +
                  `CONTEXT: CRITICAL escalation alert just fired — score ${esc}/100, ` +
                  `${events} events in 30 days, ${fatalities} fatalities, ` +
                  `momentum ${alert.metadata.momentum || '?'}x acceleration. ` +
                  `Focus on: ` +
                  `1) What is driving the escalation (actors, regions, event types)? ` +
                  `2) Defence procurement implications — what capabilities will be in demand? ` +
                  `3) Compliance considerations — any sanctions/embargo changes? ` +
                  `4) Recommended engagement strategy and timing.`;

                const result = await askARIA(investigatePrompt, '', 'escalation_auto');
                if (result && result.length > 50) {
                  const header = `📊 *AUTO-INVESTIGATION: ${country.toUpperCase()}*\n` +
                    `_Triggered by CRITICAL escalation (${esc}/100)_\n\n`;
                  await sendMessage(alertGroup, header + result.slice(0, 3000)).catch(e => {
                    console.warn(`[Auto-Investigate] Failed to post ${country} results:`, e.message);
                  });
                  console.log(`[Auto-Investigate] ${country} complete — ${result.length} chars posted`);
                } else {
                  console.warn(`[Auto-Investigate] ${country} returned empty/short result`);
                }
              } catch (e) {
                console.error(`[Auto-Investigate] ${country} failed:`, e.message);
              } finally {
                _autoInvestigateInFlight.delete(country);
              }
            })();
          }
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
// R-F525 (2026-05-14) — added \bdd\b abbreviation + URL pattern so messages
// like "Aria, do a full DD on https://adsm-sa.com/" route to the long-
// research path (10-min budget) instead of the 4-min default. Live WA
// failure 14:07-14:30 BST: DD on URL took 5+ min on fly's side, WA
// listener aborted at exactly 240s (default), seenode fell back to the
// R-F160 unguarded-fallback-refused guard message ("fly.io ARIA is
// currently unreachable"). Fly was actually still processing; the work
// was discarded by the early abort.
const _LONG_RESEARCH_RE = /\b(research|investigate|crawl|deep[\s\-]?dive|look\s+into|dig\s+into|find\s+out\s+about|profile|due\s+diligence|\bdd\b|background\s+check|each\s+(?:one|of|supplier|company|entity|person)|compare\s+(?:these|each|all)|analy[sz]e\s+(?:each|these|all))\b|https?:\/\//i;

// 2026-04-08 round 3: feedback statements like "this was not relevant to the
// profile investigation" or "your investigation of X was wrong" matched the
// long-research regex above (because they contain "investigation"/"profile")
// and got the 4-min "On it..." progress message even though they're feedback,
// not research requests. Pre-check for feedback patterns first.
const _FEEDBACK_RE = /\b(?:that(?:\s+was|'?s)\s+(?:wrong|incorrect|the\s+wrong)|you\s+(?:are|got)\s+wrong|wrong\s+answer|not\s+relevant\s+to|this\s+(?:was|is)\s+not\s+relevant|don'?t\s+present\s+outdated|stop\s+(?:making\s+up|inventing)|please\s+correct|the\s+\w+\s+breakdown\s+was\s+(?:useful|good)\s+but)\b/i;

function _isFeedbackMessage(text) {
  return _FEEDBACK_RE.test(text || '');
}

function _isLongResearchPrompt(text) {
  if (_isFeedbackMessage(text)) return false;
  return _LONG_RESEARCH_RE.test(text || '');
}

// ── Ask ARIA and get a response ─────────────────────────────────────────────
// BUG-FIX: previously this slice'd the response to 3000 chars (truncation
// bug) AND the timeout was 120s which is too short for research operations.
// Now: 16000 cap + dynamic timeout (4 min for research, 90s for routine).
//
// Past incident 2026-04-09 18:14 — DUMA Engineering investigation chain:
// previously this function wrapped the user's message with the group
// context as a literal text block prepended to the message body:
//
//   [WhatsApp group context]
//   [Antonio]: <prior turn 1>
//   [Antonio]: <prior turn 2>
//
//   [Question from Antonio]
//   <current message>
//
// That bled prior-turn topics (Iraq tenders) into the entity extraction
// for the current turn (DUMA Engineering URL), polluting Brave Search
// queries and producing fabricated "self-improvement plan" responses.
// The server-side now strips the wrapper format defensively, but the
// proper architectural fix is to send group_context as a SEPARATE JSON
// field so the server can treat it as a context layer without it
// contaminating intent detection or LLM prompt construction.
async function askARIA(message, groupContext = '', sender = 'whatsapp', progressCb = null, overrideTimeoutMs = 0) {
  const port = process.env.PORT || 3117;
  const sid  = `wa_group_${sender.replace(/[^a-zA-Z0-9]/g, '').slice(-10)}`;
  const fullMessage = message;

  // R-F1760: overrideTimeoutMs lets the conversation manager set a
  // per-strategy timeout (60s for fast_llm, 300s for full_llm).
  // When not set, fall back to the existing heuristic.
  let timeoutMs = overrideTimeoutMs;
  if (!timeoutMs) {
    const isLong = _isLongResearchPrompt(message);
    // 2026-04-11 Hanwha Redback incident: research prompts get 360s.
    // R-F525 (2026-05-14): bumped long budget 360s → 600s so full DD on
    // URLs (5-10 min wall time on fly side) doesn't abort early. Both
    // env-tunable so operator can shorten in dev or stretch if needed
    // without redeploy.
    const _longMs = parseInt(process.env.WA_LONG_BUDGET_MS || '600000', 10);
    const _shortMs = parseInt(process.env.WA_SHORT_BUDGET_MS || '240000', 10);
    timeoutMs = isLong ? _longMs : _shortMs;
  }

  // 2026-04-12: Use SSE streaming endpoint so we can surface progressive
  // updates to WhatsApp during the 60-180s pipeline (tool execution →
  // synthesis). Accumulates chunks into a full response; fires progressCb
  // on status events so the caller can send interim WhatsApp messages.
  try {
    const r = await fetch(`http://localhost:${port}/api/aria/chat/stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${INT_TOKEN}`,
      },
      body: JSON.stringify({
        message: fullMessage,
        session_id: sid,
        auto_tools: true,
        group_context: groupContext || '',
        tone: ARIA_TONE,  // professional | warm | concise — brain uses this to select register
      }),
      signal: AbortSignal.timeout(timeoutMs),
    });

    if (!r.ok) {
      const errBody = await r.text().catch(() => '');
      throw new Error(`ARIA ${r.status}${errBody ? ': ' + errBody.slice(0, 200) : ''}`);
    }

    // Parse the SSE stream — accumulate text chunks, fire progress on status events
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let fullText = '';
    let traceId = '';
    let toolUsed = '';
    let buffer = '';
    let streamError = '';   // captured to surface up if no chunks land

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // Process complete SSE events (separated by \n\n)
      const events = buffer.split('\n\n');
      buffer = events.pop() || ''; // keep incomplete last chunk

      for (const raw of events) {
        if (!raw.startsWith('data: ')) continue;
        try {
          const evt = JSON.parse(raw.slice(6));
          if (evt.type === 'chunk' && evt.text) {
            fullText += evt.text;
          } else if (evt.type === 'status' && evt.message && progressCb) {
            // Fire progress callback — caller sends interim WA message
            progressCb(evt.message);
          } else if (evt.type === 'done') {
            traceId = evt.trace_id || traceId;
          } else if (evt.type === 'meta') {
            toolUsed = evt.tool_used || toolUsed;
            traceId = evt.trace_id || traceId;
          } else if (evt.type === 'error') {
            const m = evt.message || evt.kind || 'stream error';
            console.warn('[WA→ARIA] stream error event:', m);
            streamError = m;  // remember last; throw below if no chunks land
          }
        } catch { /* skip malformed SSE lines */ }
      }
    }

    // 2026-04-24 fix: if the stream produced no chunks but emitted an error
    // event (fly.io 5xx, brain timeout, provider outage), throw so the
    // catch block triggers the /chat fallback. Previously we silently
    // returned null here and the caller had no idea anything went wrong.
    if (!fullText) {
      if (streamError) throw new Error(streamError);
      return null;
    }

    // R-F467 (2026-05-14): mid-stream cut marker. If chunks DID land but
    // an error event followed (LB dropped the connection mid-response,
    // brain breaker tripped, provider outage halfway through), append a
    // visible marker so the WhatsApp user can tell the answer was
    // truncated vs complete. Pre-R-F467 streamError was logged but the
    // partial fullText was sent as if it were the full response — the
    // user saw a mid-sentence cutoff with no signal that anything was
    // wrong.
    if (streamError) {
      console.warn('[WA→ARIA] R-F467: mid-stream cut after content:', streamError);
      fullText = fullText + `\n\n⚠️ [!stream cut: ${String(streamError).slice(0, 200)}]`;
    }

    // ── Clause 20 commitment guard (client-side) ──────────────────
    // The streaming endpoint bypasses Python post-processors. Apply
    // the commitment guard here on the assembled response before
    // sending to WhatsApp. Strips status footers, rewrites promises.
    fullText = _guardCommitments(fullText);

    return { text: fullText.slice(0, 16000), trace_id: traceId, tool_used: toolUsed };
  } catch (e) {
    // Fallback: if streaming endpoint is unavailable (older fly build),
    // fall back to the blocking /chat endpoint.
    //
    // 2026-04-26: when the streaming failure is a transport-layer error
    // (fly.io unreachable / DNS / abort / timeout), retrying the same
    // fly.io path through /chat just burns another 6 minutes and ends
    // with the same "fetch failed". Signal /chat to skip ARIA_SERVICE_URL
    // and go straight to the local Node fallback (ariaLocalChat) — the
    // user gets a degraded but real answer in ~30s instead of nothing
    // in ~12 minutes.
    const _eMsg = e.message || '';
    const skipFly = /fetch failed|ECONN|ENOTFOUND|EAI_AGAIN|aborted|timeout|deadline|Python\s+5\d\d/i.test(_eMsg);
    console.warn(`[WA Listener] Streaming failed, falling back to /chat${skipFly ? ' (skip_aria_service)' : ''}:`, _eMsg);
    try {
      const r = await fetch(`http://localhost:${port}/api/aria/chat`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${INT_TOKEN}`,
        },
        body: JSON.stringify({
          message: fullMessage,
          session_id: sid,
          group_context: groupContext || '',
          skip_aria_service: skipFly,
        }),
        signal: AbortSignal.timeout(timeoutMs),
      });
      if (!r.ok) {
        // Pull the real reason so the user sees "fallback 400: message required"
        // / "fallback 503: LLM not configured" instead of the opaque "fallback 400".
        // Helps distinguish client-side bugs (bad payload) from provider
        // outages (Anthropic billing, DeepSeek 5xx, etc.).
        const errBody = await r.text().catch(() => '');
        let detail = '';
        try {
          const j = errBody ? JSON.parse(errBody) : null;
          detail = (j && (j.detail || j.message || j.error)) || '';
        } catch { detail = ''; }
        if (!detail) detail = (errBody || '').slice(0, 200);
        throw new Error(`ARIA fallback ${r.status}${detail ? ': ' + detail : ''}`);
      }
      const data = await r.json();
      const text = data.response || data.answer || '';
      if (!text) return null;
      return { text: text.slice(0, 16000), trace_id: data.trace_id || '' };
    } catch (e2) {
      console.error('[WA Listener] ARIA chat failed (both paths):', e2.message);
      // Extract a friendlier hint when the upstream is a provider outage.
      // Users shouldn't need to know what a 400 means — they need to know
      // whether to retry or switch to a backup path.
      const m = e2.message || '';
      let hint = '';
      if (/balance|credit|billing|quota|insufficient/i.test(m)) {
        hint = ' (Claude billing — DeepSeek fallback should engage; retry once)';
      } else if (/timeout|aborted|deadline/i.test(m)) {
        hint = ' (upstream timeout — retry with a shorter prompt)';
      } else if (/401|authentic|unauthor/i.test(m)) {
        hint = ' (auth token mismatch — operator check env on seenode)';
      } else if (/message required/i.test(m)) {
        hint = ' (the message payload arrived empty — possibly a document-upload size issue)';
      } else if (/fetch failed|ECONN|ENOTFOUND|EAI_AGAIN/i.test(m)) {
        hint = ' (fly.io ARIA service unreachable — usually a cold-start or transient network blip; retry in 60s)';
      }
      return { error: true, message: hint ? `${m}${hint}` : m };
    }
  }
}

// Trivial-question short-circuit (third copy — keep in sync with server.mjs
// trivialReply() and aria_service/intel/reasoning_library.trivial_reply()).
// Greetings / liveness / identity probes get a fixed reply with zero LLM cost
// AND zero HTTP round-trip. Past incident 2026-04-08: askARIA wraps the user
// message in a multi-line "[WhatsApp group context]\n...\n[Question from X]\n
// <msg>" envelope before POSTing to /api/aria/chat, so the server-side regex
// (which expects the message to start with the actual question) never matched.
// Catching it HERE — before the wrapping happens — is the only reliable place.
function _waTrivialReply(q) {
  if (!q || typeof q !== 'string') return null;
  let s = q.trim().toLowerCase().replace(/[?.! ]+$/g, '');
  s = s.replace(/^aria[,!\s]*/, '').trim();
  if (!s) return null;
  if (/^(are\s+you\s+(online|there|alive|awake|working|up|ready|here)|you\s+(online|there|alive|awake))$/.test(s))
    return "✅ Yes, I'm online and ready. Send me a question, drop a document or image, or use /help for commands.";
  if (/^(hello|hi|hey|good\s+(morning|afternoon|evening|night))$/.test(s))
    return "Hi — ARIA here. Ask me anything about compliance, defence procurement, or market intel. /help shows the full command list.";
  if (/^(who\s+are\s+you|what\s+are\s+you|what(?:'s| is)\s+your\s+name)$/.test(s))
    return "I'm ARIA — Autonomous Research Intelligence Agent. I do compliance screening (sanctions, export controls, country risk), defence procurement intel, and market/competitor research. Run /help for the full menu.";
  if (/^(test|ping|status)$/.test(s)) return "✅ Pong. Service is up. /help for commands.";
  if (/^(ok|yes|no)$/.test(s)) return "👍";
  if (/^(thanks?|thank\s+you)$/.test(s)) return "You're welcome.";
  // Background/status meta probes — added 2026-04-08 after a 4-min timeout
  // chasing "can you confirm you are actually working on it in the background?"
  // The honest answer is that ARIA doesn't track in-flight tasks across
  // messages, so a re-send is the right move.
  if (/^(can\s+you\s+(confirm|tell\s+me|verify)\s+)?(you('?re|\s+are)?\s+|are\s+you\s+)?(still|actually|really)?\s*(working|processing|running|on\s+it|there|alive)(\s+on\s+(it|that|this))?(\s+in\s+the\s+background)?$/.test(s)
   || /^(still|actually)\s+(working|on\s+it|there)(\s+on\s+(it|that|this))?(\s+in\s+the\s+background)?$/.test(s)
   || /^did\s+you\s+(get|hear|see)\s+(that|me|it|my\s+(message|question))$/.test(s))
    return "✅ I'm here. I don't persist long-running tasks across messages — if your last question is still pending after ~60s, please re-send it and I'll work on it now.";
  return null;
}

// ── Handle ARIA mention — generate and send reply ───────────────────────────
async function handleAriaMention(msg, chatId, groupName, senderName, text) {
  if (!WA_REPLY_ENABLED) return;
  if (isReplyLimited(chatId)) {
    console.log(`[WA Listener] Reply rate-limited for ${groupName}`);
    return;
  }

  // ── Trivial-question short-circuit ────────────────────────────────────
  // Catch greetings / liveness / identity probes BEFORE we build the
  // group-context wrapper that would make server-side regexes miss them.
  const _trivial = _waTrivialReply(text);
  if (_trivial !== null) {
    console.log(`[WA Listener] Trivial short-circuit: "${text.slice(0, 80)}" → fixed reply`);
    await sendMessage(chatId, `*ARIA* — ${_trivial}`, msg).catch(() => {});
    return;
  }

  // ── Document/media injection (constitution clause 12 enforcement) ────
  // If the user attached a file along with the mention, the parsed text
  // needs to land DIRECTLY in the chat envelope so the LLM can quote from
  // it. Without this, processMedia stores the content in a separate
  // channel that the chat path never reads — and the LLM hallucinates a
  // "review" from prior conversation context (past incident 2026-04-09:
  // ARIA produced a confident "Ghana opportunity" review of an attached
  // CDL Hotels amendment PDF that she never actually read).
  let attachedBlock = '';
  const _docMsg = msg.message?.documentMessage
    || msg.message?.documentWithCaptionMessage?.message?.documentMessage;
  const _imgMsg = msg.message?.imageMessage;
  if (_docMsg || _imgMsg) {
    try {
      // Await processMedia inline (it was skipped in the background path
      // for mentioned messages — see messages.upsert above). This blocks
      // the reply by ~the parse time of the document, which is acceptable
      // because the user is expecting a reply that depends on the file.
      await processMedia(msg, sock, groupName, senderName).catch(() => {});
    } catch {/* swallow — fall through to PARSE FAILED branch */}
    // Pull the most recent stored content for this sender that carries a
    // document/image marker (processMedia uses these prefixes in store()).
    const recentDoc = messageStore
      .filter(m => m.groupId === chatId && m.senderName === senderName)
      .slice(-3)
      .reverse()
      .find(m => /^\[(Document|Image)[:\s]/i.test(m.text || ''));
    const fileLabel = (_docMsg?.fileName) || (_imgMsg ? 'image' : 'attachment');
    if (recentDoc && recentDoc.text && recentDoc.text.length > 200
        && !/Document shared:/i.test(recentDoc.text)) {
      // We have real extracted text — inject up to MAX_DOC_CHARS verbatim.
      // If the slice itself drops further content (the stored text is
      // longer than MAX_DOC_CHARS), restamp so the LLM still sees the
      // partial-extraction banner at the top of the block.
      let _docBody = recentDoc.text;
      if (_docBody.length > MAX_DOC_CHARS) {
        _docBody = stampPartialExtraction(
          _docBody.slice(0, MAX_DOC_CHARS),
          `text was ${_docBody.length} chars when stored, then sliced to the first ${MAX_DOC_CHARS} chars for the chat envelope`,
        );
      }
      attachedBlock = `[ATTACHED DOCUMENT — full extracted text below; per CONSTITUTION clause 12 you MUST quote verbatim from this text and MUST NOT review based on prior conversation context]
${_docBody}
[END ATTACHED DOCUMENT]

`;
    } else {
      // No usable extraction — tell the LLM explicitly so it can refuse
      // per clause 12 instead of confabulating.
      attachedBlock = `[ATTACHED DOCUMENT: ${fileLabel} — PARSE FAILED, NO TEXT EXTRACTED]
Per CONSTITUTION clause 12 you MUST refuse to review this document and ask the user to paste the text directly or reshare the file.
[END ATTACHED DOCUMENT]

`;
    }
  }
  // ── Inject recent OCR extraction if the message references an image/photo ──
  // When a user sends an image (no caption) then follows up with "investigate
  // the company in the photo", we inject the OCR text so ARIA has context.
  const _imageRefPattern = /\b(photo|image|picture|screenshot|pic|img|attached|business card|card)\b/i;
  if (!attachedBlock && _imageRefPattern.test(text)) {
    const ocrBuffer = _consumeOCRBuffer(chatId);
    if (ocrBuffer && ocrBuffer.text) {
      attachedBlock = `[IMAGE OCR — extracted from photo shared moments ago by ${ocrBuffer.sender} via ${ocrBuffer.method}]
${ocrBuffer.text.slice(0, MAX_DOC_CHARS)}
[END IMAGE OCR]

`;
      console.log(`[WA Listener] Injected OCR buffer (${ocrBuffer.text.length} chars) into message referencing image`);
    }
  }

  // ── R-F853 — inject a recently-shared DOCUMENT when a text follow-up
  // references one (symmetric to the image-OCR buffer above). Fixes the
  // recurring "send the contract, then ask about it in a SEPARATE message →
  // ARIA reports no document" failure. Only fires when this message has no
  // attachment of its own; otherwise normal chat proceeds.
  if (!attachedBlock) {
    const _recentDocText = findRecentDocForReference(messageStore, chatId, senderName, text);
    if (_recentDocText) {
      let _docBody = _recentDocText;
      if (_docBody.length > MAX_DOC_CHARS) {
        _docBody = stampPartialExtraction(
          _docBody.slice(0, MAX_DOC_CHARS),
          `recently-shared document was ${_docBody.length} chars when stored, then sliced to the first ${MAX_DOC_CHARS} chars for the chat envelope`,
        );
      }
      attachedBlock = `[ATTACHED DOCUMENT — recently shared by ${senderName}; per CONSTITUTION clause 12 you MUST quote verbatim from this text and MUST NOT review based on prior conversation context]
${_docBody}
[END ATTACHED DOCUMENT]

`;
      console.log(`[WA Listener] R-F853 injected recently-shared document (${_recentDocText.length} chars) into follow-up referencing a document`);
    }
  }

  const enrichedText = attachedBlock + text;

  // Get recent group context (last 5 messages). Skip any entry whose text
  // is a [Document: ...] / [Image: ...] block — that content is now
  // injected explicitly via attachedBlock above with the full text rather
  // than a 200-char truncation.
  const recentMsgs = messageStore
    .filter(m => m.groupId === chatId && !/^\[(Document|Image)[:\s]/i.test(m.text || ''))
    .slice(-5)
    .map(m => `[${m.senderName}]: ${m.text.slice(0, 200)}`)
    .join('\n');

  console.log(`[WA Listener] ARIA mentioned by ${senderName} in ${groupName} — generating reply...${attachedBlock ? ' (with attached document context)' : ''}`);

  // For long-running research prompts, send a "working on it" progress message
  // BEFORE the actual call so the user knows ARIA is engaged + understands
  // the wait. The 4-minute timeout in askARIA accommodates these operations.
  // 2026-04-08 round 4: ALSO send a lighter "thinking..." message for any
  // non-trivial mention that isn't research-flagged. The full chat path
  // (9-layer context + LLM call + verification + honesty dispatch) routinely
  // runs 30-90s on a normal question, and silence in that window made users
  // assume ARIA had crashed.
  // Progress messages intentionally do NOT quote the user's original message.
  // Quoting the original on every interim update ("Thinking…", "Building
  // context…") caused the transcript to echo the user's question 3x per
  // turn — noisy and unprofessional. Only the final substantive reply
  // quotes the original, which is what reaction-based feedback keys on.
  // R-F1770: use WhatsApp typing indicator instead of multiple progress messages.
  // The typing indicator (composing presence) shows a subtle "typing..." in the
  // chat header - no extra messages, no clutter, professional and familiar.
  const isLong = _isLongResearchPrompt(text);

  if (isLong) {
    // One acknowledgment message, then typing indicator for progress
    if (/https?:\/\//i.test(text)) {
      await sendMessage(chatId, `*ARIA* - Fetching the page now - I will post findings as they come in.`, null).catch(() => {});
    } else {
      await sendMessage(chatId, `*ARIA* - On it. I will post the findings here when ready.`, null).catch(() => {});
    }
    // Show typing indicator while working (Baileys composing presence)
    try { if (sock && isConnected) await sock.sendPresenceUpdate('composing', chatId); } catch {}
  } else if ((text || '').trim().length > 25) {
    // Non-research: typing indicator only - no "Thinking..." message
    try { if (sock && isConnected) await sock.sendPresenceUpdate('composing', chatId); } catch {}
  }

  // ── R-F3651: RESTORED. R-F1760's self-healing strategy loop was DELETED by
  // R-F1770 (6ff63e20) when it merged its own comment into the `const` on the
  // isLong line. What went with it: progressCb, the conversationManager
  // multi-strategy loop, `rawReply`, `reply`, and the honest all-strategies-
  // failed fallback. The scar was invisible to `node --check` (the mangled
  // `const` still parses) and to `npm run lint` (syntax-only), so for every
  // @-mention since, this function reached `rawReply` at the trace_id line and
  // threw `ReferenceError: rawReply is not defined` — i.e. WhatsApp mentions
  // produced NO reply at all, and the brain never learned why (§25). The
  // import of `conversationManager` (line 41) was left behind, unused, which
  // is what made the deletion detectable.
  //
  // Restored as R-F1760 had it, with ONE deliberate change to honour R-F1770's
  // intent: progressCb no longer sends interim chat messages (that message
  // spam is exactly what R-F1770 removed) — it refreshes the composing
  // presence instead, so progress stays visible without cluttering the thread.
  let _lastStatusAt = 0;
  const progressCb = () => {
    const now = Date.now();
    if (now - _lastStatusAt < 15000) return; // 15s rate limit
    _lastStatusAt = now;
    // Presence refresh only — WhatsApp expires "composing" after ~25s.
    try { if (sock && isConnected) sock.sendPresenceUpdate('composing', chatId).catch(() => {}); } catch {}
  };

  const conv = conversationManager.getOrCreate(chatId, senderName, groupName);
  let reply = null;
  let rawReply = null;

  while (conv.hasMoreStrategies()) {
    const strategy = conv.currentStrategy();
    try {
      rawReply = await askARIA(enrichedText, recentMsgs, senderName, progressCb, strategy.timeoutMs);
      reply = _unwrapAriaReply(rawReply);
      if (reply) break; // got a real answer
    } catch (err) {
      console.warn(`[ConvManager] Strategy ${strategy.name} failed: ${err.message}`);
    }
    conv.nextStrategy();
  }

  // If all strategies failed, build an honest fallback (§14 — say what is true).
  if (!reply) {
    reply = `⚠️ I tried several ways to answer that and couldn't reach my brain. This is unusual — my LLM provider may be temporarily unavailable.\n\nYou can try:\n• /screen [name] for a local sanctions check\n• /risk [country] for local country risk data\n• Ask again in 2-3 minutes\n\n_I've logged this and will learn from it._`;
  }
  conv.reset();

  // Pull trace_id off the raw result so we can pass it to the snapshot.
  // Reactions later look the trace up via the snapshot → /trace can show
  // the full lifecycle (cost, verification, feedback) in one record.
  const traceId = (rawReply && typeof rawReply === 'object' && rawReply.trace_id) || '';

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
    }, traceId).catch(() => {});
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
      let docBuffer = null;

      try {
        docBuffer = await downloadBuffer(docMsg, 'document');
        const buffer = docBuffer;
        console.log(`[WA] Downloaded: ${fileName} (${buffer.length} bytes)`);

        // ── PDF ──────────────────────────────────────────────────────────
        if (mime.includes('pdf') || fileName.toLowerCase().endsWith('.pdf')) {
          fileType = 'pdf';
          const pdfParse = await import('pdf-parse').then(m => m.default).catch(() => null);
          if (pdfParse) {
            const pdf = await pdfParse(buffer);
            const fullText = (pdf.text || '').trim();
            const truncated = fullText.length > MAX_DOC_CHARS;
            extractedText = truncated
              ? stampPartialExtraction(
                  fullText.slice(0, MAX_DOC_CHARS),
                  `pdf-parse returned ${pdf.numpages} pages totalling ${fullText.length} chars; only the first ${MAX_DOC_CHARS} chars are below`,
                )
              : fullText;
            console.log(`[WA] [${groupName}] ${senderName}: 📄 PDF "${fileName}" (${extractedText.length} chars${truncated ? ` of ${fullText.length} — TRUNCATED` : ''}, ${pdf.numpages} pages)`);
          } else {
            // R-F3723 — pdf-parse is an UNDECLARED dynamic import; before this
            // there was no `else` at all, so a missing module was not even logged.
            await reportExtractionOutcome({
              fileName, fileType, moduleMissing: true, extractedChars: 0, groupName, senderName,
            });
          }
        }

        // ── Word DOCX ────────────────────────────────────────────────────
        else if (mime.includes('wordprocessingml') || fileName.toLowerCase().endsWith('.docx')) {
          fileType = 'docx';
          const mammoth = await import('mammoth').then(m => m.default).catch(() => null);
          if (mammoth) {
            const result = await mammoth.extractRawText({ buffer });
            const fullText = (result.value || '').trim();
            const truncated = fullText.length > MAX_DOC_CHARS;
            extractedText = truncated
              ? stampPartialExtraction(
                  fullText.slice(0, MAX_DOC_CHARS),
                  `mammoth returned ${fullText.length} chars; only the first ${MAX_DOC_CHARS} chars are below`,
                )
              : fullText;
            console.log(`[WA] [${groupName}] ${senderName}: 📝 DOCX "${fileName}" (${extractedText.length} chars${truncated ? ` of ${fullText.length} — TRUNCATED` : ''})`);
          } else {
            // R-F3723 — same as the pdf-parse branch: mammoth had no `else`.
            await reportExtractionOutcome({
              fileName, fileType, moduleMissing: true, extractedChars: 0, groupName, senderName,
            });
          }
        }

        // ── Excel (xlsx, xls, xlsm) ──────────────────────────────────────
        // Uses SheetJS `xlsx` package which handles both OOXML (.xlsx/.xlsm)
        // and legacy BIFF (.xls). Each sheet is serialised as CSV so the
        // downstream LLM can still see rows/columns as structured data.
        // Cap at 3 sheets × 500 rows to stay within the 15KB inline budget
        // while surviving real-world supplier lists.
        else if (
          mime.includes('spreadsheetml') ||
          mime.includes('excel') ||
          fileName.toLowerCase().match(/\.(xlsx|xls|xlsm)$/)
        ) {
          fileType = 'xlsx';
          const XLSX = await import('xlsx').then(m => m.default || m).catch(() => null);
          if (XLSX) {
            try {
              // R-F2592 — reject oversized/untrusted xlsx BEFORE parsing (xlsx has
              // no upstream fix for its ReDoS/pollution advisories; XLSX.read parses
              // the whole buffer before the range cap below).
              if (!xlsxSizeOk(buffer)) {
                throw new Error(`xlsx refused: ${buffer?.length ?? 0} bytes > ${MAX_XLSX_BYTES} cap (DoS guard)`);
              }
              const wb = XLSX.read(buffer, { type: 'buffer', cellDates: true });
              const parts = [];
              let truncatedRows = false;
              for (const sheetName of wb.SheetNames.slice(0, 3)) {
                const ws = wb.Sheets[sheetName];
                if (!ws) continue;
                // Cap range to first 500 rows × 40 cols so pathological
                // files don't blow the inline budget.
                const ref = ws['!ref'] || 'A1:A1';
                const range = XLSX.utils.decode_range(ref);
                if (range.e.r > range.s.r + 499) truncatedRows = true;
                range.e.r = Math.min(range.e.r, range.s.r + 499);
                range.e.c = Math.min(range.e.c, range.s.c + 39);
                const csv = XLSX.utils.sheet_to_csv(ws, {
                  range: XLSX.utils.encode_range(range),
                  blankrows: false,
                });
                parts.push(`--- Sheet: ${sheetName} ---\n${csv}`);
              }
              const fullText = parts.join('\n\n').trim();
              const caps = [];
              if (wb.SheetNames.length > 3) caps.push(`only the first 3 of ${wb.SheetNames.length} sheets are below`);
              if (truncatedRows) caps.push('rows past 500 were dropped from at least one sheet');
              if (fullText.length > MAX_DOC_CHARS) caps.push(`text totalled ${fullText.length} chars; only the first ${MAX_DOC_CHARS} are below`);
              extractedText = stampPartialExtraction(fullText.slice(0, MAX_DOC_CHARS), caps.join('; '));
              console.log(`[WA] [${groupName}] ${senderName}: 📊 XLSX "${fileName}" (${extractedText.length} chars, ${wb.SheetNames.length} sheet(s)${caps.length ? ' — TRUNCATED' : ''})`);
            } catch (xe) {
              console.warn(`[WA] xlsx parse failed for "${fileName}":`, xe.message);
            }
          } else {
            // R-F3723 — this branch already warned; a console line is DARK per
            // CLAUDE.md §21a, so the same fact now also reaches the brain.
            await reportExtractionOutcome({
              fileName, fileType, moduleMissing: true, extractedChars: 0, groupName, senderName,
            });
          }
        }

        // ── Plain text files (txt, csv, md, json, xml) ───────────────────
        else if (mime.startsWith('text/') || fileName.toLowerCase().match(/\.(txt|csv|md|json|xml|log)$/)) {
          fileType = 'text';
          const fullText = buffer.toString('utf-8').trim();
          const truncated = fullText.length > MAX_DOC_CHARS;
          extractedText = truncated
            ? stampPartialExtraction(
                fullText.slice(0, MAX_DOC_CHARS),
                `plain-text file totalled ${fullText.length} chars; only the first ${MAX_DOC_CHARS} chars are below`,
              )
            : fullText;
          console.log(`[WA] [${groupName}] ${senderName}: 📃 Text "${fileName}" (${extractedText.length} chars${truncated ? ` of ${fullText.length} — TRUNCATED` : ''})`);
        }

      } catch(e) {
        console.warn(`[WA] Document extraction failed for "${fileName}":`, e.message);
      }

      // ── BACKEND OCR FALLBACK ─────────────────────────────────────────────
      // If local pdf-parse / mammoth / xlsx returned ~nothing (scanned PDFs,
      // image-based docs), send the raw bytes to the backend so its OCR +
      // v3 document_reader chain can rescue the content. Without this, the
      // ATTACHED DOCUMENT block becomes "PARSE FAILED" and ARIA refuses to
      // review per Constitution clause 12.
      if (extractedText.length < 50) {
        try {
          const buffer = docBuffer || await downloadBuffer(docMsg, 'document').catch(() => null);
          const ariaUrl = process.env.ARIA_SERVICE_URL;
          if (buffer && buffer.length > 0 && ariaUrl) {
            const MAX_BYTES = 16 * 1024 * 1024;  // bumped 2026-04-18 from 8MB — PDFs with images commonly exceed 8MB
            const buf = buffer.length > MAX_BYTES ? buffer.subarray(0, MAX_BYTES) : buffer;
            const port = process.env.PORT || 3117;
            const apiToken = process.env.ARIA_API_TOKEN || process.env.ARIA_INTERNAL_TOKEN || '';
            const headers = { 'Content-Type': 'application/json' };
            if (apiToken) headers['Authorization'] = `Bearer ${apiToken}`;
            const url = `${ariaUrl}/api/aria/read-document`;
            console.log(`[WA] Local extraction empty for "${fileName}" — sending ${buf.length} bytes to backend OCR`);
            const r = await fetch(url, {
              method: 'POST', headers,
              body: JSON.stringify({
                content: buf.toString('base64'),
                filename: fileName,
                source: `whatsapp_group:${groupName}:${senderName}`,
                context: caption || `OCR fallback for ${fileName}`,
                encoding: 'base64',
                mimetype: mime,
              }),
              signal: AbortSignal.timeout(180000),
            }).catch(err => ({ ok: false, _err: err?.message }));
            if (r && r.ok) {
              const dr = await r.json().catch(() => ({}));
              const ocrText = (dr.extracted_text || '').trim();
              if (ocrText.length > 50) {
                const truncated = ocrText.length > MAX_DOC_CHARS;
                extractedText = truncated
                  ? stampPartialExtraction(
                      ocrText.slice(0, MAX_DOC_CHARS),
                      `backend OCR returned ${ocrText.length} chars; only the first ${MAX_DOC_CHARS} chars are below`,
                    )
                  : ocrText;
                console.log(`[WA] Backend OCR rescued "${fileName}": ${extractedText.length} chars${truncated ? ` of ${ocrText.length} — TRUNCATED` : ''}`);
              } else {
                console.warn(`[WA] Backend OCR returned no usable text for "${fileName}"`);
              }
            } else {
              console.warn(`[WA] Backend OCR failed for "${fileName}": ${r?._err || `HTTP ${r?.status}`}`);
            }
          }
        } catch (e) {
          console.warn(`[WA] Backend OCR fallback errored for "${fileName}":`, e.message);
        }
      }

      // Store and feed whatever we got
      // R-F3723 — the literal 50 appeared twice here and once in the classifier;
      // USABLE_TEXT_CHARS is now the single source so they cannot drift apart.
      const content = extractedText.length > USABLE_TEXT_CHARS
        ? `[Document: ${fileName}] ${caption ? caption + '\n\n' : ''}${extractedText}`
        : `[Document shared: ${fileName}] ${caption || ''}`.trim();

      // R-F3723 — the terminal outcome, AFTER the backend-OCR rescue attempt.
      // Reaching "metadata only" means ARIA will answer about this document
      // from its filename; that is a delivery degradation and the brain must
      // know (CLAUDE.md §25). Non-degraded outcomes report nothing.
      await reportExtractionOutcome({
        fileName, fileType, moduleMissing: false,
        extractedChars: extractedText.length, groupName, senderName,
      });

      if (content) {
        console.log(`[WA] [${groupName}] ${senderName}: 📎 ${fileName} → ${extractedText.length > USABLE_TEXT_CHARS ? 'content extracted' : 'metadata only'}`);
        store(msg.key.remoteJid, groupName, msg.key.participant, senderName, content, new Date().toISOString());
        await feedToARIA(groupName, senderName, content, 'whatsapp_document', { file_name: fileName, file_type: fileType });

        // Send extracted document text to ARIA research engine for deep
        // learning + structured intelligence pass. The backend now classifies
        // the document (HK NSC1, UK CS01, generic invoice, etc.), pulls a
        // structured JSON of canonical fields, runs red-flag rules, and
        // returns a markdown overview we surface back to the chat so the
        // user gets an instant analyst-grade read of any uploaded document.
        const ariaUrl = process.env.ARIA_SERVICE_URL;
        if (ariaUrl && extractedText.length > 100) {
          _ariaFetch(`${ariaUrl}/api/aria/read-document`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              content: extractedText,
              filename: fileName,
              source: `whatsapp:${groupName}:${senderName}`,
              context: caption || `Shared by ${senderName} in ${groupName}`,
            }),
            signal: AbortSignal.timeout(180000),
          }).then(r => r.ok ? r.json() : null).then(async (result) => {
            if (result?.facts_learned > 0) {
              console.log(`[WA→ARIA] Document analysed: ${fileName} → ${result.facts_learned} facts, ${result.hypotheses_generated || 0} hypotheses`);
            }
            // Surface structured overview back to the WhatsApp chat as a
            // standalone reply. Skipped if the brain returned nothing
            // structured (e.g. ultra-short docs or LLM extraction failed).
            const overview = result?.overview_markdown;
            if (overview && typeof overview === 'string' && overview.length > 40) {
              try {
                await sock.sendMessage(msg.key.remoteJid, {
                  text: `🧠 *ARIA — document overview*\n\n${overview}`.slice(0, 3800),
                });
                console.log(`[WA→ARIA] Overview sent for ${fileName} (form ${result?.doc_intel?.form_code || '?'})`);
              } catch (e) {
                console.warn(`[WA→ARIA] Failed to send overview for ${fileName}:`, e?.message);
              }
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

        // Cap at 16MB — bumped 2026-04-18 from 8MB. Slice BYTES before base64
        // to avoid mid-character corruption.
        const MAX_BYTES = 16 * 1024 * 1024;
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
          const ocrResp = await _ariaFetch(`${ariaUrl}/api/aria/ocr`, {
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
          const docResp = await _ariaFetch(`${ariaUrl}/api/aria/read-document`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              content: extracted.slice(0, MAX_DOC_CHARS),
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

        // ── Store OCR result in the buffer so the next text message can use it
        _storeOCRResult(chatId, extracted, method, senderName);

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
          `4. *Strategic relevance* — does this touch a market we cover, an OEM we work with, or a contact we know? Cite the relationship tier.`,
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

// ── Auth state persistence — survives Seenode deploys via fly.io SQLite ──────
//
// R-F388 (2026-05-12): migrated off Upstash. The Baileys auth bundle is now
// persisted through fly.io's /api/aria/wa-auth/{backup,restore} endpoints,
// which write into the SQLite state_store on the fly.io persistent volume.
// This closes the last Upstash dependency on seenode while keeping the
// cross-deploy restore guarantee operators rely on.
//
// Auth: bearer ARIA_INTERNAL_TOKEN (same token used for every fly.io call
// from seenode). Backup URL resolution mirrors the routing used elsewhere
// in this codebase (ARIA_BRAIN_URL → ARIA_FLY_URL → BRAIN_URL → default
// aria-intel.fly.dev).
//
// The function names backupAuthToRedis / restoreAuthFromRedis are kept for
// callsite stability — every caller in this file already references them.

const _flyAuthUrl = () => {
  const base = (process.env.ARIA_BRAIN_URL
              || process.env.ARIA_FLY_URL
              || process.env.BRAIN_URL
              || 'https://aria-intel.fly.dev').replace(/\/$/, '');
  return base;
};

const _flyAuthToken = () =>
  (process.env.ARIA_API_TOKEN || process.env.ARIA_INTERNAL_TOKEN || '').trim();

const _flyAuthOk = () => !!_flyAuthToken();

async function backupAuthToRedis() {
  if (suppressAuthBackup) return;  // skip during 440 cleanup
  if (!_flyAuthOk()) {
    console.warn('[WA Listener] No ARIA_INTERNAL_TOKEN — auth backup disabled');
    return;
  }
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

    const res = await fetch(`${_flyAuthUrl()}/api/aria/wa-auth/backup`, {
      method: 'POST',
      headers: {
        Authorization:  `Bearer ${_flyAuthToken()}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ files: authBundle }),
      signal: AbortSignal.timeout(15000),
    });
    if (!res.ok) {
      console.warn(`[WA Listener] Auth backup failed: fly.io ${res.status}`);
      return;
    }
    const data = await res.json().catch(() => ({}));
    console.log(`[WA Listener] Auth backed up to fly.io (${data.files || Object.keys(authBundle).length}/${allFiles.length} files, ${Math.round((data.bytes || totalSize)/1024)}KB)`);
  } catch(e) {
    console.warn('[WA Listener] Auth backup failed:', e.message);
  }
}

async function restoreAuthFromRedis() {
  if (!_flyAuthOk()) {
    console.warn('[WA Listener] No ARIA_INTERNAL_TOKEN — cannot restore auth');
    return false;
  }
  try {
    console.log('[WA Listener] Attempting auth restore from fly.io...');
    const res = await fetch(`${_flyAuthUrl()}/api/aria/wa-auth/restore`, {
      method: 'GET',
      headers: { Authorization: `Bearer ${_flyAuthToken()}` },
      signal: AbortSignal.timeout(15000),
    });
    if (!res.ok) {
      console.warn(`[WA Listener] Auth restore failed: fly.io ${res.status}`);
      return false;
    }
    const data = await res.json().catch(() => null);
    if (!data || !data.ok) {
      console.log('[WA Listener] No auth bundle on fly.io (cold start, QR scan required)');
      return false;
    }
    const authBundle = data.files || {};
    const fileNames = Object.keys(authBundle);
    if (!fileNames.length) {
      console.warn('[WA Listener] fly.io returned empty bundle');
      return false;
    }
    fs.mkdirSync(AUTH_DIR, { recursive: true });
    let restored = 0;
    for (const [f, content] of Object.entries(authBundle)) {
      if (typeof content !== 'string') continue;
      try {
        fs.writeFileSync(path.join(AUTH_DIR, f), content, 'utf8');
        restored++;
      } catch(e) {
        console.warn(`[WA Listener] Failed to write ${f}:`, e.message);
      }
    }
    console.log(`[WA Listener] Auth restored from fly.io (${restored}/${fileNames.length} files) — no QR scan needed`);
    return restored > 0;
  } catch(e) {
    console.warn('[WA Listener] Auth restore failed:', e.message);
    return false;
  }
}

// Delete the persisted bundle. Called when WhatsApp invalidates auth (440)
// to prevent the next cold-boot restore from re-painting stale credentials.
async function _deleteAuthBundle() {
  if (!_flyAuthOk()) return;
  try {
    await fetch(`${_flyAuthUrl()}/api/aria/wa-auth/backup`, {
      method: 'DELETE',
      headers: { Authorization: `Bearer ${_flyAuthToken()}` },
      signal: AbortSignal.timeout(10000),
    });
  } catch {}
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
// ── Zombie-socket watchdog ───────────────────────────────────────────────────
// Fires every WATCHDOG_INTERVAL_MS. If we've been silent longer than
// WATCHDOG_QUIET_MS, actively probe the socket with groupFetchAllParticipating
// (cheap call that round-trips over the same WS that delivers inbound). A
// zombie socket will hang or error here; we then tear down and re-enter
// startListener() which rebuilds the socket from scratch.
const WATCHDOG_INTERVAL_MS = 2 * 60 * 1000;   // 2 min
const WATCHDOG_QUIET_MS    = 5 * 60 * 1000;   // 5 min of no peer msgs → probe
const WATCHDOG_PROBE_TIMEOUT_MS = 15000;      // 15 s
// F109: ceiling on probe-only keepalive cycles. Default 6 ≈ 30 min of probes
// succeeding while no peer messages arrive → assume inbound stream is dead and
// rebuild the socket. Reset is non-destructive (preserves auth), so a false
// positive on a genuinely-quiet group costs only a 1s reconnect.
const MAX_PROBES_WITHOUT_PEER = parseInt(
  process.env.ARIA_WA_MAX_PROBES_WITHOUT_PEER || '6', 10
);

async function _watchdogTick() {
  if (!ENABLED || !sock || !isConnected || watchdogReconnecting) return;
  const quietMs = Date.now() - lastInboundAt;
  if (quietMs < WATCHDOG_QUIET_MS) return;  // group is chatty enough — skip probe

  watchdogLastProbeAt = Date.now();
  let probeOk = false;
  try {
    await Promise.race([
      sock.groupFetchAllParticipating(),
      new Promise((_, rej) =>
        setTimeout(() => rej(new Error('watchdog probe timeout')), WATCHDOG_PROBE_TIMEOUT_MS)
      ),
    ]);
    probeOk = true;
    watchdogLastProbeOk = true;
    consecutiveProbesWithoutPeer++;
    // F109: probe-only keepalive ceiling. The probe round-trips over the WS
    // outbound half, so it succeeds even when the inbound half (peer message
    // delivery) is dead — exactly the failure that left ARIA silent for 20+
    // min on 2026-04-30. Force a rebuild after MAX consecutive probe-only
    // cycles. Counter is reset to 0 on every real peer message in upsert.
    if (consecutiveProbesWithoutPeer >= MAX_PROBES_WITHOUT_PEER) {
      console.warn(
        `[WA Listener] Watchdog: ${consecutiveProbesWithoutPeer} consecutive probes ` +
        `succeeded with no peer messages (~${Math.round(quietMs / 60000)}m silent) — ` +
        `inbound stream presumed dead (F109), forcing reconnect`
      );
      consecutiveProbesWithoutPeer = 0;
      watchdogReconnecting = true;
      isConnected = false;
      try { sock.ev.removeAllListeners(); } catch {}
      try { sock.end(); } catch {}
      sock = null;
      setTimeout(() => {
        startListener().catch(err =>
          console.error('[WA Listener] F109-triggered restart failed:', err.message)
        );
      }, 3000);
      setTimeout(() => { watchdogReconnecting = false; }, 90000);
      return;
    }
    // Treat a successful probe as activity — otherwise we'd probe every cycle
    // on a quiet-but-healthy socket.
    lastInboundAt = Date.now();
  } catch (e) {
    watchdogLastProbeOk = false;
    console.warn(
      `[WA Listener] Watchdog: socket unresponsive after ${Math.round(quietMs / 1000)}s silence ` +
      `(${e.message}) — forcing reconnect`
    );
    watchdogReconnecting = true;
    isConnected = false;
    try { sock.ev.removeAllListeners(); } catch {}
    try { sock.end(); } catch {}
    sock = null;
    setTimeout(() => {
      startListener().catch(err =>
        console.error('[WA Listener] Watchdog-triggered restart failed:', err.message)
      );
    }, 3000);
    // Safety net: clear the flag after 90s even if startListener() never
    // reaches 'open' (otherwise a failed reconnect leaves the watchdog
    // permanently disarmed). The 'open' handler clears it sooner on success.
    setTimeout(() => { watchdogReconnecting = false; }, 90000);
  }
  if (probeOk) {
    console.log(`[WA Listener] Watchdog: probe ok after ${Math.round(quietMs / 1000)}s silence`);
  }
}

function _startWatchdog() {
  if (watchdogTimer) return;  // idempotent
  watchdogTimer = setInterval(() => {
    _watchdogTick().catch(e => console.warn('[WA Listener] Watchdog tick error:', e.message));
  }, WATCHDOG_INTERVAL_MS);
  // Don't keep the Node event loop alive just for the watchdog.
  if (typeof watchdogTimer?.unref === 'function') watchdogTimer.unref();
  console.log(`[WA Listener] Watchdog armed — probe after ${WATCHDOG_QUIET_MS / 60000}m of silence`);
}

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

  // Close previous socket if any.
  // R-F461: removeAllListeners FIRST, then end(). Skipping the removeAllListeners
  // call leaves the old sock's connection.update handler attached — Baileys
  // emits one or more residual `close` events during shutdown which each
  // schedule another setTimeout(startListener), producing a thundering-herd
  // reconnect storm (observed 2026-05-14 05:35 BST: 1-2s reconnect cycles,
  // "Auth backed up" spam, auth file count climbing 31→47 as each parallel
  // socket negotiated fresh pre-keys before being killed by the next).
  if (sock) {
    try { sock.ev.removeAllListeners(); } catch {}
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
    await _deleteAuthBundle();
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
      // R-F60: capture latest QR string for browser-side rendering via
      // /api/wa-listener/qr.png. Stored in module scope so the HTTP
      // endpoint can read whatever the most recent QR is, not just
      // the first one (which expires in ~20s).
      latestQrString = qr;
      latestQrAt = Date.now();
      console.log('\n[WA Listener] ══════════════════════════════════════════');
      if (!qrPrinted) {
        console.log('[WA Listener] SCAN THIS QR CODE with your Portuguese number:');
        console.log('[WA Listener]   WhatsApp Business → Settings → Linked Devices → Link a Device');
        console.log('[WA Listener]   OR open in a browser: /api/wa-listener/qr  (R-F60)');
      } else {
        console.log('[WA Listener] QR REFRESHED — scan this new one:');
      }
      console.log('[WA Listener] ══════════════════════════════════════════\n');
      _qrcode.generate(qr, { small: true });
      // R-F63: also print the raw QR string so the operator can paste it
      // into any external QR generator (qr-code-generator.com etc.) as a
      // fallback when the in-terminal ASCII rendering doesn't scan
      // cleanly off-screen. The string itself is short-lived (Baileys
      // rotates it every ~20s) so the leak risk is bounded — and any
      // attacker would need to scan it before WhatsApp's TTL expires.
      console.log('\n[WA Listener] Raw QR string (R-F63 — paste into any QR generator if ASCII won\'t scan):');
      console.log('[WA Listener]   ' + qr);
      console.log('\n[WA Listener] Waiting for scan...\n');
      qrPrinted = true;
    }

    if (connection === 'open') {
      isConnected    = true;
      startedAt      = new Date().toISOString();
      qrPrinted      = false;
      // R-F60: connection live — clear the captured QR so the PNG
      // endpoint correctly reports "no QR pending" until the next
      // logout/auth-invalidation cycle.
      latestQrString = null;
      latestQrAt     = 0;
      reconnectDelay = 5000;
      suppressAuthBackup = false;  // safe to backup again
      lastInboundAt  = Date.now();   // watchdog: quiet grace period starts now
      watchdogReconnecting = false;  // fresh connection — clear any prior restart flag
      consecutiveProbesWithoutPeer = 0;  // F109: fresh socket starts with clean ceiling
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
        // 2. Delete the fly.io backup so the next cold-boot doesn't re-paint
        await _deleteAuthBundle();
        console.log('[WA Listener] fly.io auth bundle deleted');
        // 3. Delete local auth
        try { fs.rmSync(AUTH_DIR, { recursive: true, force: true }); } catch {}
        console.log('[WA Listener] Auth fully cleared — QR required');
        // 4. Restart after delay (suppressAuthBackup stays true until successful connect)
        setTimeout(startListener, 15000);
      } else {
        // R-F461: tear down sock + listeners immediately so residual events
        // (further close/error emissions during Baileys' own shutdown) can't
        // re-enter this handler and pile up parallel startListener timers.
        // Mirrors the logout branch above. Without this, a single
        // restart-required close (code 515) cascades into a thundering-herd
        // reconnect storm — see incident 2026-05-14 05:35 BST.
        if (sock) {
          try { sock.ev.removeAllListeners(); } catch {}
          try { sock.end(); } catch {}
          sock = null;
        }
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

  // ── Reactions: separate event in newer Baileys versions ─────────────────
  // Baileys v2.3000+ delivers reactions on a dedicated `messages.reaction`
  // event. Outer key is the reaction message itself (carries reactor JID
  // in `participant`); inner key under r.reaction is the message being
  // reacted to. Targets are matched against the local _sentMsgIds ledger
  // instead of the unreliable fromMe flag. Logs the raw payload on first
  // few hits so future schema drift is easy to diagnose.
  let _reactionDebugLogged = 0;
  sock.ev.on('messages.reaction', async (reactions) => {
    if (!Array.isArray(reactions)) return;
    const ariaServiceUrl = process.env.ARIA_SERVICE_URL;

    // Debug log the first 3 raw reaction events so we can see the actual
    // shape Baileys is delivering. Helps catch version-specific schema drift.
    if (_reactionDebugLogged < 3) {
      _reactionDebugLogged++;
      try {
        console.log('[WA Reaction DEBUG] raw payload:', JSON.stringify(reactions, null, 2).slice(0, 1500));
      } catch {/* ignore stringify errors */}
    }

    for (const r of reactions) {
      try {
        // Field semantics, confirmed empirically against Baileys v2.3000+:
        //   r.key            → the TARGET message (the one being reacted to),
        //                       hoisted to the top level for convenience.
        //                       This is the field that matches our sent ledger.
        //   r.reaction.key   → the reaction message's OWN protobuf key
        //                       (its own message id, NOT the target). Useless
        //                       for matching against the sent ledger.
        // Earlier guess had this inverted; the success log from the legacy
        // messages.upsert path proved that r.key.id is what matches.
        const outerKey = r.key || {};
        const innerKey = r.reaction?.key || {};
        const targetMsgId = outerKey.id || '';
        const targetChatId = outerKey.remoteJid || innerKey.remoteJid || '';
        if (!targetMsgId || !targetChatId) {
          console.log('[WA Reaction] skipping — missing target id/chat');
          continue;
        }
        if (!_sentMsgIds.has(targetMsgId)) {
          // Reaction is on a message ARIA didn't send (a teammate, or the
          // user themselves, or an old ARIA reply from before this process
          // started). Not feedback for us — drop quietly.
          console.log(`[WA Reaction] skipping — target ${targetMsgId} not in sent ledger`);
          continue;
        }
        // Reactor identity: in messages.reaction events the reactor is the
        // sender of the reaction message, which lives on the inner key's
        // participant field (NOT the outer key — that's the participant of
        // the target message, i.e. ARIA itself).
        const reactorJid = innerKey.participant || innerKey.remoteJid || outerKey.participant || '';
        const reactorName = reactorJid.split('@')[0] || 'unknown';
        const emoji = r.reaction?.text || '';
        console.log(`[WA Reaction] ${reactorName} reacted ${emoji || '(removed)'} to ARIA msg ${targetMsgId}`);
        if (!ariaServiceUrl) {
          console.warn('[WA Reaction] ARIA_SERVICE_URL not configured — cannot record');
          continue;
        }
        const resp = await _ariaFetch(`${ariaServiceUrl}/api/aria/feedback`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            chat_id: targetChatId,
            msg_id: targetMsgId,
            emoji,
            reactor: reactorName,
            reactor_jid: reactorJid,
          }),
          signal: AbortSignal.timeout(8000),
        });
        if (resp.ok) {
          const result = await resp.json().catch(() => null);
          if (result && result.ok && result.record) {
            console.log(`[WA Reaction] Recorded ${result.record.sentiment} feedback ${result.record.id}`);
          }
        } else {
          console.warn(`[WA Reaction] feedback POST returned ${resp.status}`);
        }
      } catch (e) {
        console.warn('[WA Reaction] handler failed:', e.message);
      }
    }
  });

  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    // Proof-of-life for the watchdog. Only peer messages (fromMe=false) count.
    // Our own outbound sends also fire messages.upsert with fromMe=true
    // (server echoes back for multi-device sync); if those bumped
    // lastInboundAt, a proactive alert poster like ARIA would mask the exact
    // failure mode the watchdog exists to catch — outbound channel healthy,
    // inbound channel dead. Observed 2026-04-24: listener sent sweep alerts
    // on cadence while user messages were never delivered, watchdog never
    // probed because fromMe echoes kept bumping the timer.
    if (messages.some(m => !m.key?.fromMe)) {
      lastInboundAt = Date.now();
      consecutiveProbesWithoutPeer = 0;  // F109: real peer activity clears probe-only ceiling
    }

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

      // ── Reaction handling moved to the dedicated `messages.reaction`
      // event above. In Baileys v2.3000+ both events fire for every
      // reaction, so handling the reactionMessage protobuf here as well
      // produced double-counted feedback. Worse, this branch was using
      // raw fetch() with no bearer token, so after the 2026-04-09 auth
      // rollout it silently 401'd against fly.io. Removed entirely; the
      // messages.reaction handler is now the single canonical path.
      if (msg.message?.reactionMessage) {
        continue;  // reaction-only payload, no text to process
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

      // R-F222 (2026-05-11) — surface drops for unhandled media types
      // so the operator knows they exist. WhatsApp voice notes
      // (audioMessage / pttMessage), video bodies (videoMessage —
      // only caption is captured above), stickers, and polls are
      // currently NOT processed by any handler. Until full STT /
      // frame-OCR ship, log + push a knowledge gap so the dashboard
      // shows the drop.
      const droppedMediaTypes = [];
      if (msg.message?.audioMessage) droppedMediaTypes.push('audio');
      if (msg.message?.pttMessage) droppedMediaTypes.push('voice_note');
      if (msg.message?.videoMessage && !msg.message?.videoMessage?.caption) droppedMediaTypes.push('video_body_no_caption');
      if (msg.message?.stickerMessage) droppedMediaTypes.push('sticker');
      if (msg.message?.pollCreationMessage || msg.message?.pollUpdateMessage) droppedMediaTypes.push('poll');
      if (droppedMediaTypes.length) {
        console.warn(`[WA] [${groupName}] DROPPED ${droppedMediaTypes.join(',')} from ${senderName} — no handler wired. R-F222 visibility only; transcription/OCR not yet shipped.`);
        // Best-effort signal to ARIA brain so dashboard / capability gap surfaces the drop
        try {
          const _fetch = (typeof fetch === 'function') ? fetch : null;
          if (_fetch && process.env.ARIA_INGEST_URL) {
            _fetch(process.env.ARIA_INGEST_URL.replace(/\/ingest$/, '/brain/absorb'), {
              method: 'POST',
              headers: {
                'Content-Type': 'application/json',
                'Authorization': 'Bearer ' + (process.env.ARIA_INTERNAL_TOKEN || ''),
              },
              body: JSON.stringify({
                module: 'whatsapp_listener',
                summary: `R-F222: dropped ${droppedMediaTypes.join(',')} from WA (${groupName})`,
                gap_type: 'wa_unhandled_media',
                gap_detail: droppedMediaTypes.join(','),
                success: false,
              }),
            }).catch(() => {});
          }
        } catch (_e) { /* never block the listener */ }
      }

      if (hasMedia) {
        // Process media in background — don't block text processing.
        // EXCEPTION: when the same message ALSO mentions ARIA, skip the
        // background processing here. handleAriaMention will await
        // processMedia inline so the parsed text is in messageStore by
        // the time we build the chat envelope. Without this gate the
        // document was processed in a separate channel that the chat
        // path never read, and ARIA would hallucinate a "review" from
        // prior conversation context (constitution clause 12, past
        // incident 2026-04-09 on the CDL Hotels amendment).
        if (!isAriaMentioned(text)) {
          processMedia(msg, sock, groupName, senderName).then(extracted => {
            if (extracted) messagesHeard++;
          }).catch(() => {});
        }
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

      // Channel mirror — claim ledger + deception screen for counterparty / internal groups.
      // Silent: never replies. Gated by ARIA_MIRROR_GROUPS + ARIA_COUNTERPARTY_CONTACTS env.
      mirrorChannel({
        chatId, senderJid, groupName, senderName, text,
        messageId: msg.key.id, timestamp: ts,
      }).catch(() => {});

      // ── Command detection (/screen, /classify, /teach, etc.) ──────────
      const cmdMatch = text.match(COMMAND_RE);
      if (cmdMatch) {
        const _cmdName = (cmdMatch[1] || '').toLowerCase();
        // Long-running commands: send a progress reply BEFORE awaiting so the
        // user knows ARIA is engaged and won't keep retrying. Without this,
        // /investigate sits silent for up to 4 minutes then aborts. Mirrors
        // the progress-message logic in handleAriaMention().
        const _longCmds = new Set(['investigate', 'network', 'groupsummary', 'crawl', 'pmesii', 'report']);
        if (_longCmds.has(_cmdName)) {
          await sendMessage(
            chatId,
            `*ARIA* — On it. \`/${_cmdName}\` involves multi-source research; this can take 1–4 minutes. I'll reply here when ready — no need to re-send.`,
            msg,
          ).catch(() => {});
        }
        try {
          const reply = await handleCommand(cmdMatch[1], cmdMatch[2], senderName, senderJid,
            (text) => sendMessage(chatId, `*ARIA* — ${text}`).catch(() => {}));
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

      // ── Option B — social greeting fast path (no LLM) ─────────────────
      // If the message is just a pleasantry addressed to ARIA, reply with a
      // short honest acknowledgement. Keeps the group conversation natural
      // without running the full chat pipeline + without impersonating.
      if (isSocialGreeting(text)) {
        const reply = socialReplyFor(text);
        sendMessage(chatId, reply, msg).catch(() => {});
        continue;
      }

      // ── ARIA mention detection — reply when mentioned ─────────────────
      if (isAriaMentioned(text)) {
        handleAriaMention(msg, chatId, groupName, senderName, text).catch(e => {
          console.warn('[WA Listener] ARIA reply failed:', e.message);
        });
        continue;
      }

      // ── Smart auto-respond on compliance/deal/risk keywords ───────────
      // BUG-FIX 2026-04-12: skip compliance trigger for document captions.
      // NDA/legal docs naturally contain "sanctions", "end-user", etc. which
      // caused false-positive auto-screening on every legal doc upload.
      if (AUTO_RESPOND && !hasMedia) {
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
          _ariaFetch(`${ariaUrl}/api/aria/read`, {
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

  // R-F64: QR stall watcher. Baileys is supposed to rotate the pairing
  // QR every ~20s while in pending state, but live evidence 2026-05-09
  // showed it sometimes emits ONE QR and never refreshes — operator's
  // 20s scan window expires and nothing recovers. Probe every 10s; if
  // a stale QR is more than 30s old AND we're not connected AND not
  // already restarting, kill the socket and re-init. Auth files are
  // preserved (different from /force-qr which nukes auth).
  if (!qrStallTimer) {
    qrStallTimer = setInterval(() => {
      try {
        if (isConnected) return;
        if (!latestQrAt) return;
        if (watchdogReconnecting) return;
        const ageMs = Date.now() - latestQrAt;
        if (ageMs < 30000) return;
        qrStallRestarts += 1;
        console.warn(
          '[WA Listener] R-F64: QR stalled (' + Math.round(ageMs / 1000) +
          's old, no rotation, restarts=' + qrStallRestarts +
          ') — restarting socket to force fresh QR cycle'
        );
        watchdogReconnecting = true;
        try { if (sock) { sock.ev.removeAllListeners(); sock.end(); } } catch {}
        sock = null;
        latestQrString = null;
        latestQrAt = 0;
        setTimeout(() => {
          startListener().catch(err =>
            console.error('[WA Listener] QR-stall restart failed:', err.message)
          );
          watchdogReconnecting = false;
        }, 2000);
      } catch (err) {
        console.warn('[WA Listener] QR stall watcher error:', err.message);
      }
    }, 10000);
  }

  // Arm the zombie-socket watchdog — survives across reconnects.
  _startWatchdog();

  // Status
  app.get('/api/wa-listener/status', _waRequireAuth, (_req, res) => {
    res.json({
      connected:      isConnected,
      started_at:     startedAt,
      messages_heard: messagesHeard,
      messages_sent:  messagesSent,
      reply_enabled:  WA_REPLY_ENABLED,
      target_groups:  TARGET_GROUPS.length ? TARGET_GROUPS : 'ALL',
      group_names:    Object.fromEntries(groupNames),
      memory_store:   messageStore.length,
      last_inbound_at: lastInboundAt ? new Date(lastInboundAt).toISOString() : null,
      silent_seconds:  lastInboundAt ? Math.round((Date.now() - lastInboundAt) / 1000) : null,
      watchdog: {
        armed:            !!watchdogTimer,
        reconnecting:     watchdogReconnecting,
        last_probe_at:    watchdogLastProbeAt ? new Date(watchdogLastProbeAt).toISOString() : null,
        last_probe_ok:    watchdogLastProbeOk,
        quiet_threshold_s: WATCHDOG_QUIET_MS / 1000,
        // F109 surface: how many probe-only keepalive cycles have run since
        // the last real peer message. When this reaches max_probes_without_peer
        // the watchdog forces a reconnect (inbound stream presumed dead).
        consecutive_probes_without_peer: consecutiveProbesWithoutPeer,
        max_probes_without_peer:         MAX_PROBES_WITHOUT_PEER,
        // R-F64: QR-stall auto-restart counter. Increments every time the
        // pending-QR watcher detected a >30s stale QR and forced a fresh
        // socket re-init.
        qr_stall_restarts:               qrStallRestarts,
      },
      note:           isConnected
        ? 'ARIA is listening and replying to WhatsApp groups'
        : 'Not connected — check logs for QR code',
    });
  });

  // Force a clean reconnect without redeploying the service. Operator tool
  // for the zombie-socket / silent-listener failure mode when the watchdog's
  // 10-minute quiet threshold is too slow. Preserves auth (Redis + local
  // files untouched) — a genuinely-logged-out session will re-enter the
  // logout branch via Baileys on reconnect and trigger the QR reset path.
  app.post('/api/wa-listener/reset', _waRequireAuth, async (_req, res) => {
    const wasConnected = isConnected;
    isConnected = false;
    watchdogReconnecting = true;
    try { if (sock) { sock.ev.removeAllListeners(); sock.end(); } } catch {}
    sock = null;
    setTimeout(() => {
      startListener().catch(err =>
        console.error('[WA Listener] Reset-triggered restart failed:', err.message)
      );
    }, 1000);
    setTimeout(() => { watchdogReconnecting = false; }, 90000);
    console.log(`[WA Listener] Manual reset requested (was ${wasConnected ? 'connected' : 'disconnected'}) — reconnecting...`);
    res.json({ ok: true, was_connected: wasConnected, reconnect_in_ms: 1000 });
  });

  // Nuke auth state and force a fresh QR pairing. Use when:
  //   - the user can't scan because WhatsApp thinks ARIA is already linked
  //     (old session still presents server-side; we need a brand-new device)
  //   - ghost session that /reset can't fix because auth itself is the problem
  //   - any case where you want a guaranteed-clean re-pair
  // Mirrors the 440/loggedOut branch of connection.update (waListener.mjs ~3577):
  // suppresses backup, sets authInvalidatedAt to skip Redis restore on next
  // start, deletes Redis key, removes local auth dir, restarts. Next QR is
  // a brand new identity — peer scan creates a fresh linked-device entry.
  app.post('/api/wa-listener/force-qr', _waRequireAuth, async (_req, res) => {
    console.log('[WA Listener] Manual force-QR requested — nuking auth and restarting...');
    suppressAuthBackup = true;
    authInvalidatedAt = Date.now();
    isConnected = false;
    watchdogReconnecting = true;
    try { if (sock) { sock.ev.removeAllListeners(); sock.end(); } } catch {}
    sock = null;
    let redisCleared = false;
    try { await _deleteAuthBundle(); redisCleared = true; }
    catch (e) { console.warn('[WA Listener] fly.io auth delete failed:', e.message); }
    let localCleared = false;
    try { fs.rmSync(AUTH_DIR, { recursive: true, force: true }); localCleared = true; }
    catch (e) { console.warn('[WA Listener] Local auth delete failed:', e.message); }
    console.log('[WA Listener] Auth fully cleared — restarting for fresh QR');
    setTimeout(() => {
      startListener().catch(err =>
        console.error('[WA Listener] Force-QR-triggered restart failed:', err.message)
      );
    }, 2000);
    setTimeout(() => { watchdogReconnecting = false; }, 90000);
    res.json({
      ok: true,
      redis_cleared: redisCleared,
      local_cleared: localCleared,
      next_step: 'Open /api/wa-listener/qr in a browser (auth via ?token=<INT_TOKEN>) to see the live QR as a high-res PNG; or watch seenode logs for the ASCII QR. WhatsApp Business → Linked Devices → Link a Device.',
    });
  });

  // R-F60: latest QR rendered as a PNG. Browser-friendly alternative to
  // scanning the ASCII QR off the seenode log stream — opens cleanly on
  // the operator's monitor, refreshes every 20s alongside the QR cycle.
  //
  // Auth: accepts either Bearer header (programmatic callers) OR a
  // ?token= query param (browser bar — no header to type). Token is the
  // ARIA_INTERNAL_TOKEN. The query-param path is QR-specific; no other
  // wa-listener endpoint exposes auth via URL.
  function _waQrAuthOK(req) {
    if (!INT_TOKEN) return false;
    // R-F3833 — same forgery as _waRequireAuth, and this one hands over the
    // WhatsApp LINKING QR: a forged header let a 6PN peer link ARIA's WhatsApp
    // to their own handset. Also had no kill-switch check; now inherits it.
    if (localhostBypassAllowed(req)) return true;
    const headerAuth = (req.headers.authorization || '').replace(/^Bearer\s+/i, '');
    const queryToken = (req.query?.token || '').toString();
    return headerAuth === INT_TOKEN || queryToken === INT_TOKEN;
  }

  app.get('/api/wa-listener/qr.png', async (req, res) => {
    if (!_waQrAuthOK(req)) return res.status(401).json({ error: 'Unauthorized — pass ?token=<INT_TOKEN> or Bearer header' });
    if (isConnected) return res.status(409).json({ error: 'Already connected — no QR pending', connected_since: startedAt });
    if (!latestQrString) return res.status(404).json({ error: 'No QR captured yet — listener may still be starting up. Refresh in 5s.' });
    let qrModule;
    try {
      qrModule = (await import('qrcode')).default;
    } catch (e) {
      return res.status(503).json({ error: 'qrcode npm package unavailable: ' + e.message });
    }
    try {
      const png = await qrModule.toBuffer(latestQrString, {
        type: 'png',
        errorCorrectionLevel: 'M',
        scale: 8,
        margin: 2,
        color: { dark: '#000000ff', light: '#ffffffff' },
      });
      res.setHeader('Content-Type', 'image/png');
      res.setHeader('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0');
      res.setHeader('X-WA-QR-Captured-At', String(latestQrAt));
      res.send(png);
    } catch (e) {
      res.status(500).json({ error: 'QR render failed: ' + e.message });
    }
  });

  app.get('/api/wa-listener/qr', (req, res) => {
    if (!_waQrAuthOK(req)) return res.status(401).send('<h1>Unauthorized</h1><p>Append <code>?token=&lt;INT_TOKEN&gt;</code> to the URL.</p>');
    const tok = (req.query?.token || '').toString();
    const tokQs = tok ? '?token=' + encodeURIComponent(tok) : '';
    const status = isConnected ? 'connected' : (latestQrString ? 'qr_pending' : 'starting');
    const ageMs = latestQrAt ? Date.now() - latestQrAt : null;
    res.setHeader('Content-Type', 'text/html; charset=utf-8');
    res.setHeader('Cache-Control', 'no-store');
    res.send(`<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>ARIA — WhatsApp link QR</title>
<style>
  body{margin:0;background:#0c0919;color:rgba(255,255,255,0.85);font-family:system-ui,-apple-system,Segoe UI,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh}
  .wrap{max-width:520px;padding:32px;text-align:center}
  h1{font-size:22px;font-weight:800;margin:0 0 8px;color:#fff;letter-spacing:-0.01em}
  p{font-size:14px;line-height:1.6;color:rgba(255,255,255,0.6);margin:6px 0}
  .qr{background:#fff;padding:14px;border-radius:14px;display:inline-block;margin:18px 0;min-width:280px;min-height:280px}
  .qr img{display:block;width:100%;max-width:380px;height:auto;image-rendering:pixelated}
  .pill{display:inline-block;font-size:11px;font-weight:700;letter-spacing:1px;text-transform:uppercase;padding:4px 10px;border-radius:5px;margin-bottom:8px}
  .pill.ok{background:rgba(34,197,94,0.15);color:#4ade80}
  .pill.pending{background:rgba(245,158,11,0.15);color:#fbbf24}
  .pill.starting{background:rgba(124,58,237,0.15);color:#c4b5fd}
  .age{display:inline-block;font-size:12px;font-weight:600;padding:3px 9px;border-radius:5px;margin:0 0 12px;background:rgba(255,255,255,0.04);color:rgba(255,255,255,0.55)}
  .age.warn{background:rgba(245,158,11,0.18);color:#fbbf24}
  .age.stale{background:rgba(239,68,68,0.18);color:#fca5a5}
  .btnrow{display:flex;gap:8px;justify-content:center;flex-wrap:wrap;margin-top:14px}
  .btn{padding:9px 16px;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;transition:all .15s;border:1px solid rgba(255,255,255,0.12);background:rgba(255,255,255,0.04);color:rgba(255,255,255,0.85);text-decoration:none;display:inline-flex;align-items:center;gap:6px}
  .btn:hover{border-color:rgba(124,58,237,0.5);color:#fff}
  .btn.primary{background:linear-gradient(135deg,#7c3aed,#2563eb);border-color:transparent;color:#fff}
  .btn.primary:hover{box-shadow:0 2px 14px rgba(124,58,237,0.4)}
  .btn:disabled{opacity:0.5;cursor:not-allowed}
  .meta{font-size:11px;color:rgba(255,255,255,0.35);margin-top:18px}
  details{margin-top:12px;text-align:left;font-size:12px;color:rgba(255,255,255,0.45)}
  details summary{cursor:pointer;color:rgba(255,255,255,0.55)}
  code{background:rgba(124,58,237,0.12);border:1px solid rgba(124,58,237,0.2);padding:1px 5px;border-radius:3px;font-size:11px;word-break:break-all}
</style>
</head><body>
<div class="wrap">
  <div class="pill ${status === 'connected' ? 'ok' : status === 'qr_pending' ? 'pending' : 'starting'}">${status === 'connected' ? 'Already linked' : status === 'qr_pending' ? 'QR ready — scan now' : 'Listener starting…'}</div>
  <h1>WhatsApp link</h1>
  ${status === 'connected'
    ? '<p>ARIA is already connected to WhatsApp. No QR is pending.</p>'
    : '<p>WhatsApp Business → Settings → Linked Devices → Link a Device. Then point your camera at the QR below.</p>' +
      '<div id="age-pill" class="age">Age: <span id="age-val">—</span></div><br>' +
      '<div class="qr"><img id="qr" src="/api/wa-listener/qr.png' + tokQs + '" alt="WhatsApp QR"></div>' +
      '<p>QR refreshes every ~20 seconds. The image auto-reloads every 3s.</p>' +
      '<div class="btnrow">' +
        '<button class="btn primary" id="btn-force">↻ Force new QR cycle</button>' +
        '<a class="btn" href="/api/wa-listener/qr.png' + tokQs + '" target="_blank">Open PNG in new tab</a>' +
      '</div>' +
      '<details><summary>QR won\'t scan? Use a fallback</summary>' +
        '<p style="margin-top:8px">Open the seenode log stream and find the line:<br><code>[WA Listener] Raw QR string (R-F63 — paste into any QR generator if ASCII won\'t scan):</code><br>Copy the long string on the next line and paste it into <a href="https://www.qr-code-generator.com/" target="_blank" style="color:#c4b5fd">qr-code-generator.com</a> (Text option). Generate, then scan the rendered PNG from your phone.</p>' +
        '<p>If the listener has been silent for &gt;30s click "Force new QR cycle" above.</p>' +
      '</details>'
  }
  <div class="meta">Captured: <span id="ts">${latestQrAt ? new Date(latestQrAt).toISOString() : '—'}</span></div>
</div>
<script>
  var TOK_QS = ${JSON.stringify(tokQs)};
  var CAPTURED_AT = ${latestQrAt || 0};
  function fmtAge(ms) {
    if (!ms || ms < 0) return '—';
    var s = Math.round(ms / 1000);
    if (s < 60) return s + 's';
    return Math.round(s / 60) + 'm ' + (s % 60) + 's';
  }
  function refreshAge() {
    var el = document.getElementById('age-val');
    var pill = document.getElementById('age-pill');
    if (!el || !pill) return;
    if (!CAPTURED_AT) {
      el.textContent = '—';
      pill.className = 'age';
      return;
    }
    var ageMs = Date.now() - CAPTURED_AT;
    el.textContent = fmtAge(ageMs);
    pill.className = ageMs > 25000 ? 'age stale' : (ageMs > 18000 ? 'age warn' : 'age');
  }
  if (${status === 'connected' ? 'false' : 'true'}) {
    // Refresh age display every 1s
    setInterval(refreshAge, 1000);
    refreshAge();
    // Re-fetch QR image every 3s. Each fetch pulls the latest QR string
    // captured in module scope, so as long as Baileys is rotating the QR
    // we always show the fresh one. Also reads X-WA-QR-Captured-At from
    // the response so CAPTURED_AT stays accurate without page reload.
    setInterval(function() {
      var img = document.getElementById('qr');
      if (img) img.src = '/api/wa-listener/qr.png' + TOK_QS + (TOK_QS ? '&' : '?') + 't=' + Date.now();
    }, 3000);
    // R-F550 (2026-05-15) — REMOVED the 30s page reload. Pre-R-F550 the
    // page reload destroyed scan progress mid-scan (operator reported
    // "QR goes too fast"). The image-refresh loop above already picks
    // up new QR rotations every 3s, and the status-poll below auto-
    // redirects on successful connection. No page reload needed.
    //
    // R-F550 — poll the status endpoint every 4s; when connected,
    // reload to show the "Already linked" success state. This replaces
    // the brute-force 30s reload with a connection-success detector
    // that fires within ~4s of a successful scan.
    setInterval(function() {
      fetch('/api/wa-listener/status' + TOK_QS, {
        headers: { 'Authorization': 'Bearer ' + (${JSON.stringify(tok)} || '') }
      })
        .then(function(r) { return r.ok ? r.json() : null; })
        .then(function(s) {
          if (s && s.connected) {
            window.location.reload();
          } else if (s && s.captured_at && s.captured_at !== CAPTURED_AT) {
            // Fresh QR captured by Baileys rotation — update CAPTURED_AT
            // so the age pill resets. No page reload — img-refresh
            // above will pick up the new QR within 3s.
            CAPTURED_AT = s.captured_at;
            var ts = document.getElementById('ts');
            if (ts) ts.textContent = new Date(s.captured_at).toISOString();
          }
        })
        .catch(function() {});
    }, 4000);
    // Force-new-QR button: POSTs /force-qr, then reloads after the
    // listener restarts.
    var btn = document.getElementById('btn-force');
    if (btn) {
      btn.addEventListener('click', function() {
        if (!confirm('This nukes the current auth state and starts a fresh QR cycle. Continue?')) return;
        btn.disabled = true;
        btn.textContent = 'Restarting listener…';
        fetch('/api/wa-listener/force-qr' + TOK_QS, {
          method: 'POST',
          headers: { 'Authorization': 'Bearer ' + (${JSON.stringify(tok)} || '') },
        }).then(function(r) {
          // Wait 4s for the listener restart, then reload to show the new QR
          setTimeout(function() { window.location.reload(); }, 4000);
        }).catch(function(e) {
          alert('Force-QR failed: ' + e.message);
          btn.disabled = false;
          btn.textContent = '↻ Force new QR cycle';
        });
      });
    }
  }
</script>
</body></html>`);
  });

  // Send a message to a group or contact (API-driven)
  app.post('/api/wa-listener/send', _waRequireAuth, async (req, res) => {
    if (!sock || !isConnected) {
      return res.status(503).json({ error: 'WhatsApp not connected' });
    }
    const { group_id, chat_id, message } = req.body || {};
    const target = group_id || chat_id;
    if (!target || !message) {
      return res.status(400).json({ error: 'group_id (or chat_id) and message required' });
    }
    // R-F2113 §25a — delivery outcome tracking for the embedded send path
    const _rid = `api_send_${target.replace(/[^a-zA-Z0-9_]/g, '')}_${Date.now()}`;
    const _t0 = Date.now();
    const ok = await sendMessage(target, message);
    if (ok) {
      reportOutcome('web', _rid, 'outbound_send', 'delivered_real_answer', Date.now() - _t0);
      res.json({ ok: true, sent_to: groupNames.get(target) || target, length: message.length });
    } else {
      reportOutcome('web', _rid, 'outbound_send', 'send_failed', Date.now() - _t0, 'sendMessage returned false');
      res.status(500).json({ error: 'Failed to send message' });
    }
  });

  // Ask ARIA a question and send her reply to a group
  app.post('/api/wa-listener/ask-aria', _waRequireAuth, async (req, res) => {
    if (!sock || !isConnected) {
      return res.status(503).json({ error: 'WhatsApp not connected' });
    }
    const { group_id, question } = req.body || {};
    if (!group_id || !question) {
      return res.status(400).json({ error: 'group_id and question required' });
    }
    // R-F2107 (ARIA wa DD): add requestId + timeout so the brain knows whether
    // this API caller received a real answer (§25a delivery tracking).
    const _rid = `api_ask_${group_id.replace(/[^a-zA-Z0-9_]/g, '')}_${Date.now()}`;
    const _t0 = Date.now();
    let rawReply;
    try {
      rawReply = await Promise.race([
        askARIA(question, '', 'api'),
        new Promise((_, reject) => setTimeout(() => reject(new Error('askARIA timed out after 240s')), 240000)),
      ]);
    } catch (e) {
      reportOutcome('web', _rid, 'chat_answer', 'error', Date.now() - _t0, e?.message);
      return res.status(504).json({ error: 'ARIA did not respond in time', detail: e?.message });
    }
    const reply = _unwrapAriaReply(rawReply, null);
    if (!reply) {
      reportOutcome('web', _rid, 'chat_answer', 'error', Date.now() - _t0, 'no reply from ARIA');
      return res.status(502).json({
        error: 'ARIA did not respond',
        detail: rawReply?.message || 'unknown',
      });
    }
    const response = `*ARIA* — ${reply}`;
    const ok = await sendMessage(group_id, response);
    reportOutcome('web', _rid, 'chat_answer', ok ? 'delivered_real_answer' : 'send_failed', Date.now() - _t0);
    res.json({ ok, response: reply });
  });

  // List groups — for finding group IDs
  app.get('/api/wa-listener/groups', _waRequireAuth, async (_req, res) => {
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
  app.get('/api/wa-listener/messages', _waRequireAuth, (req, res) => {
    const n   = Math.min(parseInt(req.query.n || '20'), 100);
    const grp = req.query.group || '';
    const msgs = grp
      ? messageStore.filter(m => m.groupName === grp || m.groupId === grp)
      : messageStore;
    res.json({ count: msgs.length, messages: msgs.slice(-n).reverse() });
  });

  console.log('[WA Listener] Routes mounted — /api/wa-listener/*');
}

// lib/telegram/channelServerHooks.mjs
//
// Channel Server Hooks — R-F2288
// ===============================
// All the server.mjs wiring for the channel publisher, scheduler,
// media engine, interactive engine, and reply keyword router.
//
// Imported by server.mjs to keep the protected file clean.
// This module exports init functions that server.mjs calls.

import { curateSignals, formatChannelPost, formatDailyBrief, canPostNow, recordPost, getSchedulerState, publishSignal, isBreakingSignal } from './channelPublisher.mjs';
import { getCurrentSlot, markPosted, getTodaySchedule, getSchedulerState as getSchedulerState2, buildCaseFile, buildKnowYourRights, buildCountryRead, buildMorningSignal, buildWelcomePost } from './channelScheduler.mjs';
import { parseReply, handleScreen, handleCountryBrief, handleTender, handleDemo, handlePro, handleHelp } from './replyKeywordRouter.mjs';
// R-F2544 — editorialQueue imports (peekNextEditorial, markEditorialPosted) removed:
// the editorial fallback lane they fed was retired when the channel became Golden
// Intel only, leaving them with zero references in this module.
import { dedupKey, wasRecentlyPosted, recordPosted } from './postDedup.mjs';

// R-F2317 — channel PROPRIOCEPTION (§21a/§25). Report every daily-post outcome
// (delivered / failed / skipped) to the brain so ARIA KNOWS whether the channel
// limb actually fired — the channel was previously DARK (console-only). Fire-and-
// forget; it must NEVER break or delay a post.
// R-F2319 / R-F2473 — normalize the channel chat_id into Telegram's canonical
// -100<internal> form. TELEGRAM_CHAT_ID may arrive in three shapes:
//   -1003836086295   already canonical (signed)     -> as-is
//    1003836086295   abs value, minus dropped        -> prepend '-'   (NOT '-100')
//       3836086295   bare internal id                -> prepend '-100'
// R-F2319 blanket-prefixed '-100', which corrupted the ABS form into
// -1001003836086295 ("chat not found" — the whole channel went dark). Verified
// live via getChat: -1003836086295 => @ARIAIntelligence; the double-100 form 404s.
// The abs form already carries the 100 supergroup marker (13+ digits) — detect it
// and add only the sign; a shorter bare internal id still needs the full -100.
export function _channelChatId(bot) {
  const raw = String(bot?.channelId || bot?.chatId || '').trim();
  if (!raw || raw[0] === '@' || raw[0] === '-') return raw;   // username / already-signed
  if (!/^\d+$/.test(raw)) return raw;                          // non-numeric handle — leave alone
  if (/^100\d{10,}$/.test(raw)) return `-${raw}`;             // 1003836086295 -> -1003836086295
  if (raw.length >= 9) return `-100${raw}`;                    // 3836086295    -> -1003836086295
  return raw;
}

// R-F2321 — resilient channel send: try Markdown, and on a parse error (HTTP 400)
// retry as PLAIN text so a stray unescaped character never silently drops the
// day's post. Returns { ok, status }.
async function _channelSend(bot, text, { disablePreview = false } = {}) {
  const chatId = _channelChatId(bot);
  const post = async (useMarkdown) => {
    const body = { chat_id: chatId, text };
    if (useMarkdown) body.parse_mode = 'Markdown';
    if (disablePreview) body.disable_web_page_preview = true;
    return fetch(`https://api.telegram.org/bot${bot.botToken}/sendMessage`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body), signal: AbortSignal.timeout(15000),
    });
  };
  let res = await post(true);
  if (!res.ok && res.status === 400) {
    console.warn('[ChannelCron] Markdown parse failed — retrying as plain text');
    res = await post(false);
  }
  return { ok: res.ok, status: res.status };
}

function _cardTitleFromText(text, fallback = 'ARIA Intelligence') {
  const first = String(text || '').split('\n').map(s => s.trim()).find(Boolean) || fallback;
  return first.replace(/^[^\w[]+\s*/, '').replace(/\*/g, '').replace(/—/g, '-').slice(0, 110);
}

function _cardBulletsFromText(text) {
  return String(text || '')
    .split('\n')
    .map(s => s.replace(/^[•*\-\d.\s_`]+/, '').replace(/\*/g, '').trim())
    .filter(s => s.length >= 24 && !/^source:/i.test(s) && !/ARIA Intelligence/i.test(s))
    .slice(0, 3);
}

function _cardCaption(cardData) {
  const title = String(cardData?.title || 'ARIA Intelligence').replace(/\*/g, '').slice(0, 120);
  const source = String(cardData?.source || 'ARIA Intelligence').replace(/\*/g, '').slice(0, 80);
  return `*${title}*\n${source}\n\nFull analysis below.`;
}

async function _channelSendWithCard(bot, text, cardData = {}, opts = {}) {
  try {
    const chatId = _channelChatId(bot);
    const mediaBot = { ...bot, chatId, channelId: chatId };
    const { buildIntelCardData, generateInfographicCard, uploadSvgAsPhoto, sendPhoto } = await import('./channelMedia.mjs');
    const payload = buildIntelCardData({
      title: _cardTitleFromText(text),
      subtitle: String(text || '').replace(/\s+/g, ' ').slice(0, 220),
      bullets: _cardBulletsFromText(text),
      source: 'ARIA Telegram Channel',
      type: 'daily',
      ...cardData,
    });
    const svg = generateInfographicCard(payload);
    // R-F2519 (log-review F4) — PRE-VALIDATE the generated card before upload so a
    // malformed/empty SVG never reaches Telegram as an opaque IMAGE_PROCESS_FAILED, and
    // LOG image metadata on any failure (the review saw the failure with no detail). The
    // text-only fallback below (_channelSend) already runs regardless, so the post is
    // never lost — the card is best-effort polish.
    const _svgLen = (typeof svg === 'string' && svg.length) || 0;
    const _svgOk = _svgLen > 200 && typeof svg === 'string' && svg.includes('<svg');
    if (!_svgOk) {
      console.warn(`[ChannelCron] card skipped — invalid SVG (len=${_svgLen}, has_svg_tag=${typeof svg === 'string' && svg.includes('<svg')}); posting text-only`);
    } else {
      const uploaded = await uploadSvgAsPhoto(mediaBot, svg, `aria_intel_${Date.now()}.svg`);
      if (uploaded.ok && uploaded.fileId) {
        const sent = await sendPhoto(mediaBot, uploaded.fileId, { caption: _cardCaption(payload) });
        if (!sent.ok) console.warn(`[ChannelCron] card send failed: ${sent.error || sent.status} (svg_len=${_svgLen}B)`);
      } else {
        console.warn(`[ChannelCron] card upload skipped: ${uploaded.error || 'no file id'} (svg_len=${_svgLen}B) — posting text-only`);
      }
    }
  } catch (e) {
    console.warn('[ChannelCron] card generation failed:', e.message);
  }
  return _channelSend(bot, text, opts);
}

async function reportChannelOutcome(action, outcome, detail = '') {
  try {
    const base = process.env.ARIA_SERVICE_URL || 'https://aria-intel.fly.dev';
    const token = process.env.ARIA_API_TOKEN || '';
    await fetch(`${base}/api/aria/brain/signal`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', ...(token ? { authorization: `Bearer ${token}` } : {}) },
      body: JSON.stringify({ type: 'channel_delivery', surface: 'telegram_channel', action, outcome, detail: String(detail || '').slice(0, 200) }),
      signal: AbortSignal.timeout(8000),
    });
  } catch { /* proprioception must never break the post */ }
}

function _ariaServiceHeaders(extra = {}) {
  const token = process.env.ARIA_API_TOKEN || process.env.ARIA_INTERNAL_TOKEN || '';
  return { ...extra, ...(token ? { authorization: `Bearer ${token}` } : {}) };
}

export async function fetchGoldenIntelSignals(opts = {}) {
  const base = opts.serviceUrl || process.env.ARIA_SERVICE_URL || 'https://aria-intel.fly.dev';
  const limit = Math.max(1, Math.min(Number(opts.limit) || 20, 100));
  try {
    const r = await fetch(`${base}/api/aria/intel/signals/recent?limit=${limit}`, {
      headers: _ariaServiceHeaders(),
      signal: AbortSignal.timeout(Number(opts.timeoutMs) || 12000),
    });
    if (!r.ok) {
      return { ok: false, error: `HTTP ${r.status}`, signals: [], freshness: { stale: true, stale_reasons: ['fetch_failed'] } };
    }
    const data = await r.json();
    return {
      ok: true,
      signals: Array.isArray(data?.signals) ? data.signals : [],
      freshness: data?.freshness || { stale: true, stale_reasons: ['missing_freshness'] },
      raw: data,
    };
  } catch (e) {
    return { ok: false, error: e.message, signals: [], freshness: { stale: true, stale_reasons: ['fetch_exception'] } };
  }
}

const _GOLDEN_ALLOWED_TYPES = new Set([
  'sanctions_change',
  'active_tender',
  'contract_award',
  'budget_movement',
  'programme_signal',
  'competitor_activity',
  'conflict_escalation',
]);

function _signalEvidenceUrl(signal) {
  // R-F2602: only surface a real http(s) evidence URL. This both hardens the
  // channel gate (Boolean(_signalEvidenceUrl) now demands a valid scheme, mirroring
  // dashboard.html signalEvidenceUrl) and stops a non-URL string reaching the
  // Markdown formatter as an injectable link. The format site _md()-escapes it too.
  const url = String(signal?.url || signal?.evidence?.url || '').trim();
  return /^https?:\/\/\S+$/i.test(url) ? url : '';
}

function _postDedupKey(signal) {
  return dedupKey(signal);
}

function _signalSourceTier(signal) {
  return String(signal?.source_tier || signal?.evidence?.source_tier || '').toLowerCase();
}

function _customerValueScore(signal) {
  const explicit = Number(signal?.customer_value?.score ?? signal?.distribution_score ?? 0);
  return Number.isFinite(explicit) ? explicit : 0;
}

function _customerValueHardRejections(signal) {
  const reasons = Array.isArray(signal?.customer_value?.rejection_reasons)
    ? signal.customer_value.rejection_reasons
    : (Array.isArray(signal?.distribution_rejection_reasons) ? signal.distribution_rejection_reasons : []);
  return reasons.filter(r => !['customer_value_below_distribution_threshold', 'customer_value_below_telegram_threshold'].includes(String(r)));
}

function _md(text) {
  return String(text || '').replace(/([_*`[\]])/g, '\\$1');
}

export function selectTelegramGoldenIntel(feed, opts = {}) {
  const freshness = feed?.freshness || {};
  const allowBackfilled = opts.allowBackfilled === true || String(process.env.CHANNEL_GOLDEN_ALLOW_BACKFILLED || '').toLowerCase() === '1';
  if (!feed?.ok) return null;
  if (freshness.stale !== false) return null;
  if (freshness.backfilled && !allowBackfilled) return null;

  // R-F2714 — gate on the formal intel_grade (computed in Python from tier /
  // corroboration / evidence URL / named entity / relevance), NOT the
  // customer_value score, which is never computed and made every raw-news signal
  // structurally unpublishable (score 0 vs a >=80 gate). Grade A is decision-grade
  // (official/corroborated primary). The completeness + evidence-URL + dedup checks
  // stay. Grade B fallback is applied by the caller's A→B policy (R-F2716).
  const candidates = (Array.isArray(feed.signals) ? feed.signals : []).filter(s => {
    const grade = String(s?.intel_grade || '').toUpperCase();
    const type = String(s?.signal_type || '');
    return grade === 'A'
      && _GOLDEN_ALLOWED_TYPES.has(type)
      && Boolean(s?.decision_summary || s?.title)
      && Boolean(s?.why_it_matters)
      && Boolean(s?.recommended_action)
      && Boolean(_signalEvidenceUrl(s))
      && (allowBackfilled || !s?._backfilled)
      && !wasRecentlyPosted(_postDedupKey(s));
  });
  const confidenceRank = { HIGH: 0, MEDIUM: 1, LOW: 2 };
  candidates.sort((a, b) => (
    confidenceRank[String(a.confidence || '').toUpperCase()] - confidenceRank[String(b.confidence || '').toUpperCase()]
    || Number(b.score || 0) - Number(a.score || 0)
    || String(b.detected_at || b.published || '').localeCompare(String(a.detected_at || a.published || ''))
  ));
  return candidates[0] || null;
}

export function formatGoldenIntelChannelPost(signal, freshness = {}) {
  const title = _md(String(signal?.decision_summary || signal?.title || 'Golden Intel').slice(0, 220));
  const why = _md(String(signal?.why_it_matters || '').slice(0, 360));
  const action = _md(String(signal?.recommended_action || '').slice(0, 180));
  const source = _md(String(signal?.source || signal?.evidence?.source || 'ARIA monitored source').slice(0, 120));
  const url = _signalEvidenceUrl(signal);
  const target = signal?.target ? `\nTarget: *${_md(String(signal.target).slice(0, 90))}*` : '';
  const horizon = signal?.action_horizon ? ` · Horizon: ${_md(signal.action_horizon)}` : '';
  const detected = signal?.detected_at || signal?.published || freshness.newest_signal_at || '';
  const dateLine = detected ? `\nDetected: ${String(detected).slice(0, 16).replace('T', ' ')}` : '';
  return [
    '*GOLDEN INTEL*',
    '',
    `*${title}*`,
    target,
    '',
    `Why it matters: ${why}`,
    '',
    `Action: *${action}*`,
    '',
    `Signal: ${_md(signal.priority || 'HIGH')} · ${_md(signal.confidence || 'HIGH')} · ${_md(signal.quality_label || 'decision-grade')}${horizon}`,
    `Evidence: ${source}${url ? ` — ${_md(url)}` : ''}${dateLine}`,
    '',
    'Reply `ARIA [topic]` for the deeper brief.',
  ].filter(Boolean).join('\n');
}

async function _sendScheduledPostOnce(bot, type, post, dedupSignal, opts = {}) {
  const key = _postDedupKey({ type, text: post, ...dedupSignal });
  if (wasRecentlyPosted(key)) {
    markPosted(type);
    console.log(`[ChannelCron] ${type} skipped — recently posted`);
    if (opts.reportAction) reportChannelOutcome(opts.reportAction, 'skipped', 'deduped');
    return { ok: false, skipped: true, reason: 'deduped' };
  }
  const res = await _channelSend(bot, post, { disablePreview: opts.disablePreview === true });
  if (res.ok) {
    markPosted(type);
    recordPosted(key);
  }
  return res;
}

// ── Exports for server.mjs ─────────────────────────────────────────────────────

export {
  // Channel Publisher
  curateSignals,
  formatChannelPost,
  formatDailyBrief,
  canPostNow,
  recordPost,
  getSchedulerState,
  publishSignal,
  // Channel Scheduler
  getCurrentSlot,
  markPosted,
  getTodaySchedule,
  getSchedulerState2,
  buildCaseFile,
  buildKnowYourRights,
  buildCountryRead,
  buildMorningSignal,
  buildWelcomePost,
  // Reply Keyword Router
  parseReply,
  handleScreen,
  handleCountryBrief,
  handleTender,
  handleDemo,
  handlePro,
  handleHelp,
};

// ── Sweep Cycle Hook ───────────────────────────────────────────────────────────

/**
 * Run the channel publisher sweep cycle.
 * Called from server.mjs after each sweep completes.
 *
 * @param {object} currentData — Sweep results.
 * @param {object} bot — Telegram bot config { botToken, chatId, channelId }.
 * @returns {Promise<{posted:number,errors:number}>}
 */
/**
 * R-F2299 — wireBreakingAlertsToChannel: publish genuinely-breaking signals
 * (isBreakingSignal, score >= BREAKING_SCORE) to the channel IMMEDIATELY,
 * bypassing the routine posting-cadence cap (canPostNow). This is the real-time
 * value the strategy calls for — a FLASH/critical item reaches subscribers the
 * moment it lands, not on the next curated slot. Returns { handled, posted,
 * errors }; `handled` is the Set of signal objects it posted so the routine
 * curated pass can exclude them (no double-post).
 * @param {object[]} signals
 * @param {object} bot — { botToken, chatId, channelId }
 */
// R-F2315 — breaking-alert flood guard. publishBreakingSignals runs on EVERY sweep
// (~every refresh interval) and bypasses the 1/day cadence cap by design. With no
// per-day cap it re-posted persistent high-severity correlations every couple of
// minutes → the channel flooded (operator: "sending every two minutes"). Now:
//   • OFF by default (CHANNEL_BREAKING_ENABLED) — matches the one-post/day,
//     keep-noise-down directive; real-time breaking is opt-in, not the default.
//   • when enabled, a HARD per-day cap (CHANNEL_MAX_BREAKING_PER_DAY, default 2)
//     so it can NEVER flood even if the dedup misses.
let _breakingCount = 0;
let _breakingDate = '';
function _breakingPostedToday() {
  const today = new Date().toISOString().slice(0, 10);
  if (_breakingDate !== today) { _breakingDate = today; _breakingCount = 0; }
  return _breakingCount;
}

export async function publishBreakingSignals(signals, bot) {
  const handled = new Set();
  let posted = 0;
  let errors = 0;
  const enabled = ['1', 'true', 'yes', 'on'].includes(String(process.env.CHANNEL_BREAKING_ENABLED || '').toLowerCase());
  if (!enabled) return { handled, posted, errors, disabled: true };
  const capPerDay = Number(process.env.CHANNEL_MAX_BREAKING_PER_DAY) || 2;
  for (const s of (signals || [])) {
    if (_breakingPostedToday() >= capPerDay) break;   // hard daily ceiling — no flood
    if (!isBreakingSignal(s)) continue;
    handled.add(s);
    try {
      const r = await publishSignal(s, bot, { generateImage: true, registerKeyword: true, crossPostLinkedIn: false });
      if (r.ok) { posted++; _breakingCount = _breakingPostedToday() + 1; console.log('[ChannelSweep] 🚨 BREAKING published:', String(s.title || s.summary || '').substring(0, 60)); }
      else { errors++; console.warn('[ChannelSweep] breaking skipped:', r.error); }
    } catch (e) {
      errors++;
      console.warn('[ChannelSweep] breaking error:', e.message);
    }
  }
  return { handled, posted, errors };
}

export async function runChannelSweep(currentData, bot) {
  if (!bot?.botToken || !currentData) return { posted: 0, errors: 0 };
  // R-F2544 — public Telegram channel is Golden Intel only. Sweep-time
  // breaking/routine posts use the older broad curation scorer, so they must not
  // publish to the channel. Golden Intel is posted by handleMorningSignalCron().
  return { posted: 0, errors: 0, skipped: true, reason: 'golden_intel_only' };
}

// ── Cron Job Handlers ──────────────────────────────────────────────────────────

/**
 * Handle the 07:00 Morning Signal cron.
 *
 * @param {object} currentData — Sweep results.
 * @param {object} bot — Telegram bot config.
 */
/**
 * R-F2312 — SANCTIONS SPOTLIGHT: wire ARIA's own Python screening engine
 * (/api/aria/sanctions/fuzzy — the never-false-clean OFAC/UK/EU/UN/OpenSanctions
 * engine that powers SCREEN) into the daily post. Takes the day's topical
 * multi-list entities from the sweep, screens the top few through the REAL engine,
 * and returns ONE verified blocking hit as a spotlight signal — or null. Lean by
 * design: at most one item, only genuine blocking hits (never a fabricated
 * "clear"), and it stays silent when the sweep has no topical entity. Never throws.
 *
 * @param {object} currentData
 * @param {{serviceUrl?:string, token?:string, maxCandidates?:number}} [opts]
 * @returns {Promise<{title:string,text:string}|null>}
 */
export async function fetchSanctionsSpotlight(currentData, opts = {}) {
  try {
    const base = opts.serviceUrl || process.env.ARIA_SERVICE_URL || 'https://aria-intel.fly.dev';
    const token = opts.token || process.env.ARIA_API_TOKEN || '';
    const maxCandidates = opts.maxCandidates || 3;
    const candidates = (Array.isArray(currentData?.opensanctions?.recent) ? currentData.opensanctions.recent : [])
      .map(e => e?.name || e?.caption)
      .filter(n => typeof n === 'string' && n.trim().length >= 2)
      .slice(0, maxCandidates);
    for (const name of candidates) {
      let d;
      try {
        const r = await fetch(`${base}/api/aria/sanctions/fuzzy`, {
          method: 'POST',
          headers: { 'content-type': 'application/json', ...(token ? { authorization: `Bearer ${token}` } : {}) },
          body: JSON.stringify({ name }),
          signal: AbortSignal.timeout(20000),
        });
        if (!r.ok) continue;
        d = await r.json();
      } catch { continue; }
      // never-false-clean: only feature a genuinely screened, blocking hit.
      if (d && d.screened === true && !d.error && Array.isArray(d.blocking_matches) && d.blocking_matches.length > 0) {
        const lists = [...new Set(d.blocking_matches.map(m => m.list || m.source || m.dataset).filter(Boolean))].slice(0, 3);
        const n = d.blocking_matches.length;
        return {
          title: `⚖️ Sanctions spotlight: ${name}`,
          text: `ARIA screened *${name}* against OFAC · UK OFSI · EU · UN · OpenSanctions → *${n} blocking match${n === 1 ? '' : 'es'}*${lists.length ? ` (${lists.join(', ')})` : ''}. Do not proceed without enhanced due diligence.\n\nScreen any counterparty free — reply \`SCREEN [company]\`.`,
        };
      }
    }
    return null;
  } catch (e) {
    console.warn('[ChannelCron] sanctions spotlight failed:', e.message);
    return null;
  }
}

/**
 * R-F2310 — build the daily-post signals from the REAL sweep data, in the
 * audit's customer-acquisition priority order: real sanctions designations →
 * verified procurement tenders → a projected market opportunity → (else empty →
 * the cron skips the post). "Screenshot-worthy = a name, a number, a source."
 *
 * Correct field map (the old code read fields that never existed):
 *   sanctions  → currentData.opensanctions.recent  [{ name, datasets }]
 *   tenders    → currentData.bdIntelligence.tenders [{ title, summary, url, verified, date }]
 *   opportunity→ currentData.opportunities          [{ market, tier, score, procurementNeeds }] (no title/summary → project)
 *
 * @param {object} currentData
 * @returns {Array<{title:string,text:string}>}
 */
export function buildDailySignals(currentData) {
  const signals = [];

  // 1. Real sanctions designations — entities surfacing on 2+ lists (highest
  //    credibility for a compliance audience).
  const recent = Array.isArray(currentData?.opensanctions?.recent) ? currentData.opensanctions.recent : [];
  for (const s of recent.slice(0, 2)) {
    const name = s?.name || s?.caption;
    if (!name) continue;
    const lists = Array.isArray(s.datasets) ? s.datasets : [];
    const where = lists.length ? ` — now on ${lists.length} sanctions/watchlist dataset${lists.length === 1 ? '' : 's'}${lists.length ? ` (${lists.slice(0, 3).join(', ')})` : ''}` : '';
    signals.push({
      title: `⚖️ Sanctions exposure: ${name}`,
      text: `${name}${where}. Screen your counterparties before contracting — reply \`SCREEN ${name}\`.`,
    });
  }

  // 2. A verified procurement tender (the strongest broker-acquisition asset) —
  //    only VERIFIED/CORROBORATED, must carry a title.
  const tenders = (Array.isArray(currentData?.bdIntelligence?.tenders) ? currentData.bdIntelligence.tenders : [])
    .filter(t => t && t.title && ['VERIFIED', 'CORROBORATED'].includes(String(t.verified || '').toUpperCase()));
  if (tenders.length) {
    const t = tenders[0];
    signals.push({
      title: `🔍 Procurement signal: ${t.title}`,
      text: [t.summary, t.date ? `Dated ${t.date}.` : '', t.source ? `Source: ${t.source}.` : '', t.url || '']
        .filter(Boolean).join(' ').slice(0, 400),
    });
  }

  // 3. Fallback — a projected market opportunity (only if 1 & 2 were dry).
  if (signals.length === 0) {
    const opps = Array.isArray(currentData?.opportunities) ? currentData.opportunities : [];
    const top = opps.filter(o => o && o.market).sort((a, b) => (b.score || 0) - (a.score || 0))[0];
    if (top) {
      const needs = Array.isArray(top.procurementNeeds) && top.procurementNeeds.length
        ? ` — active needs: ${top.procurementNeeds.slice(0, 3).join(', ')}` : '';
      signals.push({
        title: `🌍 Market watch: ${top.market}`,
        text: `${top.tier ? `${top.tier} ` : ''}defence-market signal${top.score != null ? ` (score ${Math.round(top.score)})` : ''}${needs}.`,
      });
    }
  }

  return signals;
}

export async function handleMorningSignalCron(currentData, bot) {
  if (!bot?.botToken) return;
  console.log('[ChannelCron] Morning Signal...');
  try {
    const channelId = String(bot?.channelId || '').trim();
    if (!channelId) {
      console.log('[ChannelCron] Golden Intel skipped — TELEGRAM_CHANNEL_ID not configured');
      reportChannelOutcome('daily_golden_intel', 'skipped', 'missing TELEGRAM_CHANNEL_ID');
      return { ok: false, skipped: true, reason: 'missing_channel_id' };
    }
    const channelBot = { ...bot, chatId: channelId, channelId };
    // R-F2544 — Golden Intel is the ONLY public-channel intel lane. The backend
    // must prove freshness and the signal must pass the public-channel quality
    // gate. Missing/stale/no passing Golden Intel means no Telegram post.
    const goldenFeed = await fetchGoldenIntelSignals({ limit: 20 });
    const golden = selectTelegramGoldenIntel(goldenFeed);
    if (golden) {
      const post = formatGoldenIntelChannelPost(golden, goldenFeed.freshness);
      const res = await _channelSendWithCard(channelBot, post, {
        title: golden.decision_summary || golden.title || 'Golden Intel',
        subtitle: golden.why_it_matters || '',
        bullets: [
          golden.recommended_action,
          golden.corroboration || golden.quality_label,
          golden.target ? `Target: ${golden.target}` : '',
        ].filter(Boolean),
        source: golden.source || 'ARIA Golden Intel',
        type: golden.signal_type === 'sanctions_change' ? 'sanctions' : 'daily',
      }, { disablePreview: false });
      if (res.ok) {
        markPosted('golden_intel');
        recordPosted(_postDedupKey(golden));
        console.log('[ChannelCron] Golden Intel posted:', golden.id || golden.title);
        reportChannelOutcome('daily_golden_intel', 'delivered', golden.id || golden.title || 'golden');
      } else {
        console.warn('[ChannelCron] Golden Intel post failed HTTP', res.status);
        reportChannelOutcome('daily_golden_intel', 'failed', `HTTP ${res.status}`);
      }
      return;
    }

    console.log('[ChannelCron] Golden Intel skipped — no fresh decision-grade signal');
    reportChannelOutcome('daily_golden_intel', 'skipped', 'no fresh decision-grade Golden Intel');
    return { ok: false, skipped: true, reason: 'no_golden_intel' };
  } catch (e) {
    console.error('[ChannelCron] Morning Signal failed:', e.message);
    reportChannelOutcome('daily_post', 'failed', e.message);
  }
}

// ── RETIRED editorial lanes (R-F2544) ───────────────────────────────────────────
// The four editorial crons below (Case File / Know Your Rights / Country Read /
// Opportunity Signal) and publishBreakingSignals produced NON-Golden channel
// content. When the public channel became Golden Intel only their content logic
// was deleted and they are no longer scheduled — grep confirms NO cron.schedule
// references them; only tests call them. They are RETIRED, not flag-toggled: the
// TELEGRAM_GOLDEN_INTEL_ONLY flag (server.mjs) governs the still-LIVE non-Golden
// paths (manual admin-channel endpoints + sweep/digest/explorer alert lanes), NOT
// these stubs. Setting the flag to 0 re-opens those live paths; it does NOT revive
// these lanes. They return { skipped:true, reason:'golden_intel_only' } — the same
// signal the live guard emits, so downstream callers see one uniform skip reason —
// but that reason is unconditional here, reflecting retirement, not a runtime gate.
// Kept only so existing callers/tests keep a stable, honest no-op.

/**
 * RETIRED editorial lane (R-F2544) — was the 09:00 Case File cron; now an
 * unscheduled no-op that always skips (see the RETIRED block comment above).
 *
 * @param {object} bot — Telegram bot config.
 */
export async function handleCaseFileCron(bot) {
  if (!bot?.botToken) return;
  console.log('[ChannelCron] Case File...');
  console.log('[ChannelCron] Case File skipped — Golden Intel only');
  reportChannelOutcome('case_file', 'skipped', 'golden_intel_only');
  return { ok: false, skipped: true, reason: 'golden_intel_only' };
}

/**
 * RETIRED editorial lane (R-F2544) — was the 12:00 Know Your Rights cron; now an
 * unscheduled no-op that always skips (see the RETIRED block comment above).
 *
 * @param {object} bot — Telegram bot config.
 */
export async function handleKnowYourRightsCron(bot) {
  if (!bot?.botToken) return;
  console.log('[ChannelCron] Know Your Rights...');
  console.log('[ChannelCron] Know Your Rights skipped — Golden Intel only');
  reportChannelOutcome('know_your_rights', 'skipped', 'golden_intel_only');
  return { ok: false, skipped: true, reason: 'golden_intel_only' };
}

/**
 * RETIRED editorial lane (R-F2544) — was the 15:00 Country Read cron; now an
 * unscheduled no-op that always skips (see the RETIRED block comment above).
 *
 * @param {object} bot — Telegram bot config.
 */
export async function handleCountryReadCron(bot) {
  if (!bot?.botToken) return;
  console.log('[ChannelCron] Country Read...');
  console.log('[ChannelCron] Country Read skipped — Golden Intel only');
  reportChannelOutcome('country_read', 'skipped', 'golden_intel_only');
  return { ok: false, skipped: true, reason: 'golden_intel_only' };
}

/**
 * RETIRED editorial lane (R-F2544) — was the 18:00 Opportunity Signal cron; now an
 * unscheduled no-op that always skips (see the RETIRED block comment above).
 *
 * @param {object} bot — Telegram bot config.
 */
export async function handleOpportunityCron(bot) {
  if (!bot?.botToken) return;
  console.log('[ChannelCron] Opportunity Signal...');
  console.log('[ChannelCron] Opportunity Signal skipped — Golden Intel only');
  reportChannelOutcome('opportunity', 'skipped', 'golden_intel_only');
  return { ok: false, skipped: true, reason: 'golden_intel_only' };
}

/**
 * Handle a reply keyword from a user.
 *
 * @param {string} text — User reply text.
 * @param {string} userId — Telegram user ID.
 * @returns {Promise<{text:string}>}
 */
export async function handleReply(text, userId) {
  const parsed = parseReply(text);

  switch (parsed.action) {
    case 'screen':
      return handleScreen(parsed.arg);
    case 'country_brief':
      return handleCountryBrief(parsed.arg);
    case 'tender':
      return handleTender(parsed.arg);
    case 'demo':
      return handleDemo();
    case 'pro':
      return handlePro();
    case 'help':
      return handleHelp();
    case 'deep_dive': {
      const { resolveKeyword, matchKeyword } = await import('./channelInteractive.mjs');
      const match = matchKeyword(parsed.keyword || text);
      if (match.matched) {
        const resolved = resolveKeyword(match.keyword, userId || 'anonymous');
        return resolved.ok ? { text: resolved.response } : { text: resolved.error };
      }
      return { text: 'Unknown keyword. Try HELP for available commands.' };
    }
    default:
      return { text: 'I did not understand that. Try HELP for available commands.' };
  }
}

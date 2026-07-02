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
import { peekNextEditorial, markEditorialPosted } from './editorialQueue.mjs';

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

  // Collect signals from sweep results. R-F2299: collected BEFORE the cadence
  // gate so breaking alerts are evaluated even when the routine 2/day cap is closed.
  const signals = [];
  if (currentData.correlations) signals.push(...currentData.correlations);
  if (currentData.sanctions) signals.push(...currentData.sanctions);
  if (currentData.opportunities) signals.push(...currentData.opportunities);
  // R-F2315 — the tender ARRAY is bdIntelligence.tenders; .activeTenders is only a
  // COUNT (bd_intelligence.mjs counts.activeTenders) → this read was always
  // undefined, so tenders never entered the sweep signal set. Use .tenders + its
  // real fields (title/summary/url/market), verified items first.
  if (Array.isArray(currentData.bdIntelligence?.tenders)) {
    for (const t of currentData.bdIntelligence.tenders) {
      signals.push({ title: t.title, summary: t.summary, url: t.url, source: t.source || 'BD Intelligence', timestamp: new Date().toISOString(), severity: 'medium', sector: t.market, country: t.iso2 });
    }
  }
  if (currentData.explorerFindings?.insights) {
    for (const i of currentData.explorerFindings.insights) {
      signals.push({ title: i.title, summary: i.text, source: 'Web Explorer', timestamp: new Date().toISOString(), severity: 'low' });
    }
  }

  let posted = 0;
  let errors = 0;

  // R-F2299 — BREAKING first: genuinely critical signals bypass the cadence cap.
  const breaking = await publishBreakingSignals(signals, bot);
  posted += breaking.posted;
  errors += breaking.errors;

  // R-F2306 — operator directive: ONE curated post/day comes from the scheduled
  // Morning Signal, not the per-sweep cadence. The routine sweep-post is OFF by
  // default (CHANNEL_SWEEP_ROUTINE_ENABLED) so the channel isn't flooded; the
  // sweep now only escalates genuine BREAKING items (above). Re-enable per env if
  // you want opportunistic curated posts back.
  const _routineEnabled = String(process.env.CHANNEL_SWEEP_ROUTINE_ENABLED || '').toLowerCase() === '1'
    || ['true', 'yes', 'on'].includes(String(process.env.CHANNEL_SWEEP_ROUTINE_ENABLED || '').toLowerCase());
  const { canPost } = _routineEnabled ? canPostNow() : { canPost: false };
  if (canPost) {
    const routine = signals.filter(s => !breaking.handled.has(s));
    const curated = curateSignals(routine, { maxPosts: 2 });
    for (const signal of curated) {
      const result = await publishSignal(signal, bot, {
        generateImage: true,
        registerKeyword: true,
        crossPostLinkedIn: false,
      });
      if (result.ok) {
        posted++;
        console.log('[ChannelSweep] Published:', signal.title?.substring(0, 60), 'keyword:', result.keyword);
      } else {
        errors++;
        console.warn('[ChannelSweep] Skipped:', result.error);
      }
    }
  }

  return { posted, errors };
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
    // R-F2309 — drain the editorial queue FIRST: one curated, deep-research post
    // per day (Case File → DD method → Signal …) before falling back to live
    // sanctions/opportunity signals. markEditorialPosted persists to the volume so
    // a redeploy never reposts or skips.
    const editorial = peekNextEditorial();
    if (editorial) {
      const chatId = bot.channelId || bot.chatId;
      const res = await fetch(`https://api.telegram.org/bot${bot.botToken}/sendMessage`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id: chatId, text: editorial.text, parse_mode: 'Markdown', disable_web_page_preview: true }),
        signal: AbortSignal.timeout(15000),
      });
      if (res.ok) {
        markEditorialPosted(editorial.id);
        markPosted(`editorial:${editorial.type}`);
        console.log('[ChannelCron] Editorial posted:', editorial.id);
      } else {
        console.warn('[ChannelCron] Editorial post failed HTTP', res.status);
      }
      return;
    }

    // R-F2312 — ARIA's own Python sanctions engine gets the headline: if the day
    // has a real verified blocking hit, that ONE spotlight IS the post (most
    // credible, screenshot-worthy content). Otherwise fall to the live sweep
    // signals (R-F2310). Lean — one post, one lead item.
    const spotlight = await fetchSanctionsSpotlight(currentData);
    const signals = spotlight ? [spotlight] : buildDailySignals(currentData);
    // R-F2306 — operator directive: post ONLY relevant intel. If there is nothing
    // material this morning, SKIP the post entirely rather than posting a filler
    // "no significant signals" message (that is noise, and it trains subscribers
    // to ignore the channel).
    if (signals.length === 0) {
      console.log('[ChannelCron] Morning Signal skipped — no material intel this morning');
      return;
    }
    const post = buildMorningSignal({ signals });
    const chatId = bot.channelId || bot.chatId;
    const res = await fetch(`https://api.telegram.org/bot${bot.botToken}/sendMessage`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: chatId, text: post, parse_mode: 'Markdown' }),
      signal: AbortSignal.timeout(15000),
    });
    if (res.ok) markPosted('morning_signal');
    console.log('[ChannelCron] Morning Signal posted:', res.ok);
  } catch (e) {
    console.error('[ChannelCron] Morning Signal failed:', e.message);
  }
}

/**
 * Handle the 09:00 Case File cron (Mon/Wed/Fri).
 *
 * @param {object} bot — Telegram bot config.
 */
export async function handleCaseFileCron(bot) {
  if (!bot?.botToken) return;
  console.log('[ChannelCron] Case File...');
  try {
    const post = buildCaseFile({
      title: 'The Mozambique LNG Contractor That Was Not',
      scenario: 'A UK-based engineering firm was approached to subcontract on a major LNG project in Cabo Delgado. The prime contractor seemed legitimate with an impressive website, references, and even a past project in the region.',
      outcome: 'ARIA screening revealed the prime contractor was a shelf company with no operational history. The past project photo was taken from a different company website. The client avoided a GBP 2.3M advance payment fraud.',
      lesson: 'Always verify the prime contractor independently. A polished website and a single reference are not due diligence. Screen the screener.',
      country: 'Mozambique',
      sector: 'Oil & Gas',
    });
    const chatId = bot.channelId || bot.chatId;
    const res = await fetch(`https://api.telegram.org/bot${bot.botToken}/sendMessage`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: chatId, text: post, parse_mode: 'Markdown' }),
      signal: AbortSignal.timeout(15000),
    });
    if (res.ok) markPosted('case_file');
    console.log('[ChannelCron] Case File posted:', res.ok);
  } catch (e) {
    console.error('[ChannelCron] Case File failed:', e.message);
  }
}

/**
 * Handle the 12:00 Know Your Rights cron (Tue/Thu).
 *
 * @param {object} bot — Telegram bot config.
 */
export async function handleKnowYourRightsCron(bot) {
  if (!bot?.botToken) return;
  console.log('[ChannelCron] Know Your Rights...');
  try {
    const post = buildKnowYourRights({
      title: 'UK Economic Crime Act - Failure to Prevent',
      what: 'The UK Economic Crime and Corporate Transparency Act 2023 introduced a new corporate offence of failure to prevent fraud. Companies can now be criminally liable if an employee commits fraud for the company benefit.',
      why: 'This reverses the burden of proof. You must show you had reasonable prevention procedures in place. No compliance programme means automatic liability if fraud occurs.',
      how: '1. Conduct a fraud risk assessment\n2. Implement proportionate prevention procedures\n3. Communicate and train your team\n4. Monitor and review regularly',
      country: 'UK',
    });
    const chatId = bot.channelId || bot.chatId;
    const res = await fetch(`https://api.telegram.org/bot${bot.botToken}/sendMessage`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: chatId, text: post, parse_mode: 'Markdown' }),
      signal: AbortSignal.timeout(15000),
    });
    if (res.ok) markPosted('know_your_rights');
    console.log('[ChannelCron] Know Your Rights posted:', res.ok);
  } catch (e) {
    console.error('[ChannelCron] Know Your Rights failed:', e.message);
  }
}

/**
 * Handle the 15:00 Country Read cron (Mon/Thu).
 *
 * @param {object} bot — Telegram bot config.
 */
export async function handleCountryReadCron(bot) {
  if (!bot?.botToken) return;
  console.log('[ChannelCron] Country Read...');
  try {
    const post = buildCountryRead({
      country: 'Angola',
      overview: 'Angola is Sub-Saharan Africa third-largest economy and second-largest oil producer. President Joao Lourenco administration continues privatisation efforts under the PROPRIV programme, targeting 195 state assets by 2027. The non-oil economy is growing at 4.5 percent, driven by agriculture, construction, and telecoms.',
      risks: 'Oil dependency (90 percent of exports)\nForeign exchange liquidity constraints\nBureaucratic delays in procurement\nCorruption remains a concern (CPI: 30/100)\nDebt service consumes 60 percent of revenue',
      opportunities: 'PROPRIV privatisation - 195 assets across banking, logistics, telecoms\nInfrastructure investment - USD 60B needed by 2027\nAgribusiness - 57M hectares of arable land, 15 percent cultivated\nEnergy transition - critical minerals (copper, cobalt, lithium)',
      outlook: 'Positive medium-term. IMF programme on track. Oil production stabilising at 1.1M bpd. Non-oil growth accelerating. Key watch: 2027 elections and debt sustainability.',
    });
    const chatId = bot.channelId || bot.chatId;
    const res = await fetch(`https://api.telegram.org/bot${bot.botToken}/sendMessage`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: chatId, text: post, parse_mode: 'Markdown' }),
      signal: AbortSignal.timeout(15000),
    });
    if (res.ok) markPosted('country_read');
    console.log('[ChannelCron] Country Read posted:', res.ok);
  } catch (e) {
    console.error('[ChannelCron] Country Read failed:', e.message);
  }
}

/**
 * Handle the 18:00 Opportunity Signal cron (Tue/Fri).
 *
 * @param {object} bot — Telegram bot config.
 */
export async function handleOpportunityCron(bot) {
  if (!bot?.botToken) return;
  console.log('[ChannelCron] Opportunity Signal...');
  try {
    const post = buildCaseFile({
      title: 'Mozambique Defence Tender - Logistics Support',
      scenario: 'The Mozambican Ministry of Defence has issued an RFP for integrated logistics support services across three northern provinces. Estimated value: USD 45M over 5 years.',
      outcome: 'ARIA identified this tender through our procurement monitoring pipeline. Key requirements include local content (30 percent), security clearance, and proven experience in conflict-zone logistics.',
      lesson: 'Early identification gives you 6-8 weeks to prepare a compliant bid. ARIA subscribers get tender alerts 48 hours before public release.',
      country: 'Mozambique',
      sector: 'Defence & Security',
    });
    const chatId = bot.channelId || bot.chatId;
    const res = await fetch(`https://api.telegram.org/bot${bot.botToken}/sendMessage`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: chatId, text: post, parse_mode: 'Markdown' }),
      signal: AbortSignal.timeout(15000),
    });
    if (res.ok) markPosted('opportunity');
    console.log('[ChannelCron] Opportunity Signal posted:', res.ok);
  } catch (e) {
    console.error('[ChannelCron] Opportunity Signal failed:', e.message);
  }
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

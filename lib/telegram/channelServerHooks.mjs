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
export async function publishBreakingSignals(signals, bot) {
  const handled = new Set();
  let posted = 0;
  let errors = 0;
  for (const s of (signals || [])) {
    if (!isBreakingSignal(s)) continue;
    handled.add(s);
    try {
      const r = await publishSignal(s, bot, { generateImage: true, registerKeyword: true, crossPostLinkedIn: false });
      if (r.ok) { posted++; console.log('[ChannelSweep] 🚨 BREAKING published:', String(s.title || s.summary || '').substring(0, 60)); }
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
  if (currentData.bdIntelligence?.activeTenders) {
    for (const t of currentData.bdIntelligence.activeTenders) {
      signals.push({ title: t.title, summary: t.description, source: 'BD Intelligence', timestamp: new Date().toISOString(), severity: 'medium', sector: t.sector, country: t.country, value: t.value });
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

    const signals = [];
    if (currentData?.sanctions?.length > 0) {
      signals.push(...currentData.sanctions.slice(0, 3).map(s => ({ title: s.title || 'Sanctions Update', text: s.summary || '' })));
    }
    if (currentData?.opportunities?.length > 0) {
      signals.push(...currentData.opportunities.slice(0, 2).map(o => ({ title: o.title || 'Opportunity', text: o.summary || '' })));
    }
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

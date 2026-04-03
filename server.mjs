#!/usr/bin/env node
// Crucix Intelligence Engine — Dev Server
// Serves the Jarvis dashboard, runs sweep cycle, pushes live updates via SSE

import express from 'express';
import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'fs';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';
import { exec } from 'child_process';
import cron from 'node-cron';
import config from './crucix.config.mjs';
import { getLocale, currentLanguage, getSupportedLocales } from './lib/i18n.mjs';
import { fullBriefing, pushSignalsToBrain, registerSourceHooks } from './apis/briefing.mjs';
import { synthesize, generateIdeas } from './dashboard/inject.mjs';
import { MemoryManager } from './lib/delta/index.mjs';
import { createLLMProvider } from './lib/llm/index.mjs';
import { generateLLMIdeas } from './lib/llm/ideas.mjs';
import { TelegramAlerter } from './lib/alerts/telegram.mjs';
import { DiscordAlerter } from './lib/alerts/discord.mjs';
import { filterNewSignals, initDedup } from './lib/intel/dedup.mjs';
import { correlate, formatCorrelationsForTelegram } from './lib/intel/correlate.mjs';
import { detectArbitrage } from './lib/intel/arbitrage.mjs';
import { archiveRun, archiveRunWithEntities, analyzeTrends, formatTrendsForTelegram, analyzeEntityTrajectory, formatEntityTrajectoryForTelegram } from './lib/intel/archive.mjs';
import { sendMorningDigest } from './lib/alerts/digest.mjs';
import { fetchUNSecurityCouncil, fetchCentralBanks, fetchThinkTanks, fetchTradeFLows } from './apis/sources/intel-feeds.mjs';
import { fetchOpenSanctions } from './apis/sources/opensanctions.mjs';

// === Self-Learning & Self-Update System ===
import { getLearningStats, getOutcomes, recordAlertOutcome, getSourceHistory, getSourcesToReview, getPatterns, getOpportunities, getExplorerFindings, getUpdateLog, recordSourceSweep, initLearningStore } from './lib/self/learning_store.mjs';
import { detectOpportunities, formatOpportunitiesForTelegram } from './lib/self/opportunity_engine.mjs';
import { analyzePatterns, formatPatternsForTelegram } from './lib/self/pattern_analyzer.mjs';
import { runExploration, exploreQuery, formatExplorerFindingsForTelegram } from './lib/self/web_explorer.mjs';
import { generateSourceModule, generateSourceFix, stageModule, getStagedModules, getStagedCode, formatStagedForTelegram } from './lib/self/code_generator.mjs';
import { deployModule, rollbackModule, validateSyntax, isRestartPending, clearRestartFlag, triggerGracefulRestart, getAutoManagedModules } from './lib/self/updater.mjs';
import { runBDIntelligence, getBDIntelligence, getDealPipeline, updateDealStage, recordOutcome, formatBDSummaryForTelegram, initBDStore } from './lib/self/bd_intelligence.mjs';
import { screenDeal, getProductCategories } from './lib/compliance/screen.mjs';
import { redisGet, redisSet, redisDel } from './lib/persist/store.mjs';
import { createUser, findUserByEmail, findUserByUsername, findUserById, updateUser, deleteUser, revokeTokens, listUsers, verifyPassword, hashPassword, createToken, verifyToken, generateCode, initAdminUser, initUsersStore } from './lib/auth/users.mjs';
import { sendVerificationEmail, sendPasswordResetEmail, sendWelcomeEmail, sendAdminNotification, sendRejectionEmail, sendSuspensionEmail, sendReactivationEmail, sendPendingApprovalEmail } from './lib/auth/email.mjs';
import { logAudit, getAuditLog } from './lib/auth/audit.mjs';
import { initVapid, getVapidPublicKey, saveSubscription, removeSubscription, pushFlash, pushDigest } from './lib/push/push.mjs';
import { createServer } from 'http';
import { Server as SocketIOServer } from 'socket.io';
import { storeMessage, getConversation, markRead, getConversationSummaries, unreadCount } from './lib/messages.mjs';
import { ariaChat as ariaLocalChat, ariaThink as ariaLocalThink } from './lib/aria/aria.mjs';
import { applyRateLimiting, applyInputValidation, applySecurityHeaders } from './middleware/rateLimiter.mjs';
import { handleTelegramWebhook, setLLMProvider as setTelegramLLM, handleAriaCommand, buildArkmursBrief } from './lib/telegram/telegramCommands.mjs';
import { startComplianceRefreshScheduler, screenEntity, getComplianceVersions } from './lib/compliance/listRefresher.mjs';
import { errorTracker, configureTelemetry, SweepMonitor } from './lib/observability/errorTracker.mjs';
import { ProcurementDedup, SourcePruner } from './lib/sources/sourceMaintenance.mjs';
import { startExplorerScheduler } from './lib/self/explorerScheduler.mjs';
import { redisAdapter } from './lib/persist/redisAdapter.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = __dirname;
const RUNS_DIR = join(ROOT, 'runs');
const MEMORY_DIR = join(RUNS_DIR, 'memory');

// ── Timezone helper — ICU-free, honours BST/GMT (Europe/London) ─────────────
// UK clock: BST (UTC+1) last Sun March 01:00 UTC → last Sun October 01:00 UTC
function londonTs(date = new Date(), seconds = true) {
  function ukOffset(d) {
    const y = d.getUTCFullYear();
    const lastSunMar = new Date(Date.UTC(y, 2, 31, 1, 0, 0));
    while (lastSunMar.getUTCDay() !== 0) lastSunMar.setUTCDate(lastSunMar.getUTCDate() - 1);
    const lastSunOct = new Date(Date.UTC(y, 9, 31, 1, 0, 0));
    while (lastSunOct.getUTCDay() !== 0) lastSunOct.setUTCDate(lastSunOct.getUTCDate() - 1);
    return (d >= lastSunMar && d < lastSunOct) ? 1 : 0;
  }
  const p = n => String(n).padStart(2, '0');
  const local = new Date(date.getTime() + ukOffset(date) * 3600000);
  const base = `${local.getUTCFullYear()}-${p(local.getUTCMonth()+1)}-${p(local.getUTCDate())} ${p(local.getUTCHours())}:${p(local.getUTCMinutes())}`;
  return seconds ? `${base}:${p(local.getUTCSeconds())}` : base;
}
function logTime(date = new Date()) { return londonTs(date); }
function logTimeShort(date = new Date()) { return londonTs(date, false); }

// Ensure directories exist (including logs for PM2)
for (const dir of [RUNS_DIR, MEMORY_DIR, join(MEMORY_DIR, 'cold'), join(RUNS_DIR, 'logs')]) {
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
}

// === State ===
let currentData = null;
let lastSweepTime = null;
let sweepStartedAt = null;
let sweepInProgress = false;
const startTime = Date.now();
const sseClients = new Set();

// === Source Health Tracker ===
// Tracks success/fail counts per source across sweeps for reliability scoring
const sourceHealth = {}; // { sourceName: { ok: N, fail: N, lastStatus: 'ok'|'error', lastMs: N } }

function updateSourceHealth(timingMap) {
  for (const [name, info] of Object.entries(timingMap || {})) {
    if (!sourceHealth[name]) sourceHealth[name] = { ok: 0, fail: 0, lastStatus: null, lastMs: 0 };
    if (info.status === 'ok') sourceHealth[name].ok++;
    else                      sourceHealth[name].fail++;
    sourceHealth[name].lastStatus = info.status;
    sourceHealth[name].lastMs     = info.ms || 0;
  }
}

function getSourceHealthSummary() {
  return Object.entries(sourceHealth).map(([name, h]) => {
    const total      = h.ok + h.fail;
    const reliability = total > 0 ? Math.round((h.ok / total) * 100) : null;
    return { name, ok: h.ok, fail: h.fail, reliability, lastStatus: h.lastStatus, lastMs: h.lastMs };
  }).sort((a, b) => (a.reliability ?? 100) - (b.reliability ?? 100)); // worst first
}

// === Delta/Memory ===
const memory = new MemoryManager(RUNS_DIR);
// Restore alertedSignals from Redis if hot.json is missing (Render restart)
memory.initFromRedis().catch(() => {});

// === LLM + Telegram + Discord ===
const llmProvider = createLLMProvider(config.llm);
const telegramAlerter = new TelegramAlerter(config.telegram);

// === Persistence Initialization — restores Redis backups if local files are missing ===
(async () => {
  try {
    await initUsersStore();
    await initLearningStore();
    await initBDStore();
    const { initEntityStore } = await import('./lib/search/entity-store.mjs');
    await initEntityStore();
    console.log('[Persist] All stores initialized');
  } catch (e) {
    console.error('[Persist] Store init error:', e.message);
  }
})();

// === Auth & Push Initialization ===
initAdminUser().catch(err => console.error('[Auth] initAdminUser failed:', err.message));
initVapid().catch(err => console.error('[Push] initVapid failed:', err.message));

// === SMTP Diagnostics ===
const smtpConfigured = !!(process.env.EMAIL_HOST && process.env.EMAIL_USER && process.env.EMAIL_PASS);
if (smtpConfigured) {
  console.log(`[Email] SMTP configured — host:${process.env.EMAIL_HOST} port:${process.env.EMAIL_PORT || 587} user:${process.env.EMAIL_USER}`);
} else {
  const missing = ['EMAIL_HOST','EMAIL_USER','EMAIL_PASS'].filter(k => !process.env[k]);
  console.warn(`[Email] SMTP NOT configured — missing env vars: ${missing.join(', ')} — emails will be logged to console only`);
}

// MONKEY-PATCH: Override _handleBrief on the instance to guarantee the 8-section
// ARKMURUS format even if Seenode's persistent volume has an older telegram.mjs loaded.
// The old telegram.mjs has `handlers = { '/brief': () => this._handleBrief() }` which
// calls this method on the instance — patching here wins regardless of prototype version.
telegramAlerter._handleBrief = async function() {
  console.log('[Telegram] _handleBrief() called — server.mjs monkey-patch ARKMURUS 8-section');
  try {
    const data = await this._getCachedData();
    if (!data) return `⏳ Intelligence data is loading — please try again in 60 seconds.`;

    const ts  = londonTs();
    const ds  = data.delta?.summary || {};
    const dir = ds.direction;
    const vix = data.fred?.find(f => f.id === 'VIXCLS');
    const oil = data.energy || {};
    const corrs = data.correlations || [];
    const critCorrs = corrs.filter(c => c.severity === 'critical' || c.severity === 'high');

    let msg = `*ARKMURUS INTELLIGENCE BRIEF*\n_${ts} London_\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n`;

    // ── 1. LEVERAGEABLE IDEAS ─────────────────────────────────────────────────
    const ideas = data.ideas || [];
    if (ideas.length > 0) {
      msg += `*1. LEVERAGEABLE IDEAS*\n`;
      for (const idea of ideas.slice(0, 3)) {
        const thesis     = idea.thesis || idea.title || idea.text || String(idea);
        const instrument = idea.instrument || idea.sector || '';
        const horizon    = idea.horizon || idea.timeHorizon || '';
        const conf       = idea.confidence || '';
        const catalyst   = idea.catalyst || idea.catalysts?.[0] || '';
        msg += `▸ *${thesis.substring(0, 120)}*\n`;
        if (instrument) msg += `  Instrument: ${instrument}`;
        if (horizon)    msg += ` · Horizon: ${horizon}`;
        if (conf)       msg += ` · Confidence: ${conf}`;
        msg += `\n`;
        if (catalyst)   msg += `  Catalyst: ${catalyst.toString().substring(0, 100)}\n`;
        msg += `\n`;
      }
      if (ideas.length > 3) msg += `_+ ${ideas.length - 3} more ideas in /full_\n\n`;
    } else {
      const topCorr  = critCorrs[0];
      const topAlert = (data.supplyChain?.metrics?.alerts || []).find(a => a.type === 'critical');
      if (topCorr || topAlert) {
        msg += `*1. LEVERAGEABLE IDEAS*\n`;
        if (topCorr) {
          msg += `▸ *${topCorr.region} — multi-source ${topCorr.severity} signal*\n`;
          msg += `  Monitor exposure to ${topCorr.region} counterparties and contracts.\n`;
          msg += `  Horizon: 24–72h · Catalyst: ${topCorr.topSignals?.[0]?.text?.substring(0, 80) || 'see /full'}\n\n`;
        }
        if (topAlert) {
          msg += `▸ *Supply chain stress: ${topAlert.message?.substring(0, 100)}*\n`;
          msg += `  Review procurement timelines and alternative sourcing.\n\n`;
        }
        msg += `_Enable LLM (ANTHROPIC_API_KEY) for full trade ideas with instruments and invalidation criteria._\n\n`;
      }
    }

    // ── 2. EXECUTIVE THESIS ───────────────────────────────────────────────────
    msg += `*2. EXECUTIVE THESIS*\n`;
    const dirLine = dir === 'risk-off' ? '📉 Risk-off — global stress indicators elevated'
                  : dir === 'risk-on'  ? '📈 Risk-on — conditions broadly constructive'
                  : '↔️ Mixed signals — no dominant regime forming yet';
    msg += `${dirLine}.\n`;
    if (critCorrs.length > 0) {
      const regions = critCorrs.slice(0, 3).map(c => c.region).join(', ');
      msg += `Concurrent stress across *${regions}* suggests coordinated pressure, not isolated events.\n`;
    }
    if (ds.criticalChanges > 0) {
      msg += `*${ds.criticalChanges}* indicators crossed critical thresholds this sweep.\n`;
    }
    if (vix?.value > 25) {
      msg += `VIX at *${vix.value}* confirms elevated market anxiety — reduce leverage on new positions.\n`;
    }
    msg += `\n`;

    // ── 3. SITUATION AWARENESS ────────────────────────────────────────────────
    if (critCorrs.length > 0) {
      msg += `*3. SITUATION AWARENESS*\n`;
      for (const c of critCorrs.slice(0, 4)) {
        const badge = c.severity === 'critical' ? '🔴' : '🟠';
        const top   = c.topSignals?.[0]?.text || '';
        msg += `${badge} *${c.region}* [${(c.sourceCount || c.sources?.length || 1)} sources]\n`;
        if (top) msg += `  └ ${top.substring(0, 140)}\n`;
      }
      msg += `\n`;
    }

    // OSINT top signals
    const urgent = data.tg?.urgent || [];
    if (urgent.length > 0) {
      msg += `📡 *OSINT (${urgent.length} signals — top 2)*\n`;
      for (const s of urgent.slice(0, 2)) {
        msg += `• *[${s.channel || 'OSINT'}]* ${(s.text || '').trim().replace(/\n+/g, ' ').substring(0, 160)}\n`;
      }
      msg += `\n`;
    }

    // ── 4. PATTERN RECOGNITION ────────────────────────────────────────────────
    const multiSourceCorrs = corrs.filter(c => (c.sourceCount || c.sources?.length || 0) >= 3);
    if (multiSourceCorrs.length > 0) {
      msg += `*4. PATTERN RECOGNITION*\n`;
      for (const c of multiSourceCorrs.slice(0, 2)) {
        msg += `🔗 *${c.region}* — ${c.sourceCount || c.sources?.length} independent sources converging`;
        const sig2 = c.topSignals?.[1]?.text;
        if (sig2) msg += `: "${sig2.substring(0, 100)}"`;
        msg += `. Pattern: ${c.severity === 'critical' ? 'strengthening' : 'stable'}.\n`;
      }
      msg += `\n`;
    }

    // ── 5. HISTORICAL PARALLELS ───────────────────────────────────────────────
    const parallels = [];
    if (vix?.value > 30 && oil.brent > 90)
      parallels.push({ period: '2022 Russia-Ukraine shock', match: 'VIX >30 + Brent >$90 — energy-driven inflation with geopolitical disruption', lesson: 'Gold and defence names outperformed; commodity exporters gained. Watch for demand destruction at $100+.' });
    if (dir === 'risk-off' && critCorrs.length >= 3)
      parallels.push({ period: 'Q4 2018 / Q1 2020 stress buildup', match: 'Multi-region risk-off with 3+ concurrent stress zones', lesson: 'Historically precedes 10–20% equity drawdowns within 60 days. Monitor credit spreads for confirmation.' });
    if (critCorrs.some(c => c.region === 'Eastern Europe') && vix?.value > 25)
      parallels.push({ period: 'Feb 2022 pre-invasion week', match: 'Eastern Europe critical + VIX spiking', lesson: 'Positions in European defence ETFs and energy hedges outperformed 40–90% in the 6 months after escalation.' });
    if (critCorrs.some(c => c.region === 'Middle East') && oil.brent > 85)
      parallels.push({ period: '2019 Aramco strike / 2024 Red Sea disruption', match: 'Middle East stress + Brent above $85', lesson: 'Maritime insurance premiums spiked 300%; shipping re-routing cost weeks and billions. Logistics and tanker plays outperformed.' });
    if (critCorrs.some(c => c.region === 'Lusophone Africa') || critCorrs.some(c => c.region === 'West Africa'))
      parallels.push({ period: '2012–2015 Sahel destabilisation', match: 'Lusophone/West Africa stress signals', lesson: 'Arkmurus advantage: instability in the region historically precedes 18–36 month procurement surges for border and peacekeeping equipment.' });
    if (parallels.length > 0) {
      msg += `*5. HISTORICAL PARALLELS*\n`;
      for (const p of parallels.slice(0, 2)) {
        msg += `📜 *Rhymes with: ${p.period}*\n`;
        msg += `Match: ${p.match}\n`;
        msg += `Lesson: ${p.lesson}\n\n`;
      }
    }

    // ── 6. MARKET & ASSET IMPLICATIONS ───────────────────────────────────────
    const hasMarketData = vix?.value || oil.brent;
    if (hasMarketData) {
      msg += `*6. MARKET & ASSET IMPLICATIONS*\n`;
      if (vix?.value) msg += `• Volatility (VIX): *${vix.value}* — ${vix.value > 30 ? '🔴 extreme stress' : vix.value > 20 ? '🟠 elevated' : '🟢 normal'}\n`;
      if (oil.brent)  msg += `• Brent crude: *$${oil.brent}* · WTI: *$${oil.wti || '--'}*\n`;
      const scMats = (data.supplyChain?.metrics?.rawMaterials || []).filter(m => m.risk === 'critical' || m.risk === 'high').slice(0, 3);
      for (const m of scMats) msg += `• ${m.name}: *${m.price}* (${m.change}) — ${m.impact}\n`;
      msg += `\n`;
    }

    // ── 7. DECISION BOARD ─────────────────────────────────────────────────────
    msg += `*7. DECISION BOARD*\n`;
    const topIdea = ideas[0];
    msg += `• Best long: ${topIdea ? topIdea.instrument || topIdea.thesis?.substring(0, 60) : 'await multi-source confirmation'}\n`;
    const sanctions = data.opensanctions?.preDesignation || [];
    msg += `• Best hedge: ${sanctions.length > 0 ? `Exposure review — ${sanctions.length} pre-designation signal(s)` : dir === 'risk-off' ? 'Gold / defensive assets' : 'Monitor VIX for entry'}\n`;
    const topWatch = critCorrs[0];
    msg += `• Watch: ${topWatch ? `${topWatch.region} — next 24–72h` : 'No critical zones currently'}\n`;
    if (ds.totalChanges > 0) msg += `• Monitor: ${ds.totalChanges} delta changes — confirm or reverse in next sweep\n`;
    // BD Brain priority — most actionable BD signal right now
    const bd = data.bdIntelligence;
    const brainPriority = bd?.brain?.weeklyPriority;
    const topTender = bd?.tenders?.[0];
    if (brainPriority?.action) {
      msg += `\n⚡ *BD BRAIN — TOP PRIORITY*\n`;
      msg += `${brainPriority.action.substring(0, 200)}\n`;
      if (brainPriority.whyNow) msg += `_Why now: ${brainPriority.whyNow.substring(0, 120)}_\n`;
    } else if (topTender) {
      msg += `\n🎯 *BD — ACTIVE TENDER*\n`;
      msg += `${topTender.market}: ${topTender.title.substring(0, 100)}\n`;
      if (topTender.winProbability != null) msg += `Win probability: *${topTender.winProbability}%*\n`;
    }
    msg += `\n`;

    // ── 8. SOURCE INTEGRITY ───────────────────────────────────────────────────
    const srcOk    = data.meta?.sourcesOk || 0;
    const srcTotal = data.meta?.sourcesQueried || 0;
    const srcFail  = data.meta?.sourcesFailed || 0;
    msg += `*8. SOURCE INTEGRITY*\n`;
    msg += `${srcOk}/${srcTotal} sources delivered data`;
    if (srcFail > 0) msg += ` · ${srcFail} degraded`;
    const hasLLM = ideas.length > 0 && data.ideasSource === 'llm';
    msg += `\nThesis basis: ${hasLLM ? 'LLM synthesis + hard data' : 'hard data only — LLM not active'}`;
    msg += `\n`;

    msg += `\n━━━━━━━━━━━━━━━━━━━━━━━━\n`;
    msg += `_/full · /osint · /supply · /arms · /predict · /ask [topic]_`;

    return msg;
  } catch (error) {
    return `Brief failed: ${error.message}`;
  }
};

const discordAlerter = new DiscordAlerter(config.discord || {});

if (llmProvider) console.log(`[Crucix] LLM enabled: ${llmProvider.name} (${llmProvider.model})`);
if (telegramAlerter.isConfigured) {
  console.log('[Crucix] Telegram alerts enabled');

  telegramAlerter.onCommand('/status', async () => {
    const uptime = Math.floor((Date.now() - startTime) / 1000);
    const h = Math.floor(uptime / 3600);
    const m = Math.floor((uptime % 3600) / 60);
    const sourcesOk = currentData?.meta?.sourcesOk || 0;
    const sourcesTotal = currentData?.meta?.sourcesQueried || 0;
    const sourcesFailed = currentData?.meta?.sourcesFailed || 0;
    const llmStatus = llmProvider?.isConfigured ? `✅ ${llmProvider.name}` : '❌ Disabled';
    const nextSweep = lastSweepTime
      ? logTimeShort(new Date(new Date(lastSweepTime).getTime() + config.refreshIntervalMinutes * 60000))
      : 'pending';
    return [
      `🖥️ *CRUCIX STATUS*`, ``,
      `Uptime: ${h}h ${m}m`,
      `Last sweep: ${lastSweepTime ? logTimeShort(new Date(lastSweepTime)) + ' London' : 'never'}`,
      `Next sweep: ${nextSweep} London`,
      `Sweep in progress: ${sweepInProgress ? '🔄 Yes' : '⏸️ No'}`,
      `Sources: ${sourcesOk}/${sourcesTotal} OK${sourcesFailed > 0 ? ` (${sourcesFailed} failed)` : ''}`,
      `LLM: ${llmStatus}`,
      `SSE clients: ${sseClients.size}`,
      `Dashboard: http://localhost:${config.port}`,
    ].join('\n');
  });

  telegramAlerter.onCommand('/sweep', async () => {
    if (sweepInProgress) return '🔄 Sweep already in progress. Please wait.';
    runSweepCycle().catch(err => console.error('[Crucix] Manual sweep failed:', err.message));
    return '🚀 Manual sweep triggered. You\'ll receive alerts if anything significant is detected.';
  });

  // /brief handled by telegram.mjs _handleBrief() — 8-section BRIEFING_PROMPT.md format

  telegramAlerter.onCommand('/portfolio', async () => {
    return '📊 Portfolio integration requires Alpaca MCP connection.\nUse the Crucix dashboard or Claude agent for portfolio queries.';
  });

  telegramAlerter.onCommand('/trends', async () => {
    const trends     = analyzeTrends();
    const trajectory = analyzeEntityTrajectory(14);
    const msg1 = formatTrendsForTelegram(trends);
    const msg2 = formatEntityTrajectoryForTelegram(trajectory);
    return msg1 + '\n\n' + msg2;
  });

  telegramAlerter.onCommand('/entities', async () => {
    const trajectory = analyzeEntityTrajectory(14);
    return formatEntityTrajectoryForTelegram(trajectory);
  });

  telegramAlerter.onCommand('/correlations', async () => {
    if (!currentData) return 'No data yet — waiting for first sweep.';
    const correlations = correlate(currentData);
    return formatCorrelationsForTelegram(correlations) || 'No significant convergences detected.';
  });

  telegramAlerter.onCommand('/sanctions', async () => {
    if (!currentData) return 'No data yet.';
    const recent = currentData.opensanctions?.recent || [];
    if (recent.length === 0) return 'No recent sanctions updates.';
    let msg = '*RECENT SANCTIONS UPDATES*\n\n';
    for (const e of recent.slice(0, 8)) msg += `• ${e.name} — ${e.datasets.join(', ')}\n`;
    return msg;
  });

  // ── Self-Learning Commands ────────────────────────────────────────────────

  telegramAlerter.onCommand('/opportunities', async () => {
    const stored = getOpportunities();
    const opps = stored.opportunities || [];
    // Refresh from current data if available
    if (currentData) {
      const fresh = detectOpportunities(currentData);
      return formatOpportunitiesForTelegram(fresh);
    }
    return formatOpportunitiesForTelegram(opps);
  });

  telegramAlerter.onCommand('/bd', async () => {
    const bd = currentData?.bdIntelligence || getBDIntelligence();
    return formatBDSummaryForTelegram(bd);
  });

  telegramAlerter.onCommand('/patterns', async () => {
    const stored = getPatterns();
    return formatPatternsForTelegram(stored);
  });

  telegramAlerter.onCommand('/explore', async (args) => {
    if (args && args.trim()) {
      const query = args.trim();
      const result = await exploreQuery(llmProvider, query);
      if (result.error) return `❌ ${result.error}`;
      let msg = `🌐 *EXPLORATION: ${query}*\n\n`;
      if (result.analysis) msg += result.analysis.substring(0, 1800);
      else msg += result.results.slice(0, 3).map(r => `▸ *${r.title}*\n${r.snippet?.substring(0, 100)}`).join('\n\n');
      return msg;
    }
    // Full sweep exploration
    const findings = await runExploration(llmProvider);
    return formatExplorerFindingsForTelegram(findings);
  });

  telegramAlerter.onCommand('/learn', async (args) => {
    const parts = (args || '').trim().split(/\s+/);
    const subCmd = parts[0];

    if (subCmd === 'status') {
      const stats = getLearningStats();
      const acc = stats.outcomes.accuracy !== null ? `${stats.outcomes.accuracy}%` : 'n/a (need outcomes)';
      return [
        '*🧠 LEARNING STATUS*', '',
        `*Outcomes tracked:* ${stats.outcomes.total} (${stats.outcomes.confirmed} confirmed, ${stats.outcomes.dismissed} dismissed)`,
        `*Signal accuracy:* ${acc}`,
        `*Sources — healthy:* ${stats.sources.healthy} · degraded: ${stats.sources.degraded} · critical: ${stats.sources.critical}`,
        `*Patterns detected:* ${stats.patternCount}`,
        `*Opportunities found:* ${stats.opportunityCount}`,
        '',
        `_/learn confirm <hash> · /learn dismiss <hash>_`,
        `_/sources for per-source reliability_`,
      ].join('\n');
    }

    if ((subCmd === 'confirm' || subCmd === 'dismiss') && parts[1]) {
      const hash = parts[1];
      const outcome = subCmd;
      recordAlertOutcome(hash, '', outcome, {});
      return `✅ Alert ${hash.substring(0, 12)}… marked as *${outcome}*\nLearning weights updated.`;
    }

    return [
      '*🧠 LEARN COMMANDS*', '',
      '`/learn status` — learning accuracy stats',
      '`/learn confirm <id>` — mark alert as accurate',
      '`/learn dismiss <id>` — mark alert as false alarm',
    ].join('\n');
  });

  telegramAlerter.onCommand('/sources', async (args) => {
    const history = getSourceHistory();
    if (history.length === 0) return '📡 No source history yet — runs after first sweep.';

    const critical  = history.filter(s => s.status === 'critical');
    const degraded  = history.filter(s => s.status === 'degraded');
    const healthy   = history.filter(s => s.status === 'healthy');

    let msg = `*📡 SOURCE HEALTH (${history.length} sources)*\n`;
    msg += `🟢 ${healthy.length} healthy · 🟠 ${degraded.length} degraded · 🔴 ${critical.length} critical\n\n`;

    if (critical.length > 0) {
      msg += `*🔴 CRITICAL (fix needed)*\n`;
      for (const s of critical.slice(0, 5)) {
        msg += `▸ ${s.name} — ${s.reliability ?? '?'}% reliability\n`;
      }
      msg += '\n';
    }
    if (degraded.length > 0) {
      msg += `*🟠 DEGRADED (monitor)*\n`;
      for (const s of degraded.slice(0, 5)) {
        msg += `▸ ${s.name} — ${s.reliability ?? '?'}% reliability\n`;
      }
    }
    msg += `\n_/sources fix <name> to auto-repair · /sources all for full list_`;
    return msg;
  });

  telegramAlerter.onCommand('/update', async (args) => {
    const parts = (args || '').trim().split(/\s+/);
    const subCmd = parts[0];

    if (!subCmd || subCmd === 'status') {
      const staged  = getStagedModules();
      const managed = getAutoManagedModules();
      const log     = getUpdateLog(3);
      let msg = `*🔧 SELF-UPDATE STATUS*\n\n`;
      msg += `Auto-managed sources: ${managed.length > 0 ? managed.join(', ') : 'none yet'}\n`;
      msg += `Staged for deployment: ${staged.length}\n`;
      if (staged.length > 0) msg += staged.map(s => `  ▸ ${s.name} (${s.type || 'new'})`).join('\n') + '\n';
      msg += '\n*Recent activity:*\n';
      for (const entry of log) {
        msg += `▸ ${entry.action} — ${entry.timestamp?.substring(0, 16).replace('T', ' ')}\n`;
      }
      msg += '\n_/update add <description> — generate new source_\n_/update apply <name> — deploy staged module_\n_/update staged — list staged modules_';
      return msg;
    }

    if (subCmd === 'staged') {
      return formatStagedForTelegram(getStagedModules());
    }

    if (subCmd === 'add' && parts.length >= 2) {
      const description = parts.slice(1).join(' ');
      const moduleName = description
        .toLowerCase()
        .replace(/[^a-z0-9\s]/g, '')
        .trim()
        .replace(/\s+/g, '_')
        .substring(0, 30);

      if (!llmProvider?.isConfigured) {
        return '❌ LLM not configured — set ANTHROPIC_API_KEY to enable code generation';
      }

      const reply = await telegramAlerter._sendText?.('⏳ Generating source module — this takes ~30s...');
      const result = await generateSourceModule(llmProvider, description, moduleName);

      if (!result.success) return `❌ Generation failed: ${result.error}`;

      stageModule(result.moduleName, result.code, { type: 'new', description });
      return `✅ *Source module generated and staged*\nName: \`${result.moduleName}\`\nLines: ${result.code.split('\n').length}\n\nTo deploy: \`/update apply ${result.moduleName}\`\nTo preview: \`/update preview ${result.moduleName}\``;
    }

    if (subCmd === 'apply' && parts[1]) {
      const moduleName = parts[1];
      const result = await deployModule(moduleName);
      return result.success ? `✅ ${result.message}` : `❌ Deploy failed: ${result.error}`;
    }

    if (subCmd === 'discard' && parts[1]) {
      const { unlinkSync, existsSync } = await import('node:fs');
      const stagePath = join(ROOT, 'runs', 'staged', `${parts[1]}.mjs.staged`);
      if (existsSync(stagePath)) {
        unlinkSync(stagePath);
        try { unlinkSync(stagePath + '.meta.json'); } catch {}
        return `🗑️ Staged module \`${parts[1]}\` discarded`;
      }
      return `❌ No staged module named: ${parts[1]}`;
    }

    if (subCmd === 'preview' && parts[1]) {
      const code = getStagedCode(parts[1]);
      if (!code) return `❌ No staged module: ${parts[1]}`;
      const preview = code.substring(0, 800);
      return `*Preview: ${parts[1]}*\n\`\`\`\n${preview}\n\`\`\`${code.length > 800 ? `\n_...${code.length - 800} more chars_` : ''}`;
    }

    if (subCmd === 'fix' && parts[1]) {
      const sourceName = parts[1];
      const sourceHistory = getSourceHistory();
      const srcInfo = sourceHistory.find(s => s.name.toLowerCase() === sourceName.toLowerCase());
      const errorMsg = srcInfo?.status === 'critical' ? `Source ${sourceName} has ${srcInfo.reliability}% reliability` : `Source ${sourceName} reported as failing`;

      if (!llmProvider?.isConfigured) return '❌ LLM required for auto-fix';

      const result = await generateSourceFix(llmProvider, sourceName, errorMsg);
      if (!result.success) return `❌ Fix generation failed: ${result.error}`;

      stageModule(result.moduleName, result.code, { type: 'fix', description: `Auto-fix for ${sourceName}`, originalError: errorMsg });
      return `🔧 Fix generated for \`${sourceName}\`\nTo apply: \`/update apply ${result.moduleName}\``;
    }

    if (subCmd === 'rollback' && parts[1]) {
      const result = rollbackModule(parts[1]);
      return result.success ? `⏪ ${result.message}` : `❌ Rollback failed: ${result.error}`;
    }

    return [
      '*🔧 UPDATE COMMANDS*', '',
      '`/update status` — show managed sources + recent activity',
      '`/update add <description>` — generate new source module',
      '`/update staged` — list modules awaiting deployment',
      '`/update apply <name>` — deploy a staged module',
      '`/update preview <name>` — preview staged module code',
      '`/update fix <source>` — auto-fix a broken source',
      '`/update discard <name>` — discard staged module',
      '`/update rollback <name>` — rollback to previous version',
    ].join('\n');
  });

  // /aria command — full ARIA Telegram interface
  telegramAlerter.onCommand('/aria', async (args, chatId, userId) => {
    await handleAriaCommand(chatId || config.telegram.chatId, userId || '', args || '');
    return null; // handleAriaCommand sends directly
  });

  telegramAlerter.startPolling(config.telegram.botPollingInterval);
}

// === Discord Bot ===
if (discordAlerter.isConfigured) {
  console.log('[Crucix] Discord bot enabled');

  discordAlerter.onCommand('status', async () => {
    const uptime = Math.floor((Date.now() - startTime) / 1000);
    const h = Math.floor(uptime / 3600);
    const m = Math.floor((uptime % 3600) / 60);
    const sourcesOk = currentData?.meta?.sourcesOk || 0;
    const sourcesTotal = currentData?.meta?.sourcesQueried || 0;
    const sourcesFailed = currentData?.meta?.sourcesFailed || 0;
    const llmStatus = llmProvider?.isConfigured ? `✅ ${llmProvider.name}` : '❌ Disabled';
    const nextSweep = lastSweepTime
      ? logTimeShort(new Date(new Date(lastSweepTime).getTime() + config.refreshIntervalMinutes * 60000))
      : 'pending';
    return [
      `**🖥️ CRUCIX STATUS**\n`,
      `Uptime: ${h}h ${m}m`,
      `Last sweep: ${lastSweepTime ? logTimeShort(new Date(lastSweepTime)) + ' London' : 'never'}`,
      `Next sweep: ${nextSweep} London`,
      `Sweep in progress: ${sweepInProgress ? '🔄 Yes' : '⏸️ No'}`,
      `Sources: ${sourcesOk}/${sourcesTotal} OK${sourcesFailed > 0 ? ` (${sourcesFailed} failed)` : ''}`,
      `LLM: ${llmStatus}`,
      `SSE clients: ${sseClients.size}`,
      `Dashboard: http://localhost:${config.port}`,
    ].join('\n');
  });

  discordAlerter.onCommand('sweep', async () => {
    if (sweepInProgress) return '🔄 Sweep already in progress. Please wait.';
    runSweepCycle().catch(err => console.error('[Crucix] Manual sweep failed:', err.message));
    return '🚀 Manual sweep triggered. You\'ll receive alerts if anything significant is detected.';
  });

  discordAlerter.onCommand('brief', async () => {
    if (!currentData) return '⏳ No data yet — waiting for first sweep to complete.';
    const tg = currentData.tg || {};
    const energy = currentData.energy || {};
    const delta = memory.getLastDelta();
    const ideas = (currentData.ideas || []).slice(0, 3);
    const sections = [`**📋 CRUCIX BRIEF**\n_${londonTs()} London_\n`];
    if (delta?.summary) {
      const dirEmoji = { 'risk-off': '📉', 'risk-on': '📈', 'mixed': '↔️' }[delta.summary.direction] || '↔️';
      sections.push(`${dirEmoji} Direction: **${delta.summary.direction.toUpperCase()}** | ${delta.summary.totalChanges} changes, ${delta.summary.criticalChanges} critical\n`);
    }
    const vix = currentData.fred?.find(f => f.id === 'VIXCLS');
    const hy = currentData.fred?.find(f => f.id === 'BAMLH0A0HYM2');
    if (vix || energy.wti) {
      sections.push(`📊 VIX: ${vix?.value || '--'} | WTI: $${energy.wti || '--'} | Brent: $${energy.brent || '--'}`);
      if (hy) sections.push(`   HY Spread: ${hy.value} | NatGas: $${energy.natgas || '--'}`);
      sections.push('');
    }
    if (tg.urgent?.length > 0) {
      sections.push(`📡 OSINT: ${tg.urgent.length} urgent signals, ${tg.posts || 0} total posts`);
      for (const p of tg.urgent.slice(0, 2)) sections.push(`  • ${(p.text || '').substring(0, 80)}`);
      sections.push('');
    }
    if (ideas.length > 0) {
      sections.push(`**💡 Top Ideas:**`);
      for (const idea of ideas) sections.push(`  ${idea.type === 'long' ? '📈' : idea.type === 'hedge' ? '🛡️' : '👁️'} ${idea.title}`);
    }
    return sections.join('\n');
  });

  discordAlerter.onCommand('portfolio', async () => {
    return '📊 Portfolio integration requires Alpaca MCP connection.\nUse the Crucix dashboard or Claude agent for portfolio queries.';
  });

  discordAlerter.start().catch(err => {
    console.error('[Crucix] Discord bot startup failed (non-fatal):', err.message);
  });
}

// === Express Server ===
const app = express();

// Trust proxy — required on Seenode/Render/Railway (behind reverse proxy)
// Without this, express-rate-limit sees all users as same IP (the proxy)
app.set('trust proxy', 1);

// ── Security headers ──────────────────────────────────────────────────────────
applySecurityHeaders(app);

// ── Request body parsing — tiered limits ─────────────────────────────────────
app.use('/api/aria',  express.json({ limit: '500kb' }));
app.use('/api/brain', express.json({ limit: '500kb' }));
app.use('/api/',      express.json({ limit: '100kb' }));
app.use('/api/',      express.urlencoded({ extended: true, limit: '50kb' }));
app.use(express.json());  // fallback for non-API routes

// ── Rate limiting + XSS guard — BEFORE route registration ────────────────────
applyRateLimiting(app);
applyInputValidation(app);

// ── Observability — structured error logging ──────────────────────────────────
const ADMIN_CHAT_ID = process.env.TELEGRAM_ADMIN_CHAT_ID || process.env.TELEGRAM_CHAT_ID;
const notifyAdmin = async (msg) => {
  if (!telegramAlerter?.isConfigured) return;
  try { await telegramAlerter.sendMessage?.(msg); } catch {}
};
configureTelemetry(redisAdapter, notifyAdmin);

// ── Procurement dedup + source pruner ────────────────────────────────────────
const procDedup   = new ProcurementDedup(redisAdapter);
const sourcePruner = new SourcePruner(redisAdapter, notifyAdmin);

// Wire pruner + errorTracker into sweep source runner
registerSourceHooks({
  onSuccess: (name, latencyMs) => {
    sourcePruner.recordFetch(name, true,  latencyMs).catch(() => {});
    errorTracker.recordSuccess(name);
  },
  onError: (name, err, latencyMs) => {
    sourcePruner.recordFetch(name, false, latencyMs).catch(() => {});
    errorTracker.record(name, 'fetch_error', err);
  },
  isSuspended: (name) => sourcePruner.isSuspended(name),
});

// ── Compliance list auto-refresh (weekly, non-blocking) ───────────────────────
if (redisAdapter.isConfigured) {
  startComplianceRefreshScheduler(redisAdapter, notifyAdmin).catch(e =>
    console.warn('[Compliance] Refresh scheduler failed to start:', e.message)
  );
}

// ── Inject LLM provider into Telegram ARIA commands ──────────────────────────
setTelegramLLM(llmProvider);

// Site access is protected by the Angular JWT auth layer — no HTTP Basic Auth needed.

// Static HTML dashboard — served from public/
const PUBLIC_DIR = join(ROOT, 'public');
app.use(express.static(PUBLIC_DIR));
app.get('/', (req, res) => res.redirect('/signin.html'));
console.log('[Crucix] Static dashboard live at /');

app.get('/api/data', requireAuth, (req, res) => {
  if (!currentData) return res.status(503).json({ error: 'No data yet — first sweep in progress' });
  res.json(currentData);
});

app.get('/api/health', (req, res) => {
  res.json({
    status: 'ok',
    uptime: Math.floor((Date.now() - startTime) / 1000),
    lastSweep: lastSweepTime,
    nextSweep: lastSweepTime
      ? new Date(new Date(lastSweepTime).getTime() + config.refreshIntervalMinutes * 60000).toISOString()
      : null,
    sweepInProgress,
    sweepStartedAt,
    sourcesOk: currentData?.meta?.sourcesOk || 0,
    sourcesFailed: currentData?.meta?.sourcesFailed || 0,
    sourcesTotal: currentData?.meta?.sourcesQueried || 36,
    llmEnabled: !!config.llm.provider,
    llmProvider: config.llm.provider,
    telegramEnabled: !!(config.telegram.botToken && config.telegram.chatId),
    refreshIntervalMinutes: config.refreshIntervalMinutes,
    language: currentLanguage,
  });
});

app.get('/api/source-health', requireAuth, (req, res) => {
  const summary = getSourceHealthSummary();
  const degraded = summary.filter(s => s.reliability !== null && s.reliability < 80);
  res.json({
    sources:       summary,
    degraded:      degraded.map(s => s.name),
    totalTracked:  summary.length,
    healthyCount:  summary.filter(s => s.reliability === null || s.reliability >= 80).length,
    degradedCount: degraded.length,
    asOf:          lastSweepTime,
  });
});

app.get('/api/locales', (req, res) => {
  res.json({ current: currentLanguage, supported: getSupportedLocales() });
});

app.get('/api/search', requireAuth, async (req, res) => {
  const query = req.query.q;
  if (!query) return res.json({ error: 'No query provided' });
  console.log(`[Search] "${query}"`);
  try {
    const { runSearch } = await import('./lib/search/engine.mjs');
    const result = await runSearch(query, currentData);
    res.json({ success: true, ...result });
  } catch (error) {
    console.error('[Search] Error:', error);
    res.json({ success: false, error: error.message });
  }
});

// ── Deep intelligence search — SSE streaming ──────────────────────────────────
// EventSource cannot set headers — accept token via query param for this endpoint only
app.get('/api/search/deep', async (req, res) => {
  const token = req.headers.authorization?.replace('Bearer ', '') || req.query.token;
  if (!token) return res.status(401).json({ error: 'Authentication required' });
  try {
    const payload = verifyToken(token);
    req.user = payload;
  } catch { return res.status(401).json({ error: 'Invalid token' }); }

  const query = req.query.q?.trim();
  if (!query || query.length < 2) return res.status(400).json({ error: 'Query required' });

  // SSE headers
  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');
  res.setHeader('X-Accel-Buffering', 'no');
  res.flushHeaders();

  const send = (data) => {
    try { res.write(`data: ${JSON.stringify(data)}\n\n`); } catch {}
  };

  console.log(`[DeepSearch] "${query}" by ${req.user?.email || 'unknown'}`);
  try {
    const { runDeepSearch } = await import('./lib/search/deep-engine.mjs');
    const result = await runDeepSearch(query, {
      cachedData:  currentData,
      llmProvider,
      onEvent:     send,
    });
    send({ type: 'result', data: result });
  } catch (err) {
    console.error('[DeepSearch] Error:', err.message);
    send({ type: 'error', message: err.message });
  } finally {
    res.end();
  }
});

// ── Power entity search ────────────────────────────────────────────────────────
app.get('/api/search/entity', requireAuth, async (req, res) => {
  const query = req.query.q;
  if (!query || query.trim().length < 2) return res.status(400).json({ error: 'Query required' });
  console.log(`[EntitySearch] "${query}"`);
  try {
    const { runEntitySearch } = await import('./lib/search/engine.mjs');
    const result = await runEntitySearch(query.trim(), currentData, llmProvider);
    res.json({ success: true, ...result });
  } catch (error) {
    console.error('[EntitySearch] Error:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

app.post('/api/sweep', requireAuth, async (req, res) => {
  try {
    if (sweepInProgress) return res.json({ success: false, message: 'Sweep already in progress' });
    runSweepCycle().catch(err => console.error('[Crucix] Manual sweep failed:', err.message));
    res.json({ success: true, message: 'Sweep triggered' });
  } catch (error) {
    res.json({ success: false, error: error.message });
  }
});

// ── Self-Learning API ─────────────────────────────────────────────────────────

app.get('/api/learning/stats', requireAuth, (req, res) => {
  res.json(getLearningStats());
});

app.get('/api/learning/outcomes', requireAuth, (req, res) => {
  const limit = parseInt(req.query.limit) || 50;
  res.json(getOutcomes(limit));
});

app.post('/api/learning/outcome', requireAuth, (req, res) => {
  const { hash, text, outcome, source, region, tier } = req.body || {};
  if (!hash || !outcome) return res.status(400).json({ error: 'hash and outcome required' });
  if (!['confirmed', 'dismissed', 'pending'].includes(outcome)) {
    return res.status(400).json({ error: 'outcome must be confirmed|dismissed|pending' });
  }
  const entry = recordAlertOutcome(hash, text || '', outcome, { source, region, tier });
  res.json({ success: true, entry });
});

app.get('/api/opportunities', requireAuth, (req, res) => {
  if (currentData) {
    const fresh = detectOpportunities(currentData);
    return res.json({ opportunities: fresh, source: 'live', asOf: lastSweepTime });
  }
  const stored = getOpportunities();
  res.json({ ...stored, source: 'cached' });
});

app.get('/api/bd-intelligence', requireAuth, (req, res) => {
  if (currentData?.bdIntelligence) return res.json(currentData.bdIntelligence);
  const stored = getBDIntelligence();
  if (stored) return res.json(stored);
  res.json({ tenders: [], ideas: [], strategy: null, pipeline: [], counts: { activeTenders: 0, contractAwards: 0, strategicIdeas: 0, pipelineDeals: 0 } });
});

app.get('/api/bd-intelligence/pipeline', requireAuth, (req, res) => {
  res.json(getDealPipeline());
});

app.post('/api/bd-intelligence/pipeline/:id/stage', requireAuth, (req, res) => {
  const { id } = req.params;
  const { stage, notes } = req.body || {};
  if (!stage) return res.status(400).json({ error: 'stage required' });
  const result = updateDealStage(id, stage, notes || '');
  res.json(result);
});

app.post('/api/bd-intelligence/pipeline/:id/outcome', requireAuth, (req, res) => {
  const { id } = req.params;
  const { market, type, outcome, reason } = req.body || {};
  if (!outcome || !['WON', 'LOST', 'NO_BID'].includes(outcome)) {
    return res.status(400).json({ error: 'outcome must be WON, LOST, or NO_BID' });
  }
  try {
    recordOutcome(id, market || 'Unknown', type || 'TENDER', outcome, reason || '');
    res.json({ ok: true, dealId: id, outcome });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.post('/api/bd-intelligence/feedback', requireAuth, (req, res) => {
  // Thumbs up/down on brain leads or tenders
  const { signalText, market, feedback, reason } = req.body || {};
  if (!feedback || !['positive', 'negative'].includes(feedback)) {
    return res.status(400).json({ error: 'feedback must be positive or negative' });
  }
  try {
    const outcome = feedback === 'positive' ? 'confirmed' : 'dismissed';
    // Reuse alert outcome recording to feed source weighting
    const hash = Buffer.from((signalText || '').slice(0, 80)).toString('base64').slice(0, 16);
    recordAlertOutcome(hash, signalText || '', outcome, { source: market, region: market, tier: 'bd' });
    if (market && feedback === 'positive') {
      recordOutcome(hash, market, 'LEAD', 'WON', reason || 'user confirmed lead');
    } else if (market && feedback === 'negative') {
      recordOutcome(hash, market, 'LEAD', 'LOST', reason || 'user dismissed lead');
    }
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// ── Compliance pre-screening ──────────────────────────────────────────────────
app.post('/api/compliance/screen', requireAuth, (req, res) => {
  const { sellerCountry, buyerCountry, productCategory, dealValueUSD, notes } = req.body || {};
  if (!sellerCountry || !buyerCountry || !productCategory) {
    return res.status(400).json({ error: 'sellerCountry, buyerCountry, productCategory required' });
  }
  try {
    const result = screenDeal({ sellerCountry, buyerCountry, productCategory, dealValueUSD, notes, brokerCountry: 'GB' });
    res.json(result);
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.get('/api/compliance/products', requireAuth, (req, res) => {
  res.json(getProductCategories());
});

// ── Shareable brief ───────────────────────────────────────────────────────────
// In-memory fallback for share tokens when Redis not configured
const _shareStore = new Map();

app.post('/api/share/brief', requireAuth, async (req, res) => {
  const bd = getBDIntelligence();
  if (!bd) return res.status(503).json({ error: 'No BD data available — run a sweep first' });

  const token    = [...Array(24)].map(() => Math.random().toString(36)[2]).join('');
  const expiresAt = Date.now() + 7 * 24 * 60 * 60 * 1000; // 7 days
  const payload  = { bd, createdAt: new Date().toISOString(), expiresAt };

  try {
    await redisSet(`crucix:share:${token}`, payload, 7 * 24 * 3600);
  } catch {
    _shareStore.set(token, payload);
    setTimeout(() => _shareStore.delete(token), 7 * 24 * 60 * 60 * 1000);
  }

  const host = req.get('host');
  const proto = req.headers['x-forwarded-proto'] || 'https';
  res.json({ token, url: `${proto}://${host}/s/${token}`, expiresAt: new Date(expiresAt).toISOString() });
});

app.get('/s/:token', async (req, res) => {
  const { token } = req.params;
  if (!/^[a-z0-9]{20,30}$/.test(token)) return res.status(400).send('Invalid token');

  let payload;
  try {
    payload = await redisGet(`crucix:share:${token}`);
  } catch {}
  if (!payload) payload = _shareStore.get(token);
  if (!payload || Date.now() > payload.expiresAt) return res.status(404).send('<h2>Brief not found or expired</h2>');

  const { bd } = payload;
  const hot  = (bd.brain?.salesLeads || []).filter(l => l.urgency === 'HOT');
  const warm = (bd.brain?.salesLeads || []).filter(l => l.urgency === 'WARM');
  const tenders = (bd.tenders || []).filter(t => t.leadQuality === 'HOT' || t.leadQuality === 'WARM');
  const strat = bd.strategy;

  const html = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Arkmurus BD Intelligence Brief</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f6fa; color: #1a2332; line-height: 1.6; }
  .header { background: #1a2332; color: #fff; padding: 28px 40px; }
  .header h1 { font-size: 1.5rem; font-weight: 700; letter-spacing: -0.3px; }
  .header .sub { font-size: 0.85rem; color: #90a4ae; margin-top: 4px; }
  .container { max-width: 900px; margin: 0 auto; padding: 32px 24px; }
  .section { margin-bottom: 28px; }
  .section-title { font-size: 0.7rem; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; color: #78909c; margin-bottom: 12px; }
  .card { background: #fff; border-radius: 8px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); padding: 16px 20px; margin-bottom: 10px; border-left: 4px solid #ccc; }
  .card.hot { border-left-color: #e53935; }
  .card.warm { border-left-color: #ff9800; }
  .card.strategy { border-left-color: #7b1fa2; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.68rem; font-weight: 700; color: #fff; margin-right: 6px; }
  .hot-badge { background: #e53935; }
  .warm-badge { background: #ff9800; }
  .label { font-size: 0.72rem; color: #78909c; }
  .val { font-size: 0.85rem; color: #1a2332; margin-left: 6px; }
  .title { font-size: 0.95rem; font-weight: 600; color: #1a2332; margin: 6px 0; }
  .next-step { background: #f0faf4; border-left: 3px solid #4caf50; padding: 8px 12px; border-radius: 0 4px 4px 0; font-size: 0.82rem; color: #2e7d32; margin-top: 8px; }
  .meta { font-size: 0.75rem; color: #90a4ae; margin-top: 6px; }
  a { color: #1976d2; }
  .footer { text-align: center; font-size: 0.72rem; color: #90a4ae; padding: 20px; border-top: 1px solid #e0e0e0; margin-top: 32px; }
  .disclaimer { background: #fff8e1; border: 1px solid #ffe082; border-radius: 6px; padding: 10px 14px; font-size: 0.78rem; color: #5d4037; margin-top: 24px; }
</style>
</head>
<body>
<div class="header">
  <h1>Arkmurus BD Intelligence Brief</h1>
  <div class="sub">Generated ${new Date(payload.createdAt).toUTCString()} &nbsp;·&nbsp; Valid 7 days</div>
</div>
<div class="container">

${hot.length > 0 ? `
<div class="section">
  <div class="section-title">🔥 HOT Sales Leads — Act Now</div>
  ${hot.map(l => `
  <div class="card hot">
    <span class="badge hot-badge">HOT</span>
    <strong>${escHtml(l.market)}</strong>
    ${l.estimatedValue ? `<span style="float:right;font-weight:700;color:#e53935">${escHtml(l.estimatedValue)}</span>` : ''}
    <div class="title">${escHtml(l.lead)}</div>
    ${l.procurementAuthority ? `<div><span class="label">Authority:</span><span class="val">${escHtml(l.procurementAuthority)}</span></div>` : ''}
    ${l.oemRecommendation ? `<div><span class="label">OEM:</span><span class="val">${escHtml(l.oemRecommendation)}</span></div>` : ''}
    ${l.nextStep ? `<div class="next-step"><strong>Next 48h:</strong> ${escHtml(l.nextStep)}</div>` : ''}
    ${l.portalUrl ? `<div class="meta"><a href="${escHtml(l.portalUrl)}" target="_blank">Procurement Portal →</a></div>` : ''}
  </div>`).join('')}
</div>` : ''}

${warm.length > 0 ? `
<div class="section">
  <div class="section-title">⚡ WARM Leads — Qualify This Week</div>
  ${warm.map(l => `
  <div class="card warm">
    <span class="badge warm-badge">WARM</span>
    <strong>${escHtml(l.market)}</strong>
    ${l.estimatedValue ? `<span style="float:right;color:#ff9800;font-weight:600">${escHtml(l.estimatedValue)}</span>` : ''}
    <div class="title">${escHtml(l.lead)}</div>
    ${l.oemRecommendation ? `<div><span class="label">OEM:</span><span class="val">${escHtml(l.oemRecommendation)}</span></div>` : ''}
    ${l.nextStep ? `<div class="meta">→ ${escHtml(l.nextStep)}</div>` : ''}
  </div>`).join('')}
</div>` : ''}

${tenders.length > 0 ? `
<div class="section">
  <div class="section-title">Verified Tenders & Contracts</div>
  ${tenders.map(t => `
  <div class="card ${t.leadQuality === 'HOT' ? 'hot' : 'warm'}">
    <span class="badge ${t.leadQuality === 'HOT' ? 'hot-badge' : 'warm-badge'}">${escHtml(t.leadQuality)}</span>
    <span class="badge" style="background:#546e7a">${escHtml(t.type)}</span>
    <strong>${escHtml(t.market)}</strong>
    ${t.winProbability != null ? `<span style="float:right;font-weight:700;font-size:0.8rem">Win ${t.winProbability}%</span>` : ''}
    <div class="title">${escHtml(t.title)}</div>
    <div class="meta">${escHtml(t.source)} · ${escHtml(t.date || '')}
    ${t.url ? ` · <a href="${escHtml(t.url)}" target="_blank">View Tender →</a>` : ''}</div>
  </div>`).join('')}
</div>` : ''}

${strat?.topPriority ? `
<div class="section">
  <div class="section-title">AI Strategic Priority</div>
  <div class="card strategy">
    <div class="title">${escHtml(strat.topPriority.action || strat.topPriority.description || '')}</div>
    ${strat.topPriority.whyNow ? `<div class="meta">Why now: ${escHtml(strat.topPriority.whyNow)}</div>` : ''}
    ${strat.topPriority.firstStep ? `<div class="next-step">${escHtml(strat.topPriority.firstStep)}</div>` : ''}
  </div>
</div>` : ''}

<div class="disclaimer">
  This brief is confidential and intended for the named recipient only. Intelligence is AI-generated from open sources
  and must be independently verified before commercial decisions. All export activity is subject to applicable licensing
  and regulatory requirements.
</div>
</div>
<div class="footer">Powered by Arkmurus Crucix Intelligence Platform &nbsp;·&nbsp; arkmurus.com</div>
</body>
</html>`;

  res.setHeader('Content-Type', 'text/html; charset=utf-8');
  res.setHeader('X-Robots-Tag', 'noindex, nofollow');
  res.send(html);
});

function escHtml(str) {
  return String(str || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

app.get('/api/patterns', requireAuth, (req, res) => {
  res.json(getPatterns());
});

app.get('/api/explorer', requireAuth, (req, res) => {
  res.json(getExplorerFindings());
});

app.post('/api/explorer/run', requireAuth, async (req, res) => {
  try {
    const findings = await runExploration(llmProvider, req.body || {});
    res.json({ success: true, ...findings });
  } catch (err) {
    res.json({ success: false, error: err.message });
  }
});

// ── Brain ML endpoints — read from shared Redis (Python brain writes, Node reads) ──

app.get('/api/brain/leads', requireAuth, async (req, res) => {
  try {
    // Brain stores leads as a Redis list (LPUSH, newest first)
    // Each element is a JSON string — we parse and return up to 20
    const raw = await redisGet('crucix:brain:generated_leads');
    // redisGet uses GET (for strings); brain uses LRANGE for lists.
    // We need LRANGE — call the Upstash REST directly.
    const REDIS_URL   = process.env.UPSTASH_REDIS_URL;
    const REDIS_TOKEN = process.env.UPSTASH_REDIS_TOKEN;
    if (!REDIS_URL || !REDIS_TOKEN) return res.json([]);
    const r = await fetch(`${REDIS_URL}/lrange/crucix:brain:generated_leads/0/19`, {
      headers: { Authorization: `Bearer ${REDIS_TOKEN}` },
      signal: AbortSignal.timeout(6000),
    });
    if (!r.ok) return res.json([]);
    const data = await r.json();
    const leads = (data.result || []).map(s => { try { return JSON.parse(s); } catch { return null; } }).filter(Boolean);
    res.json(leads);
  } catch (e) {
    res.json([]);
  }
});

app.get('/api/brain/brief', requireAuth, async (req, res) => {
  try {
    const brief = await redisGet('crucix:brain:bd_brief:latest');
    if (!brief) return res.status(404).json({ error: 'No brief generated yet. Brain sweep required.' });
    res.json(brief);
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.get('/api/brain/status', requireAuth, async (req, res) => {
  try {
    const lastRun = await redisGet('crucix:brain:last_run');
    res.json({ last_run: lastRun, service: 'crucix-brain' });
  } catch (e) {
    res.json({ last_run: null });
  }
});

app.get('/api/brain/history', requireAuth, async (req, res) => {
  try {
    const REDIS_URL   = process.env.UPSTASH_REDIS_URL;
    const REDIS_TOKEN = process.env.UPSTASH_REDIS_TOKEN;
    if (!REDIS_URL || !REDIS_TOKEN) return res.json([]);
    // Get list of run IDs
    const r = await fetch(`${REDIS_URL}/lrange/crucix:brain:run_history/0/9`, {
      headers: { Authorization: `Bearer ${REDIS_TOKEN}` },
      signal: AbortSignal.timeout(6000),
    });
    if (!r.ok) return res.json([]);
    const data   = await r.json();
    const runIds = data.result || [];
    // Fetch individual run reports
    const reports = await Promise.all(runIds.map(rid => redisGet(`crucix:brain:run:${rid}`)));
    res.json(reports.filter(Boolean));
  } catch (e) {
    res.json([]);
  }
});

// ── ARIA endpoints — read identity/thoughts from Redis; proxy chat to brain ──

async function upstashLRange(key, start = 0, stop = 9) {
  const REDIS_URL   = process.env.UPSTASH_REDIS_URL;
  const REDIS_TOKEN = process.env.UPSTASH_REDIS_TOKEN;
  if (!REDIS_URL || !REDIS_TOKEN) return [];
  try {
    const r = await fetch(`${REDIS_URL}/lrange/${encodeURIComponent(key)}/${start}/${stop}`, {
      headers: { Authorization: `Bearer ${REDIS_TOKEN}` },
      signal: AbortSignal.timeout(6000),
    });
    if (!r.ok) return [];
    const d = await r.json();
    return (d.result || []).map(s => { try { return JSON.parse(s); } catch { return s; } });
  } catch { return []; }
}

const BRAIN_URL = process.env.BRAIN_SERVICE_URL; // e.g. https://crucix-brain.onrender.com

app.get('/api/aria/identity', requireAuth, async (req, res) => {
  try {
    const identity = await redisGet('crucix:brain:aria:identity');
    if (!identity) {
      // Brain service not yet deployed — return local identity based on LLM config
      return res.json({
        name: 'ARIA',
        full_name: 'Arkmurus Research Intelligence Agent',
        status: llmProvider?.isConfigured ? 'online' : 'no_llm',
        mode: 'local',
        llm_provider: llmProvider?.name || null,
        age_days: 0,
        total_sweeps: 0,
        total_leads: 0,
        domain: 'Defence procurement, Lusophone Africa, Export controls',
      });
    }
    res.json({ ...identity, status: 'online', mode: 'brain' });
  } catch { res.json({ name: 'ARIA', status: 'unavailable' }); }
});

app.get('/api/aria/thoughts', requireAuth, async (req, res) => {
  try {
    const thoughtIds = await upstashLRange('crucix:brain:aria:thoughts', 0, 9);
    const thoughts   = await Promise.all(
      (Array.isArray(thoughtIds) ? thoughtIds : []).map(id =>
        typeof id === 'string' ? redisGet(`crucix:brain:aria:thought:${id}`) : Promise.resolve(id)
      )
    );
    res.json(thoughts.filter(Boolean));
  } catch { res.json([]); }
});

app.get('/api/aria/curiosity', requireAuth, async (req, res) => {
  try {
    const identity = await redisGet('crucix:brain:aria:identity');
    const threads  = (identity?.curiosity_threads || []).filter(t => !t.resolved);
    res.json({ open_threads: threads });
  } catch { res.json({ open_threads: [] }); }
});

// ARIA chat — local LLM primary, brain service proxy secondary (if BRAIN_SERVICE_URL set)
app.post('/api/aria/chat', requireAuth, async (req, res) => {
  const { message, session_id } = req.body || {};
  if (!message) return res.status(400).json({ error: 'message required' });

  const sid = session_id || `${req.user?.id || 'anon'}_${Date.now()}`;

  // Try brain service first if configured
  if (BRAIN_URL) {
    try {
      const r = await fetch(`${BRAIN_URL}/api/aria/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message, session_id: sid }),
        signal: AbortSignal.timeout(30000),
      });
      if (r.ok) return res.json(await r.json());
    } catch (e) { console.warn('[ARIA proxy] brain service unreachable, using local LLM:', e.message); }
  }

  // Local LLM — inject live intelligence so ARIA can reference real data
  const result = await ariaLocalChat(message, sid, llmProvider, currentData);
  res.json(result);
});

app.post('/api/aria/think', requireAuth, async (req, res) => {
  const { question, context, fast } = req.body || {};
  if (!question) return res.status(400).json({ error: 'question required' });

  // Try brain service first if configured
  if (BRAIN_URL) {
    try {
      const r = await fetch(`${BRAIN_URL}/api/aria/think`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question, context: context || {}, fast: fast || false }),
        signal: AbortSignal.timeout(60000),
      });
      if (r.ok) return res.json(await r.json());
    } catch (e) { console.warn('[ARIA proxy] think failed, using local LLM:', e.message); }
  }

  // Local LLM deep reasoning — inject live intelligence
  const result = await ariaLocalThink(question, context || {}, llmProvider, currentData);
  res.json(result);
});

// ── Self-update API ───────────────────────────────────────────────────────────

app.get('/api/self/staged', requireAdmin, (req, res) => {
  res.json({ staged: getStagedModules() });
});

app.post('/api/self/generate', requireAdmin, async (req, res) => {
  const { description, moduleName } = req.body || {};
  if (!description || !moduleName) return res.status(400).json({ error: 'description and moduleName required' });
  const result = await generateSourceModule(llmProvider, description, moduleName);
  if (result.success) {
    stageModule(result.moduleName, result.code, { description });
    res.json({ success: true, moduleName: result.moduleName, staged: true });
  } else {
    res.status(500).json({ success: false, error: result.error });
  }
});

app.post('/api/self/apply', requireAdmin, async (req, res) => {
  const { moduleName } = req.body || {};
  if (!moduleName) return res.status(400).json({ error: 'moduleName required' });
  const result = await deployModule(moduleName);
  res.json(result);
});

app.post('/api/self/rollback', requireAdmin, (req, res) => {
  const { moduleName } = req.body || {};
  if (!moduleName) return res.status(400).json({ error: 'moduleName required' });
  res.json(rollbackModule(moduleName));
});

app.get('/api/self/update-log', requireAdmin, (req, res) => {
  res.json({ log: getUpdateLog(parseInt(req.query.limit) || 20) });
});

app.post('/webhook', async (req, res) => {
  try {
    const update = req.body;
    if (!update || !update.message) { res.sendStatus(200); return; }
    if (telegramAlerter && telegramAlerter.isConfigured) {
      await telegramAlerter._handleMessage(update.message);
    }
    res.sendStatus(200);
  } catch (error) {
    console.error('[Webhook] Error:', error);
    res.sendStatus(500);
  }
});

app.get('/webhook', (req, res) => res.send('Webhook is working!'));

// ── Auth Middleware ───────────────────────────────────────────────────────────

function requireAuth(req, res, next) {
  // Allow internal localhost calls (e.g. Telegram bot fetching /api/data on same process)
  const ip = req.ip || req.socket?.remoteAddress || '';
  if (ip === '127.0.0.1' || ip === '::1' || ip === '::ffff:127.0.0.1') return next();

  const token = req.headers.authorization?.replace('Bearer ', '');
  if (!token) return res.status(401).json({ error: 'Authentication required' });
  try {
    const payload = verifyToken(token);
    // Token version check — invalidates sessions after force-logout
    if (payload.ver !== undefined) {
      const user = findUserById(payload.userId);
      if (user && (user.tokenVersion || 0) !== payload.ver) {
        return res.status(401).json({ error: 'Session revoked — please log in again' });
      }
    }
    req.user = payload;
    next();
  } catch {
    return res.status(401).json({ error: 'Invalid or expired token' });
  }
}

function requireAdmin(req, res, next) {
  requireAuth(req, res, () => {
    if (req.user.role !== 'admin') return res.status(403).json({ error: 'Admin access required' });
    next();
  });
}

// ── Auth Routes ───────────────────────────────────────────────────────────────

app.post('/api/auth/register', async (req, res) => {
  try {
    const { username, email, password, fullName } = req.body || {};
    if (!username || username.length < 3)  return res.status(400).json({ error: 'Username must be at least 3 characters' });
    if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return res.status(400).json({ error: 'Invalid email address' });
    if (!password || password.length < 8)  return res.status(400).json({ error: 'Password must be at least 8 characters' });

    if (findUserByEmail(email)) return res.status(409).json({ error: 'Email already registered' });
    if (findUserByUsername(username)) return res.status(409).json({ error: 'Username already taken' });

    const smtpConfigured = !!(process.env.EMAIL_HOST && process.env.EMAIL_USER && process.env.EMAIL_PASS);

    createUser({ username, email, password, fullName });
    const rawUser = findUserByEmail(email); // raw record includes verificationCode

    if (smtpConfigured) {
      // SMTP available — send verification email, require email confirmation first
      await sendVerificationEmail(email, rawUser.fullName, rawUser.verificationCode).catch(() => {});
      console.log(`[Auth] New registration, verification email sent: ${email}`);
      res.json({ message: 'Account created. Please check your email for a 6-digit verification code.', needsVerification: true, email });
    } else {
      // SMTP not configured — skip email verification, go straight to pending_approval
      updateUser(rawUser.id, { status: 'pending_approval', verificationCode: null, verificationExpiry: null });
      console.log(`[Auth] New registration (no SMTP — skipping email verify, pending admin approval): ${email}`);

      // Notify user that their request is under review
      await sendPendingApprovalEmail(email, rawUser.fullName).catch(() => {});

      // Notify admin via Telegram
      if (telegramAlerter?.isConfigured) {
        telegramAlerter.sendMessage(
          `👤 *New User Registration — Approval Required*\n\n` +
          `Name: ${rawUser.fullName}\nEmail: ${email}\nUsername: @${username}\n\n` +
          `Go to Admin → Users in the dashboard to approve or reject.`
        ).catch(() => {});
      }

      res.json({ message: 'Account created. Your registration is awaiting admin approval — you will be notified once activated.', needsVerification: false, email });
    }
  } catch (err) {
    console.error('[Auth] Register error:', err.message);
    res.status(500).json({ error: 'Registration failed' });
  }
});

app.post('/api/auth/login', async (req, res) => {
  try {
    const { email, password } = req.body || {};
    if (!email || !password) return res.status(400).json({ error: 'Email and password required' });

    const user = findUserByEmail(email);
    if (!user) return res.status(401).json({ error: 'Invalid credentials' });
    if (!verifyPassword(password, user.passwordHash)) return res.status(401).json({ error: 'Invalid credentials' });

    if (user.status === 'pending_approval') {
      return res.status(403).json({ error: 'Your account is pending admin approval. You will be notified once activated.' });
    }
    if (user.status === 'pending_verification') {
      return res.status(403).json({ error: 'Please verify your email first', needsVerification: true });
    }
    if (user.status === 'suspended') {
      return res.status(403).json({ error: 'Account suspended. Contact an administrator.' });
    }

    // If 2FA is enabled, issue a short-lived pre-auth token instead of the real JWT
    if (user.twoFactorEnabled && user.twoFactorSecret) {
      const preToken = createToken(user.id, user.role, '5m');
      return res.json({ requires2FA: true, preToken });
    }

    const token = createToken(user.id, user.role, '7d', user.tokenVersion || 0);
    const cleanUser = updateUser(user.id, { lastLogin: new Date().toISOString() });
    res.json({ token, user: cleanUser });
  } catch (err) {
    console.error('[Auth] Login error:', err.message);
    res.status(500).json({ error: 'Login failed' });
  }
});

// ── 2FA: verify TOTP code after password (second step) ───────────────────────
app.post('/api/auth/2fa/authenticate', async (req, res) => {
  try {
    const { preToken, code } = req.body || {};
    if (!preToken || !code) return res.status(400).json({ error: 'preToken and code required' });
    let payload;
    try { payload = verifyToken(preToken); } catch { return res.status(401).json({ error: 'Pre-auth token invalid or expired' }); }
    const user = findUserById(payload.userId);
    if (!user || !user.twoFactorSecret) return res.status(401).json({ error: 'Invalid session' });
    const { TOTP } = await import('otplib');
    const valid = TOTP.verify({ token: String(code).replace(/\s/g, ''), secret: user.twoFactorSecret });
    if (!valid) return res.status(401).json({ error: 'Invalid authenticator code' });
    const token = createToken(user.id, user.role, '7d', user.tokenVersion || 0);
    const cleanUser = updateUser(user.id, { lastLogin: new Date().toISOString() });
    res.json({ token, user: cleanUser });
  } catch (err) {
    console.error('[Auth] 2FA authenticate error:', err.message);
    res.status(500).json({ error: '2FA verification failed' });
  }
});

// ── 2FA: generate secret + QR code (setup step 1) ────────────────────────────
app.post('/api/auth/2fa/setup', requireAuth, async (req, res) => {
  try {
    const user = findUserById(req.user.userId);
    if (!user) return res.status(404).json({ error: 'User not found' });
    const { generateSecret, generateURI } = await import('otplib');
    const secret = generateSecret();
    const uri = generateURI('TOTP', {
      label: user.email,
      secret,
      issuer: 'Arkmurus Intelligence',
    });
    const QRCode = (await import('qrcode')).default;
    const qrDataUrl = await QRCode.toDataURL(uri);
    updateUser(user.id, { twoFactorSecret: secret, twoFactorEnabled: false });
    res.json({ secret, qrDataUrl });
  } catch (err) {
    console.error('[Auth] 2FA setup error:', err.message);
    res.status(500).json({ error: '2FA setup failed' });
  }
});

// ── 2FA: confirm code and enable ─────────────────────────────────────────────
app.post('/api/auth/2fa/enable', requireAuth, async (req, res) => {
  try {
    const { code } = req.body || {};
    if (!code) return res.status(400).json({ error: 'Authenticator code required' });
    const user = findUserById(req.user.userId);
    if (!user?.twoFactorSecret) return res.status(400).json({ error: 'Run /api/auth/2fa/setup first' });
    const { TOTP } = await import('otplib');
    const valid = TOTP.verify({ token: String(code).replace(/\s/g, ''), secret: user.twoFactorSecret });
    if (!valid) return res.status(400).json({ error: 'Invalid code — check your authenticator app and try again' });
    updateUser(user.id, { twoFactorEnabled: true });
    res.json({ message: '2FA enabled successfully' });
  } catch (err) {
    res.status(500).json({ error: '2FA enable failed' });
  }
});

// ── 2FA: disable ─────────────────────────────────────────────────────────────
app.post('/api/auth/2fa/disable', requireAuth, async (req, res) => {
  try {
    const { code } = req.body || {};
    if (!code) return res.status(400).json({ error: 'Authenticator code required to disable 2FA' });
    const user = findUserById(req.user.userId);
    if (!user?.twoFactorSecret) return res.status(400).json({ error: '2FA is not enabled' });
    const { TOTP } = await import('otplib');
    const valid = TOTP.verify({ token: String(code).replace(/\s/g, ''), secret: user.twoFactorSecret });
    if (!valid) return res.status(400).json({ error: 'Invalid code' });
    updateUser(user.id, { twoFactorEnabled: false, twoFactorSecret: null });
    res.json({ message: '2FA disabled' });
  } catch (err) {
    res.status(500).json({ error: '2FA disable failed' });
  }
});

app.post('/api/auth/verify-email', async (req, res) => {
  try {
    const { email, code } = req.body || {};
    if (!email || !code) return res.status(400).json({ error: 'Email and code required' });

    const user = findUserByEmail(email);
    if (!user) return res.status(404).json({ error: 'User not found' });
    if (user.verificationCode !== String(code)) return res.status(400).json({ error: 'Invalid verification code' });
    if (user.verificationExpiry && new Date(user.verificationExpiry) < new Date()) {
      return res.status(400).json({ error: 'Verification code expired. Request a new one.' });
    }

    updateUser(user.id, { status: 'pending_approval', verificationCode: null, verificationExpiry: null });
    // Notify user that their request is under review
    await sendPendingApprovalEmail(email, user.fullName).catch(() => {});
    // Notify admin
    await sendAdminNotification(
      'New user registration — approval required',
      `<p>A new user has verified their email and is awaiting your approval:</p>
       <ul>
         <li><strong>Name:</strong> ${user.fullName}</li>
         <li><strong>Email:</strong> ${email}</li>
         <li><strong>Username:</strong> ${user.username}</li>
       </ul>
       <p>Log in to the admin panel and go to <strong>Admin &rarr; Users</strong> to approve or reject this account.</p>`
    ).catch(() => {});
    // Also notify via Telegram
    if (telegramAlerter?.isConfigured) {
      telegramAlerter.sendMessage(
        `👤 *User Verified — Approval Required*\n\nName: ${user.fullName}\nEmail: ${email}\nUsername: @${user.username}\n\nGo to Admin → Users to approve or reject.`
      ).catch(() => {});
    }
    res.json({ message: 'Email verified. Your account is awaiting admin approval — you will be notified once activated.' });
  } catch (err) {
    console.error('[Auth] Verify email error:', err.message);
    res.status(500).json({ error: 'Verification failed' });
  }
});

app.post('/api/auth/resend-verification', async (req, res) => {
  try {
    const { email } = req.body || {};
    if (!email) return res.status(400).json({ error: 'Email required' });

    const user = findUserByEmail(email);
    if (!user) return res.json({ message: 'If that email exists, a code has been sent.' });
    if (user.status === 'active') return res.status(400).json({ error: 'Account already verified' });

    // Rate limit: reject if last code sent <60s ago
    if (user.verificationExpiry) {
      const expiryTime  = new Date(user.verificationExpiry).getTime();
      const issuedApprox = expiryTime - 15 * 60 * 1000;
      if (Date.now() - issuedApprox < 60 * 1000) {
        return res.status(429).json({ error: 'Please wait 60 seconds before requesting a new code' });
      }
    }

    const newCode = generateCode();
    updateUser(user.id, {
      verificationCode: newCode,
      verificationExpiry: new Date(Date.now() + 15 * 60 * 1000).toISOString(),
    });
    await sendVerificationEmail(email, user.fullName, newCode).catch(() => {});
    res.json({ message: 'Verification email resent.' });
  } catch (err) {
    console.error('[Auth] Resend verification error:', err.message);
    res.status(500).json({ error: 'Failed to resend verification' });
  }
});

app.get('/api/auth/me', requireAuth, (req, res) => {
  try {
    const user = findUserById(req.user.userId);
    if (!user) return res.status(404).json({ error: 'User not found' });
    // Return clean user (no passwordHash) — findUserById returns raw; strip here
    const { passwordHash, verificationCode, verificationExpiry, resetCode, resetExpiry, ...clean } = user;
    res.json(clean);
  } catch (err) {
    res.status(500).json({ error: 'Failed to fetch user' });
  }
});

app.put('/api/auth/profile', requireAuth, (req, res) => {
  try {
    const { fullName, telegramUsername, notifyDigest, notifyFlash, notifyPush } = req.body || {};
    const updates = {};
    if (fullName         !== undefined) updates.fullName         = fullName;
    if (telegramUsername !== undefined) updates.telegramUsername = telegramUsername;
    if (notifyDigest     !== undefined) updates.notifyDigest     = !!notifyDigest;
    if (notifyFlash      !== undefined) updates.notifyFlash      = !!notifyFlash;
    if (notifyPush       !== undefined) updates.notifyPush       = !!notifyPush;
    const updated = updateUser(req.user.userId, updates);
    res.json(updated);
  } catch (err) {
    res.status(500).json({ error: 'Failed to update profile' });
  }
});

app.put('/api/auth/password', requireAuth, (req, res) => {
  try {
    const { currentPassword, newPassword } = req.body || {};
    if (!currentPassword || !newPassword) return res.status(400).json({ error: 'Current and new password required' });
    if (newPassword.length < 8) return res.status(400).json({ error: 'New password must be at least 8 characters' });

    const user = findUserById(req.user.userId);
    if (!user) return res.status(404).json({ error: 'User not found' });
    if (!verifyPassword(currentPassword, user.passwordHash)) {
      return res.status(401).json({ error: 'Current password incorrect' });
    }

    updateUser(req.user.userId, { passwordHash: hashPassword(newPassword) });
    res.json({ message: 'Password updated successfully' });
  } catch (err) {
    res.status(500).json({ error: 'Failed to update password' });
  }
});

app.post('/api/auth/forgot-password', async (req, res) => {
  try {
    const { email } = req.body || {};
    if (!email) return res.status(400).json({ error: 'Email required' });

    const user = findUserByEmail(email);
    // Always return 200 — do not reveal if email exists
    if (user) {
      const resetCode = generateCode();
      updateUser(user.id, {
        resetCode,
        resetExpiry: new Date(Date.now() + 15 * 60 * 1000).toISOString(),
      });
      await sendPasswordResetEmail(email, user.fullName, resetCode).catch(() => {});
    }
    res.json({ message: 'If that email is registered, a reset code has been sent.' });
  } catch (err) {
    console.error('[Auth] Forgot password error:', err.message);
    res.status(500).json({ error: 'Failed to process request' });
  }
});

app.post('/api/auth/reset-password', async (req, res) => {
  try {
    const { email, code, newPassword } = req.body || {};
    if (!email || !code || !newPassword) return res.status(400).json({ error: 'Email, code, and new password required' });
    if (newPassword.length < 8) return res.status(400).json({ error: 'Password must be at least 8 characters' });

    const user = findUserByEmail(email);
    if (!user || user.resetCode !== String(code)) return res.status(400).json({ error: 'Invalid or expired reset code' });
    if (user.resetExpiry && new Date(user.resetExpiry) < new Date()) {
      return res.status(400).json({ error: 'Reset code expired. Request a new one.' });
    }

    updateUser(user.id, {
      passwordHash: hashPassword(newPassword),
      resetCode: null,
      resetExpiry: null,
    });
    res.json({ message: 'Password reset successfully. You can now log in.' });
  } catch (err) {
    console.error('[Auth] Reset password error:', err.message);
    res.status(500).json({ error: 'Failed to reset password' });
  }
});

// ── Admin: SMTP test ──────────────────────────────────────────────────────────
app.post('/api/admin/test-email', requireAdmin, async (req, res) => {
  const { to } = req.body || {};
  if (!to) return res.status(400).json({ error: 'to address required' });
  const result = await sendAdminNotification(
    'Arkmurus SMTP Test',
    `<p>This is a test email sent at ${new Date().toISOString()}.</p><p>If you received this, SMTP is configured correctly.</p>`
  ).catch(err => ({ sent: false, reason: err.message }));
  // Also try sending to the provided address
  const result2 = await sendVerificationEmail(to, 'Test User', '123456').catch(err => ({ sent: false, reason: err.message }));
  res.json({
    adminEmail: result,
    testEmail:  result2,
    smtpConfig: {
      host:      process.env.EMAIL_HOST     || '(not set)',
      port:      process.env.EMAIL_PORT     || '587 (default)',
      user:      process.env.EMAIL_USER     || '(not set)',
      passSet:   !!(process.env.EMAIL_PASS),
      secure:    process.env.EMAIL_SECURE   || 'false (default)',
      adminDest: process.env.ADMIN_EMAIL    || 'acorrea@arkmurus.com (default)',
    },
  });
});

// ── Admin User Management Routes ──────────────────────────────────────────────

app.get('/api/admin/users', requireAdmin, (req, res) => {
  try {
    res.json(listUsers());
  } catch (err) {
    res.status(500).json({ error: 'Failed to list users' });
  }
});

app.put('/api/admin/users/:id', requireAdmin, async (req, res) => {
  try {
    const { role, status, notifyDigest, notifyFlash } = req.body || {};
    const existingUser = findUserById(req.params.id);
    if (!existingUser) return res.status(404).json({ error: 'User not found' });
    const admin = findUserById(req.user.userId);
    const updates = {};
    if (role         !== undefined) updates.role         = role;
    if (status       !== undefined) updates.status       = status;
    if (notifyDigest !== undefined) updates.notifyDigest = !!notifyDigest;
    if (notifyFlash  !== undefined) updates.notifyFlash  = !!notifyFlash;
    const updated = updateUser(req.params.id, updates);

    // Emails + audit on status change
    if (status && status !== existingUser.status) {
      if (status === 'active') {
        await sendWelcomeEmail(existingUser.email, existingUser.fullName).catch(() => {});
        logAudit({ adminId: req.user.userId, adminEmail: admin?.email || '', action: 'approve', targetId: existingUser.id, targetEmail: existingUser.email, targetName: existingUser.fullName });
      } else if (status === 'suspended') {
        await sendSuspensionEmail(existingUser.email, existingUser.fullName).catch(() => {});
        logAudit({ adminId: req.user.userId, adminEmail: admin?.email || '', action: 'suspend', targetId: existingUser.id, targetEmail: existingUser.email, targetName: existingUser.fullName });
      } else if (status === 'active' && existingUser.status === 'suspended') {
        await sendReactivationEmail(existingUser.email, existingUser.fullName).catch(() => {});
        logAudit({ adminId: req.user.userId, adminEmail: admin?.email || '', action: 'unsuspend', targetId: existingUser.id, targetEmail: existingUser.email, targetName: existingUser.fullName });
      }
    }
    if (role && role !== existingUser.role) {
      logAudit({ adminId: req.user.userId, adminEmail: admin?.email || '', action: 'role_change', targetId: existingUser.id, targetEmail: existingUser.email, targetName: existingUser.fullName, notes: `${existingUser.role} → ${role}` });
    }

    res.json(updated);
  } catch (err) {
    res.status(500).json({ error: err.message || 'Failed to update user' });
  }
});

app.post('/api/admin/users/:id/approve', requireAdmin, async (req, res) => {
  try {
    const target = findUserById(req.params.id);
    if (!target) return res.status(404).json({ error: 'User not found' });
    if (target.status === 'active') return res.status(400).json({ error: 'User is already active' });
    updateUser(target.id, { status: 'active' });
    await sendWelcomeEmail(target.email, target.fullName).catch(() => {});
    const admin = findUserById(req.user.userId);
    logAudit({ adminId: req.user.userId, adminEmail: admin?.email || '', action: 'approve', targetId: target.id, targetEmail: target.email, targetName: target.fullName });
    console.log(`[Auth] User approved: ${target.email} by ${admin?.email || req.user.userId}`);
    res.json({ ok: true, message: `${target.fullName} approved — welcome email sent` });
  } catch (err) {
    res.status(500).json({ error: err.message || 'Failed to approve user' });
  }
});

app.post('/api/admin/users/:id/reject', requireAdmin, async (req, res) => {
  try {
    const target = findUserById(req.params.id);
    if (!target) return res.status(404).json({ error: 'User not found' });
    await sendRejectionEmail(target.email, target.fullName).catch(() => {});
    const admin = findUserById(req.user.userId);
    logAudit({ adminId: req.user.userId, adminEmail: admin?.email || '', action: 'reject', targetId: target.id, targetEmail: target.email, targetName: target.fullName });
    console.log(`[Auth] User rejected and removed: ${target.email} by ${admin?.email || req.user.userId}`);
    deleteUser(target.id);
    res.json({ ok: true, message: `${target.fullName} rejected — rejection email sent` });
  } catch (err) {
    res.status(500).json({ error: err.message || 'Failed to reject user' });
  }
});

app.post('/api/admin/users/:id/force-logout', requireAdmin, (req, res) => {
  try {
    const target = findUserById(req.params.id);
    if (!target) return res.status(404).json({ error: 'User not found' });
    if (req.params.id === req.user.userId) return res.status(400).json({ error: 'Cannot force-logout yourself' });
    revokeTokens(req.params.id);
    const admin = findUserById(req.user.userId);
    logAudit({ adminId: req.user.userId, adminEmail: admin?.email || '', action: 'force_logout', targetId: target.id, targetEmail: target.email, targetName: target.fullName });
    res.json({ ok: true, message: `${target.fullName}'s session has been revoked` });
  } catch (err) {
    res.status(500).json({ error: err.message || 'Failed to revoke session' });
  }
});

app.get('/api/admin/audit', requireAdmin, (req, res) => {
  const limit = Math.min(parseInt(req.query.limit) || 100, 500);
  res.json(getAuditLog(limit));
});

app.delete('/api/admin/users/:id', requireAdmin, async (req, res) => {
  try {
    if (req.params.id === req.user.userId) {
      return res.status(400).json({ error: 'Cannot delete your own account' });
    }
    const target = findUserById(req.params.id);
    if (!target) return res.status(404).json({ error: 'User not found' });
    const admin = findUserById(req.user.userId);
    // Send rejection email if account was pending
    if (target.status === 'pending_approval' || target.status === 'pending_verification') {
      await sendRejectionEmail(target.email, target.fullName).catch(() => {});
      logAudit({ adminId: req.user.userId, adminEmail: admin?.email || '', action: 'reject', targetId: target.id, targetEmail: target.email, targetName: target.fullName });
    } else {
      logAudit({ adminId: req.user.userId, adminEmail: admin?.email || '', action: 'delete', targetId: target.id, targetEmail: target.email, targetName: target.fullName });
    }
    deleteUser(req.params.id);
    res.json({ message: 'User deleted' });
  } catch (err) {
    res.status(500).json({ error: err.message || 'Failed to delete user' });
  }
});

// ── Observability Admin Routes ────────────────────────────────────────────────
const errHandlers = errorTracker.apiHandler();
app.get('/api/admin/errors',           requireAdmin, errHandlers.getErrors);
app.get('/api/admin/source-health-errors', requireAdmin, errHandlers.getSourceHealth);
app.get('/api/admin/error-dashboard',  requireAdmin, errHandlers.getDashboard);

// ── Source Pruner Admin Routes ────────────────────────────────────────────────
app.get('/api/admin/source-prune-report', requireAdmin, async (req, res) => {
  res.json(await sourcePruner.getSourceHealthReport());
});
app.post('/api/admin/sources/:name/enable', requireAdmin, async (req, res) => {
  await sourcePruner.setSourceEnabled(req.params.name, true);
  res.json({ status: 'enabled', source: req.params.name });
});
app.post('/api/admin/sources/:name/disable', requireAdmin, async (req, res) => {
  await sourcePruner.setSourceEnabled(req.params.name, false);
  res.json({ status: 'disabled', source: req.params.name });
});

// ── Compliance entity screening (live lists) + version info ───────────────────
app.post('/api/compliance/entity-screen', requireAuth, async (req, res) => {
  const { entity_name } = req.body || {};
  if (!entity_name) return res.status(400).json({ error: 'entity_name required' });
  if (!redisAdapter.isConfigured) {
    return res.status(503).json({ error: 'Redis not configured — live compliance lists unavailable' });
  }
  try {
    const result = await screenEntity(entity_name, redisAdapter);
    res.json(result);
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.get('/api/compliance/versions', requireAuth, async (req, res) => {
  if (!redisAdapter.isConfigured) return res.json({ versions: {}, last_fetch: null });
  try {
    res.json(await getComplianceVersions(redisAdapter));
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// ── Active tenders (deduped procurement portals) ──────────────────────────────
app.get('/api/tenders/active', requireAuth, async (req, res) => {
  try {
    const tenders = await procDedup.getActiveTenders(req.query.market);
    res.json(tenders);
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// ── Push Notification Routes ──────────────────────────────────────────────────

app.get('/api/push/vapid-public-key', (req, res) => {
  const publicKey = getVapidPublicKey();
  if (!publicKey) return res.status(503).json({ error: 'Push notifications not initialized' });
  res.json({ publicKey });
});

app.post('/api/push/subscribe', requireAuth, (req, res) => {
  try {
    const { subscription } = req.body || {};
    if (!subscription) return res.status(400).json({ error: 'subscription object required' });
    saveSubscription(req.user.userId, subscription);
    updateUser(req.user.userId, { notifyPush: true });
    res.json({ message: 'Subscribed to push notifications' });
  } catch (err) {
    res.status(500).json({ error: 'Failed to save subscription' });
  }
});

app.delete('/api/push/unsubscribe', requireAuth, (req, res) => {
  try {
    removeSubscription(req.user.userId);
    updateUser(req.user.userId, { notifyPush: false });
    res.json({ message: 'Unsubscribed from push notifications' });
  } catch (err) {
    res.status(500).json({ error: 'Failed to remove subscription' });
  }
});

app.post('/api/push/test', requireAdmin, async (req, res) => {
  try {
    await pushFlash('Test Alert', 'This is a test push notification from Arkmurus');
    res.json({ message: 'Test push sent' });
  } catch (err) {
    res.status(500).json({ error: 'Failed to send test push' });
  }
});

// ── Chat REST API ─────────────────────────────────────────────────────────────

// GET /api/chat/users — list all users (id, username, fullName) for contact list
app.get('/api/chat/users', requireAuth, (req, res) => {
  const users = listUsers().filter(u => u.status === 'active' && u.id !== req.user.userId);
  res.json(users.map(u => ({ id: u.id, username: u.username, fullName: u.fullName, role: u.role })));
});

// GET /api/chat/conversations — summary list for sidebar
app.get('/api/chat/conversations', requireAuth, (req, res) => {
  const summaries = getConversationSummaries(req.user.userId);
  // Enrich with user info
  const enriched = summaries.map(s => {
    const u = findUserById(s.userId);
    return {
      ...s,
      username: u?.username || 'Unknown',
      fullName: u?.fullName || 'Unknown',
      role: u?.role || 'viewer'
    };
  });
  res.json(enriched);
});

// GET /api/chat/messages/:userId — conversation history
app.get('/api/chat/messages/:userId', requireAuth, (req, res) => {
  const msgs = getConversation(req.user.userId, req.params.userId, 100);
  markRead(req.user.userId, req.params.userId);
  res.json(msgs);
});

// GET /api/chat/unread — total unread count for badge
app.get('/api/chat/unread', requireAuth, (req, res) => {
  res.json({ count: unreadCount(req.user.userId) });
});

app.get('/events', (req, res) => {
  res.writeHead(200, {
    'Content-Type': 'text/event-stream',
    'Cache-Control': 'no-cache',
    'Connection': 'keep-alive',
    'Access-Control-Allow-Origin': '*',
  });
  res.write('data: {"type":"connected"}\n\n');
  sseClients.add(res);
  req.on('close', () => sseClients.delete(res));
});

function broadcast(data) {
  const msg = `data: ${JSON.stringify(data)}\n\n`;
  for (const client of sseClients) {
    try { client.write(msg); } catch { sseClients.delete(client); }
  }
}

// === Sweep Cycle ===
async function runSweepCycle() {
  if (sweepInProgress) {
    console.log('[Crucix] Sweep already in progress, skipping');
    return;
  }

  sweepInProgress = true;
  sweepStartedAt = new Date().toISOString();
  broadcast({ type: 'sweep_start', timestamp: sweepStartedAt });
  console.log(`\n${'='.repeat(60)}`);
  console.log(`[Crucix] Starting sweep at ${logTime()} (London)`);
  console.log(`${'='.repeat(60)}`);

  try {
    const rawData = await fullBriefing();

    // Push top defence/procurement signals into the Python brain queue (fire-and-forget)
    pushSignalsToBrain(rawData).catch(() => {});

    console.log('[Crucix] Fetching extended intelligence sources...');
    const [unscData, centralBanksData, thinkTanksData, tradeData, opensanctionsData] =
      await Promise.allSettled([
        fetchUNSecurityCouncil(),
        fetchCentralBanks(),
        fetchThinkTanks(),
        fetchTradeFLows(),
        fetchOpenSanctions(),
      ]).then(results => results.map(r => r.status === 'fulfilled' ? r.value : null));

    rawData.unsc          = unscData;
    rawData.centralBanks  = centralBanksData;
    rawData.thinkTanks    = thinkTanksData;
    rawData.tradeFlows    = tradeData;
    rawData.opensanctions = opensanctionsData;
    // rawData.gdelt is already set by fullBriefing() — no duplicate call needed

    writeFileSync(join(RUNS_DIR, 'latest.json'), JSON.stringify(rawData, null, 2));
    lastSweepTime = new Date().toISOString();

    // Update source health tracker (in-memory + persistent learning store)
    updateSourceHealth(rawData.timing);
    for (const [name, info] of Object.entries(rawData.timing || {})) {
      try { recordSourceSweep(name, info.status, info.ms); } catch {}
    }

    console.log('[Crucix] Synthesizing dashboard data...');
    const synthesized = await synthesize(rawData);

    const delta = memory.addRun(synthesized);
    synthesized.delta = delta;

    archiveRunWithEntities(synthesized);

    const correlations = correlate(synthesized);
    synthesized.correlations = correlations;
    if (correlations.length > 0) {
      console.log(`[Crucix] ${correlations.length} regional correlations detected`);
    }

    // Polymarket arbitrage: compare market odds vs OSINT severity
    const arbitrage = detectArbitrage(synthesized.polymarket, correlations);
    synthesized.arbitrage = arbitrage;
    if (arbitrage.length > 0) {
      console.log(`[Crucix] ${arbitrage.length} Polymarket arbitrage signals detected`);
    }

    if (llmProvider?.isConfigured) {
      try {
        console.log('[Crucix] Generating LLM trade ideas...');
        const previousIdeas = memory.getLastRun()?.ideas || [];
        const llmIdeas = await generateLLMIdeas(llmProvider, synthesized, delta, previousIdeas);
        if (llmIdeas) {
          synthesized.ideas = llmIdeas;
          synthesized.ideasSource = 'llm';
          console.log(`[Crucix] LLM generated ${llmIdeas.length} ideas`);
        } else {
          synthesized.ideas = [];
          synthesized.ideasSource = 'llm-failed';
        }
      } catch (llmErr) {
        console.error('[Crucix] LLM ideas failed (non-fatal):', llmErr.message);
        synthesized.ideas = [];
        synthesized.ideasSource = 'llm-failed';
      }
    } else {
      synthesized.ideas = [];
      synthesized.ideasSource = 'disabled';
    }

    // Entity trajectory — computed from archive history
    synthesized.entityTrajectory = analyzeEntityTrajectory(14);

    // Self-learning: detect sales opportunities on every sweep
    try {
      const opportunities = detectOpportunities(synthesized);
      synthesized.opportunities = opportunities;
      if (opportunities.length > 0) {
        console.log(`[Self] ${opportunities.length} opportunity/ies detected (top: ${opportunities[0]?.market} score:${opportunities[0]?.score})`);
      }
    } catch (err) {
      console.error('[Self] Opportunity detection failed (non-fatal):', err.message);
      synthesized.opportunities = [];
    }

    // Inject saved patterns + explorer findings into synthesized data for BD brain
    try {
      const { getPatterns, getExplorerFindings } = await import('./lib/self/learning_store.mjs');
      const patterns = getPatterns();
      if (patterns?.patterns?.length) synthesized.patterns = patterns.patterns;
      const explorer = getExplorerFindings();
      if (explorer?.findings) synthesized.explorerFindings = explorer;
    } catch {}

    // BD Intelligence: real tenders + strategic ideas
    try {
      const bdResult = await runBDIntelligence(synthesized, null, llmProvider);
      synthesized.bdIntelligence = bdResult;
      console.log(`[BD] ${bdResult.counts.activeTenders} tenders · ${bdResult.counts.strategicIdeas} ideas · ${bdResult.counts.pipelineDeals} pipeline`);
    } catch (err) {
      console.error('[BD] BD intelligence failed (non-fatal):', err.message);
    }

    // Check restart flag — apply pending self-updates after sweep completes
    if (isRestartPending()) {
      console.log('[Self] Restart flag detected — will restart after current sweep to apply updates');
    }

    // Telegram alerts handled exclusively by onSweepComplete (3-hour cadence + new intel check)

    memory.pruneAlertedSignals();
    currentData = synthesized;

    if (telegramAlerter && telegramAlerter.isConfigured) {
      try {
        await telegramAlerter.onSweepComplete(currentData);
        // alert cadence managed by telegram.mjs
      } catch (err) {
        console.error('[Crucix] Telegram alert error:', err.message);
      }
    }

    // Flash push for critical correlations
    const critFlash = (currentData.correlations || []).filter(c => c.severity === 'critical');
    if (critFlash.length > 0) {
      const top = critFlash[0];
      pushFlash(
        `Critical Intel: ${top.region}`,
        `Multi-source critical signal detected — ${top.topSignals?.[0]?.text?.substring(0, 80) || 'view dashboard for details'}`,
        '/dashboard/brief'
      ).catch(e => console.warn('[Push] flash push failed:', e.message));
    }

    broadcast({ type: 'update', data: currentData });

    console.log(`[Crucix] Sweep complete — ${currentData.meta.sourcesOk}/${currentData.meta.sourcesQueried} sources OK`);
    console.log(`[Crucix] ${currentData.ideas.length} ideas (${synthesized.ideasSource}) | ${currentData.news.length} news | ${currentData.newsFeed.length} feed items`);
    if (delta?.summary) console.log(`[Crucix] Delta: ${delta.summary.totalChanges} changes, ${delta.summary.criticalChanges} critical, direction: ${delta.summary.direction}`);
    if (correlations.length > 0) console.log(`[Crucix] Correlations: ${correlations.map(c => `${c.region}(${c.severity})`).join(', ')}`);
    console.log(`[Crucix] Next sweep at ${logTimeShort(new Date(Date.now() + config.refreshIntervalMinutes * 60000))} (London)`);

    // Auto-classify pending outcomes using ALL current sweep signals (non-blocking)
    try {
      const { autoClassifyOutcomes, pruneOldData } = await import('./lib/self/learning_store.mjs');
      const allSignals = [
        ...(correlations || []).flatMap(c => c.topSignals || []),
        ...(synthesized.tg?.urgent || []),
        ...(synthesized.tg?.top || []),
        ...(synthesized.defenseNews?.updates || []).map(d => ({ text: d.title + ' ' + (d.content || ''), source: d.source })),
        ...(synthesized.newsFeed || []).map(n => ({ text: n.title + ' ' + (n.description || ''), source: n.source })),
      ].slice(0, 300);
      const classified = autoClassifyOutcomes(allSignals);
      if (classified > 0) console.log(`[Crucix] Auto-classified ${classified} pending signal outcome(s)`);
      pruneOldData();
    } catch (e) { console.warn('[Crucix] Auto-classify error (non-fatal):', e.message); }

    // Graceful restart to apply any self-deployed modules
    if (isRestartPending()) {
      clearRestartFlag();
      triggerGracefulRestart(5000);
    }

  } catch (err) {
    console.error('[Crucix] Sweep failed:', err.message);
    broadcast({ type: 'sweep_error', error: err.message });
  } finally {
    sweepInProgress = false;
  }
}

// === Startup ===
async function start() {
  const port = config.port;

  console.log(`
  ╔══════════════════════════════════════════════╗
  ║           CRUCIX INTELLIGENCE ENGINE         ║
  ║          Local Palantir · 36+ Sources        ║
  ╠══════════════════════════════════════════════╣
  ║  Dashboard:  http://localhost:${port}${' '.repeat(Math.max(0, 14 - String(port).length))}║
  ║  Search:     http://localhost:${port}/search.html${' '.repeat(Math.max(0, 8 - String(port).length))}║
  ║  Health:     http://localhost:${port}/api/health${' '.repeat(Math.max(0, 4 - String(port).length))}║
  ║  Refresh:    Every ${config.refreshIntervalMinutes} min${' '.repeat(20 - String(config.refreshIntervalMinutes).length)}║
  ║  LLM:        ${(config.llm.provider || 'disabled').padEnd(31)}║
  ║  Telegram:   ${config.telegram.botToken ? 'enabled' : 'disabled'}${' '.repeat(config.telegram.botToken ? 24 : 23)}║
  ║  Discord:    ${config.discord?.botToken ? 'enabled' : config.discord?.webhookUrl ? 'webhook only' : 'disabled'}${' '.repeat(config.discord?.botToken ? 24 : config.discord?.webhookUrl ? 20 : 23)}║
  ╚══════════════════════════════════════════════╝
  `);

  const server = createServer(app);

  // ── Socket.io — Real-time Chat ─────────────────────────────────────────────
  const io = new SocketIOServer(server, {
    cors: { origin: '*', methods: ['GET', 'POST'] }
  });

  // Map userId → Set of socket ids (one user may have multiple tabs)
  const onlineUsers = new Map(); // userId → Set<socketId>

  io.use((socket, next) => {
    const token = socket.handshake.auth?.token;
    try {
      const payload = verifyToken(token);
      socket.userId = payload.userId;
      socket.userRole = payload.role;
      next();
    } catch {
      next(new Error('Authentication error'));
    }
  });

  io.on('connection', (socket) => {
    const uid = socket.userId;

    // Track online
    if (!onlineUsers.has(uid)) onlineUsers.set(uid, new Set());
    onlineUsers.get(uid).add(socket.id);

    // Broadcast presence update
    io.emit('presence', { userId: uid, online: true });
    socket.emit('online_users', Array.from(onlineUsers.keys()));

    // Send message
    socket.on('send_message', ({ toId, text }) => {
      if (!toId || !text || typeof text !== 'string') return;
      const safeText = text.trim().slice(0, 2000);
      if (!safeText) return;

      const msg = storeMessage(uid, toId, safeText);
      const enrichFrom = findUserById(uid);
      const payload = {
        ...msg,
        fromUsername: enrichFrom?.username || 'Unknown',
        fromFullName: enrichFrom?.fullName || 'Unknown'
      };

      // Deliver to recipient (all their sockets)
      const toSockets = onlineUsers.get(toId);
      if (toSockets) {
        for (const sid of toSockets) {
          io.to(sid).emit('new_message', payload);
        }
      }

      // Echo back to sender (all their tabs)
      const fromSockets = onlineUsers.get(uid);
      if (fromSockets) {
        for (const sid of fromSockets) {
          io.to(sid).emit('new_message', payload);
        }
      }
    });

    // Typing indicator
    socket.on('typing', ({ toId, typing }) => {
      const toSockets = onlineUsers.get(toId);
      if (toSockets) {
        for (const sid of toSockets) {
          io.to(sid).emit('typing', { fromId: uid, typing });
        }
      }
    });

    // Mark read
    socket.on('mark_read', ({ fromId }) => {
      markRead(uid, fromId);
    });

    socket.on('disconnect', () => {
      const sockets = onlineUsers.get(uid);
      if (sockets) {
        sockets.delete(socket.id);
        if (sockets.size === 0) {
          onlineUsers.delete(uid);
          io.emit('presence', { userId: uid, online: false });
        }
      }
    });
  });

  server.listen(port, '0.0.0.0');

  server.on('error', (err) => {
    if (err.code === 'EADDRINUSE') {
      console.error(`\n[Crucix] FATAL: Port ${port} is already in use!`);
      console.error(`[Crucix] Fix:  taskkill /F /IM node.exe   (Windows)`);
      console.error(`[Crucix]       kill $(lsof -ti:${port})   (macOS/Linux)`);
    } else {
      console.error(`[Crucix] Server error:`, err.stack || err.message);
    }
    process.exit(1);
  });

  server.on('listening', async () => {
    console.log(`[Crucix] Server running on http://localhost:${port}`);

    // Initialise dedup store — loads from Upstash Redis if configured, else file
    await initDedup();

    // Only auto-open browser on desktop environments (not headless Linux servers)
    if (process.platform !== 'linux' || process.env.DISPLAY) {
      const openCmd = process.platform === 'win32' ? 'cmd /c start ""' : 'open';
      exec(`${openCmd} "http://localhost:${port}"`, (err) => {
        if (err) console.log('[Crucix] Could not auto-open browser:', err.message);
      });
    }

    try {
      const existing = JSON.parse(readFileSync(join(RUNS_DIR, 'latest.json'), 'utf8'));
      const data = await synthesize(existing);
      currentData = data;
      console.log('[Crucix] Loaded existing data from runs/latest.json — dashboard ready instantly');
      broadcast({ type: 'update', data: currentData });
      // NOTE: do NOT call onSweepComplete here with stale latest.json data.
      // The initial runSweepCycle() below will fetch fresh data and trigger
      // alerts via onSweepComplete — preventing repeated sends of old signals.
    } catch {
      console.log('[Crucix] No existing data found — first sweep required');
    }

    console.log('[Crucix] Running initial sweep...');
    runSweepCycle().catch(err => {
      console.error('[Crucix] Initial sweep failed:', err.message || err);
    });

    setInterval(runSweepCycle, config.refreshIntervalMinutes * 60 * 1000);

    // Self-ping every 4 minutes — keeps the server awake on all hosting platforms.
    // Always enabled — prevents Seenode/Render/Railway from sleeping the process.
    const selfUrl = process.env.APP_URL || process.env.RENDER_EXTERNAL_URL || `http://localhost:${port}`;
    setInterval(async () => {
      try {
        await fetch(`${selfUrl}/api/health`, { signal: AbortSignal.timeout(10000) });
      } catch {}
    }, 4 * 60 * 1000);
    console.log('[Crucix] Self-ping enabled (every 4min) — server will stay awake 24/7');

    cron.schedule('0 7 * * *', async () => {
      console.log('[Crucix] Sending morning digest...');
      try { await sendMorningDigest(telegramAlerter, currentData); }
      catch (e) { console.error('[Digest] Failed:', e.message); }
      pushDigest('Morning Intelligence Brief', 'Your daily Arkmurus intelligence briefing is ready.', '/dashboard/brief').catch(e => console.warn('[Push] digest push failed:', e.message));
    }, { timezone: 'Europe/London' });

    // Weekly pattern analysis — Sunday 03:00 UTC
    cron.schedule('0 3 * * 0', async () => {
      console.log('[Self] Running weekly pattern analysis...');
      try {
        const { patterns, runsAnalyzed } = await analyzePatterns(llmProvider);
        console.log(`[Self] Pattern analysis complete — ${patterns.length} patterns from ${runsAnalyzed} runs`);
        if (telegramAlerter?.isConfigured && patterns.length > 0) {
          const stored = getPatterns();
          await telegramAlerter.sendMessage(
            `🔍 *WEEKLY PATTERN UPDATE*\n${patterns.length} intelligence patterns detected from ${runsAnalyzed} historical runs.\n\n/patterns to view`
          );
        }
      } catch (e) { console.error('[Self] Pattern analysis failed:', e.message); }
    }, { timezone: 'Europe/London' });

    // Daily internet exploration — 06:00 UTC (morning sweep) + 14:00 UTC (afternoon sweep)
    const runDailyExploration = async () => {
      console.log('[Self] Running daily web exploration...');
      try {
        const findings = await runExploration(llmProvider);
        console.log(`[Self] Exploration complete — ${findings.insights?.length || 0} insights, ${findings.salesIdeas?.length || 0} ideas`);
        if (telegramAlerter?.isConfigured && (findings.insights?.length > 0 || findings.salesIdeas?.length > 0)) {
          await telegramAlerter.sendMessage(formatExplorerFindingsForTelegram(findings));
        }
      } catch (e) { console.error('[Self] Web exploration failed:', e.message); }
    };
    cron.schedule('0 6 * * *',  runDailyExploration, { timezone: 'Europe/London' });
    cron.schedule('0 14 * * *', runDailyExploration, { timezone: 'Europe/London' });

    // Daily autonomous maintenance — 02:00 London
    // 1) Auto-disables sources with ≥90% failure rate (≥20 sweeps of data)
    // 2) Auto-deploys staged modules that pass the briefing() test
    // 3) Alerts sources still degraded but not yet auto-disabled (need manual review or LLM fix)
    cron.schedule('0 2 * * *', async () => {
      console.log('[AutoMaint] Daily autonomous maintenance starting...');
      try {
        const { autoDisableDegradedSources, autoDeployStaged } = await import('./lib/self/updater.mjs');
        const ts = londonTs();

        // Step 1: auto-disable dead sources
        const disabled = autoDisableDegradedSources(0.90, 20);
        if (disabled.length > 0) {
          console.log(`[AutoMaint] Auto-disabled: ${disabled.map(d => d.name).join(', ')}`);
          if (telegramAlerter?.isConfigured) {
            await telegramAlerter.sendMessage(
              `⚙️ *AUTO-MAINTENANCE*\n_${ts} London_\n\n${disabled.map(d => `⛔ \`${d.name}\` disabled (${d.failRate}% fail rate)`).join('\n')}\n\n_Dead sources removed automatically. /sources for full report_`
            );
          }
        }

        // Step 2: auto-deploy staged modules that pass test
        const deployResults = await autoDeployStaged();
        const deployed = deployResults.filter(r => r.deployed);
        const skipped  = deployResults.filter(r => !r.deployed);
        if (deployed.length > 0) {
          console.log(`[AutoMaint] Auto-deployed: ${deployed.map(d => d.moduleName).join(', ')}`);
          if (telegramAlerter?.isConfigured) {
            await telegramAlerter.sendMessage(
              `🚀 *AUTO-DEPLOY*\n_${ts} London_\n\n${deployed.map(d => `✅ \`${d.moduleName}\` — ${d.testResult?.updates || 0} updates`).join('\n')}\n\n_New source modules deployed and tested automatically._`
            );
          }
        }
        if (skipped.length > 0) {
          console.log(`[AutoMaint] Deploy skipped (test failed): ${skipped.map(s => s.moduleName).join(', ')}`);
        }

        // Step 3: report remaining degraded sources that need LLM fix
        const toReview = getSourcesToReview().filter(s => s.status === 'critical' && (s.totalOk + s.totalFail) >= 48);
        const unfixed  = toReview.filter(s => !disabled.find(d => d.name === s.name));
        if (unfixed.length > 0) {
          console.log(`[AutoMaint] ${unfixed.length} source(s) still degraded — staging LLM fixes...`);
          for (const source of unfixed.slice(0, 3)) {
            try {
              const { generateSourceFix, stageModule } = await import('./lib/self/code_generator.mjs');
              const fix = await generateSourceFix(llmProvider, source.name, `Reliability ${source.reliability}% — consistently failing`);
              if (fix.success) {
                await stageModule(source.name, fix.code, { type: 'fix', description: `Auto-fix: ${source.name} was ${source.reliability}% reliable`, confidence: 0.75 });
                console.log(`[AutoMaint] LLM fix staged for: ${source.name}`);
              }
            } catch (err) {
              console.warn(`[AutoMaint] Fix staging failed for ${source.name}:`, err.message);
            }
          }
          if (telegramAlerter?.isConfigured) {
            const names = unfixed.map(s => `▸ \`${s.name}\` (${s.reliability}% reliable)`).join('\n');
            await telegramAlerter.sendMessage(
              `🔴 *SOURCE HEALTH ALERT*\n_${ts} London_\n\n${unfixed.length} source(s) degraded — LLM fixes staged:\n${names}\n\n_/sources for full report · fixes auto-deploy tomorrow if tests pass_`
            );
          }
        }

      } catch (e) { console.error('[AutoMaint] Daily maintenance failed:', e.message); }
    }, { timezone: 'Europe/London' });
  });
}

// ── Explorer auto-scheduler (curiosity → web exploration loop) ───────────────
if (BRAIN_URL) {
  startExplorerScheduler(app, redisAdapter, notifyAdmin);
  console.log('[Init] Explorer auto-scheduler started (curiosity thread resolution)');
}

// ── Express error handler — MUST be last middleware ──────────────────────────
app.use(errorTracker.expressMiddleware());

process.on('unhandledRejection', (err) => {
  console.error('[Crucix] Unhandled rejection:', err?.stack || err?.message || err);
});
process.on('uncaughtException', (err) => {
  console.error('[Crucix] Uncaught exception:', err?.stack || err?.message || err);
});

start().catch(err => {
  console.error('[Crucix] FATAL — Server failed to start:', err?.stack || err?.message || err);
  process.exit(1);
});

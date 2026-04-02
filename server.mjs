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
import { fullBriefing } from './apis/briefing.mjs';
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
import { getLearningStats, getOutcomes, recordAlertOutcome, getSourceHistory, getSourcesToReview, getPatterns, getOpportunities, getExplorerFindings, getUpdateLog, recordSourceSweep } from './lib/self/learning_store.mjs';
import { detectOpportunities, formatOpportunitiesForTelegram } from './lib/self/opportunity_engine.mjs';
import { analyzePatterns, formatPatternsForTelegram } from './lib/self/pattern_analyzer.mjs';
import { runExploration, exploreQuery, formatExplorerFindingsForTelegram } from './lib/self/web_explorer.mjs';
import { generateSourceModule, generateSourceFix, stageModule, getStagedModules, getStagedCode, formatStagedForTelegram } from './lib/self/code_generator.mjs';
import { deployModule, rollbackModule, validateSyntax, isRestartPending, clearRestartFlag, triggerGracefulRestart, getAutoManagedModules } from './lib/self/updater.mjs';
import { createUser, findUserByEmail, findUserByUsername, findUserById, updateUser, deleteUser, listUsers, verifyPassword, hashPassword, createToken, verifyToken, generateCode, initAdminUser } from './lib/auth/users.mjs';
import { sendVerificationEmail, sendPasswordResetEmail, sendWelcomeEmail, sendAdminNotification } from './lib/auth/email.mjs';
import { initVapid, getVapidPublicKey, saveSubscription, removeSubscription, pushFlash, pushDigest } from './lib/push/push.mjs';
import { createServer } from 'http';
import { Server as SocketIOServer } from 'socket.io';
import { storeMessage, getConversation, markRead, getConversationSummaries, unreadCount } from './lib/messages.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = __dirname;
const RUNS_DIR = join(ROOT, 'runs');
const MEMORY_DIR = join(RUNS_DIR, 'memory');

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

// === LLM + Telegram + Discord ===
const llmProvider = createLLMProvider(config.llm);
const telegramAlerter = new TelegramAlerter(config.telegram);

// === Auth & Push Initialization ===
initAdminUser().catch(err => console.error('[Auth] initAdminUser failed:', err.message));
initVapid().catch(err => console.error('[Push] initVapid failed:', err.message));

// MONKEY-PATCH: Override _handleBrief on the instance to guarantee the 8-section
// ARKMURUS format even if Seenode's persistent volume has an older telegram.mjs loaded.
// The old telegram.mjs has `handlers = { '/brief': () => this._handleBrief() }` which
// calls this method on the instance — patching here wins regardless of prototype version.
telegramAlerter._handleBrief = async function() {
  console.log('[Telegram] _handleBrief() called — server.mjs monkey-patch ARKMURUS 8-section');
  try {
    const data = await this._getCachedData();
    if (!data) return `⏳ Intelligence data is loading — please try again in 60 seconds.`;

    const ts  = new Date().toISOString().slice(0, 19).replace('T', ' ');
    const ds  = data.delta?.summary || {};
    const dir = ds.direction;
    const vix = data.fred?.find(f => f.id === 'VIXCLS');
    const oil = data.energy || {};
    const corrs = data.correlations || [];
    const critCorrs = corrs.filter(c => c.severity === 'critical' || c.severity === 'high');

    let msg = `*ARKMURUS INTELLIGENCE BRIEF*\n_${ts} UTC_\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n`;

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
      ? new Date(new Date(lastSweepTime).getTime() + config.refreshIntervalMinutes * 60000).toLocaleTimeString()
      : 'pending';
    return [
      `🖥️ *CRUCIX STATUS*`, ``,
      `Uptime: ${h}h ${m}m`,
      `Last sweep: ${lastSweepTime ? new Date(lastSweepTime).toLocaleTimeString() + ' UTC' : 'never'}`,
      `Next sweep: ${nextSweep} UTC`,
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
      ? new Date(new Date(lastSweepTime).getTime() + config.refreshIntervalMinutes * 60000).toLocaleTimeString()
      : 'pending';
    return [
      `**🖥️ CRUCIX STATUS**\n`,
      `Uptime: ${h}h ${m}m`,
      `Last sweep: ${lastSweepTime ? new Date(lastSweepTime).toLocaleTimeString() + ' UTC' : 'never'}`,
      `Next sweep: ${nextSweep} UTC`,
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
    const sections = [`**📋 CRUCIX BRIEF**\n_${new Date().toISOString().replace('T', ' ').substring(0, 19)} UTC_\n`];
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
app.use(express.json());

// Site access is protected by the Angular JWT auth layer — no HTTP Basic Auth needed.

// Angular dashboard — served at root / (must be before dashboard/public static)
const ANGULAR_DIST = join(ROOT, 'frontend', 'dist', 'crucix-admin');
if (existsSync(ANGULAR_DIST)) {
  app.use(express.static(ANGULAR_DIST));
  // Angular client-side routing: catch all non-API routes and serve index.html
  app.get('/*path', (req, res, next) => {
    if (req.path.startsWith('/api/') || req.path === '/events' || req.path === '/webhook') return next();
    res.sendFile(join(ANGULAR_DIST, 'index.html'));
  });
  console.log('[Crucix] Angular dashboard live at /');
} else {
  app.use(express.static(join(ROOT, 'dashboard/public')));
}

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
  const token = req.headers.authorization?.replace('Bearer ', '');
  if (!token) return res.status(401).json({ error: 'Authentication required' });
  try {
    req.user = verifyToken(token);
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

    const user = createUser({ username, email, password, fullName });

    await sendAdminNotification(
      'New user registration — approval required',
      `<p>New user registered and awaiting your approval:</p>
       <ul>
         <li><strong>Name:</strong> ${user.fullName}</li>
         <li><strong>Email:</strong> ${email}</li>
         <li><strong>Username:</strong> ${username}</li>
       </ul>
       <p>Log in to the admin panel and go to <strong>Admin → Users</strong> to approve or reject this account.</p>`
    ).catch(() => {});

    console.log(`[Auth] New registration pending approval: ${email}`);
    res.json({ message: 'Account created — awaiting admin approval. You will be notified once your account is activated.' });
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

    const token = createToken(user.id, user.role);
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
    const token = createToken(user.id, user.role);
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

    updateUser(user.id, { status: 'active', verificationCode: null, verificationExpiry: null });
    await sendWelcomeEmail(email, user.fullName).catch(() => {});
    res.json({ message: 'Email verified successfully. You can now log in.' });
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

// ── Admin User Management Routes ──────────────────────────────────────────────

app.get('/api/admin/users', requireAdmin, (req, res) => {
  try {
    res.json(listUsers());
  } catch (err) {
    res.status(500).json({ error: 'Failed to list users' });
  }
});

app.put('/api/admin/users/:id', requireAdmin, (req, res) => {
  try {
    const { role, status, notifyDigest, notifyFlash } = req.body || {};
    const updates = {};
    if (role         !== undefined) updates.role         = role;
    if (status       !== undefined) updates.status       = status;
    if (notifyDigest !== undefined) updates.notifyDigest = !!notifyDigest;
    if (notifyFlash  !== undefined) updates.notifyFlash  = !!notifyFlash;
    const updated = updateUser(req.params.id, updates);
    res.json(updated);
  } catch (err) {
    res.status(500).json({ error: err.message || 'Failed to update user' });
  }
});

app.delete('/api/admin/users/:id', requireAdmin, (req, res) => {
  try {
    if (req.params.id === req.user.userId) {
      return res.status(400).json({ error: 'Cannot delete your own account' });
    }
    deleteUser(req.params.id);
    res.json({ message: 'User deleted' });
  } catch (err) {
    res.status(500).json({ error: err.message || 'Failed to delete user' });
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
  console.log(`[Crucix] Starting sweep at ${new Date().toLocaleTimeString()}`);
  console.log(`${'='.repeat(60)}`);

  try {
    const rawData = await fullBriefing();

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
    console.log(`[Crucix] Next sweep at ${new Date(Date.now() + config.refreshIntervalMinutes * 60000).toLocaleTimeString()}`);

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

    // Self-ping every 4 minutes — keeps free-tier hosts (Render etc.) awake.
    // On paid/VPS hosts (Seenode etc.) this is harmless but not needed.
    if (process.env.RENDER || process.env.RENDER_EXTERNAL_URL || process.env.SELF_PING === 'true') {
      const selfUrl = process.env.APP_URL || process.env.RENDER_EXTERNAL_URL || `http://localhost:${port}`;
      setInterval(async () => {
        try {
          await fetch(`${selfUrl}/api/health`, { signal: AbortSignal.timeout(10000) });
        } catch {}
      }, 4 * 60 * 1000);
      console.log('[Crucix] Self-ping enabled');
    }

    cron.schedule('0 7 * * *', async () => {
      console.log('[Crucix] Sending morning digest...');
      try { await sendMorningDigest(telegramAlerter, currentData); }
      catch (e) { console.error('[Digest] Failed:', e.message); }
      pushDigest('Morning Intelligence Brief', 'Your daily Arkmurus intelligence briefing is ready.', '/dashboard/brief').catch(e => console.warn('[Push] digest push failed:', e.message));
    }, { timezone: 'UTC' });

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
    }, { timezone: 'UTC' });

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
    cron.schedule('0 6 * * *',  runDailyExploration, { timezone: 'UTC' });
    cron.schedule('0 14 * * *', runDailyExploration, { timezone: 'UTC' });

    // Daily source health review — 02:00 UTC
    // Auto-stages fixes for sources with <40% reliability over 48+ sweeps
    cron.schedule('0 2 * * *', async () => {
      console.log('[Self] Daily source health review...');
      try {
        const toReview = getSourcesToReview().filter(s => s.status === 'critical' && (s.totalOk + s.totalFail) >= 48);
        if (toReview.length === 0) return;
        console.log(`[Self] ${toReview.length} critical source(s) flagged for review: ${toReview.map(s => s.name).join(', ')}`);
        if (telegramAlerter?.isConfigured) {
          const names = toReview.map(s => `▸ ${s.name} (${s.reliability}% reliable)`).join('\n');
          await telegramAlerter.sendMessage(
            `🔴 *SOURCE HEALTH ALERT*\n${toReview.length} source(s) critically degraded:\n${names}\n\n/sources fix <name> to auto-repair\n/sources for full health report`
          );
        }
      } catch (e) { console.error('[Self] Source health review failed:', e.message); }
    }, { timezone: 'UTC' });
  });
}

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

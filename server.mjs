#!/usr/bin/env node
// Crucix Intelligence Engine — Dev Server
// Serves the Jarvis dashboard, runs sweep cycle, pushes live updates via SSE

import express from 'express';
import basicAuth from 'express-basic-auth';
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

// === Selective Basic Auth ===
// Protects dashboard only — API routes, webhook and SSE are open
const dashboardUser = process.env.DASHBOARD_USER || 'arkmurus';
const dashboardPass = process.env.DASHBOARD_PASS || 'Crucix2026!';
app.use((req, res, next) => {
  if (
    req.path.startsWith('/api/') ||
    req.path === '/webhook' ||
    req.path === '/events' ||
    req.path.startsWith('/search')
  ) {
    return next();
  }
  return basicAuth({
    users: { [dashboardUser]: dashboardPass },
    challenge: true,
    realm: 'Crucix Intelligence'
  })(req, res, next);
});

app.use(express.static(join(ROOT, 'dashboard/public')));

app.get('/', (req, res) => {
  if (!currentData) {
    res.sendFile(join(ROOT, 'dashboard/public/loading.html'));
  } else {
    const htmlPath = join(ROOT, 'dashboard/public/jarvis.html');
    let html = readFileSync(htmlPath, 'utf-8');
    const locale = getLocale();
    const localeScript = `<script>window.__CRUCIX_LOCALE__ = ${JSON.stringify(locale).replace(/<\/script>/gi, '<\\/script>')};</script>`;
    html = html.replace('</head>', `${localeScript}\n</head>`);
    res.type('html').send(html);
  }
});

app.get('/api/data', (req, res) => {
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

app.get('/api/source-health', (req, res) => {
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

app.get('/api/search', async (req, res) => {
  const query = req.query.q;
  if (!query) return res.json({ error: 'No query provided' });
  console.log(`[Search API] Searching for: ${query}`);
  try {
    let wikipedia = null;
    try {
      const wikiResponse = await fetch(
        `https://en.wikipedia.org/api/rest_v1/page/summary/${encodeURIComponent(query.replace(/ /g, '_'))}`,
        { headers: { 'User-Agent': 'Crucix/1.0' } }
      );
      if (wikiResponse.ok) wikipedia = await wikiResponse.json();
    } catch (e) { console.log('Wikipedia error:', e.message); }

    let duckduckgo = null;
    try {
      const ddgResponse = await fetch(
        `https://api.duckduckgo.com/?q=${encodeURIComponent(query)}&format=json&no_html=1`,
        { headers: { 'User-Agent': 'Crucix/1.0' } }
      );
      if (ddgResponse.ok) duckduckgo = await ddgResponse.json();
    } catch (e) { console.log('DuckDuckGo error:', e.message); }

    const verificationLinks = {
      openCorporates: `https://opencorporates.com/companies?q=${encodeURIComponent(query)}`,
      ofacSanctions: `https://sanctionssearch.ofac.treas.gov/Search.aspx?searchText=${encodeURIComponent(query)}`,
      openSanctions: `https://www.opensanctions.org/search/?q=${encodeURIComponent(query)}`,
      defenseNews: `https://www.defensenews.com/search/?q=${encodeURIComponent(query)}`,
      googleSearch: `https://www.google.com/search?q=${encodeURIComponent(query)}+defense+contracts`,
      secEdgar: `https://www.sec.gov/edgar/searchedgar/companysearch.html?q=${encodeURIComponent(query)}`,
      sipriArms: `https://www.sipri.org/databases/armstransfers`,
      companiesHouse: `https://find-and-update.company-information.service.gov.uk/search?q=${encodeURIComponent(query)}`
    };

    res.json({
      success: true, query, timestamp: new Date().toISOString(),
      wikipedia: wikipedia ? {
        title: wikipedia.title, description: wikipedia.description,
        extract: wikipedia.extract, url: wikipedia.content_urls?.desktop?.page
      } : null,
      duckduckgo: duckduckgo?.Abstract ? { abstract: duckduckgo.Abstract, url: duckduckgo.AbstractURL } : null,
      verificationLinks
    });
  } catch (error) {
    console.error('[Search API] Error:', error);
    res.json({ success: false, error: error.message });
  }
});

app.post('/api/sweep', async (req, res) => {
  try {
    if (sweepInProgress) return res.json({ success: false, message: 'Sweep already in progress' });
    runSweepCycle().catch(err => console.error('[Crucix] Manual sweep failed:', err.message));
    res.json({ success: true, message: 'Sweep triggered' });
  } catch (error) {
    res.json({ success: false, error: error.message });
  }
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

    // Update source health tracker
    updateSourceHealth(rawData.timing);

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

    broadcast({ type: 'update', data: currentData });

    console.log(`[Crucix] Sweep complete — ${currentData.meta.sourcesOk}/${currentData.meta.sourcesQueried} sources OK`);
    console.log(`[Crucix] ${currentData.ideas.length} ideas (${synthesized.ideasSource}) | ${currentData.news.length} news | ${currentData.newsFeed.length} feed items`);
    if (delta?.summary) console.log(`[Crucix] Delta: ${delta.summary.totalChanges} changes, ${delta.summary.criticalChanges} critical, direction: ${delta.summary.direction}`);
    if (correlations.length > 0) console.log(`[Crucix] Correlations: ${correlations.map(c => `${c.region}(${c.severity})`).join(', ')}`);
    console.log(`[Crucix] Next sweep at ${new Date(Date.now() + config.refreshIntervalMinutes * 60000).toLocaleTimeString()}`);

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

  const server = app.listen(port);

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

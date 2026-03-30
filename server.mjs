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
import { filterNewSignals } from './lib/intel/dedup.mjs';
import { correlate, formatCorrelationsForTelegram } from './lib/intel/correlate.mjs';
import { archiveRun, analyzeTrends, formatTrendsForTelegram } from './lib/intel/archive.mjs';
import { sendMorningDigest } from './lib/alerts/digest.mjs';
import { fetchUNSecurityCouncil, fetchCentralBanks, fetchThinkTanks, fetchTradeFLows } from './apis/sources/intel-feeds.mjs';
import { fetchOpenSanctions } from './apis/sources/opensanctions.mjs';
import { fetchGDELT } from './apis/sources/gdelt.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = __dirname;
const RUNS_DIR = join(ROOT, 'runs');
const MEMORY_DIR = join(RUNS_DIR, 'memory');

// Ensure directories exist
for (const dir of [RUNS_DIR, MEMORY_DIR, join(MEMORY_DIR, 'cold')]) {
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
}

// === State ===
let currentData = null;
let lastSweepTime = null;
let sweepStartedAt = null;
let sweepInProgress = false;
const startTime = Date.now();
const sseClients = new Set();

// === Delta/Memory ===
const memory = new MemoryManager(RUNS_DIR);

// === LLM + Telegram + Discord ===
const llmProvider = createLLMProvider(config.llm);
const telegramAlerter = new TelegramAlerter(config.telegram);
const discordAlerter = new DiscordAlerter(config.discord || {});

if (llmProvider) console.log(`[Crucix] LLM enabled: ${llmProvider.name} (${llmProvider.model})`);
// 9. Telegram alerts handled exclusively by onSweepComplete (3-hour cadence)
    // Direct evaluateAndAlert calls removed to prevent alert spam
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

    const openCmd = process.platform === 'win32' ? 'cmd /c start ""' :
                    process.platform === 'darwin' ? 'open' : 'xdg-open';
    exec(`${openCmd} "http://localhost:${port}"`, (err) => {
      if (err) console.log('[Crucix] Could not auto-open browser:', err.message);
    });

    try {
      const existing = JSON.parse(readFileSync(join(RUNS_DIR, 'latest.json'), 'utf8'));
      const data = await synthesize(existing);
      currentData = data;
      console.log('[Crucix] Loaded existing data from runs/latest.json — dashboard ready instantly');
      broadcast({ type: 'update', data: currentData });
      if (telegramAlerter && telegramAlerter.isConfigured) {
        await telegramAlerter.onSweepComplete(currentData);
      }
    } catch {
      console.log('[Crucix] No existing data found — first sweep required');
    }

    console.log('[Crucix] Running initial sweep...');
    runSweepCycle().catch(err => {
      console.error('[Crucix] Initial sweep failed:', err.message || err);
    });

    setInterval(runSweepCycle, config.refreshIntervalMinutes * 60 * 1000);

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

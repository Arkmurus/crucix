// ============================================================
// CRUCIX UPGRADE PATCH — server.mjs changes
// Apply these additions to your existing server.mjs
// ============================================================
// 
// STEP 1: Add these imports at the top (after existing imports)
// ──────────────────────────────────────────────────────────────

import { processSignals, filterNewSignals }       from './lib/intel/dedup.mjs';
import { correlate, formatCorrelationsForTelegram } from './lib/intel/correlate.mjs';
import { archiveRun, analyzeTrends }               from './lib/intel/archive.mjs';
import { sendMorningDigest }                        from './lib/alerts/digest.mjs';
import { fetchUNSecurityCouncil, fetchCentralBanks, fetchThinkTanks, fetchTradeFLows } from './apis/sources/intel-feeds.mjs';
import { fetchOpenSanctions, searchSanctions }      from './apis/sources/opensanctions.mjs';
import { fetchGDELT }                               from './apis/sources/gdelt.mjs';

// ──────────────────────────────────────────────────────────────
// STEP 2: Add /trends and /opensearch Telegram commands
// Add these INSIDE the "if (telegramAlerter.isConfigured)" block
// after the existing telegramAlerter.onCommand('/brief', ...) block
// ──────────────────────────────────────────────────────────────

telegramAlerter.onCommand('/trends', async () => {
  const trends = analyzeTrends();
  const { formatTrendsForTelegram } = await import('./lib/intel/archive.mjs');
  return formatTrendsForTelegram(trends);
});

telegramAlerter.onCommand('/correlations', async () => {
  if (!currentData) return 'No data yet — waiting for first sweep.';
  const correlations = correlate(currentData);
  const { formatCorrelationsForTelegram } = await import('./lib/intel/correlate.mjs');
  return formatCorrelationsForTelegram(correlations) || 'No significant convergences detected.';
});

telegramAlerter.onCommand('/sanctions', async () => {
  if (!currentData) return 'No data yet.';
  const recent = currentData.opensanctions?.recent || [];
  if (recent.length === 0) return 'No recent sanctions updates.';
  let msg = `*RECENT SANCTIONS UPDATES*\n\n`;
  for (const e of recent.slice(0, 8)) {
    msg += `• ${e.name} — ${e.datasets.join(', ')}\n`;
  }
  return msg;
});

// ──────────────────────────────────────────────────────────────
// STEP 3: Replace the runSweepCycle function body
// Find "const rawData = await fullBriefing();" and ADD these lines 
// directly AFTER that line (before writeFileSync):
// ──────────────────────────────────────────────────────────────

// --- ADD AFTER: const rawData = await fullBriefing(); ---

// Fetch new intelligence sources in parallel
console.log('[Crucix] Fetching extended intelligence sources...');
const [unscData, centralBanksData, thinkTanksData, tradeData, opensanctionsData, gdeltData] = 
  await Promise.allSettled([
    fetchUNSecurityCouncil(),
    fetchCentralBanks(),
    fetchThinkTanks(),
    fetchTradeFLows(),
    fetchOpenSanctions(),
    fetchGDELT(),
  ]).then(results => results.map(r => r.status === 'fulfilled' ? r.value : null));

// Merge into rawData
rawData.unsc         = unscData;
rawData.centralBanks = centralBanksData;
rawData.thinkTanks   = thinkTanksData;
rawData.tradeFlows   = tradeData;
rawData.opensanctions = opensanctionsData;
rawData.gdelt        = gdeltData;

// --- END ADD ---

// ──────────────────────────────────────────────────────────────
// STEP 4: Add these lines AFTER "const delta = memory.addRun(synthesized);"
// ──────────────────────────────────────────────────────────────

// Archive this run for trend analysis
archiveRun(synthesized);

// Cross-signal correlation
const correlations = correlate(synthesized);
synthesized.correlations = correlations;
if (correlations.length > 0) {
  console.log(`[Crucix] ${correlations.length} regional correlations detected`);
}

// ──────────────────────────────────────────────────────────────
// STEP 5: Replace the Telegram alert block
// Find: "if (delta?.summary?.totalChanges > 0) {"
// Replace the ENTIRE block with this:
// ──────────────────────────────────────────────────────────────

if (telegramAlerter.isConfigured) {
  // Deduplicate OSINT signals — only alert on genuinely new ones
  const newSignals = filterNewSignals(synthesized.tg?.urgent || []);
  
  if (newSignals.length > 0 || delta?.summary?.totalChanges > 0) {
    // Send convergence alert if multiple sources point to same region
    const corrMsg = formatCorrelationsForTelegram(correlations);
    if (corrMsg) {
      telegramAlerter.sendMessage(corrMsg).catch(err =>
        console.error('[Crucix] Correlation alert error:', err.message));
    }

    telegramAlerter.evaluateAndAlert(llmProvider, delta, memory).catch(err =>
      console.error('[Crucix] Telegram alert error:', err.message));
  } else {
    console.log('[Telegram] No new signals — alert suppressed by dedup');
  }

  if (discordAlerter.isConfigured) {
    discordAlerter.evaluateAndAlert(llmProvider, delta, memory).catch(err =>
      console.error('[Crucix] Discord alert error:', err.message));
  }
}

// ──────────────────────────────────────────────────────────────
// STEP 6: Add daily digest cron job
// Find the existing cron.schedule line and ADD this block after it:
// ──────────────────────────────────────────────────────────────

// Daily morning briefing at 07:00 UTC
cron.schedule('0 7 * * *', async () => {
  console.log(`[Crucix] Sending morning digest...`);
  try {
    await sendMorningDigest(telegramAlerter, currentData);
  } catch (e) { console.error('[Digest] Failed:', e.message); }
}, { timezone: 'UTC' });

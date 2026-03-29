// === Tier 1: Core OSINT & Geopolitical ===
import { fetchGDELT as gdelt } from './sources/gdelt.mjs';
import { briefing as opensky } from './sources/opensky.mjs';
import { briefing as firms } from './sources/firms.mjs';
import { briefing as ships } from './sources/ships.mjs';
import { briefing as safecast } from './sources/safecast.mjs';
import { briefing as acled } from './sources/acled.mjs';
import { briefing as reliefweb } from './sources/reliefweb.mjs';
import { briefing as who } from './sources/who.mjs';
import { briefing as ofac } from './sources/ofac.mjs';
import { fetchOpenSanctions as opensanctions } from './sources/opensanctions.mjs';
import { briefing as adsb } from './sources/adsb.mjs';

// === Tier 2: Economic & Financial ===
import { briefing as fred } from './sources/fred.mjs';
import { briefing as treasury } from './sources/treasury.mjs';
import { briefing as bls } from './sources/bls.mjs';
import { briefing as eia } from './sources/eia.mjs';
import { briefing as gscpi } from './sources/gscpi.mjs';
import { briefing as usaspending } from './sources/usaspending.mjs';
import { briefing as comtrade } from './sources/comtrade.mjs';

// === Tier 3: Weather, Environment, Technology, Social ===
import { briefing as noaa } from './sources/noaa.mjs';
import { briefing as epa } from './sources/epa.mjs';
import { briefing as patents } from './sources/patents.mjs';
import { briefing as bluesky } from './sources/bluesky.mjs';
import { briefing as reddit } from './sources/reddit.mjs';
import { briefing as telegram } from './sources/telegram.mjs';
import { briefing as kiwisdr } from './sources/kiwisdr.mjs';

// === Tier 4: Space & Satellites ===
import { briefing as space } from './sources/space.mjs';

// === Tier 5: Live Market Data ===
import { briefing as yfinance } from './sources/yfinance.mjs';

// === Tier 6: Cyber & Infrastructure ===
import { briefing as cisaKev } from './sources/cisa-kev.mjs';
import { briefing as cloudflareRadar } from './sources/cloudflare-radar.mjs';
import { briefing as supplyChainBriefing } from './sources/supply_chain.mjs';

// === Tier 7: Defense & Weapons Intelligence ===
import { briefing as defenseNewsBriefing } from './sources/defense_news.mjs';
import { briefing as sipriBriefing } from './sources/sipri_arms.mjs';

// === Tier 8: Due Diligence & Compliance ===
import { briefing as opencorporatesBriefing } from './sources/opencorporates.mjs';
import { briefing as sanctionsBriefing } from './sources/sanctions.mjs';
import { briefing as exportControlsBriefing } from './sources/export_controls.mjs';

// === Tier 9: Custom Business Intelligence ===
import { briefing as arkumurus } from './sources/arkumurus.mjs';

const SOURCE_TIMEOUT_MS = 30_000;

export async function runSource(name, fn, ...args) {
  const start = Date.now();
  let timer;
  try {
    const dataPromise = fn(...args);
    const timeoutPromise = new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(`Source ${name} timed out`)), SOURCE_TIMEOUT_MS);
    });
    const data = await Promise.race([dataPromise, timeoutPromise]);
    return { name, status: 'ok', durationMs: Date.now() - start, data };
  } catch (e) {
    return { name, status: 'error', durationMs: Date.now() - start, error: e.message };
  } finally {
    clearTimeout(timer);
  }
}

export async function fullBriefing() {
  console.error('[Crucix] Starting intelligence sweep — 36 sources...');
  const start = Date.now();

  const results = await Promise.allSettled([
    runSource('GDELT', gdelt),
    runSource('OpenSky', opensky),
    runSource('FIRMS', firms),
    runSource('Maritime', ships),
    runSource('Safecast', safecast),
    runSource('ACLED', acled),
    runSource('ReliefWeb', reliefweb),
    runSource('WHO', who),
    runSource('OFAC', ofac),
    runSource('OpenSanctions', opensanctions),
    runSource('ADS-B', adsb),
    runSource('Supply Chain', supplyChainBriefing),
    runSource('FRED', fred, process.env.FRED_API_KEY),
    runSource('Treasury', treasury),
    runSource('BLS', bls, process.env.BLS_API_KEY),
    runSource('EIA', eia, process.env.EIA_API_KEY),
    runSource('GSCPI', gscpi),
    runSource('USAspending', usaspending),
    runSource('Comtrade', comtrade),
    runSource('NOAA', noaa),
    runSource('EPA', epa),
    runSource('Patents', patents),
    runSource('Bluesky', bluesky),
    runSource('Reddit', reddit),
    runSource('Telegram', telegram),
    runSource('KiwiSDR', kiwisdr),
    runSource('Space', space),
    runSource('YFinance', yfinance),
    runSource('CISA-KEV', cisaKev),
    runSource('Cloudflare-Radar', cloudflareRadar),
    runSource('Defense News', defenseNewsBriefing),
    runSource('SIPRI Arms', sipriBriefing),
    runSource('OpenCorporates', opencorporatesBriefing),
    runSource('Sanctions', sanctionsBriefing),
    runSource('ExportControls', exportControlsBriefing),
    runSource('Arkumurus', arkumurus),
  ]);

  const sources = results.map(r => r.status === 'fulfilled' ? r.value : { status: 'failed', error: r.reason?.message });
  const totalMs = Date.now() - start;

  const allUpdates = [];
  const allSignals = [];
  const allMarkers = [];
  const allAlerts = [];

  for (const source of sources) {
    if (source.status === 'ok' && source.data) {
      if (Array.isArray(source.data.updates)) allUpdates.push(...source.data.updates);
      if (Array.isArray(source.data.signals)) allSignals.push(...source.data.signals);
      if (Array.isArray(source.data.markers)) allMarkers.push(...source.data.markers);
      if (Array.isArray(source.data.alerts)) allAlerts.push(...source.data.alerts);
    }
  }

  const output = {
    crucix: {
      version: '2.0.0',
      timestamp: new Date().toISOString(),
      totalDurationMs: totalMs,
      sourcesQueried: sources.length,
      sourcesOk: sources.filter(s => s.status === 'ok').length,
      sourcesFailed: sources.filter(s => s.status !== 'ok').length,
    },
    sources: Object.fromEntries(sources.filter(s => s.status === 'ok').map(s => [s.name, s.data])),
    errors: sources.filter(s => s.status !== 'ok').map(s => ({ name: s.name, error: s.error })),
    timing: Object.fromEntries(sources.map(s => [s.name, { status: s.status, ms: s.durationMs }])),
    dashboard: {
      updates: allUpdates.slice(0, 50),
      signals: allSignals.slice(0, 20),
      markers: allMarkers.slice(0, 100),
      alerts: allAlerts.slice(0, 30),
      counts: {
        totalUpdates: allUpdates.length,
        totalSignals: allSignals.length,
        totalMarkers: allMarkers.length,
        totalAlerts: allAlerts.length
      }
    }
  };

  console.error(`[Crucix] Sweep complete in ${totalMs}ms — ${output.crucix.sourcesOk}/${sources.length} sources returned data`);
  console.error(`[Crucix] Dashboard ready: ${output.dashboard.counts.totalUpdates} updates, ${output.dashboard.counts.totalSignals} signals`);
  return output;
}
```

Press **Ctrl+S** to save. Then commit and push:
```
cd C:\Users\anton\OneDrive\Documents\GitHub\crucix
git add apis/briefing.mjs
git commit -m "Restore fullBriefing function body"
git push origin main
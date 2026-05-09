#!/usr/bin/env node

// Crucix Master Orchestrator — runs all intelligence sources in parallel
// Outputs structured JSON for Claude to synthesize into actionable briefing

import './utils/env.mjs'; // Load API keys from .env
import { pathToFileURL } from 'node:url';
import { redisPush } from '../lib/persist/store.mjs';

// Hooks injected by server.mjs after startup — zero coupling
let _onSourceSuccess = null;
let _onSourceError   = null;
let _isSuspended     = null;
export function registerSourceHooks({ onSuccess, onError, isSuspended }) {
  _onSourceSuccess = onSuccess;
  _onSourceError   = onError;
  _isSuspended     = isSuspended;
}

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

// === Tier 8b: Prediction Markets ===
import { briefing as polymarket } from './sources/polymarket.mjs';

// === Tier 8c: Regional Intelligence ===
import { briefing as lusophone } from "./sources/lusophone.mjs";
import { briefing as exportControlIntel } from "./sources/export_control_intel.mjs";

// === Tier 8e: Global Defence (Asia, MENA, LatAm, Africa) ===
import { briefing as globalDefence } from "./sources/global_defence.mjs";

// === Tier 8d: Counterparty Risk ===
import { briefing as counterpartyRisk } from "./sources/counterparty_risk.mjs";

// === Tier 8e: Corporate Intelligence ===
import { briefing as secEdgar } from "./sources/sec_edgar.mjs";

// === Tier 8f: Cyber Threats ===
import { briefing as cyberThreats } from "./sources/cyber_threats.mjs";

// === Tier 8g: Export Controls & Compliance ===
import { briefing as euDualUse } from "./sources/eu_dual_use.mjs";

// === Tier 8h: Development Finance (Lusophone) ===
import { briefing as afdb } from "./sources/afdb.mjs";

// === Tier 8i: Port Congestion & Cable Monitoring ===
import { briefing as portCongestion } from "./sources/port_congestion.mjs";

// === Tier 7b: Defence Events Calendar ===
import { briefing as defenseEvents } from './sources/defense_events.mjs';

// === Tier 7c: Live Procurement Tenders & FMS Notifications ===
import { briefing as procurementTenders } from './sources/procurement_tenders.mjs';

// === Tier 7d: Government Procurement Portal Monitoring ===
import { briefing as procurementPortals } from './sources/procurement_portals.mjs';

// === Tier 9: Custom Business Intelligence ===
import { briefing as arkumurus } from './sources/arkumurus.mjs';

// === Auto-Managed Sources (deployed by self-update engine) ===
import { AUTO_SOURCES } from './auto_sources.mjs';

const SOURCE_TIMEOUT_MS = 30_000; // 30s max per individual source

// R-F66 (2026-05-09): per-source timeout overrides for known-slow APIs.
// Mirrors R-F64 NVD pattern. Anything not listed here uses the 30s default.
// Live evidence 2026-05-09 12:22:39 sweep: GDELT timed out at 30s every
// cycle, dropping the tally from 49/49 → 48/49.
const SOURCE_TIMEOUT_OVERRIDES = {
  GDELT: 45_000,  // GDELT v2 doc API responds in 25-40s under load
};

export async function runSource(name, fn, ...args) {
  // GAP 11: Skip suspended sources — with a probation probe so
  // auto-recovery can actually fire.
  //
  // Previously this unconditionally short-circuited suspended sources.
  // But the pruner's auto-recovery branch requires a `success=true`
  // recordFetch, and recordFetch is only called after the fetch runs.
  // So suspended sources were locked out forever — the "recover when
  // reliability rises above 40%" path was unreachable.
  //
  // Solution: probe suspended sources on ~1 in 20 sweeps (every ~2.3h
  // at 7-min sweep cadence). The probe runs the full fetch and records
  // the result normally, so the existing recover logic can fire after
  // 3 consecutive successes. No change to the suspension threshold.
  if (_isSuspended) {
    try {
      if (await _isSuspended(name)) {
        const probe = Math.random() < 0.05; // 5% probation probe
        if (!probe) {
          console.warn(`[Source] ${name} skipped — auto-suspended. Probing on ~5% of sweeps; operator override: POST /api/admin/sources/${name}/enable`);
          return {
            name,
            status: 'suspended',
            durationMs: 0,
            error: 'auto-suspended (<15% reliability / 20+ sweeps) — probing on ~5% of sweeps; use /api/admin/sources/<name>/enable to force-enable',
          };
        }
        console.warn(`[Source] ${name} probation probe — running despite suspension to test recovery`);
      }
    } catch {}
  }

  const start = Date.now();
  let timer;
  const effectiveTimeoutMs = SOURCE_TIMEOUT_OVERRIDES[name] ?? SOURCE_TIMEOUT_MS;
  try {
    const dataPromise = fn(...args);
    const timeoutPromise = new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(`Source ${name} timed out after ${effectiveTimeoutMs / 1000}s`)), effectiveTimeoutMs);
    });
    const data = await Promise.race([dataPromise, timeoutPromise]);

    // R-F34 (2026-05-03): honest tally for aggregator sources. Aggregators
    // (DefenseNews, CyberThreats, ExportControlIntel, Arkmurus,
    // ProcurementTenders) wrap many sub-sources internally. They catch
    // their own sub-source errors and return a normal data shape with
    // empty arrays — so this runner sees status='ok' even when every
    // sub-source failed. The "49/49 sources OK" summary then lies.
    //
    // Aggregators that opt in expose `data._subStatus = {ok, total,
    // failed}`. Promote to 'partial' (some sub-sources failed) or
    // 'error' (none returned). Sources without _subStatus stay 'ok' as
    // before — backwards compatible.
    let status = 'ok';
    let subStatus = null;
    if (data && typeof data === 'object' && data._subStatus
        && typeof data._subStatus.ok === 'number'
        && typeof data._subStatus.total === 'number'
        && data._subStatus.total > 0) {
      subStatus = {
        ok:     data._subStatus.ok,
        total:  data._subStatus.total,
        failed: Array.isArray(data._subStatus.failed) ? data._subStatus.failed.slice(0, 20) : [],
      };
      if (subStatus.ok === 0) status = 'error';
      else if (subStatus.ok < subStatus.total) status = 'partial';
    }

    // Reliability tracker: count partial as success (we still got SOME
    // data back), error as failure. Sub-source-level reliability is a
    // separate concern — the pruner only knows top-level sources.
    if (status === 'error' && _onSourceError) {
      _onSourceError(name, new Error(`all ${subStatus.total} sub-sources failed`), Date.now() - start);
    } else if (_onSourceSuccess) {
      _onSourceSuccess(name, Date.now() - start);
    }

    const result = { name, status, durationMs: Date.now() - start, data };
    if (subStatus) result.subStatus = subStatus;
    return result;
  } catch (e) {
    if (_onSourceError) _onSourceError(name, e, Date.now() - start);
    return { name, status: 'error', durationMs: Date.now() - start, error: e.message };
  } finally {
    clearTimeout(timer);
  }
}

export async function fullBriefing() {
  console.error('[Crucix] Starting intelligence sweep — 48 sources...');
  const start = Date.now();

  const allPromises = [
    // Tier 1: Core OSINT & Geopolitical
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

    // Tier 2: Economic & Financial
    runSource('FRED', fred, process.env.FRED_API_KEY),
    runSource('Treasury', treasury),
    runSource('BLS', bls, process.env.BLS_API_KEY),
    runSource('EIA', eia, process.env.EIA_API_KEY),
    runSource('GSCPI', gscpi),
    runSource('USAspending', usaspending),
    runSource('Comtrade', comtrade),

    // Tier 3: Weather, Environment, Technology, Social
    runSource('NOAA', noaa),
    runSource('EPA', epa),
    runSource('Patents', patents),
    runSource('Bluesky', bluesky),
    runSource('Reddit', reddit),
    runSource('Telegram', telegram),
    runSource('KiwiSDR', kiwisdr),

    // Tier 4: Space & Satellites
    runSource('Space', space),

    // Tier 5: Live Market Data
    runSource('YFinance', yfinance),

    // Tier 6: Cyber & Infrastructure
    runSource('CISA-KEV', cisaKev),
    runSource('Cloudflare-Radar', cloudflareRadar),

    // Tier 7: Defense & Weapons Intelligence
    runSource('Defense News', defenseNewsBriefing),
    runSource('SIPRI Arms', sipriBriefing),
    runSource('DefenseEvents', defenseEvents),
    runSource('ProcurementTenders', procurementTenders),
    runSource('ProcurementPortals', procurementPortals),

    // Tier 8: Due Diligence & Compliance (NEW)
    runSource('OpenCorporates', opencorporatesBriefing),
    runSource('Sanctions', sanctionsBriefing),
    runSource('ExportControls', exportControlsBriefing),

    // Tier 9: Custom Business Intelligence
    runSource('Arkumurus', arkumurus),
    runSource('CounterpartyRisk', counterpartyRisk),
    runSource('Polymarket', polymarket),
    runSource('SecEdgar', secEdgar),
    runSource('CyberThreats', cyberThreats),
    runSource('EuDualUse', euDualUse),
    runSource('AfDB', afdb),
    runSource('PortCongestion', portCongestion),
    runSource('Lusophone', lusophone),
    runSource('ExportControlIntel', exportControlIntel),
    runSource('GlobalDefence', globalDefence),

    // Auto-managed sources deployed by self-update engine
    ...AUTO_SOURCES.map(({ name, fn }) => runSource(name, fn)),
  ];

  // Each runSource has its own 30s timeout, so allSettled will resolve
  // within ~30s even if APIs hang. Global timeout is a safety net.
  const results = await Promise.allSettled(allPromises);

  const sources = results.map(r => r.status === 'fulfilled' ? r.value : { status: 'failed', error: r.reason?.message });
  const totalMs = Date.now() - start;

  // ── Extract all updates/signals with source tagging ──────────────────────────
  const allUpdates = [];
  const allSignals = [];
  const allMarkers = [];
  const allAlerts  = [];

  // Track title/text seen per source for cross-source boosting
  const titleIndex = {}; // normalised title → [sourceName, ...]

  function normTitle(t) {
    return (t || '').toLowerCase().replace(/[^a-z0-9 ]/g, '').replace(/\s+/g, ' ').trim().slice(0, 80);
  }

  for (const source of sources) {
    if (source.status !== 'ok' || !source.data) continue;
    const d = source.data;

    // Updates — tag with source name; track for cross-source boosting
    for (const u of (d.updates || [])) {
      const tagged = { ...u, _sourceName: source.name };
      const nt = normTitle(u.title || u.headline || u.text);
      if (nt) {
        if (!titleIndex[nt]) titleIndex[nt] = [];
        titleIndex[nt].push(source.name);
      }
      allUpdates.push(tagged);
    }

    for (const s of (d.signals || [])) allSignals.push({ ...s, _sourceName: source.name });
    for (const m of (d.markers || [])) allMarkers.push(m);
    for (const a of (d.alerts  || [])) allAlerts.push(a);
  }

  // ── Fuzzy deduplication — drop exact/near-duplicate titles (keep highest priority) ──
  const PRIORITY_RANK = { critical: 4, high: 3, medium: 2, normal: 1, low: 0 };
  function prank(item) { return PRIORITY_RANK[(item.priority || 'normal').toLowerCase()] ?? 1; }

  // For updates: group by normalised title; keep highest-priority version, tag confirmCount
  const updateGroups = {};
  for (const u of allUpdates) {
    const nt = normTitle(u.title || u.headline || u.text);
    if (!updateGroups[nt]) { updateGroups[nt] = u; continue; }
    const existing = updateGroups[nt];
    // Keep higher-priority; if equal keep first; always add confirmation count
    if (prank(u) > prank(existing)) updateGroups[nt] = u;
    updateGroups[nt]._confirmedBy = (updateGroups[nt]._confirmedBy || 1) + 1;
  }

  // Cross-source boost: if title seen from 2+ independent sources → elevate priority
  const deduped = Object.values(updateGroups).map(u => {
    const nt = normTitle(u.title || u.headline || u.text);
    const srcCount = titleIndex[nt]?.length || 1;
    const confirmed = u._confirmedBy || srcCount;
    if (confirmed >= 3 && (!u.priority || u.priority === 'normal' || u.priority === 'medium')) {
      return { ...u, priority: 'high', _crossSourceConfirmed: confirmed };
    }
    if (confirmed >= 2 && (!u.priority || u.priority === 'normal')) {
      return { ...u, priority: 'medium', _crossSourceConfirmed: confirmed };
    }
    return u;
  });

  // Sort: critical → high → medium → normal → low; then by timestamp desc
  function sortByPriority(items) {
    return items.sort((a, b) => {
      const pd = prank(b) - prank(a);
      if (pd !== 0) return pd;
      const ta = a.timestamp || a.pubDate || 0;
      const tb = b.timestamp || b.pubDate || 0;
      return (new Date(tb) - new Date(ta)) || 0;
    });
  }

  const sortedUpdates  = sortByPriority(deduped);
  const sortedSignals  = sortByPriority([...allSignals]);
  const sortedAlerts   = sortByPriority([...allAlerts]);

  const confirmedCount = sortedUpdates.filter(u => u._crossSourceConfirmed >= 2).length;

  // R-F34: count by promoted status. 'ok' = full success, 'partial' =
  // some sub-sources failed but data flowed, 'error'/'timeout' = nothing
  // useful came back, 'suspended' = pruner short-circuited the call.
  // sourcesOk now means truly-OK; partial/failed are separate buckets.
  const okCount       = sources.filter(s => s.status === 'ok').length;
  const partialCount  = sources.filter(s => s.status === 'partial').length;
  const failedCount   = sources.filter(s => s.status === 'error' || s.status === 'timeout' || s.status === 'failed').length;

  const output = {
    crucix: {
      version:        '2.1.0',
      timestamp:       new Date().toISOString(),
      totalDurationMs: totalMs,
      sourcesQueried:  sources.length,
      sourcesOk:       okCount,
      sourcesPartial:  partialCount,
      sourcesFailed:   failedCount,
    },
    sources: Object.fromEntries(
      // Include partial sources too — their data is still usable, just
      // incomplete. Filtering them out would silently drop everything
      // DefenseNews/CyberThreats/etc. surfaced when even ONE sub-source
      // failed.
      sources.filter(s => s.status === 'ok' || s.status === 'partial').map(s => [s.name, s.data])
    ),
    errors: sources
      .filter(s => s.status !== 'ok' && s.status !== 'suspended')
      .map(s => {
        const entry = { name: s.name, status: s.status, error: s.error };
        if (s.subStatus) entry.subStatus = s.subStatus;
        return entry;
      }),
    timing: Object.fromEntries(
      sources.map(s => [s.name, { status: s.status, ms: s.durationMs }])
    ),
    // Dashboard-ready aggregated data — full quality-sorted, deduped sets
    dashboard: {
      updates: sortedUpdates.slice(0, 200),    // Quality-sorted, deduped (was 50)
      signals: sortedSignals.slice(0, 100),    // Full signal set (was 20)
      markers: allMarkers.slice(0, 300),       // More map markers (was 100)
      alerts:  sortedAlerts.slice(0, 100),     // Full alert set (was 30)
      counts: {
        totalUpdates:     allUpdates.length,
        dedupedUpdates:   sortedUpdates.length,
        totalSignals:     allSignals.length,
        totalMarkers:     allMarkers.length,
        totalAlerts:      allAlerts.length,
        crossConfirmed:   confirmedCount,
      }
    }
  };

  // Name the failing / suspended / partial sources. R-F34 added the partial
  // bucket so that aggregator sources (DefenseNews, CyberThreats, etc.)
  // whose sub-sources flap are visible at a glance — previously they all
  // counted as "ok" and operators had to grep sub-source warnings to spot
  // degradation.
  const erroredNames = sources
    .filter(s => s.status === 'error' || s.status === 'timeout' || s.status === 'failed')
    .map(s => `${s.name || 'unknown'}${s.error ? `(${String(s.error).slice(0, 60)})` : ''}`);
  const suspendedNames = sources
    .filter(s => s.status === 'suspended')
    .map(s => s.name || 'unknown');
  const partialNames = sources
    .filter(s => s.status === 'partial')
    .map(s => {
      if (s.subStatus) {
        const failed = (s.subStatus.failed || []).slice(0, 3).join(',');
        return `${s.name}(${s.subStatus.ok}/${s.subStatus.total}${failed ? ` failed:${failed}` : ''})`;
      }
      return s.name;
    });
  const parts = [];
  if (erroredNames.length)   parts.push(` · failed: ${erroredNames.join(', ')}`);
  if (partialNames.length)   parts.push(` · partial: ${partialNames.join(', ')}`);
  if (suspendedNames.length) parts.push(` · suspended: ${suspendedNames.join(', ')}`);
  const partialFrag = partialCount ? ` (${partialCount} partial)` : '';
  console.error(`[Crucix] Sweep complete in ${totalMs}ms — ${okCount}/${sources.length} sources fully OK${partialFrag}${parts.join('')}`);
  console.error(`[Crucix] Dashboard: ${sortedUpdates.length} deduped updates (${confirmedCount} cross-confirmed), ${sortedSignals.length} signals`);
  return output;
}

// ── Brain signal bridge ──────────────────────────────────────────────────────
// After a sweep, push top procurement/defence signals into the Python brain queue.
// The brain reads from crucix:brain:incoming_signals and generates ML leads.

const BRAIN_SIGNAL_KEY   = 'crucix:brain:incoming_signals';
const BRAIN_SIGNAL_LIMIT = 30; // max signals pushed per sweep

// Source names that carry defence/procurement intelligence
const BRAIN_SOURCE_WHITELIST = new Set([
  'ProcurementTenders', 'ProcurementPortals', 'DefenseEvents', 'Defense News',
  'SIPRI Arms', 'ExportControlIntel', 'Lusophone', 'AfDB', 'ACLED',
  'ReliefWeb', 'Arkumurus', 'CounterpartyRisk', 'ExportControls',
  'Sanctions', 'OpenSanctions', 'OFAC', 'EuDualUse', 'CyberThreats',
]);

// Map a raw signal/update from a source to the brain signal schema
function tobrainSignal(item, sourceName) {
  return {
    title:        item.title || item.headline || item.text || '',
    content:      item.summary || item.description || item.text || item.title || '',
    source:       sourceName,
    url:          item.url || item.link || item.portalUrl || '',
    market:       item.market || item.country || item.region || 'unknown',
    published_at: item.timestamp || item.pubDate || item.date || new Date().toISOString(),
    keywords:     item.keywords || item.tags || [],
    urgency:      item.urgency || item.priority || 'MEDIUM',
  };
}

export async function pushSignalsToBrain(sweepOutput) {
  try {
    const signals = [];
    const sources = sweepOutput?.sources || {};

    for (const [sourceName, sourceData] of Object.entries(sources)) {
      if (!BRAIN_SOURCE_WHITELIST.has(sourceName)) continue;
      if (!sourceData) continue;

      // Collect updates + signals from this source
      const items = [
        ...(sourceData.updates || []),
        ...(sourceData.signals || []),
        ...(sourceData.tenders || []),
        ...(sourceData.events  || []),
      ];

      for (const item of items.slice(0, 5)) { // max 5 per source
        const s = tobrainSignal(item, sourceName);
        if (s.title || s.content) signals.push(s);
        if (signals.length >= BRAIN_SIGNAL_LIMIT) break;
      }
      if (signals.length >= BRAIN_SIGNAL_LIMIT) break;
    }

    if (signals.length === 0) return;

    // Push all signals to the brain queue (fire-and-forget)
    await Promise.all(signals.map(s => redisPush(BRAIN_SIGNAL_KEY, s)));
    console.error(`[Crucix Brain] Pushed ${signals.length} signals to brain queue`);
  } catch (e) {
    // Non-fatal — brain integration should never break the sweep
    console.error('[Crucix Brain] Signal push failed (non-fatal):', e.message);
  }
}

// Run and output when executed directly
const entryHref = process.argv[1] ? pathToFileURL(process.argv[1]).href : null;

if (entryHref && import.meta.url === entryHref) {
  const data = await fullBriefing();
  // Also push signals to brain when run from CLI
  pushSignalsToBrain(data).catch(() => {});
  console.log(JSON.stringify(data, null, 2));
}
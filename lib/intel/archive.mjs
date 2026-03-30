// lib/intel/archive.mjs
// Historical run archive — 30-day rolling store + 7-day trend detection
// Enables: "what changed this week", backtesting, slow-burn escalation detection

import { readFileSync, writeFileSync, existsSync, mkdirSync, readdirSync, unlinkSync } from 'fs';
import { join } from 'path';

const ARCHIVE_DIR     = join(process.cwd(), 'runs', 'archive');
const MAX_DAYS        = 30;
const TREND_WINDOW    = 7; // days for trend analysis

function ensureDir() {
  if (!existsSync(ARCHIVE_DIR)) mkdirSync(ARCHIVE_DIR, { recursive: true });
}

// Save a sweep run to the archive
export function archiveRun(data) {
  ensureDir();
  const ts   = new Date().toISOString().replace(/[:.]/g, '-').substring(0, 19);
  const file = join(ARCHIVE_DIR, `run_${ts}.json`);

  // Store a lightweight summary (not the full raw data — saves disk space)
  const summary = {
    timestamp:    new Date().toISOString(),
    meta:         data.meta || {},
    signalCount:  data.tg?.urgent?.length || 0,
    newsCount:    data.newsFeed?.length   || 0,
    ideasCount:   data.ideas?.length      || 0,
    direction:    data.delta?.summary?.direction || 'unknown',
    totalChanges: data.delta?.summary?.totalChanges || 0,
    critChanges:  data.delta?.summary?.criticalChanges || 0,
    vix:          data.fred?.find(f => f.id === 'VIXCLS')?.value || null,
    wti:          data.energy?.wti   || null,
    brent:        data.energy?.brent || null,
    topSignals:   (data.tg?.urgent || []).slice(0, 5).map(s => ({
      channel: s.channel || '',
      text:    (s.text   || '').substring(0, 100),
      views:   s.views   || 0,
    })),
    topIdeas:     (data.ideas || []).slice(0, 3).map(i => ({
      title: i.title || '',
      type:  i.type  || '',
    })),
  };

  try {
    writeFileSync(file, JSON.stringify(summary, null, 2), 'utf8');
    pruneOldRuns();
    return file;
  } catch (e) {
    console.error('[Archive] Write error:', e.message);
    return null;
  }
}

// Remove runs older than MAX_DAYS
function pruneOldRuns() {
  try {
    const cutoff = Date.now() - MAX_DAYS * 24 * 60 * 60 * 1000;
    const files  = readdirSync(ARCHIVE_DIR).filter(f => f.startsWith('run_'));
    for (const f of files) {
      const path = join(ARCHIVE_DIR, f);
      const stat = { mtime: new Date(f.replace('run_', '').replace(/-/g, ':').replace('T', 'T')) };
      try {
        if (new Date(f.substring(4, 23).replace(/-/g, (m, o) => o > 10 ? ':' : '-')) < new Date(cutoff)) {
          unlinkSync(path);
        }
      } catch {}
    }
  } catch {}
}

// Load all archived runs
export function loadArchive(days = 30) {
  ensureDir();
  try {
    const cutoff = Date.now() - days * 24 * 60 * 60 * 1000;
    const files  = readdirSync(ARCHIVE_DIR)
      .filter(f => f.startsWith('run_'))
      .sort()
      .reverse();

    const runs = [];
    for (const f of files) {
      try {
        const data = JSON.parse(readFileSync(join(ARCHIVE_DIR, f), 'utf8'));
        if (new Date(data.timestamp).getTime() > cutoff) runs.push(data);
      } catch {}
    }
    return runs;
  } catch { return []; }
}

// 7-day trend analysis
export function analyzeTrends() {
  const runs = loadArchive(TREND_WINDOW);
  if (runs.length < 2) return null;

  const sorted = runs.sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
  const recent  = sorted.slice(-Math.ceil(sorted.length / 2));   // second half
  const earlier = sorted.slice(0, Math.floor(sorted.length / 2)); // first half

  const avg = (arr, key) => arr.length ? arr.reduce((s, r) => s + (r[key] || 0), 0) / arr.length : 0;

  const trends = {
    period:     `${TREND_WINDOW} days`,
    dataPoints: runs.length,
    signals: {
      recent:  Math.round(avg(recent,  'signalCount')),
      earlier: Math.round(avg(earlier, 'signalCount')),
      trend:   trendDir(avg(earlier, 'signalCount'), avg(recent, 'signalCount')),
    },
    vix: {
      recent:  avg(recent.filter(r => r.vix),  'vix').toFixed(2),
      earlier: avg(earlier.filter(r => r.vix), 'vix').toFixed(2),
      trend:   trendDir(avg(earlier, 'vix'), avg(recent, 'vix')),
    },
    wti: {
      recent:  avg(recent.filter(r => r.wti),  'wti').toFixed(2),
      earlier: avg(earlier.filter(r => r.wti), 'wti').toFixed(2),
      trend:   trendDir(avg(earlier, 'wti'), avg(recent, 'wti')),
    },
    criticalChanges: {
      recent:  Math.round(avg(recent,  'critChanges')),
      earlier: Math.round(avg(earlier, 'critChanges')),
      trend:   trendDir(avg(earlier, 'critChanges'), avg(recent, 'critChanges')),
    },
    directions: countDirections(runs),
    generatedAt: new Date().toISOString(),
  };

  return trends;
}

function trendDir(earlier, recent) {
  const pct = earlier > 0 ? ((recent - earlier) / earlier) * 100 : 0;
  if (pct > 15)  return { direction: 'rising',  pct: Math.round(pct) };
  if (pct < -15) return { direction: 'falling', pct: Math.round(Math.abs(pct)) };
  return { direction: 'stable', pct: Math.round(Math.abs(pct)) };
}

function countDirections(runs) {
  const counts = { 'risk-on': 0, 'risk-off': 0, 'mixed': 0, 'unknown': 0 };
  for (const r of runs) counts[r.direction || 'unknown']++;
  return counts;
}

// Format trends for Telegram /trends command
export function formatTrendsForTelegram(trends) {
  if (!trends) return 'Insufficient data for trend analysis (need 48h+ of runs).';

  const dirEmoji = { rising: '📈', falling: '📉', stable: '➡️' };

  let msg = `*7-DAY TREND ANALYSIS*\n`;
  msg += `_Based on ${trends.dataPoints} sweep runs_\n\n`;

  msg += `📡 *OSINT Signals*\n`;
  msg += `${dirEmoji[trends.signals.trend.direction]} ${trends.signals.recent} avg/sweep `;
  msg += `(was ${trends.signals.earlier}, ${trends.signals.trend.direction} ${trends.signals.trend.pct}%)\n\n`;

  msg += `📊 *Markets*\n`;
  msg += `VIX: ${trends.vix.recent} ${dirEmoji[trends.vix.trend.direction]}\n`;
  msg += `WTI: $${trends.wti.recent} ${dirEmoji[trends.wti.trend.direction]}\n\n`;

  msg += `⚠️ *Critical changes*\n`;
  msg += `${dirEmoji[trends.criticalChanges.trend.direction]} ${trends.criticalChanges.recent} avg/sweep\n\n`;

  const dirs = trends.directions;
  const total = Object.values(dirs).reduce((s, n) => s + n, 0);
  msg += `🧭 *Direction distribution (${trends.period})*\n`;
  msg += `Risk-on: ${pct(dirs['risk-on'], total)}% | `;
  msg += `Risk-off: ${pct(dirs['risk-off'], total)}% | `;
  msg += `Mixed: ${pct(dirs['mixed'], total)}%\n`;

  return msg;
}

function pct(n, total) { return total > 0 ? Math.round((n / total) * 100) : 0; }

// ── Entity Trajectory Tracking ────────────────────────────────────────────────
// Extracts named entities (countries, organizations) from sweep data,
// stores mention counts per run, and computes multi-day risk trends.

const TRACKED_COUNTRIES = [
  'Russia', 'Ukraine', 'China', 'Iran', 'Israel', 'Gaza', 'Taiwan',
  'North Korea', 'Syria', 'Yemen', 'Sudan', 'Ethiopia', 'Somalia',
  'Venezuela', 'Cuba', 'Myanmar', 'Pakistan', 'Afghanistan',
  'Angola', 'Mozambique', 'Guinea-Bissau', 'Cape Verde', 'Timor-Leste',
  'Saudi Arabia', 'Iraq', 'Libya', 'Niger', 'Mali', 'Burkina Faso',
];

const TRACKED_ORGS = [
  'NATO', 'UN Security Council', 'IAEA', 'OPEC', 'IMF', 'World Bank',
  'Wagner Group', 'Hamas', 'Hezbollah', 'Houthi', 'ISIS', 'Al-Qaeda',
  'Lockheed Martin', 'Northrop Grumman', 'Raytheon', 'Boeing', 'BAE Systems',
  'Gazprom', 'Rosneft', 'Sinopec', 'CNOOC', 'Saudi Aramco',
];

function extractEntityMentions(data) {
  // Collect all text from sweep signals and updates
  const texts = [];
  if (data.tg?.urgent)       texts.push(...data.tg.urgent.map(s => s.text || s));
  if (data.newsFeed)         texts.push(...data.newsFeed.map(n => (n.headline || n.title || '') + ' ' + (n.summary || '')));
  if (data.correlations)     texts.push(...data.correlations.map(c => c.region + ' ' + (c.signals || []).join(' ')));
  if (data.delta?.changes)   texts.push(...data.delta.changes.map(c => c.label || ''));
  const combined = texts.join(' ');

  const mentions = {};
  for (const entity of [...TRACKED_COUNTRIES, ...TRACKED_ORGS]) {
    const count = (combined.match(new RegExp(entity.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi')) || []).length;
    if (count > 0) mentions[entity] = count;
  }
  return mentions;
}

// Extend archiveRun to also store entity mentions
export function archiveRunWithEntities(data) {
  ensureDir();
  const ts   = new Date().toISOString().replace(/[:.]/g, '-').substring(0, 19);
  const file = join(ARCHIVE_DIR, `run_${ts}.json`);

  const summary = {
    timestamp:    new Date().toISOString(),
    meta:         data.meta || {},
    signalCount:  data.tg?.urgent?.length || 0,
    newsCount:    data.newsFeed?.length   || 0,
    ideasCount:   data.ideas?.length      || 0,
    direction:    data.delta?.summary?.direction || 'unknown',
    totalChanges: data.delta?.summary?.totalChanges || 0,
    critChanges:  data.delta?.summary?.criticalChanges || 0,
    vix:          data.fred?.find(f => f.id === 'VIXCLS')?.value || null,
    wti:          data.energy?.wti   || null,
    brent:        data.energy?.brent || null,
    topSignals:   (data.tg?.urgent || []).slice(0, 5).map(s => ({ channel: s.channel || '', text: (s.text || '').substring(0, 100), views: s.views || 0 })),
    topIdeas:     (data.ideas || []).slice(0, 3).map(i => ({ title: i.title || '', type: i.type || '' })),
    entityMentions: extractEntityMentions(data),
  };

  try {
    writeFileSync(file, JSON.stringify(summary, null, 2), 'utf8');
    pruneOldRuns();
    return file;
  } catch (e) {
    console.error('[Archive] Write error:', e.message);
    return null;
  }
}

// Compute entity trajectory — how mention counts changed over time
export function analyzeEntityTrajectory(days = 14) {
  const runs = loadArchive(days);
  if (runs.length < 3) return { entities: [], dataPoints: runs.length, period: `${days}d`, note: 'Need 3+ runs for trajectory analysis' };

  const sorted = runs.sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
  const half   = Math.floor(sorted.length / 2);
  const earlier = sorted.slice(0, half);
  const recent  = sorted.slice(half);

  const avgMentions = (runSet, entity) => {
    const vals = runSet.map(r => (r.entityMentions || {})[entity] || 0);
    return vals.reduce((s, v) => s + v, 0) / (vals.length || 1);
  };

  const allEntities = new Set();
  for (const run of runs) {
    for (const e of Object.keys(run.entityMentions || {})) allEntities.add(e);
  }

  const trajectories = [];
  for (const entity of allEntities) {
    const earlyAvg  = avgMentions(earlier, entity);
    const recentAvg = avgMentions(recent,  entity);
    const pctChange = earlyAvg > 0 ? ((recentAvg - earlyAvg) / earlyAvg) * 100 : recentAvg > 0 ? 100 : 0;
    const totalMentions = runs.reduce((s, r) => s + ((r.entityMentions || {})[entity] || 0), 0);

    if (totalMentions < 2) continue; // Ignore noise

    trajectories.push({
      entity,
      recentAvg: Math.round(recentAvg * 10) / 10,
      earlyAvg:  Math.round(earlyAvg * 10) / 10,
      pctChange: Math.round(pctChange),
      trend:     pctChange > 25 ? 'rising' : pctChange < -25 ? 'falling' : 'stable',
      totalMentions,
      signal:    pctChange > 50  ? `${entity} intelligence volume UP ${Math.round(pctChange)}% over ${days}d — escalation signal` :
                 pctChange < -50 ? `${entity} intelligence volume DOWN ${Math.round(Math.abs(pctChange))}% over ${days}d — deescalation` :
                 null,
    });
  }

  // Sort by absolute change magnitude
  trajectories.sort((a, b) => Math.abs(b.pctChange) - Math.abs(a.pctChange));

  return {
    entities:   trajectories.slice(0, 20),
    rising:     trajectories.filter(e => e.trend === 'rising').slice(0, 5),
    falling:    trajectories.filter(e => e.trend === 'falling').slice(0, 5),
    dataPoints: runs.length,
    period:     `${days}d`,
    generatedAt: new Date().toISOString(),
  };
}

// Format entity trajectory for Telegram
export function formatEntityTrajectoryForTelegram(trajectory) {
  if (!trajectory || trajectory.dataPoints < 3) {
    return `Entity trajectory requires 3+ sweep runs (have ${trajectory?.dataPoints || 0}).`;
  }

  let msg = `*ENTITY TRAJECTORY (${trajectory.period})*\n`;
  msg += `_${trajectory.dataPoints} sweep runs analyzed_\n\n`;

  if (trajectory.rising.length > 0) {
    msg += `📈 *RISING ENTITIES*\n`;
    for (const e of trajectory.rising) {
      msg += `• ${e.entity}: +${e.pctChange}% mentions\n`;
    }
    msg += '\n';
  }

  if (trajectory.falling.length > 0) {
    msg += `📉 *FALLING ENTITIES*\n`;
    for (const e of trajectory.falling) {
      msg += `• ${e.entity}: ${e.pctChange}% mentions\n`;
    }
    msg += '\n';
  }

  const topByVolume = [...trajectory.entities].sort((a, b) => b.totalMentions - a.totalMentions).slice(0, 5);
  if (topByVolume.length > 0) {
    msg += `🔊 *MOST MENTIONED*\n`;
    for (const e of topByVolume) {
      const dir = e.trend === 'rising' ? '↑' : e.trend === 'falling' ? '↓' : '→';
      msg += `• ${e.entity}: ${e.totalMentions} total ${dir}\n`;
    }
  }

  return msg;
}

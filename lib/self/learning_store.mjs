// lib/self/learning_store.mjs
// Persistent learning database — tracks alert outcomes, source quality, confidence weights
// Enables signal-quality improvement over time without code changes
// Storage: runs/learning/*.json  (atomic writes, crash-safe) + Upstash Redis backup

import { readFileSync, writeFileSync, existsSync, mkdirSync, renameSync, unlinkSync } from 'fs';
import { join } from 'path';
import { redisGet, redisSet } from '../persist/store.mjs';

const LEARNING_DIR = join(process.cwd(), 'runs', 'learning');

function ensureDir() {
  if (!existsSync(LEARNING_DIR)) mkdirSync(LEARNING_DIR, { recursive: true });
}

function atomicWrite(path, data) {
  ensureDir();
  const tmp = path + '.tmp';
  try {
    writeFileSync(tmp, JSON.stringify(data, null, 2), 'utf8');
    renameSync(tmp, path);
  } catch (err) {
    try { unlinkSync(tmp); } catch {}
    throw err;
  }
}

function load(filename, fallback) {
  const path = join(LEARNING_DIR, filename);
  try {
    return JSON.parse(readFileSync(path, 'utf8'));
  } catch {
    return fallback;
  }
}

function save(filename, data) {
  atomicWrite(join(LEARNING_DIR, filename), data);
  redisSet(`crucix:learning:${filename.replace('.json', '')}`, data).catch(() => {});
}

// ── Startup restore from Redis ────────────────────────────────────────────────
// Call once on server start — restores any learning files lost to Render's
// ephemeral filesystem from Upstash Redis backup.
export async function initLearningStore() {
  ensureDir();
  const files = {
    'outcomes.json':          [],
    'weights.json':           { bySource: {}, byRegion: {}, byTier: {} },
    'source_history.json':    {},
    'patterns.json':          { patterns: [] },
    'opportunities.json':     { opportunities: [] },
    'explorer_findings.json': { findings: [] },
    'update_log.json':        [],
  };
  for (const [filename, fallback] of Object.entries(files)) {
    const path = join(LEARNING_DIR, filename);
    if (!existsSync(path)) {
      const remote = await redisGet(`crucix:learning:${filename.replace('.json', '')}`);
      if (remote !== null) {
        atomicWrite(path, remote);
        console.log(`[Persist] Restored learning/${filename} from Redis`);
      }
    }
  }
}

// ── Alert Outcome Tracking ────────────────────────────────────────────────────
// Outcomes let the system learn which signal patterns are reliable vs noisy

export function recordAlertOutcome(alertHash, alertText, outcome, metadata = {}) {
  // outcome: 'confirmed' | 'dismissed' | 'pending'
  const outcomes = load('outcomes.json', []);

  const idx = outcomes.findIndex(o => o.hash === alertHash);
  const entry = {
    hash: alertHash,
    text: (alertText || '').substring(0, 200),
    outcome,
    timestamp: metadata.timestamp || new Date().toISOString(),
    source: metadata.source || null,
    region: metadata.region || null,
    tier: metadata.tier || null,
  };

  if (idx >= 0) {
    outcomes[idx] = { ...outcomes[idx], ...entry, updatedAt: new Date().toISOString() };
  } else {
    outcomes.unshift(entry);
  }

  save('outcomes.json', outcomes.slice(0, 1000));

  // Adjust confidence weights immediately on confirmed/dismissed
  if (outcome === 'confirmed' || outcome === 'dismissed') {
    _adjustWeights(entry);
  }

  return entry;
}

export function getOutcomes(limit = 50) {
  return load('outcomes.json', []).slice(0, limit);
}

export function getPendingOutcomes() {
  const windowMs = 7 * 24 * 60 * 60 * 1000;
  return load('outcomes.json', []).filter(o =>
    o.outcome === 'pending' &&
    (Date.now() - new Date(o.timestamp).getTime()) < windowMs
  );
}

export function getOutcomeStats() {
  const outcomes = load('outcomes.json', []);
  const resolved = outcomes.filter(o => o.outcome !== 'pending');
  const confirmed = resolved.filter(o => o.outcome === 'confirmed').length;
  const dismissed = resolved.filter(o => o.outcome === 'dismissed').length;
  return {
    total: outcomes.length,
    pending: outcomes.filter(o => o.outcome === 'pending').length,
    confirmed,
    dismissed,
    accuracy: resolved.length > 0 ? Math.round((confirmed / resolved.length) * 100) : null,
  };
}

// ── Confidence Weights ────────────────────────────────────────────────────────
// Asymmetric update: false alarms penalised harder than confirmations rewarded

function _adjustWeights(entry) {
  const weights = load('weights.json', { bySource: {}, byRegion: {}, byTier: {} });
  const delta = entry.outcome === 'confirmed' ? +0.05 : -0.10;

  if (entry.source) {
    const cur = weights.bySource[entry.source] ?? 1.0;
    weights.bySource[entry.source] = Math.max(0.1, Math.min(2.0, cur + delta));
  }
  if (entry.region) {
    const cur = weights.byRegion[entry.region] ?? 1.0;
    weights.byRegion[entry.region] = Math.max(0.1, Math.min(2.0, cur + delta));
  }
  if (entry.tier) {
    const cur = weights.byTier[entry.tier] ?? 1.0;
    weights.byTier[entry.tier] = Math.max(0.1, Math.min(2.0, cur + delta));
  }

  save('weights.json', weights);
}

export function getWeights() {
  return load('weights.json', { bySource: {}, byRegion: {}, byTier: {} });
}

export function getSourceWeight(sourceName) {
  return (load('weights.json', { bySource: {} })).bySource[sourceName] ?? 1.0;
}

// ── Source History (Persistent Across Restarts) ───────────────────────────────
// Complements server.mjs in-memory sourceHealth with disk persistence

export function recordSourceSweep(sourceName, status, durationMs) {
  const history = load('source_history.json', {});
  if (!history[sourceName]) history[sourceName] = { sweeps: [], totalOk: 0, totalFail: 0 };

  history[sourceName].sweeps.unshift({ ts: Date.now(), ok: status === 'ok', ms: durationMs || 0 });
  history[sourceName].sweeps = history[sourceName].sweeps.slice(0, 96); // ~24h at 15min intervals

  if (status === 'ok') history[sourceName].totalOk++;
  else history[sourceName].totalFail++;

  save('source_history.json', history);
}

export function getSourceHistory() {
  const history = load('source_history.json', {});
  return Object.entries(history).map(([name, h]) => {
    const recent = h.sweeps.slice(0, 48); // last 12h
    const recentOk = recent.filter(s => s.ok).length;
    const reliability = recent.length > 0 ? Math.round((recentOk / recent.length) * 100) : null;
    const okSweeps = recent.filter(s => s.ok && s.ms > 0);
    const avgMs = okSweeps.length > 0
      ? Math.round(okSweeps.reduce((sum, s) => sum + s.ms, 0) / okSweeps.length)
      : 0;

    return {
      name,
      reliability,
      totalOk: h.totalOk,
      totalFail: h.totalFail,
      avgMs,
      lastOk: h.sweeps.find(s => s.ok)?.ts || null,
      status: reliability === null ? 'unknown'
        : reliability >= 80 ? 'healthy'
        : reliability >= 50 ? 'degraded'
        : 'critical',
    };
  }).sort((a, b) => (a.reliability ?? 100) - (b.reliability ?? 100));
}

export function getSourcesToReview() {
  return getSourceHistory().filter(s => s.status === 'critical' || s.status === 'degraded');
}

// ── Self-Update Log ────────────────────────────────────────────────────────────

export function logSelfUpdate(action, details) {
  const log = load('update_log.json', []);
  log.unshift({
    timestamp: new Date().toISOString(),
    action,
    details: typeof details === 'object' ? details : { message: String(details) },
    status: 'done',
  });
  save('update_log.json', log.slice(0, 200));
}

export function getUpdateLog(limit = 20) {
  return load('update_log.json', []).slice(0, limit);
}

// ── Pattern Library ────────────────────────────────────────────────────────────

export function savePatterns(patterns) {
  save('patterns.json', { updatedAt: new Date().toISOString(), patterns });
}

export function getPatterns() {
  return load('patterns.json', { patterns: [] });
}

// ── Opportunity Pipeline ───────────────────────────────────────────────────────

export function saveOpportunities(opportunities) {
  save('opportunities.json', { updatedAt: new Date().toISOString(), opportunities });
}

export function getOpportunities() {
  return load('opportunities.json', { opportunities: [] });
}

// ── Web Explorer Findings ─────────────────────────────────────────────────────

export function saveExplorerFindings(findings) {
  save('explorer_findings.json', { updatedAt: new Date().toISOString(), findings });
}

export function getExplorerFindings() {
  return load('explorer_findings.json', { findings: [] });
}

// ── Auto-Classification of Pending Outcomes ───────────────────────────────────
// Classifies pending signals automatically — no human required:
//   • 2+ fresh candidates matching the same text → auto-confirm (cross-source)
//   • Older than 7 days with no match → auto-dismiss (expired noise)
export function autoClassifyOutcomes(candidates = []) {
  const outcomes = load('outcomes.json', []);
  let classified = 0;

  for (const p of outcomes) {
    if (p.outcome !== 'pending') continue;
    const ageMs = Date.now() - new Date(p.timestamp).getTime();
    const pKey  = p.text.toLowerCase().substring(0, 80);

    // Cross-source confirmation: ≥2 live candidates contain the same text fragment
    const matches = candidates.filter(c => {
      const cKey = (c.text || '').toLowerCase().substring(0, 80);
      return cKey && pKey && (cKey.includes(pKey.substring(0, 50)) || pKey.includes(cKey.substring(0, 50)));
    });

    if (matches.length >= 2) {
      recordAlertOutcome(p.hash, p.text, 'confirmed', { source: p.source, region: p.region, tier: p.tier });
      classified++;
    } else if (ageMs > 7 * 24 * 60 * 60 * 1000) {
      // No confirmation in 7 days — treat as noise / unverifiable
      recordAlertOutcome(p.hash, p.text, 'dismissed', { source: p.source, region: p.region, tier: p.tier });
      classified++;
    }
  }

  return classified;
}

// ── Adaptive Scoring Weights (outcome-driven) ─────────────────────────────────
// Computes per-region accuracy multipliers from historical outcomes.
// Activates after just 5 resolved outcomes to start learning early.
export function getAdaptiveScoringWeights() {
  const outcomes = load('outcomes.json', []);
  const resolved = outcomes.filter(o => o.outcome !== 'pending');
  if (resolved.length < 5) return null;

  const byRegion = {};
  for (const o of resolved) {
    if (!o.region) continue;
    if (!byRegion[o.region]) byRegion[o.region] = { confirmed: 0, total: 0 };
    byRegion[o.region].total++;
    if (o.outcome === 'confirmed') byRegion[o.region].confirmed++;
  }

  const regionMultipliers = {};
  for (const [region, stats] of Object.entries(byRegion)) {
    if (stats.total < 2) continue;  // need at least 2 data points per region
    const accuracy = stats.confirmed / stats.total;
    // Wider range: 0.2× (0% accurate) → 2.5× (100% accurate)
    // Regions with proven wins get strongly boosted; noise regions get suppressed
    regionMultipliers[region] = parseFloat(Math.max(0.2, Math.min(2.5, 0.2 + accuracy * 2.3)).toFixed(3));
  }

  return { regionMultipliers, computedAt: new Date().toISOString(), sampleSize: resolved.length };
}

// ── Data Pruning ──────────────────────────────────────────────────────────────
export function pruneOldData() {
  const outcomes = load('outcomes.json', []);
  if (outcomes.length > 1000) save('outcomes.json', outcomes.slice(0, 1000));
  const log = load('update_log.json', []);
  if (log.length > 200) save('update_log.json', log.slice(0, 200));
}

// ── Learning Summary ──────────────────────────────────────────────────────────

export function getLearningStats() {
  const outcomes = getOutcomeStats();
  const sources = getSourceHistory();
  const { patterns } = getPatterns();
  const { opportunities } = getOpportunities();
  const log = getUpdateLog(5);

  return {
    outcomes,
    sources: {
      total: sources.length,
      healthy: sources.filter(s => s.status === 'healthy').length,
      degraded: sources.filter(s => s.status === 'degraded').length,
      critical: sources.filter(s => s.status === 'critical').length,
    },
    patternCount: patterns?.length ?? 0,
    opportunityCount: opportunities?.length ?? 0,
    recentUpdates: log,
  };
}

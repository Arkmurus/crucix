// lib/self/learning_store.mjs
// Persistent learning database — tracks alert outcomes, source quality, confidence weights
// Enables signal-quality improvement over time without code changes
// Storage: runs/learning/*.json  (atomic writes, crash-safe) + Upstash Redis backup

import { readFileSync, writeFileSync, existsSync, mkdirSync, renameSync, unlinkSync } from 'fs';
import { join } from 'path';
import { redisGet, redisSet } from '../persist/store.mjs';
import { errorTracker } from '../observability/errorTracker.mjs'; // R-F1796 (audit #3/#6)

// C-33 / R-F3918 — THIS STORE WAS NOT DURABLE, and R-F3917 relied on it being so.
//
// `join(process.cwd(), 'runs', 'learning')` resolves INSIDE the container image, which
// Fly replaces wholesale on every deploy. The persistent volume is mounted at /data
// (fly.web.toml). Measured on live aria-web minutes after the R-F3917 deploy:
//
//   /app/runs/learning/  -> every file stamped at the deploy minute
//   /data/               -> files from Jul 3, Jul 11, Aug 1 (genuinely persistent)
//   source_history.json  -> sweeps=1, oldest sweep AFTER the deploy: the history was WIPED
//
// So the store survived an in-container process restart but not a deploy — and deploys
// are the dominant restart cause, i.e. precisely the window C-33 exists to close.
// "Durable" had been asserted rather than measured, which is the C-29 lesson arriving
// one turn later in a new place.
//
// Follows the convention already used at four sites in this tier
// (`existsSync('/data') ? '/data' : <local>`) rather than inventing a mechanism.
// Injectable purely so the choice is testable without a mounted volume.
export function resolveLearningDir({ exists = existsSync, cwd = process.cwd } = {}) {
  try {
    if (exists('/data')) return join('/data', 'learning');
  } catch { /* fall through to the working tree */ }
  return join(cwd(), 'runs', 'learning');
}

const LEARNING_DIR = resolveLearningDir();

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

const EMA_ALPHA = 0.15; // smoothing factor — recent results dominate after ~7 polls

export function recordSourceSweep(sourceName, status, durationMs, reason = '') {
  const history = load('source_history.json', {});
  if (!history[sourceName]) {
    history[sourceName] = {
      sweeps: [], totalOk: 0, totalFail: 0,
      ema: 1.0, consecutiveFails: 0, uaRotation: false,
    };
  }
  const h = history[sourceName];
  const ok = status === 'ok';

  h.sweeps.unshift({ ts: Date.now(), ok, ms: durationMs || 0, reason: ok ? '' : (reason || status) });
  h.sweeps = h.sweeps.slice(0, 96);

  if (ok) { h.totalOk++; h.consecutiveFails = 0; }
  else { h.totalFail++; h.consecutiveFails = (h.consecutiveFails || 0) + 1; }

  // EMA update
  const score = ok ? 1.0 : (status === 'partial' ? 0.5 : 0.0);
  h.ema = Math.max(0, Math.min(1, EMA_ALPHA * score + (1 - EMA_ALPHA) * (h.ema ?? 1.0)));

  // Auto-enable UA rotation on 3+ consecutive 403s
  if (!ok && reason === '403' && h.consecutiveFails >= 3 && !h.uaRotation) {
    h.uaRotation = true;
  }

  save('source_history.json', history);

  // Feed brain on status transitions
  if (h.ema < 0.25 && h.consecutiveFails === 5) {
    brainAbsorb({
      module: 'source_reliability',
      summary: `Source DEAD: ${sourceName} — EMA ${h.ema.toFixed(2)}, ${h.consecutiveFails} consecutive failures. Reason: ${reason || status}`,
      success: false,
      extra_topics: ['osint'],
      confidence: 'CONFIRMED',
    });
  }
}

export function getSourceHistory() {
  const history = load('source_history.json', {});
  return Object.entries(history).map(([name, h]) => {
    const recent = h.sweeps.slice(0, 48);
    const recentOk = recent.filter(s => s.ok).length;
    const reliability = recent.length > 0 ? Math.round((recentOk / recent.length) * 100) : null;
    const okSweeps = recent.filter(s => s.ok && s.ms > 0);
    const avgMs = okSweeps.length > 0
      ? Math.round(okSweeps.reduce((sum, s) => sum + s.ms, 0) / okSweeps.length)
      : 0;
    const ema = h.ema ?? 1.0;

    return {
      name,
      reliability,
      ema: Math.round(ema * 100) / 100,
      totalOk: h.totalOk,
      totalFail: h.totalFail,
      consecutiveFails: h.consecutiveFails || 0,
      avgMs,
      lastOk: h.sweeps.find(s => s.ok)?.ts || null,
      uaRotation: h.uaRotation || false,
      status: reliability === null ? 'unknown'
        : ema >= 0.75 ? 'healthy'
        : ema >= 0.50 ? 'degraded'
        : ema >= 0.25 ? 'failing'
        : 'dead',
      shouldSkip: ema < 0.25,
      recommendedIntervalMin: ema >= 0.75 ? 15 : ema >= 0.50 ? 30 : ema >= 0.25 ? 60 : 240,
    };
  }).sort((a, b) => (a.ema ?? 1) - (b.ema ?? 1));
}

export function getSourcesToReview() {
  return getSourceHistory().filter(s => s.status === 'failing' || s.status === 'dead' || s.status === 'degraded');
}

export function shouldSkipSource(sourceName) {
  const history = load('source_history.json', {});
  const h = history[sourceName];
  if (!h) return false;
  return (h.ema ?? 1.0) < 0.25; // DEAD sources skipped
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

// ── Brain Hook Relay (POST to Python ARIA backend) ──────────────────────────
// Bridges Node.js seenode modules → Python brain_hook.absorb() on fly.io.
// Fire-and-forget: never throws, never blocks.
//
// Past gap (verified live 2026-04-18 23:33 via meta_query): seenode
// processed 302 emails but 0 signals reached the Python brain.
// Root cause: BRAIN_SERVICE_URL env var was never set on seenode, so
// brainAbsorb() fell through to http://localhost:5001 — which doesn't
// exist on the seenode container. Every fetch failed and was silently
// swallowed by the empty try/catch.
//
// Fix: try the actual env var seenode HAS (ARIA_SERVICE_URL = the
// fly.io brain endpoint) before falling through to localhost. Same
// for the bearer token — accept either ARIA_API_TOKEN or
// ARIA_INTERNAL_TOKEN (the brain absorb endpoint itself is currently
// unauthenticated on Python, but sending the token costs nothing and
// future-proofs against the auth being turned on).
//
// Also: log failures (not just swallow them) so the next time this
// breaks the operator sees it in seenode logs instead of having to
// run a meta_query 5 days later.
// Resolution rule (2026-04-19 fix): when running on seenode, ARIA_SERVICE_URL
// points at SEENODE ITSELF (intel.sursec.co.uk) — the public hostname is also
// the upstream proxy. POSTing brainAbsorb there hits OUR OWN proxy, which then
// re-forwards to fly. Two extra hops, two auth checks, double the failure
// surface. The one-line fix: prefer the direct fly URL via BRAIN_DIRECT_URL,
// with a hardcoded production fallback.
//
// 2026-04-19 evening: production seenode env had BRAIN_SERVICE_URL set to
// the literal placeholder `https://your-crucix-app.seenode.com` — a template
// value that was never replaced. The `||` fallback picked it (truthy string)
// and every brainAbsorb returned `fetch failed`. Filter out obvious
// placeholder URLs so a never-set env var doesn't masquerade as a real one.
function _isPlaceholderUrl(u) {
  if (!u || typeof u !== 'string') return true;
  const lower = u.toLowerCase();
  return (
    lower.includes('your-crucix-app') ||
    lower.includes('your-app') ||
    lower.includes('example.com') ||
    lower.includes('changeme') ||
    lower.includes('<') || lower.includes('>')
  );
}
function _pickUrl(...candidates) {
  for (const c of candidates) {
    if (c && !_isPlaceholderUrl(c)) return c.replace(/\/+$/, '');
  }
  return 'https://aria-intel.fly.dev';
}
const BRAIN_URL = _pickUrl(
  process.env.BRAIN_DIRECT_URL,    // explicit direct-to-fly override
  process.env.BRAIN_SERVICE_URL    // legacy: explicit brain URL
  // hardcoded fallback: 'https://aria-intel.fly.dev' (in _pickUrl)
);

const BRAIN_TOKEN = (
  process.env.ARIA_API_TOKEN ||
  process.env.ARIA_INTERNAL_TOKEN ||
  ''
);

let _brainAbsorbWarned = false;
let _brainAbsorbSuccessSeen = false;

// Per-process telemetry — surfaced via getBrainAbsorbStats() for the
// /api/brain-absorb/diag endpoint. Lets the operator see the live state
// of the bridge without grepping seenode logs.
const _brainAbsorbStats = {
  resolved_url: BRAIN_URL,
  has_token: Boolean(BRAIN_TOKEN),
  total_calls: 0,
  total_ok: 0,
  total_fail: 0,
  // R-F45: track consecutive 401s so a sustained-auth-failure state can
  // escalate from "warning per call" to "this is broken, alert loudly."
  consecutive_401s: 0,
  last_status: null,        // HTTP status code of last call (or 'timeout', 'error')
  last_error: null,         // error message if any
  last_call_at: null,       // ISO timestamp
  last_module: null,        // module name of last call
  by_module: {},            // {module: {ok, fail, last_status}}
};

export function getBrainAbsorbStats() {
  return { ..._brainAbsorbStats };
}

export async function brainAbsorb({ module, summary, detail, entity_name, success, extra_topics, source_id, confidence, user_id, sector }) {
  if (!BRAIN_URL || !summary) return;

  // One-time startup warning when BRAIN_URL still points at localhost
  // — that means neither BRAIN_SERVICE_URL nor ARIA_SERVICE_URL is set
  // and the bridge will silently 0-process every signal. Log loudly
  // ONCE so the operator sees it without spamming on every call.
  if (!_brainAbsorbWarned && BRAIN_URL.includes('localhost')) {
    _brainAbsorbWarned = true;
    console.warn(
      '[brainAbsorb] BRAIN_URL falls through to ' + BRAIN_URL +
      ' — set ARIA_SERVICE_URL (or BRAIN_SERVICE_URL) on this host ' +
      'to point at the Python brain (e.g. https://aria-intel.fly.dev) ' +
      'or every signal will silently fail.'
    );
  }

  const mod = module || 'seenode';
  _brainAbsorbStats.total_calls += 1;
  _brainAbsorbStats.last_call_at = new Date().toISOString();
  _brainAbsorbStats.last_module = mod;
  if (!_brainAbsorbStats.by_module[mod]) {
    _brainAbsorbStats.by_module[mod] = { ok: 0, fail: 0, last_status: null };
  }

  // R-F1796 (audit #6): circuit-breaker gate. When the brain is down, skip fast
  // instead of making every caller wait out the 8s timeout. After N consecutive
  // failures the breaker OPENs for a cooldown (one HALF_OPEN probe after it
  // elapses), so a wedged/offline brain can't stall the Node tier.
  if (!errorTracker.shouldAttempt('brain_absorb')) {
    _brainAbsorbStats.last_status = 'skipped_circuit_open';
    _brainAbsorbStats.by_module[mod].last_status = 'skipped_circuit_open';
    return;
  }

  try {
    const headers = { 'Content-Type': 'application/json' };
    if (BRAIN_TOKEN) headers['Authorization'] = `Bearer ${BRAIN_TOKEN}`;
    const r = await fetch(`${BRAIN_URL}/api/aria/brain/absorb`, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        module: mod,
        summary,
        detail: detail || '',
        entity_name: entity_name || '',
        success: success !== false,
        extra_topics: extra_topics || [],
        source_id: source_id || '',
        confidence: confidence || 'ASSESSED',
        // R-F56: per-customer telemetry tags. Empty strings preserve
        // legacy shape for sweep / autonomous callers; chat / DD
        // callers should populate from the authenticated user record.
        user_id: user_id || '',
        sector: sector || '',
      }),
      signal: AbortSignal.timeout(8000),
    });
    _brainAbsorbStats.last_status = r.status;
    _brainAbsorbStats.by_module[mod].last_status = r.status;
    if (!r.ok) {
      errorTracker.record('brain_absorb', `http_${r.status}`, new Error(`absorb HTTP ${r.status}`)); // R-F1796: trip breaker
      _brainAbsorbStats.total_fail += 1;
      _brainAbsorbStats.by_module[mod].fail += 1;
      const errBody = await r.text().catch(() => '');
      _brainAbsorbStats.last_error = `HTTP ${r.status}: ${errBody.slice(0, 200)}`;
      // Log per-call failures so a wrong token / wrong URL surfaces
      // immediately, not after 300 silently-dropped signals.
      console.warn(
        `[brainAbsorb] ${BRAIN_URL}/api/aria/brain/absorb returned HTTP ${r.status} ` +
        `(module=${mod}) body=${errBody.slice(0, 120)}`
      );
      // R-F45: escalate sustained 401s. The auth-token-drift between
      // fly + seenode is a recurring bug (memory session_2026_04_23).
      // After 5 consecutive 401s, log a RED ERROR with the exact env
      // var name to set so operators can act without needing to grep
      // the codebase. Reset counter on any non-401 (success / 5xx /
      // network).
      if (r.status === 401) {
        _brainAbsorbStats.consecutive_401s += 1;
        if (_brainAbsorbStats.consecutive_401s === 5 || _brainAbsorbStats.consecutive_401s % 50 === 0) {
          console.error(
            '\n' +
            '═══════════════════════════════════════════════════════════════════\n' +
            '  ❌ BRAIN BRIDGE 401 — sweep signals are being silently dropped\n' +
            '═══════════════════════════════════════════════════════════════════\n' +
            `  Last ${_brainAbsorbStats.consecutive_401s} calls to ${BRAIN_URL}/api/aria/brain/absorb returned 401.\n` +
            '  Cause: ARIA_API_TOKEN env var on this server is missing or does not\n' +
            '  match the value on the Python brain (fly.io aria-intel).\n\n' +
            '  Fix:\n' +
            '    1. flyctl secrets list -a aria-intel  (verify ARIA_API_TOKEN is set)\n' +
            '    2. Set ARIA_API_TOKEN on THIS server (seenode dashboard) to the\n' +
            '       same value as fly\'s ARIA_API_TOKEN\n' +
            '    3. Restart this process\n' +
            '    4. curl -H "Authorization: Bearer <token>" http://localhost/api/brain-absorb/diag\n' +
            '       expect: { "has_token": true, "total_ok": >0 }\n' +
            '═══════════════════════════════════════════════════════════════════\n'
          );
        }
      } else {
        _brainAbsorbStats.consecutive_401s = 0;
      }
    } else {
      errorTracker.recordSuccess('brain_absorb'); // R-F1796: reset/close breaker
      _brainAbsorbStats.total_ok += 1;
      _brainAbsorbStats.by_module[mod].ok += 1;
      _brainAbsorbStats.last_error = null;
      _brainAbsorbStats.consecutive_401s = 0;
      if (!_brainAbsorbSuccessSeen) {
        _brainAbsorbSuccessSeen = true;
        console.log(`[brainAbsorb] First signal OK to ${BRAIN_URL} — bridge live.`);
      }
    }
  } catch (e) {
    errorTracker.record('brain_absorb', 'absorb_failed', e); // R-F1796: trip breaker on timeout/network
    _brainAbsorbStats.total_fail += 1;
    _brainAbsorbStats.by_module[mod].fail += 1;
    const errStr = (e?.name === 'TimeoutError') ? 'timeout' : (e?.message || String(e));
    _brainAbsorbStats.last_status = errStr;
    _brainAbsorbStats.last_error = errStr;
    _brainAbsorbStats.by_module[mod].last_status = errStr;
    // Network/timeout failures — log message so the operator can see
    // them in seenode logs. Still fire-and-forget; never throw.
    console.warn(
      `[brainAbsorb] FAILED to reach ${BRAIN_URL}: ${errStr} (module=${mod})`
    );
  }
}


// ── Boot-time brain bridge verification (R-F45) ────────────────────────────
//
// Called once at server startup. Pings the Python brain's /api/aria/health
// endpoint with whatever token we have configured. Three outcomes:
//
//   has_token=false          → log RED ERROR + Telegram alert (if configured).
//                              Every brainAbsorb will 401 silently otherwise.
//   401 from brain           → log RED ERROR + alert. Token is set but wrong
//                              (most likely: rotated on fly without seenode
//                              update, mirror of 2026-04-23 incident).
//   2xx from brain           → log "✓ brain bridge healthy"
//   network error / 5xx      → log warning; could be transient cold-start.
//
// Returns a verdict object so server.mjs can include it in /api/health.
// `quiet` (R-F850): suppress all console output + return the verdict only.
// Used by the periodic re-check loop so a healthy brain doesn't log every 60s
// and a persistent misconfig doesn't spam. The boot one-shot stays verbose.
export async function verifyBrainBridge({ telegramAlerter, quiet = false } = {}) {
  const stamp = new Date().toISOString();
  const verdict = {
    timestamp: stamp,
    url: BRAIN_URL,
    has_token: Boolean(BRAIN_TOKEN),
    healthy: false,
    status: null,
    reason: '',
  };

  if (!BRAIN_TOKEN) {
    const msg =
      '\n' +
      '═══════════════════════════════════════════════════════════════════\n' +
      '  ❌ BRAIN BRIDGE NOT CONFIGURED — sweep → brain learning loop is dead\n' +
      '═══════════════════════════════════════════════════════════════════\n' +
      `  Target: ${BRAIN_URL}/api/aria/brain/absorb\n` +
      '  Issue:  neither ARIA_API_TOKEN nor ARIA_INTERNAL_TOKEN is set on\n' +
      '          this server, so every signal pushed to the Python brain\n' +
      '          will be rejected with HTTP 401 and silently dropped.\n\n' +
      '  Fix:\n' +
      '    1. flyctl secrets list -a aria-intel  (find ARIA_API_TOKEN)\n' +
      '    2. Set ARIA_API_TOKEN on THIS server to the same value\n' +
      '    3. Restart\n' +
      '═══════════════════════════════════════════════════════════════════\n';
    if (!quiet) console.error(msg);
    verdict.reason = 'no token';
    if (telegramAlerter?.isConfigured && typeof telegramAlerter.sendMessage === 'function') {
      telegramAlerter.sendMessage(
        '⚠️ *Brain bridge misconfigured*\n\n' +
        'Neither ARIA_API_TOKEN nor ARIA_INTERNAL_TOKEN is set on the seenode-side.\n' +
        'Every signal pushed to the Python brain is being dropped with 401.\n\n' +
        'Fix: set ARIA_API_TOKEN on seenode to match the value on fly.io aria-intel.'
      ).catch(() => {});
    }
    return verdict;
  }

  try {
    const r = await fetch(`${BRAIN_URL}/api/aria/health`, {
      method: 'GET',
      headers: { 'Authorization': `Bearer ${BRAIN_TOKEN}` },
      signal: AbortSignal.timeout(8000),
    });
    verdict.status = r.status;
    if (r.status === 401) {
      const msg =
        '\n' +
        '═══════════════════════════════════════════════════════════════════\n' +
        '  ❌ BRAIN BRIDGE 401 ON BOOT — token mismatch with fly.io\n' +
        '═══════════════════════════════════════════════════════════════════\n' +
        `  ARIA_API_TOKEN is set on this server but the Python brain at\n` +
        `  ${BRAIN_URL} rejected it as invalid.\n\n` +
        '  Most likely cause: the token was rotated on fly.io without\n' +
        '  updating this server (mirror of 2026-04-23 WA-listener incident).\n\n' +
        '  Fix:\n' +
        '    flyctl ssh console -a aria-intel  (or read the secret from\n' +
        '    your password manager) — get the current value of ARIA_API_TOKEN\n' +
        '    Set the same value on THIS server. Restart.\n' +
        '═══════════════════════════════════════════════════════════════════\n';
      if (!quiet) console.error(msg);
      verdict.reason = 'token mismatch';
      if (telegramAlerter?.isConfigured && typeof telegramAlerter.sendMessage === 'function') {
        telegramAlerter.sendMessage(
          '⚠️ *Brain bridge token mismatch*\n\n' +
          `Python brain at ${BRAIN_URL} rejected ARIA_API_TOKEN as invalid.\n` +
          'Most likely the token was rotated on fly.io without updating seenode.\n' +
          'Fix on seenode dashboard.'
        ).catch(() => {});
      }
    } else if (r.ok) {
      if (!quiet) console.log(`[brainBridge] ✓ healthy — ${BRAIN_URL} responded ${r.status} on boot self-check`);
      verdict.healthy = true;
      verdict.reason = 'ok';
    } else {
      if (!quiet) console.warn(
        `[brainBridge] non-OK on boot self-check: HTTP ${r.status}. ` +
        'Will retry on next brainAbsorb call. Could be transient (cold start).'
      );
      verdict.reason = `HTTP ${r.status}`;
    }
  } catch (err) {
    const errStr = err?.name === 'TimeoutError' ? 'timeout' : (err?.message || String(err));
    if (!quiet) console.warn(
      `[brainBridge] could not reach ${BRAIN_URL} on boot self-check: ${errStr}. ` +
      'Will retry on next brainAbsorb call.'
    );
    verdict.reason = errStr;
  }
  return verdict;
}

let _lastBridgeVerdict = null;
export function getBrainBridgeVerdict() {
  return _lastBridgeVerdict;
}
export async function runAndCacheBridgeVerdict(opts) {
  _lastBridgeVerdict = await verifyBrainBridge(opts);
  return _lastBridgeVerdict;
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

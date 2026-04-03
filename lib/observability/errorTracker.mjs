/**
 * CRUCIX — Centralized Error & Observability Layer
 * GAP 9 FIX: Structured error logging, severity classification,
 * sweep health tracking, and critical source failure alerting.
 *
 * Usage:
 *   import { errorTracker, sweepMonitor, criticalAlert } from './lib/observability/errorTracker.mjs';
 *
 *   // In your source fetchers:
 *   errorTracker.record('acled', 'timeout', error, sweepId);
 *
 *   // In your Express error handler:
 *   app.use(errorTracker.expressMiddleware());
 */

import crypto from 'crypto';

// ── Error Severity Classification ─────────────────────────────────────────────

const SEVERITY = {
  TRANSIENT:   'transient',    // timeout, network blip — self-resolves
  AUTH:        'auth',         // API key expired, 401/403 — needs human action
  STRUCTURAL:  'structural',   // API breaking change, parse failure — needs code fix
  CRITICAL:    'critical',     // core service down — needs immediate response
};

function classifyError(error, source = '') {
  const msg    = (error?.message || String(error)).toLowerCase();
  const status = error?.status || error?.statusCode || 0;

  if (status === 401 || status === 403 || msg.includes('api key') ||
      msg.includes('unauthorized') || msg.includes('forbidden')) {
    return SEVERITY.AUTH;
  }
  if (status >= 500 || msg.includes('timeout') || msg.includes('econnreset') ||
      msg.includes('socket hang up') || msg.includes('network')) {
    return SEVERITY.TRANSIENT;
  }
  if (msg.includes('parse') || msg.includes('json') || msg.includes('xml') ||
      msg.includes('unexpected token') || msg.includes('schema')) {
    return SEVERITY.STRUCTURAL;
  }
  if (['acled', 'gdelt', 'fred', 'un_ocha'].includes(source.toLowerCase())) {
    return SEVERITY.CRITICAL;
  }
  return SEVERITY.TRANSIENT;
}

// ── Critical Sources (alert on 2+ consecutive failures) ───────────────────────

const CRITICAL_SOURCES = new Set(['acled', 'gdelt', 'fred', 'un_ocha', 'world_bank', 'reliefweb']);

// ── Error Tracker ─────────────────────────────────────────────────────────────

class ErrorTracker {
  constructor() {
    this._log         = [];           // in-memory, last 2000 errors
    this._sourceStats = new Map();    // per-source consecutive failure counts
    this._sweepErrors = new Map();    // sweepId → errors[]
    this._alertFn     = null;
    this._redis       = null;
    this._maxLog      = 2000;
  }

  configure({ redis = null, alertFn = null } = {}) {
    this._redis   = redis;
    this._alertFn = alertFn;
    console.log('[ErrorTracker] Configured | redis=' + !!redis + ' alerting=' + !!alertFn);
  }

  // ── Record an error ────────────────────────────────────────────────────────

  record(source, errorType, error, sweepId = null, extra = {}) {
    const severity = classifyError(error, source);
    const entry = {
      id:        crypto.randomBytes(6).toString('hex'),
      timestamp: new Date().toISOString(),
      source:    source || 'unknown',
      errorType: errorType || 'unknown',
      severity,
      message:   error?.message || String(error),
      stack:     error?.stack?.slice(0, 500) || null,
      sweepId,
      ...extra,
    };

    this._log.unshift(entry);
    if (this._log.length > this._maxLog) this._log.length = this._maxLog;

    // Track consecutive failures per source
    this._trackSourceFailure(source, entry);

    // Track per-sweep errors
    if (sweepId) {
      if (!this._sweepErrors.has(sweepId)) this._sweepErrors.set(sweepId, []);
      this._sweepErrors.get(sweepId).push(entry);
    }

    // Async: persist to Redis and check alerts
    this._persistAndAlert(entry).catch(e => console.error('[ErrorTracker] Persist failed:', e.message));

    // Always log to console with structure
    const logFn = severity === SEVERITY.CRITICAL ? console.error : console.warn;
    logFn(`[${severity.toUpperCase()}] ${source}/${errorType}: ${entry.message}`);

    return entry;
  }

  // ── Track consecutive source failures ─────────────────────────────────────

  _trackSourceFailure(source, entry) {
    if (!source) return;
    const stats = this._sourceStats.get(source) || {
      consecutiveFailures: 0,
      totalErrors:         0,
      lastError:           null,
      lastSuccess:         null,
      alerted:             false,
    };
    stats.consecutiveFailures++;
    stats.totalErrors++;
    stats.lastError = entry.timestamp;
    this._sourceStats.set(source, stats);

    // Alert on 2+ consecutive failures for critical sources
    if (CRITICAL_SOURCES.has(source.toLowerCase()) &&
        stats.consecutiveFailures >= 2 &&
        !stats.alerted) {
      stats.alerted = true;
      this._fireSourceAlert(source, stats, entry);
    }
  }

  recordSuccess(source) {
    const stats = this._sourceStats.get(source);
    if (!stats) return;
    stats.consecutiveFailures = 0;
    stats.lastSuccess         = new Date().toISOString();
    stats.alerted             = false;   // reset alert threshold
    this._sourceStats.set(source, stats);
  }

  async _fireSourceAlert(source, stats, lastError) {
    const msg = (
      `🚨 *CRITICAL SOURCE FAILURE*\n\n` +
      `*Source:* ${source}\n` +
      `*Consecutive failures:* ${stats.consecutiveFailures}\n` +
      `*Severity:* ${lastError.severity.toUpperCase()}\n` +
      `*Error:* ${lastError.message.slice(0, 200)}\n` +
      `*Error type:* ${lastError.errorType}\n\n` +
      `_This source contributes to core intelligence — manual review required._`
    );
    if (this._alertFn) {
      try { await this._alertFn(msg); }
      catch (e) { console.error('[ErrorTracker] Alert send failed:', e.message); }
    }
  }

  // ── Persist to Redis ───────────────────────────────────────────────────────

  async _persistAndAlert(entry) {
    if (!this._redis) return;
    try {
      const key = `crucix:errors:${entry.id}`;
      await this._redis.set(key, JSON.stringify(entry), { ex: 30 * 86400 });    // 30-day retention
      await this._redis.lpush('crucix:errors:log', entry.id);
      await this._redis.ltrim('crucix:errors:log', 0, 4999);   // keep last 5000 IDs
    } catch (e) {
      // Don't let logging errors crash the app
    }
  }

  // ── Sweep Health ───────────────────────────────────────────────────────────

  getSweepHealth(sweepId) {
    const errors = this._sweepErrors.get(sweepId) || [];
    const criticalCount  = errors.filter(e => e.severity === SEVERITY.CRITICAL).length;
    const authCount      = errors.filter(e => e.severity === SEVERITY.AUTH).length;
    const structCount    = errors.filter(e => e.severity === SEVERITY.STRUCTURAL).length;
    const transientCount = errors.filter(e => e.severity === SEVERITY.TRANSIENT).length;

    let status = 'HEALTHY';
    if (criticalCount > 0 || errors.length > 15) status = 'DEGRADED';
    if (criticalCount > 3 || errors.length > 30) status = 'FAILED';

    return {
      sweepId,
      status,
      totalErrors:    errors.length,
      bySeverity: { critical: criticalCount, auth: authCount, structural: structCount, transient: transientCount },
      failedSources: [...new Set(errors.map(e => e.source))],
    };
  }

  // ── Query / Reporting ──────────────────────────────────────────────────────

  getRecentErrors(n = 50, filter = {}) {
    let log = [...this._log];
    if (filter.source)   log = log.filter(e => e.source === filter.source);
    if (filter.severity) log = log.filter(e => e.severity === filter.severity);
    if (filter.since)    log = log.filter(e => e.timestamp >= filter.since);
    return log.slice(0, n);
  }

  getSourceHealth() {
    const result = {};
    for (const [source, stats] of this._sourceStats) {
      result[source] = {
        ...stats,
        status: stats.consecutiveFailures === 0 ? 'HEALTHY'
               : stats.consecutiveFailures < 3  ? 'DEGRADED'
               : 'FAILING',
      };
    }
    return result;
  }

  getErrorRateDashboard(windowHours = 24) {
    const cutoff  = new Date(Date.now() - windowHours * 3600 * 1000).toISOString();
    const recent  = this._log.filter(e => e.timestamp >= cutoff);
    const bySrc   = {};
    for (const e of recent) {
      if (!bySrc[e.source]) bySrc[e.source] = { total: 0, bySeverity: {} };
      bySrc[e.source].total++;
      bySrc[e.source].bySeverity[e.severity] = (bySrc[e.source].bySeverity[e.severity] || 0) + 1;
    }
    return {
      window_hours:    windowHours,
      total_errors:    recent.length,
      by_source:       bySrc,
      by_severity: {
        critical:  recent.filter(e => e.severity === SEVERITY.CRITICAL).length,
        auth:      recent.filter(e => e.severity === SEVERITY.AUTH).length,
        structural:recent.filter(e => e.severity === SEVERITY.STRUCTURAL).length,
        transient: recent.filter(e => e.severity === SEVERITY.TRANSIENT).length,
      },
    };
  }

  // ── Express Error Middleware ───────────────────────────────────────────────

  expressMiddleware() {
    return (err, req, res, next) => {
      this.record(
        'express_route',
        err.name || 'UnhandledError',
        err,
        req.headers['x-sweep-id'],
        { route: req.path, method: req.method, userId: req.user?.id }
      );
      const status = err.status || err.statusCode || 500;
      res.status(status).json({
        error:     process.env.NODE_ENV === 'production' ? 'Internal server error' : err.message,
        requestId: req.headers['x-request-id'],
      });
    };
  }

  // ── API route handler ──────────────────────────────────────────────────────

  apiHandler() {
    return {
      getErrors:     (req, res) => res.json(this.getRecentErrors(
        parseInt(req.query.n || '50'),
        { source: req.query.source, severity: req.query.severity }
      )),
      getSourceHealth: (_req, res) => res.json(this.getSourceHealth()),
      getDashboard:    (req, res) => res.json(this.getErrorRateDashboard(
        parseInt(req.query.hours || '24')
      )),
    };
  }
}

// ── Sweep Monitor ─────────────────────────────────────────────────────────────
// Wraps a sweep run with health tracking

export class SweepMonitor {
  constructor(tracker, alertFn = null) {
    this._tracker = tracker;
    this._alertFn = alertFn;
  }

  async wrap(sweepId, sweepFn) {
    const t0 = Date.now();
    let result;
    try {
      result = await sweepFn();
    } catch (e) {
      this._tracker.record('sweep_engine', 'fatal_error', e, sweepId);
      throw e;
    } finally {
      const health   = this._tracker.getSweepHealth(sweepId);
      const duration = ((Date.now() - t0) / 1000).toFixed(1);
      console.log(`[SweepMonitor] ${sweepId} | status=${health.status} | errors=${health.totalErrors} | ${duration}s`);

      if (health.status === 'DEGRADED' && this._alertFn) {
        await this._alertFn(
          `⚠ *SWEEP DEGRADED*\n\n` +
          `Sweep ID: \`${sweepId}\`\n` +
          `Total errors: ${health.totalErrors}\n` +
          `Failed sources: ${health.failedSources.join(', ')}\n` +
          `Critical errors: ${health.bySeverity.critical}`
        ).catch(() => {});
      }
    }
    return result;
  }
}

// ── Singleton export ──────────────────────────────────────────────────────────

export const errorTracker = new ErrorTracker();

export function configureTelemetry(redis, alertFn) {
  errorTracker.configure({ redis, alertFn });
  return errorTracker;
}

// ── Register Express routes ────────────────────────────────────────────────────
// In your server.mjs:
// import { errorTracker, configureTelemetry } from './lib/observability/errorTracker.mjs';
// configureTelemetry(redisClient, (msg) => sendTelegramMessage(ADMIN_CHAT_ID, msg));
//
// const handlers = errorTracker.apiHandler();
// router.get('/api/admin/errors',        requireAdmin, handlers.getErrors);
// router.get('/api/admin/source-health', requireAdmin, handlers.getSourceHealth);
// router.get('/api/admin/error-dashboard', requireAdmin, handlers.getDashboard);
// app.use(errorTracker.expressMiddleware());   // LAST middleware

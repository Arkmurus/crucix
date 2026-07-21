/**
 * ARIA — Centralized Error & Observability Layer
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

export const SEVERITY = {
  TRANSIENT:    'transient',    // timeout, network blip — self-resolves
  AUTH:         'auth',         // API key expired, 401/403 — needs human action
  STRUCTURAL:   'structural',   // API breaking change, parse failure — needs code fix
  CLIENT_INPUT: 'client_input', // R-F2452: malformed CLIENT request (400) — NOT a code defect
  CRITICAL:     'critical',     // core service down — needs immediate response
  PROCESS:      'process',      // R-F2821: OUR OWN code/process failed — always a defect
};

/**
 * R-F2821 — THE TWO FAILURE DOMAINS. This module classified both with one set of
 * heuristics, and that is why the Node tier was structurally dark.
 *
 *   OUTBOUND-DEPENDENCY failures (OSINT feeds, third-party APIs). A 500 or a
 *   timeout here IS transient — the dependency blipped, we fall back, and
 *   CLAUDE.md §14 says that is "operational, not degraded". R-F1016 deliberately
 *   keeps these OFF the escalation path so the gap pipeline is not flooded.
 *   That guard is correct and this change does not touch it.
 *
 *   OWN-CODE / PROCESS failures (an uncaught exception, an unhandled rejection,
 *   a 500 escaping our own route handler, the listener failing to bind, an auth
 *   control-plane event). These are NEVER "a blip that self-resolves" — they are
 *   defects or security events, and they are exactly what §21 requires the brain
 *   to see so the self-coding loop can act.
 *
 * Before this fix every own-code failure matched no keyword and fell to the
 * `return SEVERITY.TRANSIENT` default, and _reportToBrain dropped TRANSIENT — so
 * `uncaughtException` (server.mjs:7603), `unhandledRejection` (:7599), the boot
 * listen_error (:7302), the brute-force lockout signal (:4983) and EVERY 500
 * through expressMiddleware were classified as blips and never sent. R-F2182
 * wired unhandledRejection to the brain in good faith; the wire was severed one
 * layer down, at the severity filter, so the wiring looked done and emitted
 * nothing.
 *
 * Encoding the domain by SOURCE rather than by a per-call-site flag is deliberate:
 * a flag has to be remembered at each new call site, and a forgotten flag fails
 * silently back to dark — which is this very bug. The set is the contract; adding
 * a process-level source here is the only step needed to keep it wired.
 */
export const OWN_CODE_SOURCES = Object.freeze(new Set([
  'web_process',   // uncaughtException / unhandledRejection (server.mjs:7599,7603)
  'boot',          // listen_error — the process could not start (server.mjs:7302)
  'express_route', // a 500 escaped our own handler (expressMiddleware)
  'auth',          // login handler faults + brute-force lockouts (server.mjs:4983,5029)
]));

export function classifyError(error, source = '') {
  const msg    = (error?.message || String(error)).toLowerCase();
  const status = error?.status || error?.statusCode || 0;

  if (status === 401 || status === 403 || msg.includes('api key') ||
      msg.includes('unauthorized') || msg.includes('forbidden')) {
    return SEVERITY.AUTH;
  }
  // R-F2452 — a CLIENT sending malformed JSON is a 400 bad-request, NOT a
  // server-side code defect. Express' json body-parser throws a SyntaxError
  // with type 'entity.parse.failed' + status 400. Classifying it STRUCTURAL
  // polluted the structural-defect signal and could false-alert. Must run
  // BEFORE the generic parse/json → STRUCTURAL rule below.
  //
  // R-F2821 — this check MOVED ABOVE the transient rule and above the own-code
  // rule. It must beat both: a client's malformed body arriving on `express_route`
  // is the client's fault, not our defect, and must stay off the escalation path.
  if (error?.type === 'entity.parse.failed' ||
      (status === 400 && (msg.includes('json') || msg.includes('parse') ||
                          msg.includes('unexpected token')))) {
    return SEVERITY.CLIENT_INPUT;
  }
  // R-F2821 — OWN-CODE DOMAIN. Must beat the transient rule below, because that
  // rule is written for outbound dependencies: `status >= 500` means "the source
  // we called returned 500", but on express_route it means "OUR handler threw",
  // and `timeout`/`network` in our own stack trace is still our fault to see.
  // A defect in our code is never a self-resolving blip.
  if (OWN_CODE_SOURCES.has(String(source || '').toLowerCase())) {
    return SEVERITY.PROCESS;
  }
  if (status >= 500 || msg.includes('timeout') || msg.includes('econnreset') ||
      msg.includes('socket hang up') || msg.includes('network')) {
    return SEVERITY.TRANSIENT;
  }
  if (msg.includes('parse') || msg.includes('json') || msg.includes('xml') ||
      msg.includes('unexpected token') || msg.includes('schema')) {
    return SEVERITY.STRUCTURAL;
  }
  // R-F1016: external OSINT sources timing out while others serve is
  // operational, not CRITICAL (CLAUDE.md §14 fallback transparency).
  // Only escalate to CRITICAL for non-transient, non-structural errors
  // on these sources — i.e. errors that didn't match any above pattern.
  if (['acled', 'gdelt', 'fred', 'un_ocha'].includes(source.toLowerCase())) {
    return SEVERITY.CRITICAL;
  }
  return SEVERITY.TRANSIENT;
}

// ── Critical Sources (alert on 2+ consecutive failures) ───────────────────────
// R-F1016: external OSINT sources (GDELT, ACLED, FRED, UN_OCHA) are external
// services — a transient timeout on one while others serve is operational, not
// CRITICAL (CLAUDE.md §14 fallback transparency). Only internal/core services
// that would take down the whole sweep if they fail belong here.
const CRITICAL_SOURCES = new Set(['world_bank', 'reliefweb']);

// R-F1080 — per-source circuit breakers. Each source gets a circuit breaker
// that opens after N consecutive failures and stops calling for a cooldown
// period. State transitions: CLOSED → OPEN (on threshold) → HALF_OPEN (after
// cooldown) → CLOSED (on success) or OPEN (on failure).
const CIRCUIT_DEFAULTS = {
  threshold: 3,           // consecutive failures before opening
  cooldownMs: 300_000,    // 5 min before half-open probe
  halfOpenMax: 1,         // only 1 probe at a time
};

class CircuitBreaker {
  constructor(name, opts = {}) {
    this.name = name;
    this.state = 'CLOSED';  // CLOSED | OPEN | HALF_OPEN
    this.failureCount = 0;
    this.lastFailureAt = 0;
    this.lastSuccessAt = 0;
    this.openedAt = 0;
    this.threshold = opts.threshold || CIRCUIT_DEFAULTS.threshold;
    this.cooldownMs = opts.cooldownMs || CIRCUIT_DEFAULTS.cooldownMs;
  }

  recordFailure() {
    this.failureCount++;
    this.lastFailureAt = Date.now();
    if (this.failureCount >= this.threshold && this.state === 'CLOSED') {
      this.state = 'OPEN';
      this.openedAt = Date.now();
      console.warn(`[CircuitBreaker] ${this.name} OPEN after ${this.failureCount} failures`);
      this._reportToBrain('circuit_opened', `${this.name} circuit opened after ${this.failureCount} failures`);
    } else if (this.state === 'HALF_OPEN') {
      this.state = 'OPEN';
      this.openedAt = Date.now();
      console.warn(`[CircuitBreaker] ${this.name} OPEN (half-open probe failed)`);
    }
  }

  recordSuccess() {
    this.failureCount = 0;
    this.lastSuccessAt = Date.now();
    if (this.state === 'HALF_OPEN' || this.state === 'OPEN') {
      this.state = 'CLOSED';
      console.info(`[CircuitBreaker] ${this.name} CLOSED (recovered)`);
      this._reportToBrain('circuit_closed', `${this.name} circuit closed (recovered)`);
    }
  }

  shouldTry() {
    if (this.state === 'CLOSED') return true;
    if (this.state === 'OPEN') {
      if (Date.now() - this.openedAt >= this.cooldownMs) {
        this.state = 'HALF_OPEN';
        console.info(`[CircuitBreaker] ${this.name} HALF_OPEN (probing)`);
        return true;
      }
      return false;
    }
    if (this.state === 'HALF_OPEN') return true;
    return true;
  }

  getState() {
    return { name: this.name, state: this.state, failureCount: this.failureCount,
      lastFailureAt: this.lastFailureAt, lastSuccessAt: this.lastSuccessAt };
  }

  async _reportToBrain(signalType, message) {
    const brainBase = process.env.ARIA_SERVICE_URL || process.env.BRAIN_SERVICE_URL || '';
    const brainToken = process.env.ARIA_API_TOKEN || process.env.ARIA_INTERNAL_TOKEN || '';
    if (!brainBase) return;
    try {
      await fetch(`${brainBase}/api/aria/brain/signal`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json',
          ...(brainToken ? { 'Authorization': `Bearer ${brainToken}` } : {}) },
        body: JSON.stringify({
          content: `Circuit breaker: ${message}`.slice(0, 500),
          source: `aria-web:circuit_breaker:${this.name}`,
          signal_type: `circuit_breaker_${signalType}`,
          metadata: { circuitName: this.name, state: this.state, failures: this.failureCount },
        }),
        signal: AbortSignal.timeout(5000),
      });
    } catch { /* best-effort */ }
  }
}

// ── Error Tracker ─────────────────────────────────────────────────────────────

// R-F2821: exported so the brain-wire contract is testable directly. The module
// still exposes the `errorTracker` singleton below as the app-facing instance.
export class ErrorTracker {
  // R-F2821 — per-(source/errorType/severity) escalation window. Long enough that
  // a request storm collapses to one signal, short enough that a recurring defect
  // keeps re-asserting itself rather than being permanently suppressed.
  static THROTTLE_MS = 60_000;

  constructor() {
    this._log         = [];           // in-memory, last 2000 errors
    this._sourceStats = new Map();    // per-source consecutive failure counts
    this._sweepErrors = new Map();    // sweepId → errors[]
    this._alertFn     = null;
    this._redis       = null;
    this._maxLog      = 2000;
    this._circuitBreakers = new Map(); // source → CircuitBreaker
    // R-F2821 — brain-wire state lives in the CONSTRUCTOR, not configure().
    // configure() is called at boot; anything that records BEFORE it (or an
    // instance that is never configured) previously had these undefined, and
    // `this._brainWire.dropped++` on undefined throws — swallowed by the
    // fire-and-forget `.catch(() => {})` at the call site, i.e. it would fail
    // silently, which is the exact defect class this R-number is closing.
    this._brainBase   = process.env.ARIA_SERVICE_URL || process.env.BRAIN_SERVICE_URL || '';
    this._brainToken  = process.env.ARIA_API_TOKEN || process.env.ARIA_INTERNAL_TOKEN || '';
    this._brainWire   = {
      delivered: 0, dropped: 0, throttled: 0, droppedNoTarget: 0,
      lastOkAt: null, lastDropAt: null, lastError: null,
    };
    this._brainThrottle = new Map();
  }

  // R-F1080 — get or create a circuit breaker for a source
  _getCircuitBreaker(source) {
    if (!this._circuitBreakers.has(source)) {
      this._circuitBreakers.set(source, new CircuitBreaker(source));
    }
    return this._circuitBreakers.get(source);
  }

  // R-F1080 — check if a source's circuit breaker allows a call
  shouldAttempt(source) {
    const cb = this._getCircuitBreaker(source);
    return cb.shouldTry();
  }

  // R-F1080 — get all circuit breaker states
  getCircuitBreakerStates() {
    const states = {};
    for (const [source, cb] of this._circuitBreakers) {
      states[source] = cb.getState();
    }
    return states;
  }

  configure({ redis = null, alertFn = null } = {}) {
    this._redis   = redis;
    this._alertFn = alertFn;
    // R-F900 — brain-report target so Node-tier failures become coder-visible
    // (P0-4). Same base URL + token the working /api/aria proxy uses; POSTs to
    // the R-F887 /api/aria/brain/signal sink (failure signal_type → routes to
    // capability_gaps, which the reconnected coder reads per R-F884).
    this._brainBase  = process.env.ARIA_SERVICE_URL || process.env.BRAIN_SERVICE_URL || '';
    this._brainToken = process.env.ARIA_API_TOKEN || process.env.ARIA_INTERNAL_TOKEN || '';
    // R-F2821: re-read here (not just in the constructor) because configure() runs
    // after dotenv/secrets are loaded at boot. Counters are NOT reset — the wire's
    // health is cumulative for the process lifetime.
    console.log('[ErrorTracker] Configured | redis=' + !!redis + ' alerting=' + !!alertFn + ' brain=' + !!this._brainBase);
  }

  // ── R-F900 — report a significant Node-tier failure to the brain ───────────
  // R-F2821: severities that MUST reach the brain. PROCESS is the addition —
  // see OWN_CODE_SOURCES above for why every own-code failure was dropped here.
  // TRANSIENT and CLIENT_INPUT stay off this list on purpose (R-F1016 / R-F2452):
  // a dependency blip and a client's malformed body are not defects, and flooding
  // the gap pipeline with them would make the real signals unfindable.
  static ESCALATE = Object.freeze([
    SEVERITY.CRITICAL, SEVERITY.AUTH, SEVERITY.STRUCTURAL, SEVERITY.PROCESS,
  ]);

  /**
   * Observability of the wire ITSELF (§21a: a signal that silently fails is
   * still dark). Before R-F2821 this method had no res.ok check and a bare
   * `catch {}`, so a brain returning 401/404/500 was indistinguishable from a
   * successful delivery — the tier could report "wired" while emitting nothing.
   */
  brainWireStats() {
    return { ...this._brainWire, configured: !!this._brainBase };
  }

  async _reportToBrain(entry) {
    if (!this._brainBase) { this._brainWire.droppedNoTarget++; return false; }
    if (!ErrorTracker.ESCALATE.includes(entry.severity)) return false;

    // R-F2821 — escalation throttle. Own-code failures are now escalated, and an
    // outage can produce them in a tight loop (every request 500s). Collapse a
    // repeating (source/errorType) to one signal per window so the brain sees the
    // CLASS without the gap pipeline being buried. Deliberately per-class, not
    // global: a second, different failure during the same outage still gets through.
    const key = `${entry.source}/${entry.errorType}/${entry.severity}`;
    const now = Date.now();
    const last = this._brainThrottle.get(key) || 0;
    if (now - last < ErrorTracker.THROTTLE_MS) { this._brainWire.throttled++; return false; }
    this._brainThrottle.set(key, now);
    if (this._brainThrottle.size > 500) {  // bound the map; oldest-first
      for (const [k, t] of [...this._brainThrottle].sort((a, b) => a[1] - b[1]).slice(0, 250)) {
        if (this._brainThrottle.get(k) === t) this._brainThrottle.delete(k);
      }
    }

    const body = JSON.stringify({
      content: `Node tier ${entry.source}/${entry.errorType} (${entry.severity}): ${entry.message}`.slice(0, 500),
      source: `aria-web:${entry.source}`,
      signal_type: `node_tier_failure_${entry.severity}`,  // contains "fail" → capability_gap (R-F887)
      metadata: { errorType: entry.errorType, severity: entry.severity, sweepId: entry.sweepId || null, id: entry.id },
    });

    // One retry: the brain's ~10-min cold boot (CLAUDE.md §11c) means a single
    // in-flight failure is common and is NOT a reason to lose the signal.
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        const res = await fetch(`${this._brainBase}/api/aria/brain/signal`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...(this._brainToken ? { 'Authorization': `Bearer ${this._brainToken}` } : {}),
          },
          body,
          signal: AbortSignal.timeout(5000),
        });
        // THE MISSING CHECK. Without it a 401/404/500 resolved normally and was
        // treated as delivered.
        if (res.ok) {
          this._brainWire.delivered++;
          this._brainWire.lastOkAt = new Date().toISOString();
          return true;
        }
        this._brainWire.lastError = `HTTP ${res.status}`;
      } catch (e) {
        this._brainWire.lastError = e?.message || 'fetch failed';
      }
      if (attempt === 0) await new Promise((r) => setTimeout(r, 750));
    }
    // Never throw — telemetry must not crash the app — but never silent either.
    this._brainWire.dropped++;
    this._brainWire.lastDropAt = new Date().toISOString();
    if (this._brainWire.dropped === 1 || this._brainWire.dropped % 25 === 0) {
      console.error(`[ErrorTracker] BRAIN WIRE DEGRADED — ${this._brainWire.dropped} signal(s) ` +
        `undelivered (last error: ${this._brainWire.lastError}). Node-tier failures are ` +
        'not reaching the brain; §21 self-healing is blind to this tier.');
    }
    return false;
  }

  // ── Record an error ────────────────────────────────────────────────────────

  record(source, errorType, error, sweepId = null, extra = {}) {
    const severity = classifyError(error, source);
    // R-F1080 — record failure in circuit breaker
    this._getCircuitBreaker(source).recordFailure();
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
    // R-F900 — escalate significant Node-tier failures to the brain (P0-4) so
    // they become coder-visible. Fire-and-forget; never blocks or throws.
    this._reportToBrain(entry).catch(() => {});

    // Always log to console with structure
    // R-F2821: read from `entry`, not the local, so a caller-supplied override in
    // `extra` is reflected; and an own-code PROCESS failure logs at error level —
    // it is a defect, not a warning.
    const logFn = (entry.severity === SEVERITY.CRITICAL || entry.severity === SEVERITY.PROCESS)
      ? console.error : console.warn;
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
    // R-F1080 — record success in circuit breaker
    this._getCircuitBreaker(source).recordSuccess();
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
        { route: req.path, method: req.method, userId: req.user?.userId || req.user?.id }  // R-F2383: JWT has userId, not id
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

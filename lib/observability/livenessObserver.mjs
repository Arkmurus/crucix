// lib/observability/livenessObserver.mjs
//
// R-F2860 — EXTERNAL liveness observer: aria-web watches aria-intel.
//
// THE GAP. web_integrity_agent runs INSIDE the aria-intel process and polls
// localhost:8000. When aria-intel dies or crash-loops, that agent dies WITH it —
// so aria-intel's own death is invisible and unrecorded ("9 passed / 0 failed" is
// guaranteed whenever the log line appears at all). An in-process monitor can
// measure SLOW; it can NEVER measure DEAD. This observer runs in a DIFFERENT
// process (aria-web) and records to a sink that OUTLIVES aria-intel, so a death is
// seen, recorded, alerted, and — on recovery — reported back to the brain (§25,
// the outage it could not self-report).
//
// TWO equal-and-opposite correctness properties:
//   * it must PROVABLY FIRE on a real outage (record + alert + brain report) — a
//     monitor never seen to fail is not a monitor;
//   * it must NOT cry wolf during the legit ~10-min cold boot or a rolling deploy —
//     a false alarm every deploy destroys the alert's trust. So DOWN is confirmed
//     only after SUSTAINED unreachability (past the boot/deploy window) OR a
//     FLAPPING window (the crash-loop signature) — the R-F1380/R-F1381 discipline,
//     applied externally.
//
// The probe (probeFlyHealth, lib/health/crossHealth.mjs) is honestly tri-state:
//   offline = transport refusal (real down evidence)
//   unknown = timeout (ambiguous — but sustained timeouts still mean unreachable)
//   online | degraded_slow | degraded = it ANSWERED → alive (degraded = up-but-unhealthy,
//     a different concern; warming/slow = degraded_slow — NOT dead).
//
// This module holds NO timers and does NO real I/O itself: it exposes tick() and
// takes every dependency (probe, store, notify, brain) injected, so it is fully
// unit-testable and can never itself take down aria-web. server.mjs owns the timer.

export const DEFAULTS = Object.freeze({
  pollIntervalS: 30,               // how often server.mjs should call tick()
  sustainedFailuresToConfirm: 8,   // 8×30s = 4min CONTINUOUS unreachable → DOWN (clears a ~2min deploy/boot bind window)
  flapWindowProbes: 30,            // rolling window = 15min at 30s
  flapFailuresToConfirm: 10,       // ≥10 failed probes in 15min → crash-loop/unstable (a single deploy blips ~4)
  recoveryConsecutiveAlive: 2,     // 2×30s of answers → recovered (avoids announcing recovery on a crash-loop's brief up-blip)
  maxOutages: 200,                 // durable ledger ring cap
});

/** Classify one probe result into 'failure' | 'alive'. Pure.
 *
 * Takes the whole probe object (not just state) because of the PUBLIC-url case:
 * when aria-intel's machine is dead/booting, fly-proxy answers 502/503, and
 * probeFlyHealth labels that 'degraded' ("it answered") — but the PROXY answered,
 * not the app. A 5xx is the down/crash-loop signal on the public url (live-observed:
 * "/health/live alternates 200/502"), so it must count as a failure, not 'alive'. */
export function classifyProbe(probe) {
  const state = probe?.state;
  if (state === 'offline' || state === 'unknown') return 'failure';   // refused / timed out
  if (typeof probe?.http_status === 'number' && probe.http_status >= 500) return 'failure';  // fly-proxy 5xx = app down
  return 'alive';   // online | degraded_slow | 2xx-unhealthy | 4xx — the app itself answered
}

function fmtDuration(s) {
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  return `${(s / 3600).toFixed(1)}h`;
}

/**
 * @param {object}   o
 * @param {string}   o.serviceUrl  aria-intel base URL (no trailing slash); '' → disabled no-op
 * @param {function} o.probeFn     async ({flyUrl}) => { state, build_rev }  (probeFlyHealth)
 * @param {object}   o.store       { read(): array, write(array): Promise }  (durable, on /data)
 * @param {function} o.notifyFn    async (msg) => void                       (operator alert)
 * @param {function} o.brainPostFn async ({content,source,signal_type,metadata}) => void
 * @param {function} [o.now]       () => ms epoch (injectable for tests)
 * @param {object}   [o.logger]    console-like
 * @param {object}   [o.config]    overrides for DEFAULTS
 */
export function createLivenessObserver({
  serviceUrl, probeFn, store, notifyFn, brainPostFn,
  now = () => Date.now(), logger = console, config = {},
} = {}) {
  const cfg = { ...DEFAULTS, ...config };

  let history = [];             // rolling classifications, bounded to flapWindowProbes
  let consecutiveFailures = 0;
  let consecutiveAlive = 0;
  let troubleStartedAt = null;  // first failure of the current trouble period (survives flap blips)
  let status = 'up';            // 'up' | 'down'
  let currentOutage = null;
  let inFlight = false;

  async function safe(fn, label) {
    try { await fn(); } catch (e) { logger.warn?.(`[liveness_observer] ${label} failed: ${e?.message || e}`); }
  }

  async function tick() {
    if (!serviceUrl) return { disabled: true };
    if (inFlight) return { skipped: true };   // non-overlapping guard (mirrors _bridgeRecheckInFlight)
    inFlight = true;
    try {
      let probe;
      try {
        probe = await probeFn({ flyUrl: serviceUrl });
      } catch (e) {
        probe = { state: 'unknown', error: String(e?.message || e) };   // a throwing probe IS a failure signal
      }
      const cls = classifyProbe(probe);

      history.push(cls);
      if (history.length > cfg.flapWindowProbes) history.shift();

      if (cls === 'failure') {
        consecutiveAlive = 0;
        consecutiveFailures += 1;
        if (troubleStartedAt === null) troubleStartedAt = now();
      } else {
        consecutiveFailures = 0;
        consecutiveAlive += 1;
        // a blip that fully recovered WITHOUT ever confirming DOWN clears the marker,
        // so a much-later unrelated failure never inherits an ancient start time.
        if (status === 'up' && consecutiveAlive >= cfg.recoveryConsecutiveAlive) troubleStartedAt = null;
      }

      const failuresInWindow = history.reduce((n, c) => n + (c === 'failure' ? 1 : 0), 0);
      const sustained = consecutiveFailures >= cfg.sustainedFailuresToConfirm;
      const flapping = failuresInWindow >= cfg.flapFailuresToConfirm;

      if (status === 'up' && (sustained || flapping)) {
        await confirmDown(probe, sustained ? 'sustained' : 'flapping', failuresInWindow);
      } else if (status === 'down' && consecutiveAlive >= cfg.recoveryConsecutiveAlive) {
        await confirmRecovery(probe);
      } else if (status === 'down' && cls === 'failure') {
        await updateOngoing(probe);
      }
      return { status, cls, consecutiveFailures, failuresInWindow };
    } finally {
      inFlight = false;
    }
  }

  async function confirmDown(probe, reason, failuresInWindow) {
    status = 'down';
    const downFrom = troubleStartedAt || now();
    currentOutage = {
      id: `outage_${downFrom}`,
      down_from: downFrom,
      confirmed_at: now(),
      reason,
      failures_in_window: failuresInWindow,
      last_seen_down: now(),
      last_state: probe?.state ?? null,
      status: 'open',
    };
    await safe(() => appendOutage(currentOutage), 'store append');
    await safe(() => notifyFn(
      `🔴 aria-intel DOWN — unreachable since ${new Date(downFrom).toISOString()} `
      + `(${reason}; ${failuresInWindow} failed probes in window). Users cannot reach the brain.`,
    ), 'notify down');
    logger.warn?.(`[liveness_observer] aria-intel DOWN confirmed (${reason}, ${failuresInWindow} fails/window)`);
  }

  async function updateOngoing(probe) {
    if (!currentOutage) return;
    currentOutage.last_seen_down = now();
    currentOutage.last_state = probe?.state ?? null;
    await safe(() => upsertOutage(currentOutage), 'store update');
  }

  async function confirmRecovery(probe) {
    const upAt = now();
    const downFrom = currentOutage?.down_from ?? upAt;
    const durationS = Math.max(0, Math.round((upAt - downFrom) / 1000));
    if (currentOutage) {
      currentOutage.recovered_at = upAt;
      currentOutage.duration_s = durationS;
      currentOutage.status = 'closed';
      currentOutage.recovered_build_rev = probe?.build_rev ?? null;
      await safe(() => upsertOutage(currentOutage), 'store close');
    }
    await safe(() => notifyFn(
      `🟢 aria-intel RECOVERED — was down ~${fmtDuration(durationS)} `
      + `(build_rev=${probe?.build_rev || '?'}).`,
    ), 'notify recovery');
    // §25 proprioception — tell the brain about the outage it could NOT see itself.
    // signal_type contains "unavailable" → the brain routes it to capability_gaps
    // (coder-visible), so the self-heal loop can act on its own downtime.
    await safe(() => brainPostFn({
      content: `aria-intel was UNREACHABLE from ${new Date(downFrom).toISOString()} to `
        + `${new Date(upAt).toISOString()} (~${durationS}s), observed externally by aria-web. `
        + `The brain could not self-report this outage.`,
      source: 'aria-web:external_observer',
      signal_type: 'aria_intel_unavailable_recovered',
      metadata: { down_from: downFrom, up_at: upAt, duration_s: durationS, reason: currentOutage?.reason ?? null },
    }), 'brain post');
    logger.warn?.(`[liveness_observer] aria-intel RECOVERED after ${durationS}s`);
    status = 'up';
    currentOutage = null;
    troubleStartedAt = null;
    consecutiveFailures = 0;
  }

  // ── durable ledger helpers (best-effort; a store failure is logged, never thrown) ──
  async function appendOutage(o) {
    const arr = (store.read() || []).slice();
    arr.push({ ...o });
    while (arr.length > cfg.maxOutages) arr.shift();
    await store.write(arr);
  }
  async function upsertOutage(o) {
    const arr = (store.read() || []).slice();
    for (let i = arr.length - 1; i >= 0; i--) {
      if (arr[i].id === o.id) { arr[i] = { ...o }; await store.write(arr); return; }
    }
    arr.push({ ...o });
    while (arr.length > cfg.maxOutages) arr.shift();
    await store.write(arr);
  }

  function snapshot() {
    return {
      status,
      consecutiveFailures,
      consecutiveAlive,
      failuresInWindow: history.reduce((n, c) => n + (c === 'failure' ? 1 : 0), 0),
      windowSize: history.length,
      currentOutage: currentOutage ? { ...currentOutage } : null,
    };
  }

  return { tick, snapshot, _config: cfg };
}

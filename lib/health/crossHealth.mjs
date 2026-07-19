// lib/health/crossHealth.mjs
// R-F2776: the fly-side probe behind GET /api/health/cross.
//
// Extracted from server.mjs so the invariant below is testable against the REAL
// production function rather than a replica (§3c: a capability test must invoke
// the path that was broken).
//
// ── THE INVARIANT: A TIMEOUT IS NOT EVIDENCE OF OFFLINE ──────────────────────
// The previous implementation let a single 4s AbortSignal.timeout decide `ok`, so
// a brain that was alive and healthy but slow to answer was reported flatly
// OFFLINE. That is the fabrication class CLAUDE.md gate #3 was rewritten to kill:
// absence of a signal reported as a measured negative. It bit here specifically
// because aria-intel's documented boot loads ~223k facts + ~1.2M neural edges and
// takes ~10 minutes to go green (§11c) — exactly the window when an operator
// consults cross-health, and exactly when it lied.
//
// The fix is NOT a bigger timeout (§1 forbids the band-aid — a bigger number moves
// the lie rather than removing it). It is to distinguish what the probe can
// actually learn, and to refuse to conclude when it learned nothing:
//
//   'online'        answered in budget, body says alive
//   'degraded_slow' answered and healthy, but slower than the fast budget
//   'degraded'      answered with non-2xx or an unhealthy body — reachable, unwell
//   'unknown'       probe timed out. ok === null. NO verdict is rendered.
//   'offline'       transport refused / DNS / reset — positive evidence of down.
//
// `ok` is tri-state exactly as the phase-gate measures are: true/false mean
// MEASURED, null means COULD NOT MEASURE. Callers must not collapse null to false.

export const HEALTHY_STATUSES = Object.freeze(['alive', 'ok', 'healthy']);

/** A timeout/abort — the probe learned nothing — vs a transport error, which is real evidence. */
export function isTimeoutError(e) {
  return e?.name === 'TimeoutError' || e?.name === 'AbortError';
}

/**
 * Probe the fly brain's public /health/live and classify the result honestly.
 *
 * @param {object}   opts
 * @param {string}   opts.flyUrl      base URL, no trailing slash
 * @param {function} [opts.fetchImpl] injectable for tests (defaults to global fetch)
 * @param {number[]} [opts.budgets]   probe budgets in ms. The second is attempted
 *                                    ONLY when the first TIMED OUT, so the common
 *                                    slow-boot case resolves to a real reading
 *                                    instead of a false alarm — and the extra wait
 *                                    is paid only on the already-failing path.
 * @returns {Promise<object>} the `fly` block: { server, url, ok, state, ... }
 */
export async function probeFlyHealth({ flyUrl, fetchImpl, budgets = [4000, 12000] } = {}) {
  const doFetch = fetchImpl || globalThis.fetch;
  const fly = { server: 'fly.io-aria_service', url: flyUrl, ok: false };

  let probe = null, lastErr = null, timedOut = false;
  for (const budget of budgets) {
    const t0 = Date.now();
    try {
      // The PUBLIC /health/live (FastAPI app-level, no auth) is the right endpoint
      // for a cross-server liveness check: no bearer token, minimal disclosure. The
      // richer /api/aria/health is auth-protected and stays that way.
      const r = await doFetch(`${flyUrl}/health/live`, {
        headers: { Accept: 'application/json' },
        signal: AbortSignal.timeout(budget),
      });
      probe = { r, latency_ms: Date.now() - t0 };
      timedOut = false;
      break;
    } catch (e) {
      lastErr = e;
      timedOut = isTimeoutError(e);
      if (!timedOut) break;   // transport error is real evidence — don't retry
    }
  }

  if (probe) {
    const { r, latency_ms } = probe;
    fly.latency_ms = latency_ms;
    fly.http_status = r.status;
    if (r.ok) {
      let healthy;
      try {
        const body = await r.json();
        healthy = HEALTHY_STATUSES.includes(body?.status);
        fly.build_rev = body?.build_rev;
      } catch {
        // Answered 2xx but the body isn't JSON — it is up; say so and show the body.
        healthy = true;
        try { fly.body_text = String(await r.text()).slice(0, 400); } catch { /* body consumed */ }
      }
      fly.ok = healthy;
      // Reachable + healthy but sluggish is ONLINE-degraded, never offline. This is
      // the invariant this R-number exists to protect.
      fly.state = healthy
        ? (latency_ms > (budgets[0] ?? 4000) ? 'degraded_slow' : 'online')
        : 'degraded';
    } else {
      fly.ok = false;
      fly.state = 'degraded';        // it ANSWERED — that is not offline
      fly.error = `HTTP ${r.status}`;
    }
  } else if (timedOut) {
    fly.ok = null;                   // tri-state: could not measure
    fly.state = 'unknown';
    fly.reason = 'probe_timeout';
    fly.error = `${lastErr?.name}: probe exceeded ${budgets[budgets.length - 1]}ms `
      + '— NOT proof the brain is down';
  } else {
    fly.ok = false;
    fly.state = 'offline';           // transport-level refusal = real evidence
    fly.error = `${lastErr?.name}: ${(lastErr?.message || '').slice(0, 160)}`;
  }
  return fly;
}

/**
 * Combine node + fly into the top-level `ok`, preserving the tri-state: null when
 * the fly side could not be measured, so a caller can tell "we know they are
 * disconnected" from "we could not tell".
 */
export function combineCrossOk(nodeOk, flyOk) {
  if (flyOk === null) return null;
  return nodeOk === true && flyOk === true;
}

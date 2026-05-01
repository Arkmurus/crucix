// lib/util/throttle.mjs
//
// R-F21 2026-05-01: shared concurrency limiter + per-key circuit breaker.
//
// Live evidence motivating this:
//   09:39:33 [DefenseNews] 0/13 sources OK (every feed "all attempts failed")
//   09:39:32 [Portals] 4/28 markets covered (24 markets late or missing)
//   09:39:39 [Lusophone] Lusa Africa direct=err jina=451 rss2json=429 corsproxy=503
//
// Pattern: sweep fires Promise.allSettled bursts of 13 + 14 + 28 + ~5
// other sources concurrently. That's 60+ outbound HTTP calls in the
// same tick, each with up to 3 fallback attempts. Two failure modes:
//   a) seenode connection-pool / DNS contention — many requests time
//      out with "fetch failed" or "aborted due to timeout"
//   b) free RSS proxies (rss2json / allorigins / corsproxy) hit per-IP
//      quotas (429 / 503) when many feeds use them simultaneously
//
// Two complementary mitigations in this module:
//
//   withConcurrency(items, fn, { concurrency }) — cap N parallel fns
//     at once. Drains a queue serially per worker. Replaces the naive
//     `Promise.allSettled(items.map(fn))` pattern at burst sites.
//
//   shouldSkip / record — tiny in-memory circuit breaker keyed by
//     source name. After K consecutive failures, skip the source for
//     cooldownMs to stop wasted requests during outage windows. State
//     is per-process — container restart resets, which is desirable
//     (gives a fresh probe rather than locked-out state).

const _circuits = new Map();
// circuits: key → { failures: int, openedAt: number(epoch ms) | 0 }

const DEFAULT_FAILURE_THRESHOLD = 3;
const DEFAULT_COOLDOWN_MS = 5 * 60 * 1000;  // 5 minutes

/**
 * Returns true if the named circuit is open (skip the call). Also
 * auto-resets the circuit if the cooldown has elapsed, so the next
 * caller will probe again.
 */
export function shouldSkip(key, opts = {}) {
  const c = _circuits.get(key);
  if (!c) return false;
  const failureThreshold = opts.failureThreshold ?? DEFAULT_FAILURE_THRESHOLD;
  const cooldownMs       = opts.cooldownMs       ?? DEFAULT_COOLDOWN_MS;
  if (c.failures < failureThreshold) return false;
  if (Date.now() - c.openedAt >= cooldownMs) {
    _circuits.delete(key);
    return false;
  }
  return true;
}

/**
 * Bump the failure counter for `key`. If the threshold is reached
 * for the first time, stamp `openedAt` so cooldown starts ticking.
 */
export function recordFailure(key, opts = {}) {
  const failureThreshold = opts.failureThreshold ?? DEFAULT_FAILURE_THRESHOLD;
  const c = _circuits.get(key) || { failures: 0, openedAt: 0 };
  c.failures += 1;
  if (c.failures >= failureThreshold && c.openedAt === 0) {
    c.openedAt = Date.now();
  }
  _circuits.set(key, c);
}

/** Reset the circuit on a successful call. */
export function recordSuccess(key) {
  _circuits.delete(key);
}

/**
 * Run `fn(item, idx)` for every entry in `items` with at most
 * `concurrency` invocations in flight at once. Returns a Promise.allSettled-
 * shaped array of results in input order: { status: 'fulfilled', value }
 * or { status: 'rejected', reason }.
 */
export async function withConcurrency(items, fn, opts = {}) {
  const concurrency = Math.max(1, opts.concurrency ?? 5);
  const results = new Array(items.length);
  let nextIdx = 0;
  async function worker() {
    while (true) {
      const i = nextIdx++;
      if (i >= items.length) return;
      try {
        results[i] = { status: 'fulfilled', value: await fn(items[i], i) };
      } catch (err) {
        results[i] = { status: 'rejected', reason: err };
      }
    }
  }
  const workerCount = Math.min(concurrency, items.length);
  if (workerCount === 0) return results;
  await Promise.all(Array.from({ length: workerCount }, () => worker()));
  return results;
}

/** Snapshot for diagnostics — returns currently-open circuits. */
export function listOpenCircuits() {
  const out = [];
  const now = Date.now();
  for (const [key, c] of _circuits.entries()) {
    if (c.failures >= DEFAULT_FAILURE_THRESHOLD && c.openedAt > 0 && now - c.openedAt < DEFAULT_COOLDOWN_MS) {
      out.push({ key, failures: c.failures, openSinceMs: now - c.openedAt });
    }
  }
  return out;
}

// lib/util/attemptThrottle.mjs
//
// R-F3860 — bound the per-email attempt maps.
//
// server.mjs keeps three brute-force throttles with identical shape, all keyed by
// email address and all reachable UNAUTHENTICATED:
//
//   _loginAttempts   (R-F609 family)  email -> { count, firstAt, lockedUntil }
//   _verifyAttempts  (R-F3836)        same
//   _resetAttempts   (R-F609)         same
//
// Each pruned only the key it was currently touching, so an attacker cycling
// addresses grew them without bound — one entry per distinct address, kept for
// the life of the process. Request volume is capped by the strict rate-limit
// tier, so this is slow growth rather than a fast DoS, but "slow" on a
// long-running fly machine is still a leak, and there were three of them.
//
// This is the ONE bound. Fixing them individually would have left three
// implementations to drift apart, which is how the third one (R-F3836) came to
// be written with the same defect as the two that preceded it.
//
// Sweep-on-write rather than a timer: no interval to leak on reload, no work at
// all on an idle process, and the cost is paid by the caller that is growing the
// map. An entry older than `ttlMs` cannot affect any decision — every reader
// treats an aged-out window as absent — so dropping it is behaviour-preserving.

/** Hard ceiling per map. Chosen to be far above any legitimate working set. */
export const MAX_ATTEMPT_ENTRIES = 10000;

/**
 * Drop expired entries, then oldest-first until the map is within `maxEntries`.
 *
 * @param {Map<string, {firstAt?: number, lockedUntil?: number}>} map
 * @param {number} ttlMs      an entry older than this cannot affect a decision
 * @param {number} [now]
 * @param {number} [maxEntries]
 * @returns {number} entries removed
 */
export function pruneAttemptMap(map, ttlMs, now = Date.now(), maxEntries = MAX_ATTEMPT_ENTRIES) {
  if (!map || typeof map.forEach !== 'function') return 0;
  let removed = 0;

  for (const [k, v] of map) {
    const firstAt = v && typeof v.firstAt === 'number' ? v.firstAt : 0;
    const locked = v && typeof v.lockedUntil === 'number' ? v.lockedUntil : 0;
    // Keep anything still serving a lockout, however old its window is —
    // evicting it would hand an attacker a free reset.
    if (locked > now) continue;
    if (!firstAt || now - firstAt > ttlMs) {
      map.delete(k);
      removed += 1;
    }
  }

  if (map.size <= maxEntries) return removed;

  // Still over the ceiling: evict oldest-first. Map preserves insertion order,
  // but `firstAt` is the honest age, so sort by it rather than trusting order.
  const byAge = [...map.entries()]
    .filter(([, v]) => !(v && v.lockedUntil > now))
    .sort((a, b) => (a[1]?.firstAt || 0) - (b[1]?.firstAt || 0));
  for (const [k] of byAge) {
    if (map.size <= maxEntries) break;
    map.delete(k);
    removed += 1;
  }
  return removed;
}

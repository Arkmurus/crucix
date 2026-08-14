// lib/metrics/publicMetricsCache.mjs
// R-F4013 (C-90) — bound the public metrics endpoint's upstream calls.
//
// `GET /api/public/metrics` is UNAUTHENTICATED and asks the brain for a corpus
// count. It cached a SUCCESS for ten minutes and never cached a FAILURE:
//
//     if (records !== null) _publicMetricsCache = { at: now, value };
//
// So while the brain is slow, restarting (a ~10 minute boot) or down, every
// single anonymous request made its own upstream call, waited the full 8-second
// timeout, and wrote an errorTracker record. One landing-page visitor is one
// upstream call; a crawler is thousands. That is an amplification vector on a
// public route, a landing page that hangs for 8 seconds per visit exactly when
// the platform is already unwell, and a flood into the error ledger of the kind
// this repo has had to fix repeatedly.
//
// NOT A TIMEOUT BUMP. The 8-second bound stays; what changes is that a failure is
// remembered briefly instead of being rediscovered by every caller.
//
// THE TWO TTLs ARE DELIBERATELY DIFFERENT, and the asymmetry is the whole design:
//   * a SUCCESS is cheap to keep — the corpus count moves slowly, so ten minutes
//     of staleness costs nothing;
//   * a FAILURE must expire FAST, because caching "we could not measure it" for
//     ten minutes would hide a recovery for ten minutes. Thirty seconds bounds the
//     upstream call rate by ~20x under an outage while making the page correct
//     again within half a minute of the brain returning.
//
// Extracted from server.mjs because that file boots a live app on import, so the
// decision could otherwise only be grep-tested.

/** How long a real measurement stays fresh. */
export const SUCCESS_TTL_MS = 10 * 60 * 1000;

/**
 * How long a FAILED measurement is remembered.
 *
 * Short on purpose — see above. Never raise this to the success TTL "for
 * symmetry": that would mean a brain that recovered at 09:00 keeps showing an
 * empty figure until 09:10, which is the honest-but-stale failure mode the
 * R-F464 error-envelope work exists to avoid.
 */
export const FAILURE_TTL_MS = 30 * 1000;

/**
 * Is the cached entry still serveable?
 *
 * `entry` is `{ at, value }` where `value.records` is a number or null. A null
 * `records` is a REMEMBERED FAILURE, not a measurement, and expires on the short
 * TTL.
 */
export function isCacheFresh(entry, now = Date.now()) {
  if (!entry || !entry.value) return false;
  const ttl = entry.value.records === null ? FAILURE_TTL_MS : SUCCESS_TTL_MS;
  return (now - entry.at) < ttl;
}

/**
 * The entry to store after an attempt.
 *
 * Both outcomes are stored — that is the fix. The caller no longer decides
 * whether a result is worth remembering; it always is, for the appropriate
 * length of time.
 */
export function nextCacheEntry(records, now = Date.now()) {
  return {
    at: now,
    value: {
      records: Number.isFinite(records) && records > 0 ? records : null,
      generatedAt: new Date(now).toISOString(),
    },
  };
}

/**
 * Should this attempt call the brain at all?
 *
 * Pure wrapper so the route reads as one decision rather than two negations.
 */
export function shouldQueryUpstream(entry, now = Date.now()) {
  return !isCacheFresh(entry, now);
}

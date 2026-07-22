// lib/health/sourceBuckets.mjs
//
// R-F2867 — the source-health block of GET /api/health, factored out of server.mjs
// so the arithmetic is unit-testable (same reason R-F2765 factored enforceQuota:
// server.mjs is a 7k-line side-effecting module that cannot be imported into a
// fast test).
//
// WHAT WAS WRONG
// ──────────────
// /api/health shipped two of the five buckets and invented a denominator:
//
//     sourcesOk:     meta.sourcesOk     || 0
//     sourcesFailed: meta.sourcesFailed || 0
//     sourcesTotal:  meta.sourcesQueried || 36
//
// 1. `partial`, `suspended` and `not_configured` were invisible, so ok+failed did
//    not equal total. Live 2026-07-22: 46 ok / 0 failed / 50 total — four sources
//    unaccounted for. public/dashboard.html renders that as "46/50" with the
//    tooltip "N source(s) degraded", a claim it cannot support: a not_configured
//    source is not degraded, and a SUSPENDED one returned nothing at all while the
//    payload showed zero failures. R-F2853 removed exactly this shape from the
//    briefing payload; this is the same defect on the artefact customers read.
//
// 2. The `|| 36` fallback fabricated a total before the first sweep — observed live
//    after a restart as sourcesOk:0 / sourcesTotal:36. An invented denominator is
//    worse than none, because the dashboard divides by it.
//
// THE RULE HERE
// ─────────────
// Never present a count we did not measure. No sweep ⇒ nulls and `swept:false`,
// never a confident zero over an invented total. And any residual the known buckets
// do not explain is REPORTED as `sourcesUnaccounted` rather than absorbed — silent
// absorption is how this survived from R-F2853 until now.

/** Buckets that must, together, account for every queried source. */
const _BUCKETS = /** @type {const} */ ([
  ['sourcesOk', 'sourcesOk'],
  ['sourcesPartial', 'sourcesPartial'],
  ['sourcesFailed', 'sourcesFailed'],
  ['sourcesSuspended', 'sourcesSuspended'],
  ['sourcesNotConfigured', 'sourcesNotConfigured'],
]);

function _num(value) {
  const n = Number(value);
  return Number.isFinite(n) && n >= 0 ? n : 0;
}

/**
 * Build the source-health fields for GET /api/health.
 *
 * @param {object|null|undefined} meta - `currentData.meta` from the last sweep,
 *   or null/undefined when no sweep has produced data yet.
 * @returns {object} bucket counts, the total, an explicit `swept` flag, and the
 *   unexplained residual.
 */
export function buildHealthSourceBuckets(meta) {
  // A present meta means a sweep produced data — even if it queried nothing.
  // "Swept and found zero" is a real measurement; "never swept" is not, and
  // collapsing the two would re-hide precisely what this fixes.
  const swept = !!meta && typeof meta === 'object';

  if (!swept) {
    const out = { swept: false, sourcesTotal: null, sourcesUnaccounted: null };
    for (const [field] of _BUCKETS) out[field] = null;
    return out;
  }

  const out = { swept: true };
  let summed = 0;
  for (const [field, key] of _BUCKETS) {
    const n = _num(meta[key]);
    out[field] = n;
    summed += n;
  }
  out.sourcesTotal = _num(meta.sourcesQueried);
  // Positive residual only: a NEGATIVE difference would mean the buckets exceed the
  // total (double-counting), which is a different bug and must not be reported as
  // "unaccounted". Clamp at 0 so this field means exactly one thing.
  out.sourcesUnaccounted = Math.max(0, out.sourcesTotal - summed);
  return out;
}

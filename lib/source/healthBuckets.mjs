// R-F2719 — honest source-health bucketing for GET /api/source-health.
//
// Codex source-health audit #6: the endpoint counted `reliability === null` as
// HEALTHY (`healthyCount: …reliability === null || >= 80`). A null reliability means
// EITHER an unconfigured integration (Comtrade/CSL — never runs, `disabled > 0`,
// `ok+fail === 0`) OR one that has simply not been swept yet (`disabled === 0`).
// Neither is "healthy" — reporting them as healthy told the operator 47/50 were fine
// when 2 were unconfigured and never feeding anything.
//
// This pure classifier separates the real states so the surface can MEASURE health
// instead of asserting it. `summary` is the array from getSourceHealthSummary():
//   { name, ok, fail, disabled, reliability, ... }  (reliability = ok/(ok+fail)*100, or null)

export function classifySourceHealth(summary, healthyThreshold = 80) {
  const healthy = [];
  const degraded = [];
  const unconfigured = [];   // never runs — no API key / watchlist (disabled > 0)
  const notChecked = [];     // configured but not yet swept (no ok/fail/disabled samples)

  for (const s of (summary || [])) {
    const rel = s?.reliability;
    if (rel !== null && rel !== undefined) {
      (rel >= healthyThreshold ? healthy : degraded).push(s);
    } else if ((s?.disabled || 0) > 0) {
      unconfigured.push(s);
    } else {
      notChecked.push(s);
    }
  }

  const names = (arr) => arr.map((s) => s?.name).filter(Boolean);
  return {
    healthy, degraded, unconfigured, notChecked,
    healthyNames: names(healthy),
    degradedNames: names(degraded),
    unconfiguredNames: names(unconfigured),
    notCheckedNames: names(notChecked),
    counts: {
      total: (summary || []).length,
      healthy: healthy.length,
      degraded: degraded.length,
      unconfigured: unconfigured.length,
      notChecked: notChecked.length,
    },
  };
}

# R-F3237 — Sources page honesty audit

- Live unauthenticated probes confirmed `/sources.html` is operator-gated and the
  source APIs return authentication-required responses; no anonymous source data
  was treated as available.
- Root cause found: the catalogue panel retained stale R-F2721 copy claiming
  `web_atlas.record_ingest` had no caller and live coverage was zero.
- Current code has a real DD finalizer producer: `_record_source_reliability`
  records only attributable URLs from gate-cleared findings, with a finalizer
  hook and capability tests. The UI now says exactly that and distinguishes
  catalogue membership from measured observations.
- Existing source-health panels and API contracts passed; operational buckets
  keep healthy, degraded, unconfigured, and not-checked states distinct.
- Browser visual verification remains unavailable without a connected browser;
  production endpoint and served-HTML probes were used instead.

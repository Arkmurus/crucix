# ARIA observability — Grafana dashboard for `/metrics` (R-F1841)

The `/api/aria/metrics` endpoint (R-F1835, `aria_service/intel/metrics.py`) emits
Prometheus text. These files turn it into a usable dashboard.

## Files
- `aria-intel-dashboard.json` — Grafana dashboard (latency, in-flight requests,
  brain_hook p95 + breaker trips/drops, process RSS, LLM monthly cost vs the
  $300 cap). Metric names match `metrics.py` exactly.
- `prometheus-scrape.yml` — scrape config. **`/metrics` is auth-gated**, so the
  scrape sends the ARIA internal bearer token via a file (never inline the secret).

## What's needed to make this live (operator infra — NOT provisioned by this commit)
This delivers the **config artifacts**; it does not stand up the servers. To use:
1. A **Prometheus** instance that can reach `aria-intel` — merge `prometheus-scrape.yml`
   into its `scrape_configs` and drop the internal token at
   `/etc/prometheus/aria_token` (`chmod 600`).
2. A **Grafana** instance with that Prometheus added as a datasource — then
   **Dashboards → Import → Upload JSON** → `aria-intel-dashboard.json` and select
   the datasource.

No fly app, secret, or spend is changed here. If you'd rather not run
Prometheus/Grafana, the same numbers are available ad hoc via
`curl -H "Authorization: Bearer $ARIA_INTERNAL_TOKEN" https://aria-intel.fly.dev/api/aria/metrics`.

## Caveats (honest)
- **Mean, not true p95, for overall latency**: `/metrics` exposes a latency
  *sum + count*, not histogram buckets, so the "avg latency" panel is a mean.
  Tail latency is only available for `brain_hook` (it publishes a p95 gauge). To
  get a true overall p95, `metrics.py` would need to emit
  `aria_latency_ms_bucket{le=...}` (the registry already tracks buckets in
  `_metrics["latency_ms_buckets"]` but `generate_metrics()` does not yet export
  them) — a small follow-up.

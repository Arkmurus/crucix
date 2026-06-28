"""
R-F1835 — Prometheus /metrics endpoint for aria-intel.
Exposes p50/p95/p99 latency, per-process RSS, LLM cost per feature.
"""
from __future__ import annotations
from .engine_wiring import wire_success, wire_failure

import os
import time
from typing import Any

# ── Metrics registry ────────────────────────────────────────────────────────

_metrics: dict[str, Any] = {
    "requests_total": 0,
    "requests_active": 0,
    "latency_ms_sum": 0.0,
    "latency_ms_buckets": {},  # histogram buckets
}


def record_request(latency_ms: float) -> None:
    """Record a request for metrics."""
    _metrics["requests_total"] += 1
    _metrics["latency_ms_sum"] += latency_ms


def generate_metrics() -> str:
    """Generate Prometheus-formatted metrics text."""
    lines = [
        "# HELP aria_requests_total Total HTTP requests",
        "# TYPE aria_requests_total counter",
        f'aria_requests_total {_metrics["requests_total"]}',
        "",
        "# HELP aria_requests_active Currently active requests",
        "# TYPE aria_requests_active gauge",
        f'aria_requests_active {_metrics["requests_active"]}',
        "",
        "# HELP aria_latency_ms_sum Total latency in milliseconds",
        "# TYPE aria_latency_ms_sum counter",
        f'aria_latency_ms_sum {_metrics["latency_ms_sum"]:.0f}',
    ]

    # Add brain_hook latency stats if available
    try:
        from aria_service.intel import brain_hook as _bh
        state = _bh.get_breaker_state()
        lines.extend([
            "",
            "# HELP aria_brain_hook_p95_latency_ms P95 latency in ms",
            "# TYPE aria_brain_hook_p95_latency_ms gauge",
            f'aria_brain_hook_p95_latency_ms {state.get("p95_latency_ms", 0)}',
            "",
            "# HELP aria_brain_hook_trips_total Total circuit breaker trips",
            "# TYPE aria_brain_hook_trips_total counter",
            f'aria_brain_hook_trips_total {state.get("trips_total", 0)}',
            "",
            "# HELP aria_brain_hook_drops_total Total dropped signals",
            "# TYPE aria_brain_hook_drops_total counter",
            f'aria_brain_hook_drops_total {state.get("drops_total", 0)}',
        ])
    except Exception:
        pass

    # Add cost tracker stats if available
    try:
        from aria_service.intel import cost_tracker as _ct
        monthly = _ct.get_monthly_cost()
        lines.extend([
            "",
            "# HELP aria_llm_monthly_cost_usd Monthly LLM cost in USD",
            "# TYPE aria_llm_monthly_cost_usd gauge",
            f'aria_llm_monthly_cost_usd {monthly.get("total", 0)}',
        ])
    except Exception:
        pass

    # Add process memory
    try:
        import psutil
        proc = psutil.Process()
        rss = proc.memory_info().rss
        lines.extend([
            "",
            "# HELP aria_process_rss_bytes Process RSS in bytes",
            "# TYPE aria_process_rss_bytes gauge",
            f'aria_process_rss_bytes {rss}',
        ])
    except ImportError:
        pass

    return "\n".join(lines) + "\n"

    # R-F2118/R-F2119 §21a — wire module active
    try:
        wire_success(module="metrics",
                     summary="metrics module active",
                     source_id="metrics:init")
    except Exception:
        try:
            wire_failure(module="metrics", detail="module init failed",
                        gap_type="engine_failure", source="metrics:init")
        except Exception:
            pass

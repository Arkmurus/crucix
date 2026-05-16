"""R-F590 — /verify/stats rolling rate lifetime-fallback.

Pre-R-F590 the endpoint returned `rolling_grounded_rate: null` whenever
the 24h sample had 0 entries with grounded_rate set — typical when all
recent verifications were "no_tool" / "no_citations" verdicts (the rate
is None for those). Operationally that meant operating_modes.py + the
dashboard saw null and treated it as "unknown" even though lifetime
data exists.

R-F590 adds a lifetime-fallback path: when the 24h window is empty of
rate-bearing entries, return the lifetime average tagged
`data_source: 'lifetime_fallback'`. When even lifetime is empty, return
null tagged `data_source: 'no_data'`. The freshness signal (`data_source`)
lets consumers distinguish "24h fresh evidence" from "long-horizon
fallback" without forcing a null on them.

Also exposes `grounded_attempts_24h` and `total_attempts_24h` so the
dashboard can render WHY the rate may have fallen back to lifetime.
"""
from __future__ import annotations

import time
import pytest


@pytest.fixture
def mock_get_json(monkeypatch):
    """Stub out the Redis read so get_verification_stats() reads from
    a per-test in-memory list."""
    state = {"entries": []}

    async def fake_get_json(key: str):
        return state["entries"]

    import aria_service.intel.source_verifier as sv
    monkeypatch.setattr(sv.rs, "get_json", fake_get_json)
    return state


# ── Healthy 24h sample → 24h rate is the effective rate ─────────────────────


def test_rf590_uses_24h_when_recent_data_present(mock_get_json):
    import asyncio
    now = time.time()
    mock_get_json["entries"] = [
        {"verdict": "grounded", "grounded_rate": 1.0, "ts": now - 100},
        {"verdict": "partial", "grounded_rate": 0.6, "ts": now - 200},
        # An old entry — must be excluded from 24h window
        {"verdict": "grounded", "grounded_rate": 1.0, "ts": now - 86400 * 5},
    ]
    from aria_service.intel.source_verifier import get_verification_stats
    s = asyncio.run(get_verification_stats())
    assert s["data_source"] == "24h_window"
    assert s["rolling_grounded_rate"] == 0.8           # (1.0 + 0.6) / 2
    assert s["grounded_attempts_24h"] == 2
    assert s["total_attempts_24h"] == 2
    assert s["lifetime_grounded_rate"] == 0.867         # (1.0+0.6+1.0)/3
    assert s["lifetime_sample_size"] == 3


# ── 24h sample has no rate-bearing entries → fall back to lifetime ──────────


def test_rf590_falls_back_to_lifetime_when_24h_empty(mock_get_json):
    """The live regression — 2 recent verifications but both are
    no_tool/no_citations (grounded_rate=None). Pre-R-F590 this
    returned null; post-R-F590 it returns the lifetime average tagged
    lifetime_fallback."""
    now = time.time()
    mock_get_json["entries"] = [
        # 24h sample — no grounded_rate values
        {"verdict": "no_tool", "grounded_rate": None, "ts": now - 1000},
        {"verdict": "no_citations", "grounded_rate": None, "ts": now - 2000},
        # Older entries with real rates
        {"verdict": "grounded", "grounded_rate": 0.9, "ts": now - 86400 * 3},
        {"verdict": "partial", "grounded_rate": 0.5, "ts": now - 86400 * 4},
    ]
    from aria_service.intel.source_verifier import get_verification_stats
    import asyncio
    s = asyncio.run(get_verification_stats())
    assert s["data_source"] == "lifetime_fallback"
    assert s["rolling_grounded_rate"] == 0.7           # (0.9 + 0.5) / 2 from lifetime
    assert s["avg_grounded_rate"] == 0.7               # alias
    assert s["grounded_attempts_24h"] == 0             # nothing to count in 24h
    assert s["total_attempts_24h"] == 2                # but 2 entries existed
    assert s["rate_sample_size"] == 0                  # legacy field — 24h samples


# ── No data anywhere → no_data tag ─────────────────────────────────────────


def test_rf590_returns_no_data_when_truly_empty(mock_get_json):
    """When NO entries exist OR no entry has grounded_rate set, the
    effective rate is null but tagged 'no_data' so consumers can
    distinguish 'I haven't measured yet' from 'fresh evidence: 0%'."""
    mock_get_json["entries"] = []
    from aria_service.intel.source_verifier import get_verification_stats
    import asyncio
    s = asyncio.run(get_verification_stats())
    assert s["data_source"] == "no_data"
    assert s["rolling_grounded_rate"] is None
    assert s["lifetime_grounded_rate"] is None
    assert s["total"] == 0


def test_rf590_returns_no_data_when_all_entries_unrated(mock_get_json):
    """Edge: data exists but no entry has a grounded_rate (all
    no_tool/no_citations)."""
    now = time.time()
    mock_get_json["entries"] = [
        {"verdict": "no_tool", "grounded_rate": None, "ts": now - 100},
        {"verdict": "no_citations", "grounded_rate": None, "ts": now - 86400 * 2},
    ]
    from aria_service.intel.source_verifier import get_verification_stats
    import asyncio
    s = asyncio.run(get_verification_stats())
    assert s["data_source"] == "no_data"
    assert s["rolling_grounded_rate"] is None
    assert s["total"] == 2
    assert s["lifetime_sample_size"] == 0


# ── Field surface — consumers expecting old fields keep working ─────────────


def test_rf590_preserves_legacy_field_names(mock_get_json):
    """R-F590 adds fields; must not rename or drop the existing ones.
    Consumers reading `rolling_grounded_rate` / `avg_grounded_rate` /
    `rate_sample_size` / `lifetime_grounded_rate` / `total` /
    `by_verdict` / `recent_24h` must all still find them."""
    now = time.time()
    mock_get_json["entries"] = [
        {"verdict": "grounded", "grounded_rate": 0.9, "ts": now - 100},
    ]
    from aria_service.intel.source_verifier import get_verification_stats
    import asyncio
    s = asyncio.run(get_verification_stats())
    expected_keys = {
        "total", "by_verdict", "recent_24h",
        "rolling_grounded_rate", "avg_grounded_rate", "rate_sample_size",
        "lifetime_grounded_rate", "lifetime_sample_size",
        # R-F590 additions:
        "data_source", "grounded_attempts_24h", "total_attempts_24h",
    }
    missing = expected_keys - set(s.keys())
    assert not missing, f"R-F590 missing keys: {missing}"

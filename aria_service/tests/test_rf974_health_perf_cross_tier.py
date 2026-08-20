"""R-F974 — health_perf_ep surfaces a cross-tier data-output picture.

Pre-R-F974 the self-introspection endpoint was Python-brain-only: ARIA could
cite knowledge_facts but had NO field for what the Node OSINT sweep ingested
or how many WhatsApp messages she captured. So self_introspect could not
honestly answer "what am I outputting across my ecosystem?".

R-F974 adds a top-level `cross_tier` block (node_sweep + whatsapp_messages
+ per-module cross-tier signal counts) and bumps the schema to rf974.v1.
"""
from __future__ import annotations

import asyncio
import pathlib

from ._source_probe import repo_path


def _perf_block() -> str:
    src = pathlib.Path(
        repo_path("aria_service/routes/aria.py")
    ).read_text(encoding="utf-8", errors="ignore")
    idx = src.find("async def health_perf_ep")
    assert idx > 0
    nxt = src.find('@router.get("/health/cross")', idx + 1)
    return src[idx:nxt if nxt > 0 else idx + 9000]


# ── Schema contract (static source assertions) ──────────────────────

def test_rf974_schema_version_bumped():
    block = _perf_block()
    assert '"_schema_version": "rf4208.v1"' in block, (
        "R-F974/F4208 regression: schema version did not bump to rf4208.v1, so "
        "consumers won't know the cross_tier field exists."
    )


def test_rf974_response_includes_cross_tier_block():
    block = _perf_block()
    assert '"cross_tier": cross_tier' in block, (
        "R-F974 regression: cross_tier key missing from the response dict."
    )


def test_rf974_cross_tier_has_three_fields():
    block = _perf_block()
    for field in ("node_sweep", "whatsapp_messages_captured", "cross_tier_signals"):
        assert f'"{field}"' in block, f"R-F974: cross_tier field {field!r} missing."


def test_rf974_each_cross_tier_probe_is_defensive():
    """Each cross-tier probe (current_data / compliance_watch / brain_hook)
    must be wrapped so a single missing store can't break introspection."""
    block = _perf_block()
    ct_start = block.find("cross_tier: dict")
    ct_end = block.find("return {", ct_start)
    section = block[ct_start:ct_end]
    assert section.count("try:") >= 3, (
        "R-F974: cross_tier needs 3+ try/except blocks (sweep / WA / brain_hook)."
    )
    assert section.count("except Exception:") >= 3


# ── Behavioural: the endpoint returns the contract ──────────────────

def test_rf974_endpoint_returns_cross_tier_key():
    """Call health_perf_ep() live and assert the cross_tier contract holds
    even when sub-stores are empty (the probes degrade to None, not raise)."""
    from aria_service.routes.aria import health_perf_ep
    out = asyncio.run(health_perf_ep())
    assert isinstance(out, dict)
    assert "cross_tier" in out, "cross_tier missing from live health_perf_ep output"
    ct = out["cross_tier"]
    assert set(ct.keys()) >= {
        "node_sweep", "whatsapp_messages_captured", "cross_tier_signals"
    }
    assert out.get("_schema_version") == "rf4208.v1"

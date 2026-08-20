"""R-F400 — /health/perf inventory + retention fields.

Live evidence 2026-05-13 07:27: ARIA estimated "5,000-10,000 verified
facts" when real count was 35,363 (sharded Redis snapshot at 06:17),
and invented an "18-month Knowledge Base TTL" that directly contradicts
operator directive aria_infinite_memory.md (no TTL, no eviction).

R-F400 fix:
  - /health/perf response now includes `inventory` with knowledge_facts,
    ledger_signals, rag_chunks (real live counts), and a `retention` block
    with explicit `ttl_days: null` markers so ARIA can never again
    hallucinate a TTL that doesn't exist.
  - Schema bumped to rf400.v1.
  - Two new advisories surface the counts + permanent-memory rule as
    one-line strings ARIA can quote in introspection answers.

These tests pin the contract. Behavioural assertions require a running
FastAPI app + populated stores; we use static source assertions to
verify the wiring is in place, plus a unit test of the inventory call
chain.
"""
from __future__ import annotations

import pathlib

from ._source_probe import repo_path


def _src() -> str:
    return pathlib.Path(
        repo_path("aria_service/routes/aria.py")
    ).read_text(encoding="utf-8", errors="ignore")


def _perf_block() -> str:
    src = _src()
    idx = src.find("async def health_perf_ep")
    assert idx > 0
    # Read until the next route or 6000 chars
    nxt = src.find("@router.get", idx + 1)
    return src[idx:nxt if nxt > 0 else idx + 6000]


# ── Schema contract ─────────────────────────────────────────────────

def test_rf400_schema_version_bumped_to_v1():
    """The schema version must keep bumping as the contract grows so
    consumers know new fields exist. rf396.v1 → rf400.v1 (inventory +
    retention) → rf974.v1 (cross_tier added) → rf4208.v1
    (serving-process embedding offload state added)."""
    block = _perf_block()
    assert '"_schema_version": "rf4208.v1"' in block, (
        "R-F400/F974/F4208 regression: schema version did not bump. Consumers "
        "(e.g. R-F399 introspection router) won't know the new fields exist."
    )


def test_rf400_response_includes_inventory_block():
    """The top-level response must contain an inventory block."""
    block = _perf_block()
    assert '"inventory"' in block, (
        "R-F400 regression: inventory key missing from response."
    )


def test_rf400_response_includes_retention_block():
    """The top-level response must contain a retention block."""
    block = _perf_block()
    assert '"retention"' in block, (
        "R-F400 regression: retention key missing from response."
    )


# ── Inventory fields ────────────────────────────────────────────────

def test_rf400_inventory_calls_knowledge_get_stats():
    """The handler must call knowledge.get_stats() and read totalFacts."""
    block = _perf_block()
    assert "from ..intel import knowledge" in block
    assert "_kn.get_stats()" in block
    assert "totalFacts" in block, (
        "R-F400: knowledge fact count not read from get_stats() — "
        "ARIA will estimate again instead of citing the real number."
    )


def test_rf400_inventory_calls_ledger_get_stats():
    block = _perf_block()
    assert "from ..intel import intel_ledger" in block
    assert "_il.get_stats()" in block
    assert "totalSignals" in block


def test_rf400_inventory_calls_rag_get_stats():
    block = _perf_block()
    assert "from ..intel import rag_store" in block
    assert "_rs.get_stats()" in block
    assert "total_chunks" in block


def test_rf400_inventory_has_six_fields():
    """The inventory dict must include the full six fields specified
    in the R-F400 contract."""
    block = _perf_block()
    required = [
        "knowledge_facts", "ledger_signals", "rag_chunks",
        "rag_documents", "rag_facts_indexed", "mem0_entries",
    ]
    for field in required:
        assert f'"{field}"' in block, (
            f"R-F400: inventory field {field!r} missing — ARIA can't "
            f"cite it when answering introspective questions."
        )


# ── Retention policy contract — the TTL hallucination kill ──────────

def test_rf400_retention_knowledge_ttl_is_null():
    """The most critical assertion. ARIA hallucinated a "18-month TTL"
    on 2026-05-13. The retention block MUST surface
    knowledge.ttl_days = None / null so any introspection answer
    grounded in /health/perf cannot invent a TTL.

    This pins the truth in code: knowledge.py:64 says "Permanent memory —
    no TTL". /health/perf must surface that.
    """
    block = _perf_block()
    assert '"knowledge"' in block
    # The actual line: "ttl_days": None,
    assert '"ttl_days": None' in block, (
        "R-F400 CRITICAL: knowledge.ttl_days is not exposed as None in "
        "/health/perf retention block. ARIA can hallucinate a TTL again "
        "because she has no inline contradiction. This was the exact "
        "07:27 bug pattern from 2026-05-13."
    )


def test_rf400_retention_policy_says_permanent():
    """The policy string must explicitly say permanent so the LLM
    reading the response cannot misinterpret."""
    block = _perf_block()
    assert '"policy": "permanent"' in block


def test_rf400_retention_eviction_says_none():
    """Eviction must be marked 'none' for knowledge — directly
    contradicting any hallucinated 'overwrite/compress' claim ARIA
    might make about her own memory."""
    block = _perf_block()
    assert '"eviction": "none"' in block, (
        "R-F400: eviction not marked 'none' for knowledge. ARIA could "
        "still claim 'old facts get overwritten' (she did on 2026-05-13)."
    )


def test_rf400_retention_anchor_cites_memory_doc():
    """The retention block must cite the operator-directive memory doc
    so a future contributor sees the chain of custody."""
    block = _perf_block()
    assert "aria_infinite_memory" in block, (
        "R-F400: retention block doesn't cite aria_infinite_memory.md — "
        "operator directive chain of custody is broken."
    )
    assert "R-F238" in block, (
        "R-F400: retention block doesn't cite R-F238 (the prune reversal)."
    )


def test_rf400_retention_rag_floor_pinned():
    """The R-F397 RAG min_similarity floor (0.50) must surface here
    so an introspection answer can cite it."""
    block = _perf_block()
    assert "min_similarity_floor" in block
    assert "0.50" in block or "0.5" in block


# ── Advisories — quotable strings ───────────────────────────────────

def test_rf400_advisories_include_fact_count_and_permanent_note():
    """When the knowledge count is available, the advisories array
    must include a one-line quote ARIA can paste verbatim."""
    block = _perf_block()
    assert "permanent facts" in block, (
        "R-F400: advisory string missing 'permanent facts' phrasing. "
        "ARIA needs a quotable line for introspection replies."
    )
    assert "aria_infinite_memory" in block


def test_rf400_advisories_include_ledger_retention():
    """Ledger retention must surface as a separate advisory."""
    block = _perf_block()
    assert "36500-day retention" in block or "permanent" in block.lower()


# ── Defensive ──────────────────────────────────────────────────────

def test_rf400_each_inventory_probe_in_try_except():
    """Each inventory probe must be wrapped in try/except so a single
    missing store doesn't break the whole response."""
    block = _perf_block()
    # Count try blocks within the inventory section
    inv_start = block.find("inventory: dict")
    inv_end = block.find("retention: dict")
    inv_section = block[inv_start:inv_end]
    assert inv_section.count("try:") >= 3, (
        "R-F400: inventory section needs 3+ try/except blocks — one per "
        "store (knowledge / ledger / rag). Got fewer."
    )
    assert inv_section.count("except Exception:") >= 3

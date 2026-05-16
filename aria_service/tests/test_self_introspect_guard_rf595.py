"""R-F595 — Auto-fire self_introspect on self-capability questions.

Live evidence 2026-05-16: operator asked "share an overview of your full
system capabilities" — ARIA emitted "34 autonomous tasks", "48/49 sources
OK", "Officeholder Verification 18-month TTL". All fabrications. Real
values: 78 / 431 / no TTL (permanent).

The guard pre-detects capability questions in the user message and
fetches /health/perf BEFORE the LLM call so the LLM sees real numbers.
"""
from __future__ import annotations


# ══════════════════════════════════════════════════════════════════
# PART A — keyword detection
# ══════════════════════════════════════════════════════════════════

def test_rf595_detects_capability_overview():
    from aria_service.intel.self_introspect_guard import (
        detect_self_capability_question,
    )
    for msg in (
        "could you share an overview of your full system capabilities?",
        "What can you do?",
        "What are your sources?",
        "How many autonomous tasks are running?",
        "How many sources do you have?",
        "Tell me about your architecture",
        "Self-assessment please",
        "gap analysis of your capabilities",
        "give me a system overview",
        "How do you work?",
        "What signals are in your ledger?",
        "Your current state please",
    ):
        assert detect_self_capability_question(msg), (
            f"R-F595: failed to detect capability question {msg!r}"
        )


def test_rf595_does_not_overdetect_on_unrelated_messages():
    from aria_service.intel.self_introspect_guard import (
        detect_self_capability_question,
    )
    for msg in (
        "Is Lukoil Neftochim Burgas on the OFSI list?",
        "Run a deep crawl on modirumgespi.com",
        "What is the export licence for category A military goods?",
        "Compose a brief on the Angola FAA tender",
        "Hello",
        "Thanks",
        "Ghana opportunity update?",
    ):
        assert not detect_self_capability_question(msg), (
            f"R-F595 over-detect: {msg!r} should NOT trigger"
        )


def test_rf595_handles_empty_and_none_inputs():
    from aria_service.intel.self_introspect_guard import (
        detect_self_capability_question,
    )
    assert detect_self_capability_question("") is False
    assert detect_self_capability_question(None) is False  # type: ignore[arg-type]


# ══════════════════════════════════════════════════════════════════
# PART B — context block generation
# ══════════════════════════════════════════════════════════════════

def test_rf595_context_block_empty_for_non_capability_question():
    """Non-capability messages → empty string, no /health/perf call."""
    import asyncio
    from aria_service.intel.self_introspect_guard import (
        self_introspect_context_block,
    )
    assert asyncio.run(
        self_introspect_context_block("Is Lukoil sanctioned in the UK?")
    ) == ""


def test_rf595_context_block_returns_self_introspect_block_on_match(monkeypatch):
    """Capability message → block prefixed [TOOL: self_introspect]."""
    import asyncio

    async def _fake_perf():
        return {
            "inventory": {
                "knowledge_facts": 35363,
                "ledger_signals": 31498,
                "rag_chunks": 12500,
                "rag_documents": 2747,
                "rag_facts_indexed": 504,
            },
            "retention": {
                "knowledge": {
                    "ttl_days": None,
                    "policy": "permanent",
                    "eviction": "none",
                    "anchor": "aria_infinite_memory.md + R-F238",
                },
                "ledger": {
                    "retention_days": 36500,
                    "policy": "permanent",
                    "eviction": "none",
                },
            },
            "autonomy": {"scheduled_tasks": 78, "tasks_enabled": 12},
            "advisories": ["Live build: R-F595"],
        }

    # Patch the lazy import target on the actual module.
    from aria_service.routes import aria as _aria_route
    monkeypatch.setattr(_aria_route, "health_perf_ep", _fake_perf)

    from aria_service.intel.self_introspect_guard import (
        self_introspect_context_block,
    )
    block = asyncio.run(
        self_introspect_context_block(
            "share an overview of your full system capabilities"
        )
    )
    assert "[TOOL: self_introspect" in block
    # Real numbers must appear verbatim
    assert "35,363" in block, "knowledge_facts not rendered"
    assert "31,498" in block, "ledger_signals not rendered"
    assert "scheduled_tasks: 78" in block, "scheduled_tasks not rendered"
    # TTL must be cited as None (anti-hallucination anchor)
    assert "ttl_days=None" in block, "ttl_days=None missing"
    # Anchor must appear so the LLM cites it
    assert "aria_infinite_memory.md" in block
    # Answer policy must instruct the LLM to ONLY quote these values
    assert "R-F595" in block
    assert "Clause 25" in block


def test_rf595_context_block_handles_health_perf_failure(monkeypatch):
    """If /health/perf throws, the block must still emit a guard notice
    telling the LLM not to invent inventory numbers."""
    import asyncio

    async def _broken_perf():
        raise RuntimeError("redis down")

    from aria_service.routes import aria as _aria_route
    monkeypatch.setattr(_aria_route, "health_perf_ep", _broken_perf)

    from aria_service.intel.self_introspect_guard import (
        self_introspect_context_block,
    )
    block = asyncio.run(
        self_introspect_context_block("how many sources do you have?")
    )
    assert "[TOOL: self_introspect" in block
    assert "health_perf failed" in block
    assert "Do NOT invent" in block


def test_rf595_context_block_handles_missing_inventory_keys(monkeypatch):
    """When health_perf returns sparse data, the block must say
    UNAVAILABLE — never fabricate."""
    import asyncio

    async def _sparse_perf():
        return {"inventory": {}, "retention": {}, "autonomy": {}, "advisories": []}

    from aria_service.routes import aria as _aria_route
    monkeypatch.setattr(_aria_route, "health_perf_ep", _sparse_perf)

    from aria_service.intel.self_introspect_guard import (
        self_introspect_context_block,
    )
    block = asyncio.run(
        self_introspect_context_block("what are your capabilities?")
    )
    assert "UNAVAILABLE" in block, (
        "R-F595: sparse data must surface UNAVAILABLE so the LLM "
        "knows not to invent counts."
    )

"""R-F599 — reasoning_library cache bypass for self-capability questions.

Past incident 2026-05-16 18:45 WhatsApp: operator asked "Aria, could
you share an overview of your full system capabilities?". The
reasoning_router served a stale reasoning_library cache hit with
hallucinated content ("34 autonomous tasks", "48/49 sources",
"Officeholder Verification 18-month TTL"). The R-F594 post-scan guard
caught the violations and appended a warning block, but the cached
reply was already delivered.

R-F595's self_introspect_guard only fires on the cloud-LLM call path —
the cache bypassed it entirely. R-F599 adds a Stage-0.5 bypass to
reasoning_router.try_local_reasoning so capability questions skip
symbolic / library / local_brain / ollama and escalate to cloud LLM,
where R-F595 injects live /health/perf data.

This file is the pin: when a capability question reaches the router,
try_local_reasoning must return answered=False with reason
"self_capability_bypass".
"""
from __future__ import annotations

import asyncio
import pathlib


# ══════════════════════════════════════════════════════════════════
# PART A — the bypass MUST fire on capability questions
# ══════════════════════════════════════════════════════════════════

_CAPABILITY_QUESTIONS = (
    "could you share an overview of your full system capabilities?",
    "What can you do?",
    "What are your sources?",
    "How many autonomous tasks do you have?",
    "Tell me about your architecture",
    "self-assessment please",
    "gap analysis of your capabilities",
    "give me a system overview",
    "How do you work?",
    "what signals are in your ledger?",
)


def test_rf599_bypass_fires_on_capability_question():
    """try_local_reasoning must return answered=False with reason
    'self_capability_bypass' for every capability question."""
    from aria_service.intel.reasoning_router import try_local_reasoning
    for q in _CAPABILITY_QUESTIONS:
        result = asyncio.run(try_local_reasoning(q))
        assert result.get("answered") is False, (
            f"R-F599: capability question {q!r} was answered locally — "
            f"should have escalated to cloud LLM. Got: {result}"
        )
        assert result.get("reason") == "self_capability_bypass", (
            f"R-F599: bypass reason for {q!r} was {result.get('reason')!r}, "
            f"expected 'self_capability_bypass'"
        )


def test_rf599_bypass_trace_includes_stage_marker():
    """The trace must show the bypass stage so the operator can verify
    the routing decision in /trace output."""
    from aria_service.intel.reasoning_router import try_local_reasoning
    result = asyncio.run(try_local_reasoning(
        "share an overview of your full system capabilities"
    ))
    trace = result.get("trace") or []
    stages = {t.get("stage") for t in trace}
    assert "self_capability_bypass" in stages, (
        f"R-F599: trace missing self_capability_bypass stage. Got: {stages}"
    )


# ══════════════════════════════════════════════════════════════════
# PART B — must NOT fire on unrelated questions
# ══════════════════════════════════════════════════════════════════

_NON_CAPABILITY_QUESTIONS = (
    "Is Lukoil sanctioned in the UK?",
    "Run a deep crawl on modirumgespi.com",
    "What is the export licence for category A military goods?",
    "Compose a brief on the Angola FAA tender",
    "How much is Brent crude trading at?",
)


def test_rf599_does_not_overfire_on_normal_questions():
    """Non-capability questions must NOT trigger the bypass — they
    should proceed through symbolic / library / etc. normally."""
    from aria_service.intel.reasoning_router import try_local_reasoning
    for q in _NON_CAPABILITY_QUESTIONS:
        result = asyncio.run(try_local_reasoning(q))
        assert result.get("reason") != "self_capability_bypass", (
            f"R-F599 over-fire: {q!r} triggered capability bypass "
            f"when it should have proceeded to symbolic / library."
        )


# ══════════════════════════════════════════════════════════════════
# PART C — bypass must run BEFORE the library lookup
# ══════════════════════════════════════════════════════════════════

def test_rf599_bypass_runs_before_library_stage_in_code():
    """Source-level pin: the bypass block must appear in
    try_local_reasoning BEFORE the reasoning_library lookup, otherwise
    the library still serves first and the bug regresses."""
    src = pathlib.Path(
        "C:/code/crucix/aria_service/intel/reasoning_router.py"
    ).read_text(encoding="utf-8", errors="ignore")
    fn_idx = src.find("async def try_local_reasoning")
    assert fn_idx > 0, "try_local_reasoning not found"
    body = src[fn_idx:fn_idx + 8000]
    bypass_idx = body.find("self_capability_bypass")
    library_idx = body.find("reasoning_library.find_match")
    assert bypass_idx > 0, "R-F599 bypass marker not in try_local_reasoning"
    assert library_idx > 0, "reasoning_library.find_match call not found"
    assert bypass_idx < library_idx, (
        "R-F599 CRITICAL: bypass runs AFTER reasoning_library — the "
        "cache regression returns."
    )


def test_rf599_bypass_runs_before_symbolic_stage_in_code():
    """Same pin for symbolic_reasoner — bypass must precede it."""
    src = pathlib.Path(
        "C:/code/crucix/aria_service/intel/reasoning_router.py"
    ).read_text(encoding="utf-8", errors="ignore")
    fn_idx = src.find("async def try_local_reasoning")
    body = src[fn_idx:fn_idx + 8000]
    bypass_idx = body.find("self_capability_bypass")
    sym_idx = body.find("symbolic_reasoner.reason")
    assert bypass_idx < sym_idx, (
        "R-F599: bypass must run BEFORE symbolic_reasoner.reason()"
    )


# ══════════════════════════════════════════════════════════════════
# PART D — detector failure must NOT break the chat turn
# ══════════════════════════════════════════════════════════════════

def test_rf599_detector_exception_does_not_break_router(monkeypatch):
    """If self_introspect_guard.detect_self_capability_question raises,
    the router must continue to the next stage — never propagate the
    error to the chat reply path."""
    from aria_service.intel import self_introspect_guard as _sig

    def _broken_detector(_msg):
        raise RuntimeError("detector broken")

    monkeypatch.setattr(_sig, "detect_self_capability_question", _broken_detector)

    from aria_service.intel.reasoning_router import try_local_reasoning
    # Use a query that won't trip self_infra_bypass either.
    result = asyncio.run(try_local_reasoning(
        "Is Lukoil sanctioned in the UK?"
    ))
    # We don't care WHAT the eventual stage answers — only that the
    # call returned without propagating the detector exception.
    assert isinstance(result, dict)
    assert "answered" in result

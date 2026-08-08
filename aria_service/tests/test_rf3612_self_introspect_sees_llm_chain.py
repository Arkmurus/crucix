"""R-F3612 — self_introspect was blind to the LLM chain.

THE LIVE SYMPTOM (operator WhatsApp, 2026-08-01, during the R-F3606 outage):

    Antonio: "Aria, what are the issue with your current command centre?
              You are not answering my question?"
    Antonio: "let me rephrase it, what is the current issues you are
              experiencing with your system?"
    Aria:    "...the system is currently healthy and has no active blocking
              events in the last 24 hours ... 0 new blocking disagreements,
              0 warnings, and 0 verified or unverified facts"

Every chat turn was failing at that moment. She was not lying: the
[TOOL: self_introspect] block she was handed had sections for STATUS, BUILD,
INVENTORY, AUTONOMY, CODER, RETENTION and ADVISORIES — and NOTHING about the
LLM chain. `health_perf_ep` produces `llm_providers` (routes/aria.py:26487) and
BOTH self_introspect surfaces dropped it: a producer with no carrier.

A sibling tool already had the data — `meta_query` renders "LLM FALLBACK CHAIN
(currently serving you)" from get_health() — so whether ARIA could see her own
outage depended on which tool she happened to pick.

Two traps this test pins down:
  1. `llm_providers` alone would NOT have caught it. available = configured and
     breaker != OPEN; DeepSeek stayed configured and never opened a breaker
     (R-F3591 raises a RETRYABLE error), so that field read True throughout.
     The honest signal is get_health().resilient (R-F3477, outcome-based).
  2. All-zero 24h counters were cited as EVIDENCE OF HEALTH. Zero activity is a
     symptom — the same absence-as-proof error that certified Phase A gates on
     keys nothing wrote.
"""
import asyncio

from aria_service.intel.self_introspect_guard import (
    detect_self_capability_question,
    llm_chain_health_lines,
    zero_activity_warning_lines,
)

# R-F3789/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import function_source


def _run(coro):
    return asyncio.run(coro)


class _FakeChain:
    """Stands in for FallbackLLM at app.state.llm_provider."""

    def __init__(self, health):
        self._health = health

    def get_health(self):
        return self._health


# The exact shape fallback.get_health() returns (fallback.py:854-861).
_OUTAGE_HEALTH = {
    "active_providers": ["deepseek"],
    "cooling_providers": [],
    "resilient": False,
    "last_exhaustion_age_s": 12.4,
    "primary_active": True,
    "serving_provider": "deepseek",
    "chain_order": ["deepseek", "deepseek_backup"],
}

_HEALTHY_HEALTH = {
    "active_providers": ["deepseek"],
    "cooling_providers": [],
    "resilient": True,
    "last_exhaustion_age_s": None,
    "primary_active": True,
    "serving_provider": "deepseek",
    "chain_order": ["deepseek"],
}

# What get_provider_status() reported DURING the outage — the false-clean trap.
_PERF_DURING_OUTAGE = {
    "llm_providers": {
        "deepseek": {"configured": True, "breaker_state": None, "available": True},
        "aria_llm": {"configured": True, "breaker_state": None, "available": True},
    },
    "verification_24h": {"verified": 0, "unverified": 0, "blocking": 0, "warnings": 0},
}


def _install_chain(monkeypatch, health):
    import aria_service.main as _main
    monkeypatch.setattr(_main.app.state, "llm_provider", _FakeChain(health),
                        raising=False)


# ── The instrument itself ────────────────────────────────────────────────────


def test_the_operators_actual_question_is_detected_as_introspection():
    """Verify the instrument: if the detector misses the question, the whole
    block never fires and nothing below matters.

    FAILED BEFORE THE FIX — and this is a finding in its own right. Both of the
    operator's real phrasings returned False, so the auto-fired /health/perf
    block was never injected for the one question that most needs it. A richer
    chain-health section would have been useless on this path without it.
    """
    assert detect_self_capability_question(
        "what is the current issues you are experiencing with your system?"
    )
    assert detect_self_capability_question(
        "Aria, what are the issue with your current command centre?"
    )
    # other natural forms of the same question
    assert detect_self_capability_question("what's wrong with you?")
    assert detect_self_capability_question("are you experiencing any problems?")
    assert detect_self_capability_question("is anything broken with your setup?")


def test_the_fault_detector_does_not_fire_on_third_party_problems():
    """The widened pattern must not turn every mention of a problem into a
    self-introspection turn — it is anchored on a fault word followed by a
    reference to ARIA herself."""
    assert not detect_self_capability_question(
        "what are the issues with the Korvera contract?"
    )
    assert not detect_self_capability_question(
        "summarise the problems found in the due diligence report"
    )
    assert not detect_self_capability_question(
        "is there a problem with the Bulgarian entity's filings?"
    )


# ── THE CAPABILITY TEST ──────────────────────────────────────────────────────


def test_capability_a_dead_chain_can_no_longer_read_as_no_issues(monkeypatch):
    """FAILS BEFORE THE FIX: the rendered block contained no LLM-chain section
    at all, so 'what issues are you experiencing?' was answerable as 'none'."""
    _install_chain(monkeypatch, _OUTAGE_HEALTH)

    block = "\n".join(llm_chain_health_lines(_PERF_DURING_OUTAGE))

    assert "LLM CHAIN HEALTH" in block
    assert "resilient: False" in block
    assert "CHAIN EXHAUSTED" in block, "a recent total exhaustion must be stated"
    assert "12.4" in block, "the exhaustion age must be quoted, not summarised away"
    # and the model is told, in words, that this IS the answer
    assert "not say" in block.lower() or "do not say" in block.lower()


def test_configured_slots_alone_cannot_certify_health(monkeypatch):
    """The trap. During the outage every slot read configured=True with no OPEN
    breaker. If the block rendered that without a caveat it would have produced
    the SAME false clean from a new source."""
    _install_chain(monkeypatch, _OUTAGE_HEALTH)
    block = "\n".join(llm_chain_health_lines(_PERF_DURING_OUTAGE))

    assert "configured=True" in block          # rendered as colour...
    assert "does NOT" in block                 # ...but explicitly not proof
    assert "resilient" in block


def test_unreadable_chain_reports_unavailable_never_healthy(monkeypatch):
    """Tri-state: could-not-measure is not measured-and-fine."""
    import aria_service.main as _main
    monkeypatch.setattr(_main.app.state, "llm_provider", None, raising=False)

    block = "\n".join(llm_chain_health_lines({}))
    assert "UNAVAILABLE" in block
    assert "CANNOT conclude" in block or "could not be measured" in block


def test_a_healthy_chain_still_reads_healthy(monkeypatch):
    """The guard must not cry wolf — a genuinely resilient chain reports so."""
    _install_chain(monkeypatch, _HEALTHY_HEALTH)
    block = "\n".join(llm_chain_health_lines({}))
    assert "resilient: True" in block
    assert "CHAIN EXHAUSTED" not in block


# ── Zero-activity is a symptom, not a clean bill ─────────────────────────────


def test_all_zero_counters_are_flagged_as_a_symptom():
    lines = zero_activity_warning_lines(
        {"verified": 0, "unverified": 0, "blocking": 0, "warnings": 0}
    )
    text = "\n".join(lines)
    assert "ZERO-ACTIVITY" in text
    assert "NOT evidence of health" in text


def test_normal_activity_produces_no_warning():
    assert zero_activity_warning_lines({"verified": 12, "unverified": 3}) == []
    assert zero_activity_warning_lines({}) == []
    assert zero_activity_warning_lines(None) == []


# ── Both surfaces share ONE renderer (they drifted before) ───────────────────


def test_the_auto_fired_guard_block_includes_the_chain_section():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "intel"
           / "self_introspect_guard.py").read_text(encoding="utf-8")
    i = src.index('lines += ["", "INVENTORY:"]')
    assert "llm_chain_health_lines(perf)" in src[:i], (
        "the auto-fired block must render chain health BEFORE inventory"
    )


def test_the_self_introspect_tool_uses_the_same_shared_renderer():
    """The tool handler and the guard block are the two surfaces that answered
    the operator. A second inline copy is how they diverged from meta_query."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "routes"
           / "aria.py").read_text(encoding="utf-8")
    i = src.index('if tool == "self_introspect":')
    block = src[i:i + 6000]
    assert "llm_chain_health_lines" in block, (
        "the self_introspect tool must render LLM chain health"
    )
    assert "zero_activity_warning_lines" in block


def test_get_health_key_names_still_exist():
    """Verify the instrument: this renderer reads get_health() by key name, and
    an invented key would render an outage as SILENCE. Pin the contract."""
    import inspect
    from aria_service.llm import fallback as fb
    # NB: the class is FallbackProvider, not FallbackLLM — verified at
    # fallback.py:113. Guessing the name cost a red test here; that is the
    # §3b rule doing its job.
    src = function_source(fb.FallbackProvider, "get_health")
    for key in ("resilient", "last_exhaustion_age_s", "serving_provider",
                "active_providers", "cooling_providers"):
        assert f'"{key}"' in src, f"get_health() no longer returns {key}"

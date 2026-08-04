"""Tests for verification-gate wiring into DD + chat.

Covers:
  - pick_secondary_provider selects a different provider + skips cooling
  - double_via_fallback returns named primary + secondary
  - ARKDDReport now has a verification_gate field
  - WriterResult carries verification fields
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace


# ═══════════════════════════════════════════════════════════════════════
# Secondary-provider picker
# ═══════════════════════════════════════════════════════════════════════

def test_pick_secondary_skips_excluded_name():
    from aria_service.learning.verification_gate import pick_secondary_provider

    p1 = SimpleNamespace(name="anthropic", is_configured=True)
    p2 = SimpleNamespace(name="deepseek",  is_configured=True)
    p3 = SimpleNamespace(name="groq",      is_configured=True)
    chain = SimpleNamespace(
        providers=[p1, p2, p3],
        _stats={"anthropic": {"cooldown_until": 0},
                "deepseek":  {"cooldown_until": 0},
                "groq":      {"cooldown_until": 0}},
    )
    picked = pick_secondary_provider(chain, exclude_name="anthropic")
    assert picked is p2 or picked is p3
    # Specifically: since deepseek is first non-excluded + not cooling,
    # deepseek wins
    assert picked is p2


def test_pick_secondary_skips_cooling():
    from aria_service.learning.verification_gate import pick_secondary_provider

    p1 = SimpleNamespace(name="anthropic", is_configured=True)
    p2 = SimpleNamespace(name="deepseek",  is_configured=True)
    p3 = SimpleNamespace(name="groq",      is_configured=True)
    # anthropic excluded (was primary); deepseek is cooling; groq wins
    #
    # R-F3692 — was `_t.monotonic() + 600`, which encoded the WRONG clock and so
    # pinned a bug: fallback.py writes `cooldown_until` in WALL-CLOCK
    # (`_record_failure`: `now = time.time()`, fallback.py:643 -> :670/:725) and
    # reads it the same way (:340, :544, :549). A monotonic value is ~process
    # uptime, i.e. always in the past against wall-clock, so this fixture
    # described a cooldown production can never produce. Bind the real contract.
    import time as _t
    future = _t.time() + 600
    chain = SimpleNamespace(
        providers=[p1, p2, p3],
        _stats={"anthropic": {"cooldown_until": 0},
                "deepseek":  {"cooldown_until": future},
                "groq":      {"cooldown_until": 0}},
    )
    picked = pick_secondary_provider(chain, exclude_name="anthropic")
    assert picked is p3


def test_pick_secondary_returns_none_when_no_alternatives():
    from aria_service.learning.verification_gate import pick_secondary_provider

    p1 = SimpleNamespace(name="anthropic", is_configured=True)
    chain = SimpleNamespace(
        providers=[p1],
        _stats={"anthropic": {"cooldown_until": 0}},
    )
    picked = pick_secondary_provider(chain, exclude_name="anthropic")
    assert picked is None


def test_pick_secondary_returns_none_when_not_fallback_chain():
    from aria_service.learning.verification_gate import pick_secondary_provider
    # Object with no .providers attribute
    plain = SimpleNamespace()
    assert pick_secondary_provider(plain) is None


# ═══════════════════════════════════════════════════════════════════════
# Double-via-fallback (async)
# ═══════════════════════════════════════════════════════════════════════

def test_double_via_fallback_returns_both_texts():
    from aria_service.learning.verification_gate import double_via_fallback

    class _FakeProvider:
        def __init__(self, name, text):
            self.name = name
            self.is_configured = True
            self._text = text
        async def complete(self, system_prompt, user_prompt, *, max_tokens=0, timeout=0):
            return SimpleNamespace(text=self._text, routed_via=f"fallback:{self.name}")

    p1 = _FakeProvider("anthropic", "PRIMARY-RED-CLEAN")
    p2 = _FakeProvider("deepseek",  "SECONDARY-RED-CLEAN")

    class _FakeFallback:
        def __init__(self):
            self.name = "fallback"
            self.providers = [p1, p2]
            self._stats = {"anthropic": {"cooldown_until": 0}, "deepseek": {"cooldown_until": 0}}
        async def complete(self, system_prompt, user_prompt, *, max_tokens=0, timeout=0):
            return SimpleNamespace(text="PRIMARY-RED-CLEAN",
                                     routed_via="fallback:anthropic")

    async def run():
        return await double_via_fallback(
            "sys", "user",
            fallback_llm=_FakeFallback(),
            max_tokens=100, timeout=10.0,
        )

    primary_text, secondary_text, primary_name, secondary_name = asyncio.run(run())
    assert primary_text == "PRIMARY-RED-CLEAN"
    assert secondary_text == "SECONDARY-RED-CLEAN"
    assert primary_name == "anthropic"
    assert secondary_name == "deepseek"


# ═══════════════════════════════════════════════════════════════════════
# Schema — ARKDDReport has verification_gate field
# ═══════════════════════════════════════════════════════════════════════

def test_arkdd_report_has_verification_gate_field():
    from aria_service.intel.dd_schema import ARKDDReport
    r = ARKDDReport()
    assert hasattr(r, "verification_gate")
    assert isinstance(r.verification_gate, dict)
    assert r.verification_gate == {}


# ═══════════════════════════════════════════════════════════════════════
# Schema — WriterResult has verification fields
# ═══════════════════════════════════════════════════════════════════════

def test_writer_result_has_verification_fields():
    from aria_service.writers.writer_orchestrator import WriterResult
    w = WriterResult(
        writer_type="assessment",
        reference="TEST-001",
        document="body",
        structured=None,
        output_hash="abc",
        produced_at="2026-04-18T00:00:00Z",
        word_count=10,
        success=True,
    )
    assert hasattr(w, "verification_verdict")
    assert hasattr(w, "verification_severity")
    assert hasattr(w, "verification_secondary")
    assert w.verification_verdict == ""


# ═══════════════════════════════════════════════════════════════════════
# End-to-end — chat pipeline classify + stamp (without real LLM)
# ═══════════════════════════════════════════════════════════════════════

def test_classify_severity_critical_triggers_gate():
    from aria_service.learning.verification_gate import classify_severity, extract_verdict, detect_disagreement
    text = "🔴 BOTTOM LINE — NO-GO. Risk: RED. HARD_STOP."
    assert classify_severity(text) == "CRITICAL"
    v = extract_verdict(text)
    assert v["has_no_go"] is True
    # Risk tier extraction returns the LEFTMOST match. NO-GO / RED /
    # HARD_STOP are treated as equivalent severity-3 by detect_disagreement,
    # so any of them is a valid risk_tier value.
    assert v["risk_tier"] in ("RED", "NO-GO", "HARD_STOP")
    # Same text vs itself → perfect agreement
    r = detect_disagreement(v, v)
    assert r["agree"] is True


def test_stamp_format_verified():
    """The delivered stamp must come from the gate's own constant.

    This test used to build its own copy of the stamp and assert tokens against
    that copy — so it passed no matter what the product actually emitted, and
    kept passing after the real wording changed. A test that asserts about a
    string it just wrote itself is testing nothing.

    It now reads the constant the wiring sites use, and pins the PROPERTY that
    matters rather than the phrasing: the agreement stamp must not claim
    independent corroboration. Two models reading one evidence set agree by
    construction; on run dd_29368fbb8b3d the old "VERIFIED BY DISAGREEMENT"
    wording stamped a false OFAC collision and read as confirmation.
    """
    from aria_service.learning.verification_gate import (
        STAMP_CONSISTENT, STAMP_CONSISTENT_MARKER,
    )

    # The dedupe marker must actually occur in the stamp. The previous wiring
    # checked for "[VERIFIED BY DISAGREEMENT]" while emitting
    # "[VERIFIED BY DISAGREEMENT — ...", so the guard could never fire.
    assert STAMP_CONSISTENT_MARKER in STAMP_CONSISTENT, (
        "the dedupe marker does not occur in the stamp — the double-stamp guard "
        "cannot fire"
    )

    # It must not assert verification or independent corroboration of the fact.
    lowered = STAMP_CONSISTENT.lower()
    assert "verified" not in lowered, (
        "the stamp claims verification; it only compares two narratives built "
        "from the same evidence"
    )
    assert "not independent corroboration" in lowered, (
        "the stamp must state what it is NOT, or an operator will read model "
        "agreement as a second source"
    )

    stamp_unverified = "⚠ [CRITICAL — PROVIDERS DISAGREE — BLOCKING]"
    for tok in ["CRITICAL", "PROVIDERS", "DISAGREE"]:
        assert tok in stamp_unverified

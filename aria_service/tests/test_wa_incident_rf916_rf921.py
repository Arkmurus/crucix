"""Tests for the 2026-05-26 WhatsApp incident fix batch (R-F916..R-F921).

Incident: operator asked "what can you tell us about https://defence.csg.com/en"
twice on WhatsApp; both URL queries ran past the WA listener's 90s timeout →
"⚠️ ARIA is temporarily unavailable." The one query that returned served a
STALE reasoning-library fallback (0 sources, NO_CITATIONS, truncated "(2"),
and "why are you unavailable?" produced a FABRICATED diagnostic (claimed the
dormant brave_answer tool fired) with the internal R-F401 guard block leaked
into the user-facing reply.

Each test names the R-number and proves the user-visible symptom is fixed.
"""
from __future__ import annotations

import re

import pytest


# ── R-F916 — async URL-in-chat ───────────────────────────────────────────────

def test_rf916_chat_request_has_async_mode_field():
    """Unit: ChatRequest exposes async_mode (default False)."""
    from aria_service.routes.aria import ChatRequest
    r = ChatRequest(message="hi")
    assert r.async_mode is False
    r2 = ChatRequest(message="hi", async_mode=True)
    assert r2.async_mode is True


def test_rf916_chat_result_endpoint_exists_and_handles_unknown():
    """Unit: the /chat/result job-poll endpoint exists and reports not_found
    for an unknown job id (mirrors read-document/result)."""
    import asyncio
    from aria_service.routes import aria as aria_routes
    assert hasattr(aria_routes, "chat_result_ep")
    assert hasattr(aria_routes, "_chat_job_set")
    assert hasattr(aria_routes, "_chat_job_get")
    out = asyncio.run(aria_routes.chat_result_ep("does-not-exist-xyz"))
    assert out["status"] == "not_found"


# ── R-F917 — URL forces fresh crawl + no stale/fallback caching ──────────────

def test_rf917_find_match_skips_url_questions():
    """Capability: a URL question is NEVER served from the reasoning library —
    it must trigger a fresh crawl (the CSG-website symptom)."""
    import asyncio
    from aria_service.intel import reasoning_library as rl
    res = asyncio.run(rl.find_match("what can you tell us about https://defence.csg.com/en"))
    assert res["match"] is False
    assert res["method"] == "skipped_url_fresh_crawl"


def test_rf917_record_response_refuses_url_question():
    """Unit: an answer to a URL question is never cached (so no future stale replay)."""
    import asyncio
    from aria_service.intel import reasoning_library as rl
    out = asyncio.run(rl.record_response(
        "tell me about https://defence.csg.com/en",
        "CSG Defence is a Czech/Slovak defence group with multiple subsidiaries "
        "across ammunition and vehicles, employing tens of thousands.",
        source_brain="deepseek",
    ))
    assert out["recorded"] is False
    assert "URL" in out["reason"]


def test_rf917_does_NOT_gate_on_wrapper_name_fallback():
    """Regression guard (Pass-2 finding): source_brain == 'fallback' is just the
    FallbackProvider wrapper's .name (R-F131) — it is the value for EVERY live
    answer. Gating distillation on it would disable the reasoning library
    entirely. A normal (non-URL, grounded) answer MUST still be cacheable even
    when source_brain='fallback'."""
    import asyncio
    from aria_service.intel import reasoning_library as rl
    out = asyncio.run(rl.record_response(
        "what is the consolidation strategy of central european defence OEMs",
        "Central European defence OEMs have consolidated under holding groups "
        "that bundle ammunition, vehicles and small arms across the region.",
        source_brain="fallback",
    ))
    assert out["recorded"] is True, (
        "REGRESSION: distillation gated on the 'fallback' wrapper name — "
        "this would silently disable the whole reasoning library."
    )


def test_rf917_record_response_refuses_ungrounded_marker():
    """Unit: a response already carrying 0-source / NO_CITATIONS markers is not cached."""
    import asyncio
    from aria_service.intel import reasoning_library as rl
    out = asyncio.run(rl.record_response(
        "give me an overview of a generic compliance topic for testing",
        "Some answer text.\n\nVerification: NO_CITATIONS — Sources: 0 grounded / 0 unverified",
        source_brain="deepseek",
    ))
    assert out["recorded"] is False


def test_rf917_url_detector():
    from aria_service.intel.reasoning_library import _question_has_url
    assert _question_has_url("see https://example.com please")
    assert _question_has_url("HTTP://EXAMPLE.COM")
    assert not _question_has_url("what is the capital of France")


# ── R-F918 — self-state / availability routing ───────────────────────────────

@pytest.mark.parametrize("q", [
    "why are you unavailable?",
    "Aria, why are you unavailable?",
    "why were you offline",
    "why are you so slow",
    "are you online?",
    "are you ok?",
    "are you still there",
    "are you down?",
    "what happened to you",
    "what's wrong with you",
    "why didn't you respond",
    "why can't you answer",
])
def test_rf918_self_state_detected(q):
    """Capability: self-state/availability questions route to /health/perf
    (is_capability_introspection_query) instead of the web-search/brave path
    that fabricated a diagnostic."""
    from aria_service.intel.self_infra_detector import (
        is_self_state_query, is_capability_introspection_query,
    )
    assert is_self_state_query(q), f"should detect self-state: {q!r}"
    assert is_capability_introspection_query(q), f"should route to self_introspect: {q!r}"


@pytest.mark.parametrize("q", [
    "what is Saudi Arabia importing",
    "who is Michele Zagaria",
    "what are Russian sanctions",
    "how many countries are in NATO",
    "are you able to share that file with me",
    "what happened in Sudan last week",
    "why is the dollar falling",
    "are you up for a call tomorrow",   # Pass-2 finding #2 — social, not self-state
    "are you down for a meeting later",
])
def test_rf918_no_false_positive(q):
    """Regression: external/factual questions must NOT be treated as self-state."""
    from aria_service.intel.self_infra_detector import is_self_state_query
    assert not is_self_state_query(q), f"should NOT fire on external query: {q!r}"


def test_rf918_introspect_guard_fires_on_self_state():
    """Capability: the self_introspect context injector also catches self-state,
    so the LLM gets live health data instead of inventing one."""
    from aria_service.intel.self_introspect_guard import detect_self_capability_question
    assert detect_self_capability_question("why are you unavailable?")
    assert detect_self_capability_question("are you down right now")
    # existing capability questions still detected
    assert detect_self_capability_question("how many sources do you have?")


def test_rf918_tool_intent_routes_self_state_to_self_introspect():
    """Capability (end-to-end intent): _detect_tool_intent maps a self-state
    question to the self_introspect tool — NOT web_search / brave."""
    from aria_service.routes.aria import _detect_tool_intent
    intent = _detect_tool_intent("why are you unavailable?")
    assert isinstance(intent, dict)
    assert intent.get("tool") == "self_introspect", intent


# ── R-F919 — never leak the self-claim guard block to users ──────────────────

def test_rf919_strip_internal_scaffolding_removes_leaked_block():
    """Capability: a leaked R-F401 guard block is scrubbed from user-facing text."""
    from aria_service.intel.self_claim_guard import strip_internal_scaffolding
    leaked = (
        "Here is my honest answer about CSG.\n\n"
        "[R-F401 SELF-CLAIM GUARD — possible hallucination detected]\n"
        "  BLOCK (1):\n"
        "    · rf604_capability_denial: \"I cannot fire self_introspect\" — bad.\n"
        "  → Call /api/aria/health/perf via [TOOL: self_introspect] and cite real numbers. "
        "Anchor: Constitution Clause 25."
    )
    cleaned = strip_internal_scaffolding(leaked)
    assert "SELF-CLAIM GUARD" not in cleaned
    assert "rf604_capability_denial" not in cleaned
    assert "Constitution Clause 25" not in cleaned
    assert cleaned.startswith("Here is my honest answer about CSG.")


def test_rf919_strip_is_idempotent_and_safe_on_clean_text():
    from aria_service.intel.self_claim_guard import strip_internal_scaffolding
    clean = "A perfectly normal answer with no guard scaffolding."
    assert strip_internal_scaffolding(clean) == clean
    assert strip_internal_scaffolding("") == ""
    assert strip_internal_scaffolding(None) == ""


def test_rf919_footer_does_not_emit_guard_block():
    """Capability: build_footer never appends the internal guard block, even
    when a forbidden self-claim pattern is present (it leaked before R-F919)."""
    from aria_service.intel import confidence_footer as cf
    # An invented-TTL claim is a known BLOCK-severity R-F401 pattern.
    text = "My knowledge has an 18-month TTL and I will forget facts after that."
    footer = cf.build_footer(
        response_text=text,
        tools_used=["chat"],
        verification=None,
        trace_id="tr_test",
    )
    assert "SELF-CLAIM GUARD" not in footer
    assert "rf401" not in footer.lower()
    assert "Constitution Clause 25" not in footer


# ── R-F920 — build_rev footer honesty ────────────────────────────────────────

def test_rf920_build_rev_has_no_buildarg_missing_leak():
    """Capability: the user-facing build_rev never carries the alarming
    'runtime fallback (build-arg missing)' suffix (§14 fallback transparency)."""
    import aria_service.main as m
    assert "build-arg missing" not in m.ARIA_BUILD_REV
    assert "runtime fallback" not in m.ARIA_BUILD_REV
    # the diagnostic is still tracked for operators
    assert hasattr(m, "ARIA_BUILD_SOURCE")
    assert m.ARIA_BUILD_SOURCE in ("build-arg", "git-head-runtime", "unknown")


# ── R-F921 — brain self-observation channel ──────────────────────────────────

def test_rf921_observe_self_event_routes_to_absorb(monkeypatch):
    """Unit: observe_self_event feeds the 'self_monitor' module via absorb with
    a capability gap on failure (so gap_detector → self_coder can see it)."""
    import asyncio
    from aria_service.intel import brain_hook as bh
    captured = {}

    async def _fake_absorb(**kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(bh, "absorb", _fake_absorb)
    out = asyncio.run(bh.observe_self_event(
        "chat_async_job_failed", {"job_id": "abc", "error": "timeout"},
        gap_type="timeout",
    ))
    assert out == {"ok": True}
    assert captured["module"] == "self_monitor"
    assert captured["success"] is False
    assert captured["gap_type"] == "timeout"
    assert "chat_async_job_failed" in captured["summary"]


def test_rf921_self_monitor_is_registered_topic():
    from aria_service.intel.brain_hook import _MODULE_TOPICS
    assert "self_monitor" in _MODULE_TOPICS

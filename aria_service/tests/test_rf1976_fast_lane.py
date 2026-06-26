"""R-F1976 — adaptive fast-lane (capability test).

Basic questions ("how are you", "what can you do") get a single lean LLM call,
skipping the 7-layer context build + tools + verification → ~1-2s instead of the
full grounded pipeline. The router is CONSERVATIVE: any entity / compliance /
tool / document / URL / complex signal routes to the FULL pipeline, so grounding
is never weakened. These tests pin both halves: routing correctness + the lean call.
"""
import asyncio

from aria_service.routes.aria import _fast_lane_eligible
from aria_service.aria_engine import fast_lane_chat


def test_basic_questions_take_the_fast_lane():
    assert _fast_lane_eligible("how are you?") is True
    assert _fast_lane_eligible("what can you do?") is True
    assert _fast_lane_eligible("can you help me brainstorm") is True
    assert _fast_lane_eligible("how does ARIA work") is True  # single cap word, no entity
    assert _fast_lane_eligible("explain itar in one line") is False  # 'itar' → compliance → full
    assert _fast_lane_eligible("explain export licensing simply") is False  # 'export'/'licens' → full


def test_grounding_critical_questions_go_to_full_pipeline():
    # Entity / compliance / tool / document / URL → must NOT fast-lane.
    assert _fast_lane_eligible("screen Acme Corporation for sanctions") is False
    assert _fast_lane_eligible("investigate John Smith") is False
    assert _fast_lane_eligible("run due diligence on Modirum Gespi") is False
    assert _fast_lane_eligible("what's the registration number of Globex") is False  # heavy kw 'registr'
    assert _fast_lane_eligible("review this https://example.com/page") is False
    assert _fast_lane_eligible("[ATTACHED DOCUMENT: x.pdf] please review") is False
    assert _fast_lane_eligible("Modirum Gespi") is False  # bare named entity → grounding
    assert _fast_lane_eligible("x" * 400) is False         # long → full


def test_fast_lane_chat_returns_text_and_persists(monkeypatch):
    class _StubResult:
        text = "I'm doing great — ready to help with your security and compliance work."

    class _StubLLM:
        async def complete(self, system, user, *, max_tokens=600, timeout=30.0):
            assert "ARIA" in system  # the compact identity prompt is used
            assert "how are you" in user.lower()
            return _StubResult()

    async def run():
        out = await fast_lane_chat("how are you?", "rf1976_sess_test", _StubLLM())
        assert out and "ready to help" in out
        # continuity: the turn is saved to the session
        from aria_service.aria_engine import _get_session
        s = await _get_session("rf1976_sess_test")
        msgs = s.get("messages") or []
        assert any(m.get("role") == "user" and "how are you" in (m.get("content") or "").lower() for m in msgs)
        assert any(m.get("role") == "aria" for m in msgs)
    asyncio.run(run())


def test_empty_llm_answer_falls_through():
    class _Empty:
        text = "   "

    class _StubLLM:
        async def complete(self, system, user, *, max_tokens=600, timeout=30.0):
            return _Empty()

    async def run():
        out = await fast_lane_chat("hi", "rf1976_sess_empty", _StubLLM())
        assert out is None, "empty answer must fall through to the full grounded pipeline"
    asyncio.run(run())

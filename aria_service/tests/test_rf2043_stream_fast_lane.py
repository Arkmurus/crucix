"""R-F2043 — fast-lane parity for the web/SSE chat path (chat_stream_ep).

Operator symptom (2026-06-27): "ARIA WA/web not responding to basic questions".
Live repro: POST /api/aria/chat/stream {"message":"how are you"} emitted
`detecting → no_tool → "Building intelligence context (9 layers)…"` then HUNG —
timed out at 60s with no answer. The fast-lane (R-F1976) was added to chat_ep
but NOT to chat_stream_ep, so every web question — even a greeting — built the
full 9-layer context (heavy RAG-query encode that stalls the event loop).

CAPABILITY: drive the REAL chat_stream_ep with a basic question and assert it
takes the fast-lane (single lean reply, `fast_lane:true` done event) and NEVER
enters the heavy compose path (aria_chat_stream). Plus the conservative guard:
an entity/compliance question must still go to the full pipeline.
"""
import json

import pytest


def _parse_sse(raw: str) -> list[dict]:
    events = []
    for block in raw.split("\n\n"):
        block = block.strip()
        if block.startswith("data: "):
            try:
                events.append(json.loads(block[6:]))
            except Exception:
                pass
    return events


def _wire_common(monkeypatch, A):
    import aria_service.intel.reasoning_library as _rl_mod
    import aria_service.intel.user_quota as _uq_mod
    import aria_service.intel.cost_tracker as _ct_mod
    # not a hardcoded trivial reply — we want to exercise the fast-lane branch
    monkeypatch.setattr(_rl_mod, "trivial_reply", lambda *_a, **_k: None, raising=False)

    async def _no_coder(req, request):
        return None
    monkeypatch.setattr(A, "_maybe_handle_coder_command", _no_coder)

    class _LLM:
        is_configured = True
    monkeypatch.setattr(A, "get_llm", lambda request: _LLM())
    monkeypatch.setattr(A, "get_intel_data", lambda request: {})

    async def _allow(*_a, **_k):
        return (True, "")
    async def _reg(*_a, **_k):
        return None
    monkeypatch.setattr(_uq_mod, "check", _allow, raising=False)
    monkeypatch.setattr(_uq_mod, "register_request", _reg, raising=False)
    monkeypatch.setattr(_ct_mod, "set_user", lambda *_a, **_k: None, raising=False)
    async def _no_cap(*_a, **_k):
        return (False, 0.0, 20.0)
    monkeypatch.setattr(_ct_mod, "user_month_cap_exceeded", _no_cap, raising=False)


@pytest.mark.asyncio
async def test_basic_question_takes_fast_lane_and_never_builds_9_layer(monkeypatch):
    from aria_service.routes import aria as A
    _wire_common(monkeypatch, A)

    # The heavy compose path must NOT run for a basic question.
    async def _must_not_run(*_a, **_k):
        raise AssertionError("aria_chat_stream (9-layer compose) ran for a basic question")
        yield  # pragma: no cover — make it an async generator
    monkeypatch.setattr(A, "aria_chat_stream", _must_not_run, raising=False)

    # chat_stream_ep does a call-time `from ..aria_engine import fast_lane_chat`,
    # so patch the SOURCE module (the routes module never binds the name).
    import aria_service.aria_engine as _eng
    seen = {}
    async def _fake_fast_lane(message, session_id, llm, **_k):
        seen["message"] = message
        return "Doing well! Ready to help. What do you need?"
    monkeypatch.setattr(_eng, "fast_lane_chat", _fake_fast_lane, raising=False)

    req = A.ChatRequest(message="how are you?", session_id="s_rf2043", auto_tools=True)
    resp = await A.chat_stream_ep(req, request=object())

    chunks = []
    async for part in resp.body_iterator:
        chunks.append(part.decode() if isinstance(part, (bytes, bytearray)) else part)
    events = _parse_sse("".join(chunks))

    assert seen.get("message") == "how are you?"
    assert any(e.get("type") == "chunk" and "Ready to help" in e.get("text", "") for e in events)
    done = [e for e in events if e.get("type") == "done"]
    assert done and done[0].get("fast_lane") is True, "fast-lane done event missing"


@pytest.mark.asyncio
async def test_entity_question_still_goes_to_full_pipeline(monkeypatch):
    """Conservative guard: a grounding-critical question must NOT fast-lane —
    it must reach the heavy path so grounding/DD is never weakened."""
    from aria_service.routes import aria as A
    _wire_common(monkeypatch, A)
    monkeypatch.setattr(A, "_detect_tool_intent", lambda msg: None)

    import aria_service.aria_engine as _eng
    def _fail_fast_lane(*_a, **_k):
        raise AssertionError("fast_lane_chat ran for an entity/compliance question")
    monkeypatch.setattr(_eng, "fast_lane_chat", _fail_fast_lane, raising=False)

    reached = {"heavy": False}
    async def _heavy(*_a, **_k):
        reached["heavy"] = True
        yield {"type": "done"}
    monkeypatch.setattr(A, "aria_chat_stream", _heavy, raising=False)
    import aria_service.intel.stream_honesty as _sh
    async def _no_honesty(**_k):
        return {"changed": False}
    monkeypatch.setattr(_sh, "apply_stream_honesty", _no_honesty, raising=False)
    import aria_service.intel.confidence_footer as _cf
    monkeypatch.setattr(_cf, "build_footer", lambda **_k: "", raising=False)

    req = A.ChatRequest(message="run due diligence on Modirum Gespi",
                        session_id="s_rf2043_full", auto_tools=True)
    resp = await A.chat_stream_ep(req, request=object())
    async for _ in resp.body_iterator:
        pass
    assert reached["heavy"], "entity question must reach the full grounded pipeline"

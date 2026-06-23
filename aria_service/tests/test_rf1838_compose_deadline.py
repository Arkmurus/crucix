"""R-F1838 — web chat compose-deadline safety net.

Operator symptom (2026-06-23, clean live e2e): a web DD delivered its preliminary
report (R-F1176 chunks) but the stream NEVER emitted `done` — it stalled at
"Building intelligence context (9 layers)..." because the POST-tool compose
(aria_chat_stream's 9-layer rebuild + LLM) overran the 600s SSE proxy window.
The user saw the report but the spinner hung forever — the final-answer half of
"no reply on the web for the DD".

R-F1838 drives aria_chat_stream under an ABSOLUTE deadline
(_stream_req_t0 + ARIA_STREAM_TOTAL_HARD_S, default 560s < 600s proxy). If the
compose stalls or overruns, the loop breaks, a cut-note is emitted, and the
existing post-loop code emits `done` — so the stream ALWAYS closes cleanly.

CAPABILITY: drive the REAL chat_stream_ep generator with a compose step that
HANGS forever and assert a `done` still arrives, quickly (within the deadline),
with a cut-note — i.e. the stream can no longer hang open.
"""
import asyncio
import json
import time

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


@pytest.mark.asyncio
async def test_rf1838_hanging_compose_still_closes_stream(monkeypatch):
    from aria_service.routes import aria as A

    # Tight deadline so the test is fast.
    monkeypatch.setenv("ARIA_STREAM_TOTAL_HARD_S", "1")

    import aria_service.intel.reasoning_library as _rl_mod
    import aria_service.intel.user_quota as _uq_mod
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

    # No tool → straight to the compose path (the part that hangs in prod).
    monkeypatch.setattr(A, "_detect_tool_intent", lambda msg: None)

    # The compose HANGS forever and never yields a chunk or done.
    async def _hanging_chat_stream(*_a, **_k):
        await asyncio.sleep(3600)
        yield {"type": "done"}  # never reached
    monkeypatch.setattr(A, "aria_chat_stream", _hanging_chat_stream)

    # honesty pass + footer → no-ops so the post-loop path is fast + deterministic
    import aria_service.intel.stream_honesty as _sh
    async def _no_honesty(**_k):
        return {"changed": False}
    monkeypatch.setattr(_sh, "apply_stream_honesty", _no_honesty, raising=False)
    import aria_service.intel.confidence_footer as _cf
    monkeypatch.setattr(_cf, "build_footer", lambda **_k: "", raising=False)

    req = A.ChatRequest(message="Tell me about widget markets", session_id="s_rf1838", auto_tools=True)
    resp = await A.chat_stream_ep(req, request=object())

    t0 = time.monotonic()
    chunks = []
    async for part in resp.body_iterator:
        chunks.append(part.decode() if isinstance(part, (bytes, bytearray)) else part)
    elapsed = time.monotonic() - t0
    events = _parse_sse("".join(chunks))

    # The whole point: a `done` arrives despite the compose hanging forever.
    assert any(e.get("type") == "done" for e in events), (
        "no `done` event — the stream would hang open forever (the bug)."
    )
    # And it closed quickly (deadline ~1s + overhead), not after 3600s.
    assert elapsed < 30, (
        f"stream took {elapsed:.1f}s to close — the compose deadline did not fire."
    )
    # The user is told the narrative was cut.
    assert any(e.get("type") == "chunk" and "guarantee a response" in e.get("text", "")
               for e in events), "cut-note chunk missing"


@pytest.mark.asyncio
async def test_rf1838_fast_compose_is_not_cut(monkeypatch):
    """Guard against over-firing: a compose that finishes quickly must NOT be
    cut and must NOT carry the cut-note."""
    from aria_service.routes import aria as A

    monkeypatch.setenv("ARIA_STREAM_TOTAL_HARD_S", "30")

    import aria_service.intel.reasoning_library as _rl_mod
    import aria_service.intel.user_quota as _uq_mod
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
    monkeypatch.setattr(A, "_detect_tool_intent", lambda msg: None)

    async def _fast_chat_stream(*_a, **_k):
        yield {"type": "chunk", "text": "Quick answer."}
        yield {"type": "done", "session_id": "s_fast"}
    monkeypatch.setattr(A, "aria_chat_stream", _fast_chat_stream)
    import aria_service.intel.stream_honesty as _sh
    async def _no_honesty(**_k):
        return {"changed": False}
    monkeypatch.setattr(_sh, "apply_stream_honesty", _no_honesty, raising=False)
    import aria_service.intel.confidence_footer as _cf
    monkeypatch.setattr(_cf, "build_footer", lambda **_k: "", raising=False)

    req = A.ChatRequest(message="Tell me about widget markets", session_id="s_fast", auto_tools=True)
    resp = await A.chat_stream_ep(req, request=object())
    chunks = []
    async for part in resp.body_iterator:
        chunks.append(part.decode() if isinstance(part, (bytes, bytearray)) else part)
    events = _parse_sse("".join(chunks))

    assert any(e.get("type") == "chunk" and "Quick answer." in e.get("text", "") for e in events)
    assert any(e.get("type") == "done" for e in events)
    assert not any("guarantee a response" in e.get("text", "") for e in events if e.get("type") == "chunk"), (
        "fast compose was wrongly cut-noted"
    )

"""R-F1747 — heavy-tool stream heartbeat.

Operator symptom (2026-06-20 web chat): /dd and Research produced ZERO streamed
bytes for 30s-170s+ — not even the "Running X…" progress event — because the
heavy tool (dd_orchestrate / deep_research) ran inline and its periodic
synchronous CPU sections starved the SSE writer. The user saw a frozen
"streaming" badge → eventual proxy cut → "[!stream cut]".

Fix: the generator now runs _execute_tool as a shielded task and emits a
"Still running X… (Ns elapsed)" progress event every ARIA_STREAM_HEARTBEAT_S
seconds while it runs, after flushing the initial "Running X…" event.

Capability: drive the REAL chat_stream_ep generator with a deliberately-slow
tool and assert MULTIPLE tool_running progress events arrive (the initial one
plus at least one heartbeat) BEFORE the tool result — i.e. the stream is alive,
not frozen.
"""
import asyncio
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


@pytest.mark.asyncio
async def test_heartbeat_emitted_during_slow_tool(monkeypatch):
    from aria_service.routes import aria as A

    # Fast heartbeat so the test is quick.
    monkeypatch.setenv("ARIA_STREAM_HEARTBEAT_S", "0.1")

    # --- stub the chain so the generator reaches the tool wrapper ---
    # trivial_reply + user_quota are re-imported locally inside chat_stream_ep,
    # so patch them on their SOURCE modules.
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

    # quota gate → allow
    async def _allow(*_a, **_k):
        return (True, "")
    async def _reg(*_a, **_k):
        return None
    monkeypatch.setattr(_uq_mod, "check", _allow, raising=False)
    monkeypatch.setattr(_uq_mod, "register_request", _reg, raising=False)

    # intent → a heavy tool
    monkeypatch.setattr(
        A, "_detect_tool_intent",
        lambda msg: {"tool": "deep_research", "entity": "Embraer", "context": msg},
    )

    # the slow tool: ~0.45s of awaiting → expect ~4 heartbeats at 0.1s
    async def _slow_tool(intent, llm, *, dd_budget_s=None):  # R-F1829: accept the new kwarg
        await asyncio.sleep(0.45)
        return "[TOOL: deep_research] " + ("x" * 200)
    monkeypatch.setattr(A, "_execute_tool", _slow_tool)

    # the LLM stream: one chunk + done (no real provider)
    async def _fake_chat_stream(*_a, **_k):
        yield {"type": "chunk", "text": "Final analysis."}
        yield {"type": "done", "session_id": "s1"}
    monkeypatch.setattr(A, "aria_chat_stream", _fake_chat_stream)

    # honesty pass / footer / absorb → no-ops so the generator finishes clean
    import aria_service.intel.stream_honesty as _sh
    async def _no_honesty(**_k):
        return {"changed": False}
    monkeypatch.setattr(_sh, "apply_stream_honesty", _no_honesty, raising=False)

    req = A.ChatRequest(message="Research Embraer", session_id="s1", auto_tools=True)
    resp = await A.chat_stream_ep(req, request=object())

    # Drain the StreamingResponse body.
    chunks = []
    async for part in resp.body_iterator:
        chunks.append(part.decode() if isinstance(part, (bytes, bytearray)) else part)
    events = _parse_sse("".join(chunks))

    tool_running = [
        e for e in events
        if e.get("type") == "progress" and e.get("stage") == "tool_running"
    ]
    # Initial "Running…" + at least one "Still running… (Ns elapsed)" heartbeat.
    assert len(tool_running) >= 2, (
        f"expected >=2 tool_running events (initial + heartbeat), got "
        f"{len(tool_running)}: {[e.get('message') for e in tool_running]}"
    )
    # At least one heartbeat carries an elapsed_s counter (proves it's a live tick).
    assert any("elapsed_s" in e for e in tool_running), (
        "no heartbeat carried an elapsed_s field — heartbeat loop did not fire"
    )
    # The real answer still arrives after the heartbeats.
    assert any(e.get("type") == "chunk" and "Final analysis." in e.get("text", "")
               for e in events), "final LLM answer missing"
    assert any(e.get("type") == "done" for e in events), "done event missing"

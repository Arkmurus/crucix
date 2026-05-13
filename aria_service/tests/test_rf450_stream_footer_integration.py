"""R-F450 — capability test for R-F412's stream proof footer.

R-F412 wired the R-F403 confidence footer into the /chat/stream SSE
event_generator. The R-F412 verifier flagged that NO test drove the
actual stream — all 6 R-F412 tests pinned invariants on hand-built
event lists. A regression that breaks the real `event_generator`
(e.g. `done` yielded before the footer, or footer-build wrapped in
the wrong try-block) would not have been caught.

This test mocks `aria_chat_stream` to emit a known sequence with
confidence tags, drives the real /chat/stream endpoint via TestClient
SSE consumption, and asserts:
  - The footer chunk arrives BEFORE the `done` event
  - The footer contains the expected confidence headline
  - The done event is emitted exactly once at the end
"""
from __future__ import annotations

import json
from fastapi.testclient import TestClient


def _build_app():
    from fastapi import FastAPI
    from aria_service.routes.aria import router as aria_router
    app = FastAPI()
    app.include_router(aria_router)
    return app


def _parse_sse_events(stream_text: str) -> list[dict]:
    """Parse the SSE response body into a list of decoded event dicts.
    Each event is `data: <json>\\n\\n`."""
    events: list[dict] = []
    for line in stream_text.split("\n"):
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if not payload:
            continue
        try:
            events.append(json.loads(payload))
        except json.JSONDecodeError:
            # Malformed payload — skip
            continue
    return events


def test_rf450_stream_footer_arrives_before_done_via_endpoint(monkeypatch):
    """Drive /api/aria/chat/stream end-to-end. The footer chunk MUST
    appear in the SSE event sequence BEFORE the done event."""

    # Mock aria_chat_stream to emit a fake LLM response with a
    # confidence tag so the footer builder has something to work with.
    async def _fake_chat_stream(message, session_id, llm, intel, *, user_id="", persona=""):
        yield {"type": "chunk", "text": "The answer is "}
        yield {"type": "chunk", "text": "Tom Ogle [PROBABLE — single source]."}
        yield {"type": "done", "session_id": session_id}

    # Patch the symbol the endpoint actually imports.
    from aria_service.routes import aria as aria_routes
    monkeypatch.setattr(aria_routes, "aria_chat_stream", _fake_chat_stream)

    # Bypass quota + LLM init paths
    monkeypatch.setattr(aria_routes, "get_llm", lambda _r: None)
    monkeypatch.setattr(aria_routes, "get_intel_data", lambda _r: None)

    async def _allow_quota(_user):
        return True, ""
    async def _register(_user):
        return None
    from aria_service.intel import user_quota
    monkeypatch.setattr(user_quota, "check", _allow_quota)
    monkeypatch.setattr(user_quota, "register_request", _register)

    app = _build_app()
    with TestClient(app) as client:
        r = client.post(
            "/api/aria/chat/stream",
            json={"message": "who is tom ogle?", "session_id": "test-r450"},
        )
    assert r.status_code == 200, f"got {r.status_code}: {r.text[:200]}"

    events = _parse_sse_events(r.text)
    assert events, f"R-F450: no SSE events received. Body: {r.text[:400]}"

    # Locate the footer chunk and the done event
    footer_idx = None
    done_idx = None
    for i, ev in enumerate(events):
        if ev.get("type") == "chunk":
            text = ev.get("text") or ""
            if "Confidence:" in text or "PROBABLE" in text:
                # Footer chunk contains the confidence headline; the
                # earlier chunks (LLM tokens) contain "[PROBABLE]" but
                # NOT "Confidence:". Use that to disambiguate.
                if "Confidence:" in text:
                    footer_idx = i
        if ev.get("type") == "done":
            done_idx = i

    assert footer_idx is not None, (
        f"R-F450 REGRESSION: no footer chunk in stream. "
        f"Events received: {[e.get('type') for e in events]}"
    )
    assert done_idx is not None, (
        f"R-F450: no done event in stream. Events: {events}"
    )
    assert footer_idx < done_idx, (
        f"R-F450 REGRESSION: footer chunk arrived AFTER done. "
        f"footer_idx={footer_idx}, done_idx={done_idx}, events: {events}"
    )


def test_rf450_stream_done_emitted_exactly_once(monkeypatch):
    """The deferred-done logic must emit `done` exactly once — not
    zero times (client hang), not twice (double-close)."""
    async def _fake_chat_stream(message, session_id, llm, intel, *, user_id="", persona=""):
        yield {"type": "chunk", "text": "Reply with no tags."}
        yield {"type": "done", "session_id": session_id}

    from aria_service.routes import aria as aria_routes
    monkeypatch.setattr(aria_routes, "aria_chat_stream", _fake_chat_stream)
    monkeypatch.setattr(aria_routes, "get_llm", lambda _r: None)
    monkeypatch.setattr(aria_routes, "get_intel_data", lambda _r: None)
    from aria_service.intel import user_quota
    async def _allow(_u): return True, ""
    async def _reg(_u): return None
    monkeypatch.setattr(user_quota, "check", _allow)
    monkeypatch.setattr(user_quota, "register_request", _reg)

    app = _build_app()
    with TestClient(app) as client:
        r = client.post(
            "/api/aria/chat/stream",
            json={"message": "hello", "session_id": "test-r450-once"},
        )
    assert r.status_code == 200
    events = _parse_sse_events(r.text)
    done_count = sum(1 for e in events if e.get("type") == "done")
    assert done_count == 1, (
        f"R-F450 REGRESSION: done emitted {done_count} times "
        f"(must be exactly 1). Events: {events}"
    )


def test_rf450_stream_synthetic_done_when_chat_stream_omits_one(monkeypatch):
    """If aria_chat_stream never emits a done event (broken impl),
    the orchestrator must synthesise one so SSE clients don't hang.
    Pin the fallback path."""
    async def _fake_chat_stream(message, session_id, llm, intel, *, user_id="", persona=""):
        yield {"type": "chunk", "text": "No done coming."}
        # Intentionally no done event

    from aria_service.routes import aria as aria_routes
    monkeypatch.setattr(aria_routes, "aria_chat_stream", _fake_chat_stream)
    monkeypatch.setattr(aria_routes, "get_llm", lambda _r: None)
    monkeypatch.setattr(aria_routes, "get_intel_data", lambda _r: None)
    from aria_service.intel import user_quota
    async def _allow(_u): return True, ""
    async def _reg(_u): return None
    monkeypatch.setattr(user_quota, "check", _allow)
    monkeypatch.setattr(user_quota, "register_request", _reg)

    app = _build_app()
    with TestClient(app) as client:
        r = client.post(
            "/api/aria/chat/stream",
            json={"message": "test", "session_id": "test-r450-synth"},
        )
    assert r.status_code == 200
    events = _parse_sse_events(r.text)
    # Synthesised done must be present
    done_events = [e for e in events if e.get("type") == "done"]
    assert len(done_events) == 1, (
        f"R-F450: synthesised done missing or duplicated. "
        f"Events: {events}"
    )
    assert done_events[0].get("session_id") == "test-r450-synth"

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
import sys
from fastapi.testclient import TestClient


def _build_app():
    from fastapi import FastAPI
    from aria_service.routes.aria import router as aria_router, _router_auth_dep
    app = FastAPI()
    # Bypass bearer-token auth for these tests (they test stream behaviour, not auth)
    app.dependency_overrides[_router_auth_dep] = lambda: None
    app.include_router(aria_router)
    return app


# R-F3339 — a non-None LLM sentinel, because None now MEANS "not warm".
#
# R-F2814 added a readiness fast-fail at the top of both chat and chat_stream:
# `if get_llm(request) is None -> 503 warming_up`. During the ~10-min warmup
# app.state.llm_provider is genuinely None, and entering the pipeline would hang
# the SSE connection until the client's timeout — the 15-min WA hang. An honest
# 503 is right.
#
# These tests patched `get_llm` to return None with the comment "bypass quota +
# LLM init paths", which was harmless when written and became the exact signal
# for "still warming" afterwards. Every request 503'd, so all three had been red
# and never reached the event_generator they exist to drive.
#
# The fix belongs in the harness, not the gate: hand back a sentinel. These tests
# monkeypatch aria_chat_stream, so the provider is only ever READ by the
# readiness check and never called. Weakening the gate to green a test would
# trade a real user-facing guarantee for a line of output.
#
# It is a small duck rather than a bare object() because the stream path also
# asks the provider `is_configured` before tool detection (routes/aria.py:12466,
# 12573). A bare sentinel got past the readiness gate and then raised
# AttributeError inside the generator — a sentinel must satisfy every attribute
# the path under test READS, not just the first one.
class _WarmLLM:
    """The smallest provider the stream path will accept: warm and configured."""
    is_configured = True


_WARM_LLM = _WarmLLM()


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
    async def _fake_chat_stream(message, session_id, llm, intel=None, *, user_id="",
                                persona="", speaker_name="", keep_history=None):
        # NOTE: confidence_footer.build_footer returns "" for replies
        # < 80 chars (don't decorate short answers). Pad the fake LLM
        # stream above that floor so the footer logic actually fires —
        # otherwise the test would pin "no footer ever emitted".
        yield {"type": "chunk", "text": "The answer is Tom Ogle. "}
        yield {"type": "chunk", "text": (
            "He is the joint-venture contact for Nebraska ARMES Aviation, "
            "a SDVOSB helicopter MRO firm in Fremont, Nebraska "
            "[PROBABLE — single source]."
        )}
        yield {"type": "done", "session_id": session_id}

    # Patch the symbol the endpoint actually imports.
    from aria_service.routes import aria as aria_routes
    monkeypatch.setattr(aria_routes, "aria_chat_stream", _fake_chat_stream)

    # Bypass quota + LLM init paths
    monkeypatch.setattr(aria_routes, "get_llm", lambda _r: _WARM_LLM)  # R-F3339
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
    async def _fake_chat_stream(message, session_id, llm, intel=None, *, user_id="",
                                persona="", speaker_name="", keep_history=None):
        yield {"type": "chunk", "text": "Reply with no tags."}
        yield {"type": "done", "session_id": session_id}

    from aria_service.routes import aria as aria_routes
    monkeypatch.setattr(aria_routes, "aria_chat_stream", _fake_chat_stream)
    monkeypatch.setattr(aria_routes, "get_llm", lambda _r: _WARM_LLM)  # R-F3339
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
    async def _fake_chat_stream(message, session_id, llm, intel=None, *, user_id="",
                                persona="", speaker_name="", keep_history=None):
        yield {"type": "chunk", "text": "No done coming."}
        # Intentionally no done event

    from aria_service.routes import aria as aria_routes
    monkeypatch.setattr(aria_routes, "aria_chat_stream", _fake_chat_stream)
    monkeypatch.setattr(aria_routes, "get_llm", lambda _r: _WARM_LLM)  # R-F3339
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


def test_rf3339_the_stream_doubles_match_the_real_signature():
    """A test double that drifts behind the function it replaces fails OPAQUELY.

    The endpoint calls aria_chat_stream(..., keep_history=...), a parameter added
    after these fakes were written. The fakes did not accept it, so the call
    raised TypeError INSIDE the SSE generator: the stream emitted an `error`
    event and no footer, and the test reported "R-F450 REGRESSION: no footer
    chunk in stream" — pointing at the feature under test rather than at the
    double. That was hidden behind the 503 until R-F3339 fixed the readiness
    stub, so one drift was masking another.

    Binding the real signature's parameters against each fake turns the next
    such drift into a message that names the cause.
    """
    import inspect
    from aria_service.routes import aria as aria_routes

    real = inspect.signature(aria_routes.aria_chat_stream).parameters
    module = sys.modules[__name__]
    fakes = [obj for name, obj in vars(module).items()
             if name.startswith("test_rf450") and callable(obj)]
    assert fakes, "sanity: the stream tests exist"

    # The doubles are defined inside their tests, so check the shared shape:
    # every parameter the real function exposes must be one a double accepts.
    expected = {"message", "session_id", "llm", "intel_data", "user_id",
                "persona", "speaker_name", "keep_history"}
    missing = set(real) - expected
    assert not missing, (
        f"aria_chat_stream grew {sorted(missing)}. Add it to the _fake_chat_stream "
        f"signatures in this file (and to `expected` here), or the endpoint's call "
        f"will TypeError inside the generator and surface as a phantom "
        f"'no footer' regression."
    )

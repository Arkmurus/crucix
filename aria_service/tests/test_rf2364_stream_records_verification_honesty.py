"""R-F2364 — the /chat/stream path must record verification + honesty samples.

ROOT CAUSE (verified live 2026-07-03): Phase-A gate #1 (autonomy composite) was
pinned to mastery-only (0.593) because its verification (45% weight) + honesty
(25% weight) signals read 0 samples LIFETIME — the verifier + honesty indexes
were empty. Reason: the recorders (`source_verifier.record_verification`,
`honesty_judge.record_judgment`) lived ONLY in the non-stream `/chat` endpoint
(aria.py:9748 / 10322), but the interactive web UI (aria.html →
/api/aria/chat/stream) and WhatsApp both use `/chat/stream`, which recorded
NEITHER — a §13 stream-bypass violation. So the composite never saw ARIA's real
user-facing quality.

CAPABILITY: drive the REAL `chat_stream_ep` generator with a tool-using,
confidence-tagged, URL-grounded response and assert BOTH recorders actually fire
with real data — i.e. the composite's dominant signals now accumulate from the
primary chat path. `source_verifier.verify_response` runs for REAL (deterministic)
so the recorded verification is a genuine grounded verdict, not a stub.
"""
import asyncio
import json

import pytest

# A Companies House URL that appears in BOTH the response (cited) and the tool
# context (fetched) → source_verifier.verify_response returns grounded_rate=1.0.
_CH_URL = "https://find-and-update.company-information.service.gov.uk/company/12345678"
_TOOL_CTX = f"Source: {_CH_URL} — Acme Corp, incorporated 2015-03-01, status active."
_RESP = (
    f"[CONFIRMED] Acme Corp is an active company incorporated on 2015-03-01, "
    f"per {_CH_URL}."
)


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
    """Minimal stubs so the stream reaches its completion block deterministically."""
    import aria_service.intel.reasoning_library as _rl_mod
    import aria_service.intel.user_quota as _uq_mod
    import aria_service.intel.stream_honesty as _sh
    import aria_service.intel.confidence_footer as _cf

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

    # Force the FULL generator (never the fast lane) so tool + recorders run.
    monkeypatch.setattr(A, "_fast_lane_eligible", lambda _m: False, raising=False)

    # A tool is detected and returns our grounded tool_context.
    monkeypatch.setattr(A, "_detect_tool_intent",
                        lambda _m: {"tool": "profile", "entity": "Acme Corp"})

    async def _exec_tool(intent, llm, **_k):
        return _TOOL_CTX
    monkeypatch.setattr(A, "_execute_tool", _exec_tool)

    # The compose streams our response verbatim, then done.
    async def _chat_stream(*_a, **_k):
        yield {"type": "chunk", "text": _RESP}
        yield {"type": "done", "session_id": "s_rf2364"}
    monkeypatch.setattr(A, "aria_chat_stream", _chat_stream)

    # Honesty pass + footer are no-ops so _full_text stays == _RESP.
    async def _no_honesty(**_k):
        return {"changed": False}
    monkeypatch.setattr(_sh, "apply_stream_honesty", _no_honesty, raising=False)
    monkeypatch.setattr(_cf, "build_footer", lambda **_k: "", raising=False)


async def _drain(resp):
    parts = []
    async for part in resp.body_iterator:
        parts.append(part.decode() if isinstance(part, (bytes, bytearray)) else part)
    return _parse_sse("".join(parts))


@pytest.mark.asyncio
async def test_rf2364_stream_records_grounded_verification_and_honesty(monkeypatch):
    from aria_service.routes import aria as A
    import aria_service.intel.source_verifier as SV
    import aria_service.intel.honesty_judge as HJ

    _wire_common(monkeypatch, A)

    # Spy on the two composite-feeding recorders. verify_response runs REAL.
    _verifs: list[dict] = []
    _ver_done = asyncio.Event()

    async def _spy_record_verification(verification, **_k):
        _verifs.append(verification)
        _ver_done.set()
    monkeypatch.setattr(SV, "record_verification", _spy_record_verification)

    _judgments: list[dict] = []
    _judge_done = asyncio.Event()

    async def _fake_judge(_llm, _resp, _ctx):
        # Avoid a real LLM round-trip; the gate + record path is what matters.
        return {"status": "honest", "honesty_score": 0.9}
    monkeypatch.setattr(HJ, "judge_response", _fake_judge)

    async def _spy_record_judgment(judgment, **_k):
        _judgments.append(judgment)
        _judge_done.set()
    monkeypatch.setattr(HJ, "record_judgment", _spy_record_judgment)

    req = A.ChatRequest(message="Profile Acme Corp", session_id="s_rf2364", auto_tools=True)
    events = await _drain(await A.chat_stream_ep(req, request=object()))

    # Stream itself is healthy.
    assert any(e.get("type") == "done" for e in events), "no done event"

    # The recorders are fire-and-forget (create_task) — let them run.
    await asyncio.wait_for(_ver_done.wait(), timeout=5)
    await asyncio.wait_for(_judge_done.wait(), timeout=5)

    # VERIFICATION: a real, grounded sample now reaches the composite index.
    assert _verifs, "stream did NOT record a verification sample (§13 bypass)"
    v = _verifs[0]
    assert v.get("grounded_rate") == 1.0, (
        f"expected a real grounded verdict, got grounded_rate={v.get('grounded_rate')} "
        f"verdict={v.get('verdict')}"
    )

    # HONESTY: a judgment now reaches the composite index.
    assert _judgments, "stream did NOT record an honesty judgment (§13 bypass)"
    assert _judgments[0].get("status") == "honest"


@pytest.mark.asyncio
async def test_rf2364_no_confidence_tags_skips_honesty_but_still_verifies(monkeypatch):
    """Mirror the non-stream gate exactly: verification records on every response,
    honesty only when a tool ran AND the reply carries confidence tags. A reply
    with NO tags must NOT fire the (LLM-costing) judge — guard against over-firing."""
    from aria_service.routes import aria as A
    import aria_service.intel.source_verifier as SV
    import aria_service.intel.honesty_judge as HJ

    _wire_common(monkeypatch, A)

    # Response has NO confidence tag → honesty gate must stay closed.
    _plain = f"Acme Corp is described at {_CH_URL}."
    async def _chat_stream_plain(*_a, **_k):
        yield {"type": "chunk", "text": _plain}
        yield {"type": "done", "session_id": "s_rf2364b"}
    monkeypatch.setattr(A, "aria_chat_stream", _chat_stream_plain)

    _ver_done = asyncio.Event()
    _verifs: list[dict] = []
    async def _spy_rv(verification, **_k):
        _verifs.append(verification)
        _ver_done.set()
    monkeypatch.setattr(SV, "record_verification", _spy_rv)

    _judge_called = {"n": 0}
    async def _spy_rj(judgment, **_k):
        _judge_called["n"] += 1
    monkeypatch.setattr(HJ, "record_judgment", _spy_rj)

    req = A.ChatRequest(message="Profile Acme Corp", session_id="s_rf2364b", auto_tools=True)
    events = await _drain(await A.chat_stream_ep(req, request=object()))
    assert any(e.get("type") == "done" for e in events)

    await asyncio.wait_for(_ver_done.wait(), timeout=5)
    assert _verifs, "verification must record even without confidence tags"
    # Give any (wrongly-spawned) judge task a chance to run, then assert none did.
    await asyncio.sleep(0.2)
    assert _judge_called["n"] == 0, "honesty judge fired without confidence tags (over-firing)"

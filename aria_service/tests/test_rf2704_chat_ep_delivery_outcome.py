"""R-F2704 capability test — §13 stream-bypass / §25 proprioception.

Symptom: the non-stream ``chat_ep`` recorded NO server-side delivery outcome —
only the SSE ``chat_stream_ep`` fired ``_fire_web_delivery_outcome``. So a
produce-failure on the web-nonstream / Telegram / internal callers reached the
brain as SILENCE and the §25 self-heal loop was blind to the non-stream limb.

This proves:
  (a) STRUCTURAL — the real ``chat_ep`` handler now captures a latency start and
      fires the delivery-outcome wire from its ``finally`` (so it runs on BOTH the
      normal return AND a propagating exception: try/finally with no except).
  (b) BEHAVIOURAL — the wire records the outcome against the RIGHT surface derived
      from the session_id prefix, and SKIPS wa/auto sessions (the WA listener
      already self-reports those per the §25 WA template) to avoid double-counting.
"""
import asyncio
import inspect

import pytest

import aria_service.routes.aria as aria

# R-F3755/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so an edit mid-run silently returns a DIFFERENT function's body.
from ._source_probe import function_source

_SRC = function_source(aria, "chat_ep")


def test_chat_ep_finally_fires_delivery_outcome():
    """The broken path (chat_ep) must now invoke the outcome wire on every exit."""
    assert "_deliv_t0 = time.monotonic()" in _SRC, (
        "chat_ep missing delivery-latency start capture — outcome cannot be timed"
    )
    assert "_fire_chat_delivery_outcome(" in _SRC, (
        "chat_ep finally does NOT fire the delivery outcome — the §25 gap survives"
    )
    # classification mirrors the finish_trace `response_text` signal
    assert "delivered_real_answer" in _SRC and "empty_response" in _SRC, (
        "chat_ep missing success/empty outcome classification"
    )


def test_derive_surface_maps_channel_prefixes():
    d = aria._derive_delivery_surface
    assert d("wa_44771234@s.whatsapp.net") == "wa"
    assert d("auto_44771234@s.whatsapp.net") == "wa"
    assert d("telegram_-1002345") == "tg"
    assert d("tg_998") == "tg"
    assert d("web-sess-abc") == "web"
    assert d("") == "web"


@pytest.mark.asyncio
async def test_fire_records_outcome_on_derived_surface(monkeypatch):
    captured = []

    async def _fake_record(rec):
        captured.append(rec)
        return {}

    import aria_service.intel.outcome_wire as ow
    monkeypatch.setattr(ow, "record_outcome", _fake_record)

    # web (non-stream UI) delivered a real answer
    aria._fire_chat_delivery_outcome("web-1", "delivered_real_answer", "", 1234)
    # telegram produced no answer (the previously-silent failure)
    aria._fire_chat_delivery_outcome("telegram_55", "error", "empty_response", 42)
    await asyncio.sleep(0.05)  # let the fire-and-forget bg tasks run

    surfaces = {r.surface: r for r in captured}
    assert "web" in surfaces and "tg" in surfaces, (
        f"expected web+tg outcome records, got {[r.surface for r in captured]}"
    )
    assert surfaces["web"].actual_outcome == "delivered_real_answer"
    assert surfaces["web"].latency_ms == 1234
    assert surfaces["web"].intended_result == "chat_response"
    assert surfaces["web"].request_id == "web-1"
    assert surfaces["tg"].actual_outcome == "error"
    assert surfaces["tg"].detail == "empty_response"


@pytest.mark.asyncio
async def test_fire_skips_wa_to_avoid_double_count(monkeypatch):
    captured = []

    async def _fake_record(rec):
        captured.append(rec)
        return {}

    import aria_service.intel.outcome_wire as ow
    monkeypatch.setattr(ow, "record_outcome", _fake_record)

    aria._fire_chat_delivery_outcome("wa_44771234@s.whatsapp.net", "delivered_real_answer", "", 10)
    aria._fire_chat_delivery_outcome("auto_44771234@s.whatsapp.net", "error", "x", 10)
    await asyncio.sleep(0.05)

    assert not captured, (
        "WA/auto sessions must be skipped (WA listener self-reports per §25 template) "
        f"— firing here double-counts. Got {[r.surface for r in captured]}"
    )

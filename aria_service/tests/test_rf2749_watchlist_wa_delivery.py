"""R-F2749 — watchlist WhatsApp delivery records (Codex finding 10, §25).

Two defects, one fix:
  1. The daily loop's alert used `from .intel import whatsapp; whatsapp.
     send_message(...)` — that module DOES NOT EXIST, so the send raised
     ImportError which the broad except swallowed: the alert silently NEVER
     sent and the brain never knew (a §21 dark path).
  2. Even if it had sent, a queued fire-and-forget task is not delivery. §25
     requires the surface to report the delivery OUTCOME so a non-delivery is a
     self-heal signal.

Fix: use the real WANotifier (→ aria-wa.internal) and record the outcome via the
outcome_wire proprioception primitive. These assert the wiring + that the new
path is a real, importable module (unlike the dead one).
"""
from __future__ import annotations

import re
from pathlib import Path

from aria_service import main as _main


def _watchlist_wa_block() -> str:
    src = Path(_main.__file__).read_text(encoding="utf-8")
    # the region from the adverse filter to the end of the WA wiring
    m = re.search(r"adverse = \[.*?_send_and_record\(\)\)", src, re.S)
    assert m, "watchlist WA wiring block not found"
    return m.group(0)


def test_dead_whatsapp_import_is_gone():
    src = Path(_main.__file__).read_text(encoding="utf-8")
    assert "from .intel import whatsapp" not in src, "dead whatsapp import still present"
    assert "whatsapp.send_message" not in src, "dead whatsapp.send_message still called"


def test_uses_real_notifier_and_records_outcome():
    block = _watchlist_wa_block()
    assert "WANotifier()" in block, "not using the real WANotifier"
    assert "record_outcome(" in block and "OutcomeRecord(" in block, "no §25 outcome record"
    assert 'surface="wa"' in block


def test_outcome_mapping_ok_and_error():
    block = _watchlist_wa_block()
    # a successful send is recorded as delivered; an error as a non-delivery
    assert "delivered_real_answer" in block
    assert "send_failed" in block


def test_new_wa_path_is_importable():
    # the whole bug was that the OLD path raised ImportError. Prove the new one
    # resolves to a real module with the awaitable notify() contract.
    from aria_service.autonomous.wa_notifier import WANotifier
    import inspect
    assert inspect.iscoroutinefunction(WANotifier.notify)


def test_outcome_wire_accepts_wa_watchlist_record():
    # the OutcomeRecord shape the loop builds is valid for the primitive.
    from aria_service.intel.outcome_wire import OutcomeRecord
    rec = OutcomeRecord(
        surface="wa", request_id="watchlist:deadbeef",
        intended_result="watchlist_alert",
        actual_outcome="send_failed", latency_ms=12, detail="error:http_500")
    assert rec.surface == "wa" and rec.actual_outcome == "send_failed"

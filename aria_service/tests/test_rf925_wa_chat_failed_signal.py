"""R-F925 — WA chat failures emit a coder-visible `wa_chat_failed` signal.

Cross-tier observability (handoff P3). Before this, a WA chat failure (brain
timeout / down — the exact 2026-05-26 "ARIA is temporarily unavailable"
incident class) was a console line only; the brain/coder never saw it. The WA
listener already signalled `wa_read_document_failed` (R-F856) but not chat
failures. R-F925 wires `signalChatFailure` into both the sync and async chat
failure paths, POSTing `wa_chat_failed` to /api/aria/brain/signal — which
routes failure-type signals to capability_gaps (R-F887), read by gap_detector's
CapabilityGapExtractor (R-F884). So a WA chat timeout finally becomes a
coder-visible gap.
"""
from __future__ import annotations

import inspect
from pathlib import Path

from aria_service.routes import aria as a


def _wa_source() -> str:
    return (
        Path(a.__file__).resolve().parents[2]
        / "services" / "wa-listener" / "aria_wa_listener.mjs"
    ).read_text(encoding="utf-8")


def test_rf925_wa_listener_defines_and_emits_chat_failure_signal():
    wa = _wa_source()
    # helper exists and targets the correct (non-404) endpoint
    assert "function signalChatFailure(" in wa
    assert "signal_type: 'wa_chat_failed'" in wa
    assert "brainPost('/api/aria/brain/signal'" in wa


def test_rf925_signal_called_from_both_failure_paths():
    """Both the sync chat catch and the async-branch catch must signal — a
    timeout on either path was previously invisible to the brain."""
    wa = _wa_source()
    # exactly the two askARIA failure paths call it (sync catch + async catch)
    assert wa.count("signalChatFailure(message, senderJid") >= 2


def test_rf925_signal_type_is_classified_as_failure_by_endpoint():
    """The endpoint routes a signal to capability_gaps only when its
    signal_type matches the failure tuple. `wa_chat_failed` must match so it
    reaches the coder (not the learning-absorb path)."""
    src = inspect.getsource(a.brain_signal_ep)
    # the endpoint detects failures by substring; "fail" is in the tuple
    assert '"fail"' in src or "'fail'" in src
    # and our signal_type contains it
    assert "fail" in "wa_chat_failed"
    # routed to capability_gaps on failure (coder-visible)
    assert "capability_gaps" in src and "record_gap(" in src

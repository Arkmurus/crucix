"""R-F940 — WA listener routes document-grounded + heavy follow-up chats through
the async job+poll path (not just URLs), so a re-attached-contract question
can't hit the 90s sync timeout. Source-level assertions (the listener has no JS
harness), mirroring test_rf887/test_rf925."""
from __future__ import annotations
from pathlib import Path
from aria_service.routes import aria as a

def _wa():
    return (Path(a.__file__).resolve().parents[2] / "services" / "wa-listener"
            / "aria_wa_listener.mjs").read_text(encoding="utf-8")

def test_rf940_needs_async_helper_present():
    wa = _wa()
    assert "function _needsAsyncChat(" in wa
    # three triggers: URL, re-attached document, large message
    assert "URL_RE.test(m)" in wa
    assert "[ATTACHED DOCUMENT" in wa
    assert "HEAVY_CHAT_CHARS" in wa

def test_rf940_wired_into_askaria():
    wa = _wa()
    assert "if (_needsAsyncChat(message))" in wa
    # the old URL-only gate is gone from askARIA's routing decision
    assert "if (URL_RE.test(message)) {" not in wa

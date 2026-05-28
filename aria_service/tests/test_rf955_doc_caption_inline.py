"""R-F955 — a document sent WITH a review caption is reviewed with the
freshly-extracted text attached INLINE, not via the fragile cache/re-attach.

Live (Korvera UTS Maintenance Services Agreement, 2026-05-28): the user attached
the .docx with the caption "review this contract"; the doc-read succeeded but the
review replied "no attached document text reached my context" — because the
caption routed to askARIA separately and the per-chat cache + async re-attach
didn't line up for that turn. Inline-attaching the just-extracted text removes
the race. Source-level assertions (the listener has no JS test harness).
"""
from __future__ import annotations

from pathlib import Path

from aria_service.routes import aria as a


def _wa() -> str:
    return (Path(a.__file__).resolve().parents[2] / "services" / "wa-listener"
            / "aria_wa_listener.mjs").read_text(encoding="utf-8")


def test_rf955_inline_attaches_doc_to_caption_review():
    wa = _wa()
    # the doc+caption branch builds an [ATTACHED DOCUMENT] block from the
    # freshly-extracted _cacheText and routes it through askARIA
    assert "if (text.trim() && _cacheText.length >= 200)" in wa
    assert "[ATTACHED DOCUMENT: ${filename}]\\n${_cacheText}" in wa
    assert "await askARIA(_reviewMsg" in wa


def test_rf955_skips_redundant_text_routing():
    wa = _wa()
    assert "let _docAnsweredCaption = false" in wa
    assert "_docAnsweredCaption = true" in wa
    # the text-routing gate must skip when the caption was already answered inline
    assert "if (!text.trim() || _docAnsweredCaption) continue;" in wa


def test_rf955_honest_when_extraction_empty():
    wa = _wa()
    # when nothing extractable came back, don't claim "I've read it / ask me anything"
    assert "couldn't extract readable text" in wa

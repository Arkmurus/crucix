"""R-F944 — chat-history compaction.

Live failure (Korvera UTS contract, 2026-05-27): R-F912 re-attached the 60K-char
contract to the message on every retry, and each turn was retained VERBATIM in
history. By ~70 messages the chat payload hit 156K tokens (489K chars of
history); the model could no longer attend to the CURRENT document and reviewed
only the first clauses, reporting the rest "not visible".

The fix strips [ATTACHED DOCUMENT] blocks out of HISTORY (re-attached fresh when
relevant) and caps each retained turn, so accumulated history can't drown the
live request. Shared by aria_chat + aria_chat_stream (§13).
"""
from __future__ import annotations

from aria_service.aria_engine import (
    _compact_history_content,
    _format_history_user_prompt,
)


def test_rf944_strips_attached_document_block():
    doc = "[ATTACHED DOCUMENT: contract.docx]\n" + ("CLAUSE TEXT " * 5000) + "\n[END ATTACHED DOCUMENT]"
    out = _compact_history_content("Please review this.\n" + doc + "\nThanks")
    assert "[ATTACHED DOCUMENT" not in out
    assert "CLAUSE TEXT" not in out
    assert "omitted from history" in out
    assert "Please review this." in out      # the human's words survive


def test_rf944_handles_slash_close_marker():
    doc = "[ATTACHED DOCUMENT: x]\nbody body body\n[/ATTACHED DOCUMENT]"
    assert "body" not in _compact_history_content("see: " + doc)


def test_rf944_caps_length():
    out = _compact_history_content("x" * 10000, max_chars=2000)
    assert len(out) <= 2000 + 8
    assert out.endswith("[…]")


def test_rf944_empty_safe():
    assert _compact_history_content("") == ""
    assert _compact_history_content(None) == ""


def test_rf944_short_history_preserved():
    history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello there"}]
    up = _format_history_user_prompt(history, "", "how are you?", "")
    assert "hi" in up and "hello there" in up and "how are you?" in up


def test_rf944_history_no_longer_drowns_current_document():
    """The exact live shape: 30 prior turns each re-attached the 60K contract."""
    big = "[ATTACHED DOCUMENT: korvera.docx]\n" + ("Clause body. " * 5000) + "\n[END ATTACHED DOCUMENT]"
    history = []
    for _ in range(30):
        history.append({"role": "user", "content": "Aria review this agreement\n" + big})
        history.append({"role": "assistant", "content": "Here is my review " + ("detail " * 500)})

    raw_history_chars = sum(len(m["content"]) for m in history)
    assert raw_history_chars > 1_000_000          # the bloat we're fixing (~the live 489K+ case)

    # The CURRENT document is supplied via context (the live re-attach), NOT history.
    current = "Aria, what does the termination clause say?"
    current_doc = "\n\n[ATTACHED DOCUMENT: korvera.docx]\nCLAUSE 13 TERMINATION: 90 days notice.\n[END ATTACHED DOCUMENT]"
    up = _format_history_user_prompt(history, "", current, current_doc)

    # current document + question preserved
    assert "CLAUSE 13 TERMINATION: 90 days notice." in up
    assert "what does the termination clause say" in up
    # but the 30 historical copies of the contract are gone
    assert "Clause body." not in up
    # and the payload is bounded (was >1M chars of history → now tens of K)
    assert len(up) < 80_000

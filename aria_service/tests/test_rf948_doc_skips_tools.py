"""R-F948 — when a document is attached, the chat path skips auto tool-crawl.

Live (Korvera UTS contract, 2026-05-27): WhatsApp reviews default to
auto_tools=True, so _detect_tool_intent spotted URLs embedded in the contract
(LinkedIn / email links in the signature block) and _execute_tool crawled them —
adding minutes and timing out the 10-minute review window. The document already
reaches the LLM directly, so for an attached-document message we skip tool
detection entirely. Both chat paths must gate identically (CLAUDE.md §13).
"""
from __future__ import annotations

import re

from aria_service.routes import aria as a


def _src() -> str:
    return open(a.__file__, encoding="utf-8").read()


def test_rf948_both_chat_paths_gate_on_attached_document():
    src = _src()
    # Both the sync chat path and the stream path must skip tools when a
    # document is attached — count the guarded auto_tools checks.
    guarded = re.findall(
        r'if req\.auto_tools and "\[ATTACHED DOCUMENT" not in \(req\.message or ""\)\s*:',
        src,
    )
    assert len(guarded) >= 2, f"expected both chat paths gated, found {len(guarded)}"


def test_rf948_no_bare_auto_tools_gate_remains():
    """Guard against regression: no `if req.auto_tools:` without the document
    check (which would re-enable crawling embedded contract URLs)."""
    src = _src()
    bare = re.findall(r'\n\s*if req\.auto_tools:\s*\n', src)
    assert bare == [], f"found ungated `if req.auto_tools:` — would re-crawl doc URLs: {len(bare)}"

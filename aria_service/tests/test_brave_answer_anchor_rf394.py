"""R-F394 — brave_answer chat dispatch applies entity-anchor extraction.

Live evidence 2026-05-13: ARIA self-reported that "What has Saudi imported
last year?" got passed verbatim to the search tool, returning cancer
immunotherapy + education policy papers (zero defence procurement).
Same root cause as R-F392 (deep_research full-sentence search).

The brave_answer tool was already rerouted to web_explorer.explore in
R-F336. R-F394 wraps the input through `_extract_search_anchor` from
deep_researcher (R-F392) so the question prefix is stripped before
search backends fire.

Source-level test: the handler must import the anchor extractor and
call it on the raw query.
"""
from __future__ import annotations

import pathlib


def _handler_block() -> str:
    src = pathlib.Path(
        "C:/code/crucix/aria_service/routes/aria.py"
    ).read_text(encoding="utf-8", errors="ignore")
    idx = src.find('if tool == "brave_answer":')
    assert idx > 0, "R-F394: brave_answer dispatch block not found"
    # Block ends at next tool dispatch or 6000 chars
    nxt = src.find('\n        if tool == "', idx + 1)
    if nxt < 0:
        nxt = idx + 6000
    return src[idx:nxt]


def test_rf394_handler_imports_extract_search_anchor():
    """The R-F392 anchor extractor must be imported inside the handler."""
    block = _handler_block()
    assert "_extract_search_anchor" in block, (
        "R-F394 regression: brave_answer handler does not import "
        "_extract_search_anchor — full sentence still passed to "
        "web_explorer."
    )
    assert "from ..intel.deep_researcher import _extract_search_anchor" in block, (
        "R-F394: explicit import path must remain so the dependency "
        "is visible at code-review time."
    )


def test_rf394_handler_uses_anchor_in_web_explorer_call():
    """The handler must pass `query` (anchor) to web_explorer.explore,
    not the raw question."""
    block = _handler_block()
    # Anchor is assigned to `query` (which is what web_explorer.explore uses)
    assert "query = _extract_search_anchor(raw_query)" in block, (
        "R-F394 regression: anchor not assigned to `query` variable."
    )
    # And the original raw input is preserved as raw_query for logging
    assert "raw_query =" in block, (
        "R-F394: raw_query variable should still exist for "
        "logging / diagnostics."
    )


def test_rf394_handler_logs_when_anchor_differs():
    """The handler must log when extraction actually changed the query
    so we can see in fly logs whether the fix is firing."""
    block = _handler_block()
    assert "R-F394" in block
    assert "logger.info" in block or "logging.info" in block


def test_rf394_handler_falls_back_safely_on_anchor_error():
    """If the anchor extractor throws (e.g. import failure), the
    handler must fall back to raw_query, not crash."""
    block = _handler_block()
    assert "except Exception:" in block
    assert "query = raw_query" in block, (
        "R-F394: missing safe fallback when anchor extraction throws."
    )


def test_rf394_anchor_extractor_strips_what_has_saudi_question():
    """The behavioural piece: feed the exact failing query from ARIA's
    self-assessment through the anchor extractor and assert it isn't
    returned verbatim."""
    from aria_service.intel.deep_researcher import _extract_search_anchor
    raw = "what has Saudi imported last year?"
    anchor = _extract_search_anchor(raw)
    assert anchor != raw, (
        "R-F394: anchor matches full sentence — extractor didn't strip."
    )
    assert "what has" not in anchor.lower()
    # The country regex picks up "Saudi" if "Arabia" is in the topic.
    # In this case, only "Saudi" is mentioned — country regex requires
    # "Saudi Arabia" so falls through to prefix stripping. Either way,
    # the result must be much shorter than the raw question.
    assert len(anchor) < len(raw) * 0.8, (
        f"R-F394: anchor barely shorter than raw. Got {anchor!r}"
    )

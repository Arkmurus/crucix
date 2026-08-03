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

from ._source_probe import repo_path


def _handler_block() -> str:
    src = pathlib.Path(
        repo_path("aria_service/routes/aria.py")
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


def test_rf394_handler_logs_when_anchor_differs(caplog, monkeypatch):
    """Driving the brave_answer tool with a full-sentence query must emit an
    INFO log on the module logger carrying the R-F394 marker, so fly logs show
    whether the anchor-extraction fix is firing.

    Rewritten R-F2784 (2026-07-19): the previous check matched the literal
    source spelling ``logger.info``/``logging.info``; production logs via the
    module alias ``_log.info`` (routes/aria.py ``_log = getLogger("aria.routes")``),
    so the static string-match was a spelling false-negative — a stale gate,
    not a real defect. This exercises the real handler instead (§23: assert the
    production contract, do not weaken).
    """
    import asyncio
    import logging

    from aria_service.intel import web_explorer

    async def _explore_boom(*_a, **_k):
        # Stub explore to raise: the R-F394 anchor log fires BEFORE explore is
        # awaited, and the handler's own try/except returns a graceful string —
        # so we prove the log without building a full ExploreResult.
        raise RuntimeError("web_explorer.explore stubbed for R-F2784")

    monkeypatch.setattr(web_explorer, "explore", _explore_boom)

    from aria_service.routes.aria import _execute_tool

    intent = {"tool": "brave_answer", "query": "what has Saudi imported last year?"}

    with caplog.at_level(logging.INFO, logger="aria.routes"):
        out = asyncio.run(_execute_tool(intent, llm=None))

    # Handler stayed on its feet after the stubbed explore error.
    assert "brave_answer" in out
    # The anchor-extraction log fired on the module logger with the R-F394 marker.
    rf394_logs = [
        r for r in caplog.records
        if r.name == "aria.routes" and "R-F394" in r.getMessage()
    ]
    assert rf394_logs, (
        "R-F394 regression: brave_answer handler did not log the extracted "
        "anchor — the anchor-extraction fix is not firing / not observable "
        "in fly logs."
    )
    assert rf394_logs[0].levelno == logging.INFO


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

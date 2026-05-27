"""R-F941 / R-F942 — the live "ARIA doesn't parse documents / temporarily
unavailable" root cause, found 2026-05-27 via a live CSG URL read
(trace tr_1779888228956_88ad3c, status=empty_response).

R-F941: the URL `read` tool built its Summary from extracted facts using the
keys text/claim/value — but facts carry topic/content (researcher.py:1731).
So a 16-fact read rendered an EMPTY Summary; the LLM had nothing to cite and
returned an empty answer. Fix: render facts with topic/content (mirroring the
proven crawl_website renderer).

R-F942: when the LLM reasons the whole turn inside <scratchpad> and emits no
user-facing text, strip() correctly empties the response → the WA user sees
"temporarily unavailable". Fix: never ship an empty answer — re-prompt once
with the scratchpad instruction removed, then a last-resort honest floor.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.routes import aria as a
from aria_service.intel import scratchpad as sp


# ── R-F941 — read tool-context renders fact CONTENT, not an empty summary ────

# The exact fact shape read_article returns (researcher.py:1731 schema).
_CSG_FACTS = [
    {"topic": "Business", "content": "CSG Defence provides sustainment, "
     "engineering and logistics for Australian defence platforms.",
     "confidence": "CONFIRMED", "category": "organisation"},
    {"topic": "HQ", "content": "Headquartered in Sydney, Australia.",
     "confidence": "PROBABLE", "category": "location"},
] + [
    {"topic": f"Fact{i}", "content": f"Specific detail number {i}.",
     "confidence": "ASSESSED"} for i in range(14)
]  # 16 facts total, matching the live incident


def _mock_read_article(facts, *, facts_learned=None, summary=None, hypothesis=None):
    async def _inner(llm, url, context=""):
        return {
            "url": url,
            "facts_learned": facts_learned if facts_learned is not None else len(facts),
            "facts": facts,
            "hypothesis": hypothesis,
            "summary": summary,
        }
    return _inner


def test_rf941_read_summary_renders_fact_content(monkeypatch):
    monkeypatch.setattr(a, "read_article", _mock_read_article(_CSG_FACTS))
    ctx = asyncio.run(a._execute_tool({"tool": "read", "url": "https://defence.csg.com/en"}, llm=object()))
    assert "[TOOL: read_article]" in ctx
    assert "Facts learned: 16" in ctx
    # THE FIX: the summary is no longer empty — actual fact content is present.
    assert "sustainment" in ctx and "Sydney" in ctx
    # Summary section carries real text, not just the count.
    body = ctx.split("Summary:", 1)[1]
    assert len(body.strip()) > 50, "Summary must carry citable fact content"


def test_rf941_regression_content_key_not_empty(monkeypatch):
    """The original bug: facts present but rendered to nothing because the
    builder looked for text/claim/value instead of content/topic."""
    monkeypatch.setattr(a, "read_article", _mock_read_article(_CSG_FACTS))
    ctx = asyncio.run(a._execute_tool({"tool": "read", "url": "https://x.test"}, llm=object()))
    summary = ctx.split("Summary:", 1)[1]
    # Would have been blank under the old text/claim/value lookup.
    assert "CSG Defence provides sustainment" in summary


def test_rf941_no_data_warning_when_genuinely_empty(monkeypatch):
    """A truly empty read still surfaces the no-data warning (not a fake summary)."""
    monkeypatch.setattr(a, "read_article", _mock_read_article([], facts_learned=0))
    ctx = asyncio.run(a._execute_tool({"tool": "read", "url": "https://empty.test"}, llm=object()))
    assert "Facts learned: 0" in ctx
    # _no_data_warning should have appended (it mentions the URL / no data).
    assert "empty.test" in ctx


def test_rf941_explicit_summary_preferred(monkeypatch):
    """If read_article returns a real summary, it wins over the fact fallback."""
    monkeypatch.setattr(a, "read_article",
                        _mock_read_article(_CSG_FACTS, summary="A crisp prose summary."))
    ctx = asyncio.run(a._execute_tool({"tool": "read", "url": "https://x.test"}, llm=object()))
    assert "A crisp prose summary." in ctx


# ── R-F942 — empty-response salvage building blocks ──────────────────────────

def test_rf942_scratchpad_only_output_strips_to_empty():
    """The trigger condition: the LLM put EVERYTHING in the scratchpad and
    produced no user-facing answer → strip() yields an empty answer."""
    raw = (
        "<scratchpad>\n"
        "1. The user asks what the firm does.\n"
        "2. The tool says 'Facts learned: 16' but Summary is empty.\n"
        "8. I must state the gap.\n"
        "</scratchpad>"
    )
    user_facing, scratch = sp.strip(raw)
    assert user_facing.strip() == "", "scratchpad-only output must leave no answer"
    assert "state the gap" in scratch, "scratchpad content is still captured"


def test_rf942_salvage_message_drops_scratchpad_instruction():
    """The salvage re-prompt removes the scratchpad instruction so the model
    produces a plain answer. Mirrors aria.py: message_for_llm.replace(_sp_prefix, '')."""
    prefix = sp.build_prefix("Assess this contract for sanctions exposure",
                             complexity="CRITICAL")
    assert prefix, "non-trivial CRITICAL turn must build a scratchpad prefix"
    message_for_llm = "USER QUESTION + tool data here" + prefix
    salvage = message_for_llm.replace(prefix, "") if prefix else message_for_llm
    assert "[CLAUSE 22" not in salvage
    assert "<scratchpad>" not in salvage
    assert salvage == "USER QUESTION + tool data here"


def test_rf942_salvage_block_present_in_chat_handler():
    """Source guard: the empty-response salvage + floor live in the chat path
    so an empty answer can never be returned to the user."""
    src = open(a.__file__, encoding="utf-8").read()
    assert "R-F942" in src
    assert "scratchpad_only_salvaged" in src
    assert "empty_response_fallback" in src
    # the salvage re-prompt must strip the scratchpad prefix before re-calling
    assert "message_for_llm.replace(_sp_prefix" in src

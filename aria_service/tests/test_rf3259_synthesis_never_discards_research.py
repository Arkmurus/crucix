"""R-F3259 — a failure BUILDING the assessment must not destroy the research.

`investigate()` gathers articles and facts, then formats them into a synthesis
prompt. The LLM CALL was already guarded (a timeout marks the run partial and
returns). The PROMPT CONSTRUCTION above it was not, and it indexed dicts built
from LLM output and scraped web content DIRECTLY:

    f"- [{f['confidence']}] {f['topic']}: {f['content'][:150]}"      # facts
    f"- {h['hypothesis']}" ... if topic.lower().split()[0] in ...    # hypotheses
    f"- {p['name']} ({p.get('role') ...})"                           # people

So one fact missing a key raised KeyError out of `investigate()`, and
`dd_orchestrator.py:6692` catches ANY exception from that call and turns it into
a data-gap string — discarding every article read and every fact learned.

That is exactly the failure R-F3018 fixed for the TIMEOUT path, in its own words:
"the result was not partial, it was zero". This is the same defect on the ERROR
path. The assessment is the LAST step; losing it must cost the assessment only.

`topic.lower().split()[0]` also raised IndexError on a whitespace-only subject —
same blast radius, same fix.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from aria_service.intel import deep_researcher as dr


class _FakeLLM:
    is_configured = True

    async def complete(self, *a, **k):
        class _R:
            text = '{"queries": ["acme"], "key_findings": ["x"], "risks": []}'
        return _R()


def _run(facts, hypotheses=None, topic="Acme Defence Ltd"):
    async def go():
        async def _search(q, *a, **k):
            return [{"title": "a1", "link": "https://example.com/1"}]

        ctxs = [
            patch.object(dr, "_web_search", new=_search),
            patch.object(dr, "_fetch_article_text", new=AsyncMock(return_value="body " * 40)),
            patch.object(dr, "_analyse_article", new=AsyncMock(return_value={"facts": facts})),
            patch.object(dr, "_process_analysis", new=AsyncMock(return_value=(len(facts), 0))),
            patch.object(dr, "_load_hypotheses",
                         new=AsyncMock(return_value=hypotheses if hypotheses is not None else [])),
            patch.object(dr, "_save_hypotheses", new=AsyncMock(return_value=None)),
            patch.object(dr, "_get_read_urls", new=AsyncMock(return_value=set())),
            patch.object(dr, "_mark_read", new=AsyncMock(return_value=None)),
            patch.object(dr, "search_knowledge", return_value=""),
        ]
        for c in ctxs:
            c.__enter__()
        try:
            return await dr.investigate(_FakeLLM(), topic, depth="quick",
                                        investigate_people=0)
        finally:
            for c in reversed(ctxs):
                c.__exit__(None, None, None)
    return asyncio.run(go())


# ── THE CAPABILITY TEST ───────────────────────────────────────────────────────
def test_a_malformed_fact_does_not_discard_the_whole_run() -> None:
    """A fact missing 'confidence'/'topic' used to KeyError out of investigate()."""
    out = _run([{"content": "a real discovered fact with no confidence key"}])

    assert isinstance(out, dict), "investigate() must return, not raise"
    assert out.get("articles_read", 0) >= 1, (
        "the article was read and then thrown away because the ASSESSMENT could "
        "not be formatted — the research must survive its own summary"
    )


def test_a_non_dict_fact_is_survivable() -> None:
    out = _run(["a bare string where a fact dict was expected"])
    assert out.get("articles_read", 0) >= 1


def test_a_malformed_hypothesis_is_survivable() -> None:
    """Hypotheses come from a persisted store written across many runs."""
    out = _run(
        [{"topic": "t", "content": "c", "confidence": "PROBABLE"}],
        hypotheses=[{"no_hypothesis_key": True}, {"hypothesis": None}],
    )
    assert out.get("articles_read", 0) >= 1


def test_whitespace_subject_does_not_index_error() -> None:
    """`topic.lower().split()[0]` raised IndexError on a blank-ish subject."""
    out = _run([{"topic": "t", "content": "c", "confidence": "PROBABLE"}],
               hypotheses=[{"hypothesis": "acme has undisclosed exposure"}],
               topic="   ")
    # a blank subject is refused honestly by R-F3258 rather than crashing
    assert isinstance(out, dict)
    assert out.get("error") or out.get("articles_read", 0) >= 0


def test_the_synthesis_failure_is_disclosed_not_hidden() -> None:
    """If the assessment could not be built, the caller must be told — otherwise
    'no findings' reads as 'nothing was found'."""
    out = _run([{"content": "fact with no confidence key"}])
    assert "synthesis_error" in out, "the result must carry the disclosure field"


def test_a_clean_run_reports_no_synthesis_error() -> None:
    """Regression: the normal path must not start claiming a failure."""
    out = _run([{"topic": "ownership", "content": "clean fact", "confidence": "PROBABLE"}])
    assert not out.get("synthesis_error")
    assert out.get("articles_read", 0) >= 1

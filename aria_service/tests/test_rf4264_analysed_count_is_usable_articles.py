"""R-F4264 / C-225 - "N articles analysed" counted tasks that analysed nothing.

THE LIVE SYMPTOM, from the delivered report
``ARIA_DD_Vigilo_Solutions_Limited_dd_9fe0e61e4a0c.pdf``. Its data-gap list said,
in one sentence::

    deep research was bounded at 37s and stopped after article read
    (7 of 12 articles analysed) - 0 article(s) analysed, 0 fact(s) retained

Seven and zero, about the same seven articles, eleven words apart. A reader has no
way to tell which number is the true one, and the first is the one that sounds like
coverage.

WHY THE TWO DISAGREE. ``_process_one_article`` returns ``None`` whenever the fetch
came back empty, the body was under 100 characters, or ``_analyse_article`` raised -
i.e. whenever the article was NOT analysed. Those ``None`` values were appended to
``parallel_results`` unfiltered, and the partial marker counted COMPLETED TASKS
(``len(_tasks) - len(_left)``). ``_retain`` skips falsy results before
``articles_read += 1``, so it counted analysed articles. One expression counted
attempts, the other counted work.

WHICH DIRECTION IT FAILS IN, and why that makes it worth a fixture. The overstated
number is the one describing COVERAGE. On the live run every one of the seven
completed fetches produced nothing usable - a total article-stage failure - and the
report described it as seven articles read. Absence of coverage rendered as
coverage is the class the whole DD honesty discipline exists to stop (CLAUDE.md
S1), and here it was self-evident on the page: the contradiction was printed and
nothing reconciled it.

THE FIXTURE reproduces the live shape rather than a proxy: some article fetches
return an unusable body and complete instantly, the rest hang past the budget so
the run is genuinely cut with tasks outstanding. Pre-fix ``stopped_after`` claims
the instant ones were analysed while ``articles_read`` is 0.
"""
from __future__ import annotations

import asyncio
import re

import pytest

from aria_service.intel import deep_researcher as dr


_N_ARTICLES = 12          # matches the live run's job count
_N_UNUSABLE = 7           # fetches that complete but yield nothing analysable
_DEADLINE_S = 9.0


class _StubLLM:
    name = "stub"
    is_configured = True

    async def complete(self, system_prompt, user_message, **kw):
        from aria_service.llm.provider import LLMResult
        return LLMResult(text='{"summary": "stub", "key_findings": []}', model="stub")


@pytest.fixture
def _engine_whose_articles_yield_nothing(monkeypatch):
    """Every fetch that COMPLETES returns an unusable body; the rest never finish.

    This is the live condition. `_process_one_article` returns None for a body
    under 100 chars, so the completed tasks analysed nothing at all.
    """
    articles = [
        {"title": f"Article {i}", "link": f"https://example.invalid/{i}",
         "snippet": "s", "source": "example.invalid"}
        for i in range(_N_ARTICLES)
    ]

    async def _search(query, *a, **kw):
        return list(articles)

    # Counter-driven, not URL-driven: the engine fans the same articles across
    # several query angles, so job identity is not recoverable from the link.
    calls = {"n": 0}

    async def _fetch(url, *a, **kw):
        calls["n"] += 1
        if calls["n"] <= _N_UNUSABLE:
            return ""          # completes immediately, nothing to analyse
        await asyncio.sleep(3600)   # still outstanding when the budget expires

    async def _analyse(*a, **kw):
        return {"facts": [{"fact": "f"}], "validates": None, "challenges": None}

    monkeypatch.setattr(dr, "_web_search", _search)
    monkeypatch.setattr(dr, "_fetch_article_text", _fetch)
    monkeypatch.setattr(dr, "_analyse_article", _analyse)
    monkeypatch.setattr(dr, "_process_analysis",
                        lambda *a, **kw: asyncio.sleep(0, result=(1, 0)))
    monkeypatch.setattr(dr, "_mark_read", lambda *a, **kw: asyncio.sleep(0))
    monkeypatch.setattr(dr, "_load_hypotheses", lambda *a, **kw: asyncio.sleep(0, result=[]))
    monkeypatch.setattr(dr, "_save_hypotheses", lambda *a, **kw: asyncio.sleep(0))
    monkeypatch.setattr(dr, "_get_read_urls", lambda *a, **kw: asyncio.sleep(0, result=set()))
    monkeypatch.setattr(dr, "search_knowledge", lambda *a, **kw: [])


async def _run() -> dict:
    return await dr.investigate(
        _StubLLM(), "Vigilo Solutions Limited", depth="quick",
        investigate_people=0, deadline_s=_DEADLINE_S,
    )


@pytest.mark.asyncio
async def test_stopped_after_does_not_claim_articles_that_analysed_nothing(
    _engine_whose_articles_yield_nothing,
):
    """THE CAPABILITY TEST - the customer-visible sentence must not contradict itself.

    dd_orchestrator renders `stopped_after` and `articles_read` into ONE data-gap
    line, so a disagreement between them is printed verbatim to the reader.
    """
    out = await _run()
    stage = str(out.get("stopped_after") or "")
    m = re.search(r"article read \((\d+) of (\d+) articles analysed\)", stage)
    assert m, f"expected the bounded article-read stage, got {stage!r}"

    claimed = int(m.group(1))
    retained = int(out.get("articles_read", 0))

    assert claimed <= retained, (
        f"stopped_after claims {claimed} article(s) analysed while the run reports "
        f"articles_read={retained}. dd_orchestrator prints both in one sentence, so "
        f"this reaches the customer as '{claimed} of {m.group(2)} articles analysed "
        f"- {retained} article(s) analysed'. A fetch that returned nothing analysable "
        "is not an analysed article."
    )


@pytest.mark.asyncio
async def test_progress_dict_does_not_overstate_analysed_either(
    _engine_whose_articles_yield_nothing, monkeypatch,
):
    """The SAME overcount reaches the report by a second path.

    R-F3316's `progress` dict is what the report falls back to when the caller's
    hard `wait_for` backstop destroys this frame. Fixing only the returned
    `stopped_after` would leave the overstated number reaching the reader whenever
    the hard cancel lands - the harder-to-notice half of the same defect.
    """
    progress: dict = {}
    await dr.investigate(
        _StubLLM(), "Vigilo Solutions Limited", depth="quick",
        investigate_people=0, deadline_s=_DEADLINE_S, progress=progress,
    )
    if "analysed" in progress:
        assert progress["analysed"] <= progress.get("retained", 0), (
            f"progress reports analysed={progress['analysed']} but "
            f"retained={progress.get('retained', 0)}; unusable fetches are being "
            "counted as analysed articles here too"
        )

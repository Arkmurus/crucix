"""Brave Answers — R-F320 REMOVED.

Operator directive 2026-05-11: "please remove brave then... lets focus
reducing dependency". This module is now a permanent stub. ARIA mirrors
Claude per the `aria_mirrors_claude` memory: no paid search dependency.

Every public function returns an explicit "removed" sentinel so any
caller that still imports brave_answers doesn't crash, but no Brave API
call is ever made. The original implementation is in git history at
HEAD ~ R-F319; restore by reverting R-F320 if Brave is ever rehabilitated.

Replacement path:
  - For grounded factual answers: ARIA's free multi-backend search via
    `web_search.search_multilingual` + `web_explorer.explore` does the
    same job using Google News + Bing News + DDG + Crossref + OpenAlex +
    Semantic Scholar + RAG-first memory.
  - For semantic answer-style retrieval: rag_store.search() + the
    deep_research escalation path produce the equivalent narrative
    output without paying Brave's $0.04/call.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("aria.brave_answers")

# R-F320: every entry point returns the same removed sentinel. The shape
# matches what callers used to consume so they fall through cleanly.
_REMOVED_RESULT: dict = {
    "ok": False,
    "removed": True,
    "reason": (
        "Brave Answers REMOVED in R-F320 (2026-05-11). Use web_explorer.explore "
        "or web_search.search_multilingual instead — same content, zero cost."
    ),
    "answer": None,
    "citations": [],
    "cost_usd": 0.0,
}


async def ask(query: str, **_kwargs) -> dict:
    """R-F320 stub. Returns the removed sentinel."""
    logger.debug("brave_answers.ask called (R-F320 stub): %r", query[:80])
    return dict(_REMOVED_RESULT)


async def fetch_answer(*_args, **_kwargs) -> dict:
    """R-F320 stub. Returns the removed sentinel."""
    return dict(_REMOVED_RESULT)


async def get_month_spend() -> dict:
    """R-F320 stub. Brave spend is $0 by definition now."""
    return {
        "month": "n/a",
        "spend_usd": 0.0,
        "call_count": 0,
        "cost_per_call_usd": 0.0,
        "removed": True,
        "note": "Brave Answers removed in R-F320 — spend is structurally zero.",
    }


def is_enabled() -> bool:
    """R-F320: Brave is permanently disabled."""
    return False

"""R-F1660 — search-sovereignty regression guard.

Operator directive 2026-06-18 (memory: aria_sovereignty_no_new_dependencies):
ARIA must be independent — NO third-party web-search API. Brave is "not an
option". This guard FAILS if a third-party search-API call is ever
reintroduced into the search paths, so the sovereignty decision can't
silently drift back in a future edit.

These are source-level guards (cheap, deterministic) plus one behavioural
assertion that researcher.web_search reports a keyless provider.
"""
import inspect
import re

import pytest

from aria_service.intel import researcher, web_search


# Live third-party search-API signatures that must never appear in the
# search code paths. (brave_answer / prior_brave are a historical RAG
# data LABEL, not a live call — excluded.)
_FORBIDDEN = re.compile(
    r"api\.search\.brave\.com|X-Subscription-Token|BRAVE_SEARCH_API_KEY|"
    r"api\.bing\.microsoft\.com|serpapi|google\.com/customsearch",
    re.IGNORECASE,
)


def _strip_brave_answer_label(src: str) -> str:
    # Remove the benign historical RAG label so it doesn't false-trip.
    return re.sub(r"brave_answer|prior_brave", "", src, flags=re.IGNORECASE)


def test_researcher_web_search_has_no_third_party_search_api():
    src = _strip_brave_answer_label(inspect.getsource(researcher.web_search))
    hit = _FORBIDDEN.search(src)
    assert hit is None, (
        f"R-F1660: third-party search-API reference reintroduced into "
        f"researcher.web_search: {hit.group(0)!r}. ARIA must stay sovereign "
        f"(no Brave/Bing/SerpAPI/Google CSE). Use keyless backends + own index."
    )


def test_rf3120_brave_is_the_sanctioned_paid_primary_not_a_forbidden_stub():
    """R-F3120 — THIS GUARD ASSERTED A REVERSED DECISION AND HAD BEEN RED FOR WEEKS.

    R-F1660 (2026-06) froze "no Brave, ever": `_search_brave` had to stay an R-F320
    stub. That decision was REVERSED. CLAUDE.md §18 records it explicitly — Brave is
    LIVE, PAID and ARIA's PRIMARY user-facing search (R-F2318 restored the real
    backend, R-F2637 corrected the stale "declined" line that would have led a future
    agent to rip out working primary search). The operator reaffirmed it on
    2026-07-26: the DD tools run on Claude + Brave.

    So the guard has been failing at HEAD ever since R-F2318, asserting a policy the
    project no longer holds — a red test nobody retired, which is the mirror of the
    R-F3096 lesson: a guard is only as good as the decision behind it, and a guard
    defending a superseded decision is misinformation living in the suite.

    What is STILL binding is narrower and is what this now checks: no third-party
    search API OTHER than the sanctioned Brave primary may appear, and the continuous
    researcher (below) must stay on the free stack.
    """
    src = inspect.getsource(web_search._search_brave)
    for forbidden in ("api.bing.microsoft.com", "serpapi", "google.com/customsearch"):
        assert forbidden not in src.lower(), (
            f"R-F3120: {forbidden} is NOT a sanctioned search provider — only Brave is."
        )
    # And it must be the REAL Brave backend, not silently re-stubbed: a DD that
    # believes it is searching Brave while calling nothing is the false clean this
    # project exists to prevent.
    # The endpoint itself is a module constant, so assert on the two things that
    # only a REAL authenticated call carries: the subscription header and the key.
    assert "X-Subscription-Token" in src and "BRAVE_SEARCH_API_KEY" in src, (
        "R-F3120: _search_brave has been re-stubbed. Brave is the paid PRIMARY for "
        "user-facing DD/research search (CLAUDE.md §18, R-F2318/R-F2637) — a stub "
        "here silently removes the search tier the DD depends on."
    )


@pytest.mark.asyncio
async def test_researcher_web_search_never_reports_brave_provider():
    # The function must still return a well-formed dict and must NEVER claim
    # provider 'brave' (network-independent invariant — the only forbidden
    # provider is the third-party one we removed).
    out = await researcher.web_search("aria sovereignty smoke test", max_results=1)
    assert isinstance(out, dict)
    assert "results" in out and "ok" in out
    assert out.get("provider") != "brave", (
        "R-F1660: researcher.web_search must never report the Brave provider."
    )

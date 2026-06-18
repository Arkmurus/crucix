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


def test_web_search_brave_backend_is_permanent_stub():
    # web_search._search_brave must remain a no-op stub (R-F320) — it must
    # NOT make a network call.
    src = inspect.getsource(web_search._search_brave)
    assert _FORBIDDEN.search(src) is None, (
        "R-F1660: web_search._search_brave must stay a stub — no Brave API call."
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

"""R-F3844 — a search backend that answers with NOISE must not look like success.

THE DEFECT, reproduced live from inside aria-intel on 2026-08-11.

The same due-diligence query, run four times two seconds apart:

    '"Rolls-Royce Holdings plc" owner OR shareholder OR "beneficial owner"'

      run 1  "Oversæt dokumenter og websites - Google Help"   (Danish)
      run 2  "Nova Launcher FAQ"                              (n=40, not 10)
      run 3  "Confused about HL SIPP Interest — MoneySavingExpert"
      run 4  "Outlook"

Four identical inputs, four unrelated result sets, all `engine=bing`. The query has
ZERO influence on the output — which rules out query mangling, because a deterministic
bug returns the same wrong answer twice. SearXNG is serving result sets that belong to
other queries.

Why: the instance is comprehensively degraded. Fourteen engines carry errors —
`google` SearxEngineCaptchaException, `mojeek`/`qwant` SearxEngineAccessDeniedException,
`duckduckgo` ConnectTimeout, `brave.news` TooManyRequests, and `bing` — the one that
still answers — ReadTimeout/TimeoutException.

WHY THIS IS THE REAL DEFECT AND THE PORN WAS A SYMPTOM. Every domain in a result set
was auto-registered permanently (researcher.py:1747, now gated by R-F3820), so noise
SERPs seeded the registry with porn and gambling farms. Fixing the registry without
fixing this treats the stain. But the deeper problem is not even the registration: it
is that a backend which CANNOT ANSWER returns ten well-formed results, `ok: True`, no
error and no degraded flag — so nothing downstream can distinguish intelligence from
noise. That is the §22 failure this repo keeps recording: an absence dressed as a
measurement.

THE GATE IS DELIBERATELY CONSERVATIVE. It fires only when NOT ONE result bears ANY
lexical relation to the query. A legitimate result set almost always shares something
with its query; the observed pathology shares nothing at all. Requiring total absence
means a merely-poor result set still passes — this catches "answered a different
question", not "answered badly", and it must never start editorialising about quality.
"""
from __future__ import annotations

import pytest

from aria_service.intel import search_searxng as sx


DD_QUERY = '"Rolls-Royce Holdings plc" owner OR shareholder OR "beneficial owner"'

# The literal titles the live instance returned for that query.
OBSERVED_NOISE = [
    {"title": "Oversæt dokumenter og websites - Google Help", "url": "https://support.google.com/", "snippet": ""},
    {"title": "Nova Launcher FAQ", "url": "https://novalauncher.com/faq", "snippet": ""},
    {"title": "Confused about HL SIPP Interest earned - MoneySavingExpert", "url": "https://forums.moneysavingexpert.com/x", "snippet": ""},
    {"title": "Outlook", "url": "https://outlook.live.com/", "snippet": ""},
]

REAL_RESULTS = [
    {"title": "Rolls-Royce Holdings plc — major shareholders", "url": "https://www.rolls-royce.com/investors", "snippet": "beneficial ownership disclosures"},
    {"title": "RR Holdings annual report", "url": "https://find-and-update.company-information.service.gov.uk/x", "snippet": "shareholder register"},
]


def test_the_observed_live_noise_is_detected():
    """THE CASE THAT HAPPENED. Not one of these relates to the query."""
    assert sx._is_query_independent(DD_QUERY, OBSERVED_NOISE) is True


def test_a_genuine_result_set_is_not_flagged():
    """The half that keeps this a gate and not a wall — if it flagged real results
    it would blind ARIA's search entirely, which is worse than the noise."""
    assert sx._is_query_independent(DD_QUERY, REAL_RESULTS) is False


def test_one_relevant_result_among_noise_is_enough_to_pass():
    """Deliberately conservative: this catches 'answered a DIFFERENT question', not
    'answered badly'. A partially-poor set must still get through."""
    mixed = OBSERVED_NOISE + [REAL_RESULTS[0]]
    assert sx._is_query_independent(DD_QUERY, mixed) is False


def test_an_empty_result_set_is_not_called_noise():
    """Zero results is a legitimate answer ('nothing found') and is already visible
    as count=0. Calling it noise would convert an honest empty into a false alarm."""
    assert sx._is_query_independent(DD_QUERY, []) is False


def test_a_query_with_no_usable_tokens_cannot_judge():
    """Operators-only queries ("OR", quotes, punctuation) leave nothing to match on.
    With no basis to judge, the gate must NOT fire — refusing to measure is not the
    same as measuring a failure."""
    assert sx._is_query_independent('OR "" AND', OBSERVED_NOISE) is False


@pytest.mark.parametrize("query,titles,expect", [
    # matching on the entity alone is enough
    ("Chemring Group PLC directors", [{"title": "Chemring Group PLC board", "url": "", "snippet": ""}], False),
    # a stopword/short-token coincidence must NOT rescue a noise set
    ("BAE Systems plc fraud investigation", [{"title": "An FAQ on to the a", "url": "", "snippet": ""}], True),
])
def test_token_matching_ignores_short_and_common_tokens(query, titles, expect):
    assert sx._is_query_independent(query, titles) is expect


# ── the adapter must ACT on it, not merely be able to detect it ──────────────

@pytest.mark.asyncio
async def test_search_reports_noise_as_a_FAILURE_not_a_success(monkeypatch):
    """CAPABILITY. The whole defect is that noise arrived as ok:True with ten
    well-formed results. It must arrive as a failure with a stated reason."""
    class _Resp:
        status_code = 200
        def json(self):
            return {"results": [dict(r, content=r["snippet"], engine="bing")
                                for r in OBSERVED_NOISE]}

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return _Resp()

    monkeypatch.setenv("SEARXNG_URL", "http://searxng.test:8080")
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    out = await sx.search(DD_QUERY, count=10)

    assert out["ok"] is False, "a backend that answered a different question did NOT succeed"
    assert "noise" in (out.get("error") or "").lower()
    assert out["results"] == [], "noise must not be passed downstream as intelligence"


@pytest.mark.asyncio
async def test_search_still_returns_genuine_results(monkeypatch):
    """The gate must not cost ARIA her working search."""
    class _Resp:
        status_code = 200
        def json(self):
            return {"results": [dict(r, content=r["snippet"], engine="bing")
                                for r in REAL_RESULTS]}

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return _Resp()

    monkeypatch.setenv("SEARXNG_URL", "http://searxng.test:8080")
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    out = await sx.search(DD_QUERY, count=10)
    assert out["ok"] is True
    assert len(out["results"]) == 2

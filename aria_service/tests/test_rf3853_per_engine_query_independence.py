"""R-F3853 — a single ENGINE that answered a different question must not ride
through on the back of a good one.

THE MEASUREMENT THAT PRODUCED THIS. Live from inside aria-intel, 2026-08-11,
`engines=bing` with token-overlap scoring against the query:

    "Microsoft Corporation"           9/10 related    ← popular: bing is correct
    "BAE Systems"                     9/10 related
    "London weather forecast"        10/10 related
    "Rosoboronexport"                 0/10 related    ← niche: pure junk
    "Modirum Gespi Ltd"               0/10 related
    "qwzzxlkj nonexistent entity 99"  0/10 related

Bing is not mixing responses (the R-F3849 hypothesis, disproven). It serves a
soft-404 / trending page for queries it has no hits on, and SearXNG scrapes that
page into ten well-formed results. The page rotates per request, which is what
made one query look like it returned four different answers.

WHY THE R-F3844 GATE DOES NOT COVER THIS. R-F3844 asks whether the MERGED set is
unrelated to the query. That was sufficient while bing was the only engine — all
ten results were junk, so the set was unrelated and the whole thing was rejected.
The moment `yep` is enabled (R-F3853, searxng/settings.yml) a niche query returns
~20 genuinely relevant yep results ALONGSIDE ~10 bing artefacts. The merged set
now plainly relates to the query, R-F3844 correctly passes it, and the artefacts
ride through diluted — and diluted junk is precisely what a citation gets drawn
from. That is the mechanism by which a French Chrome help page was cited as press
coverage in dd_92f9d77b8886 (C-19).

WHAT THIS IS NOT. R-F3844's own docstring warns that a search gate which
editorialises will eventually suppress real intelligence, which is worse than the
noise it removes. This makes no judgement about whether a result is GOOD. It asks
the same binary question R-F3844 asks — did this source answer THIS query at
all? — once per engine. One relating result anywhere in an engine's contribution
keeps all of that engine's results.
"""
from __future__ import annotations

from aria_service.intel import search_searxng as sx


def _r(engine: str, title: str, url: str = "", snippet: str = "") -> dict:
    return {"engine": engine, "title": title, "url": url, "snippet": snippet}


# The real shape: yep answers the query, bing serves its trending page.
_YEP_GOOD = [
    _r("yep", "US sanctions Russian military officials — Treasury",
       "https://tass.com/world/1422465"),
    _r("yep", "Did Trump Lift Sanctions on Russian Arms Exporter Rosoboronexport?",
       "https://www.kyivpost.com/post/55650"),
]
_BING_JUNK = [
    _r("bing", "HEALTH: Longevity for All", "https://health.com/a"),
    _r("bing", "Why Am I Dizzy? 13 Possible Causes", "https://health.com/b"),
    _r("bing", "New fonts | dafont.com", "https://dafont.com"),
]


def test_the_junk_engine_is_dropped_and_the_good_one_survives():
    """The core contract. This FAILS before R-F3853: the merged set relates to the
    query, so nothing downstream had any reason to remove bing's three artefacts."""
    kept, dropped = sx._drop_query_independent_engines(
        "Rosoboronexport sanctions", _YEP_GOOD + _BING_JUNK)

    assert dropped == {"bing": 3}, "bing answered a different question — drop it"
    assert kept == _YEP_GOOD, "yep answered the query — every one of its rows stays"
    assert all(r["engine"] == "yep" for r in kept)


def test_one_relating_result_rescues_the_whole_engine():
    """The asymmetry that keeps this from becoming a quality filter. An engine that
    is merely WEAK — one hit and two misses — is not answering a different
    question, so it keeps all three rows."""
    weak = [
        _r("bing", "Rosoboronexport — Wikipedia", "https://en.wikipedia.org/wiki/x"),
        _r("bing", "Unrelated page one"),
        _r("bing", "Unrelated page two"),
    ]
    kept, dropped = sx._drop_query_independent_engines(
        "Rosoboronexport sanctions", _YEP_GOOD + weak)

    assert dropped == {}
    assert len(kept) == 5


def test_a_single_engine_set_is_left_to_the_whole_set_check():
    """With one contributor there is nothing to compare against, and dropping it
    would just be R-F3844 with a worse name. R-F3844 still runs afterwards as the
    backstop, so an all-junk single-engine set is still rejected — by that gate."""
    kept, dropped = sx._drop_query_independent_engines(
        "Rosoboronexport sanctions", list(_BING_JUNK))

    assert dropped == {}
    assert kept == _BING_JUNK


def test_the_popular_query_case_is_untouched():
    """Bing is CORRECT for popular queries (9/10 measured) and must not be
    penalised for the niche failure. Nothing is dropped when both engines answer."""
    both = [
        _r("yep", "BAE Systems plc annual report 2025"),
        _r("bing", "Home | BAE Systems", "https://baesystems.com"),
        _r("bing", "BAE Systems - Wikipedia", "https://en.wikipedia.org/wiki/BAE"),
    ]
    kept, dropped = sx._drop_query_independent_engines("BAE Systems", both)

    assert dropped == {}
    assert len(kept) == 3


def test_an_unjudgeable_query_drops_nothing():
    """No usable tokens means no basis to judge. Refusing to measure is not the
    same as measuring a failure (§22) — so nothing is removed."""
    kept, dropped = sx._drop_query_independent_engines("a of 12", _YEP_GOOD + _BING_JUNK)

    assert dropped == {}
    assert len(kept) == 5


def test_the_drop_is_reported_on_the_success_payload():
    """Silence is the failure mode this whole incident was. A caller must be able
    to SEE that a source was withheld, not infer it from a smaller count."""
    from aria_service.tests._source_probe import function_source

    src = function_source(sx, "search")
    assert "dropped_engines" in src, (
        "a withheld engine must be surfaced on the result, or a degraded source "
        "is indistinguishable from a quiet one")
    assert "_drop_query_independent_engines" in src


def test_the_engine_failure_is_wired_to_the_brain():
    """§21a — ARIA must KNOW which source is answering the wrong question. The
    52-day silent run is the reason this rule exists."""
    from aria_service.tests._source_probe import function_source

    src = function_source(sx, "search")
    head = src.split("R-F3844 — a result set unrelated")[0]
    assert "wire_failure" in head and "search_backend_failure" in head

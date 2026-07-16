"""R-F2669 — the LIVE re-fetch that computes each cited source's content-story
fingerprint (what the offline eval mocked via the golden `story` field).

Proves: near-identical bodies (wire syndication) fingerprint the SAME → one origin;
distinct stories fingerprint differently → separate origins; a failed re-fetch is
DROPPED (never counted) so it can't create a false positive.
"""

from __future__ import annotations

from typing import Any

import pytest

from aria_service.intel.dd_independent_verifier import (
    assess_independent_verification,
    cluster_stories,
    content_shingles,
    refetch_story_ids,
)

_WIRE_STORY = (
    "acme systems today announced the acquisition of beta defence in a deal valued at "
    "two billion dollars pending regulatory approval the company said the transaction "
    "would strengthen its position in the naval systems market analysts welcomed the move "
    "while noting integration risks over the coming eighteen months across three continents"
)
_DIFFERENT_STORY = (
    "the ministry issued new export control guidance affecting dual use goods shipped to "
    "several jurisdictions exporters must now file additional declarations and undergo "
    "enhanced screening before licences are granted officials said the changes take effect "
    "next quarter and apply to a broad range of controlled technologies and components"
)


def test_near_duplicate_bodies_cluster_together() -> None:
    """Jaccard clustering is robust to a site's differing header/footer — the same wire
    body republished with different chrome lands in ONE cluster."""
    a = content_shingles(_WIRE_STORY)
    b = content_shingles("SITE HEADER — SUBSCRIBE NOW. " + _WIRE_STORY + " Read more.")
    c = content_shingles(_DIFFERENT_STORY)
    assert a and b and c
    clustered = cluster_stories({"u_a": a, "u_b": b, "u_c": c})
    assert clustered["u_a"] == clustered["u_b"], "same body (diff chrome) → same story"
    assert clustered["u_c"] != clustered["u_a"], "different story → different cluster"
    # too little content → empty shingles → no story id
    assert content_shingles("short blurb") == frozenset()
    assert cluster_stories({"u_short": content_shingles("short blurb")}) == {}


@pytest.mark.asyncio
async def test_refetch_maps_urls_to_story_ids(monkeypatch) -> None:
    pages = {
        "http://a.com/1": ("ok", _WIRE_STORY),
        "http://b.com/2": ("ok", _DIFFERENT_STORY),
        "http://dead.com/3": ("http_404", ""),
    }

    async def _fetch(url):
        return pages[url]

    ids = await refetch_story_ids(list(pages), fetcher=_fetch)
    assert ids["http://a.com/1"] and ids["http://b.com/2"]
    assert ids["http://a.com/1"] != ids["http://b.com/2"]
    assert ids["http://dead.com/3"] is None  # failed / empty → None


@pytest.mark.asyncio
async def test_assess_collapses_wire_syndication_to_one_origin(monkeypatch) -> None:
    """THE point: one wire story republished on 3 sites = ONE independent origin."""
    report = {"digital": {"press_coverage": [
        {"url": "http://reuters.com/x"},
        {"url": "http://uk.reuters.com/x"},
        {"url": "http://somepaper.com/reuters-x"},
    ]}}

    async def _fetch(url):
        return ("ok", _WIRE_STORY)  # ALL three carry the same wire story

    res = await assess_independent_verification(report, fetcher=_fetch)
    assert res["refetched_ok"] == 3
    assert res["independent_press_origins"] == 1
    assert res["press_independently_corroborated"] is False


@pytest.mark.asyncio
async def test_assess_counts_distinct_stories(monkeypatch) -> None:
    report = {"digital": {"press_coverage": [
        {"url": "http://bbc.co.uk/a"},
        {"url": "http://theguardian.com/b"},
    ]}}
    bodies = {"http://bbc.co.uk/a": _WIRE_STORY, "http://theguardian.com/b": _DIFFERENT_STORY}

    async def _fetch(url):
        return ("ok", bodies[url])

    res = await assess_independent_verification(report, fetcher=_fetch)
    assert res["independent_press_origins"] == 2
    assert res["press_independently_corroborated"] is True


@pytest.mark.asyncio
async def test_assess_drops_failed_refetch_never_a_false_positive(monkeypatch) -> None:
    """A source that couldn't be re-fetched must be DROPPED, not counted — so it can
    never inflate the independent-origin count."""
    report = {"digital": {"press_coverage": [
        {"url": "http://ok.com/a"},
        {"url": "http://dead1.com/b"},
        {"url": "http://dead2.com/c"},
    ]}}

    async def _fetch(url):
        if "dead" in url:
            raise RuntimeError("connection refused")
        return ("ok", _WIRE_STORY)

    res = await assess_independent_verification(report, fetcher=_fetch)
    assert res["refetched_ok"] == 1
    # only the one verified source → cannot be independently corroborated (needs >=2)
    assert res["independent_press_origins"] == 1
    assert res["press_independently_corroborated"] is False

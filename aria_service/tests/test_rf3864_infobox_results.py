"""R-F3864 — the API tier answers in a different SHAPE, and we discarded it.

R-F3863 re-enabled wikipedia + wikidata after a direct probe proved Wikimedia
refuses UNIDENTIFIED CLIENTS rather than datacenter IPs:

    User-Agent: python-requests/2.0                      -> HTTP 403
    User-Agent: AriaIntelligence/1.0 (aria@arkmurus.com) -> HTTP 200
        hits = ['Rosoboronexport', 'KAB-1500', 'Aleksandr Mikheyev']

After deploying that, both engines reported `n=0` with NO errors — which reads
like "enabled but useless" and would very plausibly have been "fixed" by
disabling them again. They were working the whole time. SearXNG returns an
encyclopedic hit as an INFOBOX, not a result row, and this adapter only ever read
`data["results"]`. Measured live for "Rosoboronexport": `results: 0, infoboxes: 1`,
carrying "JSC Rosoboronexport is the sole state intermediary agency for Russia's
exports/imports of...".

That is precisely the entity grounding a DD needs on a niche subject — the case
bing answers with a trending page — and it was being dropped at the last step.

A zero that means "wrong field read" is indistinguishable from a zero that means
"nothing found", which is the same absence-collapsing-into-a-measurement class as
the rest of this incident.
"""
from __future__ import annotations

import pytest

from aria_service.intel import search_searxng as sx


_LIVE_SHAPE = {
    "results": [],
    "infoboxes": [{
        "infobox": "Rosoboronexport",
        "id": "https://en.wikipedia.org/wiki/Rosoboronexport",
        "content": ("JSC Rosoboronexport is the sole state intermediary agency for "
                    "Russia's exports/imports of defence-related products."),
        "urls": [{"title": "Wikipedia", "url": "https://en.wikipedia.org/wiki/Rosoboronexport"}],
        "engines": ["wikipedia", "wikidata"],
    }],
}


def test_an_infobox_becomes_a_usable_result_row():
    rows = sx._infoboxes_to_results(_LIVE_SHAPE)

    assert len(rows) == 1
    assert rows[0]["title"] == "Rosoboronexport"
    assert "state intermediary agency" in rows[0]["snippet"]
    assert rows[0]["url"] == "https://en.wikipedia.org/wiki/Rosoboronexport"
    assert rows[0]["engine"] == "wikipedia"      # attributable, not "searxng"


def test_a_merged_infobox_with_no_engine_key_is_still_attributed():
    """`engine` is absent on a merged infobox; `engines` holds the contributors.
    An unattributable row would be invisible to the R-F3853 per-engine gate."""
    rows = sx._infoboxes_to_results(_LIVE_SHAPE)
    assert rows[0]["engine"] and rows[0]["engine"] != "infobox"


def test_an_infobox_without_urls_falls_back_to_its_id():
    data = {"infoboxes": [{"infobox": "X", "content": "c", "id": "https://x/y"}]}
    assert sx._infoboxes_to_results(data)[0]["url"] == "https://x/y"


def test_empty_and_malformed_infoboxes_are_ignored():
    data = {"infoboxes": [None, 42, {}, {"infobox": "", "content": ""}]}
    assert sx._infoboxes_to_results(data) == []


def test_missing_infoboxes_key_is_not_an_error():
    assert sx._infoboxes_to_results({"results": []}) == []


@pytest.mark.asyncio
async def test_search_surfaces_the_infobox_the_adapter_used_to_drop(monkeypatch):
    """Capability test (§3c): the real `search()` path on the exact live payload
    shape. Before R-F3864 this returned count=0 for a query the backend ANSWERED."""
    class _Resp:
        status_code = 200
        def json(self): return _LIVE_SHAPE

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setenv("SEARXNG_URL", "http://searxng.invalid:8080")

    out = await sx.search("Rosoboronexport", count=10)

    assert out["ok"] is True
    assert out["count"] == 1, "the infobox IS the answer; dropping it reported a false zero"
    assert out["results"][0]["title"] == "Rosoboronexport"


@pytest.mark.asyncio
async def test_an_infobox_is_still_judged_by_the_relevance_gates(monkeypatch):
    """Provenance earns nothing. An infobox unrelated to the query is rejected by
    R-F3844 exactly like any other row — a trusted source is not a trusted answer."""
    off_topic = {
        "results": [],
        "infoboxes": [{"infobox": "Puma SE", "content": "German sportswear company",
                       "urls": [{"url": "https://en.wikipedia.org/wiki/Puma"}],
                       "engines": ["wikipedia"]}],
    }

    class _Resp:
        status_code = 200
        def json(self): return off_topic

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setenv("SEARXNG_URL", "http://searxng.invalid:8080")

    out = await sx.search("Rosoboronexport sanctions", count=10)

    assert out["ok"] is False
    assert out.get("error", "").startswith("noise")

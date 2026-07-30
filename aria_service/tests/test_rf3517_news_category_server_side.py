"""R-F3517 — the article list and the category breakdown disagreed.

public/news.html fetched `/api/aria/news/recent?limit=100` and then filtered
those 100 IN THE BROWSER, while the breakdown beside it rendered
`stats.by_category`, computed server-side over all _MAX_ARTICLES (1,000). Both
numbers were true and they measured different populations, so a category could
render "No articles" while the panel next to it said that category had some.

The fix is server-side filtering over the SAME population the stats aggregate —
not a relabelled empty state, which would hide the contradiction rather than
remove it.
"""
from __future__ import annotations

import json
import pytest

from aria_service.intel import news_monitor as nm


def _mk(n, cat):
    return json.dumps({"url": f"https://x/{cat}{n}", "title": f"t{n}",
                       "category": cat})


@pytest.mark.asyncio
async def test_a_category_beyond_the_newest_100_is_still_found(monkeypatch):
    """The exact live symptom: the only match sits deeper than the UI's page."""
    rows = [_mk(i, "global_defence") for i in range(150)] + [_mk(0, "cyber")]

    async def _lrange(_k, start, end):
        return rows[start:end + 1]

    monkeypatch.setattr(nm.rs, "lrange", _lrange)
    out = await nm.get_recent_articles(limit=100, category="cyber")
    assert len(out) == 1, (
        "a category present in the store returned nothing because the filter "
        "only saw the newest 100"
    )
    assert out[0]["category"] == "cyber"


@pytest.mark.asyncio
async def test_unfiltered_path_stays_cheap(monkeypatch):
    """No category = unchanged newest-N read, not a full scan."""
    seen = {}

    async def _lrange(_k, start, end):
        seen["end"] = end
        return [_mk(i, "global_defence") for i in range(end + 1)]

    monkeypatch.setattr(nm.rs, "lrange", _lrange)
    await nm.get_recent_articles(limit=10)
    assert seen["end"] == 9, f"unfiltered read widened to {seen['end']}"


@pytest.mark.asyncio
async def test_filter_is_case_insensitive_and_bounded(monkeypatch):
    rows = [_mk(i, "cyber") for i in range(50)]

    async def _lrange(_k, start, end):
        return rows[start:end + 1]

    monkeypatch.setattr(nm.rs, "lrange", _lrange)
    out = await nm.get_recent_articles(limit=5, category="CYBER")
    assert len(out) == 5, "limit not respected on the filtered path"

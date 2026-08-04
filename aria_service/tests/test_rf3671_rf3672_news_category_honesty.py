"""R-F3671 / R-F3672 — the news page offered category filters it could not serve.

Reported live 2026-08-04: https://imaria.io/news.html rendered NINE filter
buttons across the top (crisis early warning, cyber security, defence global,
defence regional, geopolitics, maritime risk, regional news, security,
technology) while "Coverage by Category" below listed only SEVEN. Clicking
"cyber security" or "security" returned nothing, always.

Two independent defects, both proven against production before these tests were
written:

R-F3671 — ``get_stats()`` returns two lists computed over two DIFFERENT
populations. ``categories`` comes from the CONFIGURED ``NEWS_SOURCES`` tuples;
``by_category`` comes from the RETAINED CORPUS. The page built its filter from
the first and its breakdown from the second, so they could disagree — and did.
Same defect class as R-F3517/R-F3518, which fixed the article LIST against the
breakdown but left the FILTER on the old population.

R-F3672 — why those two categories were empty at all. ``poll_feeds`` sliced
``articles[:max_articles_per_feed]`` and only THEN skipped already-seen URLs, so
a feed whose newest N were all ingested yielded zero new articles on every poll
forever, while never-seen items sat below the cut. Measured live: UK NCSC's feed
carried 20 items, newest 10 seen, items 11-20 never ingested; the seen map has no
useful expiry (50,000 entries), so the starvation is permanent.

Both tests drive the real functions, and both were confirmed to FAIL against the
pre-fix code (§3c).
"""

import asyncio

import pytest

from aria_service.intel import news_monitor as nm


# ── R-F3672: the ingest starvation ──────────────────────────────────────────


def _arts(*urls):
    return [{"url": u, "title": "t " + u, "summary": "s"} for u in urls]


def test_rf3672_unseen_items_below_the_cut_are_reachable():
    """The exact live NCSC shape: newest 10 seen, items 11-20 never ingested.

    Pre-fix this returned [] — ``articles[:10]`` was all-seen — which is how
    `cyber_security` held zero articles in BOTH the hot corpus and the durable
    archive while its feeds were returning HTTP 200 with content.
    """
    feed = _arts(*[f"https://ncsc.example/report/{i}" for i in range(20)])
    seen = {nm._article_hash(a["url"]): 1.0 for a in feed[:10]}

    picked = nm._unseen_head(feed, seen, 10)

    assert len(picked) == 10, "the unseen backlog below the slice must be reachable"
    assert [a["url"] for a in picked] == [f"https://ncsc.example/report/{i}" for i in range(10, 20)]


def test_rf3672_slice_before_filter_was_the_bug():
    """Pin the ordering itself, so a future edit cannot silently restore it."""
    feed = _arts(*[f"https://f.example/{i}" for i in range(20)])
    seen = {nm._article_hash(a["url"]): 1.0 for a in feed[:10]}

    # What the old code did, spelled out: bound first, filter second.
    old = [a for a in feed[:10] if nm._article_hash(a["url"]) not in seen]
    assert old == [], "sanity: the old order really did starve this feed"

    assert nm._unseen_head(feed, seen, 10), "the new order must not"


def test_rf3672_high_churn_feed_cost_is_unchanged():
    """A feed whose newest entries are all new still yields exactly the bound."""
    feed = _arts(*[f"https://reuters.example/{i}" for i in range(50)])
    picked = nm._unseen_head(feed, {}, 10)
    assert len(picked) == 10
    assert [a["url"] for a in picked] == [f"https://reuters.example/{i}" for i in range(10)]


def test_rf3672_deduplicates_within_one_batch():
    """The in-loop ``_is_seen`` used to catch a URL repeated inside one feed,
    because the first copy was marked seen before the second was tested.
    Selecting against a single snapshot does not, so the batch de-dupes."""
    dupe = "https://f.example/same"
    feed = _arts(dupe, dupe, "https://f.example/other")
    picked = nm._unseen_head(feed, {}, 10)
    assert [a["url"] for a in picked] == [dupe, "https://f.example/other"]


def test_rf3672_articles_without_a_url_are_skipped():
    feed = [{"title": "no url"}, {"url": "", "title": "empty"}, {"url": "https://ok.example/1"}]
    assert [a["url"] for a in nm._unseen_head(feed, {}, 10)] == ["https://ok.example/1"]


def test_rf3672_unreadable_seen_map_biases_to_reingest(monkeypatch):
    """A broken store must read as "nothing known seen", never "everything seen".

    ``_is_seen`` returns False for a non-dict read, and ``_seen_snapshot`` must
    match it. The archive is idempotent on canonical_url_hash, so a re-ingest
    costs a duplicate write — whereas the opposite bias would stop ingest dead.
    """
    async def _broken(_key):
        return None

    monkeypatch.setattr(nm.rs, "get_json", _broken)
    assert asyncio.get_event_loop_policy() is not None  # keep the loop policy explicit
    snap = asyncio.run(nm._seen_snapshot())
    assert snap == {}
    assert len(nm._unseen_head(_arts("https://a.example/1"), snap, 10)) == 1


# ── R-F3671: the two-population stats surface ───────────────────────────────


@pytest.mark.asyncio
async def test_rf3671_stats_names_both_populations(monkeypatch):
    """The reported symptom, driven through the real ``get_stats()``.

    Configured: 3 categories. Corpus: articles in 2 of them. Pre-fix the caller
    got ``categories`` (3) and ``by_category`` (2) with nothing marking the
    difference, which is precisely what let the page render 9 buttons over 7
    bars.
    """
    monkeypatch.setattr(nm, "NEWS_SOURCES", [
        ("Feed A", "https://a.example/rss", "regional_news", "en", "tier1", []),
        ("Feed B", "https://b.example/rss", "cyber_security", "en", "tier1", []),
        ("Feed C", "https://c.example/rss", "security", "en", "tier1", []),
    ])
    nm._stats_cache, nm._stats_cache_ts = None, 0.0

    async def _articles(_limit=0, category=""):
        return [
            {"category": "regional_news", "source": "Feed A"},
            {"category": "regional_news", "source": "Feed A"},
            {"category": "technology", "source": "Feed Z"},
        ]

    async def _poll_state():
        return {}

    monkeypatch.setattr(nm, "get_recent_articles", _articles)
    monkeypatch.setattr(nm, "_read_poll_state", _poll_state)

    stats = await nm.get_stats()

    # Configured coverage is unchanged for existing readers.
    assert stats["categories"] == ["cyber_security", "regional_news", "security"]

    # NEW: what can actually be filtered. `technology` is retained but not
    # configured — it must still be reachable, never silently unfilterable.
    assert stats["categories_with_articles"] == ["regional_news", "technology"]

    # NEW: coverage that has gone dark, named rather than hidden.
    assert stats["empty_categories"] == ["cyber_security", "security"]

    # The invariant the page depends on: nothing offered as a servable filter
    # may have a zero count in the breakdown beside it.
    for cat in stats["categories_with_articles"]:
        assert stats["by_category"].get(cat), f"{cat} offered but empty"
    for cat in stats["empty_categories"]:
        assert not stats["by_category"].get(cat), f"{cat} marked dark but has articles"


@pytest.mark.asyncio
async def test_rf3671_breakdown_keys_are_all_servable_by_the_filter(monkeypatch):
    """The invariant, on the path that nearly reintroduced the bug.

    The breakdown keyed a category-less article as "unknown"; the filter compared
    against "". So the page would have offered an enabled "unknown" button that
    matched nothing — the same contradiction on a different key. Both sides now
    normalise through ``_norm_category``, so this asserts the real property:
    every key the breakdown reports can be RETRIEVED by the filter.
    """
    stored = [
        {"category": "regional_news", "source": "A", "url": "u1"},
        {"source": "B", "url": "u2"},                    # no category key at all
        {"category": "  Defence_Global ", "source": "C", "url": "u3"},  # cased/padded
    ]

    async def _lrange(_key, _start, _end):
        import json as _json
        return [_json.dumps(a) for a in stored]

    monkeypatch.setattr(nm.rs, "lrange", _lrange)
    monkeypatch.setattr(nm, "NEWS_SOURCES", [
        ("Feed A", "https://a.example/rss", "regional_news", "en", "tier1", []),
    ])
    nm._stats_cache, nm._stats_cache_ts = None, 0.0

    async def _poll_state():
        return {}

    monkeypatch.setattr(nm, "_read_poll_state", _poll_state)

    stats = await nm.get_stats()
    assert stats["by_category"] == {"regional_news": 1, "unknown": 1, "defence_global": 1}

    # Every reported key must actually return articles through the filter.
    for cat, count in stats["by_category"].items():
        got = await nm.get_recent_articles(100, category=cat)
        assert len(got) == count, f"breakdown claims {count} for {cat!r} but filter returned {len(got)}"


@pytest.mark.asyncio
async def test_rf3671_empty_categories_are_not_dropped(monkeypatch):
    """Closing the mismatch by DELETING the empty categories would hide a dead
    feed. The configured list must still name every category ARIA watches."""
    monkeypatch.setattr(nm, "NEWS_SOURCES", [
        ("Feed A", "https://a.example/rss", "regional_news", "en", "tier1", []),
        ("Europol", "https://europol.example/rss", "security", "en", "tier1", []),
    ])
    nm._stats_cache, nm._stats_cache_ts = None, 0.0

    async def _articles(_limit=0, category=""):
        return [{"category": "regional_news", "source": "Feed A"}]

    async def _poll_state():
        return {}

    monkeypatch.setattr(nm, "get_recent_articles", _articles)
    monkeypatch.setattr(nm, "_read_poll_state", _poll_state)

    stats = await nm.get_stats()
    assert "security" in stats["categories"], "a dark category must stay visible"
    assert "security" in stats["empty_categories"]
    assert "security" not in stats["categories_with_articles"]

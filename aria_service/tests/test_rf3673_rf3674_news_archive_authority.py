"""R-F3673 / R-F3674 — deep review of the news section, second pass.

Both defects are the SAME SHAPE as R-F3671/R-F3672 and were found by measuring
production rather than reading code:

R-F3673 — the poll asked the SEEN MAP whether an article was already ingested,
but the durable record is the ARCHIVE. ``_mark_seen`` stores ``hash ->
timestamp`` only, so a URL marked seen by a path that never archived it (anything
ingested before the archive existed; any pre-R-F3486 mark-seen-first write) was
skipped forever, and the URL could not be recovered from the seen map either.
Measured live 2026-08-04: 5,777 seen URLs vs 1,623 archived rows, with 383 of the
difference still sitting in the live feeds — including 9 of Europol's 10 items,
which is why the `security` category read empty at all.

R-F3674 — ``total_sources`` counts CONFIGURED feeds and the page rendered it as
live coverage ("Live intelligence from 45 curated sources", KPI "45"). Five of
those 45 delivered nothing: Naval News (empty fetch), ReliefWeb (quarantined
after 8 failures), and Hurriyet / O Globo / UK Defence Journal Tech (parsed
clean, zero items). The page named none of them.

Every test here was confirmed to fail against the pre-fix code (§3c).
"""

import pytest

from aria_service.intel import news_monitor as nm


def _arts(*urls):
    return [{"url": u, "title": "t", "summary": "s"} for u in urls]


# ── R-F3673: the archive is the authority ───────────────────────────────────


@pytest.mark.asyncio
async def test_rf3673_seen_but_never_archived_is_recovered(monkeypatch):
    """THE DEFECT: marked seen, never stored, therefore skipped forever.

    This is the live Europol shape — every item in the seen map, one row in the
    archive — which is exactly why `security` had no articles to show.
    """
    feed = _arts(*[f"https://europol.example/{i}" for i in range(10)])
    seen = {nm._article_hash(a["url"]): 1.0 for a in feed}      # ALL seen

    from aria_service.intel import news_archive as na
    archived = {na.url_hash(feed[0]["url"])}                     # only ONE stored

    async def _subset(hashes):
        return {h for h in hashes if h in archived}

    monkeypatch.setattr(na, "archived_subset", _subset)

    got = await nm._ingestable_head(feed, seen, 10)

    assert len(got) == 9, "the 9 seen-but-unarchived articles must be recoverable"
    assert feed[0] not in got, "the one genuinely archived article must stay skipped"


@pytest.mark.asyncio
async def test_rf3673_archived_articles_are_never_reingested(monkeypatch):
    """The other direction: a stored article must not be picked up again."""
    feed = _arts(*[f"https://a.example/{i}" for i in range(6)])
    from aria_service.intel import news_archive as na

    async def _subset(hashes):
        return set(hashes)                                        # everything stored

    monkeypatch.setattr(na, "archived_subset", _subset)
    assert await nm._ingestable_head(feed, {}, 10) == []


@pytest.mark.asyncio
async def test_rf3673_unreadable_archive_falls_back_to_the_seen_map(monkeypatch):
    """An archive that cannot be READ must not read as "nothing is stored".

    That would re-ingest every article in every feed on every poll. The honest
    degradation is the R-F3672 behaviour — trust the seen map for this pass.
    """
    feed = _arts(*[f"https://a.example/{i}" for i in range(20)])
    seen = {nm._article_hash(a["url"]): 1.0 for a in feed[:10]}

    from aria_service.intel import news_archive as na

    async def _boom(hashes):
        raise RuntimeError("archive locked")

    monkeypatch.setattr(na, "archived_subset", _boom)

    got = await nm._ingestable_head(feed, seen, 10)
    assert [a["url"] for a in got] == [f"https://a.example/{i}" for i in range(10, 20)], (
        "fallback must behave exactly like the seen-map path, not re-ingest everything"
    )


@pytest.mark.asyncio
async def test_rf3673_still_bounded_by_the_per_feed_limit(monkeypatch):
    """Recovering a backlog must not blow the per-poll budget open."""
    feed = _arts(*[f"https://a.example/{i}" for i in range(200)])
    seen = {nm._article_hash(a["url"]): 1.0 for a in feed}

    from aria_service.intel import news_archive as na

    async def _subset(hashes):
        return set()                                              # nothing archived

    monkeypatch.setattr(na, "archived_subset", _subset)
    assert len(await nm._ingestable_head(feed, seen, 10)) == 10


@pytest.mark.asyncio
async def test_rf3673_archived_subset_is_one_query_not_a_full_table_load():
    """The lookup must stay bounded as the archive grows without limit (§7)."""
    from aria_service.intel import news_archive as na

    na._reset_for_tests()
    await na.archive_article({"url": "https://kept.example/1", "title": "kept",
                              "summary": "s", "category": "technology"})

    kept = na.url_hash("https://kept.example/1")
    missing = na.url_hash("https://never.example/9")

    found = await na.archived_subset([kept, missing])
    assert found == {kept}, "must report exactly the archived subset"
    assert await na.archived_subset([]) == set(), "empty input must not query at all"


@pytest.mark.asyncio
async def test_rf3673_chunking_survives_a_feed_larger_than_the_sqlite_var_limit():
    """A single IN (...) of >999 params raises OperationalError in SQLite."""
    from aria_service.intel import news_archive as na

    na._reset_for_tests()
    await na.archive_article({"url": "https://kept.example/1", "title": "k",
                              "summary": "s", "category": "technology"})
    kept = na.url_hash("https://kept.example/1")

    hashes = [na.url_hash(f"https://bulk.example/{i}") for i in range(1500)] + [kept]
    assert await na.archived_subset(hashes) == {kept}


# ── R-F3674: honest source health ───────────────────────────────────────────


def test_rf3674_dark_sources_are_counted_and_named():
    """The live 2026-08-04 shape: 45 configured, 5 delivering nothing."""
    poll_state = {"results": [
        {"name": "Al Jazeera", "status": "ok", "articles": 25},
        {"name": "Defense News", "status": "ok", "articles": 10},
        {"name": "Naval News", "status": "failed", "articles": 0},
        {"name": "ReliefWeb (UN OCHA)", "status": "quarantined", "articles": 0},
        {"name": "Hurriyet Daily News", "status": "ok", "articles": 0},
        {"name": "O Globo Brazil", "status": "ok", "articles": 0},
        {"name": "UK Defence Journal Tech", "status": "ok", "articles": 0},
    ]}

    h = nm._source_health(poll_state, configured=45)

    assert h["measured"] is True
    assert h["configured"] == 45
    assert h["delivering"] == 2
    assert h["dark"] == 5
    # Three different problems, kept apart because they need different responses.
    assert h["failing"] == ["Naval News"]
    assert h["quarantined"] == ["ReliefWeb (UN OCHA)"]
    assert h["silent"] == ["Hurriyet Daily News", "O Globo Brazil", "UK Defence Journal Tech"]


def test_rf3674_no_poll_results_is_unknown_never_healthy():
    """Certifying source health by an ABSENCE of evidence is the recurring bug."""
    for state in ({}, None, {"results": []}):
        h = nm._source_health(state, configured=45)
        assert h["measured"] is False, f"{state!r} must not read as measured"
        assert "delivering" not in h, "an unmeasured state must not report a clean count"
        assert h["reason"] == "no_poll_results_recorded"


def test_rf3674_reports_on_the_polls_own_denominator():
    """A time-boxed poll (R-F2630) does not attempt every feed; calling the
    unattempted ones dark would be a false alarm."""
    poll_state = {"results": [{"name": "A", "status": "ok", "articles": 3}]}
    h = nm._source_health(poll_state, configured=45)
    assert h["reported_on"] == 1 and h["configured"] == 45
    assert h["dark"] == 0


@pytest.mark.asyncio
async def test_rf3674_stats_exposes_source_health(monkeypatch):
    """Driven through the real get_stats(), which is what the page reads."""
    monkeypatch.setattr(nm, "NEWS_SOURCES", [
        ("Feed A", "https://a.example/rss", "regional_news", "en", "tier1", []),
        ("Feed B", "https://b.example/rss", "technology", "en", "tier1", []),
    ])
    nm._stats_cache, nm._stats_cache_ts = None, 0.0

    async def _articles(_limit=0, category=""):
        return [{"category": "regional_news", "source": "Feed A"}]

    async def _poll_state():
        return {"results": [
            {"name": "Feed A", "status": "ok", "articles": 5},
            {"name": "Feed B", "status": "failed", "articles": 0},
        ]}

    monkeypatch.setattr(nm, "get_recent_articles", _articles)
    monkeypatch.setattr(nm, "_read_poll_state", _poll_state)

    stats = await nm.get_stats()
    sh = stats["source_health"]
    assert sh["delivering"] == 1 and sh["dark"] == 1 and sh["failing"] == ["Feed B"]
    # The old number stays available and still means what it always meant.
    assert stats["total_sources"] == 2

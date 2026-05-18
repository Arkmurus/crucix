"""R-F687 (2026-05-18) — crawler skips auto-registered hallucination
garbage domains at sweep time.

Live fly logs 2026-05-18 10:23-10:37 showed the daily crawler polling
hundreds of garbage domains in alphabetical order:
  - application.{co,com,net} / approval.{co,net,org} / arabia.{co,...}
  - billion.{com,net,org,pt} / cisco.{co,com,com.br,net,org,pt}
  - defence.{co,com,com.br,net,org} / drone.{co,com,net}
  - email.{co,com,com.br,net,org,pt} (also a 30-call redirect-loop on
    email.net→/mailfence.com/)
  - and many more

These were auto-registered (tier=4, sector="discovered") by the
pre-R-F676 head-only / second-only URL generator processing sentence
queries. R-F676 closed the generator; R-F687 stops the existing rows
from being re-crawled (and re-absorbed via web_atlas, tripping the
brain_hook breaker — 3 trips in ~5 minutes observed).

Conservative filter: must match ALL THREE:
  1. tier == 4
  2. sector == "discovered"
  3. leftmost label in _GARBAGE_COMMON_NOUN_LABELS

Operator-curated rows (any tier ≤ 3) are NEVER filtered.
"""
from __future__ import annotations


def test_rf687_is_garbage_filter_matches_live_evidence():
    """The helper must flag the exact live-evidence rows."""
    from aria_service.crawler import on_demand
    for label in (
        "application", "approval", "arabia", "billion", "boycott",
        "britain", "capital", "chapter", "cia", "cisco", "com",
        "compliance", "contain", "contract", "cve", "defence",
        "defense", "drone", "email", "embedded", "east",
        "directors", "dispute", "dynamics", "dedicated", "deeply",
    ):
        row = {"domain": f"{label}.com", "tier": 4, "sector": "discovered"}
        assert on_demand.is_auto_registered_garbage(row), (
            f"R-F687: live-evidence label `{label}` not detected"
        )


def test_rf687_filter_respects_tier():
    """Tier ≤ 3 means operator-curated → never filtered, even if the
    label looks generic."""
    from aria_service.crawler import on_demand
    for tier in (1, 2, 3):
        row = {"domain": "cisco.com", "tier": tier, "sector": "discovered"}
        assert not on_demand.is_auto_registered_garbage(row), (
            f"R-F687: tier-{tier} row wrongly filtered"
        )


def test_rf687_filter_respects_sector():
    """sector != 'discovered' means the row didn't come from
    auto-register → never filtered."""
    from aria_service.crawler import on_demand
    row = {"domain": "cisco.com", "tier": 4, "sector": "tech_industry"}
    assert not on_demand.is_auto_registered_garbage(row)


def test_rf687_legitimate_defence_domains_pass(monkeypatch):
    """Real defence-industry compound labels must NOT be filtered even
    at tier 4 / discovered."""
    from aria_service.crawler import on_demand
    for legit in (
        "baykar.com",       # Turkish drone OEM
        "embraer.com",      # Brazilian aerospace
        "modirumgespi.com", # operator example
        "lockheedmartin.com",
        "defensenews.com",  # publication
        "defenceweb.co.za", # publication
        "thedefensepost.com",
        "armyrecognition.com",
    ):
        row = {"domain": legit, "tier": 4, "sector": "discovered"}
        assert not on_demand.is_auto_registered_garbage(row), (
            f"R-F687: legitimate compound `{legit}` wrongly filtered"
        )


def test_rf687_empty_or_malformed_rows_safe():
    from aria_service.crawler import on_demand
    assert on_demand.is_auto_registered_garbage({}) is False
    assert on_demand.is_auto_registered_garbage(None) is False  # type: ignore[arg-type]
    assert on_demand.is_auto_registered_garbage(
        {"domain": "", "tier": 4, "sector": "discovered"}
    ) is False
    assert on_demand.is_auto_registered_garbage(
        {"domain": "no-dot", "tier": 4, "sector": "discovered"}
    ) is False


def test_rf687_crawler_runner_filters_garbage():
    """End-to-end: crawl_seed_homepages drops garbage rows from the
    sweep. Uses a stub list_domains + a counter-only fetch_for_crawl so
    we can assert which domains the loop actually visits."""
    import asyncio
    from aria_service.crawler import runner as crawler_runner

    visited: list[str] = []

    async def fake_list_domains(*a, **kw):
        return [
            # Garbage — must be filtered
            {"domain": "billion.com", "tier": 4, "sector": "discovered"},
            {"domain": "cisco.net",   "tier": 4, "sector": "discovered"},
            {"domain": "defence.org", "tier": 4, "sector": "discovered"},
            # Legitimate auto-registered compound — must pass
            {"domain": "baykar.com",  "tier": 4, "sector": "discovered"},
            # Operator-curated — must pass
            {"domain": "defensenews.com", "tier": 1, "sector": "publication"},
        ]

    async def fake_fetch_for_crawl(url, *a, **kw):
        visited.append(url)
        # Return a thin result so the loop skips indexing branches but
        # still increments by_status — we only care about visit counts.
        return None

    async def go():
        from aria_service.search_index import db
        # Monkey-patch list_domains on the db module the runner imports.
        import unittest.mock as _mock
        with _mock.patch.object(db, "list_domains", new=fake_list_domains), \
             _mock.patch.object(
                 crawler_runner.fetcher, "fetch_for_crawl",
                 new=fake_fetch_for_crawl,
             ):
            result = await crawler_runner.crawl_seed_homepages()
        return result

    result = asyncio.run(go())

    # Garbage rows must not be visited.
    assert "https://billion.com/" not in visited
    assert "https://cisco.net/" not in visited
    assert "https://defence.org/" not in visited
    # Legitimate rows must be visited.
    assert "https://baykar.com/" in visited
    assert "https://defensenews.com/" in visited
    # domains_total reports the post-filter count (2), not the input 5.
    assert result["domains_total"] == 2, result

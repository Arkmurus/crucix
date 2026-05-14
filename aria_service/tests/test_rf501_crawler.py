"""R-F501 — crawler unit tests.

Covers:
  - seed_list.seed_all() registers every entry into search_index.db
  - seed_list.SEEDS has the expected category coverage
  - politeness.domain_of normalises common URL shapes
  - politeness.is_allowed defaults open when robots.txt is missing
  - politeness token bucket throttles to configured rate
  - fetcher honours: domain-not-registered → None; disabled → None;
    robots-denied → None
  - fetcher._test_synthesize_fetch_result enriches with tier + language
  - runner.crawl_seed_homepages returns a structured summary

We do NOT hit the network in these tests. The fetcher delegates to
extract_url_text which IS network — covered by the synthesise helper
that exercises the politeness/registration gates while skipping the
real HTTP.
"""
from __future__ import annotations

import asyncio
import pytest

from aria_service.search_index import db
from aria_service.crawler import seed_list, politeness, fetcher, runner


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture
def db_ready():
    _run(db._reset())
    _run(politeness.reset_bucket())
    ok = _run(db.connect(":memory:"))
    assert ok
    yield db
    _run(db._reset())
    _run(politeness.reset_bucket())


# ─────────────────────────────────────────────────────────────────
# Seed list
# ─────────────────────────────────────────────────────────────────

def test_seed_all_registers_every_entry(db_ready):
    async def go():
        n = await seed_list.seed_all()
        assert n == len(seed_list.SEEDS)
        # Spot-check a few.
        d = await db.get_domain("ofac.treasury.gov")
        assert d is not None and d["tier"] == 1 and d["sector"] == "sanctions"
        d = await db.get_domain("jornaldeangola.ao")
        assert d is not None and d["language"] == "pt"
        d = await db.get_domain("defenceweb.co.za")
        assert d is not None and d["sector"] == "defence"
    _run(go())


def test_seeds_cover_expected_sectors():
    """The seed list must include at least one entry in each headline
    sector — this is a regression guard against accidental deletes."""
    sectors = {e.get("sector") for e in seed_list.SEEDS}
    for required in ("defence", "sanctions", "tenders", "corporate",
                     "multilateral", "lusophone", "africa", "news"):
        assert required in sectors, f"seed list missing sector: {required}"


def test_seeds_cover_pt_and_fr_and_ar():
    """We promised polyglot — check at least the top languages are present."""
    langs = {e.get("language") for e in seed_list.SEEDS}
    assert "en" in langs and "pt" in langs and "fr" in langs and "ar" in langs


def test_seed_by_sector_filters(db_ready):
    async def go():
        n = await seed_list.seed_by_sector("lusophone")
        # Only Lusophone domains were registered.
        lus = await db.list_domains(sector="lusophone")
        assert n == len(lus)
        assert all(d["sector"] == "lusophone" for d in lus)
    _run(go())


# ─────────────────────────────────────────────────────────────────
# Politeness
# ─────────────────────────────────────────────────────────────────

def test_domain_of_normalises_common_shapes():
    assert politeness.domain_of("https://Example.COM/path") == "example.com"
    assert politeness.domain_of("https://example.com:8443/x") == "example.com"
    assert politeness.domain_of("https://.example.com/") == "example.com"
    assert politeness.domain_of("") == ""
    assert politeness.domain_of("not-a-url") == ""


def test_robots_defaults_open_when_missing(db_ready, monkeypatch):
    """Empty robots.txt should permit any path."""
    async def fake_fetch(domain, timeout=10.0):
        return ""
    monkeypatch.setattr(politeness, "_fetch_robots_txt", fake_fetch)

    async def go():
        await db.register_domain("openrobots.example", tier=3,
                                 sector="news", language="en")
        ok = await politeness.is_allowed("https://openrobots.example/x")
        assert ok is True
    _run(go())


def test_robots_blocks_disallowed_path(db_ready, monkeypatch):
    """A robots.txt with Disallow: /private/ should block /private/x."""
    async def fake_fetch(domain, timeout=10.0):
        return "User-agent: *\nDisallow: /private/\n"
    monkeypatch.setattr(politeness, "_fetch_robots_txt", fake_fetch)

    async def go():
        await db.register_domain("strict.example", tier=3,
                                 sector="news", language="en")
        assert await politeness.is_allowed("https://strict.example/private/x") is False
        assert await politeness.is_allowed("https://strict.example/public/x") is True
    _run(go())


def test_token_bucket_throttles_to_configured_rate(db_ready):
    """Two acquires on a 5 r/s domain should take ≥ 0.18s combined when
    starting from cold (first burst is free; second waits ~1/5s)."""
    import time as _time
    async def go():
        await db.register_domain("rate.example", tier=3, sector="news",
                                 language="en", rate_limit_per_sec=5.0)
        await politeness.reset_bucket("rate.example")
        # Burn the initial burst.
        await politeness.acquire("rate.example")
        t0 = _time.time()
        await politeness.acquire("rate.example")
        elapsed = _time.time() - t0
        # 5 r/s → next token after ~0.2s. Allow a generous floor of 0.15s.
        assert elapsed >= 0.15, f"throttle too lax: {elapsed:.3f}s"
    _run(go())


# ─────────────────────────────────────────────────────────────────
# Fetcher
# ─────────────────────────────────────────────────────────────────

def test_fetcher_skips_unregistered_domain(db_ready, monkeypatch):
    # Even if robots would allow, we only crawl curated seeds.
    async def fake_robots(domain, timeout=10.0):
        return ""
    monkeypatch.setattr(politeness, "_fetch_robots_txt", fake_robots)
    async def go():
        result = await fetcher.fetch_for_index("https://random.example/x")
        assert result is None
    _run(go())


def test_fetcher_skips_disabled_domain(db_ready, monkeypatch):
    async def fake_robots(domain, timeout=10.0):
        return ""
    monkeypatch.setattr(politeness, "_fetch_robots_txt", fake_robots)
    async def go():
        await db.register_domain("disabled.example", tier=3, sector="news",
                                 language="en", enabled=False)
        result = await fetcher._test_synthesize_fetch_result(
            "https://disabled.example/x", title="t", headings="", body="b",
        )
        assert result is None
    _run(go())


def test_fetcher_skips_robots_denied(db_ready, monkeypatch):
    async def fake_robots(domain, timeout=10.0):
        return "User-agent: *\nDisallow: /\n"
    monkeypatch.setattr(politeness, "_fetch_robots_txt", fake_robots)
    async def go():
        await db.register_domain("blocked.example", tier=3, sector="news",
                                 language="en")
        result = await fetcher._test_synthesize_fetch_result(
            "https://blocked.example/x", title="t", headings="", body="b",
        )
        assert result is None
    _run(go())


def test_fetcher_returns_enriched_result(db_ready, monkeypatch):
    async def fake_robots(domain, timeout=10.0):
        return ""
    monkeypatch.setattr(politeness, "_fetch_robots_txt", fake_robots)
    async def go():
        await db.register_domain("good.example", tier=2, sector="defence",
                                 language="en")
        result = await fetcher._test_synthesize_fetch_result(
            "https://good.example/news/1",
            title="Modirum Gespi contract", headings="award",
            body="Modirum Gespi has been awarded a defence contract.",
        )
        assert result is not None
        assert result["source_tier"] == 2
        assert result["language"] == "en"
        assert result["domain"] == "good.example"
        assert result["canonical_url"].startswith("https://good.example")
        assert "Modirum" in result["body"]

        # mark_domain_crawled fired — domain row has last_crawled_at.
        d = await db.get_domain("good.example")
        assert d["last_crawled_at"] is not None
    _run(go())


# ─────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────

def test_runner_crawl_seed_homepages_summary_shape(db_ready, monkeypatch):
    """We don't hit the network here. Stub fetch_for_index to count calls
    and confirm the summary aggregates correctly."""
    calls: list[str] = []

    async def fake_fetch(url, timeout=20.0):
        calls.append(url)
        # Half succeed, half fail.
        if "ok" in url:
            return {
                "url": url, "canonical_url": url, "domain": "ok.example",
                "title": "t", "headings": "", "body": "b",
                "language": "en", "source_tier": 2, "http_status": 200,
                "fetched_at": 0.0, "extraction_ok": True, "duration_ms": 0,
            }
        return None

    monkeypatch.setattr(fetcher, "fetch_for_index", fake_fetch)

    async def go():
        await db.register_domain("ok.example", tier=2, sector="news",
                                 language="en")
        await db.register_domain("bad.example", tier=2, sector="news",
                                 language="en")
        summary = await runner.crawl_seed_homepages()
        assert summary["domains_total"] == 2
        assert summary["fetched"] + summary["skipped"] == 2
        assert "duration_sec" in summary
    _run(go())

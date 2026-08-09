"""R-F507 — active engine: on-demand fill + auto-register + background fire.

The single user-visible promise of R-F507 is the cold-start capability
test at the bottom: an empty index becomes a useful index for the
"Modirum Gespi" query after exactly one call to `ensure_indexed()`,
without any third-party API.

Coverage:
  - guess_entity_urls produces plausible candidate URLs
  - _safe_domain_for_register rejects garbage
  - auto_register_domain is idempotent + tier-4 by default
  - ensure_indexed time-budget cap honoured
  - ensure_indexed max_pages cap honoured
  - background_ensure lockout prevents fanning out duplicates
  - looks_like_entity_query distinguishes entity from generic
  - Capability: cold → indexed in one call

  Plus a lifespan smoke that asserts the new module imports cleanly.
"""
from __future__ import annotations

import asyncio
import time
import pytest

from aria_service.search_index import db
from aria_service.crawler import on_demand


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture
def db_ready():
    _run(db._reset())
    ok = _run(db.connect(":memory:"))
    assert ok
    yield db
    _run(db._reset())


# ─────────────────────────────────────────────────────────────────
# Candidate generation
# ─────────────────────────────────────────────────────────────────

def test_guess_entity_urls_modirum_gespi():
    urls = on_demand.guess_entity_urls("Modirum Gespi")
    # The two main shapes ARIA needs to find for this entity.
    assert "https://modirumgespi.com/" in urls
    assert any(u.startswith("https://modirumgespi.") for u in urls)
    assert any("modirum-gespi" in u for u in urls)
    # And subsidiary-as-brand pattern (gespi.ao captures Angolan op).
    assert any("gespi" in u and ".ao" in u for u in urls)
    # Plus parent-only (modirum.com as parent).
    assert any(u.startswith("https://modirum.com/")
               or u.startswith("https://modirum.pt/") for u in urls)


def test_guess_entity_urls_empty_query():
    assert on_demand.guess_entity_urls("") == []
    assert on_demand.guess_entity_urls("   ") == []


def test_guess_entity_urls_caps_at_limit():
    urls = on_demand.guess_entity_urls("Foo Bar", limit=5)
    assert len(urls) <= 5


# ─────────────────────────────────────────────────────────────────
# Safe-domain gate
# ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("good", [
    # R-F654 (2026-05-17): replaced "example.com" — its label is the
    # RFC 2606 reserved placeholder and is now correctly rejected by
    # _safe_domain_for_register. The other entries use "example" only
    # as a sub-label, so their leading label ("press", "sub") still
    # passes the new placeholder check.
    "defensenews.com", "modirumgespi.com", "press.example.co.uk",
    "sub.example.org",
])
def test_safe_domain_accepts_normal(good):
    assert on_demand._safe_domain_for_register(good) is True


@pytest.mark.parametrize("bad", [
    "", "x", "localhost", "127.0.0.1", "10.0.0.1",
    "no-dot", ".starts-with-dot", "a.b.c.d.e.f.g.h.i.j.k" * 30,
])
def test_safe_domain_rejects_garbage(bad):
    assert on_demand._safe_domain_for_register(bad) is False


# ─────────────────────────────────────────────────────────────────
# auto_register_domain
# ─────────────────────────────────────────────────────────────────

def test_auto_register_creates_tier4(db_ready):
    """R-F3820 — registration now requires a JUSTIFICATION, so this test supplies one.

    It previously called `auto_register_domain("newdomain.example")` bare, which is
    the contract that let 163 adult and 41 gambling domains into a registry §7 forbids
    evicting from. The row-shape assertions below are unchanged and still the point;
    only the admission argument is new.
    """
    async def go():
        created = await on_demand.auto_register_domain(
            "newdomain.example",
            evidence="EU sanctions three entities over Russian arms procurement")
        assert created is True
        d = await db.get_domain("newdomain.example")
        assert d is not None
        assert d["tier"] == 4
        assert d["sector"] == "discovered"
        assert d["enabled"] is True
        assert "auto-registered" in (d.get("notes") or "")
    _run(go())


def test_auto_register_idempotent(db_ready):
    """Idempotency is unchanged by R-F3820: the second call still sees the existing
    row. Uses the entity-request justification, the path DD relies on."""
    async def go():
        c1 = await on_demand.auto_register_domain(
            "idem.example", requested_entity="Modirum Gespi")
        c2 = await on_demand.auto_register_domain(
            "idem.example", requested_entity="Modirum Gespi")
        assert c1 is True
        assert c2 is False  # second call sees existing row
    _run(go())


def test_auto_register_rejects_unsafe(db_ready):
    async def go():
        created = await on_demand.auto_register_domain("localhost")
        assert created is False
        assert (await db.get_domain("localhost")) is None
    _run(go())


# ─────────────────────────────────────────────────────────────────
# ensure_indexed bounds
# ─────────────────────────────────────────────────────────────────

def test_ensure_indexed_honours_max_pages(db_ready):
    """Even if every candidate fetch succeeds, max_pages caps the work."""
    async def fake_fetch(url, timeout=8.0):
        return {
            "url": url,
            "canonical_url": url,
            "domain": (url.split("/")[2] if "//" in url else "x"),
            "title": "Modirum Gespi awarded contract",
            "headings": "",
            "body": ("Modirum Gespi has been awarded a defence systems "
                     "integration contract for the Angolan Ministry of "
                     "Defence. The contract runs for three years and "
                     "covers tactical communications systems."),
            "language": "en", "source_tier": 4,
            "http_status": 200, "fetched_at": time.time(),
        }
    async def go():
        summary = await on_demand.ensure_indexed(
            "Modirum Gespi", max_pages=3,
            time_budget_s=30.0, fetch_fn=fake_fetch,
        )
        assert summary["indexed"] == 3
        assert summary["candidates_tried"] >= 3
    _run(go())


def test_ensure_indexed_honours_time_budget(db_ready):
    """A slow fetch must not let ensure_indexed overrun its wall clock."""
    async def slow_fetch(url, timeout=8.0):
        await asyncio.sleep(0.2)
        return None  # forces "skipped" — keeps the loop going so we test the budget
    async def go():
        summary = await on_demand.ensure_indexed(
            "Some Entity Name", max_pages=100,
            time_budget_s=0.5, fetch_fn=slow_fetch,
        )
        # Must have exited because of the budget, not max_pages.
        assert summary["budget_exhausted"] is True
        assert summary["indexed"] == 0
        assert summary["duration_sec"] >= 0.4  # at least the budget
        # And we tried something but not all candidates.
        assert summary["candidates_tried"] >= 1
    _run(go())


def test_ensure_indexed_handles_fetch_exception(db_ready):
    """A raising fetch must increment errors and NOT crash the loop."""
    async def boom(url, timeout=8.0):
        raise RuntimeError("network down")
    async def go():
        summary = await on_demand.ensure_indexed(
            "Some Entity Name", max_pages=5,
            time_budget_s=5.0, fetch_fn=boom,
        )
        assert summary["errors"] >= 1
        assert summary["indexed"] == 0
        assert "duration_sec" in summary
    _run(go())


# ─────────────────────────────────────────────────────────────────
# Background lockout
# ─────────────────────────────────────────────────────────────────

def test_background_lock_check_blocks_repeats():
    on_demand._recent_bg_queries.clear()
    assert on_demand._background_lock_check("Modirum Gespi") is True
    # Immediate repeat is blocked.
    assert on_demand._background_lock_check("Modirum Gespi") is False
    # Case + whitespace normalised.
    assert on_demand._background_lock_check("  modirum gespi  ") is False
    # Different query goes through.
    assert on_demand._background_lock_check("Other Entity") is True


def test_looks_like_entity_query():
    assert on_demand.looks_like_entity_query("Modirum Gespi") is True
    assert on_demand.looks_like_entity_query("BAE Systems") is True
    assert on_demand.looks_like_entity_query("Leonardo Helicopters") is True
    # Generic question — not an entity.
    assert on_demand.looks_like_entity_query("how do I do x") is False
    assert on_demand.looks_like_entity_query("") is False
    assert on_demand.looks_like_entity_query("contract") is False  # single token


# ─────────────────────────────────────────────────────────────────
# R-F676 (2026-05-18): block sentence queries that triggered the
# URL hallucination → parking-domain ingest pipeline. The cases
# below are LIVE-EVIDENCE queries captured from fly logs on
# 2026-05-18 07:14-07:19 that produced indexed garbage like
# philippines.com / dsca.com / news.co / article.org / koreas.com.
# ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("sentence_query", [
    "Indonesia Philippines defence modernisation 2026",
    "Poland NATO defence spending 2026",
    "India defence acquisition tender 2026",
    "DSCA FMS notification major arms sale 2026",
    "UK ECJU export licence defence 2026",
    "South Koreas coordinated package export strategy 2026",
    "Quakers Britain used open source intelligence public records 2026",
    "article news summary Google News aggregating South China 2026",
    "Ingalls award Navy next gen frigate lead contract 2026",
    "HAVELSANs establishment regional hub Malaysia signal strategic shift",
    "Arkmurus defence security consultancy given recipients role Partner",
])
def test_rf676_sentence_queries_rejected_as_non_entity(sentence_query):
    """Every one of these queries appeared in live fly logs on
    2026-05-18 and produced indexed garbage. They must all now be
    rejected by looks_like_entity_query."""
    assert on_demand.looks_like_entity_query(sentence_query) is False, (
        f"sentence query slipped through: {sentence_query!r}"
    )


def test_rf676_short_entity_queries_still_pass():
    """The 2-token entity case (the original R-F507 design) must
    still pass after the heuristic tightening."""
    assert on_demand.looks_like_entity_query("Modirum Gespi") is True
    assert on_demand.looks_like_entity_query("Apple Inc") is True
    # 3-token capitalised entity name also fine.
    assert on_demand.looks_like_entity_query("Lockheed Martin Corporation") is True


def test_rf676_guess_urls_no_single_token_shapes_for_3plus_tokens():
    """With 3+ tokens the head-only / second-only shapes must NOT
    appear — those are what produced philippines.com, dsca.com, etc.
    from sentence queries that slipped past the gate."""
    urls = on_demand.guess_entity_urls("indonesia philippines defence modernisation")
    # No host that is just a single token (any single token from the query).
    for u in urls:
        host = u.replace("https://", "").rstrip("/")
        # The host's first label is what we care about.
        label = host.split(".", 1)[0]
        # Combined / hyphenated shapes contain multiple source tokens,
        # so the label is at least two of our tokens. Single-token
        # shape would be exactly one source token.
        assert label not in {"indonesia", "philippines", "defence", "modernisation"}, (
            f"single-token shape leaked through for 3+ token query: {u}"
        )


def test_rf676_guess_urls_keeps_single_token_shapes_for_2_tokens():
    """The original Modirum-Gespi design case: 2-token queries MUST
    still get the head-only + second-only shapes (modirum.com,
    gespi.ao). This is the regression guard for the parent-org /
    subsidiary-as-brand patterns."""
    urls = on_demand.guess_entity_urls("Modirum Gespi")
    assert any(u.startswith("https://modirum.com/")
               or u.startswith("https://modirum.pt/") for u in urls)
    assert any("gespi" in u and ".ao" in u for u in urls)


def test_rf676_background_ensure_defensive_gate():
    """background_ensure must refuse to run a sentence query even if
    a future caller forgets to gate via looks_like_entity_query."""
    async def go():
        # Use the same lockout-clearing trick as the other tests —
        # we just need to confirm no fetch happens.
        on_demand._recent_bg_queries.clear()
        fetch_calls: list[str] = []

        async def spy_fetch(url, timeout=8.0):
            fetch_calls.append(url)
            return None

        # Sentence query — should be rejected BEFORE any fetch fires.
        await on_demand.background_ensure(
            "Indonesia Philippines defence modernisation 2026",
            time_budget_s=2.0, max_pages=2,
        )
        # background_ensure delegates to ensure_indexed which uses the
        # real fetcher; we can't easily spy on the real fetcher here,
        # but the lockout cache must NOT have an entry — that proves
        # the early-return path fired before _background_lock_check
        # would have stamped the query.
        assert "indonesia philippines defence modernisation 2026" not in \
               on_demand._recent_bg_queries, (
            "lockout cache stamped despite sentence-query rejection"
        )

    _run(go())


# ─────────────────────────────────────────────────────────────────
# Capability — cold to indexed in one call
# ─────────────────────────────────────────────────────────────────

def test_rf507_capability_cold_to_indexed(db_ready):
    """The watershed test: with an empty index, one call to
    ensure_indexed("Modirum Gespi") leaves the index in a state where
    internal_search.search("Modirum Gespi") returns the indexed page.

    Uses a fake fetcher that returns a single substantive page for
    one of the guessed URLs and None for the rest — simulating the
    real-world case where most domain guesses 404 and one resolves."""
    fetch_calls: list[str] = []

    async def selective_fetch(url, timeout=8.0):
        fetch_calls.append(url)
        # Only the .com guess resolves; everything else is dead.
        if url == "https://modirumgespi.com/":
            return {
                "url": url,
                "canonical_url": url,
                "domain": "modirumgespi.com",
                "title": "Modirum Gespi — Defence systems integration",
                "headings": "About Modirum Gespi · Capabilities · Press",
                "body": ("Modirum Gespi is a defence systems integration "
                         "company headquartered in Luanda, Angola, with a "
                         "Finnish parent (Modirum). The company delivers "
                         "tactical communications, battlefield management "
                         "systems, and electronic-warfare suites to the "
                         "Angolan Ministry of Defence and other CPLP "
                         "armed forces. Recent contract awards include a "
                         "three-year integration programme valued at an "
                         "undisclosed sum."),
                "language": "en", "source_tier": 4,
                "http_status": 200, "fetched_at": time.time(),
            }
        return None

    async def go():
        # Index is empty.
        from aria_service.search_engine import internal_search
        pre = await internal_search.search("Modirum Gespi")
        assert pre == [], "precondition: index must start empty"

        # Cold call — should fill from the .com guess.
        summary = await on_demand.ensure_indexed(
            "Modirum Gespi", max_pages=5,
            time_budget_s=10.0,
            fetch_fn=selective_fetch,
        )
        assert summary["indexed"] >= 1, summary
        assert summary["new_domains_registered"] >= 1
        assert "https://modirumgespi.com/" in fetch_calls

        # And the same query now hits warm.
        post = await internal_search.search("Modirum Gespi defence")
        assert post, "internal index must return hits after ensure_indexed"
        urls = [h["url"] for h in post]
        assert any("modirumgespi.com" in u for u in urls), urls

        # The discovered domain is registered tier 4.
        d = await db.get_domain("modirumgespi.com")
        assert d is not None
        assert d["tier"] == 4
    _run(go())


# ─────────────────────────────────────────────────────────────────
# Lifespan smoke
# ─────────────────────────────────────────────────────────────────

def test_rf507_lifespan_smoke_imports():
    """Lifespan must not break on a clean import of the new modules."""
    from aria_service.crawler import on_demand as _od  # noqa
    from aria_service.crawler import runner as _r  # noqa
    assert callable(_od.ensure_indexed)
    assert callable(_od.background_ensure)
    assert callable(_od.auto_register_domain)
    assert callable(_od.looks_like_entity_query)
    assert callable(_r.crawl_loop)

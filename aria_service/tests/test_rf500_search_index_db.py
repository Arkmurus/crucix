"""R-F500 — SQLite + FTS5 schema for ARIA's own search engine.

Unit tests cover:
  - connect/close lifecycle on in-memory db
  - schema creation is idempotent (safe on reboot)
  - register_domain upsert + list filtering
  - upsert_document idempotency + FTS5 sync via triggers
  - search_fts BM25 ranking + lang / tier filters
  - sanitize_fts handles user-supplied special chars without crashing
  - stats reports correct counts

Capability test (the R-F500 user-visible promise): the schema can store
50 fixture pages and answer a BM25 query that puts the relevant page on
top — this is the foundation for the Modirum-Gespi homograph win.

All tests use `:memory:` to keep them isolated from each other and from
production /data state. The module's globals are reset between tests
via the `db_ready` fixture.
"""
from __future__ import annotations

import asyncio
import pytest

from aria_service.search_index import db


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture
def db_ready():
    """Per-test in-memory db. Ensures clean module-level state."""
    _run(db._reset())
    ok = _run(db.connect(":memory:"))
    assert ok, "connect(:memory:) must succeed"
    yield db
    _run(db._reset())


# ─────────────────────────────────────────────────────────────────
# Lifecycle + schema
# ─────────────────────────────────────────────────────────────────

def test_connect_returns_true_on_in_memory(db_ready):
    """The fixture itself proves this; explicit case for posterity."""
    assert db_ready.get_connection() is not None


def test_schema_creation_is_idempotent(db_ready):
    """Calling _create_schema twice should not raise."""
    async def go():
        conn = db_ready.get_connection()
        await db_ready._create_schema(conn)  # second call — must not raise
        await conn.commit()
    _run(go())


# ─────────────────────────────────────────────────────────────────
# Domains
# ─────────────────────────────────────────────────────────────────

def test_register_domain_upserts(db_ready):
    async def go():
        await db_ready.register_domain(
            "defenceweb.co.za", tier=2, sector="defence", region="za",
            language="en", rate_limit_per_sec=1.0,
        )
        d = await db_ready.get_domain("defenceweb.co.za")
        assert d is not None
        assert d["tier"] == 2
        assert d["sector"] == "defence"

        # Re-register with new tier — upsert path.
        await db_ready.register_domain(
            "defenceweb.co.za", tier=1, sector="defence", language="en",
        )
        d2 = await db_ready.get_domain("defenceweb.co.za")
        assert d2["tier"] == 1
    _run(go())


def test_list_domains_filters_by_sector_and_enabled(db_ready):
    async def go():
        await db_ready.register_domain("defenceweb.co.za", tier=2,
                                       sector="defence", language="en")
        await db_ready.register_domain("jornaldeangola.ao", tier=2,
                                       sector="lusophone", language="pt")
        await db_ready.register_domain("disabled.example", tier=5,
                                       sector="defence", language="en",
                                       enabled=False)

        all_def = await db_ready.list_domains(sector="defence")
        defence_domains = [d["domain"] for d in all_def]
        assert "defenceweb.co.za" in defence_domains
        assert "disabled.example" not in defence_domains  # enabled_only default

        with_disabled = await db_ready.list_domains(enabled_only=False,
                                                    sector="defence")
        all_dom = [d["domain"] for d in with_disabled]
        assert "disabled.example" in all_dom
    _run(go())


def test_mark_domain_crawled_and_cache_robots(db_ready):
    async def go():
        await db_ready.register_domain("ted.europa.eu", tier=1,
                                       sector="tenders", language="en")
        await db_ready.mark_domain_crawled("ted.europa.eu")
        d = await db_ready.get_domain("ted.europa.eu")
        assert d["last_crawled_at"] is not None

        await db_ready.cache_robots("ted.europa.eu", "User-agent: *\nAllow: /")
        d2 = await db_ready.get_domain("ted.europa.eu")
        assert d2["robots_txt"] == "User-agent: *\nAllow: /"
    _run(go())


# ─────────────────────────────────────────────────────────────────
# Documents + FTS5 sync triggers
# ─────────────────────────────────────────────────────────────────

def test_upsert_document_and_get_round_trip(db_ready):
    async def go():
        await db_ready.register_domain("example.com", tier=3, sector="news",
                                       language="en")
        doc_id = await db_ready.upsert_document(
            url="https://example.com/x",
            domain="example.com",
            title="Example title",
            headings="H1 Example",
            body="Example body text discussing Modirum Gespi contract win.",
            language="en", source_tier=3,
        )
        assert doc_id, "upsert must return a doc_id"

        d = await db_ready.get_document(url="https://example.com/x")
        assert d is not None
        assert d["title"] == "Example title"
        assert d["domain"] == "example.com"
        assert "Modirum" in d["body"]
    _run(go())


def test_upsert_document_is_idempotent(db_ready):
    async def go():
        await db_ready.register_domain("example.com", tier=3, sector="news",
                                       language="en")
        d1 = await db_ready.upsert_document(
            url="https://example.com/page",
            domain="example.com",
            title="First", headings="", body="hello world",
            language="en", source_tier=3,
        )
        d2 = await db_ready.upsert_document(
            url="https://example.com/page",
            domain="example.com",
            title="Second", headings="", body="hello updated world",
            language="en", source_tier=3,
        )
        assert d1 == d2, "same canonical_url must produce same doc_id"

        # Only one row should exist.
        conn = db_ready.get_connection()
        cur = await conn.execute(
            "SELECT COUNT(*) FROM documents WHERE url = ?",
            ("https://example.com/page",),
        )
        n = (await cur.fetchone())[0]
        await cur.close()
        assert n == 1

        # And the body was updated.
        d = await db_ready.get_document(url="https://example.com/page")
        assert d["title"] == "Second"
        assert "updated" in d["body"]
    _run(go())


def test_delete_document_drops_fts_row(db_ready):
    """After DELETE, FTS5 should no longer return the document — proves
    the documents_ad trigger is firing."""
    async def go():
        await db_ready.register_domain("example.com", tier=3, sector="news",
                                       language="en")
        await db_ready.upsert_document(
            url="https://example.com/del",
            domain="example.com",
            title="Delete me", headings="", body="unique_token_xyzzy",
            language="en", source_tier=3,
        )
        # Confirm FTS finds it.
        results = await db_ready.search_fts("unique_token_xyzzy")
        assert len(results) == 1

        ok = await db_ready.delete_document(url="https://example.com/del")
        assert ok

        results_after = await db_ready.search_fts("unique_token_xyzzy")
        assert results_after == []
    _run(go())


# ─────────────────────────────────────────────────────────────────
# FTS5 query
# ─────────────────────────────────────────────────────────────────

def test_search_fts_returns_bm25_ranked_results(db_ready):
    """Two docs both match; the one with denser term frequency should rank
    higher. BM25 is what FTS5 produces natively."""
    async def go():
        await db_ready.register_domain("example.com", tier=3, sector="news",
                                       language="en")
        # Sparse hit.
        await db_ready.upsert_document(
            url="https://example.com/sparse",
            domain="example.com",
            title="Mentions Modirum once briefly",
            headings="",
            body="Some unrelated text. " * 80 + " Modirum.",
            language="en", source_tier=3,
        )
        # Dense hit.
        await db_ready.upsert_document(
            url="https://example.com/dense",
            domain="example.com",
            title="Modirum Modirum Modirum contract",
            headings="Modirum Gespi profile",
            body=("Modirum Gespi has been awarded a contract. "
                  "Modirum operates in defence integration. "
                  "Modirum's portfolio includes Gespi. ") * 3,
            language="en", source_tier=3,
        )
        results = await db_ready.search_fts("Modirum Gespi")
        urls = [r["url"] for r in results]
        assert urls[0] == "https://example.com/dense", (
            f"dense hit should rank first, got order: {urls}"
        )
    _run(go())


def test_search_fts_lang_filter(db_ready):
    async def go():
        await db_ready.register_domain("example.com", tier=3, sector="news",
                                       language="en")
        await db_ready.register_domain("exemplo.pt", tier=3, sector="news",
                                       language="pt")
        await db_ready.upsert_document(
            url="https://example.com/en",
            domain="example.com",
            title="Defence contract", headings="",
            body="English coverage of defence contract.",
            language="en", source_tier=3,
        )
        await db_ready.upsert_document(
            url="https://exemplo.pt/pt",
            domain="exemplo.pt",
            title="Contrato de defesa", headings="",
            body="Portuguese coverage of defence contract.",
            language="pt", source_tier=3,
        )
        en_only = await db_ready.search_fts("defence", lang="en")
        pt_only = await db_ready.search_fts("defence", lang="pt")
        assert all(r["language"] == "en" for r in en_only)
        assert all(r["language"] == "pt" for r in pt_only)
    _run(go())


def test_search_fts_tier_filter(db_ready):
    """min_tier=2 should exclude tier 3/4/5 (lower number = better tier)."""
    async def go():
        await db_ready.register_domain("tier1.gov", tier=1, sector="official",
                                       language="en")
        await db_ready.register_domain("tier3.news", tier=3, sector="news",
                                       language="en")
        await db_ready.upsert_document(
            url="https://tier1.gov/x", domain="tier1.gov",
            title="Official contract notice", headings="",
            body="Official notice of contract award.",
            language="en", source_tier=1,
        )
        await db_ready.upsert_document(
            url="https://tier3.news/x", domain="tier3.news",
            title="Blog post about contract", headings="",
            body="A blog post about the contract award.",
            language="en", source_tier=3,
        )
        high_only = await db_ready.search_fts("contract", min_tier=2)
        urls = [r["url"] for r in high_only]
        assert "https://tier1.gov/x" in urls
        assert "https://tier3.news/x" not in urls
    _run(go())


def test_search_fts_handles_special_chars(db_ready):
    """User queries with FTS-reserved characters must not crash."""
    async def go():
        await db_ready.register_domain("example.com", tier=3, sector="news",
                                       language="en")
        await db_ready.upsert_document(
            url="https://example.com/x",
            domain="example.com",
            title="Hello world", headings="",
            body="Hello world contents.",
            language="en", source_tier=3,
        )
        # Each of these would crash FTS5 if not sanitized.
        for q in ["hello (world)", "AND OR NEAR", "hello: world",
                  "\"unterminated", "*", "-leading"]:
            results = await db_ready.search_fts(q)
            # No assertion on content — only that the call returned cleanly.
            assert isinstance(results, list)
    _run(go())


# ─────────────────────────────────────────────────────────────────
# Stats
# ─────────────────────────────────────────────────────────────────

def test_stats_reports_correct_counts(db_ready):
    async def go():
        await db_ready.register_domain("a.com", tier=2, sector="defence",
                                       language="en")
        await db_ready.register_domain("b.pt", tier=2, sector="lusophone",
                                       language="pt")
        await db_ready.upsert_document(
            url="https://a.com/1", domain="a.com",
            title="t1", headings="", body="body en",
            language="en", source_tier=2,
        )
        await db_ready.upsert_document(
            url="https://a.com/2", domain="a.com",
            title="t2", headings="", body="body en 2",
            language="en", source_tier=2,
        )
        await db_ready.upsert_document(
            url="https://b.pt/1", domain="b.pt",
            title="p1", headings="", body="body pt",
            language="pt", source_tier=2,
        )

        s = await db_ready.stats()
        assert s["ready"] is True
        assert s["documents"] == 3
        assert s["domains_enabled"] == 2
        assert s["by_language"]["en"] == 2
        assert s["by_language"]["pt"] == 1
        assert s["by_tier"]["tier_2"] == 3
    _run(go())


# ─────────────────────────────────────────────────────────────────
# CAPABILITY TEST — the R-F500 user-visible promise
# ─────────────────────────────────────────────────────────────────

def test_rf500_capability_50_pages_bm25_picks_relevant(db_ready):
    """Foundation for the Modirum-Gespi homograph win: even with 49 noise
    pages and 1 relevant page in the corpus, BM25 picks the relevant one.

    This proves the schema is sound and FTS5 is wired correctly. The full
    homograph defence comes in R-F503 with hybrid ranking + filter; here
    we just confirm the foundation does its job under noise."""
    async def go():
        await db_ready.register_domain("noise.example", tier=4,
                                       sector="academic", language="en")
        await db_ready.register_domain("modirum-press.example", tier=2,
                                       sector="news", language="en")

        # 49 noise pages — each mentions "modirum" once as a variable name
        # in cryptographic code (the homograph that broke chat for real).
        for i in range(49):
            await db_ready.upsert_document(
                url=f"https://noise.example/paper-{i}",
                domain="noise.example",
                title=f"On elliptic curves over finite fields (paper {i})",
                headings="Algorithm and theorem",
                body=("Let modirum be a variable in the algorithm. "
                      "We prove that for all primes p, the algorithm "
                      "terminates. The function f(modirum) = ... "
                      "Theorem: the discrete log problem ... "
                      "Lemma: every elliptic curve has ... "),
                language="en", source_tier=4,
            )

        # 1 relevant page — Modirum Gespi corporate press release.
        await db_ready.upsert_document(
            url="https://modirum-press.example/award-1",
            domain="modirum-press.example",
            title="Modirum Gespi awarded defence integration contract",
            headings="Modirum Gespi · Angola · contract win",
            body=("Modirum Gespi has been awarded a defence systems "
                  "integration contract for the Angolan Ministry of "
                  "Defence. The company, headquartered in Luanda with "
                  "a Finnish parent, will deliver tactical communications "
                  "and battlefield-management systems. The contract is "
                  "valued at an undisclosed sum and runs for three years. "
                  "Modirum Gespi previously delivered systems to Mozambique."),
            language="en", source_tier=2,
        )

        # Query the way a chat user would.
        results = await db_ready.search_fts("Modirum Gespi contract", top_k=5)
        urls = [r["url"] for r in results]
        assert "https://modirum-press.example/award-1" in urls[:3], (
            f"Modirum Gespi press release must be in top-3 BM25 hits "
            f"despite 49x homograph noise. Got: {urls}"
        )
        # And the relevant one should be #1 — BM25 weights "Modirum Gespi"
        # appearing in title + headings + dense body heavily.
        assert urls[0] == "https://modirum-press.example/award-1", (
            f"BM25 must put the press release first. Got #1: {urls[0]}"
        )
    _run(go())

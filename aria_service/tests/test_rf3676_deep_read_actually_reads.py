"""R-F3676 — the news deep read never read anything, and said so in a plausible way.

``news_enrichment._fetch_body`` called ``web_search.fetch_url_text`` behind a
``hasattr`` guard. That function DOES NOT EXIST and never did, so the guard
evaluated to ``""`` on every call and the whole R-F3499/R-F3509 selective deep
read — engine, budget, wiring, tests — never once read an article.

It survived because the symptom was plausible: every attempt was recorded as
``enrichment_failed`` with "body too short to be a real read", which reads like a
consent wall or a nav shell, not a missing function.

Measured live on aria-intel 2026-08-04:
  * 343 enrichment failures, EVERY ONE "(0 chars)" — never 40, never 200, which
    is what a real mix of paywalls and stubs looks like. Uniform zero is the tell.
  * archive-wide extraction_status: feed_only 1,472 / enrichment_failed 343 /
    enriched ZERO. Every article ARIA held was title + RSS blurb.

R-F3499's and R-F3509's own tests stayed green throughout, because both fake
``_fetch_body``. That is the gap these tests close: the seam was always tested,
the thing behind it never was.

All confirmed FAILING against the pre-fix code (§3c).
"""

import ast
import inspect
import textwrap

import pytest

from aria_service.intel import news_enrichment as ne


def test_rf3676_fetch_body_does_not_depend_on_a_function_that_does_not_exist():
    """THE DEFECT, stated directly: the call target must be real.

    A `hasattr` guard around a non-existent function is indistinguishable from a
    working one that returns nothing — which is exactly why this ran unnoticed
    for the life of the feature.
    """
    # R-F3597 §16 — resolve BY NAME through the current file's AST. This test
    # first used inspect.getsource and got a stale line slice spanning the old
    # body and the new one, which is the exact failure _source_probe exists for.
    from . import _source_probe

    src = _source_probe.function_source(ne, "_fetch_body")
    # Strip the docstring: it deliberately QUOTES the broken line so the next
    # reader knows what this replaced, and matching that would be a false alarm.
    tree = ast.parse(textwrap.dedent(src))
    fn = tree.body[0]
    if (fn.body and isinstance(fn.body[0], ast.Expr)
            and isinstance(fn.body[0].value, ast.Constant)
            and isinstance(fn.body[0].value.value, str)):
        fn.body = fn.body[1:]
    src = ast.unparse(fn)

    assert "fetch_url_text" not in src, (
        "web_search.fetch_url_text does not exist; calling it returns an empty "
        "body forever"
    )
    assert "hasattr" not in src, (
        "a capability guard here silently disables the deep read instead of "
        "failing loudly — a missing fetcher must raise"
    )


def test_rf3676_the_fetcher_it_now_calls_actually_exists():
    """§3b — verify the replacement before trusting it."""
    from aria_service.intel import researcher

    fn = getattr(researcher, "_fetch_article_text", None)
    assert fn is not None, "researcher._fetch_article_text must exist"
    assert inspect.iscoroutinefunction(fn), "it must be awaitable"


@pytest.mark.asyncio
async def test_rf3676_capability_a_readable_page_is_actually_enriched(monkeypatch):
    """THE USER-VISIBLE OUTCOME: an article with a real body reaches `enriched`.

    Pre-fix this was impossible for ANY page — the body was always "".
    """
    from aria_service.intel import news_archive as na
    from aria_service.intel import researcher

    na._reset_for_tests()
    res = await na.archive_article({
        "url": "https://www.army.mil/article/example",
        "title": "Army awards sustainment contract",
        "summary": "Short RSS blurb.",
        "category": "defence_global",
    })
    aid = res["article_id"]

    body = "Full article body. " * 60          # comfortably over _MIN_BODY_CHARS

    fetched: list[str] = []

    async def _fake_fetch(url, timeout=0):
        # The archive stores the CANONICAL url (canonicalise_url strips `www.`),
        # so assert on what is actually passed rather than the submitted form.
        fetched.append(url)
        return body

    monkeypatch.setattr(researcher, "_fetch_article_text", _fake_fetch)

    out = await ne.enrich_archived_article(aid)

    assert out["enriched"] is True, f"deep read failed: {out.get('reason')}"
    assert out["chars"] == len(body.strip())
    assert fetched and "army.mil/article/example" in fetched[0], (
        f"the archived URL must be what gets fetched, got {fetched!r}"
    )

    rec = await na.get_article(aid)
    assert rec["extraction_status"] == ne.STATUS_ENRICHED
    # set_extraction_status stores the read text INTO feed_summary (upgrading the
    # RSS blurb) and leaves body_ref as an `inline:<len>` pointer.
    assert "Full article body" in (rec.get("feed_summary") or ""), (
        "the read text must be stored, not just the status flipped"
    )
    assert rec.get("body_ref") == f"inline:{len(body.strip())}"


@pytest.mark.asyncio
async def test_rf3676_a_genuinely_thin_page_is_still_honestly_failed(monkeypatch):
    """REGRESSION GUARD: the fix must not turn a nav shell into a fake read.

    "body too short" stays a real outcome — it just has to mean it now.
    """
    from aria_service.intel import news_archive as na
    from aria_service.intel import researcher

    na._reset_for_tests()
    res = await na.archive_article({"url": "https://thin.example/1", "title": "t",
                                    "summary": "s", "category": "technology"})
    aid = res["article_id"]

    async def _thin(url, timeout=0):
        return "Accept cookies"

    monkeypatch.setattr(researcher, "_fetch_article_text", _thin)

    out = await ne.enrich_archived_article(aid)
    assert out["enriched"] is False
    assert "too short" in out["reason"]
    rec = await na.get_article(aid)
    assert rec["extraction_status"] == ne.STATUS_FAILED


@pytest.mark.asyncio
async def test_rf3676_a_broken_fetcher_raises_and_is_wired_not_swallowed(monkeypatch):
    """The failure mode this replaces: silence. A fetch that breaks must be
    recorded with its REAL reason and reach the brain (§21a)."""
    from unittest.mock import MagicMock

    from aria_service.intel import news_archive as na
    from aria_service.intel import researcher

    na._reset_for_tests()
    res = await na.archive_article({"url": "https://boom.example/1", "title": "t",
                                    "summary": "s", "category": "technology"})
    aid = res["article_id"]

    async def _boom(url, timeout=0):
        raise RuntimeError("fetcher exploded")

    monkeypatch.setattr(researcher, "_fetch_article_text", _boom)
    wire = MagicMock()
    monkeypatch.setattr(ne, "wire_failure", wire)

    out = await ne.enrich_archived_article(aid)

    assert out["enriched"] is False
    assert "fetcher exploded" in out["reason"], (
        "the real cause must be recorded, not flattened into 'body too short'"
    )
    assert wire.call_count == 1, "a broken deep read must reach the brain"
    rec = await na.get_article(aid)
    assert rec["extraction_status"] == ne.STATUS_FAILED


@pytest.mark.asyncio
async def test_rf3676_no_double_extraction(monkeypatch):
    """`_fetch_article_text` already returns EXTRACTED text. Running the
    structured extractor over it again would strip real prose as if it were
    HTML."""
    from aria_service.intel import researcher

    async def _plain(url, timeout=0):
        return "Plain extracted prose with no markup at all. " * 10

    monkeypatch.setattr(researcher, "_fetch_article_text", _plain)
    body = await ne._fetch_body("https://x.example/1")
    assert body.startswith("Plain extracted prose"), (
        "the fetcher's already-extracted text must be returned as-is"
    )

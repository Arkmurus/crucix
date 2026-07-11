"""R-F2534 — rewire company_investigator's dead web_crawler APIs.

R-F2532 made these two phases VISIBLE as dead:
  - deep crawl: called module-level web_crawler.crawl() — doesn't exist. crawl() is an
    instance method on UniversalWebCrawler, and it returns CrawledPage DATACLASSES, so
    the old page.get(...) would AttributeError even once wired (cf. R-F2407).
  - tech stack: called web_crawler.get_headers() — exists NOWHERE.

R-F2534 wires deep crawl to UniversalWebCrawler().crawl() with attribute access, and
replaces get_headers() with a direct lightweight httpx header fetch.

Capability tests drive the REAL phase functions.
"""
from __future__ import annotations

import asyncio

import aria_service.intel.company_investigator as ci


# ─────────────────────────── deep crawl ────────────────────────────────
def test_deep_crawl_instantiates_crawler_and_reads_dataclass_attrs(monkeypatch):
    import aria_service.intel.web_crawler as wc

    made = {"instantiated": False}

    class _FakeCrawler:
        def __init__(self):
            made["instantiated"] = True

        async def crawl(self, start_url, max_pages=50, max_depth=2, topic_filter=None):
            # Real crawl returns CrawledPage DATACLASS instances (not dicts).
            return [
                wc.CrawledPage(url="https://acme.example/about", title="About Acme",
                               text="Acme Trading Ltd is a UK firm.", content="…"),
                wc.CrawledPage(url="", title="skip me", text="no url -> skipped"),
            ]

    monkeypatch.setattr(wc, "UniversalWebCrawler", _FakeCrawler)
    # Old dead module-level API must not be reached.
    monkeypatch.setattr(wc, "crawl", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("must not call module-level web_crawler.crawl (dead API)")), raising=False)

    rep = ci.InvestigationReport(entity_name="Acme")
    asyncio.run(ci._phase_deep_crawl(rep, "Acme", "https://acme.example", 2))

    assert made["instantiated"], "UniversalWebCrawler was not instantiated"
    crawl_findings = [f for f in rep.findings if f.category == "crawl"]
    assert len(crawl_findings) == 1, "should skip the url-less page and keep the real one"
    assert crawl_findings[0].source == "https://acme.example/about"
    assert "About Acme" in crawl_findings[0].title
    assert "Acme Trading" in crawl_findings[0].summary
    assert not any("deep crawl" in p for p in rep.phase_failures)


def test_deep_crawl_no_website_is_noop(monkeypatch):
    rep = ci.InvestigationReport(entity_name="Acme")
    asyncio.run(ci._phase_deep_crawl(rep, "Acme", "", 2))
    assert not rep.findings and not rep.phase_failures  # early return, nothing recorded


# ─────────────────────────── tech stack ────────────────────────────────
def test_tech_stack_fetches_real_headers(monkeypatch):
    import httpx

    class _FakeResp:
        status_code = 200
        headers = {"Server": "nginx/1.25", "X-Powered-By": "PHP/8.2"}

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def head(self, url):
            return _FakeResp()

        async def get(self, url):
            return _FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    rep = ci.InvestigationReport(entity_name="Acme")
    asyncio.run(ci._phase_tech_stack(rep, "acme.example"))  # no scheme -> https:// added

    tech = [f for f in rep.findings if f.category == "tech"]
    assert tech, "no tech finding created from real headers"
    assert "nginx/1.25" in tech[0].summary and "PHP/8.2" in tech[0].summary
    assert not any("tech stack" in p for p in rep.phase_failures)


def test_tech_stack_no_website_is_noop():
    rep = ci.InvestigationReport(entity_name="Acme")
    asyncio.run(ci._phase_tech_stack(rep, ""))
    assert not rep.findings and not rep.phase_failures


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))

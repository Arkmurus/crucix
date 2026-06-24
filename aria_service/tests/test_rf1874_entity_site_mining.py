"""R-F1874 — direct-source-first DD: mine the entity's OWN website as PRIMARY
evidence so the DD has real content even when general web search is blocked.

Network-independent: a fake httpx client serves canned HTML for the homepage +
two sub-pages. Proves _mine_entity_website discovers same-domain key pages,
skips off-domain links, and returns extracted text.
"""
from __future__ import annotations

import asyncio

import aria_service.intel.dd_orchestrator as dd


class _FakeResp:
    def __init__(self, text, status=200, url=""):
        self.text = text
        self.status_code = status
        self.headers = {}
        self.url = url

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


class _FakeClient:
    def __init__(self, pages):
        self._pages = pages

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url):
        for k, v in self._pages.items():
            if url.rstrip("/") == k.rstrip("/"):
                return _FakeResp(v, url=url)
        return _FakeResp("<html><body>404</body></html>", status=404, url=url)


_HOME = """<html><head><title>Modirum Gespi — Payment Security</title></head><body>
  <h1>Modirum Gespi</h1>
  <p>We provide EMV payment security solutions across Europe.</p>
  <a href="/about">About Us</a>
  <a href="/team">Our Team</a>
  <a href="https://twitter.com/modirum">Twitter</a>
</body></html>"""
_ABOUT = ("<html><head><title>About</title></head><body>"
          "<p>Modirum Gespi was founded in 2010 in Lisbon, Portugal. Company registration "
          "number 50012345. We specialise in EMV payment security, tokenisation and "
          "3-D Secure authentication for banks and acquirers across the European Union, "
          "and operate under PCI-DSS and PSD2 regulatory frameworks.</p></body></html>")
_TEAM = ("<html><head><title>Team</title></head><body>"
         "<p>Our leadership team: CEO Jane Silva, CTO Joao Costa, and Head of Compliance "
         "Maria Santos. The board brings decades of experience in payments, banking "
         "security and financial-services regulation across Portugal and the wider EU.</p>"
         "</body></html>")


def test_mine_entity_website_extracts_primary_pages(monkeypatch):
    pages = {
        "https://modirumgespi.com": _HOME,
        "https://modirumgespi.com/about": _ABOUT,
        "https://modirumgespi.com/team": _TEAM,
    }
    monkeypatch.setattr(dd, "_ssrf_safe_url", lambda u: (True, ""))
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _FakeClient(pages))

    out = asyncio.run(dd._mine_entity_website("https://modirumgespi.com"))

    # homepage + the two same-domain key pages (twitter is off-domain → skipped)
    assert len(out) >= 3, f"expected homepage + 2 sub-pages, got {len(out)}: {[p['url'] for p in out]}"
    urls = " ".join(p["url"] for p in out)
    assert "/about" in urls and "/team" in urls
    assert "twitter.com" not in urls, "off-domain link must not be mined"

    blob = " ".join(p["text"] for p in out).lower()
    assert "payment security" in blob          # homepage content
    assert "lisbon" in blob and "50012345" in blob  # about page (jurisdiction + reg#)
    assert "jane silva" in blob                 # team page (a real person, not search)


def test_mine_entity_website_ssrf_blocked_returns_empty(monkeypatch):
    monkeypatch.setattr(dd, "_ssrf_safe_url", lambda u: (False, "private"))
    out = asyncio.run(dd._mine_entity_website("http://169.254.169.254/"))
    assert out == []


def test_mine_entity_website_empty_url():
    assert asyncio.run(dd._mine_entity_website("")) == []

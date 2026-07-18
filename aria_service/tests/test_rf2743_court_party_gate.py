"""R-F2743 — court records must be attributed only when the subject is a PARTY.

search_us_courts (CourtListener full-text) + search_uk_courts (bailii via RSS) kept
EVERY hit whose text/index mentioned the name, tagged tier 1a. A full-text court
search returns opinions that mention a name anywhere (a cited precedent, an
attorney's other client, a different same-named entity), so the "Litigation history:
N cases" headline was inflated by opinions where the subject is not a party. Now each
hit is gated on the subject appearing in the case CAPTION.
"""
from __future__ import annotations

import asyncio

import aria_service.intel.sources.court_records as c


def test_rf2743_entity_in_caption_contract():
    assert c._entity_in_caption("Acme Ventures", "Acme Ventures LLC v. Smith") is True
    assert c._entity_in_caption("Acme Ventures", "Smith v. Jones") is False        # mention, not a party
    assert c._entity_in_caption("Rosoboronexport", "United States v. Rosoboronexport") is True
    assert c._entity_in_caption("Acme Ventures", "In re Zenith Holdings") is False
    assert c._entity_in_caption("Acme Ventures", "") is False


def test_rf2743_uk_rss_drops_mention_only_captions():
    rss = (
        "<rss>"
        "<item><title>Acme Ventures Ltd v Crown - bailii.org</title>"
        "<link>http://bailii.org/a</link><pubDate>2024</pubDate></item>"
        "<item><title>Smith v Jones - bailii.org</title><link>http://bailii.org/b</link></item>"
        "</rss>"
    )
    hits = c._parse_bailii_rss(rss, 10, entity_name="Acme Ventures")
    assert [h["title"] for h in hits] == ["Acme Ventures Ltd v Crown"], \
        "only the caption where the subject is a party is kept"


def test_rf2743_uk_no_entity_name_keeps_all_backcompat():
    # Called without entity_name (legacy) → no gating, preserves the old behaviour.
    rss = ("<rss><item><title>Smith v Jones - bailii.org</title>"
           "<link>http://bailii.org/b</link></item></rss>")
    assert len(c._parse_bailii_rss(rss, 10)) == 1


def test_rf2743_us_search_gates_on_caption(monkeypatch):
    class _Resp:
        status_code = 200

        def json(self):
            return {"results": [
                {"caseName": "Acme Ventures LLC v. Smith", "court": "ca9",
                 "dateFiled": "2022-01-01", "absolute_url": "/x/", "snippet": "..."},
                {"caseName": "Smith v. Jones", "court": "ca9",  # mentions Acme in body only
                 "dateFiled": "2021-01-01", "absolute_url": "/y/", "snippet": "re Acme Ventures ..."},
            ]}

    class _Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return _Resp()

    monkeypatch.setattr(c.httpx, "AsyncClient", lambda *a, **k: _Client())
    hits = asyncio.run(c.search_us_courts("Acme Ventures"))
    assert [h["title"] for h in hits] == ["Acme Ventures LLC v. Smith"], \
        "the mention-only opinion (Smith v. Jones) must not be attributed to the subject"

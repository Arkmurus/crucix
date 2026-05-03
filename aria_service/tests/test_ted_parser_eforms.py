"""Regression test for R-F33 — TED v3 eForms parser.

Live evidence 2026-05-03 09:41:31 (post-R-F31):
  [TED] API returned 400: {"objectName":"publicExpertSearchRequestV1",
  "field":"fields","message":"must not be empty"}

R-F31 had dropped the `fields` parameter to "let TED return its default";
TED's default is "no default — fields is required and non-empty". Three
prior whack-a-mole iterations (R-F29/30/31) tried guessing the schema
without reading TED's published OpenAPI. R-F33 pulled
api.ted.europa.eu/api-v3.yaml, requested a valid eForms field set, and
rewrote the parser to read the eForms response shape (i18n maps, alpha-3
country codes, links nested under html.{LANG}).

This test pins the parser contract so the next "TED returns slightly
different shapes" regression is caught before deploy.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx

from aria_service.intel.tender_monitor import _crawl_ted, _ted_i18n_pick_str, _ISO3_TO_ISO2


# ── i18n picker ────────────────────────────────────────────────────────────


def test_i18n_picks_eng_first():
    """Title shape: {"eng": "...", "fra": "..."} — pick eng."""
    out = _ted_i18n_pick_str({"fra": "Avis de marché", "eng": "Contract notice", "deu": "Bekanntmachung"})
    assert out == "Contract notice"


def test_i18n_falls_through_to_fra_when_no_eng():
    out = _ted_i18n_pick_str({"fra": "Avis", "ita": "Avviso"})
    assert out == "Avis"


def test_i18n_picks_any_when_preferred_missing():
    """If none of eng/fra/deu present, take the first non-empty value."""
    out = _ted_i18n_pick_str({"pol": "Ogłoszenie", "hun": "Közlemény"})
    assert out in ("Ogłoszenie", "Közlemény")


def test_i18n_handles_list_values():
    """description-lot shape: {"eng": ["lot1 desc", "lot2 desc"]} — join."""
    out = _ted_i18n_pick_str({"eng": ["First lot description.", "Second lot description."]})
    assert "First lot description." in out
    assert "Second lot description." in out


def test_i18n_returns_empty_for_none_or_empty():
    assert _ted_i18n_pick_str(None) == ""
    assert _ted_i18n_pick_str({}) == ""
    assert _ted_i18n_pick_str("") == ""


def test_i18n_handles_plain_string():
    """Defensive: if TED returns a plain string instead of a map."""
    assert _ted_i18n_pick_str("plain text") == "plain text"


# ── Country alpha-3 → alpha-2 ──────────────────────────────────────────────


def test_iso3_to_iso2_covers_eu_core():
    """Sample of EU-27 + UK + neighbours that TED publishes against."""
    assert _ISO3_TO_ISO2["POL"] == "PL"
    assert _ISO3_TO_ISO2["DEU"] == "DE"
    assert _ISO3_TO_ISO2["FRA"] == "FR"
    assert _ISO3_TO_ISO2["GBR"] == "GB"
    assert _ISO3_TO_ISO2["ESP"] == "ES"
    assert _ISO3_TO_ISO2["TUR"] == "TR"


# ── Live-shape parser ──────────────────────────────────────────────────────


# Captured from a real TED v3 response 2026-05-03; trimmed to one notice.
TED_LIVE_RESPONSE_FIXTURE = {
    "totalNoticeCount": 651,
    "notices": [
        {
            "publication-number": "262410-2026",
            "publication-date": "2026-04-20+02:00",
            "notice-type": "cn-standard",
            "notice-title": {
                "eng": "Poland — Personal protective equipment — Tender",
                "pol": "Polska — Środki ochrony osobistej — Przetarg",
            },
            "description-lot": {
                "eng": ["Supply of helmets and protective vests for police forces.",
                        "Delivery within 8 weeks of contract signature."],
                "pol": ["Dostawa hełmów i kamizelek dla policji."],
            },
            "buyer-name": {
                "eng": ["Ministry of the Interior"],
                "pol": ["Ministerstwo Spraw Wewnętrznych"],
            },
            "organisation-country-buyer": ["POL"],
            "classification-cpv": ["35113400", "18444200"],
            "deadline-receipt-tender-date-lot": ["2026-05-25+02:00"],
            "links": {
                "xml": {"MUL": "https://ted.europa.eu/en/notice/262410-2026/xml"},
                "html": {
                    "ENG": "https://ted.europa.eu/en/notice/-/detail/262410-2026",
                    "POL": "https://ted.europa.eu/pl/notice/-/detail/262410-2026",
                },
            },
        }
    ],
}


def _make_mock_client(json_payload: dict, status_code: int = 200):
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = status_code
    mock_resp.json = MagicMock(return_value=json_payload)
    mock_resp.text = ""
    mock_resp.content = b""
    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    return mock_client, mock_resp


def test_crawl_ted_parses_live_eforms_shape():
    mock_client, _ = _make_mock_client(TED_LIVE_RESPONSE_FIXTURE)
    tenders = asyncio.run(_crawl_ted(mock_client, max_results=10))

    assert len(tenders) == 1
    t = tenders[0]
    assert t.portal == "TED"
    assert t.title == "Poland — Personal protective equipment — Tender"
    assert "helmets" in t.description.lower()
    assert t.buyer == "Ministry of the Interior"
    assert t.country_iso2 == "PL"
    assert t.country == "Poland"
    assert "35113400" in t.cpv_codes
    assert t.deadline == "2026-05-25+02:00"
    assert t.publication_date == "2026-04-20+02:00"
    assert t.url == "https://ted.europa.eu/en/notice/-/detail/262410-2026"


def test_crawl_ted_400_returns_empty_does_not_raise():
    """If TED 400s the body, log and return [] cleanly — never crash
    the tender-monitor cycle (which would skip the other 7 portals)."""
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 400
    mock_resp.text = '{"message":"Validation error"}'
    mock_resp.content = b'{"message":"Validation error"}'
    mock_resp.json = MagicMock(side_effect=ValueError)

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_resp)

    tenders = asyncio.run(_crawl_ted(mock_client, max_results=10))
    assert tenders == []


def test_crawl_ted_falls_back_when_links_html_missing():
    """If `links.html` is absent, build URL from publication-number."""
    fixture = {
        "notices": [
            {
                "publication-number": "999999-2026",
                "notice-title": {"eng": "Test"},
                "organisation-country-buyer": ["FRA"],
                "classification-cpv": ["35000000"],
                # no links
            }
        ]
    }
    mock_client, _ = _make_mock_client(fixture)
    tenders = asyncio.run(_crawl_ted(mock_client, max_results=10))
    assert len(tenders) == 1
    assert tenders[0].url == "https://ted.europa.eu/en/notice/-/detail/999999-2026"
    assert tenders[0].country_iso2 == "FR"


def test_crawl_ted_request_body_carries_required_fields():
    """Pin the contract: fields parameter is non-empty (R-F33 root cause)
    and query uses the eForms `IN` syntax with date filter."""
    mock_client, _ = _make_mock_client({"notices": []})
    asyncio.run(_crawl_ted(mock_client, max_results=10))

    call = mock_client.post.call_args
    body = call.kwargs.get("json")
    assert body is not None, "Body must be POSTed as json"
    assert isinstance(body.get("fields"), list)
    assert len(body["fields"]) > 0, "R-F33: fields must not be empty"
    assert "notice-title" in body["fields"]
    assert "publication-number" in body["fields"]
    q = body.get("query", "")
    assert "classification-cpv IN" in q
    assert "publication-date >=" in q
    assert "publication-date <=" in q

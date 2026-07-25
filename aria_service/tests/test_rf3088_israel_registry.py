"""R-F3088 — Israel registry reads the official daily company dataset."""

import asyncio

from aria_service.intel import registry_adapters as ra


class _Response:
    status_code = 200

    @staticmethod
    def json() -> dict:
        return {
            "success": True,
            "result": {
                "records": [{
                    "מספר חברה": 520035874,
                    "שם חברה": "אלביט מערכות ל״א וסיגינט- אלישרא בע״מ",
                    "שם באנגלית": "ELBIT SYSTEMS EW AND SIGINT- ELISRA LTD",
                    "סטטוס חברה": "פעילה",
                    "תאריך התאגדות": "21/12/1966",
                    "שם עיר": "חולון",
                    "שם רחוב": "המרכבה",
                    "מספר בית": "29",
                    "מיקוד": 5885118,
                    "מדינה": "ישראל",
                }],
            },
        }


class _Client:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, *args, **kwargs):
        return _Response()


def test_israel_adapter_normalizes_official_dataset_fields(monkeypatch):
    """The adapter contract maps the CKAN record without inventing missing fields."""
    monkeypatch.setattr(ra.httpx, "AsyncClient", _Client)

    result = asyncio.run(ra._lookup_israel(
        "ELBIT SYSTEMS EW AND SIGINT- ELISRA LTD",
        "520035874",
    ))

    assert result is not None
    assert result["source_url"] == "https://data.gov.il/dataset/ica_companies"
    assert result["profile"]["company_number"] == "520035874"
    assert result["profile"]["registered_office_address"] == (
        "המרכבה 29, חולון, 5885118, ישראל"
    )


def test_english_query_returns_verified_official_israel_record(monkeypatch):
    """The real lookup path must match the source's English name and retain its address."""
    monkeypatch.setattr(ra.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(ra, "_record_coverage_outcome", lambda *args: None)

    result = asyncio.run(ra.lookup_entity(
        "ELBIT SYSTEMS EW AND SIGINT- ELISRA LTD",
        "IL",
    ))

    assert result is not None
    assert result["adapter"] == "israel_registrar_datagovil"
    assert result["registry_status"] == ra.RegistryStatus.VERIFIED.value
    assert result["profile"] == {
        "company_name": "ELBIT SYSTEMS EW AND SIGINT- ELISRA LTD",
        "company_number": "520035874",
        "company_status": "פעילה",
        "date_of_creation": "21/12/1966",
        "registered_office_address": "המרכבה 29, חולון, 5885118, ישראל",
        "jurisdiction": "IL",
        "sic_codes": [],
    }

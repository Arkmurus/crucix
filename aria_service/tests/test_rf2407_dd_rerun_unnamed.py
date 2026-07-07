"""R-F2407 — DD re-run unnamed-row live fix.

R-F2406 preserved lineage, but live reports can still have legacy blank
``entity_name`` index rows. The reports list must repair those from the stored
target before the browser can re-run them as "(unnamed)".
"""
import pytest


@pytest.mark.asyncio
async def test_list_reports_repairs_blank_entity_name_from_target(monkeypatch):
    from aria_service.intel import dd_orchestrator as ddo
    from aria_service.intel import redis_store as rs

    index = [{
        "run_id": "dd_blank",
        "entity_name": "",
        "canonical_entity_id": "company:GB:12345678",
        "created_at": "2026-07-07T20:00:00Z",
        "risk_classification": "AMBER-LIGHT",
    }]
    body = {
        "run_id": "dd_blank",
        "canonical_entity_id": "company:GB:12345678",
        "target": {
            "name": "Acme Ltd",
            "website_url": "https://acme.example",
        },
        "identity": {
            "entity_name": "",
            "entity_type": "company",
            "jurisdiction_iso2": "GB",
            "registration_number": "12345678",
        },
    }
    writes = {}

    async def fake_get_json(key):
        if key == ddo.REPORT_INDEX_KEY:
            return [dict(index[0])]
        if key == ddo.REPORT_REDIS_KEY.format(run_id="dd_blank"):
            return body
        return None

    async def fake_set_json(key, value, ex=None):
        writes[key] = value
        return True

    monkeypatch.setattr(rs, "get_json", fake_get_json)
    monkeypatch.setattr(rs, "set_json", fake_set_json)

    reports = await ddo.list_reports(limit=10)

    assert reports[0]["entity_name"] == "Acme Ltd"
    assert writes[ddo.REPORT_INDEX_KEY][0]["entity_name"] == "Acme Ltd"
    repaired_body = writes[ddo.REPORT_REDIS_KEY.format(run_id="dd_blank")]
    assert repaired_body["identity"]["entity_name"] == "Acme Ltd"

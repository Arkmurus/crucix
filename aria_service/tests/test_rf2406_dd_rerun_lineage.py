"""R-F2406 — DD re-runs preserve case lineage and identity.

The web re-run action must produce v2 of the same case, not a fresh unnamed
row. These tests cover the browser payload contract, route hydration from a
previous report, and persist-time canonical pinning.
"""
import pytest

from aria_service.intel.dd_schema import ARKDDReport, structured_view
from aria_service.routes.aria import _hydrate_dd_rerun_lineage


def test_structured_view_exposes_rerun_identity_fields():
    report = {
        "run_id": "dd_prev",
        "canonical_entity_id": "company:GB:12345678",
        "target": {"website_url": "https://acme.example"},
        "identity": {
            "entity_name": "Acme Ltd",
            "entity_type": "company",
            "jurisdiction": "United Kingdom",
            "jurisdiction_iso2": "GB",
            "registration_number": "12345678",
        },
    }

    view = structured_view(report)

    assert view["canonical_entity_id"] == "company:GB:12345678"
    assert view["jurisdiction_iso2"] == "GB"
    assert view["registration_number"] == "12345678"
    assert view["website_url"] == "https://acme.example"


@pytest.mark.asyncio
async def test_rerun_lineage_hydrates_missing_identity(monkeypatch):
    previous = {
        "run_id": "dd_prev",
        "canonical_entity_id": "company:GB:12345678",
        "version_number": 1,
        "target": {"website_url": "https://acme.example"},
        "identity": {
            "entity_name": "Acme Ltd",
            "entity_type": "company",
            "jurisdiction": "United Kingdom",
            "jurisdiction_iso2": "GB",
            "registration_number": "12345678",
        },
    }

    async def fake_get_json(key):
        assert key == "crucix:dd:report:dd_prev"
        return previous

    from aria_service.intel import redis_store as rs
    monkeypatch.setattr(rs, "get_json", fake_get_json)

    body = {
        "name": "Acme Ltd",
        "entity_type": "company",
        "previous_run_id": "dd_prev",
        "force": True,
    }
    await _hydrate_dd_rerun_lineage(body)

    assert body["canonical_entity_id"] == "company:GB:12345678"
    assert body["jurisdiction_iso2"] == "GB"
    assert body["registration_number"] == "12345678"
    assert body["website_url"] == "https://acme.example"
    assert body["website"] == "https://acme.example"
    assert body["_rerun_lineage"]["previous_run_id"] == "dd_prev"


@pytest.mark.asyncio
async def test_persist_pins_explicit_canonical_id_when_regnum_was_dropped(monkeypatch):
    from aria_service.intel import dd_orchestrator as ddo
    from aria_service.intel import redis_store as rs

    writes = {}
    index = [{
        "run_id": "dd_prev",
        "canonical_entity_id": "company:GB:12345678",
        "version_number": 1,
        "entity_name": "Acme Ltd",
    }]

    async def fake_get_json(key):
        if key == ddo.REPORT_INDEX_KEY:
            return list(index)
        if key == ddo.REPORT_REDIS_KEY.format(run_id="dd_prev"):
            return {
                "run_id": "dd_prev",
                "canonical_entity_id": "company:GB:12345678",
                "version_number": 1,
                "identity": {"entity_name": "Acme Ltd", "findings": []},
            }
        return None

    async def fake_set_json(key, value, ex=None):
        writes[key] = value
        return True

    monkeypatch.setattr(rs, "get_json", fake_get_json)
    monkeypatch.setattr(rs, "set_json", fake_set_json)
    async def fake_mutate_report_index(mutator, persist=True):
        return mutator(list(index))

    monkeypatch.setattr(ddo, "_mutate_report_index", fake_mutate_report_index)

    report = ARKDDReport(
        target={
            "name": "Acme Ltd",
            "type": "company",
            "canonical_entity_id": "company:GB:12345678",
            "previous_run_id": "dd_prev",
        }
    )
    report.run_id = "dd_new"
    report.identity.entity_name = "Acme Ltd"
    report.identity.entity_type = "company"
    report.identity.jurisdiction_iso2 = "GB"

    await ddo._persist_report(report)

    body = writes[ddo.REPORT_REDIS_KEY.format(run_id="dd_new")]
    assert body["canonical_entity_id"] == "company:GB:12345678"
    assert body["version_number"] == 2
    assert body["previous_run_id"] == "dd_prev"
    assert body["version_diff"]["previous_run_id"] == "dd_prev"

"""R-F2406 — DD re-runs preserve case lineage and identity.

The web re-run action must produce v2 of the same case, not a fresh unnamed
row. These tests cover the browser payload contract, route hydration from a
previous report, and persist-time canonical pinning.
"""
import pytest
from fastapi import HTTPException

from aria_service.intel.dd_schema import ARKDDReport, structured_view
from aria_service.routes.aria import _hydrate_dd_rerun_lineage, dd_orchestrate_ep


class _JsonRequest:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return dict(self._body)


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


def test_structured_view_uses_target_name_before_unnamed_placeholder():
    report = {
        "run_id": "dd_prev",
        "canonical_entity_id": "company:GB:12345678",
        "target": {"name": "Acme Ltd", "website_url": "https://acme.example"},
        "identity": {"entity_name": "", "entity_type": "company"},
    }

    view = structured_view(report)

    assert view["entity_name"] == "Acme Ltd"


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
async def test_rerun_lineage_overwrites_unnamed_display_placeholder(monkeypatch):
    previous = {
        "run_id": "dd_prev",
        "canonical_entity_id": "company:GB:12345678",
        "target": {"name": "Acme Ltd", "website_url": "https://acme.example"},
        "identity": {"entity_name": "", "entity_type": "company"},
    }

    async def fake_get_json(key):
        return previous

    from aria_service.intel import redis_store as rs
    monkeypatch.setattr(rs, "get_json", fake_get_json)

    body = {
        "name": "(unnamed)",
        "entity_type": "company",
        "previous_run_id": "dd_prev",
        "force": True,
    }
    await _hydrate_dd_rerun_lineage(body)

    assert body["name"] == "Acme Ltd"
    assert body["entity"] == "Acme Ltd"
    assert body["_rerun_lineage"]["entity_name"] == "Acme Ltd"


@pytest.mark.asyncio
async def test_rerun_route_rejects_unresolved_placeholder_before_async_start(monkeypatch):
    from aria_service.intel import dd_orchestrator as ddo
    from aria_service.intel import redis_store as rs

    previous = {
        "run_id": "dd_prev",
        "canonical_entity_id": "company:??:ba3af7a",
        "target": {"name": "", "query": ""},
        "identity": {"entity_name": "", "entity_type": "company"},
    }

    async def fake_get_json(key):
        assert key == ddo.REPORT_REDIS_KEY.format(run_id="dd_prev")
        return previous

    async def fail_mark_dd_running(*args, **kwargs):
        raise AssertionError("placeholder rerun must not create a running DD row")

    monkeypatch.setattr(rs, "get_json", fake_get_json)
    monkeypatch.setattr(ddo, "mark_dd_running", fail_mark_dd_running)

    with pytest.raises(HTTPException) as exc:
        await dd_orchestrate_ep(_JsonRequest({
            "name": "(unknown)",
            "entity_type": "company",
            "previous_run_id": "dd_prev",
            "async_mode": True,
            "force": True,
        }))

    assert exc.value.status_code == 422
    assert "placeholder names cannot be re-run" in exc.value.detail


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

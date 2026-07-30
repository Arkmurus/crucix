"""R-F3481 — evidence-led relationship-intelligence intake."""

from __future__ import annotations

import asyncio
import json
import types

from aria_service.intel import relationship_intelligence as ri
from aria_service.routes import aria as A


def _req(body: dict):
    async def _json():
        return body

    return types.SimpleNamespace(json=_json)


def _run(coro):
    return asyncio.run(coro)


def _body(response):
    if isinstance(response, dict):
        return response
    return json.loads(response.body)


def test_unverified_request_is_explainable_and_cannot_be_priority():
    result = ri.assess_access_request(
        name="Ada Lovelace",
        email="ada@regulated.example",
        use_case="Compliance advisory",
        company="Analytical Engines Ltd",
        country="United Kingdom",
        role="Compliance Director",
    )

    assert result["schema_version"] == "1.0.0"
    assert result["trust_state"] == "submitted_unverified"
    assert result["priority"] == "needs_verification"
    assert result["scores"]["total"] <= 49
    assert result["factors"]
    assert all({"code", "points", "basis", "detail"} <= set(f) for f in result["factors"])
    assert "score is not conversion probability" in result["invariants"]


def test_consumer_email_and_missing_context_are_explicit_gaps():
    result = ri.assess_access_request(
        name="A Visitor",
        email="visitor@gmail.com",
        use_case="ARIA landing page",
    )

    assert set(result["gaps"]) == {
        "work_email_or_verified_identity",
        "specific_use_case",
        "organisation",
        "jurisdiction",
        "role_or_decision_capacity",
    }


def test_new_request_telemetry_contains_no_submitted_pii(monkeypatch):
    emitted = []
    monkeypatch.setattr(ri, "wire_success", lambda **kwargs: emitted.append(kwargs))

    assessment = ri.assess_new_access_request(
        name="Sensitive Person",
        email="private.person@example.org",
        use_case="Compliance advisory",
    )
    ri.record_persisted_access_request(assessment)

    payload = json.dumps(emitted)
    assert emitted
    assert "Sensitive Person" not in payload
    assert "private.person@example.org" not in payload


def test_assessment_alone_does_not_claim_persistence(monkeypatch):
    emitted = []
    monkeypatch.setattr(ri, "wire_success", lambda **kwargs: emitted.append(kwargs))
    ri.assess_new_access_request(
        name="Not Yet Stored",
        email="pending@example.org",
        use_case="Compliance advisory",
    )
    assert emitted == []


def test_assessment_failure_reaches_failure_wire(monkeypatch):
    failures = []
    monkeypatch.setattr(
        ri,
        "assess_access_request",
        lambda **kwargs: (_ for _ in ()).throw(ValueError("private detail")),
    )
    monkeypatch.setattr(ri, "wire_failure", lambda **kwargs: failures.append(kwargs))

    try:
        ri.assess_new_access_request(name="x", email="x@y.z", use_case="x")
    except ValueError:
        pass
    else:
        raise AssertionError("assessment failure must propagate to the honest route error")

    assert failures
    assert failures[0]["gap_type"] == "engine_failure"
    assert "private detail" not in failures[0]["detail"]


def test_capability_real_create_endpoint_persists_assessment_and_repeat_count():
    body = {
        "name": "Katherine Johnson",
        "email": "katherine@nasa.example",
        "use_case": "Government / institutional",
        "company": "NASA",
        "country": "United States",
        "role": "Programme Lead",
    }
    first = _body(_run(A.leads_inbound_create_ep(_req(body))))
    second = _body(_run(A.leads_inbound_create_ep(_req(body))))
    assert first["ok"] is True
    assert first["lead_id"] == second["lead_id"]

    listed = _run(A.leads_inbound_list_ep(limit=500))
    record = next(item for item in listed["leads"] if item["lead_id"] == first["lead_id"])
    assert record["submission_count"] == 2
    assert record["assessment"]["trust_state"] == "submitted_unverified"
    assert record["assessment"]["priority"] == "needs_verification"
    assert record["assessment"]["scores"]["total"] <= 49


def test_store_read_failure_returns_honest_503(monkeypatch):
    successes = []

    async def fail_read(_key):
        raise RuntimeError("store unavailable")

    monkeypatch.setattr(A.rs, "get_json", fail_read)
    monkeypatch.setattr(ri, "record_persisted_access_request", lambda assessment: successes.append(assessment))
    response = _run(A.leads_inbound_create_ep(_req({
        "name": "Unavailable Store",
        "email": "store@example.org",
        "use_case": "Compliance advisory",
    })))
    assert response.status_code == 503
    assert _body(response) == {
        "ok": False,
        "error": "Could not record your details right now. Please try again shortly.",
    }
    assert successes == [], "failed persistence must never emit a success signal"

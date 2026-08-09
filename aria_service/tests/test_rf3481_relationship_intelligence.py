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

    # Pinned deliberately, not derived from the module: a schema change must be
    # a conscious edit here. 1.1.0 → 1.2.0 at R-F3531, which added the trust
    # transitions, trust_is_established and next_action_code.
    # 1.2.0 → 1.3.0 at R-F3633 (5cf46fb5, "make access requests actionable"), which
    # replaced the triage with an owned access-decision workflow. R-F3804 — the pin
    # did exactly its job: it demanded a conscious edit and R-F3633 never made one,
    # so this has been red since. Recording the bump is the edit it asked for.
    assert result["schema_version"] == "1.3.0"
    assert result["trust_state"] == "submitted_unverified"
    assert result["readiness"] == "needs_verification"
    assert "priority" not in result
    assert result["evidence_completeness"] == {
        "supplied": 4,
        "required": 4,
        "is_complete": True,
    }
    assert "scores" not in result
    assert result["factors"]
    assert all({"code", "basis", "detail"} <= set(f) for f in result["factors"])
    assert all("points" not in factor for factor in result["factors"])
    assert "no conversion probability is inferred" in result["invariants"]


def test_consumer_email_and_missing_context_are_explicit_gaps():
    # R-F3804 — `use_case` must be a value the product actually treats as
    # non-specific. This read "ARIA landing page", a string the system never
    # produces: the web tier sends the user's own typed text plus a separate
    # `source: 'landing'` (server.mjs:2925), so the fixture was asserting against an
    # arbitrary sentence. R-F3633 defines the vocabulary explicitly
    # (`_NON_SPECIFIC_USE_CASES = {"", "other", "use case"}`), so the test now uses
    # it and still asserts the intent: a non-specific use case is an EXPLICIT gap,
    # never a credited evidence factor.
    result = ri.assess_access_request(
        name="A Visitor",
        email="visitor@gmail.com",
        use_case="other",
    )

    assert set(result["gaps"]) == {
        "specific_use_case",
        "organisation",
        "jurisdiction",
        "role_or_decision_capacity",
    }
    assert any(
        factor["code"] == "CONSUMER_OR_UNKNOWN_EMAIL_DOMAIN"
        for factor in result["factors"]
    )


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
    assert record["assessment"]["readiness"] == "needs_verification"
    assert record["assessment"]["evidence_completeness"]["is_complete"] is True


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


def test_same_email_with_corrected_name_updates_one_relationship():
    email = "corrected-name@example.org"
    first = _body(_run(A.leads_inbound_create_ep(_req({
        "name": "A. Example",
        "email": email,
        "use_case": "Compliance advisory",
    }))))
    second = _body(_run(A.leads_inbound_create_ep(_req({
        "name": "Alex Example",
        "email": email.upper(),
        "use_case": "Compliance advisory",
    }))))

    assert first["lead_id"] == second["lead_id"]
    listed = _run(A.leads_inbound_list_ep(limit=500))
    matches = [item for item in listed["leads"] if item.get("email", "").lower() == email]
    assert len(matches) == 1
    assert matches[0]["name"] == "Alex Example"
    assert matches[0]["submission_count"] == 2


def test_public_caller_cannot_spoof_attribution_source():
    created = _body(_run(A.leads_inbound_create_ep(_req({
        "name": "Source Spoof",
        "email": "source-spoof@example.org",
        "use_case": "Compliance advisory",
        "source": "trusted_partner",
    }))))
    listed = _run(A.leads_inbound_list_ep(limit=500))
    record = next(item for item in listed["leads"] if item["lead_id"] == created["lead_id"])
    assert record["source"] == "landing"


def test_capability_delete_endpoint_returns_provable_erasure_receipt():
    created = _body(_run(A.leads_inbound_create_ep(_req({
        "name": "Erase Me",
        "email": "erase-me@example.org",
        "use_case": "Compliance advisory",
    }))))
    lead_id = created["lead_id"]

    erased = _run(A.leads_inbound_delete_ep(lead_id))
    assert erased == {
        "ok": True,
        "lead_id": lead_id,
        "erasure_complete": True,
        "record_deleted": True,
        "index_removed": True,
    }
    listed = _run(A.leads_inbound_list_ep(limit=500))
    assert all(item.get("lead_id") != lead_id for item in listed["leads"])


def test_delete_rejects_invalid_id_and_does_not_claim_missing_record_erased():
    invalid = _run(A.leads_inbound_delete_ep("../../account"))
    assert invalid.status_code == 400
    assert _body(invalid)["ok"] is False

    missing = _run(A.leads_inbound_delete_ep("lead_0000000000000000"))
    assert missing.status_code == 404
    assert _body(missing)["ok"] is False


def test_delete_never_claims_erasure_when_strict_readback_finds_record(monkeypatch):
    reads = iter(({"lead_id": "lead_1111111111111111"}, {"lead_id": "lead_1111111111111111"}))
    successes = []

    async def strict_read(_key):
        return next(reads)

    async def reports_deleted(_key):
        return True

    async def reports_index_removed(_key, _member):
        return True

    monkeypatch.setattr(A.rs, "get_json_strict", strict_read)
    monkeypatch.setattr(A.rs, "delete", reports_deleted)
    monkeypatch.setattr(A.rs, "zrem", reports_index_removed)
    monkeypatch.setattr(
        ri,
        "record_erased_access_request",
        lambda **kwargs: successes.append(kwargs),
    )

    response = _run(A.leads_inbound_delete_ep("lead_1111111111111111"))
    assert response.status_code == 503
    assert _body(response)["erasure_complete"] is False
    assert successes == []


def test_failed_erasure_is_wired_not_silent():
    """Review finding on R-F3481 — the failure branch reached no sink.

    The success branch called record_erased_access_request(). The 503 branch
    returned without wiring, and the endpoint's @fail_wire decorator does not
    cover it: that fires on an unhandled EXCEPTION, and a returned JSONResponse
    is not one. §21a defines a path as wired only when BOTH branches emit.

    An erasure that cannot be proven is a data-protection incident — the operator
    may have told a data subject their record was gone while it is still there.
    Silence is the one unacceptable outcome.
    """
    import aria_service.intel.relationship_intelligence as _ri
    captured = {}

    real = _ri.wire_failure
    try:
        _ri.wire_failure = lambda **kw: captured.update(kw)
        _ri.record_failed_erasure(record_deleted=True, still_present=True)
    finally:
        _ri.wire_failure = real

    assert captured, "a failed erasure emitted no brain signal"
    assert captured.get("gap_type") == "data_protection_violation", captured
    assert "erasure could not be proven" in captured.get("detail", "")
    # Non-PII by construction: booleans only, never the subject's identifiers.
    assert "@" not in captured.get("detail", "")


def test_failed_erasure_wiring_is_reachable_from_the_endpoint():
    """Guard the WIRING, not just the helper: the 503 branch must call it."""
    import ast
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1] / "routes" / "aria.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.AsyncFunctionDef)
               and n.name == "leads_inbound_delete_ep"), None)
    assert fn is not None, "leads_inbound_delete_ep not found"
    called = {getattr(c.func, "attr", "") for c in ast.walk(fn) if isinstance(c, ast.Call)}
    assert "record_failed_erasure" in called, (
        "the unprovable-erasure branch does not wire a failure — it is dark"
    )

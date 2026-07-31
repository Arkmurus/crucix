"""R-F3531 — the intake pipeline must be COHERENT end to end.

R-F3481 shipped an honest assessment on top of an incoherent pipeline:

  * it graded four facts, three of which nothing collected and the aria-web
    proxy would have dropped anyway — every lead sat at 1/4 forever;
  * ``trust_state`` was hardcoded at every call site and nothing ever wrote
    EMAIL_VERIFIED / OPERATOR_VERIFIED, so two of three readiness states were
    unreachable code and the triage column was a constant;
  * ``next_best_action`` named two controls the operator surface did not have.

The first three tests here are the ones that matter long-term: they read the
PRODUCER, the PROXY and the SURFACE and fail if any of them stops carrying a
graded fact or stops offering a recommended action. The rest drive the real
endpoints through every state transition.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import re
import types
from datetime import datetime, timedelta, timezone

from aria_service.intel import relationship_intelligence as ri
from aria_service.routes import aria as A


_REPO = pathlib.Path(__file__).resolve().parents[2]


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


def _create(**overrides):
    body = {
        "name": "Grace Hopper",
        "email": f"grace-{overrides.pop('slug', 'base')}@navy.example",
        "use_case": "Government / institutional",
        "company": "US Navy",
        "country": "United States",
        "role": "Rear Admiral",
    }
    body.update(overrides)
    return body, _body(_run(A.leads_inbound_create_ep(_req(body))))


def _fetch(lead_id: str) -> dict:
    listed = _run(A.leads_inbound_list_ep(limit=500))
    return next(item for item in listed["leads"] if item["lead_id"] == lead_id)


# ── The coherence contract ───────────────────────────────────────────────────


def test_every_graded_fact_survives_every_hop_of_the_pipeline():
    """The producer, the proxy and the brain must all carry INTAKE_FIELDS.

    This is the R-F3481 root defect as a test. The landing form collected three
    fields; the assessment graded four; and server.mjs rebuilt the forwarded body
    from a hardcoded list, so even a form that sent everything would have had
    company/country/role dropped in transit — the classic producer→consumer
    no-carrier defect. Any hop that stops carrying a graded fact fails here.
    """
    form = (_REPO / "public" / "index.html").read_text(encoding="utf-8")
    lead_form = form[form.index('id="lead-form"'):form.index("</form>", form.index('id="lead-form"'))]
    submitted = set(re.findall(r'name="([a-z_]+)"', lead_form))

    custom_js = (_REPO / "public" / "pelican" / "assets" / "js" / "custom.js").read_text(encoding="utf-8")
    server = (_REPO / "server.mjs").read_text(encoding="utf-8")
    proxy = server[server.index("app.post('/api/leads'"):server.index("async function _mailLeadVerification")]

    for field in ri.INTAKE_FIELDS:
        assert field in submitted, f"the landing form never collects {field!r}, which the assessment grades"
        assert field in custom_js, f"the landing script never submits {field!r}"
        assert field in proxy, f"the aria-web proxy drops {field!r} before it reaches the brain"


def test_every_recommended_action_is_implemented_on_the_operator_surface():
    """The assessment may not recommend a control the page does not have.

    R-F3481's next_best_action told the operator to "verify email ownership,
    then assign a human owner to review"; leads.html offered one button, Erase.
    """
    page = (_REPO / "public" / "leads.html").read_text(encoding="utf-8")
    for action in ri.OperatorAction:
        assert action.value in page, f"assessment recommends {action.value!r} but the surface never offers it"


def test_every_readiness_state_is_reachable():
    """Two of the three were dead code until the trust transitions existed."""
    unverified = ri.assess_access_request(
        name="A", email="a@corp.example", use_case="Compliance advisory",
        company="C", country="UK", role="Director",
    )
    incomplete = ri.assess_access_request(
        name="A", email="a@corp.example", use_case="Compliance advisory",
        trust_state=ri.TrustState.EMAIL_VERIFIED,
    )
    ready = ri.assess_access_request(
        name="A", email="a@corp.example", use_case="Compliance advisory",
        company="C", country="UK", role="Director",
        trust_state=ri.TrustState.EMAIL_VERIFIED,
    )
    assert unverified["readiness"] == "needs_verification"
    assert incomplete["readiness"] == "incomplete"
    assert ready["readiness"] == "ready_for_review"
    assert {unverified["readiness"], incomplete["readiness"], ready["readiness"]} == {
        state.value for state in ri.IntakeReadiness
    }


def test_complete_evidence_can_never_outrank_missing_identity():
    """The load-bearing invariant: a perfect form is still unverified."""
    perfect_but_unverified = ri.assess_access_request(
        name="Plausible", email="ceo@big.example", use_case="Defence brokerage",
        company="Big Corp", country="United Kingdom", role="CEO",
    )
    assert perfect_but_unverified["evidence_completeness"]["is_complete"] is True
    assert perfect_but_unverified["readiness"] == "needs_verification"
    assert perfect_but_unverified["trust_is_established"] is False


def test_unknown_stored_trust_state_falls_back_to_the_weakest(monkeypatch):
    """A corrupt or legacy value must never be read as verified."""
    for junk in (None, "", "verified", "TRUE", 1, {"x": 1}):
        assert ri.coerce_trust_state(junk) == ri.TrustState.SUBMITTED_UNVERIFIED
    assert ri.coerce_trust_state("email_verified") == ri.TrustState.EMAIL_VERIFIED


# ── The email-ownership control ──────────────────────────────────────────────


def test_capability_intake_issues_a_challenge_and_verification_advances_trust():
    body, created = _create(slug="verify-flow")
    assert created["ok"] is True
    token = created["verification"]["token"]
    assert token

    before = _fetch(created["lead_id"])
    assert before["assessment"]["trust_state"] == "submitted_unverified"
    assert before["assessment"]["readiness"] == "needs_verification"
    assert before["assessment"]["next_action_code"] == "await_email_verification"
    assert before["verification"]["pending"] is True

    verified = _body(_run(A.leads_inbound_verify_ep(_req({
        "lead_id": created["lead_id"], "token": token,
    }))))
    assert verified == {"ok": True, "verified": True, "already_verified": False}

    after = _fetch(created["lead_id"])
    assert after["assessment"]["trust_state"] == "email_verified"
    assert after["assessment"]["trust_is_established"] is True
    assert after["assessment"]["readiness"] == "ready_for_review"
    assert after["assessment"]["next_action_code"] == "assign_owner"
    assert after["verified_by"] == "email_challenge"


def test_capability_a_used_token_cannot_be_replayed():
    body, created = _create(slug="replay")
    token = created["verification"]["token"]
    _run(A.leads_inbound_verify_ep(_req({"lead_id": created["lead_id"], "token": token})))

    # Second use of the SAME link is idempotent for the human (mail clients
    # prefetch, people double-click) but the token itself is spent.
    again = _body(_run(A.leads_inbound_verify_ep(_req({"lead_id": created["lead_id"], "token": token}))))
    assert again["already_verified"] is True

    record = _run(A.rs.get_json_strict(A._LEADS_INBOUND_KEY.format(lead_id=created["lead_id"])))
    assert record["verification"] is None, "the consumed challenge is still on the record"


def test_capability_wrong_token_is_rejected_generically_and_wired(monkeypatch):
    failures = []
    monkeypatch.setattr(ri, "wire_failure", lambda **kw: failures.append(kw))
    body, created = _create(slug="wrong-token")

    response = _run(A.leads_inbound_verify_ep(_req({
        "lead_id": created["lead_id"], "token": "not-the-token",
    })))
    assert response.status_code == 400
    payload = _body(response)
    assert payload["verified"] is False

    unknown = _run(A.leads_inbound_verify_ep(_req({
        "lead_id": "lead_0000000000000000", "token": "anything",
    })))
    # An enumeration oracle would answer these two differently. It must not.
    assert unknown.status_code == response.status_code
    assert _body(unknown)["error"] == payload["error"]

    assert failures, "a rejected verification reached no sink"
    assert {"unknown_lead", "mismatch"} <= {f["detail"].rsplit("=", 1)[-1] for f in failures}

    still_unverified = _fetch(created["lead_id"])
    assert still_unverified["assessment"]["trust_state"] == "submitted_unverified"


def test_expired_challenge_is_neither_pending_nor_acceptable():
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    token, challenge = ri.issue_verification_challenge(now=past - timedelta(days=8))
    assert ri.verification_is_pending(challenge) is False
    ok, reason = ri.check_verification_token(challenge, token)
    assert (ok, reason) == (False, "expired")

    live_token, live = ri.issue_verification_challenge()
    assert ri.verification_is_pending(live) is True
    assert ri.check_verification_token(live, live_token) == (True, "")
    assert ri.check_verification_token(live, live_token + "x")[0] is False
    assert ri.check_verification_token(None, live_token) == (False, "no_challenge")


def test_challenge_material_never_leaves_the_brain():
    body, created = _create(slug="no-leak")
    token = created["verification"]["token"]
    served = json.dumps(_fetch(created["lead_id"]))
    assert token not in served
    assert "token_sha256" not in served, "the digest is served to the operator UI, which has no use for it"
    assert "token" not in json.loads(served)["verification"]


def test_challenge_material_never_leaves_the_brain_via_a_mutation_reply_either():
    """Review finding: the PATCH reply returns the record it just mutated.

    Actions that do not consume the challenge (note, owner, stage) leave the raw
    dict on the in-memory record, so the reply is a second, easily-missed exit
    for the digest — the list view is not the only surface to check.
    """
    body, created = _create(slug="patch-leak")
    token = created["verification"]["token"]
    reply = _body(_run(A.leads_inbound_update_ep(created["lead_id"], _req({
        "action": "add_note", "note": "left a voicemail", "actor": "ops@imaria.io",
    }))))
    serialized = json.dumps(reply)
    assert token not in serialized
    assert "token_sha256" not in serialized
    assert set(reply["lead"]["verification"]) == {"pending", "expires_at"}


def test_erasure_removes_the_live_credential_with_the_record():
    """A challenge stored in its own key would outlive the subject's erasure."""
    body, created = _create(slug="erase-credential")
    lead_id = created["lead_id"]
    assert created["verification"]["token"]
    _run(A.leads_inbound_delete_ep(lead_id))
    assert _run(A.rs.get_json(A._LEADS_INBOUND_KEY.format(lead_id=lead_id))) is None
    replay = _run(A.leads_inbound_verify_ep(_req({
        "lead_id": lead_id, "token": created["verification"]["token"],
    })))
    assert replay.status_code == 400


def test_reverify_replaces_the_old_link_and_only_for_unverified_contacts():
    body, created = _create(slug="reissue")
    first_token = created["verification"]["token"]
    reissued = _body(_run(A.leads_inbound_reverify_ep(created["lead_id"])))
    assert reissued["ok"] is True
    second_token = reissued["verification"]["token"]
    assert second_token != first_token
    assert reissued["email"] == body["email"]

    superseded = _run(A.leads_inbound_verify_ep(_req({
        "lead_id": created["lead_id"], "token": first_token,
    })))
    assert superseded.status_code == 400, "the superseded link still works"

    accepted = _body(_run(A.leads_inbound_verify_ep(_req({
        "lead_id": created["lead_id"], "token": second_token,
    }))))
    assert accepted["verified"] is True

    refused = _run(A.leads_inbound_reverify_ep(created["lead_id"]))
    assert refused.status_code == 400, "a verified contact should not be re-challenged"


# ── The operator controls ────────────────────────────────────────────────────


def test_capability_operator_attestation_requires_an_operator_and_a_basis():
    body, created = _create(slug="attest")
    lead_id = created["lead_id"]

    anonymous = _run(A.leads_inbound_update_ep(lead_id, _req({
        "action": "mark_operator_verified", "note": "spoke to them",
    })))
    assert anonymous.status_code == 400, "an unattributable attestation was accepted"

    baseless = _run(A.leads_inbound_update_ep(lead_id, _req({
        "action": "mark_operator_verified", "actor": "ops@imaria.io",
    })))
    assert baseless.status_code == 400, "an attestation with no stated basis was accepted"

    good = _body(_run(A.leads_inbound_update_ep(lead_id, _req({
        "action": "mark_operator_verified",
        "actor": "ops@imaria.io",
        "note": "Called the switchboard on the published number and confirmed the role.",
    }))))
    assert good["ok"] is True
    record = _fetch(lead_id)
    assert record["assessment"]["trust_state"] == "operator_verified"
    assert record["assessment"]["trust_is_established"] is True
    assert record["verified_by"] == "ops@imaria.io"
    # The basis is retained as an auditable note — this is the record of WHY an
    # unverified contact was granted trust.
    assert any("switchboard" in n["text"] for n in record["notes"])


def test_capability_owner_and_stage_advance_the_workflow_and_reassess():
    body, created = _create(slug="workflow")
    lead_id = created["lead_id"]
    _run(A.leads_inbound_verify_ep(_req({
        "lead_id": lead_id, "token": created["verification"]["token"],
    })))
    assert _fetch(lead_id)["assessment"]["next_action_code"] == "assign_owner"

    assigned = _body(_run(A.leads_inbound_update_ep(lead_id, _req({
        "action": "assign_owner", "actor": "ops@imaria.io",
    }))))
    assert assigned["lead"]["owner"] == "ops@imaria.io"
    # The verdict must move with the state, not lag it.
    assert assigned["lead"]["assessment"]["next_action_code"] == "review_and_decide"

    staged = _body(_run(A.leads_inbound_update_ep(lead_id, _req({
        "action": "set_stage", "stage": "accepted", "actor": "ops@imaria.io",
    }))))
    assert staged["lead"]["lifecycle_stage"] == "ACCEPTED"

    bad_stage = _run(A.leads_inbound_update_ep(lead_id, _req({
        "action": "set_stage", "stage": "WON", "actor": "ops@imaria.io",
    })))
    assert bad_stage.status_code == 400
    assert _fetch(lead_id)["lifecycle_stage"] == "ACCEPTED"


def test_unsupported_action_and_bad_id_are_refused():
    body, created = _create(slug="refusals")
    unsupported = _run(A.leads_inbound_update_ep(created["lead_id"], _req({"action": "delete_everything"})))
    assert unsupported.status_code == 400
    bad_id = _run(A.leads_inbound_update_ep("../../etc/passwd", _req({"action": "add_note", "note": "x"})))
    assert bad_id.status_code == 400
    missing = _run(A.leads_inbound_update_ep("lead_0000000000000000", _req({
        "action": "add_note", "note": "x", "actor": "ops@imaria.io",
    })))
    assert missing.status_code == 404


def test_empty_note_is_refused_and_notes_are_bounded():
    body, created = _create(slug="notes")
    lead_id = created["lead_id"]
    empty = _run(A.leads_inbound_update_ep(lead_id, _req({"action": "add_note", "note": "   "})))
    assert empty.status_code == 400

    for i in range(55):
        _run(A.leads_inbound_update_ep(lead_id, _req({
            "action": "add_note", "note": f"note {i}", "actor": "ops@imaria.io",
        })))
    notes = _fetch(lead_id)["notes"]
    assert len(notes) == 50, "the note list is unbounded"
    assert notes[-1]["text"] == "note 54"


# ── Re-submission must never lose ground ─────────────────────────────────────


def test_resubmission_never_downgrades_trust_or_wipes_supplied_evidence():
    body, created = _create(slug="resubmit")
    lead_id = created["lead_id"]
    _run(A.leads_inbound_verify_ep(_req({
        "lead_id": lead_id, "token": created["verification"]["token"],
    })))

    # The same person fills the form in again, this time leaving the optional
    # context blank. Neither their confirmed identity nor the evidence they
    # already gave may be lost.
    again = _body(_run(A.leads_inbound_create_ep(_req({
        "name": "Grace Hopper", "email": body["email"], "use_case": "Government / institutional",
    }))))
    assert again["lead_id"] == lead_id
    assert again["already_verified"] is True
    assert again["verification"] is None, "a verified contact was re-challenged"

    record = _fetch(lead_id)
    assert record["assessment"]["trust_state"] == "email_verified"
    assert record["company"] == "US Navy"
    assert record["country"] == "United States"
    assert record["role"] == "Rear Admiral"
    assert record["assessment"]["evidence_completeness"]["is_complete"] is True


def test_resubmission_does_not_invalidate_a_link_already_in_the_inbox():
    body, created = _create(slug="double-submit")
    first_token = created["verification"]["token"]
    resubmitted = _body(_run(A.leads_inbound_create_ep(_req(body))))
    assert resubmitted["verification"] is None, "a second submission reissued and broke the live link"

    accepted = _body(_run(A.leads_inbound_verify_ep(_req({
        "lead_id": created["lead_id"], "token": first_token,
    }))))
    assert accepted["verified"] is True


def test_a_stale_link_is_reissued_but_a_fresh_one_is_reused():
    """Resubmission must be neither spammable nor a dead end.

    Reusing forever means a contact whose email went astray is stuck until an
    operator notices; reissuing every time turns the public form into a way to
    mail someone repeatedly. The window separates the two.
    """
    fresh_token, fresh = ri.issue_verification_challenge()
    assert ri.challenge_is_reusable(fresh) is True

    stale_moment = datetime.now(timezone.utc) - timedelta(
        seconds=ri.VERIFICATION_REISSUE_AFTER_SECONDS + 60)
    _, stale = ri.issue_verification_challenge(now=stale_moment)
    assert ri.verification_is_pending(stale) is True, "still inside the 7-day TTL"
    assert ri.challenge_is_reusable(stale) is False, "an old link should be replaced on resubmit"

    # Live path: age the stored challenge, resubmit, and a new link is minted.
    body, created = _create(slug="stale-reissue")
    key = A._LEADS_INBOUND_KEY.format(lead_id=created["lead_id"])
    record = _run(A.rs.get_json_strict(key))
    record["verification"]["issued_at"] = stale_moment.isoformat()
    _run(A.rs.set_json(key, record))

    again = _body(_run(A.leads_inbound_create_ep(_req(body))))
    assert again["verification"] is not None, "a stale link was not reissued"
    assert again["verification"]["token"] != created["verification"]["token"]


def test_served_assessment_is_derived_from_the_record_not_a_stale_snapshot():
    """A stored verdict that disagrees with stored facts must not be served."""
    body, created = _create(slug="stale")
    lead_id = created["lead_id"]
    key = A._LEADS_INBOUND_KEY.format(lead_id=lead_id)
    record = _run(A.rs.get_json_strict(key))
    record["assessment"] = {"readiness": "ready_for_review", "trust_state": "operator_verified"}
    _run(A.rs.set_json(key, record))

    served = _fetch(lead_id)["assessment"]
    assert served["readiness"] == "needs_verification"
    assert served["trust_state"] == "submitted_unverified"
    assert served["schema_version"] == ri.ASSESSMENT_SCHEMA_VERSION


def test_operator_actions_are_wired_to_the_brain(monkeypatch):
    emitted = []
    monkeypatch.setattr(ri, "wire_success", lambda **kw: emitted.append(kw))
    body, created = _create(slug="wiring")
    _run(A.leads_inbound_update_ep(created["lead_id"], _req({
        "action": "add_note", "note": "called them", "actor": "ops@imaria.io",
    })))
    assert any("operator advanced access request" in e.get("summary", "") for e in emitted)
    payload = json.dumps(emitted)
    assert body["email"] not in payload, "operator telemetry carried PII"
    assert "called them" not in payload

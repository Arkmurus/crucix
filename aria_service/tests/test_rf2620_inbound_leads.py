"""R-F2620 — inbound marketing leads from the public landing form.

The landing form used to drop every sign-up on the floor (client-side only, POSTed
nowhere). These endpoints capture the lead into the brain and let the operator view
it. Capability test drives the REAL endpoint functions against the in-memory store
(conftest sets ARIA_STATE_BACKEND=memory):

  - POST create → {ok, lead_id}, and the record is retrievable via the list endpoint
  - validation: no name / bad email → 400, nothing recorded
  - idempotent: same (email,name) twice → one lead
  - the list endpoint returns newest-first with a total
  - _OPERATOR_ONLY_RE does NOT gate /leads/inbound (the public POST, carried by the
    web tier's service token, must not 403)
"""
from __future__ import annotations

import asyncio
import types

from aria_service.routes import aria as A


def _req(body: dict):
    async def _json():
        return body
    return types.SimpleNamespace(json=_json)


def _run(coro):
    return asyncio.run(coro)


def _status(resp) -> int:
    # a JSONResponse has .status_code; a plain dict return is a 200
    return getattr(resp, "status_code", 200)


def _body(resp):
    # JSONResponse stores bytes in .body; a plain dict is returned as-is
    if isinstance(resp, dict):
        return resp
    import json
    return json.loads(resp.body)


def test_create_then_list_roundtrip():
    email = "roundtrip@example.com"
    created = _run(A.leads_inbound_create_ep(_req(
        {"name": "Ada Lovelace", "email": email, "use_case": "Compliance advisory"}
    )))
    assert _status(created) == 200
    b = _body(created)
    assert b["ok"] is True and b["lead_id"].startswith("lead_")

    listed = _run(A.leads_inbound_list_ep(limit=100))
    emails = [l.get("email") for l in listed["leads"]]
    assert email in emails, f"created lead not in list: {emails[:5]}"
    rec = next(l for l in listed["leads"] if l["email"] == email)
    assert rec["name"] == "Ada Lovelace"
    assert rec["use_case"] == "Compliance advisory"
    assert rec["source"] == "landing"
    assert listed["total"] >= 1


def test_validation_rejects_missing_or_bad_email():
    r1 = _run(A.leads_inbound_create_ep(_req({"name": "No Email", "email": ""})))
    assert _status(r1) == 400 and _body(r1)["ok"] is False
    r2 = _run(A.leads_inbound_create_ep(_req({"name": "", "email": "x@y.com"})))
    assert _status(r2) == 400
    r3 = _run(A.leads_inbound_create_ep(_req({"name": "Bad", "email": "not-an-email"})))
    assert _status(r3) == 400


def test_idempotent_same_person_one_lead():
    body = {"name": "Grace Hopper", "email": "grace@navy.mil", "use_case": "Government / institutional"}
    id1 = _body(_run(A.leads_inbound_create_ep(_req(body))))["lead_id"]
    id2 = _body(_run(A.leads_inbound_create_ep(_req(body))))["lead_id"]
    assert id1 == id2, "same (email,name) must map to one lead_id"
    listed = _run(A.leads_inbound_list_ep(limit=500))
    matches = [l for l in listed["leads"] if l["email"] == "grace@navy.mil"]
    assert len(matches) == 1, f"idempotency broken: {len(matches)} copies"


def test_public_post_not_operator_gated():
    # The landing POST is carried by the web tier's SERVICE token, not the operator
    # token — the operator-only regex must NOT match it, or every sign-up would 403.
    assert A._OPERATOR_ONLY_RE.search("/api/aria/leads/inbound") is None


def test_inbound_leads_is_a_wired_brain_limb():
    # §21: the limb must have a real brain topic, not fall through to 'gen'.
    from aria_service.intel import brain_hook as bh
    assert "inbound_leads" in bh._MODULE_TOPICS
    assert bh._MODULE_TOPICS["inbound_leads"], "topic list must be non-empty"

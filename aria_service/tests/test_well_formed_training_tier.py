"""Tests for the 2026-04-26 well_formed training tier (angle b).

Closes the training-pipeline starvation diagnosed 2026-04-25:
substantive responses with proper [CONFIRMED|PROBABLE|ASSESSED] tier
markers were being rejected because their grounded_rate bottomed at 0
(sweep signals are typically 1-source on first appearance, so verifier
never reaches the 2+-source corroboration that flips a claim from
unverified to verified). Result: chat_turns collector returned 0 for
weeks. The well_formed tier accepts these for training when claim count
indicates real tag discipline.
"""
from __future__ import annotations

import asyncio


def _run(coro):
    return asyncio.run(coro)


def test_engine_classifies_substantive_unverified_as_well_formed(monkeypatch):
    """When the response has 3+ tier-marked claims but grounded_rate is
    below 0.40, the audit entry must be tagged `well_formed`, not the
    legacy `unverified` (which the training filter rejects)."""
    from aria_service import aria_engine as ae
    from aria_service.intel import response_verifier as rv
    from aria_service.intel import chat_audit_log as cal

    captured: dict = {}

    async def fake_verify(response_text, tool_context="", session_id=""):
        return {
            "claims_checked": 5,
            "verified": 1,
            "unverified": 4,
            "contradicted": 0,
        }

    async def fake_record_chat(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(rv, "verify_and_tag_response", fake_verify)
    monkeypatch.setattr(cal, "record_chat", fake_record_chat)

    _run(ae._verify_and_record_chat(
        session_id="sess-1",
        user_message="Tell me about Iran sanctions",
        response_text="[CONFIRMED] OFAC has 12 designations. " * 5,
        tool_context=None,
        mastery_overall=0.7,
        mastery_weak_topics=[],
        operating_mode="standard",
    ))

    assert captured.get("verification_status") == "well_formed", (
        f"5 claims, 1/5 grounded (0.20) — should be well_formed, got "
        f"{captured.get('verification_status')!r}"
    )
    assert captured.get("grounded_rate") == 0.2


def test_engine_keeps_unverified_for_thin_claims(monkeypatch):
    """A response with only 1-2 claims and low grounded_rate stays
    `unverified`. well_formed is reserved for substantive responses
    (≥3 claims) — a one-liner with a single tagged claim is too thin
    for training even if the tag is well-placed."""
    from aria_service import aria_engine as ae
    from aria_service.intel import response_verifier as rv
    from aria_service.intel import chat_audit_log as cal

    captured: dict = {}

    async def fake_verify(response_text, tool_context="", session_id=""):
        return {
            "claims_checked": 2,
            "verified": 0,
            "unverified": 2,
            "contradicted": 0,
        }

    async def fake_record_chat(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(rv, "verify_and_tag_response", fake_verify)
    monkeypatch.setattr(cal, "record_chat", fake_record_chat)

    _run(ae._verify_and_record_chat(
        session_id="sess-2",
        user_message="quick question",
        response_text="[ASSESSED] X. [PROBABLE] Y.",
        tool_context=None,
        mastery_overall=0.7,
        mastery_weak_topics=[],
        operating_mode="standard",
    ))

    assert captured.get("verification_status") == "unverified"


def test_engine_keeps_grounded_for_high_grounded_rate(monkeypatch):
    """The `grounded` tier (≥0.40 grounded_rate) is unchanged — an
    actually-verified response stays `grounded`, never demoted to
    `well_formed`."""
    from aria_service import aria_engine as ae
    from aria_service.intel import response_verifier as rv
    from aria_service.intel import chat_audit_log as cal

    captured: dict = {}

    async def fake_verify(response_text, tool_context="", session_id=""):
        return {
            "claims_checked": 5,
            "verified": 4,
            "unverified": 1,
            "contradicted": 0,
        }

    async def fake_record_chat(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(rv, "verify_and_tag_response", fake_verify)
    monkeypatch.setattr(cal, "record_chat", fake_record_chat)

    _run(ae._verify_and_record_chat(
        session_id="sess-3",
        user_message="Tell me about X",
        response_text="[CONFIRMED] high-quality response " * 10,
        tool_context=None,
        mastery_overall=0.7,
        mastery_weak_topics=[],
        operating_mode="standard",
    ))

    assert captured.get("verification_status") == "grounded"
    assert captured.get("grounded_rate") == 0.8


def test_training_filter_accepts_well_formed_entries(monkeypatch):
    """training_export.chat_turns must accept verification_status=well_formed
    in addition to the existing `grounded` tier. Belt-and-braces: also
    verifies the meta.verification_tier field is set."""
    from aria_service.learning import training_export as te
    from aria_service.intel import chat_audit_log as cal

    long_reply = (
        "[ASSESSED] OFAC publishes the SDN list. [PROBABLE] Recent additions "
        "include Iran-linked entities. [ASSESSED] EU sanctions follow a similar "
        "process under CFSP decisions. [CONFIRMED] UNSC resolutions take "
        "primacy where they exist. [PROBABLE] Coordination happens at G7. "
    ) * 4

    async def fake_get_recent(limit: int = 500):
        return [
            {
                "user_message": "Walk me through current sanctions structure",
                "response": long_reply,
                "verification_status": "well_formed",
                "grounded_rate": 0.0,
                "trace_id": "trace-wf-1",
            },
        ]

    monkeypatch.setattr(cal, "get_recent", fake_get_recent)
    out = _run(te._collect_chat_turns(days=7))
    assert len(out) == 1, "well_formed entry must be accepted by chat_turns filter"
    assert out[0]["meta"]["verification_tier"] == "well_formed"
    assert out[0]["meta"]["source"] == "chat_audit"


def test_training_filter_diag_breaks_down_by_tier(monkeypatch):
    """Diagnostic must report kept_by_tier so the dashboard can show
    the grounded/well_formed split, not just a single 'collected' count."""
    from aria_service.learning import training_export as te
    from aria_service.intel import chat_audit_log as cal

    long_reply = "[ASSESSED] Test claim with substantial body. " * 20

    async def fake_get_recent(limit: int = 500):
        return [
            {"user_message": "q1", "response": long_reply,
             "verification_status": "grounded", "grounded_rate": 0.6,
             "trace_id": "g-1"},
            {"user_message": "q2", "response": long_reply,
             "verification_status": "well_formed", "grounded_rate": 0.0,
             "trace_id": "wf-1"},
            {"user_message": "q3", "response": long_reply,
             "verification_status": "well_formed", "grounded_rate": 0.0,
             "trace_id": "wf-2"},
        ]

    monkeypatch.setattr(cal, "get_recent", fake_get_recent)
    out = _run(te._collect_chat_turns(days=7))
    assert len(out) == 3
    diag = te._last_collection_diag.get("chat_turns")
    assert diag is not None
    assert diag.get("kept_by_tier") == {"grounded": 1, "well_formed": 2}


def test_training_filter_still_rejects_unverified(monkeypatch):
    """The legacy `unverified` tier (claims found but neither grounded
    nor well_formed) stays rejected — we don't want low-claim-count
    one-liners polluting the training set."""
    from aria_service.learning import training_export as te
    from aria_service.intel import chat_audit_log as cal

    long_reply = "[ASSESSED] thin claim " * 30

    async def fake_get_recent(limit: int = 500):
        return [
            {
                "user_message": "tiny",
                "response": long_reply,
                "verification_status": "unverified",
                "grounded_rate": 0.0,
                "trace_id": "rej-1",
            },
        ]

    monkeypatch.setattr(cal, "get_recent", fake_get_recent)
    out = _run(te._collect_chat_turns(days=7))
    assert len(out) == 0, "unverified entries must still be rejected"

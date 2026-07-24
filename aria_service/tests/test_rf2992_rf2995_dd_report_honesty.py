"""R-F2992 + R-F2995 — DD report self-consistency & identity-scoring honesty.

Driven by a real Silverbrook run (dd_d49c837b49b7) where the report asserted BOTH
"adverse-media completed (32 items -> AMBER)" AND "adverse-media did NOT complete"
(R-F2779), and scored identity YES "live registry status" while flagging the
registry as unavailable this run (R-F1636). These drive the real functions.
"""
import pytest

from aria_service.intel.dd_orchestrator import (
    _scrub_stale_adverse_incomplete,
    _refresh_persisted_decision_readiness,
)
from aria_service.intel.dd_schema import _dd_decision_readiness

_STALE = (
    "R-F2779: adverse-media screening did NOT complete this run — the ABSENCE of "
    "adverse-media / litigation / corruption findings is NOT a clean bill. A dedicated "
    "adverse-media check is required before relying on this verdict."
)


def _report(ident_gaps, *, adverse=None, risk=None):
    r = {
        "identity": {
            "registration_number": "04300718",
            "registration_status": "active",
            "incorporation_date": "2001-10-08",
            "directors": [{"name": "Justin Howard"}],
            "data_gaps": list(ident_gaps or []),
        },
        "compliance": {},
        "network": {},
        "digital": {},
    }
    if adverse is not None:
        r["adverse_media"] = adverse
    if risk is not None:
        r["risk_classification"] = risk
    return r


# ── R-F2995 — identity scoring honesty ────────────────────────────────────────

def test_rf2995_identity_answered_when_registry_verified():
    r = _dd_decision_readiness(_report([]))
    q = r["questions"]["identity"]
    assert q["answered"] is True
    assert q["status"] == "ANSWERED"


def test_rf2995_identity_unresolved_when_registry_unavailable():
    # Same populated identity fields, but the run flagged R-F1636 registry-unavailable.
    r = _dd_decision_readiness(_report([
        "R-F1636: registry unavailable — identity enriched from OSINT/vault, NOT "
        "registry-verified; manual registry check still required before transacting."
    ]))
    q = r["questions"]["identity"]
    assert q["answered"] is False, "must not score YES on 'live registry status' when registry was unavailable"
    assert "registry was unavailable" in q["blocker"]


# ── R-F2992 — scrub the stale 'did not complete' disclosure ───────────────────

def test_rf2992_scrub_removes_stale_r_f2779_from_all_three_sinks():
    body = {
        "data_gaps_summary": ["financial capacity is unknown", _STALE],
        "digital": {
            "data_gaps": [_STALE, "deep research did not complete within 40s (bounded)"],
            "findings": [
                {
                    "title": "Adverse-media screening incomplete — verdict does NOT certify absence of adverse media",
                    "detail": _STALE,
                    "source": "dd_orchestrator._run_synthesis:R-F2779",
                },
                {"title": "keep me", "source": "network_walker"},
            ],
        },
    }
    removed = _scrub_stale_adverse_incomplete(body)
    assert removed is True
    assert all("R-F2779" not in str(g) for g in body["data_gaps_summary"])
    assert "financial capacity is unknown" in body["data_gaps_summary"]  # non-stale kept
    assert all("R-F2779" not in str(g) for g in body["digital"]["data_gaps"])
    assert any("deep research" in g for g in body["digital"]["data_gaps"])  # non-stale kept
    sources = [str(f.get("source", "")) for f in body["digital"]["findings"]]
    assert all("R-F2779" not in s for s in sources)
    assert any(f.get("title") == "keep me" for f in body["digital"]["findings"])  # non-stale kept


def test_rf2992_scrub_noop_when_nothing_stale():
    body = {"data_gaps_summary": ["x"], "digital": {"data_gaps": [], "findings": []}}
    assert _scrub_stale_adverse_incomplete(body) is False


# ── R-F2992 — next_actions is rebuilt on a non-GREEN escalation ───────────────

def test_rf2992_next_actions_rebuilt_on_amber_escalation():
    # A report escalated to AMBER-LIGHT whose adverse-media question is now ANSWERED,
    # but whose next_actions still carries the stale "did not complete" blocker.
    body = _report(
        [],
        adverse={
            "ok": True,
            "templates_searched": 30,
            "findings_count": 32,
            "search_backends_answered": True,
            "status": "complete",
        },
        risk="AMBER-LIGHT",
    )
    body["next_actions"] = [
        "Resolve decision-readiness blocker: adverse-media screening did not complete"
    ]
    readiness = _refresh_persisted_decision_readiness(body)
    # adverse must be answered now...
    assert readiness["questions"]["adverse_media"]["answered"] is True
    # ...and the stale adverse blocker must be gone from next_actions (rebuilt fresh).
    assert not any(
        "adverse-media screening did not complete" in a for a in body["next_actions"]
    ), body["next_actions"]

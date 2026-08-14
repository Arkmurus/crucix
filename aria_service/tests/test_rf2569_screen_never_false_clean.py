"""R-F2569 + R-F2571 — /compliance/screen + /compliance/sanctions never-false-clean.

R-F2569: a sanctions screen that did NOT actually run must never be certified CLEAR (live
bug: sanctioned "Bank Rossiya" returned CLEAR/PERMITTED while the backend errored).
R-F2571: those endpoints now screen IN-PROCESS via the canonical check_sanctions (the same
authoritative path DD reports use) instead of the broken cross-tier Node hop.

Capability tests drive the REAL endpoints with a mocked check_sanctions, asserting:
  HARD_STOP -> BLOCKED, REVIEW -> REVIEW_REQUIRED, CLEAR -> CLEAR (ran + no match),
  INSUFFICIENT_DATA / source_unavailable -> REVIEW_REQUIRED (never CLEAR).
"""
from __future__ import annotations

import asyncio
import types

import pytest

from aria_service.routes import aria as A
from aria_service.intel.sanctions_canonical import lookup as _lookup


def _req(entity: str = "Bank Rossiya"):
    return A.ComplianceScreenRequest(
        entity_name=entity, product_description="", destination_country="")


def _request():
    st = types.SimpleNamespace(app_url="http://unused.test", internal_token="tok")
    return types.SimpleNamespace(app=types.SimpleNamespace(state=st))


def _mock_check_sanctions(verdict, matches=None, source_unavailable=False, reason=None):
    def _fn(name, *a, **k):
        return {"verdict": verdict, "matches": matches or [],
                "source_unavailable": source_unavailable, "reason": reason}
    return _fn


_HARD_MATCH = [{"formatted_name": "BANK ROSSIYA", "source": "ofac_sdn", "match_score": 1.0}]


def _run_screen(monkeypatch, verdict, **kw):
    monkeypatch.setattr(_lookup, "check_sanctions", _mock_check_sanctions(verdict, **kw))
    return asyncio.run(A.compliance_screen_ep(_req(), _request()))


def _run_sanctions(monkeypatch, verdict, **kw):
    monkeypatch.setattr(_lookup, "check_sanctions", _mock_check_sanctions(verdict, **kw))
    monkeypatch.setattr(A.knowledge, "search_knowledge", lambda n: "")
    return asyncio.run(A.compliance_sanctions_ep(A.SanctionsRequest(name="Bank Rossiya"), _request()))


# ── /compliance/screen ───────────────────────────────────────────────────────
def test_screen_hard_stop_is_blocked(monkeypatch):
    r = _run_screen(monkeypatch, "HARD_STOP", matches=_HARD_MATCH)
    assert r["status"] == "BLOCKED"
    assert r["blocked"] is True


def test_screen_review_is_review_required(monkeypatch):
    r = _run_screen(monkeypatch, "REVIEW", matches=_HARD_MATCH)
    assert r["status"] == "REVIEW_REQUIRED"
    assert r["sanctions"]["matched"] is True


def test_screen_review_without_match_records_does_not_claim_a_match(monkeypatch):
    """R-F4019 capability: cautious review is not a sanctions-list hit."""
    r = _run_screen(monkeypatch, "REVIEW")
    assert r["status"] == "REVIEW_REQUIRED"
    assert r["sanctions"]["risk_level"] == "medium"
    assert r["sanctions"]["matches"] == []
    assert r["sanctions"]["matched"] is False


def test_screen_clear_that_ran_is_clear(monkeypatch):
    r = _run_screen(monkeypatch, "CLEAR")
    assert r["status"] == "CLEAR"


def test_screen_insufficient_is_never_clear(monkeypatch):
    r = _run_screen(monkeypatch, "INSUFFICIENT_DATA", reason="sanctions_store_empty_or_unavailable")
    assert r["status"] != "CLEAR"
    assert r["status"] == "REVIEW_REQUIRED"
    assert r["screened_against"]["Sanctions (entity)"] != "clear"


def test_screen_source_unavailable_is_never_clear(monkeypatch):
    r = _run_screen(monkeypatch, "CLEAR", source_unavailable=True)
    assert r["status"] == "REVIEW_REQUIRED"   # source_unavailable overrides a would-be clear


# ── /compliance/sanctions ────────────────────────────────────────────────────
def test_sanctions_hard_stop_not_clear(monkeypatch):
    r = _run_sanctions(monkeypatch, "HARD_STOP", matches=_HARD_MATCH)
    assert r["clear"] is False
    assert r["match_count"] >= 1


def test_sanctions_clear_that_ran_is_clear(monkeypatch):
    r = _run_sanctions(monkeypatch, "CLEAR")
    assert r["clear"] is True


def test_sanctions_insufficient_not_clear(monkeypatch):
    r = _run_sanctions(monkeypatch, "INSUFFICIENT_DATA", reason="unavailable")
    assert r["clear"] is False
    assert r["screening_unavailable"] is True


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))

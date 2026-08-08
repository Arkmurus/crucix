"""R-F2710 — Capability tests for amendment-triage honesty.

Three verified defects, fixed here:
  #2  MERGE — the R-F566 draft-text-similarity merge collapsed 7 distinct
      A_FALSE_INFO attack families into ONE queue row and summed all their
      failures into a single mis-attributed "68×".
  #4  DRAFT — _draft_amendment returned one static template per category, so
      J1_AUDIT_TRAIL_DENIAL (an audit-trail attack) received the D_CONSTITUTIONAL
      advance-fee-fraud template.
  #3  RECONCILE — a candidate stayed "pending" even after the attack passed a
      later run; nothing reconciled the queue against run outcomes.

These drive the REAL functions (_draft_amendment, _reconcile_recovered_amendments).
"""
from __future__ import annotations

import asyncio

import aria_service.intel.adversarial_challenge as ac
import aria_service.intel.redis_store as rs
from aria_service.intel.adversarial_challenge import (
    _draft_amendment, ATTACK_LIBRARY, AttackCategory,
)

# R-F3785/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import function_source

_QKEY = "aria:adversarial:amendments_queue"


# ── #4 attack-specific drafting ─────────────────────────────────────────────

def test_rf2710_distinct_attacks_get_distinct_drafts():
    """Distinct attacks in the SAME category must not produce byte-identical
    drafts (that identity is what caused the false merge)."""
    a = [x for x in ATTACK_LIBRARY if x.category == AttackCategory.A_FALSE_INFO]
    assert len(a) >= 2, "need >=2 A_FALSE_INFO attacks to prove distinctness"
    drafts = {x.id: _draft_amendment(x) for x in a}
    assert len(set(drafts.values())) == len(drafts), "drafts must be per-attack distinct"
    # every draft names its own attack id
    for aid, d in drafts.items():
        assert aid in d, f"draft for {aid} must name the attack"


def test_rf2710_audit_trail_attack_not_fraud_templated():
    """J1_AUDIT_TRAIL_DENIAL must NOT get advance-fee-fraud text."""
    j = next((x for x in ATTACK_LIBRARY if x.id == "J1_AUDIT_TRAIL_DENIAL"), None)
    if j is None:
        import pytest
        pytest.skip("J1_AUDIT_TRAIL_DENIAL not in library")
    d = _draft_amendment(j)
    assert "J1_AUDIT_TRAIL_DENIAL" in d
    for fraud in ("front-loading", "financial-transfer", "advance-fee", "beneficial-ownership evasion"):
        assert fraud not in d, f"audit-trail attack must not carry fraud text {fraud!r}"


# ── #3 reconciliation ───────────────────────────────────────────────────────

def _seed(queue):
    asyncio.run(rs.set_json(_QKEY, queue, ex=3600))

def _read():
    return asyncio.run(rs.get_json(_QKEY)) or []


def test_rf2710_recovered_attack_dropped_from_queue():
    """An attack that PASSES the latest run has its pending candidate dropped."""
    _seed([
        {"attack_id": "X_PASSES_NOW", "anchor_clauses": [1], "fail_count": 3, "merged_attacks": []},
        {"attack_id": "Y_STILL_FAILS", "anchor_clauses": [2], "fail_count": 1, "merged_attacks": []},
    ])
    results = [
        {"attack_id": "X_PASSES_NOW", "passed": True},
        {"attack_id": "Y_STILL_FAILS", "passed": False, "responses": ["real answer"]},
    ]
    dropped = asyncio.run(ac._reconcile_recovered_amendments(results))
    ids = [q["attack_id"] for q in _read()]
    assert dropped == 1
    assert "X_PASSES_NOW" not in ids, "recovered candidate must be dropped"
    assert "Y_STILL_FAILS" in ids, "still-failing candidate must remain"


def test_rf2710_partial_run_does_not_clear_untested():
    """A run that does NOT include an attack must not clear its candidate."""
    _seed([{"attack_id": "Z_UNTESTED", "anchor_clauses": [3], "fail_count": 5, "merged_attacks": []}])
    results = [{"attack_id": "SOMETHING_ELSE", "passed": True}]
    dropped = asyncio.run(ac._reconcile_recovered_amendments(results))
    assert dropped == 0
    assert "Z_UNTESTED" in [q["attack_id"] for q in _read()]


def test_rf2710_attack_that_both_passed_and_failed_is_not_recovered():
    """If an attack both passed and failed in the run set, it is NOT recovered
    (fail wins — it is still a live regression)."""
    _seed([{"attack_id": "FLAKY", "anchor_clauses": [4], "fail_count": 2, "merged_attacks": []}])
    results = [
        {"attack_id": "FLAKY", "passed": True},
        {"attack_id": "FLAKY", "passed": False, "responses": ["x"]},
    ]
    dropped = asyncio.run(ac._reconcile_recovered_amendments(results))
    assert dropped == 0
    assert "FLAKY" in [q["attack_id"] for q in _read()]


# ── #2 no cross-attack merge in the staging path ────────────────────────────

def test_rf2710_no_crossattack_similarity_merge_in_source():
    """The draft-text-similarity merge across DISTINCT attack_ids is removed —
    the else-branch of the staging insert must create a fresh row, not merge."""
    import inspect
    src = function_source(ac, "_stage_amendments_for_failures")
    assert "_find_similar_existing_entry(proposed_text, queue)" not in src, \
        "cross-attack similarity merge must be gone from the staging path"

"""R-F2025 — adversarial scoring must exclude per-attack empty LLM responses.

The bug: DeepSeek (sole LLM, §18) returns empty strings under load; the
pattern-checker scored an empty response as a FAILURE. A PARTIAL-empty run —
below the 50% `degraded` gate and the 100% `_all_responses_empty` invalid-guard
— collapsed the manipulation-resistance score with phantom failures (live
2026-06-27: 3/23 empty produced a misleading ~24%, all 3 verified empty by ARIA).

This drives the REAL run_weekly scoring path (§3c) with controlled per-attack
results: one CRITICAL attack with empty responses + one attack with a real
response. The empty critical must NOT inflate critical_failures or collapse the
score.

Run: python -m pytest aria_service/tests/test_rf2025_adversarial_empty_exclusion.py -v
"""
from __future__ import annotations

import asyncio

from aria_service.intel import adversarial_challenge as ac


def _two_attack_ids():
    ids = [a.id for a in ac.ATTACK_LIBRARY][:2]
    assert len(ids) == 2, "need >=2 attacks in the library for this test"
    return ids


def test_rf2025_empty_critical_response_excluded_from_scoring(monkeypatch):
    crit_id, ok_id = _two_attack_ids()
    cat0 = list(ac.AttackCategory)[0].value

    async def _fake_run_single(aid, llm_fn=None):
        if aid == crit_id:
            # provider blip — empty/whitespace responses, marked CRITICAL
            return {"attack_id": aid, "category": cat0, "severity": "CRITICAL",
                    "passed": False, "responses": ["", "   "], "degraded": True}
        # real response that resisted the attack
        return {"attack_id": aid, "category": cat0, "severity": "HIGH",
                "passed": True, "responses": ["I will not do that."], "degraded": False}

    async def _noop_stage(_results):
        return None

    monkeypatch.setattr(ac, "run_single", _fake_run_single)
    monkeypatch.setattr(ac, "_stage_amendments_for_failures", _noop_stage)

    summary = asyncio.run(ac.run_weekly(attack_ids=[crit_id, ok_id]))

    # the empty critical attack is excluded from scoring entirely
    assert summary.get("excluded_empty_responses") == 1
    assert summary.get("scored_attacks") == 1
    # THE FIX: the empty critical must NOT count as a critical failure...
    assert summary.get("critical_failures") == 0, \
        "empty-response critical attack must not inflate critical_failures"
    # ...nor collapse the score (old code: base 0.5 - 0.15 penalty = 0.35)
    assert summary.get("overall_score") == 1.0, \
        "score must reflect only the attack that actually got a response"
    # a partial-empty run is NOT marked fully-invalid (that's the 100%-empty guard)
    assert summary.get("invalid") is not True


def test_rf2025_all_empty_still_invalid(monkeypatch):
    """Regression guard: a 100%-empty run must still hit the invalid-guard
    (the per-attack exclusion must not mask a fully-degraded LLM)."""
    crit_id, ok_id = _two_attack_ids()
    cat0 = list(ac.AttackCategory)[0].value

    async def _all_empty(aid, llm_fn=None):
        return {"attack_id": aid, "category": cat0, "severity": "CRITICAL",
                "passed": False, "responses": ["", ""], "degraded": True}

    async def _noop_stage(_results):
        return None

    monkeypatch.setattr(ac, "run_single", _all_empty)
    monkeypatch.setattr(ac, "_stage_amendments_for_failures", _noop_stage)

    summary = asyncio.run(ac.run_weekly(attack_ids=[crit_id, ok_id]))
    assert summary.get("invalid") is True
    assert summary.get("invalid_reason") == "llm_degraded_empty_responses"

"""R-F1746 — capability test: self_quiz feeds regional mastery (gate #2) HONESTLY.

R-F1746 wired self_quiz to call update_regional_mastery so a quiz outcome moves
the Phase A gate-#2 regional heatmap. The CRITICAL property is honesty: the lift
must be gated on the real pass/fail signal (correct=passed_quiz), NOT a blanket
correct=True — otherwise the gate metric inflates without genuine recall.

These tests drive the REAL self_quiz path (the broken/changed path), with real
detect_topics/detect_regions, and assert:
  (1) a PASSED quiz on a TOPIC×region case lifts the cell (correct=True);
  (2) a FAILED quiz records the SAME cell as correct=False (no false credit).
ARIA's R-F1746 shipped without this capability test — Claude added it on cross-check.
"""
from __future__ import annotations

import asyncio

from aria_service.intel import student


def _harness(monkeypatch, *, local_response: str):
    """Wire self_quiz with one compliance×southern_africa case and a local
    reasoning result == `local_response`. Returns the captured
    update_regional_mastery calls list."""
    from aria_service.intel import student, reasoning_library as rl
    from aria_service.intel import reasoning_router

    # Question maps to TOPIC 'compliance' (export-control/sanctions pattern);
    # text mentions South Africa → REGION 'southern_africa'. Both real.
    case = {
        "id": "q1",
        "question": "What export control compliance applies in South Africa?",
        "response": "South Africa NCACC end-use compliance governs defence export control.",
        "intent": "compliance",
    }
    fake_index = [{"id": "q1", "ts_last_used": 0, "confidence_score": 0.1}]

    async def _load_index():
        return list(fake_index)

    def _case_key(cid):
        return f"rl:case:{cid}"

    class _RS:
        async def get_json(self, key):
            return case if key == "rl:case:q1" else None

        async def set_json(self, key, value, *, ex=None, **k):
            return None

    async def _local(question, **kw):
        return {"answered": True, "response": local_response, "source": "local_brain"}

    async def _noop_update_mastery(*a, **k):
        return None

    regional_calls: list[dict] = []

    async def _spy_regional(topics, regions, correct, weight=1.0):
        regional_calls.append({
            "topics": list(topics), "regions": list(regions), "correct": correct,
        })
        return None

    monkeypatch.setattr(rl, "_load_index", _load_index)
    monkeypatch.setattr(rl, "_case_key", _case_key)
    monkeypatch.setattr(rl, "rs", _RS())
    monkeypatch.setattr(student, "rs", _RS())
    monkeypatch.setattr(reasoning_router, "try_local_reasoning", _local)
    monkeypatch.setattr(student, "update_mastery", _noop_update_mastery)
    monkeypatch.setattr(student, "update_regional_mastery", _spy_regional)
    return regional_calls


def test_passed_quiz_lifts_regional_cell(monkeypatch):
    # local answer == stored answer → Jaccard similarity 1.0 → passed_quiz=True.
    stored = "South Africa NCACC end-use compliance governs defence export control."
    calls = _harness(monkeypatch, local_response=stored)

    out = asyncio.run(student.self_quiz(num_questions=1))
    assert out["passed"] == 1, f"expected a pass, got {out}"

    # The regional gate-#2 store was credited for compliance×southern_africa
    # with correct=True (honest positive signal).
    hits = [c for c in calls
            if "compliance" in c["topics"] and "southern_africa" in c["regions"]]
    assert hits, f"update_regional_mastery not called for the cell; calls={calls}"
    assert all(c["correct"] is True for c in hits), (
        f"passed quiz must credit correct=True; got {hits}"
    )


def test_failed_quiz_does_not_falsely_credit(monkeypatch):
    # local answer unrelated → similarity < 0.4 → passed_quiz=False.
    calls = _harness(monkeypatch, local_response="The weather today is mild and sunny.")

    from aria_service.intel import student as _st
    out = asyncio.run(_st.self_quiz(num_questions=1))
    assert out["passed"] == 0, f"expected a fail, got {out}"

    # HONESTY PROPERTY: the cell is recorded with correct=False, never True.
    hits = [c for c in calls
            if "compliance" in c["topics"] and "southern_africa" in c["regions"]]
    assert hits, f"update_regional_mastery not called on fail; calls={calls}"
    assert all(c["correct"] is False for c in hits), (
        f"failed quiz must NOT credit correct=True (gate-2 gaming); got {hits}"
    )

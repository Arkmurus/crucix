"""R-F2390 — eval_runner.run_eval records verification + honesty into the
composite's stores when record=True.

Background (measured live 2026-07-03): after the 2026-07-02 wipe, the composite
collapsed to mastery-only because verification (45%) + honesty (25%) had zero
samples, and /eval/run did NOT populate them — only the live chat path recorded.
R-F2390 wires the SAME recorders (source_verifier.record_verification +
honesty_judge.record_judgment) into run_eval behind record=True, so the frozen
golden set can populate the composite deterministically, offline.

Capability tests drive run_eval() (the real path) and assert:
  - record=False → NO recorder calls (legacy scoring eval writes no signals).
  - record=True → record_verification is called with the REAL verifier verdict
    (grounded, because the framed snippet marker is genuine), honesty is
    recorded when the answer carries confidence tags, and the summary surfaces
    the counts + grounded rate.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _run(coro):
    return asyncio.run(coro)


_ITEMS = [
    {"id": "gold_a", "question": "Is Rosoboronexport on the OFAC SDN list?",
     "expected_answer": "Yes, it is designated."},
    {"id": "gold_b", "question": "Is Entity B on the EU list?",
     "expected_answer": "No."},
]

# A grounded, confidence-tagged answer that cites snippet #1 (which the mocked
# context provides) so the REAL verify_response scores it 'grounded'.
_ANSWER = "Rosoboronexport is designated [CONFIRMED] [from snippet #1]."
_CONTEXT = "Snippet #1: OFAC SDN list includes Rosoboronexport. https://ofac.treasury.gov/x"


def _patched_chat_session():
    """A stand-in for routes.aria._aria_chat_session that honours the R-F2390
    return_context contract: returns (response, context) when asked."""
    async def _fake(question, llm, *, ground_markers=False, return_context=False):
        if return_context:
            return _ANSWER, _CONTEXT
        return _ANSWER
    return _fake


def _common_patches(rec_verify, rec_honesty, judge_resp):
    return [
        patch("aria_service.intel.eval_runner.get_golden_set",
              new=AsyncMock(return_value=list(_ITEMS))),
        patch("aria_service.routes.aria._aria_chat_session",
              new=_patched_chat_session()),
        patch("aria_service.intel.eval_runner._save_run", new=AsyncMock()),
        patch("aria_service.intel.eval_runner.wire_success"),
        patch("aria_service.intel.eval_runner.eval_judge.judge_enabled",
              new=MagicMock(return_value=False)),
        # Recorders are mocked so the test doesn't need a live state_store; we
        # assert they are CALLED with the real verifier output (persistence is
        # verified live in the gate-populate run).
        patch("aria_service.intel.source_verifier.record_verification", new=rec_verify),
        patch("aria_service.intel.honesty_judge.record_judgment", new=rec_honesty),
        patch("aria_service.intel.honesty_judge.judge_response", new=judge_resp),
    ]


def test_record_false_writes_no_signals():
    """A normal scoring eval (record=False) must NOT call the recorders."""
    rec_verify = AsyncMock(return_value={"id": "v"})
    rec_honesty = AsyncMock(return_value={"id": "j"})
    judge_resp = AsyncMock(return_value={"status": "ok", "honesty_score": 1.0, "claims": []})
    patches = _common_patches(rec_verify, rec_honesty, judge_resp)
    for p in patches:
        p.start()
    try:
        result = _run(__import__("aria_service.intel.eval_runner",
                                 fromlist=["run_eval"]).run_eval(MagicMock(), label="norecord"))
    finally:
        for p in patches:
            p.stop()
    assert result["summary"]["total"] == 2
    assert result["summary"].get("recorded") is False
    rec_verify.assert_not_called()
    rec_honesty.assert_not_called()


def test_record_true_records_verification_and_honesty():
    """record=True drives the REAL verifier and persists via the recorders."""
    rec_verify = AsyncMock(return_value={"id": "v"})
    rec_honesty = AsyncMock(return_value={"id": "j"})
    judge_resp = AsyncMock(return_value={
        "status": "ok", "honesty_score": 1.0,
        "claims": ["x"], "supported_count": 1, "verdicts": [],
    })
    patches = _common_patches(rec_verify, rec_honesty, judge_resp)
    for p in patches:
        p.start()
    try:
        result = _run(__import__("aria_service.intel.eval_runner",
                                 fromlist=["run_eval"]).run_eval(
            MagicMock(), label="record", record=True))
    finally:
        for p in patches:
            p.stop()

    # Verification recorded for BOTH entries.
    assert rec_verify.await_count == 2
    # The REAL verify_response ran: the verification dict passed to the recorder
    # is a genuine grounded verdict (not fabricated), proving honest attribution.
    first_call = rec_verify.await_args_list[0]
    verification = first_call.args[0]
    assert verification["verdict"] == "grounded"
    assert verification["grounded_rate"] == 1.0

    # Honesty recorded (answer carried [CONFIRMED] + had source context).
    assert rec_honesty.await_count == 2

    # Summary surfaces the recording stats.
    summ = result["summary"]
    assert summ["recorded"] is True
    assert summ["verification_recorded"] == 2
    assert summ["honesty_recorded"] == 2
    assert summ["grounded_rate_samples"] == 2
    assert summ["mean_grounded_rate"] == 1.0


def test_record_true_representative_limit_samples_across_set():
    """limit>0 takes an evenly-strided representative sample, not the first N."""
    many = [{"id": f"g{i}", "question": f"Q{i}", "expected_answer": f"A{i}"} for i in range(40)]
    rec_verify = AsyncMock(return_value={"id": "v"})
    rec_honesty = AsyncMock(return_value={"id": "j"})
    judge_resp = AsyncMock(return_value={"status": "no_claims", "claims": [], "honesty_score": None})
    patches = [
        patch("aria_service.intel.eval_runner.get_golden_set",
              new=AsyncMock(return_value=many)),
        patch("aria_service.routes.aria._aria_chat_session", new=_patched_chat_session()),
        patch("aria_service.intel.eval_runner._save_run", new=AsyncMock()),
        patch("aria_service.intel.eval_runner.wire_success"),
        patch("aria_service.intel.eval_runner.eval_judge.judge_enabled",
              new=MagicMock(return_value=False)),
        patch("aria_service.intel.source_verifier.record_verification", new=rec_verify),
        patch("aria_service.intel.honesty_judge.record_judgment", new=rec_honesty),
        patch("aria_service.intel.honesty_judge.judge_response", new=judge_resp),
    ]
    for p in patches:
        p.start()
    try:
        result = _run(__import__("aria_service.intel.eval_runner",
                                 fromlist=["run_eval"]).run_eval(
            MagicMock(), label="rep", record=True, limit=8))
    finally:
        for p in patches:
            p.stop()
    assert result["summary"]["total"] == 8
    assert rec_verify.await_count == 8

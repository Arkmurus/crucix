"""R-F1396 — LLM-judge eval scorer (WS-0a of the learning strategy).

The broken path (capability): the 500-Q eval bucketed answers ONLY by
embedding/token cosine ≥0.75 — an answer that is factually CORRECT but
worded differently scored as fail/warn. That artifact produced the
meaningless 21.6% v0.2 number. The fix: eval_judge.judge_answer grades on
factual agreement and run_eval buckets on the judge verdict, with cosine
as fallback only.

Capability tests drive the REAL functions: eval_judge.judge_answer and
eval_runner.run_eval (with external I/O seams stubbed: chat session,
golden-set source, run persistence — the scoring chain itself runs real).
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import eval_judge, eval_runner


# ── Stub LLM (provider.complete contract: (system, user, *, max_tokens, timeout) → .text) ──

class _Result:
    def __init__(self, text: str, model: str = "stub-judge"):
        self.text = text
        self.model = model


class _StubLLM:
    is_configured = True

    def __init__(self, reply: str):
        self.reply = reply
        self.calls = []

    async def complete(self, system_prompt, user_message, *, max_tokens=4096, timeout=60.0):
        self.calls.append({"system": system_prompt, "user": user_message})
        return _Result(self.reply)


class _BrokenLLM:
    is_configured = True

    async def complete(self, *a, **k):
        raise RuntimeError("judge provider down")


# A correct-but-differently-worded pair with near-zero token overlap, so the
# cosine fallback (Jaccard in test env — embedder offline per conftest)
# scores it BELOW the 0.75 pass bar. This is the operator-visible symptom.
_QUESTION = "What is the standard ECJU SITCL processing time?"
_EXPECTED = "Standard SITCL applications currently take 8-12 weeks to process."
_ACTUAL_CORRECT_REWORDED = (
    "Right now you should plan for roughly two to three months before the "
    "licence comes back from the export control unit."
)

_JUDGE_CORRECT_JSON = (
    '{"verdict": "correct", "grounded": false, '
    '"reason": "Two to three months matches 8-12 weeks."}'
)


def test_cosine_alone_fails_the_reworded_answer():
    """Pin the premise: without the judge, this correct answer fails."""
    score = eval_runner._cosine_score(_ACTUAL_CORRECT_REWORDED, _EXPECTED)
    assert score < eval_runner.PASS_THRESHOLD, (
        f"premise broken: cosine={score} — pick a lower-overlap wording"
    )


# ── Unit: verdict parsing ──────────────────────────────────────────────────

def test_parse_clean_json():
    out = eval_judge._parse_verdict(_JUDGE_CORRECT_JSON)
    assert out == {
        "verdict": "correct",
        "grounded": False,
        "reason": "Two to three months matches 8-12 weeks.",
    }


def test_parse_fenced_json():
    fenced = "```json\n{\"verdict\": \"partial\", \"grounded\": true, \"reason\": \"half right\"}\n```"
    out = eval_judge._parse_verdict(fenced)
    assert out["verdict"] == "partial"
    assert out["grounded"] is True


def test_parse_keyword_fallback():
    out = eval_judge._parse_verdict("Verdict: wrong — contradicts the reference.")
    assert out["verdict"] == "wrong"


def test_parse_garbage_returns_none():
    assert eval_judge._parse_verdict("") is None
    assert eval_judge._parse_verdict("the answer is incorrectness itself ok") is None


def test_verdict_score_mapping():
    assert eval_judge.VERDICT_SCORES == {"correct": 1.0, "partial": 0.5, "wrong": 0.0}


# ── Capability: judge_answer drives the real path ──────────────────────────

def test_judge_answer_passes_correct_reworded_answer():
    llm = _StubLLM(_JUDGE_CORRECT_JSON)
    out = asyncio.run(
        eval_judge.judge_answer(llm, _QUESTION, _EXPECTED, _ACTUAL_CORRECT_REWORDED)
    )
    assert out["ok"] is True
    assert out["verdict"] == "correct"
    assert out["score"] == 1.0
    # The judge saw all three texts
    assert _QUESTION in llm.calls[0]["user"]
    assert _EXPECTED in llm.calls[0]["user"]
    assert _ACTUAL_CORRECT_REWORDED in llm.calls[0]["user"]


def test_judge_answer_empty_actual_is_wrong_without_llm_call():
    llm = _StubLLM(_JUDGE_CORRECT_JSON)
    out = asyncio.run(eval_judge.judge_answer(llm, _QUESTION, _EXPECTED, "   "))
    assert out["ok"] is True
    assert out["verdict"] == "wrong"
    assert llm.calls == []  # deterministic — no spend


def test_judge_answer_provider_failure_is_unscored_and_wired(monkeypatch):
    fired = {}
    monkeypatch.setattr(
        eval_judge, "wire_failure",
        lambda module, detail, **k: fired.update({"module": module, "detail": detail}),
    )
    out = asyncio.run(
        eval_judge.judge_answer(_BrokenLLM(), _QUESTION, _EXPECTED, _ACTUAL_CORRECT_REWORDED)
    )
    assert out["ok"] is False
    assert out["verdict"] == "unscored"
    assert fired["module"] == "eval_judge"  # §21a failure branch reaches the brain


def test_judge_answer_unparseable_reply_is_unscored(monkeypatch):
    monkeypatch.setattr(eval_judge, "wire_failure", lambda *a, **k: None)
    out = asyncio.run(
        eval_judge.judge_answer(
            _StubLLM("I think it is fine."), _QUESTION, _EXPECTED, _ACTUAL_CORRECT_REWORDED
        )
    )
    # "fine" contains no verdict keyword → unscored, never a silent bucket
    assert out["ok"] is False
    assert out["verdict"] == "unscored"


# ── Capability: run_eval buckets on the judge (the user-visible fix) ───────

def _run_eval_with(monkeypatch, llm, judge_env="1"):
    """Drive the REAL eval_runner.run_eval with I/O seams stubbed."""
    monkeypatch.setenv("ARIA_EVAL_JUDGE_ENABLED", judge_env)

    entry = {
        "id": "gold_test_1",
        "question": _QUESTION,
        "expected_answer": _EXPECTED,
        "category": "export_control",
    }

    async def _fake_golden_set():
        return [entry]

    async def _noop_save(run):
        pass

    async def _no_prev(limit=10):
        return []

    monkeypatch.setattr(eval_runner, "get_golden_set", _fake_golden_set)
    monkeypatch.setattr(eval_runner, "_save_run", _noop_save)
    monkeypatch.setattr(eval_runner, "get_recent_runs", _no_prev)
    monkeypatch.setattr(eval_runner, "wire_success", lambda *a, **k: None)

    # Skip the R-F1068 framework side-call (separate instrument, network-bound)
    from aria_service.intel import llm_eval_framework as _fw
    async def _fw_skip(*a, **k):
        raise RuntimeError("skipped in test")
    monkeypatch.setattr(_fw, "evaluate", _fw_skip)

    # The chat session returns the correct-but-reworded answer
    from aria_service.routes import aria as _routes_aria
    async def _fake_chat(q, _llm):
        return _ACTUAL_CORRECT_REWORDED
    monkeypatch.setattr(_routes_aria, "_aria_chat_session", _fake_chat)

    return asyncio.run(eval_runner.run_eval(llm, label="rf1396-test"))


def test_run_eval_judge_passes_what_cosine_failed(monkeypatch):
    """THE capability assertion: pre-R-F1396 this entry bucketed fail/warn
    (cosine < 0.75); with the judge it buckets pass."""
    run = _run_eval_with(monkeypatch, _StubLLM(_JUDGE_CORRECT_JSON))
    res = run["results"][0]
    assert res["bucket"] == "pass", f"judge verdict must drive the bucket: {res}"
    assert res["judge"]["verdict"] == "correct"
    assert res["score"] < eval_runner.PASS_THRESHOLD  # cosine recorded, still low
    assert run["summary"]["pass_rate"] == 1.0
    assert run["summary"]["judge_coverage"] == 1.0
    assert run["summary"]["scorer"] == "llm_judge+cosine_fallback"


def test_run_eval_falls_back_to_cosine_when_judge_down(monkeypatch):
    """Judge failure must NOT zero the run — entry falls back to the cosine
    bucket and coverage records 0 (no silent poisoning, R-F197 lesson)."""
    monkeypatch.setattr(eval_judge, "wire_failure", lambda *a, **k: None)
    run = _run_eval_with(monkeypatch, _BrokenLLM())
    res = run["results"][0]
    assert res["judge"] is None
    assert res["bucket"] == eval_runner._bucket(res["score"])  # cosine bucket
    assert run["summary"]["judge_coverage"] == 0
    assert run["summary"]["scorer"] == "cosine"


def test_run_eval_judge_disabled_env_reverts_to_cosine(monkeypatch):
    llm = _StubLLM(_JUDGE_CORRECT_JSON)
    run = _run_eval_with(monkeypatch, llm, judge_env="0")
    assert run["summary"]["judge_coverage"] == 0
    assert llm.calls == []  # kill switch means zero judge spend


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

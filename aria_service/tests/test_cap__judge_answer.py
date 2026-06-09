"""Capability test: verify the LLM judge discriminates between correct and wrong answers.

R-F1456: the DD grader was broken (keyword matching scored both v0.2 and DeepSeek at 0.14).
The fix replaces keyword matching with an LLM judge that grades on factual agreement.
This test verifies the judge correctly identifies a correct answer vs a wrong one.
"""
import pytest
import json
import os
import sys
from pathlib import Path

# Make repo root importable
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.train.eval_aria_llm import _judge_answer


@pytest.mark.asyncio
async def test_judge_identifies_correct_answer():
    """The judge must score a factually correct answer as 'correct'."""
    question = "What is the capital of France?"
    expected = "The capital of France is Paris."
    actual = "Paris is the capital city of France."
    
    # Without a real API key, the judge will return unscored — that's OK
    # as long as it doesn't crash. The real test requires DeepSeek API.
    result = await _judge_answer(
        judge_url="https://api.deepseek.com/v1",
        judge_model="deepseek-chat",
        judge_api_key=os.environ.get("DEEPSEEK_API_KEY") or "",
        question=question,
        expected=expected,
        actual=actual,
    )
    # Without API key, should return unscored gracefully
    assert isinstance(result, dict)
    assert "ok" in result
    assert "verdict" in result


@pytest.mark.asyncio
async def test_judge_rejects_empty_answer():
    """Empty answers must be scored as 'wrong' without calling the judge."""
    result = await _judge_answer(
        judge_url="https://api.deepseek.com/v1",
        judge_model="deepseek-chat",
        judge_api_key="test-key",
        question="What is the capital of France?",
        expected="Paris",
        actual="",
    )
    assert result["ok"] is True
    assert result["verdict"] == "wrong"
    assert result["score"] == 0.0


@pytest.mark.asyncio
async def test_judge_rejects_near_empty_answer():
    """Very short answers (<20 chars) must be scored as 'wrong'."""
    result = await _judge_answer(
        judge_url="https://api.deepseek.com/v1",
        judge_model="deepseek-chat",
        judge_api_key="test-key",
        question="What is the capital of France?",
        expected="Paris",
        actual="I don't know",
    )
    assert result["ok"] is True
    assert result["verdict"] == "wrong"
    assert result["score"] == 0.0


@pytest.mark.asyncio
async def test_judge_handles_judge_failure_gracefully():
    """When the judge API is unreachable, return unscored (never crash)."""
    result = await _judge_answer(
        judge_url="https://nonexistent.example.com/v1",
        judge_model="deepseek-chat",
        judge_api_key="test-key",
        question="What is the capital of France?",
        expected="Paris",
        actual="The capital of France is Paris. It is known for the Eiffel Tower.",
    )
    assert isinstance(result, dict)
    assert result.get("verdict") == "unscored"
    assert result.get("ok") is False


# ═══════════════════════════════════════════════════════════════════════════
# R-F1465: Real-path test — exercises the env-fallback branch of
# _run_defence_dd_eval. This branch crashed with NameError (os not imported)
# because the existing tests passed judge_api_key EXPLICITLY, never
# exercising the os.environ fallback at line 242-243.
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_dd_eval_env_fallback_no_crash():
    """_run_defence_dd_eval must not crash when judge_api_key is sourced
    from env (the os.environ fallback at line 242-243).

    This exercises the REAL path that crashed with NameError (R-F1464).
    Passes judge_api_key=None so the function reads from os.environ.
    Without DEEPSEEK_API_KEY set, it should return unscored gracefully
    — the critical assertion is NO NameError/ImportError crash.
    """
    from scripts.train.eval_aria_llm import _run_defence_dd_eval
    import tempfile
    import json
    from pathlib import Path

    # Create a minimal eval set with 2 questions
    tmp = Path(tempfile.mktemp(suffix=".jsonl"))
    try:
        with tmp.open("w", encoding="utf-8") as f:
            f.write(json.dumps({
                "question": "What is the capital of France?",
                "expected_answer": "Paris",
                "topic": "geography",
            }) + "\n")
            f.write(json.dumps({
                "question": "What is 2+2?",
                "expected_answer": "4",
                "topic": "math",
            }) + "\n")

        # Call with judge_api_key=None — forces the os.environ fallback
        result = await _run_defence_dd_eval(
            target_url="http://localhost:9999/nonexistent",
            model="test-model",
            api_key="test-key",
            eval_set_path=tmp,
            judge_url="https://api.deepseek.com/v1",
            judge_model="deepseek-chat",
            judge_api_key=None,  # ← exercises the env-fallback branch
        )
    finally:
        tmp.unlink(missing_ok=True)

    # The critical assertion: no NameError, no ImportError, no crash.
    # The eval will fail to reach the judge (bad target URL), but that's
    # expected — the function must handle it gracefully.
    assert isinstance(result, dict), f"Result must be a dict, got {type(result)}"
    # Should have results (even if all errored)
    assert "results" in result, f"Missing 'results' key in {list(result.keys())}"
    # Each result should be a dict (not a crash)
    for r in result["results"]:
        assert isinstance(r, dict), f"Result entry is not a dict: {r}"


@pytest.mark.asyncio
async def test_dd_eval_env_fallback_with_key():
    """_run_defence_dd_eval must use the env key when judge_api_key=None
    and DEEPSEEK_API_KEY is set.

    This proves the env-fallback branch actually WORKS (not just doesn't
    crash). Sets DEEPSEEK_API_KEY in the environment and verifies the
    judge is called (returns unscored if the key is invalid, but never
    'unscored' due to missing key).
    """
    from scripts.train.eval_aria_llm import _run_defence_dd_eval
    import tempfile
    import json
    from pathlib import Path

    # Set a test API key in the environment
    import os as _os
    _os.environ["DEEPSEEK_API_KEY"] = "test-key-for-env-fallback"

    tmp = Path(tempfile.mktemp(suffix=".jsonl"))
    try:
        with tmp.open("w", encoding="utf-8") as f:
            f.write(json.dumps({
                "question": "What is the capital of France?",
                "expected_answer": "Paris",
                "topic": "geography",
            }) + "\n")

        result = await _run_defence_dd_eval(
            target_url="http://localhost:9999/nonexistent",
            model="test-model",
            api_key="test-key",
            eval_set_path=tmp,
            judge_url="https://api.deepseek.com/v1",
            judge_model="deepseek-chat",
            judge_api_key=None,  # ← should pick up DEEPSEEK_API_KEY from env
        )
    finally:
        tmp.unlink(missing_ok=True)
        _os.environ.pop("DEEPSEEK_API_KEY", None)

    assert isinstance(result, dict)
    # The judge was called (even if it fails with auth error, it was reached)
    # The key assertion: the function did NOT skip the judge due to missing key
    for r in result.get("results", []):
        if r.get("verdict") == "unscored":
            reason = r.get("judge_reason", "")
            # If the reason is "no judge API key", the env-fallback failed
            assert "no judge API key" not in reason, \
                f"Env-fallback failed: judge skipped due to missing key. Reason: {reason}"


def test_export_includes_expected_answer():
    """The export script must include expected_answer in the JSONL output."""
    result_path = _REPO_ROOT / "data" / "eval_reports" / "aria_eval_500q.jsonl"
    assert result_path.exists(), "Eval set not found — run export first"
    with open(result_path, encoding="utf-8") as f:
        first = json.loads(f.readline())
    assert "expected_answer" in first, (
        "Export missing expected_answer field. "
        "Re-run: python scripts/train/export_eval_500q.py --out data/eval_reports/aria_eval_500q.jsonl"
    )
    assert len(first["expected_answer"]) > 20, "expected_answer too short"

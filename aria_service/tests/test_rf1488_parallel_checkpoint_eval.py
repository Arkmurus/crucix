"""R-F1488 capability test — eval is concurrent + crash-safe (checkpoint/resume).

The old eval ran one question at a time (~5.5h for 500-Q on the shim); the driver's
poll cap then killed a healthy 76%-done run and lost everything (2026-06-10, no
checkpoint). R-F1488: questions run concurrently AND each completion is appended to a
checkpoint so a crash/cap resumes instead of restarting. No paid calls — clients mocked.
"""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts" / "train"))
import eval_aria_llm as ev  # noqa: E402


async def _fake_call_chat(target_url, model, api_key, prompt):
    return (f"answer to {prompt}", 0.5)


async def _fake_judge(judge_url, judge_model, judge_api_key, question, expected, actual):
    # Deterministic: "PASS" in the question => correct, else wrong.
    return {"verdict": "correct" if "PASS" in question else "wrong", "reason": "test"}


def _write_eval_set(tmp: str, n: int = 6) -> Path:
    p = Path(tmp) / "eval.jsonl"
    with p.open("w", encoding="utf-8") as f:
        for i in range(n):
            tag = "PASS" if i % 2 == 0 else "FAIL"
            f.write(json.dumps({"question": f"Q{i} {tag}", "expected_answer": "exp", "topic": "t"}) + "\n")
    return p


@pytest.mark.asyncio
async def test_concurrent_eval_all_processed_format_preserved():
    with tempfile.TemporaryDirectory() as tmp:
        es = _write_eval_set(tmp, 6)
        out = Path(tmp) / "report.json"
        with patch.object(ev, "_call_chat", _fake_call_chat), \
             patch.object(ev, "_judge_answer", _fake_judge):
            rep = await ev._run_defence_dd_eval(
                "http://x/v1", "m", None, es, judge_api_key="k", concurrency=4, out_path=out)
        assert rep["total"] == 6
        assert rep["passed"] == 3                       # 3 PASS questions
        assert rep["accuracy"] == round(3 / 6, 3)
        assert len(rep["results"]) == 6
        assert all("_latency" not in r for r in rep["results"]), "internal latency fields must be stripped"
        assert rep["p50_latency_s"] is not None         # latencies aggregated
        assert (Path(str(out) + ".partial.jsonl")).exists(), "checkpoint must be written"


@pytest.mark.asyncio
async def test_resume_from_checkpoint_skips_done():
    with tempfile.TemporaryDirectory() as tmp:
        es = _write_eval_set(tmp, 6)
        out = Path(tmp) / "report.json"
        ckpt = Path(str(out) + ".partial.jsonl")
        # Pre-seed the checkpoint with idx 0,1,2 already done.
        with ckpt.open("w", encoding="utf-8") as f:
            for i in range(3):
                f.write(json.dumps({"idx": i, "result": {
                    "question": f"pre{i}", "topic": "t", "passed": True, "verdict": "correct"}}) + "\n")
        called = []

        async def _tracking_call(target_url, model, api_key, prompt):
            called.append(prompt)
            return ("a", 0.1)

        with patch.object(ev, "_call_chat", _tracking_call), \
             patch.object(ev, "_judge_answer", _fake_judge):
            rep = await ev._run_defence_dd_eval(
                "http://x/v1", "m", None, es, judge_api_key="k", concurrency=4, out_path=out)
        assert len(called) == 3, f"resume must skip the 3 checkpointed questions; called {len(called)}"
        assert rep["total"] == 6, "final report has all 6 (3 resumed + 3 new)"

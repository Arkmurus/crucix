"""R-F1336 — DPO pair builder: golden chosen vs v0.1 rejected.

Capability: failed eval questions join back to their golden expected
answers (the chosen half) through the seed_id, and the builder emits
valid {prompt, chosen, rejected, meta} pairs from a (mocked) endpoint.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "train"))

import pytest

from build_dpo_from_eval import generate_rejected, load_failed_questions


@pytest.fixture
def eval_artifacts(tmp_path):
    """A miniature eval set + report wired to REAL seed entries so the
    seed_id -> expected_answer join is exercised against the true data."""
    from aria_service.intel.eval_golden_seed import SEED_ENTRIES

    seeds = [e for e in SEED_ENTRIES if len(e.get("expected_answer", "")) > 40][:4]
    assert len(seeds) == 4, "need 4 usable golden entries"

    eval_set = tmp_path / "eval.jsonl"
    with eval_set.open("w", encoding="utf-8") as fh:
        for e in seeds:
            fh.write(json.dumps({
                "question": e["question"],
                "expected_keywords": ["x"],
                "topic": e.get("category", "general"),
                "seed_id": e["seed_id"],
            }) + "\n")

    # Report: first two failed, third passed, fourth errored
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"dd_eval": {"results": [
        {"question": seeds[0]["question"][:200], "passed": False},
        {"question": seeds[1]["question"][:200], "passed": False},
        {"question": seeds[2]["question"][:200], "passed": True},
        {"question": seeds[3]["question"][:200], "error": "timeout"},
    ]}}), encoding="utf-8")
    return eval_set, report, seeds


def test_load_failed_joins_golden_answers(eval_artifacts):
    eval_set, report, seeds = eval_artifacts
    failed = load_failed_questions(report, eval_set)
    # failed + errored rows only (2 + 1), each carrying the golden answer
    assert len(failed) == 3
    by_seed = {f["seed_id"]: f for f in failed}
    assert seeds[0]["seed_id"] in by_seed
    assert seeds[2]["seed_id"] not in by_seed  # passed -> not a pair
    assert by_seed[seeds[0]["seed_id"]]["chosen"] == seeds[0]["expected_answer"]


def test_generate_rejected_pairs_schema(eval_artifacts, monkeypatch):
    eval_set, report, seeds = eval_artifacts
    failed = load_failed_questions(report, eval_set)

    class _Resp:
        def raise_for_status(self): pass
        def json(self):
            return {"choices": [{"message": {"content": "INCOHERENT PMESII RAMBLE"}}]}

    class _Client:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, headers=None, json=None):
            assert url.endswith("/chat/completions")
            assert json["model"] == "aria-llm-v0.1"
            return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    pairs = asyncio.run(generate_rejected(
        failed, target="https://fake/v1", model="aria-llm-v0.1",
        api_key=None, concurrency=2,
    ))
    assert len(pairs) == 3
    for p in pairs:
        assert p["prompt"] and p["chosen"] and p["rejected"]
        assert p["rejected"] == "INCOHERENT PMESII RAMBLE"
        assert p["chosen"] != p["rejected"]
        assert p["meta"]["source"] == "golden_vs_v01_eval_fail"

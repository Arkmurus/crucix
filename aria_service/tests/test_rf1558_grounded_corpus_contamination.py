"""R-F1558 — Stage-1 grounded corpus must NOT be built from the eval set.

build_grounded_corpus.py was documented as consuming aria_eval_500q_openbook.jsonl
(the eval set). Training on those questions then evaluating on them inflates
judge-DD (contamination). This drives the real `main()` and asserts:
  1. fail-closed: input == eval set (via --eval-set) → 0 rows remain → exit 2, no output.
  2. --held-out-split carves a disjoint train/eval; corpus is built from train only
     and shares zero questions with the held-out eval sidecar.
DeepSeek is stubbed so the test makes no network call and costs nothing.
"""
import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest

_MOD_PATH = Path(__file__).resolve().parents[2] / "scripts" / "train" / "build_grounded_corpus.py"


def _load_mod():
    spec = importlib.util.spec_from_file_location("build_grounded_corpus_under_test", _MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _q_of_corpus_row(row: dict) -> str:
    """Extract the question from a corpus row's user message."""
    content = row["messages"][0]["content"]
    return content.split("[QUESTION]\n")[-1].strip()


def test_fail_closed_when_input_is_the_eval_set(tmp_path, monkeypatch):
    mod = _load_mod()
    evalp = tmp_path / "eval.jsonl"
    rows = [{"question": f"Is entity {i} sanctioned?", "context": "ctx", "topic": "t"} for i in range(6)]
    evalp.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    outp = tmp_path / "corpus.jsonl"

    monkeypatch.setenv("DEEPSEEK_API_KEY", "dummy-not-used")
    # input IS the eval set and we declare it as --eval-set → everything excluded
    monkeypatch.setattr(sys, "argv",
                        ["x", "--in", str(evalp), "--out", str(outp), "--eval-set", str(evalp)])
    rc = asyncio.run(mod.main())
    assert rc == 2, "guard must fail-closed (exit 2) when 0 training rows remain"
    assert not outp.exists(), "no corpus file should be written when the guard trips"


def test_held_out_split_produces_disjoint_train_and_eval(tmp_path, monkeypatch):
    mod = _load_mod()

    async def _fake_deepseek(api_key, user, max_tokens=700):
        # grounded + cited → passes _passes_gates for context rows
        return True, "The entity appears on the sanctions list [from OFAC SDN]."

    monkeypatch.setattr(mod, "_deepseek", _fake_deepseek)

    inp = tmp_path / "openbook.jsonl"
    rows = [{"question": f"Question number {i} about entity {i}?", "context": f"ctx {i}", "topic": "t"}
            for i in range(40)]
    inp.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    outp = tmp_path / "corpus.jsonl"

    monkeypatch.setenv("DEEPSEEK_API_KEY", "dummy-not-used")
    monkeypatch.setattr(sys, "argv",
                        ["x", "--in", str(inp), "--out", str(outp), "--held-out-split", "--concurrency", "2"])
    rc = asyncio.run(mod.main())
    assert rc == 0

    sidecar = Path(str(outp) + ".heldout_eval.jsonl")
    assert sidecar.exists(), "held-out eval sidecar must be written"

    held_qs = {json.loads(ln)["question"] for ln in sidecar.read_text(encoding="utf-8").splitlines() if ln.strip()}
    corpus_qs = {_q_of_corpus_row(json.loads(ln)) for ln in outp.read_text(encoding="utf-8").splitlines() if ln.strip()}

    assert held_qs, "sidecar should contain the held-out eval questions"
    assert corpus_qs, "corpus should contain the train questions"
    # The core guarantee: zero overlap between training corpus and the eval the
    # model will be scored on.
    assert corpus_qs.isdisjoint(held_qs), (
        f"CONTAMINATION: {len(corpus_qs & held_qs)} training questions appear in the held-out eval"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

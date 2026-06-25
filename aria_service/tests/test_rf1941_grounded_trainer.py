"""R-F1941 — the grounded-SFT trainer's data pipeline must be correct (the part
verifiable without a GPU). Tests load_corpus (keeps only well-formed chat rows)
and split (deterministic, disjoint, right sizes)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_TRAINER = Path(__file__).resolve().parents[2] / "data" / "training" / "train_aria_llm.py"


def _mod():
    spec = importlib.util.spec_from_file_location("aria_llm_trainer", _TRAINER)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_load_corpus_keeps_only_wellformed(tmp_path):
    m = _mod()
    f = tmp_path / "c.jsonl"
    rows = [
        {"messages": [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}], "label": "grounded"},
        {"messages": [{"role": "user", "content": "q2"}]},                      # no assistant -> drop
        {"messages": [{"role": "assistant", "content": "a"}]},                  # no user -> drop
        {"messages": [{"role": "user", "content": "q3"}, {"role": "assistant", "content": ""}]},  # empty answer -> drop
        {"topic": "x"},                                                          # no messages -> drop
    ]
    f.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    out = m.load_corpus(str(f))
    assert len(out) == 1
    assert out[0]["messages"][1]["content"] == "a"


def test_split_deterministic_disjoint_sized(tmp_path):
    m = _mod()
    data = [{"messages": [{"role": "user", "content": str(i)}, {"role": "assistant", "content": "a"}]} for i in range(100)]
    train, val = m.split(data, eval_frac=0.1, seed=42)
    assert len(train) == 90 and len(val) == 10
    # disjoint
    tset = {d["messages"][0]["content"] for d in train}
    vset = {d["messages"][0]["content"] for d in val}
    assert not (tset & vset)
    assert len(tset | vset) == 100
    # deterministic with same seed
    train2, val2 = m.split(data, eval_frac=0.1, seed=42)
    assert [d["messages"][0]["content"] for d in val] == [d["messages"][0]["content"] for d in val2]


def test_split_no_eval(tmp_path):
    m = _mod()
    data = [{"messages": [{"role": "user", "content": str(i)}, {"role": "assistant", "content": "a"}]} for i in range(20)]
    train, val = m.split(data, eval_frac=0.0, seed=1)
    assert len(train) == 20 and len(val) == 0

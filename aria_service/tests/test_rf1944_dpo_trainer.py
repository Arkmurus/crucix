"""R-F1944 — the DPO trainer's data pipeline must be correct (GPU-free part):
load_pairs keeps only well-formed pairs (prompt + chosen + rejected, chosen !=
rejected); split is deterministic/disjoint."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_T = Path(__file__).resolve().parents[2] / "data" / "training" / "train_aria_dpo.py"


def _mod():
    spec = importlib.util.spec_from_file_location("aria_dpo_trainer", _T)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _pair(p, c, r):
    return {"prompt": [{"role": "user", "content": p}],
            "chosen": [{"role": "assistant", "content": c}],
            "rejected": [{"role": "assistant", "content": r}]}


def test_load_pairs_keeps_only_wellformed(tmp_path):
    m = _mod()
    f = tmp_path / "p.jsonl"
    rows = [
        _pair("q1", "grounded ans", "ungrounded ans"),     # good
        {"prompt": [{"role": "user", "content": "q2"}], "chosen": [], "rejected": [{"role": "assistant", "content": "x"}]},  # empty chosen -> drop
        _pair("q3", "same", "same"),                        # identical -> drop
        {"chosen": [{"role": "assistant", "content": "c"}]},  # no prompt/rejected -> drop
    ]
    f.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    out = m.load_pairs(str(f))
    assert len(out) == 1
    assert out[0]["prompt"][0]["content"] == "q1"


def test_split_deterministic_disjoint(tmp_path):
    m = _mod()
    data = [_pair(f"q{i}", "c", "r") for i in range(100)]
    train, val = m.split(data, 0.1, 42)
    assert len(train) == 90 and len(val) == 10
    tset = {d["prompt"][0]["content"] for d in train}
    vset = {d["prompt"][0]["content"] for d in val}
    assert not (tset & vset) and len(tset | vset) == 100
    # deterministic
    _, val2 = m.split(data, 0.1, 42)
    assert [d["prompt"][0]["content"] for d in val] == [d["prompt"][0]["content"] for d in val2]

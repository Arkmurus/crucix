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


# ── R-F3336: the CONFIG half, which nothing guarded ─────────────────────────
#
# R-F1941 replaced a 74-line distillation toy with this trainer AND fixed the
# config it reads. The very next day, commit 6fe94c43 — message "R-F1966: fix DD
# reports page hanging - state_store write queue backlog" — reverted BOTH
# (-155/+74 on the trainer, and the config back to dataset_format=
# instruction_response with dataset_file missing entirely). An unrelated deploy
# commit swept a stale working-tree copy: the two-agents-one-tree / `git add -A`
# hazard, and it stood for a month.
#
# The trainer half DID have a signal — the three tests above went red — but the
# import blew up on `datasets` (the toy imports it at module scope; this trainer
# is stdlib-only so it can be validated without a GPU), so the failure was filed
# as a missing dev dependency rather than as a reverted pipeline.
#
# The config half had NO test at all. A cycle would have raised KeyError on
# cfg["dataset_file"] at run time, on a paid GPU (CLAUDE.md §24), which is the
# worst place to discover it. These assert the contract the trainer actually
# reads, so the next silent revert fails here instead of on the pod.

import json as _json

_CFG = Path(__file__).resolve().parents[2] / "data" / "training" / "training_config.json"


def _cfg() -> dict:
    return _json.loads(_CFG.read_text(encoding="utf-8"))


def test_rf3336_config_has_every_key_the_trainer_reads():
    """Each key is read by name in train_aria_llm.py; a missing one is a KeyError
    on the pod, not a warning."""
    cfg = _cfg()
    for key in ("dataset_file", "dataset_format", "eval_split", "seed",
                "max_seq_length", "learning_rate", "num_epochs"):
        assert key in cfg, (
            f"training_config.json is missing {key!r} — R-F1941 added it and a "
            f"stale-file sweep removed it once already (6fe94c43)"
        )


def test_rf3336_config_matches_the_grounded_corpus_the_trainer_expects():
    """dataset_format is the one field that silently trains the WRONG objective.

    The pre-R-F1941 value was 'instruction_response' (teacher-response
    distillation, capped at ~0.31 < DeepSeek 0.34). This trainer formats chat
    messages with completion-only loss; run it against the old format and it
    trains something nobody asked for, expensively, and reports success.
    """
    cfg = _cfg()
    assert cfg["dataset_format"] == "chat_messages", (
        f"dataset_format is {cfg['dataset_format']!r} — the distillation format "
        f"R-F1941 moved off; the grounded corpus is chat messages"
    )
    assert cfg.get("completion_only_loss") is True, (
        "completion-only loss is the point: train on the assistant answer, not "
        "on the long retrieved context"
    )


def test_rf3336_the_configured_dataset_actually_exists():
    """A path that resolves to nothing fails after the pod is already billing."""
    path = Path(__file__).resolve().parents[2] / _cfg()["dataset_file"]
    assert path.is_file(), f"configured dataset_file does not exist: {path}"


def test_rf3336_trainer_is_the_grounded_one_not_the_distillation_toy():
    """The revert is detectable by SHAPE, so it cannot come back quietly.

    The toy is a flat 74-line script with no functions; this trainer exposes the
    data pipeline the tests above drive.
    """
    src = _TRAINER.read_text(encoding="utf-8")
    assert "def load_corpus" in src and "def split" in src, (
        "train_aria_llm.py has lost its data pipeline — this is what commit "
        "6fe94c43 did to it; restore from the pre-sweep revision, do not rewrite"
    )
    assert "completion_only" in src or "completion-only" in src, (
        "the completion-only loss path is gone — that is the distillation toy"
    )

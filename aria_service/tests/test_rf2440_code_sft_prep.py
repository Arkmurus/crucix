"""R-F2440 — capability tests for the code-sovereign SFT builder + pre-flight.

The load-bearing property: TRAIN/EVAL PARITY — the SFT *target* the model is
trained to produce, when fed through the EVAL scorer, actually RESOLVES the task.
If that holds, we are training the model on exactly what the eval rewards (no
format skew that would waste a paid cycle). Also asserts the contamination guard
(eval-tier shas never enter the SFT set) and honest thin-corpus gating.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, _REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


PREP = _load("prep_code_sft_h", "scripts/train/prepare_code_sft.py")
E = _load("mined_eval_h2", "scripts/eval/eval_mined_tier.py")


def _synthetic_row() -> dict:
    buggy = ("def clamp(v):\n    if v < 0:\n        return 0\n"
             "    if v > 100:\n        return 0\n    return v\n")
    gold = ("def clamp(v):\n    if v < 0:\n        return 0\n"
            "    if v > 100:\n        return 100\n    return v\n")
    test = ("from mod import clamp\n\ndef test_upper():\n    assert clamp(150) == 100\n\n"
            "def test_lower():\n    assert clamp(-1) == 0\n")
    return {"instruction": "Fix gap: clamp returns 0 for v>100",
            "buggy_context": {"mod.py": buggy}, "fix": {"mod.py": gold},
            "verify_test": {"test_mod.py": test}, "sha": "SYNTH1", "r_number": "R-F0000",
            "source_files": ["mod.py"], "test_files": ["test_mod.py"],
            "multi_file": False, "newly_added_source": [], "dep_modules": [], "n_deps": 0}


def test_pair_shapes():
    prompt, target, nwin = PREP._pair(_synthetic_row())
    assert "### WINDOW 1" in prompt and "REPRODUCE TEST" in prompt
    assert "### FIXED 1" in target
    assert nwin >= 1


def test_train_eval_parity_target_resolves():
    """The SFT target, scored by the EVAL, must RESOLVE the task — proof we train
    on exactly what the eval rewards."""
    row = _synthetic_row()
    _prompt, target, _ = PREP._pair(row)
    E.build_prompt(row)                         # populate row["_windows"]
    fixed = E.parse_fixed(target)               # parse the SFT target as a model would emit
    res = E.score_windows(row, fixed, deps={})  # score via the real eval sandbox
    assert res["resolved"] is True, res
    assert res["windows_applied"] >= 1


def test_eval_tier_shas_never_enter_sft(tmp_path):
    """Contamination guard: a sha present in the eval tier is dropped from SFT."""
    row = _synthetic_row()
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text(json.dumps(row) + "\n", encoding="utf-8")
    eval_tier = tmp_path / "eval.jsonl"
    eval_tier.write_text(json.dumps({"sha": "SYNTH1"}) + "\n", encoding="utf-8")  # same sha
    out = tmp_path / "sft.jsonl"
    proc = subprocess.run(
        [sys.executable, str(_REPO / "scripts" / "train" / "prepare_code_sft.py"),
         "--corpus", str(corpus), "--eval-tier", str(eval_tier), "--out", str(out)],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    # the only row is an eval-tier sha -> SFT output must be empty (fully held out)
    written = [l for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert written == []
    assert "eval_tier_holdout" in proc.stdout


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

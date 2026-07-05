"""R-F2434 — capability tests for the git-fix miner + mined-tier scorer.

Drives the REAL measurement paths (not helpers-in-a-vacuum):
  * the localized-edit SCORER (aligned_windows -> score_windows -> real pytest
    sandbox) on a self-contained synthetic row: a CORRECT localized edit scores
    resolved=True, a no-op/garbage edit scores resolved=False (objective,
    ungameable) — the exact path the DeepSeek baseline runs.
  * the full run_eval loop with a stub model (oracle vs garbage).
  * the miner's pure gates: drop_reason (non-fix filtering), _next_pull_candidates
    (import-closure parsing), and the heavy-import guard.

No git / no network — the synthetic row is stdlib-only so the sandbox runs
anywhere, exactly like the R-F2431 harness.
"""
from __future__ import annotations

import importlib.util
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


E = _load("mined_eval_h", "scripts/eval/eval_mined_tier.py")
MINE = _load("mine_git_fixes_h", "scripts/eval/mine_git_fixes.py")


def _synthetic_row() -> dict:
    buggy = (
        "def clamp(v):\n"
        "    if v < 0:\n"
        "        return 0\n"
        "    if v > 100:\n"
        "        return 0\n"       # bug: should be 100
        "    return v\n"
    )
    gold = (
        "def clamp(v):\n"
        "    if v < 0:\n"
        "        return 0\n"
        "    if v > 100:\n"
        "        return 100\n"     # fixed
        "    return v\n"
    )
    test = (
        "from mod import clamp\n\n"
        "def test_upper():\n"
        "    assert clamp(150) == 100\n\n"
        "def test_lower():\n"
        "    assert clamp(-1) == 0\n"
    )
    return {
        "instruction": "Fix gap: clamp returns 0 for v>100 instead of 100",
        "buggy_context": {"mod.py": buggy}, "fix": {"mod.py": gold},
        "verify_test": {"test_mod.py": test},
        "sha": "SYNTHETIC", "r_number": "R-F0000",
        "source_files": ["mod.py"], "test_files": ["test_mod.py"],
        "multi_file": False, "newly_added_source": [], "dep_modules": [], "n_deps": 0,
    }


def test_aligned_windows_localizes_the_change():
    row = _synthetic_row()
    wins = E.aligned_windows(row["buggy_context"]["mod.py"], row["fix"]["mod.py"], ctx=1)
    assert len(wins) >= 1
    buggy_w, gold_w = wins[0]
    assert "return 0" in buggy_w and "return 100" in gold_w


def test_correct_edit_resolves_noop_does_not():
    """The scorer path the DeepSeek baseline runs: oracle window -> resolved;
    no edit -> unresolved. Objective + ungameable."""
    row = _synthetic_row()
    E.build_prompt(row)  # populates _windows / _oracle
    good = E.score_windows(row, dict(row["_oracle"]), deps={})
    assert good["resolved"] is True and good["windows_applied"] >= 1
    noop = E.score_windows(row, {}, deps={})
    assert noop["resolved"] is False


def test_wrong_edit_does_not_resolve():
    row = _synthetic_row()
    E.build_prompt(row)
    # a garbage replacement that still applies (region found) but is wrong
    idx = next(iter(row["_windows"]))
    _, orig = row["_windows"][idx]
    wrong = {idx: orig}  # returns the buggy region unchanged
    res = E.score_windows(row, wrong, deps={})
    assert res["resolved"] is False


def test_run_eval_with_oracle_and_garbage_stubs():
    row = _synthetic_row()

    def oracle_stub(target_url, model, api_key, prompt, max_tokens, temperature):
        # the row's _oracle is populated by build_prompt inside run_eval
        fixed = "\n".join(
            f"### FIXED {i}\n```python\n{gw}```" for i, gw in row["_oracle"].items())
        return fixed, 0.01, {"prompt_tokens": 5, "completion_tokens": 5}

    def garbage_stub(target_url, model, api_key, prompt, max_tokens, temperature):
        return "no edits here", 0.01, {}

    E._pull_deps = lambda r: {}  # synthetic: no external deps
    E._call_model = oracle_stub
    rep = E.run_eval([row], target_url="stub", model="oracle", api_key=None,
                     max_tokens=100, temperature=0.0)
    assert rep["resolved_rate"] == 1.0

    row2 = _synthetic_row()
    E._call_model = garbage_stub
    rep2 = E.run_eval([row2], target_url="stub", model="garbage", api_key=None,
                      max_tokens=100, temperature=0.0)
    assert rep2["resolved_rate"] == 0.0


def test_miner_drop_reason_filters_nonfixes():
    base = {"r_numbers": ["R-F1"], "tests": ["aria_service/tests/test_x.py"],
            "src": ["aria_service/intel/x.py"]}
    assert MINE.drop_reason(base) is None
    assert MINE.drop_reason({**base, "r_numbers": ["R-F1", "R-F2"]}) == "multi_or_zero_r_subject"
    assert MINE.drop_reason({**base, "r_numbers": []}) == "multi_or_zero_r_subject"
    assert MINE.drop_reason({**base, "tests": []}) == "no_test_file"
    assert MINE.drop_reason({**base, "src": []}) == "no_source_file"


def test_miner_import_closure_parsing():
    assert MINE._next_pull_candidates(
        "E   ModuleNotFoundError: No module named 'aria_service.intel.foo'"
    ) == ["aria_service.intel.foo"]
    cands = MINE._next_pull_candidates(
        "ImportError: cannot import name 'redis_store' from 'aria_service.intel'")
    assert cands == ["aria_service.intel.redis_store", "aria_service.intel"]
    assert MINE._next_pull_candidates("AssertionError: 1 != 2") is None


def test_miner_heavy_guard():
    assert MINE._HEAVY_RE.search("import torch\n")
    assert MINE._HEAVY_RE.search("from chromadb import Client\n")
    assert not MINE._HEAVY_RE.search("import json\nfrom aria_service.intel import x\n")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

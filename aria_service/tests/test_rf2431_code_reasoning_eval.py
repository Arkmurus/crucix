"""R-F2431 — capability test for the code-reasoning eval harness.

Drives the REAL harness paths (not helpers): validate_task, score_fix, and the
full run_eval loop with a STUBBED model (no network). Proves the eval is
OBJECTIVE and ungameable:
  * every held-out task genuinely reproduces (fails on its own bug),
  * the GOLD fix scores resolved=True end-to-end through run_eval,
  * a NO-OP / garbage "fix" scores resolved=False,
  * an invalid task (its bug does not break the test) is flagged, never a free pass.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_MOD_PATH = _REPO / "scripts" / "eval" / "code_reasoning_eval.py"
_SET = _REPO / "data" / "eval" / "code_reasoning_heldout.jsonl"


def _load_harness():
    spec = importlib.util.spec_from_file_location("cre_eval", _MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cre_eval"] = mod
    spec.loader.exec_module(mod)
    return mod


H = _load_harness()
TASKS = H.load_tasks(_SET)


def test_heldout_set_present():
    assert len(TASKS) >= 10
    ids = [t["id"] for t in TASKS]
    assert len(ids) == len(set(ids))


def test_every_task_is_a_genuine_reproduce():
    """The bug must actually break the fail_to_pass node — else it's a free pass."""
    for t in TASKS:
        val = H.validate_task(t)
        assert val["valid"], f"{t['id']} is not a genuine reproduce: {val}"


def test_gold_fix_resolves_and_noop_does_not():
    """Objective gate: gold -> resolved, no-op -> unresolved, for every task."""
    for t in TASKS:
        gold = H.score_fix(t, {t["module_path"]: t["gold"]})
        noop = H.score_fix(t, {})
        assert gold["resolved"] is True, f"{t['id']} gold did not resolve: {gold}"
        assert noop["resolved"] is False, f"{t['id']} no-op falsely resolved: {noop}"


def test_run_eval_with_gold_stub_scores_perfect():
    """Drive the FULL run_eval loop (prompt build -> parse -> score) with a
    stub model that returns each task's gold fix. resolved_rate must be 1.0."""
    def _gold_stub(*, target_url, model, api_key, prompt, max_tokens, temperature):
        # find the task whose module content is embedded in the prompt
        for t in TASKS:
            if t["module_path"] in prompt and t["buggy"].strip().splitlines()[0] in prompt:
                return (f"### FILE: {t['module_path']}\n```python\n{t['gold']}```", 0.01,
                        {"prompt_tokens": 10, "completion_tokens": 20})
        raise AssertionError("no task matched prompt")

    H._call_model = _gold_stub  # type: ignore[assignment]
    rep = H.run_eval(tasks=TASKS, target_url="stub", model="gold-stub",
                     api_key=None, max_tokens=100, temperature=0.0)
    assert rep["resolved_rate"] == 1.0, rep
    assert rep["n_invalid"] == 0


def test_run_eval_with_garbage_stub_scores_zero():
    """A model that returns non-fixing garbage must score 0 resolved — the gate
    cannot be passed without an actual fix."""
    def _garbage_stub(*, target_url, model, api_key, prompt, max_tokens, temperature):
        return ("### FILE: nope.py\n```python\nx = 1\n```", 0.01,
                {"prompt_tokens": 10, "completion_tokens": 5})

    H._call_model = _garbage_stub  # type: ignore[assignment]
    rep = H.run_eval(tasks=TASKS, target_url="stub", model="garbage-stub",
                     api_key=None, max_tokens=100, temperature=0.0)
    assert rep["resolved_rate"] == 0.0, rep


def test_invalid_task_is_flagged_not_free_passed():
    """A task whose bug does NOT break its test is INVALID (would be a free
    pass). run_eval must flag it, not count it as resolved."""
    bogus = {
        "id": "bogus-not-a-reproduce", "source_r": "test", "bug_class": "meta",
        "instruction": "no real bug", "module_path": "ok.py",
        "buggy": "def f():\n    return 1\n", "gold": "def f():\n    return 1\n",
        "test_path": "test_ok.py",
        "test_content": "from ok import f\n\ndef test_ok():\n    assert f() == 1\n",
        "fail_to_pass": "test_ok.py::test_ok", "pass_to_pass": [],
    }
    val = H.validate_task(bogus)
    assert val["valid"] is False  # the "bug" does not break the test

    def _stub(*, target_url, model, api_key, prompt, max_tokens, temperature):
        return ("### FILE: ok.py\n```python\ndef f():\n    return 1\n```", 0.01, {})

    H._call_model = _stub  # type: ignore[assignment]
    rep = H.run_eval(tasks=[bogus], target_url="stub", model="s", api_key=None,
                     max_tokens=100, temperature=0.0)
    assert rep["n_invalid"] == 1
    assert rep["resolved"] == 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

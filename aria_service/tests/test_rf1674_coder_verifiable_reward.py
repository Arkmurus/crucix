"""R-F1674 — coder verifiable-reward corpus: a fix is GOLD (training-grade) only
when the reward is REAL and OBJECTIVE — tests genuinely ran (not the
ARIA_CODER_TESTS_ENABLED=0 no-op pass) AND green AND the function-preservation
guard held. This is the DeepSeek-R1 verifiable-reward signal for coder SFT; the
gate must be ungameable, so it's unit-tested here.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from aria_service.autonomous.self_coder import build_coder_reward_record


class _TR:
    def __init__(self, all_green, passed, failed, attempts=1):
        self.all_green = all_green
        self.passed = passed
        self.failed = failed
        self.attempts = attempts


def _rec(test_result, stage_ok, tests_enabled, reproduce_fail_to_pass=False):
    return build_coder_reward_record(
        instruction="Fix gap: x", approach="do y", code_changes={"a.py": "..."},
        r_number=1, test_result=test_result, stage_ok=stage_ok,
        auto_deployed=False, tests_enabled=tests_enabled,
        reproduce_fail_to_pass=reproduce_fail_to_pass,
    )


def test_gold_only_when_tests_genuinely_ran_green_and_preserved():
    r = _rec(_TR(True, 12, 0), stage_ok=True, tests_enabled=True,
             reproduce_fail_to_pass=True)
    assert r["gold"] is True
    assert r["reward"]["tests_ran"] is True


def test_not_gold_when_reproduce_test_did_not_fail_to_pass():
    """R-F1681: even if all other conditions are met, gold=False when the
    reproduce test did not go FAIL->PASS (e.g. no reproduce test existed
    and the fix relied on generated tests only)."""
    r = _rec(_TR(True, 12, 0), stage_ok=True, tests_enabled=True,
             reproduce_fail_to_pass=False)
    assert r["gold"] is False
    assert r["reward"]["reproduce_fail_to_pass"] is False


def test_not_gold_when_tests_disabled_even_if_all_green():
    # ARIA_CODER_TESTS_ENABLED=0 => run_isolated returns all_green as a FREE PASS.
    # That must NOT count as gold (the keystone anti-gaming check).
    r = _rec(_TR(True, 0, 0), stage_ok=True, tests_enabled=False)
    assert r["gold"] is False
    assert r["reward"]["tests_ran"] is False


def test_not_gold_when_enabled_but_no_cases_ran():
    # enabled but 0 passed + 0 failed = nothing actually executed => not gold
    r = _rec(_TR(True, 0, 0), stage_ok=True, tests_enabled=True)
    assert r["gold"] is False


def test_not_gold_when_tests_failed():
    r = _rec(_TR(False, 3, 2), stage_ok=True, tests_enabled=True)
    assert r["gold"] is False


def test_not_gold_when_function_not_preserved():
    # tests green but the preservation guard rejected it (stage_ok False)
    r = _rec(_TR(True, 12, 0), stage_ok=False, tests_enabled=True)
    assert r["gold"] is False


def test_record_carries_full_reward_provenance():
    r = _rec(_TR(True, 12, 0), stage_ok=True, tests_enabled=True)
    rw = r["reward"]
    assert rw["tests_passed"] == 12 and rw["function_preserved"] is True
    assert r["target_files"] == ["a.py"] and "ts" in r

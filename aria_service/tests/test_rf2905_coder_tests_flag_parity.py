"""R-F2905 — ARIA_CODER_TESTS_ENABLED must be read ONE way.

It was parsed two incompatible ways:
    test_runner.py Gate 1   disabled iff value == "0"   -> tests RAN unless "0"
    self_coder.py:1313      enabled  iff value == "1"   -> recorded NOT-run unless "1"

So for any other truthy spelling ("true"/"yes"), ARIA paid to run the tests and
then filed every result as tests_ran=False. Because gold requires tests_ran, that
forced gold=False by construction.

The live evidence, from data/aria_training/coder_verifiable_gold.jsonl: 17 records
carry tests_passed>0 together with tests_ran=False — a state that is only
reachable if the two readings disagreed.
"""
from __future__ import annotations

import pytest

from aria_service.autonomous.self_coder import build_coder_reward_record
from aria_service.autonomous.test_runner import coder_tests_enabled


class _TestResult:
    """Stand-in for a run where tests genuinely executed and passed."""

    def __init__(self, passed=5, failed=0, all_green=True):
        self.passed = passed
        self.failed = failed
        self.all_green = all_green
        self.attempts = None


class TestSingleReading:
    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " true "])
    def test_truthy_spellings_all_enable(self, monkeypatch, value):
        monkeypatch.setenv("ARIA_CODER_TESTS_ENABLED", value)
        assert coder_tests_enabled() is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
    def test_falsey_spellings_all_disable(self, monkeypatch, value):
        monkeypatch.setenv("ARIA_CODER_TESTS_ENABLED", value)
        assert coder_tests_enabled() is False

    def test_default_is_off(self, monkeypatch):
        """Tests stay opt-in — the fix must not silently enable them."""
        monkeypatch.delenv("ARIA_CODER_TESTS_ENABLED", raising=False)
        assert coder_tests_enabled() is False

    def test_runner_gate_and_record_cannot_disagree(self, monkeypatch):
        """THE bug: the runner's gate and the reward record must agree for every
        value. Before R-F2905 they diverged on everything except "0" and "1"."""
        for value in ("1", "true", "yes", "on", "0", "false", "no", ""):
            monkeypatch.setenv("ARIA_CODER_TESTS_ENABLED", value)
            runner_would_run = coder_tests_enabled()
            rec = build_coder_reward_record(
                instruction="i", approach="a", code_changes={"f.py": "x"},
                r_number=1, test_result=_TestResult(),
                stage_ok=True, auto_deployed=False,
                tests_enabled=coder_tests_enabled(),
                reproduce_fail_to_pass=True,
            )
            assert rec["reward"]["tests_ran"] == runner_would_run, (
                f"value {value!r}: runner would run={runner_would_run} but the "
                f"record says tests_ran={rec['reward']['tests_ran']}"
            )


class TestGoldIsNowReachable:
    def test_passing_work_under_a_truthy_spelling_earns_gold(self, monkeypatch):
        """The user-visible outcome: genuinely passing, reproduce-verified work
        is recorded as gold instead of being discarded."""
        monkeypatch.setenv("ARIA_CODER_TESTS_ENABLED", "true")
        rec = build_coder_reward_record(
            instruction="i", approach="a", code_changes={"f.py": "x"},
            r_number=1, test_result=_TestResult(passed=21),
            stage_ok=True, auto_deployed=False,
            tests_enabled=coder_tests_enabled(),
            reproduce_fail_to_pass=True,
        )
        assert rec["reward"]["tests_ran"] is True
        assert rec["gold"] is True

    def test_the_exact_discarded_shape_is_no_longer_produced(self, monkeypatch):
        """tests_passed>0 with tests_ran=False is the impossible-looking state
        that appears 17 times in the live gold file. It must be unreachable."""
        monkeypatch.setenv("ARIA_CODER_TESTS_ENABLED", "true")
        rec = build_coder_reward_record(
            instruction="i", approach="a", code_changes={"f.py": "x"},
            r_number=1, test_result=_TestResult(passed=5),
            stage_ok=True, auto_deployed=False,
            tests_enabled=coder_tests_enabled(),
            reproduce_fail_to_pass=False,
        )
        r = rec["reward"]
        assert not (r["tests_passed"] > 0 and r["tests_ran"] is False), r

    def test_a_disabled_runner_still_cannot_mint_gold(self, monkeypatch):
        """The honesty property must survive the fix: a no-op green with tests
        off is NOT gold, however many tests the result object claims."""
        monkeypatch.setenv("ARIA_CODER_TESTS_ENABLED", "0")
        rec = build_coder_reward_record(
            instruction="i", approach="a", code_changes={"f.py": "x"},
            r_number=1, test_result=_TestResult(passed=99),
            stage_ok=True, auto_deployed=False,
            tests_enabled=coder_tests_enabled(),
            reproduce_fail_to_pass=True,
        )
        assert rec["reward"]["tests_ran"] is False
        assert rec["gold"] is False

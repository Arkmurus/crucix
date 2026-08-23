"""R-F4260 — gate #7 stops blocking Phase A, and keeps being measured.

Operator instruction, 2026-08-23, verbatim: *"do not remove it but ensure it is
not blocking the whole project."*

Gate #7 counts qualified design-partner conversations. It is the only gate NO
CODE CAN MOVE — every other gate is a live probe ARIA can influence by getting
better, while this one advances solely by the operator talking to customers.
Blocking Phase A on it turned a business-development milestone into an
engineering stop-work order.

The danger in implementing that is the one this repo keeps finding: "stop
blocking" quietly becoming "stop measuring". These tests exist to make that
impossible. The gate keeps its real `pass`, `value` and `evidence`;
`all_pass_including_advisory` preserves the ORIGINAL strict answer verbatim; and
a BLOCKING gate that fails still shuts the door.

Capability tests (§3c): every test drives `compute_phase_gates()`, the ONE
canonical measure both routes render.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import phase_gates


@pytest.fixture(scope="module")
def gates() -> dict:
    """Drive the real canonical measure once."""
    try:
        return asyncio.run(phase_gates.compute_phase_gates())
    except RuntimeError:                       # pragma: no cover - loop already set
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(phase_gates.compute_phase_gates())
        finally:
            loop.close()


class TestGateSevenIsAdvisoryNotRemoved:
    def test_the_gate_still_exists(self, gates):
        assert "gate_7_design_partners" in gates["gates"], (
            "the operator said do not remove it"
        )

    def test_it_is_flagged_advisory(self, gates):
        assert gates["gates"]["gate_7_design_partners"]["advisory"] is True

    def test_it_is_still_measured_and_reported(self, gates):
        """Advisory must not mean invisible — the number is the whole point of
        keeping it."""
        gate = gates["gates"]["gate_7_design_partners"]
        assert "pass" in gate and "value" in gate
        assert gate["evidence"], "an advisory gate still has to say where it read from"
        assert gate["measurable"] is (gate["pass"] is not None)

    def test_every_other_gate_is_still_blocking(self, gates):
        advisory = {k for k, g in gates["gates"].items() if g.get("advisory")}
        assert advisory == {"gate_7_design_partners"}, (
            "exactly one gate was made advisory; anything else is scope creep"
        )


class TestTheSummaryDoesNotLoseTheStrictAnswer:
    def test_the_summary_names_the_advisory_gates(self, gates):
        assert gates["summary"]["advisory_gates"] == ["gate_7_design_partners"]

    def test_the_summary_carries_the_rationale(self, gates):
        rationale = gates["summary"]["advisory_rationale"]
        assert "operator-owned and uncodeable" in rationale
        assert "all_pass_including_advisory" in rationale

    def test_the_strict_all_seven_answer_is_preserved(self, gates):
        """Nobody loses the ability to ask the original question."""
        assert "all_pass_including_advisory" in gates["summary"]

    def test_blocking_totals_are_reported(self, gates):
        summary = gates["summary"]
        assert summary["blocking_total"] == summary["total"] - 1
        assert summary["blocking_passed"] <= summary["blocking_total"]


class TestTheArithmetic:
    """Pure checks on the rule, independent of whatever the live probes say."""

    @staticmethod
    def _summarise(gates: dict) -> tuple[bool, bool]:
        blocking = [g for g in gates.values() if not g.get("advisory")]
        blocking_measurable = [g for g in blocking if g.get("pass") is not None]
        blocking_passed = sum(1 for g in blocking_measurable if g.get("pass"))
        measurable = [g for g in gates.values() if g.get("pass") is not None]
        passed = sum(1 for g in measurable if g.get("pass"))
        return ((len(blocking) - len(blocking_measurable)) == 0
                and blocking_passed == len(blocking),
                (len(gates) - len(measurable)) == 0 and passed == len(gates))

    def test_a_failing_advisory_gate_no_longer_shuts_the_door(self):
        all_pass, strict = self._summarise({
            "a": {"pass": True, "advisory": False},
            "b": {"pass": True, "advisory": False},
            "gate_7_design_partners": {"pass": False, "advisory": True},
        })
        assert all_pass is True
        assert strict is False, "the strict answer must still say no"

    def test_a_failing_BLOCKING_gate_still_shuts_the_door(self):
        """The whole point of keeping six gates blocking."""
        all_pass, _ = self._summarise({
            "a": {"pass": False, "advisory": False},
            "gate_7_design_partners": {"pass": True, "advisory": True},
        })
        assert all_pass is False

    def test_an_unmeasURABLE_blocking_gate_still_shuts_the_door(self):
        """R-F2639: 'could not measure' is never 'measured and passed'."""
        all_pass, _ = self._summarise({
            "a": {"pass": None, "advisory": False},
            "b": {"pass": True, "advisory": False},
        })
        assert all_pass is False

    def test_an_unmeasurable_ADVISORY_gate_does_not_shut_the_door(self):
        all_pass, _ = self._summarise({
            "a": {"pass": True, "advisory": False},
            "gate_7_design_partners": {"pass": None, "advisory": True},
        })
        assert all_pass is True


class TestTheClassificationCannotDrift:
    def test_the_gate_record_derives_advisory_from_the_one_set(self):
        """Two places declaring which gates are advisory would eventually
        disagree — the summary must act on the same set the record is stamped
        from."""
        record = phase_gates._gate(
            7, "gate_7_design_partners", "x", "y", 1, False, "evidence")
        assert record["advisory"] is True
        other = phase_gates._gate(1, "gate_1_composite", "x", "y", 1, True, "evidence")
        assert other["advisory"] is False

"""R-F3482 — `release_gate_eligible` was a claim with no consumer. Now it gates.

Codex's corrected R-F3474 sign-off said the gold cases are "release-gate skeletons", and
that is right. This closes the specific defect underneath that description:

  * Nothing anywhere READ `release_gate_eligible`. The flag was published, reviewed and
    quoted, and no code could act on it — the producer-with-no-carrier shape.
  * Two of the three cases asserted `true` on evidence that cannot support it. Babcock
    declares `fixture_mode: "frozen"` and `release_gate_eligible: true` while its four
    observations carry NO payload — each `source_ref` points at one of my own regression
    test files. A manifest that certifies itself as release-gating, on evidence that is a
    pointer to a test, is the same self-certification defect this repo keeps removing.

WHAT THIS HARNESS ACTUALLY DOES, stated precisely so it is not over-read: for every
observation carrying the four status axes, it constructs a real `EvidenceAssessment`,
derives the verdict with PRODUCTION code, and diffs it against the case's
`expected_findings`. That is a genuine executable gate over the verdict derivation.

WHAT IT DOES NOT DO: it does not run the DD orchestrator, perform retrieval, or render a
report. A case is therefore gate-eligible for the DERIVATION contract only. Full report
replay needs frozen raw observations that do not exist yet, and `tac_mikronglobal` is
already honestly marked blocked for exactly that reason.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from aria_service.intel.dd_evidence_standard import (
    AttemptOutcome,
    ConfigurationState,
    EvidenceAssessment,
    MatchOutcome,
    SourceState,
)

GOLD_DIR = pathlib.Path(__file__).resolve().parents[2] / "data" / "eval" / "dd_gold_v1"

#: The four axes an observation must carry to be EXECUTABLE by this harness.
_AXES = ("configuration_state", "source_state", "attempt_outcome", "match_outcome")


def _cases() -> list[tuple[str, dict]]:
    out = []
    for path in sorted(GOLD_DIR.glob("*.json")):
        out.append((path.name, json.loads(path.read_text(encoding="utf-8"))))
    return out


def _executable_observations(case: dict) -> list[dict]:
    """Observations this harness can actually derive a verdict from."""
    return [
        o for o in (case.get("observations") or [])
        if isinstance(o.get("payload"), dict)
        and all(k in o["payload"] for k in _AXES)
    ]


def _derive(payload: dict) -> str:
    return EvidenceAssessment(
        configuration_state=ConfigurationState(payload["configuration_state"]),
        source_state=SourceState(payload["source_state"]),
        attempt_outcome=AttemptOutcome(payload["attempt_outcome"]),
        match_outcome=MatchOutcome(payload["match_outcome"]),
    ).derive_verdict().value


def test_the_gold_directory_is_present():
    assert GOLD_DIR.is_dir(), f"gold case directory missing: {GOLD_DIR}"
    assert _cases(), "no gold cases found — this harness would certify nothing"


@pytest.mark.parametrize("name,case", _cases(), ids=[n for n, _ in _cases()])
def test_gate_eligibility_is_backed_by_executable_evidence(name, case):
    """THE DEFECT. A case may claim release-gate eligibility ONLY if this harness can
    execute it. Otherwise the flag is a self-certification nothing can honour."""
    if not case.get("release_gate_eligible"):
        return  # honestly not gating — nothing to prove
    executable = _executable_observations(case)
    assert executable, (
        f"{name} claims release_gate_eligible=true but carries NO observation with the "
        f"four status axes, so no gate can execute it. Either freeze real evidence "
        f"(payload with {list(_AXES)}) or set release_gate_eligible=false with a "
        f"gate_blocker saying what is missing.")


@pytest.mark.parametrize("name,case", _cases(), ids=[n for n, _ in _cases()])
def test_frozen_means_frozen(name, case):
    """`frozen: true` must mean the evidence IS here, not that a pointer to a test is.
    A source_ref into the repo is a REFERENCE; it changes whenever that file changes,
    which is the opposite of frozen."""
    for obs in case.get("observations") or []:
        if not obs.get("frozen"):
            continue
        ref = str(obs.get("source_ref") or "")
        if ref.startswith("synthetic://"):
            assert isinstance(obs.get("payload"), dict) and obs["payload"], (
                f"{name}:{obs.get('observation_id')} is frozen with no payload")
            continue
        assert not ref.endswith(".py"), (
            f"{name}:{obs.get('observation_id')} is marked frozen but its source_ref is a "
            f"repo test file ({ref}). That is a reference, not frozen evidence — it "
            f"changes whenever that test changes.")


@pytest.mark.parametrize("name,case", _cases(), ids=[n for n, _ in _cases()])
def test_executable_observations_derive_the_expected_verdict(name, case):
    """THE ACTUAL GATE: production derivation vs the case's expected finding."""
    # Only DERIVATION expectations are comparable here. `expected_state` also carries
    # REPORT-level states (no_finding, open_gap, closed_false_positive) which the verdict
    # function does not produce — my first cut compared those to a derived verdict and
    # reported a false regression on a correct manifest. One observation may legitimately
    # back several findings, so this maps ref -> the derivation expectation only.
    _verdicts = {v.value for v in __import__(
        "aria_service.intel.dd_evidence_standard", fromlist=["EvidenceVerdict"]
    ).EvidenceVerdict}
    expected_by_ref: dict[str, str] = {}
    for finding in case.get("expected_findings") or []:
        state = str(finding.get("expected_state") or "")
        if state not in _verdicts:
            continue
        for ref in finding.get("evidence_refs") or []:
            expected_by_ref[ref] = state

    checked = 0
    for obs in _executable_observations(case):
        oid = obs.get("observation_id")
        expected = expected_by_ref.get(oid)
        if not expected:
            continue
        derived = _derive(obs["payload"])
        assert derived == expected, (
            f"{name}:{oid} — production derived {derived!r} but the gold case expects "
            f"{expected!r}. Either the derivation regressed or the gold expectation is "
            f"wrong; both are findings, neither is noise.")
        checked += 1

    if case.get("release_gate_eligible"):
        assert checked, (
            f"{name} is gate-eligible but no observation was actually checked against an "
            f"expected finding — the gate would pass vacuously")


def test_a_blocked_case_states_why():
    """An honestly-blocked case must say what is missing, or it is just absent."""
    blocked = [(n, c) for n, c in _cases() if not c.get("release_gate_eligible")]
    assert blocked, "no blocked case — expected at least tac_mikronglobal"
    for name, case in blocked:
        reason = str(case.get("gate_blocker") or case.get("fixture_mode") or "")
        assert reason.strip(), f"{name} is not gate-eligible but gives no reason"


def test_the_harness_can_detect_a_wrong_expectation():
    """VERIFY THE INSTRUMENT. If the derivation and the expectation could never disagree,
    this gate certifies everything."""
    stale = {
        "configuration_state": "configured",
        "source_state": "stale",
        "attempt_outcome": "succeeded",
        "match_outcome": "no_match",
    }
    assert _derive(stale) == "degraded", (
        "stale evidence must not derive a completed verdict")
    assert _derive(stale) != "completed_no_match", (
        "the harness cannot distinguish degraded from completed — it would pass a "
        "false clean")

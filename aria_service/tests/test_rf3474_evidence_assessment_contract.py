"""R-F3474 — executable orthogonal DD evidence status contract.

The contract is a truth table, not prose.  These tests enumerate every possible
axis combination, prove that each is either rejected or appears exactly once in
the published derivation table, and pin the stale-sanctions false-clean case.
"""
from __future__ import annotations

from itertools import product

import pytest

from aria_service.intel.dd_evidence_standard import (
    AttemptOutcome,
    ConfigurationState,
    EvidenceAssessment,
    EvidenceContractError,
    EvidenceVerdict,
    MatchOutcome,
    SourceState,
    VERDICT_FUNCTION_VERSION,
    describe_standard,
    evidence_assessment_matrix,
)


def _assessment(
    configuration: ConfigurationState = ConfigurationState.CONFIGURED,
    source: SourceState = SourceState.CURRENT,
    attempt: AttemptOutcome = AttemptOutcome.SUCCEEDED,
    match: MatchOutcome = MatchOutcome.NO_MATCH,
) -> EvidenceAssessment:
    return EvidenceAssessment(configuration, source, attempt, match)


def test_complete_cartesian_product_is_mapped_or_rejected() -> None:
    """No undocumented combination may fall through to an invented verdict."""
    published = evidence_assessment_matrix()
    published_keys = {
        (
            row["configuration_state"],
            row["source_state"],
            row["attempt_outcome"],
            row["match_outcome"],
        )
        for row in published
    }
    assert len(published_keys) == len(published), "derivation rows must be unique"

    valid = set()
    invalid = set()
    for configuration, source, attempt, match in product(
        ConfigurationState, SourceState, AttemptOutcome, MatchOutcome
    ):
        candidate = EvidenceAssessment(configuration, source, attempt, match)
        key = (configuration.value, source.value, attempt.value, match.value)
        try:
            candidate.validate()
        except EvidenceContractError:
            invalid.add(key)
        else:
            valid.add(key)
            assert candidate.derive_verdict().value == next(
                row["verdict"] for row in published if (
                    row["configuration_state"],
                    row["source_state"],
                    row["attempt_outcome"],
                    row["match_outcome"],
                ) == key
            )

    all_combinations = (
        len(ConfigurationState)
        * len(SourceState)
        * len(AttemptOutcome)
        * len(MatchOutcome)
    )
    assert len(valid) + len(invalid) == all_combinations
    assert published_keys == valid


@pytest.mark.parametrize(
    ("candidate", "error_fragment"),
    [
        (
            _assessment(
                attempt=AttemptOutcome.NOT_ATTEMPTED,
                match=MatchOutcome.MATCH,
            ),
            "not_attempted requires match_outcome=not_evaluated",
        ),
        (
            _assessment(
                attempt=AttemptOutcome.TIMED_OUT,
                match=MatchOutcome.NO_MATCH,
            ),
            "timed_out requires match_outcome=not_evaluated",
        ),
        (
            _assessment(
                source=SourceState.UNAVAILABLE,
                attempt=AttemptOutcome.SUCCEEDED,
            ),
            "source_state=unavailable requires a failed attempt outcome",
        ),
        (
            EvidenceAssessment(
                ConfigurationState.NOT_CONFIGURED,
                SourceState.CURRENT,
                AttemptOutcome.NOT_ATTEMPTED,
                MatchOutcome.NOT_EVALUATED,
            ),
            "not_configured requires source_state=unknown",
        ),
    ],
)
def test_validation_matrix_rejects_illegal_states(
    candidate: EvidenceAssessment,
    error_fragment: str,
) -> None:
    with pytest.raises(EvidenceContractError, match=error_fragment):
        candidate.validate()


def test_stale_sanctions_no_match_is_degraded_not_clean() -> None:
    """Named regression: a stale list that returned no match is not clearance."""
    sanctions = _assessment(
        source=SourceState.STALE,
        attempt=AttemptOutcome.SUCCEEDED,
        match=MatchOutcome.NO_MATCH,
    )
    assert sanctions.derive_verdict() == EvidenceVerdict.DEGRADED
    assert sanctions.derive_verdict() != EvidenceVerdict.COMPLETED_NO_MATCH


def test_current_successful_match_outcomes_remain_distinct() -> None:
    assert _assessment(match=MatchOutcome.MATCH).derive_verdict() \
        == EvidenceVerdict.COMPLETED_MATCH
    assert _assessment(match=MatchOutcome.NO_MATCH).derive_verdict() \
        == EvidenceVerdict.COMPLETED_NO_MATCH
    assert _assessment(match=MatchOutcome.AMBIGUOUS).derive_verdict() \
        == EvidenceVerdict.COMPLETED_PARTIAL
    assert _assessment(match=MatchOutcome.UNRESOLVED).derive_verdict() \
        == EvidenceVerdict.COMPLETED_PARTIAL


def test_ambiguous_and_unresolved_have_distinct_machine_states() -> None:
    ambiguous = _assessment(match=MatchOutcome.AMBIGUOUS)
    unresolved = _assessment(match=MatchOutcome.UNRESOLVED)
    assert ambiguous.match_outcome != unresolved.match_outcome
    assert ambiguous.derive_verdict() == unresolved.derive_verdict()


def test_non_configured_governance_states_derive_without_attempting() -> None:
    for configuration, expected in (
        (ConfigurationState.NOT_CONFIGURED, EvidenceVerdict.BLOCKED),
        (ConfigurationState.ACCESS_BASIS_MISSING, EvidenceVerdict.BLOCKED),
        (ConfigurationState.DISABLED, EvidenceVerdict.NOT_RUN),
        (ConfigurationState.NOT_APPLICABLE, EvidenceVerdict.NOT_APPLICABLE),
    ):
        assessment = EvidenceAssessment(
            configuration,
            SourceState.UNKNOWN,
            AttemptOutcome.NOT_ATTEMPTED,
            MatchOutcome.NOT_EVALUATED,
        )
        assert assessment.derive_verdict() == expected


def test_historical_verdict_function_version_fails_closed() -> None:
    candidate = EvidenceAssessment(
        ConfigurationState.CONFIGURED,
        SourceState.CURRENT,
        AttemptOutcome.SUCCEEDED,
        MatchOutcome.NO_MATCH,
        verdict_fn_version="0.9.0",
    )
    with pytest.raises(EvidenceContractError, match="verdict_fn_version"):
        candidate.derive_verdict()


def test_machine_readable_standard_publishes_the_contract_version() -> None:
    contract = describe_standard()["assessment_contract"]
    assert contract["verdict_fn_version"] == VERDICT_FUNCTION_VERSION
    assert contract["valid_combinations"] == len(evidence_assessment_matrix())

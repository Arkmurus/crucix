"""R-F3480 — the evidence contract DECLARES ten invariants. Which are enforced?

`describe_standard()["invariants"]` publishes ten sentences. A declared invariant that
nothing checks is the producer-with-no-carrier shape this codebase keeps closing: it reads
as a guarantee, it is quoted in reviews and specs, and it is enforced by nobody.

Codex's corrected R-F3474 sign-off already established that the CONTRACT is sound but
dormant in production. This closes the narrower question it did not ask: **for each
published sentence, is there code that makes it true?** Every invariant below is exercised
against the real classes, so the manifest can no longer drift away from the behaviour.

TWO STRUCTURAL FACTS this pins, both true today and neither previously asserted:

  * Validation lives on the DESERIALISATION path (`EvidenceRecord.from_mapping`). A
    directly-constructed `EvidenceRecord(...)` is a frozen dataclass and bypasses every
    field invariant. That is acceptable while nothing in production builds records — and
    it becomes a live hole the moment something does. Pinned here so wiring work
    (task: R-F3474 integration) has to confront it rather than inherit it.

  * Historical version replay is NOT implemented. v1.0.0 rejects any other version and
    there is no version→function registry, so the published invariant "historical reports
    pin the verdict function version used at issue time" is HALF true: the version is
    recorded, and it cannot be replayed. Named explicitly below so shipping v2 without a
    registry fails here instead of silently re-deriving old reports under new policy.
"""
from __future__ import annotations

import uuid

import pytest

from aria_service.intel.dd_evidence_standard import (
    AttemptOutcome,
    ConfigurationState,
    EvidenceAssessment,
    EvidenceContractError,
    EvidenceRecord,
    EvidenceVerdict,
    MatchOutcome,
    SnapshotPolicy,
    SourceState,
    VERDICT_FUNCTION_VERSION,
    describe_standard,
)


def _ok_record() -> dict:
    """A minimal record that PASSES from_mapping, so each test can break one thing."""
    return {
        "evidence_id": str(uuid.uuid4()),
        "tenant_id": "t1",
        "case_id": str(uuid.uuid4()),
        "case_scope_version": 1,
        "subject_entity_id": str(uuid.uuid4()),
        "source_attempt_id": str(uuid.uuid4()),
        "source_id": "companies_house",
        "source_authority": "primary_official",
        "retrieval_outcome": "success",
        "retrieved_at": "2026-07-30T12:00:00+00:00",
        # SHA-256 hex digests, both REQUIRED: the fingerprint always, and content_hash
        # whenever the outcome is success ("answered source outcomes require a SHA-256
        # content hash"). My first fixture used placeholders and every test below passed
        # for the WRONG reason — rejecting the fixture rather than the property.
        "request_fingerprint": "a" * 64,
        "content_hash": "b" * 64,
        "licence_policy_id": "lp",
        "access_method": "api",
        "adapter_version": "1",
        "parser_version": "1",
    }


def test_the_baseline_record_is_actually_valid():
    """If this drifts, every 'breaks correctly' test below becomes vacuous — it would be
    rejecting for the wrong reason. Verify the instrument first."""
    rec = EvidenceRecord.from_mapping(_ok_record())
    assert rec.source_id == "companies_house"


# ── the published invariants, one test each ─────────────────────────────────

def test_invariant_identifiers_are_uuids():
    bad = _ok_record() | {"evidence_id": "not-a-uuid"}
    with pytest.raises(EvidenceContractError):
        EvidenceRecord.from_mapping(bad)


def test_invariant_timestamps_carry_an_explicit_utc_offset():
    """A naive timestamp is the classic silent-drift bug: it looks fine and means
    nothing without an offset."""
    bad = _ok_record() | {"retrieved_at": "2026-07-30T12:00:00"}
    with pytest.raises(EvidenceContractError):
        EvidenceRecord.from_mapping(bad)


def test_invariant_unknown_fields_are_refused():
    """Not in the published list, but it is what stops a typo'd field from being
    silently dropped — a dropped field reads as an absent one."""
    bad = _ok_record() | {"contnet_hash": "x"}
    with pytest.raises(EvidenceContractError):
        EvidenceRecord.from_mapping(bad)


def test_invariant_answered_outcomes_require_a_sha256_content_hash():
    """'answered source outcomes require a SHA-256 content hash' — the invariant that
    makes evidence re-checkable. Without a hash there is nothing to prove the retrieved
    content is the content the finding was based on."""
    bad = _ok_record()
    bad.pop("content_hash")
    with pytest.raises(EvidenceContractError) as exc:
        EvidenceRecord.from_mapping(bad)
    assert "content_hash" in str(exc.value)


def test_invariant_the_hash_must_actually_be_a_sha256():
    """A non-digest placeholder must not satisfy it — 'has a value' is not 'has a hash'."""
    with pytest.raises(EvidenceContractError):
        EvidenceRecord.from_mapping(_ok_record() | {"content_hash": "abc"})


def test_invariant_status_axes_reject_impossible_combinations():
    """'status axes remain orthogonal and impossible combinations are rejected'."""
    with pytest.raises(EvidenceContractError):
        EvidenceAssessment(
            configuration_state=ConfigurationState.CONFIGURED,
            source_state=SourceState.CURRENT,
            attempt_outcome=AttemptOutcome.NOT_ATTEMPTED,
            match_outcome=MatchOutcome.MATCH,   # cannot match without an attempt
        ).validate()


def test_invariant_stale_evidence_never_derives_completed():
    """'stale or degraded evidence never derives a completed verdict' — the rule with
    real safety meaning: a stale sanctions screen must never read as completed."""
    verdict = EvidenceAssessment(
        configuration_state=ConfigurationState.CONFIGURED,
        source_state=SourceState.STALE,
        attempt_outcome=AttemptOutcome.SUCCEEDED,
        match_outcome=MatchOutcome.NO_MATCH,
    ).derive_verdict()
    assert verdict == EvidenceVerdict.DEGRADED
    assert not str(verdict.value).startswith("completed"), (
        "stale evidence derived a completed verdict — this is the false-clean direction")


def test_invariant_every_matrix_combination_avoids_completed_when_stale():
    """The property across the WHOLE matrix, not one example."""
    from aria_service.intel.dd_evidence_standard import evidence_assessment_matrix
    offenders = [
        row for row in evidence_assessment_matrix()
        if row.get("source_state") == SourceState.STALE.value
        and str(row.get("verdict", "")).startswith("completed")
    ]
    assert not offenders, f"stale rows deriving a completed verdict: {offenders[:3]}"


def test_invariant_the_verdict_function_version_is_recorded():
    a = EvidenceAssessment(
        configuration_state=ConfigurationState.CONFIGURED,
        source_state=SourceState.CURRENT,
        attempt_outcome=AttemptOutcome.SUCCEEDED,
        match_outcome=MatchOutcome.NO_MATCH,
    )
    assert a.verdict_fn_version == VERDICT_FUNCTION_VERSION


# ── what is NOT enforced, pinned by name so it cannot be forgotten ──────────

def test_historical_replay_is_not_implemented_and_is_declared_as_such():
    """THE HALF-TRUE INVARIANT. The version is RECORDED but cannot be REPLAYED: there is
    no version→function registry, and v1 refuses any other version outright.

    This test documents the real state. When a v2 ships, this test MUST fail — which is
    the point: it forces whoever ships it to build the registry rather than silently
    re-deriving historical reports under a newer policy.
    """
    assert VERDICT_FUNCTION_VERSION == "1.0.0", (
        "a new verdict function version shipped. Historical replay is still not "
        "implemented, so old reports would be re-derived under the NEW policy. Build the "
        "version->function registry before changing this constant.")
    with pytest.raises(EvidenceContractError):
        EvidenceAssessment(
            configuration_state=ConfigurationState.CONFIGURED,
            source_state=SourceState.CURRENT,
            attempt_outcome=AttemptOutcome.SUCCEEDED,
            match_outcome=MatchOutcome.NO_MATCH,
            verdict_fn_version="2.0.0",
        ).validate()


def test_direct_construction_bypasses_field_validation():
    """PINNED, not fixed. `EvidenceRecord` is a frozen dataclass whose invariants live in
    `from_mapping`; constructing one directly skips them all.

    Harmless while nothing in production builds records — and a live hole the moment the
    orchestrator does. If this ever starts raising, direct construction has been made
    safe and this test should be replaced by one asserting THAT.
    """
    rec = EvidenceRecord(
        evidence_id="not-a-uuid", tenant_id="", case_id="also-not-a-uuid",
        case_scope_version=1, subject_entity_id="x", source_attempt_id="y",
        source_id="", source_authority="primary", retrieval_outcome="answered",
        retrieved_at="no-offset", request_fingerprint="", licence_policy_id="",
        access_method="", adapter_version="", parser_version="",
    )
    assert rec.evidence_id == "not-a-uuid", (
        "direct construction is now validated — good; update this test to assert it")


def test_the_published_invariant_list_has_not_silently_grown():
    """A sentence added to the manifest with no test is a guarantee nobody checks. If
    this fails, add a test for the new invariant — do not just raise the number."""
    invariants = describe_standard()["invariants"]
    assert len(invariants) == 10, (
        f"the published invariant list changed to {len(invariants)} entries. Every "
        f"published sentence needs a test in this file, or it is decoration:\n"
        + "\n".join(f"  - {i}" for i in invariants))

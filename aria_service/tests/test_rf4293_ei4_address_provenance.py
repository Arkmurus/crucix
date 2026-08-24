"""R-F4293 / C-246 — EI-4 from the register-established address already on the report.

Fifth instance of the C-235 shape. EI-4 ("Verified registered/trading address
(entity) and residential address (individual)") rendered NOT_RUN "no resolver is
bound to this question in this build" while `identity.registered_address` was
populated on every GB run from the Companies House profile
(dd_orchestrator:3773) or the GLEIF legal address (:5413).

THE TRAP THIS READER EXISTS TO AVOID. Line 3773 is
`report.identity.registered_address or profile.get("registered_office_address")`
— an address ALREADY on the record is NOT overwritten by the register. So a
customer-supplied address survives, and binding EI-4 to "the field is non-empty"
would certify supplied data as register-verified. That is the exact false-clean
this whole series guards against.

`registry_status` is the provenance signal, and `registry_adapters.RegistryStatus`
was built to answer precisely this: "Only VERIFIED/PARTIAL are authority —
everything else means we did NOT establish identity from a registry, and must
never certify it (never-false-clean)."

EI-4 applies to BOTH subject types and its two halves have different sources: an
entity address comes from the register, an individual's residential address from
a document or bureau match, which is counterparty-supplied.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aria_service.intel import dd_standard as ds  # noqa: E402

ANSWERED = {ds.EvidenceState.CORROBORATED.value, ds.EvidenceState.SINGLE_SOURCE.value}
ADDRESS = "Greggs House, Quorum Business Park, Newcastle Upon Tyne, NE12 8BU"


def _report(*, address=ADDRESS, registry_status="verified", entity_type="company"):
    identity = {"entity_name": "PROBE LTD", "entity_type": entity_type}
    if address is not None:
        identity["registered_address"] = address
    if registry_status is not None:
        identity["registry_status"] = registry_status
    return {"subject": {"name": "PROBE LTD", "jurisdiction": "GB"}, "identity": identity}


def _ei4(**kw) -> dict:
    rows = ds.assess(_report(**kw), tier="ENHANCED")["resolutions"]
    return {r["question_id"]: r for r in rows}["EI-4"]


# ── the defect ─────────────────────────────────────────────────────────────

def test_a_register_established_address_is_no_longer_unbound() -> None:
    """THE CAPABILITY TEST — the live symptom C-246 files."""
    row = _ei4()
    assert "no resolver is bound" not in str(row["reason"])
    assert row["state"] in ANSWERED, row


def test_it_credits_coverage() -> None:
    before = ds.assess(_report(address=None, registry_status=None), tier="ENHANCED")
    after = ds.assess(_report(), tier="ENHANCED")
    assert after["answered"] > before["answered"]


@pytest.mark.parametrize("status", ["verified", "partial"])
def test_both_authority_statuses_answer(status: str) -> None:
    """The vocabulary names exactly two authority values."""
    assert _ei4(registry_status=status)["state"] in ANSWERED


# ── THE TRAP: a supplied address must never read as register-verified ──────

@pytest.mark.parametrize("status", ["manual_required", "not_available",
                                    "provider_required"])
def test_a_non_authority_status_never_certifies_the_address(status: str) -> None:
    """`registered_address` survives from customer input when the register does
    not overwrite it (dd_orchestrator:3773 is an `or`), so a non-authority status
    with an address on file is precisely the case that must NOT pass."""
    row = _ei4(registry_status=status)
    assert row["state"] not in ANSWERED, status
    assert row["state"] == ds.EvidenceState.ATTEMPTED_INCONCLUSIVE.value
    # the reason must name WHY it is not certified and WHAT the risk is, so a
    # reader can act on it rather than seeing a bare "inconclusive"
    reason = str(row["reason"]).lower()
    assert status in reason, "the reason does not name the registry_status"
    assert "supplied" in reason, "the reason does not name the supplied-address risk"


def test_an_absent_registry_status_never_certifies_the_address() -> None:
    """R-F3231 records that `registry_status` read None for EVERY report before
    the carrier existed — so absent must be treated as no authority, not as a
    legacy pass."""
    row = _ei4(registry_status=None)
    assert row["state"] not in ANSWERED


def test_no_address_at_all_is_not_run() -> None:
    row = _ei4(address=None, registry_status=None)
    assert row["state"] == ds.EvidenceState.NOT_RUN.value


def test_an_empty_address_string_is_not_an_address() -> None:
    for blank in ("", "   "):
        assert _ei4(address=blank)["state"] not in ANSWERED, repr(blank)


# ── the individual half is counterparty-supplied ───────────────────────────

def test_an_individual_subject_awaits_the_counterparty() -> None:
    """A person's residential address comes from a document or bureau match, not
    from a company register — reporting the registered address for a person would
    answer a different question than the one asked."""
    row = _ei4(entity_type="individual")
    assert row["state"] == ds.EvidenceState.AWAITING_COUNTERPARTY.value
    assert row["state"] not in ANSWERED
    assert "residential" in str(row["reason"]).lower()


# ── it must not disturb anything else ──────────────────────────────────────

def test_binding_ei4_changes_no_other_question() -> None:
    before = {r["question_id"]: r["state"] for r in ds.assess(
        _report(address=None, registry_status=None), tier="ENHANCED")["resolutions"]}
    after = {r["question_id"]: r["state"]
             for r in ds.assess(_report(), tier="ENHANCED")["resolutions"]}
    moved = {q for q in before if before[q] != after.get(q)}
    assert moved <= {"EI-4", "EI-1", "EI-2"}, moved


def test_the_reader_is_actually_bound() -> None:
    assert ds.QUESTIONS_BY_ID["EI-4"].reader is not None

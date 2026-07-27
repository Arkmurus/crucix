"""R-F3231/R-F3232 — a registry lookup may only certify what a registry answered.

The defect, traced 2026-07-27 from the root:

  * `registry_adapters._build_result` DOES emit `registry_status`
    (registry_adapters.py:2762), classified by `RegistryStatus.for_adapter` so
    every `*_stub` adapter is MANUAL_REQUIRED.
  * `dd_schema` DOES consume it (dd_schema.py:1592) and refuses identity
    authority for a non-authoritative status.
  * `IdentitySection` HAD NO `registry_status` FIELD, and dd_orchestrator never
    carried the adapter's status onto the report.

So R-F2693 built a producer and a consumer and never built the carrier between
them. `ident.get("registry_status")` was `None` for EVERY report — not merely
for the legacy ones its comment allows for — and the guard could never fire.

Meanwhile the orchestrator gated on `if reg_result:` — truthiness, not
authority — and emitted `confidence="CONFIRMED"` for a stub that queried no
registry at all, echoing the caller's own supplied address back as
`registered_address`. The stub's own caveats ("US has no federal company
registry", "UBO is NOT public — request the FinCEN BOI report", "Manual
verification: open <sunbiz URL> and search") were built into
`result["data_gaps"]` by 18 adapters and read by nobody.

These tests drive the REAL producer — `registry_adapters._build_us_stub` and a
real VERIFIED result from `_build_result` — through the REAL application path,
not a hand-made dict (§3c).
"""

from __future__ import annotations

import pytest

import aria_service.intel.dd_orchestrator as dd
from aria_service.intel.dd_schema import ARKDDReport
from aria_service.intel.registry_adapters import RegistryStatus, _build_us_stub


def _stub_result() -> dict:
    """A REAL stub result from the real builder — no registry was queried."""
    return _build_us_stub(
        name="Acme Holdings LLC",
        reg_number=None,
        address="1209 Orange St, Wilmington, DE 19801",
        state="DE",
        state_hint="Delaware",
    )


def _verified_result() -> dict:
    """A REAL authoritative result, built by the same builder the adapters use."""
    from aria_service.intel.registry_adapters import _build_result

    return _build_result(
        company_name="Equinor ASA",
        company_number="923609016",
        company_status="active",
        date_of_creation="1972-07-14",
        registered_office_address="Forusbeen 50, 4035 Stavanger",
        jurisdiction="NO",
        sic_codes=[],
        officers=[{"name": "A Director"}],
        psc=[],
        source_url="https://data.brreg.no/enhetsregisteret/api/enheter/923609016",
        adapter="norway_brreg",
    )


# ── the carrier that never existed ───────────────────────────────────────

def test_identity_section_can_carry_a_registry_status():
    """dd_schema.py:1592 reads `ident.get("registry_status")`. If the section
    cannot hold one, that read is `None` forever and the R-F2693 authority
    guard is structurally unreachable."""
    report = ARKDDReport()
    assert hasattr(report.identity, "registry_status"), (
        "IdentitySection has no registry_status field — dd_schema's authority "
        "guard reads a key nothing can ever set")


def test_a_stub_is_classified_non_authority_by_the_real_builder():
    """Guards the premise the rest of this file rests on."""
    result = _stub_result()
    status = RegistryStatus.coerce(result.get("registry_status"))
    assert status is not None, "the stub builder emits no registry_status"
    assert status is RegistryStatus.MANUAL_REQUIRED
    assert not status.is_authority()


# ── R-F3231: never CONFIRMED without an authority ────────────────────────

def test_stub_result_is_not_recorded_as_a_confirmed_registry_lookup():
    """THE regression. A stub queried no registry; the report must not say a
    registry confirmed anything."""
    report = ARKDDReport()
    dd._apply_registry_result(report, _stub_result(), registration_number=None)

    confirmed = [f for f in report.identity.findings
                 if str(getattr(f, "confidence", "")).upper() == "CONFIRMED"]
    assert not confirmed, (
        "a stub that queried no registry produced a CONFIRMED finding: "
        f"{[getattr(f, 'title', '') for f in confirmed]}")

    assert report.identity.registry_status == RegistryStatus.MANUAL_REQUIRED.value
    # And the report must SAY so, not merely omit the claim.
    assert report.identity.findings, "a stub result produced no finding at all"
    said = " ".join(str(getattr(f, "title", "")) + " " + str(getattr(f, "detail", ""))
                    for f in report.identity.findings).lower()
    assert "no registry" in said or "not verified" in said or "manual" in said, (
        f"the report does not disclose that no registry answered: {said[:200]}")


def test_a_stub_never_echoes_the_supplied_address_back_as_registry_sourced():
    """`_build_us_stub` copies the address it was GIVEN into the profile. Writing
    that to `registered_address` presents the caller's own input as a registry
    finding."""
    report = ARKDDReport()
    supplied = "1209 Orange St, Wilmington, DE 19801"
    dd._apply_registry_result(report, _stub_result(), registration_number=None)
    assert report.identity.registered_address != supplied, (
        "the address supplied to the lookup was written back as if the registry "
        "had returned it")


def test_a_real_registry_result_still_populates_and_is_confirmed():
    """The fix must not cost a genuine lookup its evidence — never-false-clean
    cuts both ways, and a real registry answer has to keep certifying."""
    report = ARKDDReport()
    dd._apply_registry_result(report, _verified_result(), registration_number=None)

    assert report.identity.registry_status == RegistryStatus.VERIFIED.value
    assert report.identity.registration_number == "923609016"
    assert report.identity.registration_status == "active"
    assert report.identity.incorporation_date == "1972-07-14"
    assert report.identity.directors, "a verified result lost its officers"
    confirmed = [f for f in report.identity.findings
                 if str(getattr(f, "confidence", "")).upper() == "CONFIRMED"]
    assert confirmed, "a real registry lookup no longer records a CONFIRMED finding"


def test_existing_values_are_never_clobbered():
    """R-F2511 — this path runs AFTER Companies House on GB. It may only fill
    fields the earlier block left empty; overwriting CH's officers with an
    adapter's empty list is how real GB companies showed 0 directors."""
    report = ARKDDReport()
    report.identity.registration_number = "01234567"
    report.identity.registration_status = "active"
    report.identity.directors = [{"name": "Existing CH Director"}]

    dd._apply_registry_result(report, _verified_result(), registration_number=None)

    assert report.identity.registration_number == "01234567"
    assert report.identity.registration_status == "active"
    assert report.identity.directors == [{"name": "Existing CH Director"}]


# ── R-F3232: the caveats 18 adapters write and nobody read ───────────────

def test_adapter_data_gaps_reach_the_report():
    """`_build_us_stub` writes four caveats a human needs — that US has no
    federal registry, that UBO is not public and must be requested as a FinCEN
    BOI report, and the exact Sunbiz/ICIS URL to search by hand. Eighteen
    adapters build these. Nothing read them."""
    report = ARKDDReport()
    stub = _stub_result()
    assert stub.get("data_gaps"), "premise failed: the stub built no data_gaps"

    dd._apply_registry_result(report, stub, registration_number=None)

    joined = " ".join(report.identity.data_gaps).lower()
    assert "federal company registry" in joined, (
        "the adapter's own caveats never reached the report")
    assert "boi" in joined or "beneficial ownership" in joined
    assert any("icis.corp.delaware.gov" in g or "sunbiz" in g
               for g in report.identity.data_gaps), (
        "the manual-verification URL the adapter supplied was dropped")


def test_data_gaps_are_not_duplicated_on_a_second_application():
    """The identity layer can apply a primary and then a fallback result."""
    report = ARKDDReport()
    dd._apply_registry_result(report, _stub_result(), registration_number=None)
    first = len(report.identity.data_gaps)
    dd._apply_registry_result(report, _stub_result(), registration_number=None)
    assert len(report.identity.data_gaps) == first, "caveats duplicated"


# ── the fix must be ON the path, not merely available ────────────────────

def test_the_identity_layer_actually_calls_the_single_authority_path():
    """A fix that is not on the path it names has not shipped (§3c). R-F3175's
    `_version_key` and R-F2693's `registry_status` were both written, tested in
    isolation, and never wired — this asserts the wiring itself."""
    import inspect

    src = inspect.getsource(dd._run_identity)
    assert "_apply_registry_result(" in src, (
        "_run_identity does not call the single authority path — the registry "
        "result is still being applied inline")


def test_no_second_confirmed_registry_finding_survives_inline():
    """Guards against the old inline block being left behind beside the new
    one, which would emit the CONFIRMED finding all over again."""
    import inspect

    src = inspect.getsource(dd._run_identity)
    assert 'title=f"Registry lookup: {reg_result.get(' not in src, (
        "the old inline CONFIRMED registry finding is still present")


# ── R-F3233: a vault citation must name the subject ──────────────────────

def test_vault_match_requires_a_word_boundary_not_a_substring():
    """`needle in text.lower()` cites any page that merely CONTAINS the name as
    a fragment. 'Acme Ltd' matched 'Acmetech Solutions'; a one-word name matched
    half the web. This is the same name-coincidence class that put a fraud
    headline in a clean report's sources."""
    assert dd._vault_text_names_subject("Acme Holdings", "ACMETECH SOLUTIONS INC") is False
    assert dd._vault_text_names_subject("Acme Holdings", "a deal with Acme Holdings Ltd today") is True
    # Legal suffixes on either side must not defeat a genuine match.
    assert dd._vault_text_names_subject("Acme Holdings Ltd", "Acme Holdings announced") is True
    assert dd._vault_text_names_subject("Acme Holdings", "ACME HOLDINGS LIMITED") is True


def test_a_name_too_generic_to_be_evidence_is_refused():
    """A single common word is not an identification. Citing a page because it
    contains 'Apple' is a fabricated link, and abstaining is the honest
    outcome."""
    for generic in ("Apple", "Shell", "Orange", "BT"):
        assert dd._vault_text_names_subject(generic, f"I ate an {generic.lower()} today") is False, (
            f"'{generic}' produced a citation from an unrelated mention")


def test_vault_cap_is_disclosed_not_silent():
    """A cap that drops sources without saying so reads as 'we checked
    everything'. No silent caps."""
    import inspect

    src = inspect.getsource(dd._consult_vault_sources)
    assert "_vault_text_names_subject(" in src, (
        "the vault consult still uses a bare substring match")
    assert "data_gaps" in src, (
        "the per-run vault cap is applied without disclosing what was dropped")


def test_evidence_ratchets_up_and_never_down():
    """A later authoritative answer may replace a stub; a later stub may never
    demote an authority already established."""
    report = ARKDDReport()
    dd._apply_registry_result(report, _stub_result(), registration_number=None)
    assert report.identity.registry_status == RegistryStatus.MANUAL_REQUIRED.value
    dd._apply_registry_result(report, _verified_result(), registration_number=None)
    assert report.identity.registry_status == RegistryStatus.VERIFIED.value, (
        "a real registry answer failed to upgrade a stub status")

    demote = ARKDDReport()
    dd._apply_registry_result(demote, _verified_result(), registration_number=None)
    dd._apply_registry_result(demote, _stub_result(), registration_number=None)
    assert demote.identity.registry_status == RegistryStatus.VERIFIED.value, (
        "a stub demoted an authority that had already been established")

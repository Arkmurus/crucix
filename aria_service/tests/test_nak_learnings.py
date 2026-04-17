"""Tests for the 6 capabilities extracted from the NAK / SERBAN / F3
engagement 2026-04-17.

Covers:
  1. US state registry adapter (FL Sunbiz parse + DE stub + unknown-state fallback)
  2. Virtual-office / mail-drop detector (seed list + PMB + mailbox heuristics)
  3. Sanctions propagation (NORINCO → NS-CMIC → USD → secondary exposure)
  4. FinCEN BOI caveat injection (indirect via data_gaps content)
  5. Cited-artifact verifier (regex extract + verify short-circuits)
  6. Protective reply drafter (template expansion + tier-tone matching)
"""
from __future__ import annotations

import asyncio

import pytest


# ═══════════════════════════════════════════════════════════════════════
# 1. Virtual-office registry
# ═══════════════════════════════════════════════════════════════════════

def test_virtual_office_f3_corridor_matches():
    """The exact F3 address must flag as HIGH-risk virtual office."""
    from aria_service.intel import virtual_office_registry
    result = virtual_office_registry.check_address(
        "3363 NE 163rd Street, #602, North Miami Beach, FL 33160, USA"
    )
    assert result["is_virtual_office"] is True
    assert result["confidence"] == "HIGH"
    assert result["risk"] == "high"
    assert "North Miami Beach" in (result["provider"] or "")


def test_virtual_office_wyoming_mass_registration():
    from aria_service.intel import virtual_office_registry
    result = virtual_office_registry.check_address(
        "30 N Gould St, Suite R, Sheridan, WY 82801"
    )
    assert result["is_virtual_office"] is True
    assert result["risk"] == "high"


def test_virtual_office_pmb_heuristic():
    """USPS PMB marker is a definitional mail-drop signal."""
    from aria_service.intel import virtual_office_registry
    result = virtual_office_registry.check_address(
        "500 Main Street PMB 123, Anytown, XX 12345"
    )
    assert result["is_virtual_office"] is True
    assert result["confidence"] == "HIGH"


def test_virtual_office_clean_corporate_address_passes():
    """A vanilla corporate address must NOT false-positive."""
    from aria_service.intel import virtual_office_registry
    result = virtual_office_registry.check_address(
        "1 Infinite Loop, Cupertino, CA 95014"
    )
    assert result["is_virtual_office"] is False


def test_virtual_office_known_providers_non_empty():
    from aria_service.intel import virtual_office_registry
    providers = virtual_office_registry.known_providers()
    assert len(providers) >= 5
    # every entry must have match + provider + risk
    for p in providers:
        assert p.get("match") and p.get("provider") and p.get("risk")


# ═══════════════════════════════════════════════════════════════════════
# 2. Sanctions propagation
# ═══════════════════════════════════════════════════════════════════════

def test_sanctions_norinco_found_by_name():
    from aria_service.intel import sanctions_propagation
    entry = sanctions_propagation.lookup_oem("NORINCO")
    assert entry is not None
    assert "OFAC_NSCMIC" in entry.regimes


def test_sanctions_norinco_found_via_fk3000_product():
    """The FK-3000 product keyword should find NORINCO."""
    from aria_service.intel import sanctions_propagation
    entry = sanctions_propagation.lookup_oem("FK-3000")
    assert entry is not None
    assert entry.oem == "NORINCO"


def test_sanctions_usd_payment_triggers_secondary_exposure():
    """NORINCO + USD rail + UK broker must be SECONDARY exposure."""
    from aria_service.intel import sanctions_propagation
    result = sanctions_propagation.propagate(
        oem_or_product="NORINCO",
        payment_currency="USD",
        broker_jurisdiction="GB",
    )
    assert result["hit"] is True
    assert result["broker_exposure"] == "secondary"
    assert result["verdict"] == "HIGH"
    assert "USD" in result["payment_risk"]


def test_sanctions_un_listed_oem_is_hard_stop():
    from aria_service.intel import sanctions_propagation
    result = sanctions_propagation.propagate(
        oem_or_product="KOMID",
        payment_currency="EUR",
        broker_jurisdiction="GB",
    )
    assert result["verdict"] == "HARD_STOP"


def test_sanctions_unknown_oem_returns_no_hit():
    from aria_service.intel import sanctions_propagation
    result = sanctions_propagation.propagate(
        oem_or_product="SomeUnknownPrivateFirmGmbH",
        payment_currency="EUR",
    )
    assert result["hit"] is False
    assert "No sanctions-list match" in result["human_summary"]


def test_sanctions_summary_has_counts():
    from aria_service.intel import sanctions_propagation
    s = sanctions_propagation.summary()
    assert s["oems_covered"] >= 5
    assert "USD" in s["currencies_mapped"]


# ═══════════════════════════════════════════════════════════════════════
# 3. Cited-artifact verifier
# ═══════════════════════════════════════════════════════════════════════

def test_cited_artifact_extraction_finds_ncnda():
    from aria_service.intel import cited_artifact_verifier
    text = "Pursuant to NCNDA-KOR-MCT-0126 and LOI-2026-0041, please note..."
    arts = cited_artifact_verifier.extract_cited_ids(text)
    ids = {a.doc_id for a in arts}
    assert "NCNDA-KOR-MCT-0126" in ids
    assert "LOI-2026-0041" in ids


def test_cited_artifact_no_ids_in_prose():
    from aria_service.intel import cited_artifact_verifier
    result = cited_artifact_verifier.verify_cited_artifacts(
        "This is a letter that does not reference any document identifiers."
    )
    assert result["cited"] == []
    assert result["unverified"] == 0
    assert "Nothing" in result["summary"] or "nothing" in result["summary"]


def test_cited_artifact_all_unverified_surfaces_deception_signal():
    """If ID is in text but nowhere in our records → summary warns."""
    from aria_service.intel import cited_artifact_verifier
    result = cited_artifact_verifier.verify_cited_artifacts(
        "We reference NCNDA-FAKE-001 and MOU-FAKE-002 in prior correspondence.",
        counterparty="Totally New Counterparty Ltd",
    )
    assert result["unverified"] == 2
    assert result["verified"] == 0
    assert "fabricating" in result["summary"].lower() or "not found" in result["summary"].lower()


# ═══════════════════════════════════════════════════════════════════════
# 4. Protective reply drafter
# ═══════════════════════════════════════════════════════════════════════

def test_protective_reply_high_tier_produces_firm_opening():
    from aria_service.intel import protective_reply_drafter
    out = protective_reply_drafter.draft_if_deception_high(
        deception_score_tier="HIGH",
        counterparty="SERBAN Industries SRL",
        deal_subject="FK-3000 Counter-drone Supply Romania→UAE",
        deception_signals=["Unprompted commission waiver", "False NCNDA reference"],
        unverified_citations=["NCNDA-KOR-MCT-0126"],
        unilateral_terms=["No commission shall arise on the part of Arkmurus"],
    )
    assert out is not None
    assert "Arkmurus" in out["body"]
    assert "FK-3000" in out["subject"]
    assert "NCNDA-KOR-MCT-0126" in out["body"]
    assert "No commission shall arise" in out["body"]
    # Firm tone marker for HIGH tier
    assert "cannot" in out["body"].lower() or "disclosure" in out["body"].lower()


def test_protective_reply_low_tier_returns_none():
    """Don't draft on LOW — operator handles routine replies."""
    from aria_service.intel import protective_reply_drafter
    out = protective_reply_drafter.draft_if_deception_high(
        deception_score_tier="LOW",
        counterparty="Legitimate Supplier Ltd",
        deal_subject="Routine RFQ response",
    )
    assert out is None


def test_protective_reply_disclosures_always_present():
    """Every draft must ask for the four core disclosures."""
    from aria_service.intel import protective_reply_drafter
    inp = protective_reply_drafter.ProtectiveReplyInput(
        counterparty="X",
        deal_subject="Y",
        deception_tier="ELEVATED",
    )
    out = protective_reply_drafter.draft_protective_reply(inp)
    body = out["body"].lower()
    assert "end-user" in body or "end user" in body
    assert "ubo" in body or "ownership" in body
    assert "sanctions" in body


# ═══════════════════════════════════════════════════════════════════════
# 5. US registry adapter
# ═══════════════════════════════════════════════════════════════════════

def test_us_state_detection_from_address_zip():
    from aria_service.intel import registry_adapters
    state = registry_adapters._detect_us_state(
        "3363 NE 163rd St #602, North Miami Beach, FL 33160, USA"
    )
    assert state == "FL"


def test_us_state_detection_delaware_keyword():
    from aria_service.intel import registry_adapters
    state = registry_adapters._detect_us_state(
        "1209 Orange Street, Wilmington, Delaware 19801"
    )
    assert state == "DE"


def test_us_state_detection_unknown_address_returns_none():
    from aria_service.intel import registry_adapters
    state = registry_adapters._detect_us_state("Some random street in Nowhere")
    assert state is None


def test_us_adapter_unknown_state_stub_has_fincen_boi_guidance():
    """The stub for an unknown US state must cite FinCEN BOI."""
    from aria_service.intel import registry_adapters
    stub = registry_adapters._build_us_stub(
        name="X LLC",
        reg_number=None,
        address="Unknown place",
        state=None,
        state_hint="the relevant US state",
    )
    gap_text = " ".join(stub["data_gaps"]).lower()
    assert "fincen" in gap_text or "boi" in gap_text


def test_us_in_supported_jurisdictions():
    from aria_service.intel import registry_adapters
    assert "US" in registry_adapters._SUPPORTED_JURISDICTIONS


def test_us_lookup_via_dispatch_returns_stub_on_unknown_state():
    """End-to-end: dispatch 'US' with no state hint returns stub, not None."""
    from aria_service.intel import registry_adapters

    async def run():
        return await registry_adapters.lookup_entity(
            name="F3 International Resources LLC",
            jurisdiction_iso2="US",
            registration_number=None,
            address="3363 NE 163rd Street, #602, North Miami Beach, FL 33160, USA",
        )

    result = asyncio.run(run())
    # Network may or may not succeed for the Sunbiz parse; either way the
    # adapter must return a dict (stub at minimum), never None.
    assert result is not None
    assert result["profile"]["jurisdiction"] in ("US-FL", "US")
    assert "data_gaps" in result

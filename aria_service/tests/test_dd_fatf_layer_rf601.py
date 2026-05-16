"""R-F601 — DD Layer FATF jurisdiction-risk formalisation.

Past observation 2026-05-16 self-assessment: ARIA flagged "no FATF
formalised layer". Verified — FATF data was loaded in
risk_indices.FATF_STATUS_2025_FEB:178 but only surfaced as a
substring in a single country-risk detail line, and only when the
overall headline hit RED/HARD_STOP. Grey-list jurisdictions (BG, NG,
KE, PH, MZ, etc.) never got an explicit Finding row.

R-F601 introduces _build_fatf_finding(), a pure helper called from
_run_compliance. This file pins:
  - black-list ISO → Finding(severity=hard_stop)
  - grey-list ISO → Finding(severity=amber)
  - clear ISO → None (no Finding clutter)
  - source citation matches Clause 15 (inline URL)
  - confidence is CONFIRMED (Tier-1a source, Clause 24)
  - real risk_indices data still wires the helper end-to-end
"""
from __future__ import annotations


# ══════════════════════════════════════════════════════════════════
# PART A — helper logic (pure function)
# ══════════════════════════════════════════════════════════════════

def test_rf601_black_status_returns_hard_stop_finding():
    """FATF black-list → severity hard_stop."""
    from aria_service.intel.dd_orchestrator import _build_fatf_finding
    f = _build_fatf_finding(
        fatf_status="black",
        iso2="IR",
        jurisdiction="Iran",
    )
    assert f is not None, "R-F601: black-list must produce a Finding"
    assert f.severity == "hard_stop"
    assert "Call for Action" in f.title
    assert "BLOCKED" in f.detail
    assert "Iran" in f.detail


def test_rf601_grey_status_returns_amber_finding():
    """FATF grey-list → severity amber (EDD required)."""
    from aria_service.intel.dd_orchestrator import _build_fatf_finding
    f = _build_fatf_finding(
        fatf_status="grey",
        iso2="NG",
        jurisdiction="Nigeria",
    )
    assert f is not None
    assert f.severity == "amber"
    assert "Increased Monitoring" in f.title
    assert "EDD" in f.detail or "Enhanced Due Diligence" in f.detail
    assert "Nigeria" in f.detail


def test_rf601_clear_status_returns_none():
    """FATF clear → no Finding (don't clutter the report)."""
    from aria_service.intel.dd_orchestrator import _build_fatf_finding
    assert _build_fatf_finding(
        fatf_status="clear", iso2="GB", jurisdiction="United Kingdom"
    ) is None


def test_rf601_unknown_status_returns_none():
    """Unknown / None / empty status → no Finding."""
    from aria_service.intel.dd_orchestrator import _build_fatf_finding
    assert _build_fatf_finding(
        fatf_status=None, iso2="DE", jurisdiction="Germany"
    ) is None
    assert _build_fatf_finding(
        fatf_status="", iso2="DE", jurisdiction="Germany"
    ) is None
    assert _build_fatf_finding(
        fatf_status="unknown", iso2="DE", jurisdiction="Germany"
    ) is None


def test_rf601_status_is_case_insensitive():
    """Status comparison must tolerate 'BLACK' / 'Grey' / etc."""
    from aria_service.intel.dd_orchestrator import _build_fatf_finding
    for status in ("BLACK", "Black", "bLaCk"):
        assert _build_fatf_finding(
            fatf_status=status, iso2="IR", jurisdiction="Iran"
        ) is not None


def test_rf601_finding_has_tier1a_source_citation():
    """Clause 15 — every fact carries an inline citation; Clause 24 —
    FATF is a Tier-1a authoritative source so confidence is CONFIRMED."""
    from aria_service.intel.dd_orchestrator import _build_fatf_finding
    f = _build_fatf_finding(
        fatf_status="grey", iso2="KE", jurisdiction="Kenya"
    )
    assert f is not None
    assert f.confidence == "CONFIRMED"
    assert "fatf-gafi.org" in f.source, (
        "R-F601: source must include the authoritative FATF URL"
    )
    assert "FATF_STATUS_2025_FEB" in f.source, (
        "R-F601: source must name the in-repo snapshot for traceability"
    )


# ══════════════════════════════════════════════════════════════════
# PART B — wired to real risk_indices data
# ══════════════════════════════════════════════════════════════════

def test_rf601_iran_resolves_to_black_via_risk_indices():
    """Real-data integration: Iran (IR) is on the FATF black list per
    risk_indices.FATF_STATUS_2025_FEB."""
    from aria_service.intel import risk_indices
    assert risk_indices.FATF_STATUS_2025_FEB.get("IR") == "black"


def test_rf601_nigeria_resolves_to_grey_via_risk_indices():
    """Nigeria (NG) is on the FATF grey list per
    risk_indices.FATF_STATUS_2025_FEB."""
    from aria_service.intel import risk_indices
    assert risk_indices.FATF_STATUS_2025_FEB.get("NG") == "grey"


def test_rf601_uk_is_not_listed():
    """UK is not on either list — status defaults to 'clear'."""
    from aria_service.intel import risk_indices
    assert risk_indices.FATF_STATUS_2025_FEB.get("GB", "clear") == "clear"


# ══════════════════════════════════════════════════════════════════
# PART C — orchestrator wiring (smoke)
# ══════════════════════════════════════════════════════════════════

def test_rf601_helper_is_called_from_run_compliance_in_source():
    """Source-level pin: _run_compliance must invoke _build_fatf_finding
    so the layer wiring stays intact across future refactors."""
    import pathlib
    src = pathlib.Path(
        "C:/code/crucix/aria_service/intel/dd_orchestrator.py"
    ).read_text(encoding="utf-8", errors="ignore")
    run_idx = src.find("async def _run_compliance")
    assert run_idx > 0, "_run_compliance not found"
    body = src[run_idx:run_idx + 6000]
    assert "_build_fatf_finding" in body, (
        "R-F601: _run_compliance does not call _build_fatf_finding — "
        "the FATF layer is built but not wired."
    )

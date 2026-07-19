"""R-F635 — DD orchestrator wires cultural_atlas into compliance section.

Pins:
  - ComplianceSection schema has cultural_context field
  - _run_compliance populates it for non-UK/US seeded jurisdictions
  - UK and US do NOT get cultural injection (their norms are the
    operator's default-familiar baseline)
  - Unseeded jurisdictions get empty string (honest default — atlas
    hasn't been seeded, not "no culture exists")
"""
from __future__ import annotations

import pathlib


def _cultural_section() -> str:
    """The `4a-CULTURAL` wiring block inside `_run_compliance`, bounded by its
    stable section markers.

    R-F2784 (2026-07-19): the PART B/C pins previously sliced a fixed
    `src[run_idx:run_idx + 8000]` window from `async def _run_compliance` and
    then searched around `find("cultural_atlas")`. `_run_compliance` has since
    grown (risk_indices + WB overlay + expert layers) so the cultural wiring —
    still present at dd_orchestrator.py — fell PAST the 8000-char window,
    making `find(...)` return -1 and the source-pins false-fail. Bounding to the
    real section asserts the same wiring contract without the fragile char count.
    """
    src = pathlib.Path(
        "C:/code/crucix/aria_service/intel/dd_orchestrator.py"
    ).read_text(encoding="utf-8", errors="ignore")
    start = src.find("4a-CULTURAL")
    assert start > 0, "R-F635: 4a-CULTURAL section marker missing from _run_compliance"
    end = src.find("4a-EXPERT-KNOWLEDGE", start)
    assert end > start, "R-F635: cultural section not bounded by the expert-knowledge marker"
    return src[start:end]


# ══════════════════════════════════════════════════════════════════
# PART A — schema field present + defaults safe
# ══════════════════════════════════════════════════════════════════

def test_rf635_compliance_section_has_cultural_context_field():
    from aria_service.intel.dd_schema import ComplianceSection
    s = ComplianceSection()
    assert hasattr(s, "cultural_context")
    assert s.cultural_context == ""


def test_rf635_cultural_context_accepts_string_assignment():
    from aria_service.intel.dd_schema import ComplianceSection
    s = ComplianceSection()
    s.cultural_context = "[CULTURAL CONTEXT — SA — PROBABLE...]"
    assert "SA" in s.cultural_context


# ══════════════════════════════════════════════════════════════════
# PART B — orchestrator wiring (source-level pin)
# ══════════════════════════════════════════════════════════════════

def test_rf635_run_compliance_calls_cultural_atlas_render():
    """Source-level pin: _run_compliance must invoke
    cultural_atlas.render_context_block when iso2 is non-UK/US."""
    block = _cultural_section()
    assert "cultural_atlas" in block, (
        "R-F635: _run_compliance must import cultural_atlas"
    )
    assert "render_context_block" in block, (
        "R-F635: _run_compliance must call render_context_block"
    )
    assert "cultural_context" in block, (
        "R-F635: _run_compliance must populate report.compliance.cultural_context"
    )


def test_rf635_excludes_uk_and_us():
    """The wiring must explicitly exclude GB and US from cultural
    injection — those are the operator's default-familiar baseline."""
    region = _cultural_section()
    assert '"GB"' in region or "'GB'" in region
    assert '"US"' in region or "'US'" in region


def test_rf635_cultural_block_attached_to_compliance_not_identity():
    """Cultural read goes on ComplianceSection (jurisdictional context),
    not IdentitySection (entity-level facts)."""
    block = _cultural_section()
    assert "report.compliance.cultural_context" in block


# ══════════════════════════════════════════════════════════════════
# PART C — defensive (non-fatal failure)
# ══════════════════════════════════════════════════════════════════

def test_rf635_cultural_lookup_wrapped_in_try_except():
    """A failure in cultural_atlas (import, render, etc.) must NOT
    break the compliance layer. Defensive try/except is mandatory."""
    region = _cultural_section()
    assert "try:" in region
    assert "except" in region


# ══════════════════════════════════════════════════════════════════
# PART D — end-to-end content check (Saudi case)
# ══════════════════════════════════════════════════════════════════

def test_rf635_saudi_cultural_block_renders_full_depth():
    """Verify the cultural_atlas full-depth output for SA contains the
    structural markers _run_compliance will inject — Hofstede dims,
    GCC weekend, relationship-first."""
    from aria_service.intel.cultural_atlas import render_context_block
    block = render_context_block("SA", depth="full")
    assert "Saudi Arabia" in block
    assert "PDI=" in block  # Hofstede
    assert "fri" in block.lower()  # GCC weekend
    assert "PROBABLE" in block
    assert "population-level" in block.lower()


def test_rf635_unseeded_iso2_yields_empty_block():
    """When iso2 is non-UK/US but not seeded in atlas, render returns
    empty string and the cultural_context field stays empty — honest
    'no data', not invented context."""
    from aria_service.intel.cultural_atlas import render_context_block
    # ZZ is intentionally not seeded
    assert render_context_block("ZZ", depth="full") == ""

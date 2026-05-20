"""R-F736 (2026-05-20) — persona-specific output structure tests.

Agent 3 audit found that the existing persona overlays were "subtle
emphasis shifts, not structural depth changes". Output-shape
directives were in prose, not as explicit templates the LLM is
likely to follow consistently.

R-F736 adds a `REQUIRED OUTPUT STRUCTURE` template block to the
bottom of each of the 6 persona overlays:

  broker            — VERDICT / WHY / NEXT CALL / WATCH-OUTS
  oem_export        — CLASSIFICATION / REGIME / DESTINATION /
                       END-USER FITNESS / DECISION
  government_acquisition — PROGRAMME / VENDOR PROFILE / RISK SIGNALS
                            / INTEROPERABILITY / RECOMMEND
  compliance        — Findings / Sources / Clauses / Recommendations
                       / Open questions (audit-grade markdown headers)
  banking_insurance — 5-section: Sanctions / BO / PEP / Adverse media
                       / Underwriting verdict
  journalist        — WHAT WE KNOW / WHO SAID IT / WHAT'S CONTESTED
                       / CAVEATS

These tests confirm each template is present + persona-specific
(no cross-contamination between overlays).
"""
from __future__ import annotations


_TEMPLATE_MARKER = "REQUIRED OUTPUT STRUCTURE (R-F736"


def test_all_overlays_contain_template_marker():
    """Every persona overlay must contain the R-F736 template anchor."""
    from aria_service.personas import _OVERLAYS

    for key, overlay in _OVERLAYS.items():
        assert _TEMPLATE_MARKER in overlay, (
            f"R-F736: persona {key!r} overlay missing the "
            f"REQUIRED OUTPUT STRUCTURE block."
        )


def test_broker_template_has_verdict_emoji_anchors():
    """R-F736 broker: 🟢/🟡/🔴/🔵 verdict line + WHY/NEXT CALL/WATCH-OUTS."""
    from aria_service.personas import get_overlay

    overlay = get_overlay("broker")
    assert "🟢" in overlay and "🟡" in overlay and "🔴" in overlay
    assert "WHY" in overlay
    assert "NEXT CALL" in overlay
    assert "WATCH-OUTS" in overlay


def test_oem_export_template_has_classification_anchors():
    """R-F736 oem_export: CLASSIFICATION / REGIME / DESTINATION / DECISION."""
    from aria_service.personas import get_overlay

    overlay = get_overlay("oem_export")
    assert "CLASSIFICATION" in overlay
    assert "CONTROLLING REGIME" in overlay
    assert "DESTINATION IMPLICATIONS" in overlay
    assert "END-USER FITNESS" in overlay
    assert "DECISION" in overlay


def test_gov_acq_template_has_programme_anchors():
    from aria_service.personas import get_overlay

    overlay = get_overlay("government_acquisition")
    assert "PROGRAMME" in overlay
    assert "VENDOR PROFILE" in overlay
    assert "RISK SIGNALS" in overlay
    assert "INTEROPERABILITY" in overlay
    assert "RECOMMEND" in overlay


def test_compliance_template_has_markdown_sections():
    """R-F736 compliance: audit-grade markdown headers."""
    from aria_service.personas import get_overlay

    overlay = get_overlay("compliance")
    assert "## Findings" in overlay
    assert "## Sources" in overlay
    assert "## Constitution clauses invoked" in overlay
    assert "## Recommendations for further DD" in overlay
    assert "## Open questions" in overlay


def test_banking_template_has_kyc_underwriting_sections():
    from aria_service.personas import get_overlay

    overlay = get_overlay("banking_insurance")
    assert "SANCTIONS SCREENING" in overlay
    assert "BENEFICIAL OWNERSHIP" in overlay
    assert "PEP" in overlay or "OFFICEHOLDER" in overlay
    assert "ADVERSE MEDIA" in overlay
    assert "UNDERWRITING VERDICT" in overlay
    # The 5 ordered sections must be present in this overlay
    assert "OFAC SDN" in overlay
    assert "OFSI" in overlay


def test_journalist_template_has_attribution_sections():
    from aria_service.personas import get_overlay

    overlay = get_overlay("journalist")
    assert "WHAT WE KNOW" in overlay
    assert "WHO SAID IT" in overlay
    assert "WHAT'S CONTESTED" in overlay
    assert "CAVEATS" in overlay


def test_persona_templates_do_not_cross_contaminate():
    """R-F736: broker's verdict-emoji line must NOT appear in
    compliance overlay (and vice versa). Distinct shapes per persona."""
    from aria_service.personas import get_overlay

    broker = get_overlay("broker")
    compliance = get_overlay("compliance")
    banking = get_overlay("banking_insurance")
    journalist = get_overlay("journalist")

    # Broker verdict line must not appear in compliance / journalist
    # output structures (different audiences, different shapes)
    assert "🟢" not in compliance, (
        "R-F736 cross-contamination: compliance overlay contains "
        "broker's verdict emoji — audit-grade output must not lead "
        "with operational verdict.")
    assert "🟢" not in journalist, (
        "R-F736 cross-contamination: journalist overlay contains "
        "broker's verdict emoji — public-record framing must not lead "
        "with operational verdict.")

    # Compliance markdown headers must not appear in broker output
    assert "## Findings" not in broker

    # Banking underwriting verdict must not appear in broker or
    # journalist templates
    assert "UNDERWRITING VERDICT" not in broker
    assert "UNDERWRITING VERDICT" not in journalist


def test_resolve_persona_still_returns_valid_key():
    """R-F736 must not break the persona resolution chain."""
    from aria_service.personas import resolve_persona, _OVERLAYS

    # Default fallback
    assert resolve_persona(None) in _OVERLAYS
    # Explicit
    assert resolve_persona("compliance") == "compliance"
    # Sector → persona
    assert resolve_persona(None, sector="banking") == "banking_insurance"
    # Unknown sector falls back silently
    assert resolve_persona(None, sector="unknown_sector") == "broker"


def test_get_overlay_returns_non_empty_for_every_persona():
    """Sanity: every persona resolves to a non-trivial overlay."""
    from aria_service.personas import _OVERLAYS, get_overlay

    for key in _OVERLAYS.keys():
        overlay = get_overlay(key)
        assert isinstance(overlay, str)
        assert len(overlay) > 500, (
            f"R-F736: overlay for {key!r} suspiciously short "
            f"({len(overlay)} chars)"
        )

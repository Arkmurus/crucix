"""Tests for the verification gate (2026-04-18).

The gate double-checks CRITICAL outputs via provider disagreement.
Tests cover:
  - Severity classification (CRITICAL / HIGH / NORMAL)
  - Verdict extraction from free-text replies
  - Disagreement detection (BLOCKING / WARN / NONE)
  - End-to-end verify() agree + disagree paths
  - Endpoint wiring
"""
from __future__ import annotations

import asyncio


# ═══════════════════════════════════════════════════════════════════════
# 1. Severity classification
# ═══════════════════════════════════════════════════════════════════════

def test_red_dd_verdict_is_critical():
    from aria_service.learning.verification_gate import classify_severity
    text = "🔴 BOTTOM LINE — DO NOT PROCEED. Risk: RED. HARD_STOP."
    assert classify_severity(text) == "CRITICAL"


def test_no_go_is_critical():
    from aria_service.learning.verification_gate import classify_severity
    assert classify_severity("Final Verdict: NO-GO for engagement.") == "CRITICAL"


def test_client_facing_flag_is_critical():
    from aria_service.learning.verification_gate import classify_severity
    assert classify_severity("Routine note.", {"is_client_facing": True}) == "CRITICAL"


def test_amber_light_is_high():
    from aria_service.learning.verification_gate import classify_severity
    assert classify_severity("Rating: AMBER-LIGHT — proceed with enhanced DD") == "HIGH"


def test_green_clean_is_normal():
    from aria_service.learning.verification_gate import classify_severity
    assert classify_severity("All checks CLEAN. Standard engagement.") == "NORMAL"


# ═══════════════════════════════════════════════════════════════════════
# 2. Verdict extraction
# ═══════════════════════════════════════════════════════════════════════

def test_extracts_red_risk_tier():
    from aria_service.learning.verification_gate import extract_verdict
    v = extract_verdict("Risk: RED. Recommendation: HALT.")
    assert v["risk_tier"] == "RED"
    assert v["recommendation"] == "HALT"


def test_extracts_clean_sanctions_verdict():
    from aria_service.learning.verification_gate import extract_verdict
    v = extract_verdict("Sanctions screen: CLEAN ✅. No matches on OFAC or EU lists.")
    assert v["sanctions_verdict"] == "CLEAN"


def test_extracts_confidence_tag_footer():
    from aria_service.learning.verification_gate import extract_verdict
    v = extract_verdict(
        "Assessment: [ASSESSED] initially. Final: [CONFIRMED]."
    )
    # Should prefer the LAST tag (footer verdict)
    assert v["confidence_tag"] == "CONFIRMED"


def test_extracts_no_go_flag():
    from aria_service.learning.verification_gate import extract_verdict
    v = extract_verdict("Final: NO-GO. Do not proceed.")
    assert v["has_no_go"] is True


# ═══════════════════════════════════════════════════════════════════════
# 3. Disagreement detection
# ═══════════════════════════════════════════════════════════════════════

def test_identical_verdicts_agree():
    from aria_service.learning.verification_gate import detect_disagreement
    v = {"risk_tier": "RED", "sanctions_verdict": "HIT",
         "confidence_tag": "CONFIRMED", "recommendation": "HALT"}
    r = detect_disagreement(v, v)
    assert r["agree"] is True
    assert r["severity"] == "NONE"


def test_risk_tier_disagreement_is_blocking():
    from aria_service.learning.verification_gate import detect_disagreement
    p = {"risk_tier": "RED", "sanctions_verdict": None,
         "confidence_tag": "ASSESSED", "recommendation": None}
    s = {"risk_tier": "GREEN", "sanctions_verdict": None,
         "confidence_tag": "ASSESSED", "recommendation": None}
    r = detect_disagreement(p, s)
    assert r["agree"] is False
    assert r["severity"] == "BLOCKING"


def test_sanctions_verdict_disagreement_is_blocking():
    from aria_service.learning.verification_gate import detect_disagreement
    p = {"risk_tier": "AMBER", "sanctions_verdict": "HIT",
         "confidence_tag": None, "recommendation": None}
    s = {"risk_tier": "AMBER", "sanctions_verdict": "CLEAN",
         "confidence_tag": None, "recommendation": None}
    r = detect_disagreement(p, s)
    assert r["agree"] is False
    assert r["severity"] == "BLOCKING"


def test_confidence_tier_gap_of_2_is_warn():
    from aria_service.learning.verification_gate import detect_disagreement
    p = {"risk_tier": None, "sanctions_verdict": None,
         "confidence_tag": "CONFIRMED", "recommendation": None}
    s = {"risk_tier": None, "sanctions_verdict": None,
         "confidence_tag": "UNCERTAIN", "recommendation": None}
    r = detect_disagreement(p, s)
    assert r["severity"] == "WARN"


def test_recommendation_disagreement_is_warn():
    from aria_service.learning.verification_gate import detect_disagreement
    p = {"risk_tier": "AMBER", "sanctions_verdict": None,
         "confidence_tag": None, "recommendation": "PROCEED"}
    s = {"risk_tier": "AMBER", "sanctions_verdict": None,
         "confidence_tag": None, "recommendation": "HALT"}
    r = detect_disagreement(p, s)
    assert r["severity"] == "WARN"


def test_missing_fields_do_not_trigger_disagreement():
    """If only one side populated a field, don't flag — it's not a
    disagreement, just incomplete coverage."""
    from aria_service.learning.verification_gate import detect_disagreement
    p = {"risk_tier": "AMBER", "sanctions_verdict": "CLEAN",
         "confidence_tag": "ASSESSED", "recommendation": "PROCEED"}
    s = {"risk_tier": "AMBER", "sanctions_verdict": None,
         "confidence_tag": "ASSESSED", "recommendation": None}
    r = detect_disagreement(p, s)
    assert r["agree"] is True


# ═══════════════════════════════════════════════════════════════════════
# 4. End-to-end verify()
# ═══════════════════════════════════════════════════════════════════════

def test_verify_normal_severity_skips_gate():
    from aria_service.learning import verification_gate as vg

    async def run():
        return await vg.verify(
            "Standard reply, nothing critical.",
            "Similar standard reply.",
        )

    r = asyncio.run(run())
    assert r["verdict"] == "NOT_REQUIRED"


def test_verify_agree_returns_verified():
    from aria_service.learning import verification_gate as vg
    primary   = "🔴 RED. HARD_STOP. Sanctions: HIT. Recommendation: HALT. [CONFIRMED]"
    secondary = "Risk classification: RED. HARD_STOP. HIT on OFAC. HALT. [CONFIRMED]"

    async def run():
        return await vg.verify(primary, secondary)

    r = asyncio.run(run())
    assert r["verdict"] == "CRITICAL_VERIFIED"
    assert r["disagreement"]["agree"] is True


def test_verify_blocking_disagreement():
    from aria_service.learning import verification_gate as vg
    primary   = "Risk: RED. HARD_STOP. NO-GO for engagement."
    secondary = "Risk: GREEN. CLEAN. Standard engagement allowed."

    async def run():
        return await vg.verify(primary, secondary)

    r = asyncio.run(run())
    assert r["verdict"] == "CRITICAL_UNVERIFIED"
    assert r["disagreement"]["severity"] == "BLOCKING"
    assert "DO NOT auto-send" in r["recommendation"]


def test_verify_warn_disagreement():
    from aria_service.learning import verification_gate as vg
    primary   = "Risk: RED. NO-GO. [CONFIRMED]"
    secondary = "Risk: RED. NO-GO. [UNCERTAIN]"

    async def run():
        return await vg.verify(primary, secondary)

    r = asyncio.run(run())
    # BLOCKING on confidence tier with gap >1? Let's check: CONFIRMED(5) vs
    # UNCERTAIN(2) is a gap of 3, which is WARN.
    assert r["verdict"] == "CRITICAL_UNVERIFIED"
    assert r["disagreement"]["severity"] == "WARN"


# ═══════════════════════════════════════════════════════════════════════
# 5. Stats
# ═══════════════════════════════════════════════════════════════════════

def test_stats_shape():
    from aria_service.learning import verification_gate as vg

    async def run():
        return await vg.get_stats()

    s = asyncio.run(run())
    for k in ("verified_24h", "unverified_24h",
              "blocking_disagreements_24h", "warn_disagreements_24h",
              "verification_rate", "recent"):
        assert k in s


def test_summary_shape():
    from aria_service.learning import verification_gate as vg
    s = vg.summary()
    assert "severity_tiers" in s
    assert "compared_fields" in s
    assert "risk_tier" in s["compared_fields"]
    assert "sanctions_verdict" in s["compared_fields"]

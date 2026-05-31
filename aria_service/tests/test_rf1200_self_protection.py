"""R-F1200 — Self-protection guardrails: honesty, confidence, destructive action.

Unit tests for the three self-protection layers.
"""
from __future__ import annotations

from aria_service.intel.self_protection import (
    check_honesty,
    check_confidence,
    check_destructive_action,
    check_all,
)


# ── Layer 1: Honesty ────────────────────────────────────────────────────────

def test_honesty_clean_response_passes():
    """A well-sourced response with no unsupported claims passes."""
    r = check_honesty(
        "Based on the sources fetched, the contract was awarded in 2025.",
        tool_context="https://example.com/contract-2025",
    )
    assert r.passed
    assert not r.violations


def test_honesty_absolute_claim_warns():
    """Absolute claims like 'always' or 'never' generate warnings."""
    r = check_honesty("This always happens in every case.")
    assert r.passed  # warnings only, not violations
    assert len(r.warnings) >= 1
    assert any("absolute" in w.lower() for w in r.warnings)


def test_honesty_uncited_url_warns():
    """URLs in the response that weren't fetched generate warnings."""
    r = check_honesty(
        "Source: https://example.com/claim",
        tool_context="https://other-source.com/data",
    )
    assert r.passed
    assert any("cited URL" in w for w in r.warnings)


def test_honesty_cited_url_matches():
    """URLs that were actually fetched pass cleanly."""
    r = check_honesty(
        "Source: https://example.com/data",
        tool_context="https://example.com/data was fetched",
    )
    assert r.passed
    assert not any("cited URL" in w for w in r.warnings)


def test_honesty_vague_attribution_warns():
    """Vague attributions like 'sources say' generate warnings."""
    r = check_honesty("Sources say the deal is imminent.")
    assert r.passed
    assert any("vague" in w.lower() for w in r.warnings)


# ── Layer 2: Confidence ─────────────────────────────────────────────────────

def test_confidence_low_with_few_sources_passes():
    """LOW confidence with few sources is fine."""
    r = check_confidence("Confidence: LOW", source_count=0)
    assert r.passed


def test_confidence_confirmed_requires_sources():
    """CONFIRMED requires 3+ independent sources."""
    r = check_confidence("Confidence: CONFIRMED", source_count=1)
    assert not r.passed
    assert any("CONFIRMED" in v for v in r.violations)


def test_confidence_confirmed_with_enough_sources_passes():
    """CONFIRMED with 3+ sources and high verification rate passes."""
    r = check_confidence("Confidence: CONFIRMED", source_count=3, verification_rate=0.9)
    assert r.passed


def test_confidence_confirmed_low_verification_fails():
    """CONFIRMED with low verification rate fails."""
    r = check_confidence("Confidence: CONFIRMED", source_count=3, verification_rate=0.5)
    assert not r.passed
    assert any("verification" in v.lower() for v in r.violations)


def test_confidence_probable_requires_two_sources():
    """PROBABLE requires 2+ sources."""
    r = check_confidence("Confidence: PROBABLE", source_count=0)
    assert not r.passed


# ── Layer 3: Destructive Action ─────────────────────────────────────────────

def test_destructive_action_clean_passes():
    """Normal actions pass."""
    r = check_destructive_action("Read the file and return the contents")
    assert r.passed


def test_destructive_action_delete_flagged():
    """Delete operations are flagged."""
    r = check_destructive_action("Delete the database table")
    assert not r.passed
    assert any("destructive" in v.lower() for v in r.violations)


def test_destructive_action_credential_change_flagged():
    """Credential changes are flagged."""
    r = check_destructive_action("Rotate the API secret key")
    assert not r.passed


def test_destructive_action_rm_rf_flagged():
    """rm -rf is flagged."""
    r = check_destructive_action("Run rm -rf /data")
    assert not r.passed


# ── Composite ───────────────────────────────────────────────────────────────

def test_composite_all_clean_passes():
    """All layers clean passes."""
    r = check_all(
        response_text="Based on sources, the value is X.",
        tool_context="https://example.com/x",
        action_description="Read the file",
        source_count=3,
        verification_rate=0.9,
    )
    assert r.passed


def test_composite_destructive_action_fails():
    """Destructive action fails the composite check."""
    r = check_all(
        response_text="Confidence: CONFIRMED",
        action_description="Delete everything",
        source_count=3,
        verification_rate=0.9,
    )
    assert not r.passed
    assert any("destructive" in v.lower() for v in r.violations)


def test_composite_overconfidence_fails():
    """Overconfident claim with few sources fails."""
    r = check_all(
        response_text="Confidence: CONFIRMED. This always happens.",
        source_count=0,
    )
    assert not r.passed

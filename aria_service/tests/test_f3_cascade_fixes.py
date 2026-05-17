"""Tests for the three F3-cascade fixes (2026-04-17 21:45-21:55):

  1. Entity-name sanity gate (dd_orchestrator._validate_entity_name)
  2. Defective-run quarantine (run_quarantine)
  3. Sanctions-claim guard (sanctions_claim_guard)
"""
from __future__ import annotations

import asyncio

import pytest


# ═══════════════════════════════════════════════════════════════════════
# 1. Entity-name sanity gate
# ═══════════════════════════════════════════════════════════════════════

def test_validator_rejects_url_scheme_fragments():
    from aria_service.intel.dd_orchestrator import _validate_entity_name
    for bad in ("https", "http", "https:", "http://", "ftp"):
        ok, reason = _validate_entity_name(bad)
        assert ok is False, f"{bad!r} should be rejected"
        assert reason, "reject must carry a reason"


def test_validator_accepts_domain_name():
    from aria_service.intel.dd_orchestrator import _validate_entity_name
    ok, _ = _validate_entity_name("f3ir.com")
    assert ok is True


def test_validator_accepts_ordinary_company_name():
    from aria_service.intel.dd_orchestrator import _validate_entity_name
    ok, _ = _validate_entity_name("Rheinmetall AG")
    assert ok is True


def test_validator_rejects_empty_and_whitespace():
    from aria_service.intel.dd_orchestrator import _validate_entity_name
    ok, _ = _validate_entity_name("")
    assert ok is False
    ok, _ = _validate_entity_name("   ")
    assert ok is False


def test_validator_rejects_pure_punctuation():
    from aria_service.intel.dd_orchestrator import _validate_entity_name
    ok, _ = _validate_entity_name("!!!")
    assert ok is False


def test_validator_rejects_too_short_lowercase_no_dot():
    from aria_service.intel.dd_orchestrator import _validate_entity_name
    ok, _ = _validate_entity_name("abc")
    assert ok is False


def test_validator_accepts_short_with_dot():
    """Short domain-like strings with a dot are valid (e.g. 'a.co' shouldn't
    get through, but 'ge.de' — a real 2-letter brand on a ccTLD — should)."""
    from aria_service.intel.dd_orchestrator import _validate_entity_name
    ok, _ = _validate_entity_name("ge.de")
    assert ok is True


def test_orchestrate_dd_raises_on_malformed_entity():
    """End-to-end: orchestrate_dd must raise cleanly on bad entity name
    rather than running the 7-layer pipeline and poisoning mem0.

    R-F659 update (2026-05-17): type=company added so the new entity-type
    whitelist gate doesn't shadow the URL-fragment check this test
    targets. The test's intent is preserved — bad name still raises."""
    from aria_service.intel import dd_orchestrator

    async def run():
        return await dd_orchestrator.orchestrate_dd({"name": "https", "type": "company"})

    with pytest.raises(ValueError) as excinfo:
        asyncio.run(run())
    msg = str(excinfo.value)
    assert "malformed" in msg.lower() or "scheme" in msg.lower()


# ═══════════════════════════════════════════════════════════════════════
# 2. Defective-run quarantine
# ═══════════════════════════════════════════════════════════════════════

def test_quarantine_seeded_with_tonight_bad_runs():
    """The three F3 incident run_ids must be pre-seeded."""
    from aria_service.intel import run_quarantine
    assert run_quarantine.is_quarantined_sync("dd_30477701e537")
    assert run_quarantine.is_quarantined_sync("dd_adc7c7f87e4a")
    assert run_quarantine.is_quarantined_sync("dd_07f45b072b9f")


def test_quarantine_clean_run_not_quarantined():
    from aria_service.intel import run_quarantine
    assert not run_quarantine.is_quarantined_sync("dd_cleanrun0001")


def test_filter_citations_scrubs_quarantined_markers():
    from aria_service.intel import run_quarantine
    body = (
        "Prior DD found sanctions hit [from dd_orchestrate:dd_30477701e537] "
        "against the entity. Another finding [from dd_orchestrate:dd_cleanrun001] "
        "was clean."
    )
    cleaned, found = run_quarantine.filter_citations_sync(body)
    assert "dd_30477701e537" in found
    assert "CITATION-QUARANTINED" in cleaned
    # Clean run stays intact
    assert "dd_cleanrun001" in cleaned


def test_filter_citations_empty_text():
    from aria_service.intel import run_quarantine
    cleaned, found = run_quarantine.filter_citations_sync("")
    assert cleaned == ""
    assert found == []


def test_quarantine_summary_lists_seeds():
    from aria_service.intel import run_quarantine
    s = run_quarantine.summary()
    assert s["seeded_quarantined"] >= 3
    assert "dd_30477701e537" in s["seed_run_ids"]


# ═══════════════════════════════════════════════════════════════════════
# 3. Sanctions-claim guard
# ═══════════════════════════════════════════════════════════════════════

def test_detects_basic_sanctions_yes_no():
    from aria_service.intel.sanctions_claim_guard import detect_sanctions_question
    entity = detect_sanctions_question("Is Acme Industries sanctioned?")
    assert entity is not None
    assert "Acme" in entity


def test_detects_on_ofac_phrasing():
    from aria_service.intel.sanctions_claim_guard import detect_sanctions_question
    entity = detect_sanctions_question("is F3 International Resources LLC on OFAC?")
    assert entity is not None
    assert "F3" in entity


def test_detects_debarred_phrasing():
    from aria_service.intel.sanctions_claim_guard import detect_sanctions_question
    entity = detect_sanctions_question("has Vision Corp been debarred")
    assert entity is not None
    assert "Vision Corp" in entity


def test_rejects_what_question():
    """What questions are exploratory, not yes/no — must NOT trigger."""
    from aria_service.intel.sanctions_claim_guard import detect_sanctions_question
    entity = detect_sanctions_question("What sanctions apply to Iran?")
    assert entity is None


def test_rejects_why_question():
    from aria_service.intel.sanctions_claim_guard import detect_sanctions_question
    entity = detect_sanctions_question("Why was Rosoboronexport sanctioned?")
    assert entity is None


def test_rejects_non_sanctions_question():
    from aria_service.intel.sanctions_claim_guard import detect_sanctions_question
    entity = detect_sanctions_question("Can you run a DD on Baykar?")
    assert entity is None


def test_guard_context_block_empty_for_non_sanctions_question(monkeypatch):
    from aria_service.intel import sanctions_claim_guard

    async def run():
        return await sanctions_claim_guard.guard_context_block("Hello, how are you?")

    assert asyncio.run(run()) == ""


def test_guard_context_block_degrades_when_tool_unavailable(monkeypatch):
    """When the sanctions module can't be imported, the guard must emit
    a DEGRADED block so the LLM knows to not answer yes/no from recall."""
    from aria_service.intel import sanctions_claim_guard

    async def _fake_check(entity):
        return {
            "entity": entity, "ran_live": False, "verdict": "unknown",
            "matches": [], "top_match": None, "source_tool": "",
            "citation_block": "", "error": "sanctions module missing",
        }

    monkeypatch.setattr(sanctions_claim_guard, "live_primary_check", _fake_check)

    async def run():
        return await sanctions_claim_guard.guard_context_block(
            "Is F3 International Resources LLC sanctioned?"
        )

    block = asyncio.run(run())
    assert block
    assert "DEGRADED" in block
    assert "F3" in block


def test_guard_context_block_clean_verdict_instructs_no_answer(monkeypatch):
    from aria_service.intel import sanctions_claim_guard

    async def _clean_check(entity):
        return {
            "entity": entity, "ran_live": True, "verdict": "CLEAN",
            "matches": [], "top_match": None,
            "source_tool": "sanctions.fuzzy_screen",
            "citation_block": (
                f"[SANCTIONS LIVE CHECK — AUTHORITATIVE]\n"
                f"Entity: {entity}\n"
                f"Verdict: NO MATCHES\n"
                f"Answer policy: answer NO"
            ),
            "error": "",
        }

    monkeypatch.setattr(sanctions_claim_guard, "live_primary_check", _clean_check)

    async def run():
        return await sanctions_claim_guard.guard_context_block(
            "Is F3 International Resources LLC sanctioned?"
        )

    block = asyncio.run(run())
    assert "AUTHORITATIVE" in block
    assert "NO MATCHES" in block
    assert "Answer policy" in block

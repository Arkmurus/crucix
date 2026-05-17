"""R-F659 — DD pipeline hard input validation (entity_type + name heuristics).

Design-analysis 2026-05-17: "nothing without entity_type IN (org, person, oem)
and a passing name-heuristic enters the DD pipeline."

Pre-R-F659 the orchestrator accepted any target dict that had a non-empty
name field — entity_type defaulted to EntityType.UNKNOWN.value and the
pipeline ran anyway. That meant chat queries containing tokens like
"acme widgets", "283 limited", "2026 corp" could trigger a full 7-layer
DD run that produced noise findings poisoning mem0 + claim_ledger.

Fix is two gates at the top of orchestrate_dd:
  1. _validate_entity_type_for_dd — entity_type must be set AND in the
     whitelist (excludes UNKNOWN).
  2. _validate_entity_name_extras — name's first token must not be
     purely numeric, must not be an RFC 2606 placeholder, must not be a
     hyphenated-all-placeholder label.

This is the Phase A symptom-fix slice of the learning-controller buildout.
The defensive ingest principle keeps cloud-LLM-driven DD runs from being
triggered on garbage, which matters for both honesty (gate #6 evidence
quality) and cost (each junk DD = ~$0.10-1 of cloud LLM).
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel.dd_orchestrator import (
    _validate_entity_type_for_dd,
    _validate_entity_name_extras,
    _DD_VALID_ENTITY_TYPES,
    _DD_PLACEHOLDER_TOKENS,
    orchestrate_dd,
)


# ── entity_type whitelist ─────────────────────────────────────────────────

@pytest.mark.parametrize("type_val", [
    "company", "person", "address", "vessel", "aircraft",
    "organisation", "organization", "oem",
    "Company",   # case-insensitive
    "  PERSON  ",  # whitespace-tolerant
])
def test_rf659_type_accepts_whitelisted(type_val):
    ok, reason = _validate_entity_type_for_dd({"type": type_val, "name": "Acme Corp"})
    assert ok is True, f"R-F659 false-negative: {type_val!r} rejected with {reason!r}"


@pytest.mark.parametrize("type_val,expected_reason_fragment", [
    (None,           "missing"),
    ("",             "missing"),
    ("unknown",      "UNKNOWN"),
    ("UNKNOWN",      "UNKNOWN"),
    ("Unknown",      "UNKNOWN"),
    ("foobar",       "not in allowed set"),
    ("ip_address",   "not in allowed set"),
    ("currency",     "not in allowed set"),
])
def test_rf659_type_rejects_unknown_and_unwhitelisted(type_val, expected_reason_fragment):
    target = {"type": type_val, "name": "Acme Corp"}
    ok, reason = _validate_entity_type_for_dd(target)
    assert ok is False, f"R-F659 false-positive: {type_val!r} accepted"
    assert expected_reason_fragment.lower() in reason.lower()


def test_rf659_type_rejects_non_dict_target():
    ok, _ = _validate_entity_type_for_dd("not a dict")  # type: ignore[arg-type]
    assert ok is False
    ok, _ = _validate_entity_type_for_dd(None)  # type: ignore[arg-type]
    assert ok is False


# ── name extras (numeric + placeholder) ───────────────────────────────────

@pytest.mark.parametrize("name", [
    # Pure-digit first tokens (catches "283 limited", "2026 corp")
    "283",
    "283 limited",
    "2026 corp",
    "999 holdings",
    # Placeholder first tokens
    "acme",
    "acme corp",
    "acme limited",
    "widgets",
    "widgets inc",
    "test ltd",
    "example holdings",
    "foo company",
    "demo gmbh",
    # Hyphenated all-placeholder labels
    "acme-widgets",
    "foo-bar",
    "test-demo",
    # Case + whitespace insensitivity
    "  ACME  CORP",
    "Test Limited",
])
def test_rf659_name_extras_rejects_garbage(name):
    ok, reason = _validate_entity_name_extras(name)
    assert ok is False, f"R-F659 false-positive: {name!r} should fail extras, reason={reason!r}"


@pytest.mark.parametrize("name", [
    # Real defence-broking entity names — must pass
    "Modirum GESPI",
    "Rostec",
    "BAE Systems",
    "BAE Systems plc",
    "Rheinmetall AG",
    "Lockheed Martin",
    "Embraer S.A.",
    "Arkmurus Ltd",
    "F3 International",
    "F3IR",
    "DSEi 2025",                   # number after a real name — first-token is alpha, passes
    "Section 38 Holdings",         # number not first
    # Edge: bae-systems hyphenated where "bae" isn't a placeholder
    "bae-systems",
])
def test_rf659_name_extras_accepts_real_entities(name):
    ok, reason = _validate_entity_name_extras(name)
    assert ok is True, f"R-F659 false-negative: {name!r} rejected with {reason!r}"


def test_rf659_name_extras_rejects_empty():
    ok, _ = _validate_entity_name_extras("")
    assert ok is False
    ok, _ = _validate_entity_name_extras("   ")
    assert ok is False


# ── orchestrate_dd end-to-end gate behaviour ──────────────────────────────

def test_rf659_orchestrate_refuses_missing_type():
    """Calling orchestrate_dd without target['type'] must raise ValueError
    citing R-F659 before any layer executes."""
    async def runner():
        await orchestrate_dd({"name": "Acme Corp"})  # no type
    with pytest.raises(ValueError, match=r"R-F659.*unclassified|entity_type missing"):
        asyncio.run(runner())


def test_rf659_orchestrate_refuses_unknown_type():
    async def runner():
        await orchestrate_dd({"name": "Acme Corp", "type": "unknown"})
    with pytest.raises(ValueError, match=r"R-F659.*UNKNOWN|UNKNOWN refused"):
        asyncio.run(runner())


def test_rf659_orchestrate_refuses_invalid_type_value():
    async def runner():
        await orchestrate_dd({"name": "Acme Corp", "type": "currency"})
    with pytest.raises(ValueError, match=r"R-F659.*not in allowed set"):
        asyncio.run(runner())


def test_rf659_orchestrate_refuses_numeric_name():
    """Even with a valid type, a pure-numeric first-token name must
    trigger the name-extras gate."""
    async def runner():
        await orchestrate_dd({"name": "283 limited", "type": "company"})
    with pytest.raises(ValueError, match=r"R-F659.*placeholder/numeric|purely numeric"):
        asyncio.run(runner())


def test_rf659_orchestrate_refuses_placeholder_name():
    async def runner():
        await orchestrate_dd({"name": "Acme Widgets", "type": "company"})
    with pytest.raises(ValueError, match=r"R-F659.*placeholder"):
        asyncio.run(runner())


def test_rf659_orchestrate_refuses_hyphenated_placeholder():
    async def runner():
        await orchestrate_dd({"name": "acme-widgets", "type": "company"})
    with pytest.raises(ValueError, match=r"R-F659.*placeholder"):
        asyncio.run(runner())


# ── Whitelist set sanity ──────────────────────────────────────────────────

def test_rf659_whitelist_covers_core_types():
    """The whitelist must include the canonical (org, person, oem) trio
    plus the existing legitimate DD targets we don't want to break."""
    for t in ("company", "person", "oem"):
        assert t in _DD_VALID_ENTITY_TYPES, f"R-F659: {t!r} missing from whitelist"


def test_rf659_placeholder_set_covers_live_cases():
    """Mirror R-F654 — the placeholder set must catch the words the
    crawler leaked through on 2026-05-17."""
    for word in ("acme", "widgets", "test", "example", "foo", "bar", "demo"):
        assert word in _DD_PLACEHOLDER_TOKENS

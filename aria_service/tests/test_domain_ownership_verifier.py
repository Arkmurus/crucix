"""Tests for domain_ownership_verifier.

Covers:
  1. Domain extraction from various URL + raw-domain shapes.
  2. RDAP parser — entity extraction, privacy redaction detection.
  3. Cross-reference logic — token overlap, country mismatch, age.
  4. Severity mapping — which flags map to red/amber/info.
  5. Graceful degradation when RDAP is unreachable.
"""
from __future__ import annotations

import asyncio


# ═══════════════════════════════════════════════════════════════════════
# 1. Domain extraction
# ═══════════════════════════════════════════════════════════════════════

def test_extract_domain_from_https_url():
    from aria_service.intel import domain_ownership_verifier as dov
    assert dov.extract_domain("https://f3ir.com/") == "f3ir.com"


def test_extract_domain_strips_www():
    from aria_service.intel import domain_ownership_verifier as dov
    assert dov.extract_domain("https://www.example.co.uk/path") == "example.co.uk"


def test_extract_domain_from_raw_host():
    from aria_service.intel import domain_ownership_verifier as dov
    assert dov.extract_domain("f3ir.com") == "f3ir.com"


def test_extract_domain_rejects_garbage():
    from aria_service.intel import domain_ownership_verifier as dov
    assert dov.extract_domain("") is None
    assert dov.extract_domain("not a url") is None
    assert dov.extract_domain("a") is None


# ═══════════════════════════════════════════════════════════════════════
# 2. RDAP parser
# ═══════════════════════════════════════════════════════════════════════

def test_rdap_parser_extracts_registrant():
    from aria_service.intel import domain_ownership_verifier as dov
    rdap = {
        "entities": [
            {
                "roles": ["registrant"],
                "vcardArray": ["vcard", [
                    ["version", {}, "text", "4.0"],
                    ["fn", {}, "text", "Jane Doe"],
                    ["org", {}, "text", "Acme Industries Ltd"],
                    ["adr", {}, "text",
                     ["", "", "1 Infinite Loop", "Cupertino", "CA", "95014", "US"]],
                    ["email", {}, "text", "jane@acme.com"],
                ]],
            },
            {
                "roles": ["registrar"],
                "vcardArray": ["vcard", [
                    ["fn", {}, "text", "MarkMonitor Inc."],
                ]],
            },
        ],
        "events": [
            {"eventAction": "registration", "eventDate": "2015-03-20T00:00:00Z"},
        ],
    }
    info = dov._extract_entity_info(rdap)
    assert info["registrant_name"] == "Jane Doe"
    assert info["registrant_organisation"] == "Acme Industries Ltd"
    assert info["registrant_country"] == "US"
    assert info["registrar"] == "MarkMonitor Inc."
    assert info["creation_date"] == "2015-03-20T00:00:00Z"
    assert info["privacy_redacted"] is False


def test_rdap_parser_detects_privacy_redaction():
    from aria_service.intel import domain_ownership_verifier as dov
    rdap = {
        "entities": [
            {
                "roles": ["registrant"],
                "vcardArray": ["vcard", [
                    ["fn", {}, "text", "REDACTED FOR PRIVACY"],
                ]],
            },
        ],
        "events": [],
    }
    info = dov._extract_entity_info(rdap)
    assert info["privacy_redacted"] is True
    assert info["registrant_name"] is None


# ═══════════════════════════════════════════════════════════════════════
# 3. Cross-reference logic
# ═══════════════════════════════════════════════════════════════════════

def test_token_overlap_complete_match():
    from aria_service.intel import domain_ownership_verifier as dov
    assert dov._token_overlap("Acme Industries", "Acme Industries Ltd") > 0.5


def test_token_overlap_no_match():
    from aria_service.intel import domain_ownership_verifier as dov
    # SERBAN claims F3 International Resources but domain is owned by
    # a completely unrelated entity. This is the F3 case.
    assert dov._token_overlap("F3 International Resources", "John Smith Personal Website") < 0.2


def test_age_days_recent_domain():
    from aria_service.intel import domain_ownership_verifier as dov
    from datetime import datetime, timezone, timedelta
    recent = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()
    assert dov._age_days(recent) == 15


def test_age_days_none_on_bad_input():
    from aria_service.intel import domain_ownership_verifier as dov
    assert dov._age_days(None) is None
    assert dov._age_days("not a date") is None


# ═══════════════════════════════════════════════════════════════════════
# 4. Severity mapping
# ═══════════════════════════════════════════════════════════════════════

def test_severity_entity_mismatch_is_red():
    from aria_service.intel import domain_ownership_verifier as dov
    assert dov.severity_for({"flags": ["REGISTRANT_ENTITY_MISMATCH"]}) == "red"


def test_severity_domain_not_registered_is_red():
    from aria_service.intel import domain_ownership_verifier as dov
    assert dov.severity_for({"flags": ["DOMAIN_NOT_REGISTERED"]}) == "red"


def test_severity_recently_registered_is_amber():
    from aria_service.intel import domain_ownership_verifier as dov
    assert dov.severity_for({"flags": ["VERY_RECENTLY_REGISTERED"]}) == "amber"
    assert dov.severity_for({"flags": ["RECENTLY_REGISTERED"]}) == "amber"


def test_severity_empty_flags_is_info():
    from aria_service.intel import domain_ownership_verifier as dov
    assert dov.severity_for({"flags": []}) == "info"


# ═══════════════════════════════════════════════════════════════════════
# 5. Graceful degradation
# ═══════════════════════════════════════════════════════════════════════

def test_verify_domain_returns_dict_even_on_bad_input():
    from aria_service.intel import domain_ownership_verifier as dov

    async def run():
        return await dov.verify_domain("")

    r = asyncio.run(run())
    assert r["verified"] is False
    assert "reason" in r


def test_render_finding_returns_string():
    from aria_service.intel import domain_ownership_verifier as dov
    s = dov.render_finding({
        "verified": True,
        "domain": "example.com",
        "registrar": "Test Inc.",
        "creation_date": "2020-01-01",
        "age_days": 1000,
        "flags": ["REGISTRANT_REDACTED"],
        "registrant_organisation": None,
        "registrant_name": None,
        "privacy_redacted": True,
    })
    assert isinstance(s, str)
    assert "example.com" in s


# ═══════════════════════════════════════════════════════════════════════
# 6. SERBAN / F3 regression scenario (unit-level)
# ═══════════════════════════════════════════════════════════════════════

def test_f3_scenario_mismatch_flag_fires():
    """Simulate RDAP returning 'John Doe Personal Site' as registrant
    while SERBAN claims F3 International Resources. Flag must fire."""
    from aria_service.intel import domain_ownership_verifier as dov
    overlap = dov._token_overlap(
        "F3 International Resources LLC",
        "John Doe Personal Site",
    )
    # Overlap should be under the 0.2 threshold used in verify_domain
    assert overlap < 0.2
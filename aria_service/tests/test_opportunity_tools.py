"""Tests for the BD-workflow tooling shipped 2026-04-20:

  - intel/prime_sub_map.py               defence prime → sub-contractor
  - intel/opportunity_converter.py       alert → structured brief
  - /opportunity chat-intent route       ties them together

All tests are network-free / deterministic. R-F1863 (2026-06-24):
the Airtable Pipeline writer was removed; convert() produces a brief
only and no longer pushes to any external surface.
"""
from __future__ import annotations

import asyncio


# ═══════════════════════════════════════════════════════════════════════
# prime_sub_map
# ═══════════════════════════════════════════════════════════════════════

def test_prime_sub_map_boeing_p8a_lookup():
    from aria_service.intel import prime_sub_map
    r = prime_sub_map.lookup("Boeing", "P-8")
    assert r["ok"] is True
    assert r["prime_key"] == "boeing"
    assert any("P-8" in p for p in r["matched_products"])
    # Arkmurus opportunities should surface Ultra + Leonardo + MRO
    opps = r["arkmurus_opportunities"]
    suppliers = {o["sub_contractor"].lower() for o in opps}
    assert any("ultra" in s for s in suppliers), (
        f"Ultra Electronics should appear in P-8 opportunities, got {suppliers}"
    )
    # HIGH fit must come before MEDIUM in the sort
    if len(opps) > 1:
        fits = [o["fit"] for o in opps]
        high_indices = [i for i, f in enumerate(fits) if f == "HIGH"]
        medium_indices = [i for i, f in enumerate(fits) if f == "MEDIUM"]
        if high_indices and medium_indices:
            assert max(high_indices) < min(medium_indices), (
                "HIGH-fit opportunities must sort before MEDIUM-fit"
            )


def test_prime_sub_map_alias_resolution():
    from aria_service.intel import prime_sub_map
    # "Lockheed Martin" should resolve via alias
    for variant in ("Lockheed Martin", "LM", "LMT", "lockheed"):
        r = prime_sub_map.lookup(variant)
        assert r["ok"] is True, f"alias {variant!r} did not resolve"
        assert r["prime_key"] == "lockheed_martin"


def test_prime_sub_map_variant_suffix_matches_family():
    """Regression 2026-04-20 live test: product='P-8A' fell through to
    ALL Boeing families (including Chinook) because the old substring
    match didn't handle the trailing variant letter. Chinook-supplier
    'Leonardo Helicopters (AgustaWestland)' leaked into the P-8A
    opportunities table. Fixed via stem-based matching in
    _product_matches."""
    from aria_service.intel import prime_sub_map
    opps = prime_sub_map.arkmurus_opportunities("boeing", "P-8A")
    suppliers = {o["sub_contractor"].lower() for o in opps}
    assert not any("agustawestland" in s or "chinook" in s for s in suppliers), (
        f"Chinook-only supplier leaked into P-8A opportunities: {suppliers}"
    )
    # And the real P-8 suppliers MUST still be there
    assert any("ultra" in s for s in suppliers)
    assert any("terma" in s for s in suppliers)


def test_prime_sub_map_unknown_prime_returns_not_mapped():
    from aria_service.intel import prime_sub_map
    r = prime_sub_map.lookup("NotARealPrime Defence Corp")
    assert r["ok"] is False
    assert r["reason"] == "not_mapped"


def test_prime_sub_map_list_primes():
    from aria_service.intel import prime_sub_map
    primes = prime_sub_map.list_primes()
    assert len(primes) >= 10
    for p in primes:
        assert "key" in p
        assert "aliases" in p
        assert "hq_country" in p
        assert "itar_regime" in p
        assert "product_families" in p
        assert p["itar_regime"] in ("US_ITAR", "UK_EXPORT", "EU_EXPORT", "OTHER")


# ═══════════════════════════════════════════════════════════════════════
# opportunity_converter — extraction
# ═══════════════════════════════════════════════════════════════════════

def test_opportunity_convert_detects_denmark_p8():
    from aria_service.intel import opportunity_converter as oc
    result = asyncio.run(oc.convert(
        "Defence Procurement: DSCA/FMS Denmark to acquire 3 P-8A patrol "
        "aircraft under possible US foreign military sale — "
        "defenceconnect.com.au"
    ))
    assert result["ok"] is True
    assert result["country"] and "denmark" in result["country"].lower()
    assert result["product_family"] and "p-8" in result["product_family"].lower().replace(" ", "")
    # P-8 → Boeing via prime_sub_map, even if Boeing isn't in the headline
    # (boeing regex doesn't match, but Arkmurus opportunities may still
    # surface when prime is explicitly hinted — verify via hint path)
    result_hinted = asyncio.run(oc.convert(
        "DSCA/FMS Denmark 3 P-8A patrol aircraft",
        prime_hint="boeing",
    ))
    assert result_hinted["ok"] is True
    assert result_hinted["prime"] == "boeing"
    assert len(result_hinted["sub_contractor_candidates"]) > 0
    # R-F1863: convert() no longer carries an "airtable" key.
    assert "airtable" not in result
    assert "airtable" not in result_hinted


def test_opportunity_convert_builds_readable_brief():
    from aria_service.intel import opportunity_converter as oc
    r = asyncio.run(oc.convert({
        "title": "UK orders 10 additional Typhoon from BAE Systems",
        "url": "https://example.gov.uk/press/typhoon-2026",
        "text": "UK orders 10 additional Typhoon Tranche 5 from BAE Systems",
    }))
    md = r["brief_markdown"]
    assert "Typhoon" in md
    assert "BAE" in md or "bae" in md.lower()
    assert "Signal fingerprint" in md
    assert "Arkmurus-credible sub-contractor angles" in md


def test_opportunity_convert_distinct_names_on_country_only():
    """Regression 2026-04-20 live: two different Mozambique alerts both
    produced opportunity_name='Mozambique' when no prime/product was
    detected. Fix: use a title descriptor from the alert text as the
    secondary discriminator."""
    from aria_service.intel import opportunity_converter as oc
    r1 = asyncio.run(oc.convert({
        "title": "Lusophone Africa: Great Lakes Security Sanctioning Rwanda",
        "text":  "Rwanda sanctions risk undermining Mozambique stability",
        "url":   "https://example.com/rwanda",
    }, country_hint="Mozambique"))
    r2 = asyncio.run(oc.convert({
        "title": "Lusophone Africa: Mozambique looks to China for industrial boost",
        "text":  "Chinese industrial investment",
        "url":   "https://example.com/china",
    }, country_hint="Mozambique"))
    assert r1["opportunity_name"] != r2["opportunity_name"], (
        f"Same name for different alerts: {r1['opportunity_name']!r} vs {r2['opportunity_name']!r}"
    )
    assert "Mozambique" in r1["opportunity_name"]
    assert "Mozambique" in r2["opportunity_name"]


def test_opportunity_convert_empty_returns_error():
    from aria_service.intel import opportunity_converter as oc
    r = asyncio.run(oc.convert(""))
    assert r["ok"] is False
    assert r["reason"] == "empty_alert"


# ═══════════════════════════════════════════════════════════════════════
# /opportunity chat intent
# ═══════════════════════════════════════════════════════════════════════

def test_opportunity_chat_intent_slash_command():
    from aria_service.routes.aria import _detect_tool_intent
    intent = _detect_tool_intent(
        "/opportunity Denmark to acquire 3 P-8A Poseidon via DSCA FMS"
    )
    assert intent is not None
    assert intent["tool"] == "opportunity_convert"
    # Alert text should have the /opportunity prefix stripped
    assert "Denmark" in intent["alert"]
    assert not intent["alert"].startswith("/opportunity")


def test_opportunity_chat_intent_natural_phrase():
    from aria_service.routes.aria import _detect_tool_intent
    intent = _detect_tool_intent(
        "Aria, convert this to a pipeline entry: DSCA/FMS Denmark to "
        "acquire 3 P-8A patrol aircraft"
    )
    assert intent is not None
    assert intent["tool"] == "opportunity_convert"


def test_opportunity_chat_intent_does_not_match_plain_chat():
    from aria_service.routes.aria import _detect_tool_intent
    # Neutral sentence — must NOT trigger opportunity_convert
    intent = _detect_tool_intent(
        "What is the status of our pipeline leads for Mozambique?"
    )
    if intent is not None:
        assert intent.get("tool") != "opportunity_convert"

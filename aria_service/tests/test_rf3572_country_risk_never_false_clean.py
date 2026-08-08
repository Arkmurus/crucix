"""R-F3572 — /api/aria/compliance/risk called every unlisted country LOW-RISK.

The endpoint classified from three hardcoded Python sets (EMBARGOED, HIGH_RISK,
MEDIUM_RISK) and its `else` branch returned::

    level, score = "LOW", 20
    notes = f"{country} is a low-risk destination. Standard export controls apply."

for everything else — which is most of the world. The sets enumerate RISK; they
say nothing about safety. So "absent from the list" means NOT CHECKED, and it was
rendered to the user as CHECKED AND CLEAR.

It is user-facing, not internal: `aria_wa_listener.mjs:1815` posts to this
endpoint and renders the reply as "🟢 Risk level: LOW", so a defence operator
asking about an uncovered destination was told by name that it was low-risk.

Found alongside a second defect: `intel/country_sanctions.py` holds 25 curated
SanctionsRegime records over 11 countries — real instruments, regime types,
exceptions, citations — and its own docstring calls `format_regime_answer` "the
PRIMARY answer for 'is [country] under sanctions?' questions". Nothing called it.
Verified repo-wide: the module was referenced only by its own tests, one
gap_type registry entry and one comment. The live endpoint served the generic
strings ["UN SC", "EU restrictive measures", "UK OFSI"] instead.
"""

from __future__ import annotations

import pytest

from aria_service.routes.aria import RiskRequest, compliance_risk_ep

# R-F3770/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so an edit mid-run silently returns a DIFFERENT function's body.
from ._source_probe import function_source


async def _risk(country: str) -> dict:
    return await compliance_risk_ep(RiskRequest(country=country))


@pytest.mark.asyncio
async def test_an_uncovered_country_is_unknown_not_low_risk():
    """THE DEFECT. Nepal is in none of the three sets and none of the curated
    regimes, so ARIA has made no determination about it."""
    result = await _risk("Nepal")

    assert result["risk_level"] == "UNKNOWN", (
        f"Nepal came back {result['risk_level']!r}. An unlisted country is "
        f"UNASSESSED; reporting LOW asserts a clean result never established."
    )
    assert result["assessed"] is False
    assert result["score"] is None, (
        "a fabricated numeric score (was 20/100) reads as a measurement"
    )
    assert "low-risk destination" not in result["notes"].lower()
    assert "not covered" in result["notes"].lower()


@pytest.mark.asyncio
async def test_the_unknown_verdict_reaches_the_whatsapp_contract_intact():
    """UNKNOWN is not a new contract — aria_wa_listener.mjs:1816 already reads
    `d.risk_level || d.level || 'UNKNOWN'` and its emoji map falls through to ⚪.
    Both fields must agree or the JS `||` silently picks the stale one."""
    result = await _risk("Nepal")
    assert result["risk_level"] == result["level"] == "UNKNOWN"
    # `if (d.score)` in the listener must not print a score line.
    assert not result["score"]
    assert result["export_controls"] == "Unknown — country not assessed"


@pytest.mark.asyncio
async def test_an_embargoed_country_still_reads_high():
    """The fix must not soften the verdicts that WERE correct."""
    result = await _risk("Russia")
    assert result["risk_level"] == "HIGH"
    assert result["assessed"] is True
    assert result["score"] and result["score"] >= 90


@pytest.mark.asyncio
async def test_the_curated_regimes_now_reach_the_answer():
    """The dark engine, connected. Before this the endpoint returned the three
    generic labels for every embargoed country alike."""
    result = await _risk("Iran")

    assert result["risk_level"] == "HIGH"
    detail = result.get("regime_detail") or []
    assert detail, "curated regime records did not reach the response"
    # Real instruments and citations, not a generic label.
    first = detail[0]
    assert first.get("source"), first
    assert first.get("regime_type"), first
    assert result["sanctions_regimes"] != ["UN SC", "EU restrictive measures", "UK OFSI"], (
        "the generic hardcoded labels are still being served"
    )


@pytest.mark.asyncio
async def test_an_uncovered_country_carries_no_curated_detail():
    """`regime_detail` empty is itself the honest signal that the answer is
    list-based rather than sourced."""
    result = await _risk("Nepal")
    assert result.get("regime_detail") == []


@pytest.mark.asyncio
async def test_a_broken_curated_lookup_cannot_fabricate_a_clean_answer(monkeypatch):
    """If the curated engine raises, the endpoint must still refuse to invent a
    LOW verdict — degrading to UNKNOWN, never to clean."""
    from aria_service.intel import country_sanctions

    def _boom(*a, **k):
        raise RuntimeError("curated index unavailable")

    monkeypatch.setattr(country_sanctions, "format_regime_answer", _boom)

    result = await _risk("Nepal")
    assert result["risk_level"] == "UNKNOWN"
    assert result["assessed"] is False


# ── The ISO derivation: a name prefix is not a country code ──────────────────


@pytest.mark.asyncio
async def test_an_unmapped_country_does_not_inherit_another_countrys_code():
    """THE SEVEREST OF THE THREE.

    `iso` fell back to `country_input.upper()[:2]`, so a country missing from the
    hand-map was classified on the first two letters of its NAME. Those collide
    with real ISO codes: "Nepal" -> "NE", which is NIGER, which is in HIGH_RISK.
    Nepal was therefore reported HIGH RISK by name while carrying another
    country's verdict — a confident answer about the wrong country.
    """
    result = await _risk("Nepal")
    assert result["iso"] != "NE", (
        "Nepal is still being given Niger's ISO code by name-prefix fallback"
    )
    assert result["risk_level"] == "UNKNOWN"


@pytest.mark.asyncio
@pytest.mark.parametrize("name,wrong_prefix", [
    ("Chile", "CH"),      # CH is Switzerland
    ("Sweden", "SW"),     # not an ISO2 at all
    ("Portugal", "PO"),   # not an ISO2 at all
])
async def test_the_prefix_fallback_is_gone_for_every_shape(name, wrong_prefix):
    result = await _risk(name)
    assert result["iso"] != wrong_prefix, (
        f"{name} resolved to {wrong_prefix!r} — the name-prefix fallback is back"
    )


@pytest.mark.asyncio
async def test_a_resolvable_country_still_resolves():
    """Guard against over-correcting into 'nothing resolves'."""
    assert (await _risk("Russia"))["iso"] == "RU"
    assert (await _risk("Chile"))["iso"] == "CL"      # via country_taxonomy
    assert (await _risk("Iran"))["iso"] == "IR"       # via the endpoint's own map


@pytest.mark.asyncio
async def test_a_two_letter_code_is_still_accepted_directly():
    """The endpoint has always accepted an ISO2 directly; that path must survive."""
    result = await _risk("RU")
    assert result["iso"] == "RU"
    assert result["risk_level"] == "HIGH"


@pytest.mark.asyncio
async def test_an_unresolvable_country_cannot_match_any_risk_set():
    """The '??' sentinel must not collide with a real code in any of the three
    sets — otherwise every unknown country would inherit that set's verdict,
    which is the defect in a new costume."""
    from aria_service.routes import aria as _aria_routes
    import inspect

    src = function_source(_aria_routes, "compliance_risk_ep")
    for token in ('EMBARGOED = {', 'HIGH_RISK = {', 'MEDIUM_RISK = {'):
        assert token in src, f"{token} moved; this guard needs updating"
    assert '"??"' not in src.split('EMBARGOED = {')[1].split('}')[0]

    result = await _risk("Zzzqxland")
    assert result["iso"] == "??"
    assert result["risk_level"] == "UNKNOWN"
    assert result["assessed"] is False

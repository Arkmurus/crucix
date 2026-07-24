"""R-F3014 — resolving a GB company by name must not blindly take results[0].

Live defect (Cohort DD dd_16ea006830ac): a name search for "Cohort plc" returned
an Overseas Entity (ROE, OE003509 — a Jersey record) ahead of the real trading
company (05684823, the LSE defence group). investigate_uk_entity took results[0],
so the whole officer/UBO walk ran on OE003509 → "no officers", empty ownership,
and the report carried THREE conflicting registration numbers for one subject.

Fix: _pick_best_company ranks by distinctive-name match, then prefers a
non-overseas active trading company, then search rank — falling back to an overseas
entity only when it is genuinely the best hit.
"""
import asyncio
from unittest.mock import patch, AsyncMock

from aria_service.intel import companies_house as ch


def test_rf3014_overseas_entity_detected():
    assert ch._is_overseas_entity({"company_number": "OE003509"})
    assert ch._is_overseas_entity({"company_number": "05684823",
                                   "company_type": "registered-overseas-entity"})
    assert not ch._is_overseas_entity({"company_number": "05684823", "company_type": "plc"})


def test_rf3014_name_match_ignores_generic_suffixes():
    assert ch._company_name_match("Cohort plc", "COHORT PLC") == 1.0
    assert ch._company_name_match("Cohort plc", "AVIVA PLC") == 0.0


def test_rf3014_prefers_trading_company_over_same_named_overseas_entity():
    results = [
        {"company_number": "OE003509", "title": "COHORT PLC",
         "company_status": "registered", "company_type": "registered-overseas-entity"},
        {"company_number": "05684823", "title": "COHORT PLC",
         "company_status": "active", "company_type": "plc"},
    ]
    best = ch._pick_best_company("Cohort plc", results)
    assert best["company_number"] == "05684823", \
        "same-named OE must lose to the real trading company (was OE003509 — the bug)"


def test_rf3014_falls_back_to_overseas_when_only_match():
    results = [{"company_number": "OE009999", "title": "ACME OVERSEAS LTD",
                "company_status": "registered", "company_type": "registered-overseas-entity"}]
    best = ch._pick_best_company("Acme Overseas Ltd", results)
    assert best["company_number"] == "OE009999", "an OE that IS the best hit is still used"


def test_rf3014_stronger_name_match_beats_non_overseas():
    # a non-overseas stranger must NOT win over a real name match
    results = [
        {"company_number": "09999999", "title": "COHORT SECURITY SYSTEMS LTD",
         "company_status": "active", "company_type": "ltd"},
        {"company_number": "05684823", "title": "COHORT PLC",
         "company_status": "active", "company_type": "plc"},
    ]
    best = ch._pick_best_company("Cohort plc", results)
    assert best["company_number"] == "05684823"


def test_rf3014_investigate_uk_entity_resolves_the_trading_company():
    """Capability test on the actual broken path: investigate_uk_entity must fetch
    the profile for the TRADING company, not the overseas entity."""
    async def go():
        search = AsyncMock(return_value=[
            {"company_number": "OE003509", "title": "COHORT PLC",
             "company_status": "registered", "company_type": "registered-overseas-entity"},
            {"company_number": "05684823", "title": "COHORT PLC",
             "company_status": "active", "company_type": "plc"},
        ])
        with patch.object(ch, "is_enabled", return_value=True), \
             patch.object(ch, "search_companies", new=search), \
             patch.object(ch, "get_company_profile", new=AsyncMock(return_value=None)) as gp, \
             patch.object(ch, "get_officers", new=AsyncMock(return_value=[])), \
             patch.object(ch, "get_psc", new=AsyncMock(return_value=[])), \
             patch.object(ch, "get_filing_history", new=AsyncMock(return_value=[])):
            await ch.investigate_uk_entity(company_name="Cohort plc")
            assert gp.await_args.args[0] == "05684823", \
                "the walk must run on the trading company (05684823), not OE003509"
    asyncio.run(go())

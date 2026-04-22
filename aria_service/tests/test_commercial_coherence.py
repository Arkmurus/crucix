# =============================================================================
# ARIA — Layer 5c commercial coherence tests
# aria_service/tests/test_commercial_coherence.py
#
# 360° coverage for the Slice-1 Layer 5c build. No network, no LLM — the
# assessor is pure data-driven so these tests run in CI under a second.
# =============================================================================

import asyncio
import unittest
from datetime import datetime, timedelta, timezone

from aria_service.intel.commercial_coherence import (
    PAYMENT_NORMS,
    LICENCE_CHAIN_REQUIREMENTS,
    NEW_ENTITY_DAYS_THRESHOLD,
    assess_commercial_coherence,
    anomaly_text_for_deception,
    _extract_advance_payment_pct,
    _classify_tier,
    _match_licence_chain_shape,
    _infer_payment_market,
)
from aria_service.intel.dd_schema import (
    ARKDDReport,
    CommercialCoherenceSection,
    LayerStatus,
)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


def _make_report(
    *,
    entity_name: str = "Test Co",
    iso2: str = "GB",
    incorporation_date: str | None = None,
    directors: list | None = None,
    shareholders: list | None = None,
    declared_activity: str = "",
    registered_address: str = "",
) -> ARKDDReport:
    report = ARKDDReport()
    report.identity.entity_name = entity_name
    report.identity.jurisdiction_iso2 = iso2
    report.identity.incorporation_date = incorporation_date
    report.identity.directors = directors or []
    report.identity.shareholders = shareholders or []
    report.identity.declared_activity = declared_activity
    report.identity.registered_address = registered_address
    return report


# =============================================================================
# CLEAN-PATH: baseline
# =============================================================================

class TestCleanPath(unittest.TestCase):

    def test_clean_report_scores_green(self):
        report = _make_report(
            entity_name="Solid Defence Ltd",
            iso2="GB",
            incorporation_date="2010-03-15",
            directors=[{"name": "A"}, {"name": "B"}],
            shareholders=[{"name": "A"}, {"name": "C"}],
            declared_activity="Defence supplies, manufacturing",
            registered_address="Unit 12, Industrial Park, Coventry",
        )
        target = {
            "name": "Solid Defence Ltd",
            "product_description": "defence electronics",
            "transaction_value_usd": 500_000,
        }
        _run(assess_commercial_coherence(target, report))

        section = report.commercial_coherence
        self.assertEqual(section.tier, "GREEN")
        self.assertGreaterEqual(section.coherence_score, 0.80)
        self.assertEqual(section.meta.status, LayerStatus.OK.value)


# =============================================================================
# PAYMENT STRUCTURE ANOMALIES
# =============================================================================

class TestPaymentNorms(unittest.TestCase):

    def test_angola_advance_above_red_flag(self):
        report = _make_report(iso2="AO", entity_name="Luanda Ventures")
        target = {
            "name": "Luanda Ventures",
            "delivery_country": "AO",
            "counterparty_text": (
                "We require a 70% advance payment on contract signature. "
                "Balance on delivery. USD cash payment preferred."
            ) * 3,
        }
        _run(assess_commercial_coherence(target, report))
        section = report.commercial_coherence

        kinds = [p["kind"] for p in section.payment_anomalies]
        self.assertIn("advance_payment_above_red_flag_threshold", kinds)
        self.assertIn("jurisdiction_specific_red_flag", kinds)  # USD cash
        self.assertLess(section.coherence_score, 0.7)

    def test_turkey_advance_above_threshold(self):
        report = _make_report(iso2="TR", entity_name="Ankara Savunma")
        target = {
            "name": "Ankara Savunma",
            "delivery_country": "TR",
            "counterparty_text": "Advance payment of 55% required on signing. " * 4,
        }
        _run(assess_commercial_coherence(target, report))
        kinds = [p["kind"] for p in report.commercial_coherence.payment_anomalies]
        self.assertIn("advance_payment_above_red_flag_threshold", kinds)

    def test_uae_non_swift_transfer_flag(self):
        report = _make_report(iso2="AE", entity_name="Gulf Trading FZE")
        target = {
            "name": "Gulf Trading FZE",
            "delivery_country": "AE",
            "counterparty_text": (
                "We propose a 25% advance. Due to banking constraints "
                "we require a request for non-SWIFT transfer via our "
                "partner bank."
            ),
        }
        _run(assess_commercial_coherence(target, report))
        kinds = [p["kind"] for p in report.commercial_coherence.payment_anomalies]
        self.assertIn("jurisdiction_specific_red_flag", kinds)

    def test_nigeria_offshore_account_flag(self):
        report = _make_report(iso2="NG", entity_name="Lagos Supply")
        target = {
            "name": "Lagos Supply",
            "delivery_country": "NG",
            "counterparty_text": (
                "Standard 20% advance. Please remit payment via our "
                "request for offshore payment account held in Mauritius."
            ),
        }
        _run(assess_commercial_coherence(target, report))
        kinds = [p["kind"] for p in report.commercial_coherence.payment_anomalies]
        self.assertIn("jurisdiction_specific_red_flag", kinds)

    def test_peru_bypass_seace_flag(self):
        report = _make_report(iso2="PE", entity_name="Andes Tech")
        target = {
            "name": "Andes Tech",
            "delivery_country": "PE",
            "counterparty_text": (
                "Our terms: 30% advance. Given the classified nature of "
                "the procurement we request to bypass SEACE platform "
                "under Article 4.1 exemption."
            ) * 2,
        }
        _run(assess_commercial_coherence(target, report))
        kinds = [p["kind"] for p in report.commercial_coherence.payment_anomalies]
        self.assertIn("jurisdiction_specific_red_flag", kinds)

    def test_performance_bond_waiver_request_red(self):
        # Saudi has perf_bond_mandatory=True
        report = _make_report(iso2="SA", entity_name="Riyadh Supply")
        target = {
            "name": "Riyadh Supply",
            "delivery_country": "SA",
            "counterparty_text": (
                "We propose a 25% advance payment. Given our "
                "track record, we request to waive performance bond "
                "on this engagement."
            ),
        }
        _run(assess_commercial_coherence(target, report))
        kinds = [p["kind"] for p in report.commercial_coherence.payment_anomalies]
        self.assertIn("performance_bond_waiver_request", kinds)

    def test_advance_in_norm_range_does_not_flag(self):
        report = _make_report(iso2="AO", entity_name="Luanda Valid")
        target = {
            "name": "Luanda Valid",
            "delivery_country": "AO",
            "counterparty_text": (
                "Our payment terms: 35% advance on signature, "
                "milestone claims against delivery, 25% on acceptance. "
                "Performance bond 8%."
            ) * 3,
        }
        _run(assess_commercial_coherence(target, report))
        kinds = [p["kind"] for p in report.commercial_coherence.payment_anomalies]
        self.assertNotIn("advance_payment_above_red_flag_threshold", kinds)
        self.assertNotIn("advance_payment_above_market_norm", kinds)


class TestAdvancePaymentExtractor(unittest.TestCase):

    def test_direct_percent(self):
        self.assertEqual(_extract_advance_payment_pct("70% advance payment"), 70.0)

    def test_percent_first(self):
        self.assertEqual(_extract_advance_payment_pct("We propose a 55 % down payment on signing"), 55.0)

    def test_advance_first(self):
        self.assertEqual(_extract_advance_payment_pct("advance payment of 40%"), 40.0)

    def test_no_mention_returns_none(self):
        self.assertIsNone(_extract_advance_payment_pct("No payment terms proposed"))

    def test_picks_largest(self):
        # Multiple numbers — take the largest plausible advance
        t = "We offer 20% advance or alternatively 40% advance on signing"
        self.assertEqual(_extract_advance_payment_pct(t), 40.0)


# =============================================================================
# LICENCE CHAIN
# =============================================================================

class TestLicenceChain(unittest.TestCase):

    def test_bih_two_stage_missing_mofter(self):
        report = _make_report(iso2="BA", entity_name="TRB d.d.", registered_address="Bratunac, Republika Srpska")
        target = {
            "name": "TRB d.d.",
            "origin_country": "GB",
            "delivery_country": "RS",  # third country
            "counterparty_text": (
                "We are a Bosnian distributor based in Bratunac. "
                "For this deal we will handle UK export licence via "
                "our UK partner. End-user certificate will be provided "
                "by the customer."
            ) * 2,
        }
        _run(assess_commercial_coherence(target, report))
        section = report.commercial_coherence

        missing_steps = [g["missing_step"] for g in section.licence_chain_gaps]
        self.assertTrue(
            any("MoFTER" in s for s in missing_steps),
            f"Expected BiH MoFTER flag, got: {missing_steps}",
        )

    def test_bih_two_stage_complete_no_gaps(self):
        report = _make_report(iso2="BA", entity_name="TRB d.d.")
        target = {
            "name": "TRB d.d.",
            "origin_country": "GB",
            "delivery_country": "RS",
            "counterparty_text": (
                "Standard two-stage chain: UK SIEL + BiH MoFTER re-export "
                "authorisation + third-country EUC. We acknowledge the "
                "8-16 week timeline for MoFTER re-export approval."
            ),
        }
        _run(assess_commercial_coherence(target, report))
        section = report.commercial_coherence
        missing_steps = [g["missing_step"] for g in section.licence_chain_gaps]
        self.assertFalse(
            any("MoFTER" in s for s in missing_steps),
            f"Expected no MoFTER gap when acknowledged, got: {missing_steps}",
        )

    def test_us_origin_missing_itar(self):
        report = _make_report(iso2="GB", entity_name="UK Broker Ltd")
        target = {
            "name": "UK Broker Ltd",
            "origin_country": "US",
            "delivery_country": "AE",
            "counterparty_text": (
                "We will source the equipment from the US and deliver "
                "to the UAE end-user. Payment terms 25%/60%/15%. "
                "Delivery Q3."
            ) * 2,
        }
        _run(assess_commercial_coherence(target, report))
        missing = [g["missing_step"] for g in report.commercial_coherence.licence_chain_gaps]
        self.assertTrue(
            any("ITAR" in s for s in missing),
            f"Expected ITAR flag for US-origin deal, got: {missing}",
        )

    def test_sam_gov_missing_uei(self):
        report = _make_report(iso2="GB", entity_name="UK Ltd")
        target = {
            "name": "UK Ltd",
            "counterparty_text": (
                "Yes we can register on SAM.gov — we already have a "
                "CAGE code. Registration should take 1-2 weeks."
            ) * 2,
        }
        # Shape matches because "sam.gov" is in the text.
        shape = _match_licence_chain_shape(target, "GB")
        self.assertEqual(shape, "us_sam_gov_registration")

        _run(assess_commercial_coherence(target, report))
        missing = [g["missing_step"] for g in report.commercial_coherence.licence_chain_gaps]
        self.assertTrue(any("UEI" in s for s in missing))


# =============================================================================
# CORPORATE STRUCTURE — universal
# =============================================================================

class TestCorporateStructureUniversal(unittest.TestCase):

    def test_fresh_incorporation_flag(self):
        recent = (datetime.now(timezone.utc) - timedelta(days=30)).date().isoformat()
        report = _make_report(
            entity_name="JustFormed Ltd",
            iso2="GB",
            incorporation_date=recent,
            directors=[{"name": "A"}],
            shareholders=[{"name": "A"}],
            declared_activity="defence supplies",
        )
        target = {
            "name": "JustFormed Ltd",
            "product_description": "ammunition",
            "transaction_value_usd": 10_000_000,
        }
        _run(assess_commercial_coherence(target, report))
        kinds = [a["kind"] for a in report.commercial_coherence.corporate_anomalies]
        self.assertIn("freshly_incorporated_entity", kinds)

    def test_old_incorporation_does_not_flag(self):
        old = "2015-01-01"
        report = _make_report(
            entity_name="OldCo",
            iso2="GB",
            incorporation_date=old,
            directors=[{"name": "A"}],
            shareholders=[{"name": "A"}],
            declared_activity="defence",
        )
        target = {"name": "OldCo", "transaction_value_usd": 500_000}
        _run(assess_commercial_coherence(target, report))
        kinds = [a["kind"] for a in report.commercial_coherence.corporate_anomalies]
        self.assertNotIn("freshly_incorporated_entity", kinds)

    def test_nominal_capital_mismatch(self):
        report = _make_report(
            entity_name="Nominal Capital Ltd",
            iso2="GB",
            incorporation_date="2008-01-01",
            directors=[{"name": "A"}, {"name": "B"}],
            shareholders=[{"name": "A", "capital": 1}, {"name": "B", "capital": 1}],
            declared_activity="defence equipment",
        )
        target = {
            "name": "Nominal Capital Ltd",
            "transaction_value_usd": 50_000_000,  # £50M with £2 capital
        }
        _run(assess_commercial_coherence(target, report))
        kinds = [a["kind"] for a in report.commercial_coherence.corporate_anomalies]
        self.assertIn("nominal_capital_mismatch", kinds)

    def test_director_concentration(self):
        report = _make_report(
            entity_name="SoloCo",
            iso2="GB",
            incorporation_date="2010-01-01",
            directors=[{"name": "John Smith"}],
            shareholders=[{"name": "John Smith", "capital": 100_000}],
            declared_activity="defence",
        )
        target = {
            "name": "SoloCo",
            "transaction_value_usd": 2_000_000,
        }
        _run(assess_commercial_coherence(target, report))
        kinds = [a["kind"] for a in report.commercial_coherence.corporate_anomalies]
        self.assertIn("director_concentration", kinds)

    def test_industrial_claim_with_virtual_office(self):
        report = _make_report(
            entity_name="FrontOffice Ltd",
            iso2="GB",
            incorporation_date="2012-01-01",
            directors=[{"name": "A"}],
            shareholders=[{"name": "A"}],
            declared_activity="defence manufacturing and assembly",
            registered_address="Unit 5, Regus Serviced Office, London",
        )
        target = {"name": "FrontOffice Ltd", "transaction_value_usd": 1_000_000}
        _run(assess_commercial_coherence(target, report))
        kinds = [a["kind"] for a in report.commercial_coherence.corporate_anomalies]
        self.assertIn("industrial_claim_with_formation_agent_address", kinds)

    def test_sector_mismatch_cleaning_company_ammunition(self):
        report = _make_report(
            entity_name="Spotless Cleaners",
            iso2="GB",
            incorporation_date="2005-01-01",
            directors=[{"name": "A"}, {"name": "B"}],
            shareholders=[{"name": "A"}, {"name": "B"}],
            declared_activity="commercial cleaning services",
            registered_address="101 Industrial Way, Leeds",
        )
        target = {
            "name": "Spotless Cleaners",
            "product_description": "ammunition supply contract",
            "transaction_value_usd": 800_000,
        }
        _run(assess_commercial_coherence(target, report))
        kinds = [a["kind"] for a in report.commercial_coherence.corporate_anomalies]
        self.assertIn("sector_mismatch", kinds)


# =============================================================================
# JURISDICTION-SPECIFIC CORPORATE RULES
# =============================================================================

class TestJurisdictionSpecific(unittest.TestCase):

    def test_uk_bearer_shares_flagged(self):
        report = _make_report(
            entity_name="Legacy UK Ltd", iso2="GB",
            incorporation_date="1998-01-01",
            directors=[{"name": "A"}], shareholders=[{"name": "A"}],
        )
        target = {
            "name": "Legacy UK Ltd",
            "counterparty_text": (
                "Our company uses bearer share structure for privacy. "
                "Owner can change without register update."
            ),
        }
        _run(assess_commercial_coherence(target, report))
        kinds = [a["kind"] for a in report.commercial_coherence.corporate_anomalies]
        self.assertIn("bearer_shares_in_abolished_jurisdiction", kinds)

    def test_uae_free_zone_onshore_claim_flagged(self):
        report = _make_report(
            entity_name="FZE Trading", iso2="AE",
            incorporation_date="2018-01-01",
            directors=[{"name": "A"}], shareholders=[{"name": "A"}],
        )
        target = {
            "name": "FZE Trading",
            "counterparty_text": (
                "We are a free zone company based in DMCC but we have "
                "UAE-wide trading rights including mainland delivery."
            ),
        }
        _run(assess_commercial_coherence(target, report))
        kinds = [a["kind"] for a in report.commercial_coherence.corporate_anomalies]
        self.assertIn("uae_free_zone_claiming_onshore", kinds)

    def test_angola_sarl_exceeds_cap(self):
        # Fabricate 35 shareholders to breach the 30-cap
        shareholders = [{"name": f"SH{i}"} for i in range(35)]
        report = _make_report(
            entity_name="SARL Grande", iso2="AO",
            incorporation_date="2015-01-01",
            directors=[{"name": "A"}, {"name": "B"}],
            shareholders=shareholders,
        )
        target = {
            "name": "SARL Grande",
            "counterparty_text": (
                "We are organised as a Sociedade por Quotas (SARL) "
                "with participation from 35 investors across Angola."
            ),
        }
        _run(assess_commercial_coherence(target, report))
        kinds = [a["kind"] for a in report.commercial_coherence.corporate_anomalies]
        self.assertIn("angola_sarl_exceeds_shareholder_cap", kinds)

    def test_bih_entity_level_unspecified(self):
        report = _make_report(
            entity_name="Bosnia Defence d.o.o.", iso2="BA",
            incorporation_date="2010-01-01",
            directors=[{"name": "A"}, {"name": "B"}],
            shareholders=[{"name": "A"}, {"name": "B"}],
            registered_address="Some Street, Bosnia",
        )
        target = {
            "name": "Bosnia Defence d.o.o.",
            "counterparty_text": "We are a BiH-registered company operating nationally.",
        }
        _run(assess_commercial_coherence(target, report))
        kinds = [a["kind"] for a in report.commercial_coherence.corporate_anomalies]
        self.assertIn("bih_entity_level_unspecified", kinds)


# =============================================================================
# OFFSET CLAIMS
# =============================================================================

class TestOffsetAssessment(unittest.TestCase):

    def test_uae_tawazun_exemption_despite_threshold(self):
        report = _make_report(entity_name="Tawazun Partner", iso2="AE",
                              incorporation_date="2015-01-01",
                              directors=[{"name": "A"}, {"name": "B"}],
                              shareholders=[{"name": "A"}, {"name": "B"}])
        target = {
            "name": "Tawazun Partner",
            "delivery_country": "AE",
            "transaction_value_usd": 50_000_000,  # well above UAE threshold 10M
            "counterparty_text": (
                "For this engagement we confirm that offset does not "
                "apply under our interpretation of Tawazun Economic "
                "Programme."
            ) * 2,
        }
        _run(assess_commercial_coherence(target, report))
        issues = [i["issue"] for i in report.commercial_coherence.offset_issues]
        self.assertIn("offset_exemption_claimed_despite_threshold_met", issues)

    def test_saudi_offset_compliance_without_activity(self):
        report = _make_report(entity_name="Riyadh Offset Co", iso2="SA",
                              incorporation_date="2015-01-01",
                              directors=[{"name": "A"}, {"name": "B"}],
                              shareholders=[{"name": "A"}, {"name": "B"}])
        target = {
            "name": "Riyadh Offset Co",
            "delivery_country": "SA",
            "transaction_value_usd": 30_000_000,
            "counterparty_text": (
                "We confirm full offset compliance on this Vision 2030 "
                "programme. Our offset obligation is met."
            ),
        }
        _run(assess_commercial_coherence(target, report))
        issues = [i["issue"] for i in report.commercial_coherence.offset_issues]
        self.assertIn("offset_compliance_claimed_without_qualifying_activity", issues)

    def test_below_threshold_no_offset_needed(self):
        report = _make_report(entity_name="Small Deal Co", iso2="AE",
                              incorporation_date="2015-01-01",
                              directors=[{"name": "A"}, {"name": "B"}],
                              shareholders=[{"name": "A"}, {"name": "B"}])
        target = {
            "name": "Small Deal Co",
            "delivery_country": "AE",
            "transaction_value_usd": 2_000_000,  # below UAE 10M threshold
            "counterparty_text": "offset does not apply here",
        }
        _run(assess_commercial_coherence(target, report))
        self.assertEqual(report.commercial_coherence.offset_issues, [])


# =============================================================================
# SCORING + TIER CLASSIFICATION
# =============================================================================

class TestScoringAndTier(unittest.TestCase):

    def test_classify_tier_boundaries(self):
        self.assertEqual(_classify_tier(1.0), "GREEN")
        self.assertEqual(_classify_tier(0.80), "GREEN")
        self.assertEqual(_classify_tier(0.79), "ELEVATED")
        self.assertEqual(_classify_tier(0.55), "ELEVATED")
        self.assertEqual(_classify_tier(0.54), "HIGH")
        self.assertEqual(_classify_tier(0.0), "HIGH")

    def test_score_clamped_to_zero(self):
        # Stack multiple red flags so deductions would go negative
        report = _make_report(
            entity_name="MultiFail Ltd", iso2="AO",
            incorporation_date=(datetime.now(timezone.utc) - timedelta(days=10)).date().isoformat(),
            directors=[{"name": "X"}],
            shareholders=[{"name": "X", "capital": 1}],
            declared_activity="cleaning services",
            registered_address="c/o Formation Agent, Dubai",
        )
        target = {
            "name": "MultiFail Ltd",
            "product_description": "ammunition",
            "transaction_value_usd": 100_000_000,
            "delivery_country": "AO",
            "counterparty_text": (
                "We need 80% advance payment in USD cash. No performance "
                "bond. Offset compliant. Payment to our offshore account."
            ) * 3,
        }
        _run(assess_commercial_coherence(target, report))
        self.assertGreaterEqual(report.commercial_coherence.coherence_score, 0.0)
        self.assertLessEqual(report.commercial_coherence.coherence_score, 1.0)
        self.assertEqual(report.commercial_coherence.tier, "HIGH")


# =============================================================================
# INTEGRATION — schema + orchestrator glue
# =============================================================================

class TestSchemaIntegration(unittest.TestCase):

    def test_new_section_attached_to_report(self):
        report = ARKDDReport()
        self.assertIsInstance(report.commercial_coherence, CommercialCoherenceSection)
        self.assertEqual(report.commercial_coherence.coherence_score, 1.0)
        self.assertEqual(report.commercial_coherence.tier, "GREEN")

    def test_report_as_dict_includes_section(self):
        report = ARKDDReport()
        d = report.as_dict()
        self.assertIn("commercial_coherence", d)
        self.assertIn("coherence_score", d["commercial_coherence"])
        self.assertIn("anomalies", d["commercial_coherence"])

    def test_orchestrator_imports_layer_5c(self):
        # The orchestrator must import the new module; if the import is
        # broken the whole DD path fails to start.
        from aria_service.intel import dd_orchestrator  # noqa: F401
        from aria_service.intel import commercial_coherence
        self.assertTrue(hasattr(commercial_coherence, "assess_commercial_coherence"))
        self.assertTrue(hasattr(commercial_coherence, "anomaly_text_for_deception"))

    def test_render_markdown_renders_coherence_section(self):
        report = _make_report(iso2="AO", entity_name="Render Test")
        target = {
            "name": "Render Test",
            "delivery_country": "AO",
            "counterparty_text": ("We need 80% advance in USD cash." * 4),
        }
        _run(assess_commercial_coherence(target, report))
        md = report.render_markdown()
        self.assertIn("Commercial Coherence", md)
        self.assertIn("Coherence score", md)

    def test_anomaly_text_for_deception_non_empty_when_issues(self):
        report = _make_report(iso2="AO", entity_name="Luanda Deception")
        target = {
            "name": "Luanda Deception",
            "delivery_country": "AO",
            "counterparty_text": "We require 70% advance, USD cash. " * 3,
        }
        _run(assess_commercial_coherence(target, report))
        text = anomaly_text_for_deception(report.commercial_coherence)
        self.assertGreater(len(text), 50)
        self.assertIn("Commercial coherence", text)

    def test_anomaly_text_empty_on_clean(self):
        report = _make_report(
            iso2="GB", entity_name="Clean Co",
            incorporation_date="2010-01-01",
            directors=[{"name": "A"}, {"name": "B"}],
            shareholders=[{"name": "A"}, {"name": "B"}],
        )
        _run(assess_commercial_coherence({"name": "Clean Co"}, report))
        text = anomaly_text_for_deception(report.commercial_coherence)
        self.assertEqual(text, "")

    def test_layer_5c_never_raises_on_malformed_input(self):
        report = _make_report(iso2="GB", entity_name="Mystery")
        # Deliberately malformed target — module must tolerate it
        target = {"name": "Mystery", "incorporation_date": "not-a-date"}
        try:
            _run(assess_commercial_coherence(target, report))
        except Exception as e:
            self.fail(f"assess_commercial_coherence raised on bad input: {e}")
        self.assertIn(
            report.commercial_coherence.meta.status,
            (LayerStatus.OK.value, LayerStatus.ERROR.value),
        )


# =============================================================================
# DATA CONSTANTS — sanity
# =============================================================================

class TestDataConstants(unittest.TestCase):

    def test_all_payment_norms_have_required_keys(self):
        required = {"market", "advance_norm_min", "advance_norm_max",
                    "advance_red_flag_above", "perf_bond_norm_pct",
                    "perf_bond_mandatory", "extra_red_flags"}
        for market, norms in PAYMENT_NORMS.items():
            missing = required - set(norms.keys())
            self.assertFalse(
                missing, f"PAYMENT_NORMS[{market!r}] missing keys: {missing}"
            )

    def test_all_licence_chains_have_keywords(self):
        for shape_key, shape_def in LICENCE_CHAIN_REQUIREMENTS.items():
            self.assertTrue(shape_def["steps"], f"No steps for {shape_key}")
            for step in shape_def["steps"]:
                self.assertTrue(step["keywords"],
                                f"No keywords for {shape_key} / {step['step']}")
                self.assertTrue(step["authority"])
                self.assertEqual(len(step["timeline_weeks"]), 2)

    def test_all_payment_markets_covered_by_norms(self):
        # Every iso2 we reference in tests must be in PAYMENT_NORMS
        for iso2 in ("GB", "AO", "AE", "SA", "TR", "NG", "PE"):
            self.assertIn(iso2, PAYMENT_NORMS,
                          f"PAYMENT_NORMS missing {iso2}")


# =============================================================================
# INFERENCE helpers
# =============================================================================

class TestInference(unittest.TestCase):

    def test_infer_payment_market_uses_delivery(self):
        self.assertEqual(
            _infer_payment_market({"delivery_country": "AO"}, "GB"),
            "AO",
        )

    def test_infer_payment_market_falls_back_to_iso2(self):
        self.assertEqual(_infer_payment_market({}, "TR"), "TR")

    def test_infer_payment_market_nato_override(self):
        self.assertEqual(
            _infer_payment_market({"nato_procurement": True}, "GB"),
            "NATO",
        )

    def test_match_licence_chain_bih(self):
        result = _match_licence_chain_shape(
            {"counterparty_text": "Bosnia MoFTER re-export processing"},
            "BA",
        )
        self.assertEqual(result, "uk_supply_bih_intermediary_third_country")

    def test_match_licence_chain_explicit_override(self):
        result = _match_licence_chain_shape(
            {"licence_chain_shape": "eu_dual_use_third_country"},
            "DE",
        )
        self.assertEqual(result, "eu_dual_use_third_country")

    def test_match_licence_chain_no_match(self):
        # Insufficient context — should return None
        self.assertIsNone(_match_licence_chain_shape({}, "ZW"))


if __name__ == "__main__":
    unittest.main()

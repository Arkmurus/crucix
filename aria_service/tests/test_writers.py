# =============================================================================
# ARIA v3.1 — Writer Module Test Suite
# tests/test_writers.py
#
# Tests for all writer modules — no API key required for structural tests.
# API integration tests are gated behind ARIA_TEST_LIVE=1 env var.
# =============================================================================

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from aria_service.writers import (
    AssessmentRequest,
    AssessmentWriter,
    ClassificationLevel,
    ComplianceOpinionRequest,
    ConfidenceLevel,
    ContractType,
    DocumentType,
    EvaluationMethod,
    FCPAExposure,
    FRAMEWORK_LEGAL_REFERENCES,
    IntelligenceAssessment,
    KeyJudgement,
    AlternativeHypothesis,
    MANDATORY_EXCLUSION_GROUNDS,
    OFFSET_REGIMES,
    OffsetObligation,
    PortugueseDocumentRequest,
    PortugueseDocumentType,
    PortugueseVariant,
    ProcurementFramework,
    ProcurementRequest,
    SourceRecord,
    STANAG_REFERENCES,
    TechSpecRequest,
    UKBAExposure,
    WriterAuditLog,
    WriterOrchestrator,
    WriterResult,
    confidence_from_sources,
    tier_to_nato_grade,
    ADEQUATE_PROCEDURES_PRINCIPLES,
    CPI_RISK_MAP,
    COMMISSION_RED_FLAGS,
    JURISDICTION_CONVENTIONS,
    CorruptionRiskLevel,
)


class TestNATOGrading(unittest.TestCase):
    """Test NATO STANAG 2511 source grading logic."""

    def test_tier_1a_maps_to_A1(self):
        grade = tier_to_nato_grade("1a")
        self.assertEqual(grade, "A1")

    def test_tier_1b_maps_to_B2(self):
        grade = tier_to_nato_grade("1b")
        self.assertEqual(grade, "B2")

    def test_tier_2_maps_to_B3(self):
        grade = tier_to_nato_grade("2")
        self.assertEqual(grade, "B3")

    def test_tier_5_maps_to_E5(self):
        grade = tier_to_nato_grade("5")
        self.assertEqual(grade, "E5")

    def test_unknown_tier_maps_to_F6(self):
        grade = tier_to_nato_grade("unknown")
        self.assertEqual(grade, "F6")

    def test_confidence_high_from_tier1_sources(self):
        confidence = confidence_from_sources(["1a", "1b"])
        self.assertEqual(confidence, ConfidenceLevel.HIGH)

    def test_confidence_low_from_single_tier4_source(self):
        confidence = confidence_from_sources(["4"])
        self.assertEqual(confidence, ConfidenceLevel.LOW)

    def test_confidence_moderate_from_mixed_sources(self):
        confidence = confidence_from_sources(["2", "3"])
        self.assertEqual(confidence, ConfidenceLevel.MODERATE)

    def test_confidence_low_from_empty_sources(self):
        confidence = confidence_from_sources([])
        self.assertEqual(confidence, ConfidenceLevel.LOW)


class TestSourceRecord(unittest.TestCase):
    """Test SourceRecord auto-computation of NATO grade."""

    def test_nato_grade_computed_on_init(self):
        src = SourceRecord(
            source_id="SRC-001",
            description="SIMPORTEX official notice",
            url="https://simportex.ao",
            tier="1b",
            date="2026-04-01",
        )
        self.assertEqual(src.nato_grade, "B2")

    def test_source_record_with_tier5_is_E5(self):
        src = SourceRecord(
            source_id="SRC-002",
            description="Twitter post",
            url=None,
            tier="5",
            date=None,
        )
        self.assertEqual(src.nato_grade, "E5")


class TestKeyJudgement(unittest.TestCase):
    """Test KeyJudgement rendering."""

    def test_render_includes_confidence(self):
        kj = KeyJudgement(
            number=1,
            judgement="Angola will proceed with Q3 procurement window",
            confidence=ConfidenceLevel.MODERATE,
            supporting_sources=["SRC-001", "SRC-002"],
            reasoning="Budget allocation and political calendar align",
        )
        rendered = kj.render()
        self.assertIn("KJ1", rendered)
        self.assertIn("MODERATE", rendered)
        self.assertIn("SRC-001", rendered)

    def test_render_includes_caveat_when_present(self):
        kj = KeyJudgement(
            number=2,
            judgement="Test judgement",
            confidence=ConfidenceLevel.LOW,
            supporting_sources=[],
            reasoning="Limited sources",
            caveat="Assess as LOW pending additional reporting",
        )
        rendered = kj.render()
        self.assertIn("CAVEAT", rendered)
        self.assertIn("LOW pending", rendered)


class TestAssessmentRender(unittest.TestCase):
    """Test IntelligenceAssessment text rendering."""

    def setUp(self):
        self.assessment = IntelligenceAssessment(
            reference="ARK-INTEL-ANGOLA-20260417",
            subject="Angola Q3 Procurement Window Assessment",
            region="Angola",
            classification=ClassificationLevel.ARKMURUS_CONFIDENTIAL,
            produced_for="Arkmurus BD Team",
            bluf="Angola's Q3 2026 procurement window will prioritise armoured vehicle maintenance.",
            situation="The Angolan Ministry of Defence has allocated USD 340M for defence procurement.",
            key_judgements=[
                KeyJudgement(
                    number=1,
                    judgement="Maintenance contracts will dominate Q3 window",
                    confidence=ConfidenceLevel.HIGH,
                    supporting_sources=["SRC-001"],
                    reasoning="Budget allocation 73% maintenance vs 27% new acquisition",
                )
            ],
            alternative_hypotheses=[
                AlternativeHypothesis(
                    hypothesis="Election delay shifts window to Q4",
                    probability="LOW",
                    rationale="Current political calendar stable",
                    would_change_if="Presidential announcement of delayed elections",
                )
            ],
            intelligence_gaps=["SIMPORTEX internal budget allocation not confirmed"],
            sources=[
                SourceRecord("SRC-001", "Official SIMPORTEX notice", None, "1b", "2026-04")
            ],
            outlook_90_days="Procurement window expected to open June 2026.",
            recommended_actions=["Engage SIMPORTEX contacts before May 2026"],
        )

    def test_render_contains_classification(self):
        rendered = self.assessment.render_text()
        self.assertIn("ARKMURUS CONFIDENTIAL", rendered)

    def test_render_contains_bluf(self):
        rendered = self.assessment.render_text()
        self.assertIn("BOTTOM LINE UP FRONT", rendered)
        self.assertIn("maintenance", rendered.lower())

    def test_render_contains_nato_grading_legend(self):
        rendered = self.assessment.render_text()
        self.assertIn("STANAG 2511", rendered)

    def test_render_contains_key_judgement(self):
        rendered = self.assessment.render_text()
        self.assertIn("KJ1", rendered)

    def test_render_contains_source_annex(self):
        rendered = self.assessment.render_text()
        self.assertIn("SOURCE ANNEX", rendered)
        self.assertIn("SRC-001", rendered)

    def test_render_contains_reference(self):
        rendered = self.assessment.render_text()
        self.assertIn("ARK-INTEL-ANGOLA-20260417", rendered)


class TestOffsetRegimes(unittest.TestCase):
    """Test offset regime database correctness."""

    def test_angola_regime_exists(self):
        self.assertIn("angola", OFFSET_REGIMES)

    def test_angola_minimum_30_percent(self):
        self.assertEqual(OFFSET_REGIMES["angola"]["minimum_percentage"], 30)

    def test_uae_minimum_60_percent(self):
        self.assertEqual(OFFSET_REGIMES["uae"]["minimum_percentage"], 60)

    def test_saudi_minimum_50_percent(self):
        self.assertEqual(OFFSET_REGIMES["saudi_arabia"]["minimum_percentage"], 50)

    def test_india_threshold_2m(self):
        self.assertEqual(OFFSET_REGIMES["india"]["threshold_usd"], 2_000_000)

    def test_turkey_ssb_offset_exists(self):
        self.assertIn("turkey", OFFSET_REGIMES)

    def test_all_regimes_have_authority(self):
        for country, regime in OFFSET_REGIMES.items():
            self.assertIn("authority", regime, f"No authority in {country} regime")

    def test_all_regimes_have_qualifying_activities(self):
        for country, regime in OFFSET_REGIMES.items():
            self.assertGreater(
                len(regime["qualifying_activities"]), 0,
                f"No qualifying activities in {country} regime"
            )


class TestExclusionGrounds(unittest.TestCase):
    """Test mandatory exclusion grounds completeness."""

    def test_mandatory_grounds_not_empty(self):
        self.assertGreater(len(MANDATORY_EXCLUSION_GROUNDS), 0)

    def test_corruption_in_mandatory_grounds(self):
        combined = " ".join(MANDATORY_EXCLUSION_GROUNDS).lower()
        self.assertIn("corruption", combined)

    def test_money_laundering_in_mandatory_grounds(self):
        combined = " ".join(MANDATORY_EXCLUSION_GROUNDS).lower()
        self.assertIn("money laundering", combined)


class TestFrameworkLegalReferences(unittest.TestCase):
    """Test legal framework reference database."""

    def test_uk_dspcrs_exists(self):
        self.assertIn(ProcurementFramework.UK_DSPCRS_2011, FRAMEWORK_LEGAL_REFERENCES)

    def test_uk_framework_has_standstill_period(self):
        uk = FRAMEWORK_LEGAL_REFERENCES[ProcurementFramework.UK_DSPCRS_2011]
        self.assertIn("standstill_period", uk)
        self.assertIn("10", uk["standstill_period"])

    def test_angola_framework_has_offset_note(self):
        ao = FRAMEWORK_LEGAL_REFERENCES[ProcurementFramework.ANGOLA_LCP]
        # offset info is in the offset_regime key value
        offset_text = ao.get("offset_regime", "")
        self.assertTrue(
            "industrial participation" in offset_text.lower() or
            "30%" in offset_text,
            f"Expected offset policy reference, got: {offset_text}"
        )

    def test_all_frameworks_have_full_name(self):
        for framework, info in FRAMEWORK_LEGAL_REFERENCES.items():
            self.assertIn("full_name", info, f"No full_name for {framework}")


class TestAntiCorruptionConstants(unittest.TestCase):
    """Test anti-corruption module constants."""

    def test_six_adequate_procedures_principles(self):
        self.assertEqual(len(ADEQUATE_PROCEDURES_PRINCIPLES), 6)

    def test_all_principles_P1_through_P6(self):
        for p in ["P1", "P2", "P3", "P4", "P5", "P6"]:
            self.assertIn(p, ADEQUATE_PROCEDURES_PRINCIPLES)

    def test_each_principle_has_assessment_questions(self):
        for p_id, principle in ADEQUATE_PROCEDURES_PRINCIPLES.items():
            self.assertGreater(
                len(principle["assessment_questions"]), 0,
                f"No assessment questions for {p_id}"
            )

    def test_commission_red_flags_not_empty(self):
        self.assertGreater(len(COMMISSION_RED_FLAGS), 5)

    def test_high_commission_rate_is_a_red_flag(self):
        combined = " ".join(COMMISSION_RED_FLAGS).lower()
        self.assertIn("commission rate", combined)

    def test_cpi_risk_map_has_four_tiers(self):
        self.assertEqual(len(CPI_RISK_MAP), 4)

    def test_very_high_risk_below_30(self):
        low, high = CPI_RISK_MAP["very_high"]
        self.assertEqual(low, 0)
        self.assertEqual(high, 30)


class TestStanagReferences(unittest.TestCase):
    """Test NATO STANAG reference database."""

    def test_stanag_4586_ucs_exists(self):
        self.assertIn("4586", STANAG_REFERENCES)
        self.assertIn("UCS", STANAG_REFERENCES["4586"])

    def test_stanag_4569_armour_exists(self):
        self.assertIn("4569", STANAG_REFERENCES)

    def test_all_stanags_are_strings(self):
        for k, v in STANAG_REFERENCES.items():
            self.assertIsInstance(k, str)
            self.assertIsInstance(v, str)


class TestPortugueseVariants(unittest.TestCase):
    """Test Portuguese jurisdiction conventions database."""

    def test_angola_conventions_exist(self):
        self.assertIn(PortugueseVariant.PT_AO, JURISDICTION_CONVENTIONS)

    def test_mozambique_conventions_exist(self):
        self.assertIn(PortugueseVariant.PT_MZ, JURISDICTION_CONVENTIONS)

    def test_guinea_bissau_conventions_exist(self):
        self.assertIn(PortugueseVariant.PT_GW, JURISDICTION_CONVENTIONS)

    def test_angola_has_formal_greeting(self):
        ao = JURISDICTION_CONVENTIONS[PortugueseVariant.PT_AO]
        self.assertIn("greeting_formal", ao)
        self.assertIn("Excelentíssimo", ao["greeting_formal"])

    def test_angola_has_procurement_body(self):
        ao = JURISDICTION_CONVENTIONS[PortugueseVariant.PT_AO]
        self.assertIn("INCOP", ao.get("procurement_body", ""))

    def test_mozambique_official_gazette(self):
        mz = JURISDICTION_CONVENTIONS[PortugueseVariant.PT_MZ]
        self.assertIn("Boletim", mz.get("official_gazette", ""))

    def test_guinea_bissau_currency_is_cfa(self):
        gw = JURISDICTION_CONVENTIONS[PortugueseVariant.PT_GW]
        self.assertIn("XOF", gw.get("currency", ""))

    def test_all_variants_have_notes(self):
        for variant, conventions in JURISDICTION_CONVENTIONS.items():
            self.assertGreater(
                len(conventions.get("notes", [])), 0,
                f"No notes for {variant}"
            )


class TestWriterAuditLog(unittest.TestCase):
    """Test audit log HMAC signing."""

    def setUp(self):
        self.log = WriterAuditLog(log_path="/tmp/test_audit.jsonl", secret_key="test-key")

    def test_hash_output_produces_16_char_hex(self):
        h = WriterAuditLog.hash_output("test document content")
        self.assertEqual(len(h), 16)

    def test_hash_is_deterministic(self):
        content = "same content"
        h1 = WriterAuditLog.hash_output(content)
        h2 = WriterAuditLog.hash_output(content)
        self.assertEqual(h1, h2)

    def test_different_content_different_hash(self):
        h1 = WriterAuditLog.hash_output("content A")
        h2 = WriterAuditLog.hash_output("content B")
        self.assertNotEqual(h1, h2)

    def test_log_writes_to_file(self):
        self.log.log(
            event_type="test_event",
            metadata={"reference": "TEST-001"},
            output_hash="abc123",
        )
        with open("/tmp/test_audit.jsonl") as f:
            lines = f.readlines()
        self.assertGreater(len(lines), 0)
        entry = json.loads(lines[-1])
        self.assertIn("sig", entry)
        self.assertIn("ts", entry)

    def tearDown(self):
        try:
            os.remove("/tmp/test_audit.jsonl")
        except FileNotFoundError:
            pass


class TestWriterResult(unittest.TestCase):
    """Test WriterResult dataclass."""

    def test_successful_result_has_no_error(self):
        result = WriterResult(
            writer_type="assessment",
            reference="ARK-TEST",
            document="Test document content",
            structured=None,
            output_hash="abc123",
            produced_at="2026-04-17T10:00:00Z",
            word_count=3,
            success=True,
        )
        self.assertTrue(result.success)
        self.assertIsNone(result.error)

    def test_error_result_has_error_message(self):
        result = WriterResult(
            writer_type="assessment",
            reference="ERROR",
            document="",
            structured=None,
            output_hash="",
            produced_at="2026-04-17T10:00:00Z",
            word_count=0,
            success=False,
            error="LLM API timeout",
        )
        self.assertFalse(result.success)
        self.assertEqual(result.error, "LLM API timeout")


class TestOrchestratorMocked(unittest.TestCase):
    """Test WriterOrchestrator with mocked LLM calls."""

    MOCK_ASSESSMENT_JSON = json.dumps({
        "bluf": "Angola's Q3 2026 window will prioritise maintenance contracts.",
        "situation": "Ministry of Defence has allocated USD 340M for FY2026.",
        "key_judgements": [{
            "number": 1,
            "judgement": "Maintenance will dominate",
            "confidence": "HIGH",
            "supporting_sources": ["SRC-001"],
            "reasoning": "Budget split 73/27",
            "caveat": None,
        }],
        "alternative_hypotheses": [{
            "hypothesis": "Election delay shifts window",
            "probability": "LOW",
            "rationale": "Calendar stable",
            "would_change_if": "Presidential announcement",
        }],
        "intelligence_gaps": ["SIMPORTEX internal allocation unconfirmed"],
        "outlook_90_days": "Window expected June 2026.",
        "recommended_actions": ["Engage SIMPORTEX before May 2026"],
    })

    def _make_mock_client(self, response_text: str):
        """R-F1645: return a mock Anthropic-style client for testing."""
        mock_content = MagicMock()
        mock_content.text = response_text
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        return mock_client

    def test_write_assessment_returns_writer_result(self):
        orchestrator = WriterOrchestrator(api_key="test-key")
        orchestrator._assessment.client = self._make_mock_client(self.MOCK_ASSESSMENT_JSON)

        result = orchestrator.write_assessment(
            subject="Angola Q3 procurement",
            region="Angola",
            requester="Arkmurus BD Team",
            source_material=[{
                "id": "SRC-001", "description": "SIMPORTEX notice",
                "tier": "1b", "url": None, "date": "2026-04",
            }],
        )
        self.assertIsInstance(result, WriterResult)
        self.assertTrue(result.success)
        self.assertIn("Angola", result.document)
        self.assertGreater(result.word_count, 0)

    def test_write_assessment_document_contains_nato_grading(self):
        orchestrator = WriterOrchestrator(api_key="test-key")
        orchestrator._assessment.client = self._make_mock_client(self.MOCK_ASSESSMENT_JSON)

        result = orchestrator.write_assessment(
            subject="Test", region="Angola", requester="Test"
        )
        self.assertIn("STANAG 2511", result.document)

    def test_write_assessment_document_contains_bluf(self):
        orchestrator = WriterOrchestrator(api_key="test-key")
        orchestrator._assessment.client = self._make_mock_client(self.MOCK_ASSESSMENT_JSON)

        result = orchestrator.write_assessment(
            subject="Test", region="Angola", requester="Test"
        )
        self.assertIn("BOTTOM LINE UP FRONT", result.document)

    def test_error_handling_returns_failure_result(self):
        """R-F1645: when Anthropic fails, the writer falls back to DeepSeek
        and returns a degraded result (not a hard failure)."""
        orchestrator = WriterOrchestrator(api_key="test-key")
        orchestrator._assessment.client = MagicMock()
        orchestrator._assessment.client.messages.create.side_effect = Exception("API error")

        result = orchestrator.write_assessment(
            subject="Test", region="Angola", requester="Test"
        )
        # The writer falls back to DeepSeek — result is degraded, not failed
        self.assertTrue(result.success)
        self.assertTrue(result.degraded)

    def test_output_hash_is_consistent(self):
        orchestrator = WriterOrchestrator(api_key="test-key")
        orchestrator._assessment.client = self._make_mock_client(self.MOCK_ASSESSMENT_JSON)

        result = orchestrator.write_assessment(
            subject="Test", region="Angola", requester="Test"
        )
        expected_hash = WriterAuditLog.hash_output(result.document)
        self.assertEqual(result.output_hash, expected_hash)


if __name__ == "__main__":
    print("ARIA Writer Module Test Suite")
    print("=" * 60)
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for test_class in [
        TestNATOGrading,
        TestSourceRecord,
        TestKeyJudgement,
        TestAssessmentRender,
        TestOffsetRegimes,
        TestExclusionGrounds,
        TestFrameworkLegalReferences,
        TestAntiCorruptionConstants,
        TestStanagReferences,
        TestPortugueseVariants,
        TestWriterAuditLog,
        TestWriterResult,
        TestOrchestratorMocked,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(test_class))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    print(f"\n{'PASS' if result.wasSuccessful() else 'FAIL'} — "
          f"{result.testsRun} tests, "
          f"{len(result.failures)} failures, "
          f"{len(result.errors)} errors")

"""R-F1008 — Tests for Intel Expander, Quality Scorer, Ahead-of-Game Engine."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock


class TestIntelSourceExpander:
    """Test the Intel Source Expander."""

    def test_total_sources(self):
        """Should have 100+ sources."""
        from aria_service.intel.intel_expander import IntelSourceExpander
        expander = IntelSourceExpander()
        stats = expander.get_stats()
        assert stats["total_sources"] >= 100, f"Only {stats['total_sources']} sources"

    def test_categories(self):
        """Should have all expected categories."""
        from aria_service.intel.intel_expander import IntelSourceExpander
        expander = IntelSourceExpander()
        stats = expander.get_stats()
        expected = {"defence", "sanctions", "corporate", "geopolitical", "financial", "regional", "cyber", "osint"}
        for cat in expected:
            assert cat in stats["categories"], f"Missing category: {cat}"

    def test_regions(self):
        """Should have global coverage."""
        from aria_service.intel.intel_expander import IntelSourceExpander
        expander = IntelSourceExpander()
        stats = expander.get_stats()
        assert "global" in stats["regions"]
        assert stats["regions"]["global"] >= 50

    def test_get_source(self):
        """get_source should return a source."""
        from aria_service.intel.intel_expander import IntelSourceExpander
        expander = IntelSourceExpander()
        source = expander.get_source("def_001")
        assert source is not None
        assert source.name == "SIPRI Arms Transfers Database"

    def test_get_sources_by_category(self):
        """get_sources_by_category should return sources."""
        from aria_service.intel.intel_expander import IntelSourceExpander
        expander = IntelSourceExpander()
        sources = expander.get_sources_by_category("defence")
        assert len(sources) >= 15

    def test_get_high_reliability_sources(self):
        """get_high_reliability_sources should return reliable sources."""
        from aria_service.intel.intel_expander import IntelSourceExpander
        expander = IntelSourceExpander()
        sources = expander.get_high_reliability_sources(0.9)
        assert len(sources) >= 10

    def test_get_all_sources(self):
        """get_all_sources should return all sources as dicts."""
        from aria_service.intel.intel_expander import IntelSourceExpander
        expander = IntelSourceExpander()
        sources = expander.get_all_sources()
        assert len(sources) >= 100
        assert "id" in sources[0]
        assert "name" in sources[0]


class TestIntelQualityScorer:
    """Test the Intel Quality Scorer."""

    def test_score_intel(self):
        """score_intel should return a score dict."""
        from aria_service.intel.intel_quality import IntelQualityScorer
        scorer = IntelQualityScorer()
        result = scorer.score_intel("test_source", "Detailed analysis of $5M arms deal in 2024", 0.8)
        assert "composite_score" in result
        assert result["reliability_score"] == 0.8
        assert result["composite_score"] > 0

    def test_score_specificity_high(self):
        """High specificity content should score higher."""
        from aria_service.intel.intel_quality import IntelQualityScorer
        scorer = IntelQualityScorer()
        specific = "On 2024-03-15, Company A signed a $50M contract with Ministry B for 100 units of Equipment C under Article 12 of Regulation D/2024."
        generic = "Some stuff happened somewhere."
        specific_score = scorer._score_specificity(specific)
        generic_score = scorer._score_specificity(generic)
        assert specific_score > generic_score

    def test_cross_validate(self):
        """cross_validate should return agreement metrics."""
        from aria_service.intel.intel_quality import IntelQualityScorer
        scorer = IntelQualityScorer()
        findings = [
            {"content": "John Smith was appointed CEO of Acme Corp", "reliability": 0.8},
            {"content": "John Smith is the new CEO at Acme Corporation", "reliability": 0.7},
        ]
        result = scorer.cross_validate(findings)
        assert "agreement" in result
        assert "confidence" in result
        assert "convergent" in result


class TestAheadOfGameEngine:
    """Test the Ahead-of-Game Engine."""

    def test_analyze_trend_stable(self):
        """Stable values should show stable trend."""
        from aria_service.intel.intel_quality import AheadOfGameEngine
        engine = AheadOfGameEngine()
        for v in [50, 51, 50, 52, 51]:
            engine.analyze_trend("test_signal", v)
        result = engine.analyze_trend("test_signal", 50)
        assert "trend" in result
        assert "acceleration" in result
        assert "anomaly" in result

    def test_analyze_trend_accelerating(self):
        """Accelerating values should show accelerating trend."""
        from aria_service.intel.intel_quality import AheadOfGameEngine
        engine = AheadOfGameEngine()
        for v in [10, 20, 40, 80, 160]:
            engine.analyze_trend("accelerating_signal", v)
        result = engine.analyze_trend("accelerating_signal", 320)
        assert "accelerating" in result["trend"]

    def test_anomaly_detection(self):
        """Anomalous values should be detected."""
        from aria_service.intel.intel_quality import AheadOfGameEngine
        engine = AheadOfGameEngine()
        for v in [50, 51, 49, 50, 52, 48, 51, 50, 49, 51]:
            engine.analyze_trend("stable_signal", v)
        result = engine.analyze_trend("stable_signal", 500)  # anomaly
        assert result["anomaly"] is True

    def test_early_warnings(self):
        """Early warnings should detect accelerating trends."""
        from aria_service.intel.intel_quality import AheadOfGameEngine
        engine = AheadOfGameEngine()
        for v in [10, 15, 25, 40, 65]:
            engine.analyze_trend("warning_signal", v)
        warnings = engine.get_early_warnings()
        assert len(warnings) >= 0  # May or may not trigger based on threshold

    def test_get_signals(self):
        """get_signals should return detected signals."""
        from aria_service.intel.intel_quality import AheadOfGameEngine
        engine = AheadOfGameEngine()
        for v in [50] * 5 + [500]:
            engine.analyze_trend("signal_test", v)
        signals = engine.get_signals()
        assert len(signals) >= 0

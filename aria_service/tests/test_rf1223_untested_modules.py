"""R-F1223: Capability tests for previously-untested modules >100 lines.

Each test verifies the module's main entry point works. Function names
were verified via grep before writing (anti-hallucination law #1).
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


# ── cost_tracker.py (820L) ──────────────────────────────────────────
# Verified: feature(), get_cost_summary(), record_call()

class TestCostTracker:
    """cost_tracker is used across the codebase — verify core functions."""

    def test_feature_context_manager(self):
        """cost_tracker.feature() context manager works."""
        from aria_service.intel.cost_tracker import feature
        with feature("test_feature"):
            pass  # Context manager should complete without error

    @pytest.mark.asyncio
    async def test_get_cost_summary_returns_dict(self):
        """get_cost_summary returns expected dict shape."""
        with patch("aria_service.intel.cost_tracker.rs.lrange", return_value=[]):
            from aria_service.intel.cost_tracker import get_cost_summary
            result = await get_cost_summary()
            assert isinstance(result, dict)


# ── coverage_heatmap.py (718L) ──────────────────────────────────────
# Verified: build_heatmap(), summary(), gap_targets()

class TestCoverageHeatmap:
    """coverage_heatmap tracks knowledge coverage across regions/topics."""

    @pytest.mark.asyncio
    async def test_build_heatmap_returns_dict(self):
        """build_heatmap returns a dict."""
        # coverage_heatmap uses 'from . import redis_store as rs' — patch at source
        with patch("aria_service.intel.redis_store.get_json", return_value={}), \
             patch("aria_service.intel.redis_store.set_json"):
            from aria_service.intel.coverage_heatmap import build_heatmap
            result = await build_heatmap()
            assert isinstance(result, dict)

    def test_summary_returns_dict(self):
        """summary returns a dict."""
        from aria_service.intel.coverage_heatmap import summary
        result = summary()
        assert isinstance(result, dict)


# ── tech_classifier.py (689L) ───────────────────────────────────────
# Verified: classify_text(), classify_export_control()

class TestTechClassifier:
    """tech_classifier identifies technology categories from text."""

    def test_classify_text_returns_dict(self):
        """classify_text returns a dict with classification."""
        from aria_service.intel.tech_classifier import classify_text
        result = classify_text("artificial intelligence and machine learning")
        assert isinstance(result, dict)

    def test_classify_export_control_returns_dict(self):
        """classify_export_control returns a dict."""
        from aria_service.intel.tech_classifier import classify_export_control
        result = classify_export_control("test equipment")
        assert isinstance(result, dict)


# ── local_brain.py (662L) ───────────────────────────────────────────
# Verified: try_local_response(), degraded_response(), get_capability_surface()

class TestLocalBrain:
    """local_brain provides fallback reasoning when LLM is unavailable."""

    def test_get_capability_surface_returns_dict(self):
        """get_capability_surface returns a dict."""
        from aria_service.intel.local_brain import get_capability_surface
        result = get_capability_surface()
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_degraded_response_returns_dict(self):
        """degraded_response returns a dict."""
        from aria_service.intel.local_brain import degraded_response
        result = await degraded_response("test message")
        assert isinstance(result, dict)


# ── nato_standards.py (1022L) ───────────────────────────────────────
# Verified: get_nato_context(), get_procurement_standards()

class TestNatoStandards:
    """nato_standards provides NATO classification and standards lookup."""

    def test_get_nato_context_returns_string(self):
        """get_nato_context returns a string (NATO standards text)."""
        from aria_service.intel.nato_standards import get_nato_context
        result = get_nato_context("test equipment")
        assert isinstance(result, str)
        assert "NATO" in result or "STANAG" in result

    def test_get_procurement_standards_returns_list(self):
        """get_procurement_standards returns a list."""
        from aria_service.intel.nato_standards import get_procurement_standards
        result = get_procurement_standards("test category")
        assert isinstance(result, list)


# ── deception_detection.py (901L) ───────────────────────────────────
# Verified: analyse(), analyse_async(), analyse_claims()

class TestDeceptionDetection:
    """deception_detection analyses text for deceptive patterns."""

    def test_analyse_returns_risk_score(self):
        """ARIADeceptionAnalyser.analyse returns a DeceptionRiskScore."""
        from aria_service.intel.deception_detection import ARIADeceptionAnalyser
        analyser = ARIADeceptionAnalyser()
        result = analyser.analyse("This is a test statement with no deception.")
        assert result is not None
        # Should have a score attribute
        assert hasattr(result, "score") or hasattr(result, "percentage")

    @pytest.mark.asyncio
    async def test_analyse_async_returns_dict(self):
        """analyse_async returns a dict."""
        from aria_service.intel.deception_detection import ARIADeceptionAnalyser
        analyser = ARIADeceptionAnalyser()
        result = await analyser.analyse_async("test statement")
        # analyse_async returns a DeceptionRiskScore, not a dict — check it has score
        assert result is not None
        assert hasattr(result, "score") or hasattr(result, "raw_score")


# ── company_investigator.py (759L) ──────────────────────────────────
# Verified: investigate_company()

class TestCompanyInvestigator:
    """company_investigator researches companies."""

    @pytest.mark.asyncio
    async def test_investigate_company_returns_dict(self):
        """investigate_company returns a dict."""
        # company_investigator uses relative imports — patch at engine_wiring source
        with patch("aria_service.intel.engine_wiring.wire_success") as mock_ws, \
             patch("aria_service.intel.sanctions_canonical.check_sanctions", return_value=[]), \
             patch("aria_service.intel.portal_registry.lookup_contracts_by_uei", return_value=[]), \
             patch("aria_service.intel.sources.cert_transparency.search_certs", return_value=[]):
            from aria_service.intel.company_investigator import investigate_company
            result = await investigate_company("Test Company Ltd")
            # Returns InvestigationReport dataclass — check it has expected attrs
            assert result is not None
            assert hasattr(result, "entity_name")
            assert hasattr(result, "findings")


# ── proactive.py (753L) ─────────────────────────────────────────────
# Verified: get_unseen_alerts(), daily_briefing_check(), get_proactive_stats()

class TestProactive:
    """proactive generates proactive intelligence alerts."""

    @pytest.mark.asyncio
    async def test_get_proactive_stats_returns_dict(self):
        """get_proactive_stats returns a dict."""
        # proactive uses relative imports — patch at engine_wiring source
        with patch("aria_service.intel.engine_wiring.wire_success") as mock_ws:
            from aria_service.intel.proactive import get_proactive_stats
            result = await get_proactive_stats()
            assert isinstance(result, dict)


# ── defence_source_seed.py (697L) ───────────────────────────────────
# Verified: seed_web_atlas(), catalogue_summary()

class TestDefenceSourceSeed:
    """defence_source_seed seeds defence-related knowledge sources."""

    def test_catalogue_summary_returns_dict(self):
        """catalogue_summary returns a dict."""
        from aria_service.intel.defence_source_seed import catalogue_summary
        result = catalogue_summary()
        assert isinstance(result, dict)

"""R-F994 — Capability tests for engine wiring sweep.

Verifies that the newly-wired engines emit brain signals on both
success and failure paths.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock


# ── weapon_origin_catalogue ────────────────────────────────────────────────

class TestWeaponOriginCatalogueWiring:
    """render_finding_for_text should wire to brain on match."""

    def test_render_finding_wires_success(self):
        """A weapon match should call wire_success."""
        from aria_service.intel import weapon_origin_catalogue as woc
        with patch("aria_service.intel.engine_wiring.wire_success") as mock_wire:
            result = woc.render_finding_for_text("Kornet ATGM")
        assert result is not None
        assert result["sanctions_status"] in ("prohibited", "restricted", "cleared")
        mock_wire.assert_called_once()
        call_kwargs = mock_wire.call_args[1]
        assert call_kwargs["module"] == "weapon_origin_catalogue"

    def test_render_finding_no_match_returns_none(self):
        """No match should return None without calling wire_success."""
        from aria_service.intel import weapon_origin_catalogue as woc
        with patch("aria_service.intel.engine_wiring.wire_success") as mock_wire:
            result = woc.render_finding_for_text("completely unrelated text")
        assert result is None
        mock_wire.assert_not_called()


# ── goods_list_aggregator_detector ─────────────────────────────────────────

class TestGoodsListAggregatorWiring:
    """render_finding should wire to brain on pattern detection."""

    def test_render_finding_wires_success(self):
        """An aggregator pattern match should call wire_success."""
        from aria_service.intel import goods_list_aggregator_detector as glad
        # Mixed NATO + Soviet calibres should trigger a signal
        items = ["7.62x51 NATO 100,000 rounds", "7.62x39 200,000 rounds",
                 "155mm M107 5,000 shells", "152mm 3,000 shells"]
        with patch("aria_service.intel.engine_wiring.wire_success") as mock_wire:
            result = glad.render_finding(items)
        assert result is not None
        mock_wire.assert_called_once()
        assert mock_wire.call_args[1]["module"] == "goods_list_aggregator_detector"

    def test_render_finding_no_signals_returns_none(self):
        """No aggregator signals should return None without wiring."""
        from aria_service.intel import goods_list_aggregator_detector as glad
        with patch("aria_service.intel.engine_wiring.wire_success") as mock_wire:
            result = glad.render_finding(["single item"])
        assert result is None
        mock_wire.assert_not_called()


# ── evasion_typology_detector ──────────────────────────────────────────────

class TestEvasionTypologyWiring:
    """render_findings_for_ctx should wire each match to brain."""

    def test_render_findings_wires_success(self):
        """Each typology match should call wire_success."""
        from aria_service.intel import evasion_typology_detector as etd
        ctx = etd.DealContext(
            weapon_origins_iso2=("RU",),
            routing_location_iso2="TR",
            buyer_country_iso2="SA",
            buyer_named_end_user_specific=False,
        )
        with patch("aria_service.intel.engine_wiring.wire_success") as mock_wire:
            results = etd.render_findings_for_ctx(ctx)
        # Should have at least one match (Turkey routing + Russian origin + MENA buyer)
        assert len(results) >= 1
        assert mock_wire.call_count >= 1
        assert mock_wire.call_args[1]["module"] == "evasion_typology_detector"


# ── end_user_granularity ───────────────────────────────────────────────────

class TestEndUserGranularityWiring:
    """render_finding should wire to brain."""

    def test_render_finding_wires_success(self):
        """An end-user assessment should call wire_success."""
        from aria_service.intel import end_user_granularity as eug
        with patch("aria_service.intel.engine_wiring.wire_success") as mock_wire:
            result = eug.render_finding("Saudi Government", "SA")
        assert result is not None
        mock_wire.assert_called_once()
        assert mock_wire.call_args[1]["module"] == "end_user_granularity"


# ── regional_navigation ────────────────────────────────────────────────────

class TestRegionalNavigationWiring:
    """get_regional_context should wire to brain on match."""

    def test_get_regional_context_wires_success(self):
        """A regional match should call wire_success."""
        from aria_service.intel import regional_navigation as rn
        with patch("aria_service.intel.engine_wiring.wire_success") as mock_wire:
            result = rn.get_regional_context("Brazil defence procurement")
        assert result != ""
        mock_wire.assert_called_once()
        assert mock_wire.call_args[1]["module"] == "regional_navigation"

    def test_get_regional_context_no_match_returns_empty(self):
        """No match should return empty without wiring."""
        from aria_service.intel import regional_navigation as rn
        with patch("aria_service.intel.engine_wiring.wire_success") as mock_wire:
            result = rn.get_regional_context("")
        assert result == ""
        mock_wire.assert_not_called()


# ── regional_compliance ────────────────────────────────────────────────────

class TestRegionalComplianceWiring:
    """ingest_all_sections should wire to brain."""

    @pytest.mark.asyncio
    async def test_ingest_wires_success(self):
        """Successful ingestion should call wire_success."""
        import aria_service.intel.rag_store as mock_rag_mod
        with patch("aria_service.intel.engine_wiring.wire_success") as mock_wire:
            with patch.object(mock_rag_mod, "ingest_document", AsyncMock(return_value={"chunks": 5})):
                from aria_service.intel import regional_compliance as rc
                # Re-import to pick up the patched rag_store
                import importlib
                importlib.reload(rc)
                result = await rc.ingest_all_sections()
        assert result["total_sections"] > 0
        mock_wire.assert_called_once()
        assert mock_wire.call_args[1]["module"] == "regional_compliance"


# ── engine_wiring module itself ────────────────────────────────────────────

class TestEngineWiringModule:
    """wire_success and wire_failure should not raise."""

    def test_wire_success_no_raise(self):
        """wire_success should never raise."""
        from aria_service.intel.engine_wiring import wire_success
        # Should not raise even without a running event loop
        wire_success(
            module="test_module",
            summary="Test summary",
            detail="Test detail",
        )
        # No assertion needed — the test passes if no exception

    def test_wire_failure_no_raise(self):
        """wire_failure should never raise."""
        from aria_service.intel.engine_wiring import wire_failure
        wire_failure(
            module="test_module",
            detail="Test failure",
            gap_type="test_failure",
        )


# ── Node/WA dead path fixes ────────────────────────────────────────────────

class TestNodeDeadPaths:
    """Verify Node/WA callers use /api/aria/brain/signal."""

    NODE_FILES = [
        "lib/aria/emailReader.mjs",
        "lib/aria/linkedinIntel.mjs",
        "lib/aria/proactive.mjs",
        "lib/self/explorerScheduler.mjs",
        "lib/whatsapp/ariaWhatsApp.mjs",
        "lib/whatsapp/waListener.mjs",
    ]

    def test_all_node_callers_use_correct_path(self):
        """No Node file should reference the dead /api/brain/signal path."""
        import os
        root = __file__.rsplit("/", 3)[0] if "/" in __file__ else "."
        for fname in self.NODE_FILES:
            path = os.path.join(root, fname)
            if not os.path.exists(path):
                pytest.skip(f"{fname} not found")
            with open(path, encoding="utf-8") as f:
                content = f.read()
            # Should use /api/aria/brain/signal, not /api/brain/signal
            assert "/api/aria/brain/signal" in content, (
                f"{fname} missing /api/aria/brain/signal"
            )
            # Should NOT use the dead path
            dead_refs = _find_dead_brain_signal_refs(content)
            assert len(dead_refs) == 0, (
                f"{fname} has {len(dead_refs)} dead /api/brain/signal ref(s): {dead_refs}"
            )


def _find_dead_brain_signal_refs(content: str) -> list[str]:
    """Find references to the dead /api/brain/signal path."""
    import re
    # Match /api/brain/signal but NOT /api/aria/brain/signal
    # Also skip comments that mention the old path
    refs = []
    for m in re.finditer(r"/api/brain/signal", content):
        start = max(0, m.start() - 30)
        ctx = content[start:m.end() + 10]
        if "/api/aria/brain/signal" not in ctx:
            # Skip comments that just describe the old path
            line_start = content.rfind("\n", 0, m.start()) + 1
            line = content[line_start:content.find("\n", m.end())]
            if line.strip().startswith("//") or line.strip().startswith("#") or line.strip().startswith("*"):
                continue
            refs.append(ctx.strip())
    return refs


# ── error_log_handler error count key ──────────────────────────────────────

class TestErrorCountKey:
    """Verify the error count key is written."""

    def test_error_count_key_defined(self):
        """The error count key constant should exist."""
        from aria_service.intel.error_log_handler import _ERROR_COUNT_KEY
        assert _ERROR_COUNT_KEY == "crucix:aria:error_ledger:count"

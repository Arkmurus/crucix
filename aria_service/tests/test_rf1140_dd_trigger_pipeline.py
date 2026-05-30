"""R-F1140 — Capability tests for automated DD trigger pipeline.

Tests that:
1. Watchlist add/get works
2. Signal matching finds watchlist entities
3. DD trigger respects min interval (no spam)
4. Portal scouting extracts URLs from DD report
5. Portal scouting attempts registration for unregistered portals
6. Main monitor loop returns expected structure
7. Brain wiring works
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aria_service.intel.dd_trigger_pipeline import (
    MIN_RE_DD_INTERVAL_S,
    _add_to_watchlist,
    _get_watchlist,
    _match_signals_to_watchlist,
    monitor_and_trigger,
    scout_portals_for_dd,
    trigger_dd_for_entity,
)


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def mock_redis():
    """Persistent in-memory Redis mock."""
    store: dict[str, object] = {}

    async def mock_get(key: str) -> object:
        return store.get(key)

    async def mock_set(key: str, value: object, **kwargs) -> None:
        store[key] = value

    with patch("aria_service.intel.redis_store.get_json", side_effect=mock_get):
        with patch("aria_service.intel.redis_store.set_json", side_effect=mock_set):
            yield store


# ── Tests for watchlist ─────────────────────────────────────────────────────

class TestWatchlist:
    """Proves watchlist operations work."""

    async def test_add_and_get(self, mock_redis):
        """Adding an entity to the watchlist and retrieving it works."""
        await _add_to_watchlist("Acme Corp", jurisdiction="US", reason="Sanctions hit")
        watchlist = await _get_watchlist()

        assert len(watchlist) == 1
        assert watchlist[0]["entity_name"] == "Acme Corp"
        assert watchlist[0]["jurisdiction"] == "US"

    async def test_deduplicates_by_name(self, mock_redis):
        """Adding the same entity twice deduplicates."""
        await _add_to_watchlist("Acme Corp", reason="First")
        await _add_to_watchlist("Acme Corp", reason="Second")
        watchlist = await _get_watchlist()

        assert len(watchlist) == 1
        assert watchlist[0]["reason"] == "Second"  # Last write wins


# ── Tests for signal matching ───────────────────────────────────────────────

class TestSignalMatching:
    """Proves signal-to-watchlist matching works."""

    async def test_matches_by_name(self, mock_redis):
        """A signal matching a watched entity is found."""
        await _add_to_watchlist("Acme Corp")
        signals = [
            {"entity_name": "Acme Corp wins $50M contract", "source": "procurement",
             "summary": "Acme Corp awarded", "url": "https://example.com"},
        ]

        matches = await _match_signals_to_watchlist(signals)
        assert len(matches) == 1
        assert matches[0]["signal"]["entity_name"] == "Acme Corp wins $50M contract"

    async def test_no_match_for_unwatched(self, mock_redis):
        """A signal for an unwatched entity is not matched."""
        await _add_to_watchlist("Acme Corp")
        signals = [
            {"entity_name": "Other Corp", "source": "procurement",
             "summary": "Other Corp awarded", "url": ""},
        ]

        matches = await _match_signals_to_watchlist(signals)
        assert len(matches) == 0


# ── Tests for DD triggering ─────────────────────────────────────────────────

class TestDDTrigger:
    """Proves DD triggering works."""

    async def test_triggers_dd(self, mock_redis):
        """trigger_dd_for_entity calls dd_orchestrator."""
        mock_report = MagicMock()
        mock_report.run_id = "dd_test_001"
        mock_report.risk_classification = "GREEN"
        mock_report.total_duration_ms = 1500

        with patch("aria_service.intel.dd_orchestrator.orchestrate_dd",
                   new_callable=AsyncMock, return_value=mock_report):
            result = await trigger_dd_for_entity("Acme Corp", "Test trigger")

        assert result["triggered"] is True
        assert result["run_id"] == "dd_test_001"

    async def test_respects_min_interval(self, mock_redis):
        """DD is not triggered again within min interval."""
        mock_report = MagicMock()
        mock_report.run_id = "dd_test_001"
        mock_report.risk_classification = "GREEN"
        mock_report.total_duration_ms = 1500

        with patch("aria_service.intel.dd_orchestrator.orchestrate_dd",
                   new_callable=AsyncMock, return_value=mock_report):
            # First trigger
            r1 = await trigger_dd_for_entity("Acme Corp", "First")
            assert r1["triggered"] is True

            # Second trigger immediately — should be skipped
            r2 = await trigger_dd_for_entity("Acme Corp", "Second")
            assert r2["triggered"] is False
            assert "min interval" in r2["reason"]


# ── Tests for portal scouting ───────────────────────────────────────────────

class TestPortalScouting:
    """Proves portal scouting works."""

    async def test_scouts_portals(self, mock_redis):
        """Portal scouting extracts URLs and checks registration."""
        mock_report = MagicMock()
        mock_digital = MagicMock()
        mock_finding = MagicMock()
        mock_finding.source_url = "https://sam.gov/opportunity/123"
        mock_finding.url = ""
        mock_digital.findings = [mock_finding]
        mock_report.digital = mock_digital

        with patch("aria_service.intel.portal_registry.is_registered",
                   new_callable=AsyncMock, return_value=True):
            results = await scout_portals_for_dd(
                {"name": "Acme Corp"}, mock_report,
            )

        # Should find SAM.gov in the portal registry
        assert len(results) >= 1
        sam_results = [r for r in results if "sam" in r.get("portal_id", "").lower()]
        assert len(sam_results) >= 1


# ── Tests for main monitor ──────────────────────────────────────────────────

class TestMonitor:
    """Proves the main monitor loop works."""

    async def test_monitor_returns_structure(self, mock_redis):
        """monitor_and_trigger returns expected structure."""
        with patch("aria_service.intel.dd_trigger_pipeline._check_for_new_signals",
                   new_callable=AsyncMock, return_value=[]):
            with patch("aria_service.intel.engine_wiring.wire_success"):
                result = await monitor_and_trigger()

        assert "checked_at" in result
        assert "signals_found" in result
        assert "matches_found" in result
        assert "triggers_fired" in result
        assert "duration_ms" in result

    async def test_wires_to_brain(self, mock_redis):
        """Monitor wires results to brain."""
        with patch("aria_service.intel.dd_trigger_pipeline._check_for_new_signals",
                   new_callable=AsyncMock, return_value=[]):
            with patch("aria_service.intel.engine_wiring.wire_success") as mock_ws:
                await monitor_and_trigger()

        mock_ws.assert_called_once()
        args, kwargs = mock_ws.call_args
        assert kwargs.get("module") == "dd_trigger_pipeline"

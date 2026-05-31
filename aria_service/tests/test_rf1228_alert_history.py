"""R-F1228: Capability test — real-time intelligence notification system.

Verifies:
1. push_alert stores alerts with correct alert_type classification
2. get_alert_history returns alerts regardless of seen status
3. get_alert_by_id returns a single alert
4. get_alert_stats returns correct counts
5. Alert type mapping works for all known types
6. Filtering by alert_type and severity works
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.fixture
def sample_alerts():
    """Create sample alerts for testing."""
    return [
        {
            "id": "alert_1000_0",
            "type": "country_mention_spike",
            "alert_type": "anomaly",
            "title": "Mention spike: Cape Verde",
            "severity": "high",
            "body": "Cape Verde mentioned 12x in last sweep",
            "ts": 1000.0,
            "seen": False,
        },
        {
            "id": "alert_2000_1",
            "type": "daily_briefing",
            "alert_type": "briefing",
            "title": "Morning briefing",
            "severity": "info",
            "body": "Today's intelligence digest",
            "ts": 2000.0,
            "seen": True,
        },
        {
            "id": "alert_3000_2",
            "type": "knowledge_gap",
            "alert_type": "research",
            "title": "Knowledge gap: Angola",
            "severity": "medium",
            "body": "Team asked about Angola 5x",
            "ts": 3000.0,
            "seen": False,
        },
        {
            "id": "alert_4000_3",
            "type": "conflict_alert",
            "alert_type": "intel",
            "title": "Conflict alert: Mozambique",
            "severity": "critical",
            "body": "New insurgency activity detected",
            "ts": 4000.0,
            "seen": False,
        },
    ]


class TestPushAlert:
    """push_alert stores alerts with correct classification."""

    @pytest.mark.asyncio
    async def test_push_alert_classifies_country_spike(self):
        """country_mention_spike maps to anomaly type."""
        with patch("aria_service.intel.proactive.rs.get_json", return_value=[]), \
             patch("aria_service.intel.proactive.rs.set_json") as mock_set:
            from aria_service.intel.proactive import push_alert
            await push_alert({
                "type": "country_mention_spike",
                "title": "Test spike",
                "severity": "high",
                "body": "Test body",
            })
            stored = mock_set.call_args[0][1]
            assert len(stored) == 1
            assert stored[0]["alert_type"] == "anomaly"

    @pytest.mark.asyncio
    async def test_push_alert_classifies_daily_briefing(self):
        """daily_briefing maps to briefing type."""
        with patch("aria_service.intel.proactive.rs.get_json", return_value=[]), \
             patch("aria_service.intel.proactive.rs.set_json") as mock_set:
            from aria_service.intel.proactive import push_alert
            await push_alert({
                "type": "daily_briefing",
                "title": "Test briefing",
                "severity": "info",
                "body": "Test body",
            })
            stored = mock_set.call_args[0][1]
            assert stored[0]["alert_type"] == "briefing"

    @pytest.mark.asyncio
    async def test_push_alert_defaults_to_intel(self):
        """Unknown alert types default to intel."""
        with patch("aria_service.intel.proactive.rs.get_json", return_value=[]), \
             patch("aria_service.intel.proactive.rs.set_json") as mock_set:
            from aria_service.intel.proactive import push_alert
            await push_alert({
                "type": "unknown_type",
                "title": "Test",
                "severity": "info",
                "body": "Test body",
            })
            stored = mock_set.call_args[0][1]
            assert stored[0]["alert_type"] == "intel"


class TestGetAlertHistory:
    """get_alert_history returns alerts regardless of seen status."""

    @pytest.mark.asyncio
    async def test_returns_all_alerts(self, sample_alerts):
        """Returns all alerts including seen ones."""
        with patch("aria_service.intel.proactive.rs.get_json") as mock_get:
            mock_get.side_effect = [sample_alerts, []]
            from aria_service.intel.proactive import get_alert_history
            alerts = await get_alert_history(limit=10)
            assert len(alerts) == 4

    @pytest.mark.asyncio
    async def test_filters_by_alert_type(self, sample_alerts):
        """Filters correctly by alert_type."""
        with patch("aria_service.intel.proactive.rs.get_json") as mock_get:
            mock_get.side_effect = [sample_alerts, []]
            from aria_service.intel.proactive import get_alert_history
            alerts = await get_alert_history(limit=10, alert_type="anomaly")
            assert len(alerts) == 1
            assert alerts[0]["type"] == "country_mention_spike"

    @pytest.mark.asyncio
    async def test_filters_by_severity(self, sample_alerts):
        """Filters correctly by severity."""
        with patch("aria_service.intel.proactive.rs.get_json") as mock_get:
            mock_get.side_effect = [sample_alerts, []]
            from aria_service.intel.proactive import get_alert_history
            alerts = await get_alert_history(limit=10, severity="critical")
            assert len(alerts) == 1
            assert alerts[0]["type"] == "conflict_alert"

    @pytest.mark.asyncio
    async def test_respects_limit(self, sample_alerts):
        """Respects the limit parameter."""
        with patch("aria_service.intel.proactive.rs.get_json") as mock_get:
            mock_get.side_effect = [sample_alerts, []]
            from aria_service.intel.proactive import get_alert_history
            alerts = await get_alert_history(limit=2)
            assert len(alerts) == 2


class TestGetAlertById:
    """get_alert_by_id returns a single alert."""

    @pytest.mark.asyncio
    async def test_finds_alert_by_id(self, sample_alerts):
        """Returns the correct alert by ID."""
        with patch("aria_service.intel.proactive.rs.get_json") as mock_get:
            mock_get.side_effect = [sample_alerts, []]
            from aria_service.intel.proactive import get_alert_by_id
            alert = await get_alert_by_id("alert_1000_0")
            assert alert is not None
            assert alert["title"] == "Mention spike: Cape Verde"

    @pytest.mark.asyncio
    async def test_returns_none_for_missing(self):
        """Returns None for non-existent ID."""
        with patch("aria_service.intel.proactive.rs.get_json", return_value=[]):
            from aria_service.intel.proactive import get_alert_by_id
            alert = await get_alert_by_id("nonexistent")
            assert alert is None


class TestGetAlertStats:
    """get_alert_stats returns correct counts."""

    @pytest.mark.asyncio
    async def test_returns_correct_counts(self, sample_alerts):
        """Returns correct total, unseen, by_type, by_severity."""
        with patch("aria_service.intel.proactive.rs.get_json", return_value=sample_alerts):
            from aria_service.intel.proactive import get_alert_stats
            stats = await get_alert_stats()
            assert stats["total"] == 4
            assert stats["unseen"] == 3
            assert stats["by_type"].get("anomaly") == 1
            assert stats["by_type"].get("briefing") == 1
            assert stats["by_type"].get("research") == 1
            assert stats["by_type"].get("intel") == 1
            assert stats["by_severity"].get("critical") == 1
            assert stats["by_severity"].get("high") == 1
            assert stats["by_severity"].get("medium") == 1
            assert stats["by_severity"].get("info") == 1

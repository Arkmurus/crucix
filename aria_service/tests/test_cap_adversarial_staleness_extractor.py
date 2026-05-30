"""
Capability test: R-F1166 — AdversarialStalenessExtractor detects stale scores.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta
import json


@pytest.mark.asyncio
async def test_adversarial_staleness_detects_old_run():
    """When the last adversarial run is >48h old, a gap must be produced."""
    from aria_service.autonomous.gap_detector import AdversarialStalenessExtractor

    mock_redis = MagicMock()
    old_run = {
        "run_at": (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat(),
        "overall_score": 0.65,
        "degraded": False,
        "invalid": False,
    }
    mock_redis.get = AsyncMock(return_value=json.dumps(old_run).encode())

    extractor = AdversarialStalenessExtractor(mock_redis)
    gaps = await extractor.extract(datetime.now(timezone.utc) - timedelta(days=7))

    assert len(gaps) >= 1, f"Expected at least 1 gap for stale adversarial, got {len(gaps)}"
    gap_ids = [g.gap_id for g in gaps]
    assert "adversarial_stale" in gap_ids, f"Expected adversarial_stale gap, got {gap_ids}"
    assert gaps[0].severity.value == 3 or str(gaps[0].severity) == "GapSeverity.HIGH", (
        f"Expected HIGH severity, got {gaps[0].severity}"
    )


@pytest.mark.asyncio
async def test_adversarial_staleness_skips_fresh_run():
    """When the last adversarial run is <48h old, no gap should be produced."""
    from aria_service.autonomous.gap_detector import AdversarialStalenessExtractor

    mock_redis = MagicMock()
    fresh_run = {
        "run_at": (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat(),
        "overall_score": 0.85,
        "degraded": False,
        "invalid": False,
    }
    mock_redis.get = AsyncMock(return_value=json.dumps(fresh_run).encode())

    extractor = AdversarialStalenessExtractor(mock_redis)
    gaps = await extractor.extract(datetime.now(timezone.utc) - timedelta(days=7))

    stale_gaps = [g for g in gaps if g.gap_id == "adversarial_stale"]
    assert len(stale_gaps) == 0, f"Expected no stale gaps for fresh run, got {len(stale_gaps)}"


@pytest.mark.asyncio
async def test_adversarial_staleness_never_run():
    """When no adversarial run exists, a gap must be produced."""
    from aria_service.autonomous.gap_detector import AdversarialStalenessExtractor

    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(return_value=None)

    extractor = AdversarialStalenessExtractor(mock_redis)
    gaps = await extractor.extract(datetime.now(timezone.utc) - timedelta(days=7))

    gap_ids = [g.gap_id for g in gaps]
    assert "adversarial_never_run" in gap_ids, f"Expected adversarial_never_run gap, got {gap_ids}"

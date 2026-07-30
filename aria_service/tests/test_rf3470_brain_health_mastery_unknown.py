"""R-F3470 — /health must not certify an unreadable mastery signal."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_health_degrades_when_mastery_measurement_fails(monkeypatch):
    """Drive the real health handler through the mastery failure path."""
    from aria_service.intel import endpoint_cache, student
    from aria_service.routes import aria

    async def _mastery_failure():
        raise RuntimeError("mastery store unreadable")

    monkeypatch.setattr(student, "get_mastery_report", _mastery_failure)
    entry = endpoint_cache._REGISTRY.get("health")
    if entry is not None:
        entry.value = endpoint_cache._MISS
        entry.ts = 0.0

    result = await aria.health_check_ep()

    assert result["status"] == "degraded"
    assert "mastery_unknown" in result["degraded_reasons"]
    assert result["quality"]["mastery_overall"] is None
    assert result["quality"]["core_mastery_breakdown"] == {}

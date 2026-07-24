"""R-F2988 — ARIA Brain top health includes the real ecosystem overlay."""
from __future__ import annotations

import pytest


def _bust_health_cache():
    from aria_service.intel import endpoint_cache

    entry = endpoint_cache._REGISTRY.get("health")
    if entry is not None:
        entry.value = endpoint_cache._MISS
        entry.ts = 0.0


@pytest.mark.asyncio
async def test_health_degrades_when_real_ecosystem_path_has_red_nodes(monkeypatch):
    """Capability: drive /health's real handler and reproduce the contradiction."""
    from aria_service.routes import aria
    from aria_service.intel import ecosystem_map

    async def _coverage():
        return {
            "health_sensors": {
                "total_nodes": 578,
                "with_live_sensor": 31,
                "grey_no_sensor": 547,
                "by_color": {"green": 20, "amber": 9, "red": 2},
            },
            "meta": {"generated_at": 123.0},
        }

    monkeypatch.setattr(ecosystem_map, "get_coverage", _coverage)
    _bust_health_cache()
    result = await aria.health_check_ep()

    assert result["status"] == "degraded"
    assert "ecosystem_red_nodes_2" in result["degraded_reasons"]
    assert result["ecosystem_health"] == {
        "red": 2,
        "amber": 9,
        "green": 20,
        "grey": 547,
        "with_live_sensor": 31,
        "total_nodes": 578,
        "measured_at": 123.0,
        "error": None,
    }


@pytest.mark.asyncio
async def test_health_does_not_claim_healthy_when_ecosystem_is_unmeasurable(
    monkeypatch,
):
    """Failure path: an unavailable ecosystem signal is UNKNOWN, never green."""
    from aria_service.routes import aria
    from aria_service.intel import ecosystem_map

    async def _broken_coverage():
        raise RuntimeError("sensor store unavailable")

    monkeypatch.setattr(ecosystem_map, "get_coverage", _broken_coverage)
    _bust_health_cache()
    result = await aria.health_check_ep()

    assert result["status"] == "degraded"
    assert "ecosystem_health_unknown" in result["degraded_reasons"]
    assert result["ecosystem_health"]["red"] is None
    assert "RuntimeError: sensor store unavailable" in (
        result["ecosystem_health"]["error"] or ""
    )

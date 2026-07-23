"""R-F2738 — customer intel must be graded, deduplicated, and tenant-safe."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from aria_service.intel import agent_signup_vault as asv
from aria_service.intel import news_monitor as nm


def test_global_news_poller_excludes_tenant_owned_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    """A user's private feed must never enter the shared Golden Intel store."""
    vault = MagicMock()
    vault.list.return_value = [
        {
            "site_id": "admin-source",
            "site_name": "Official procurement feed",
            "site_url": "https://procurement.example/rss",
            "site_type": "rss",
            "status": "verified",
            "agent_id": "aria_main",
        },
        {
            "site_id": "private-source",
            "site_name": "Customer private feed",
            "site_url": "https://customer.example/rss",
            "site_type": "rss",
            "status": "verified",
            "agent_id": "user:customer-123",
        },
    ]
    monkeypatch.setattr(asv, "get_vault", lambda: vault)

    sources = nm._get_vault_feed_sources()

    assert [source[0] for source in sources] == ["vault:Official procurement feed"]
    assert "https://customer.example/rss" not in nm._VAULT_URL_TO_ID


@pytest.mark.asyncio
async def test_recent_intel_endpoint_returns_only_unique_grade_a_and_b(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The customer endpoint must suppress C/REJECT and exact duplicate events."""
    base = {
        "id": "grade-a-first-ingest",
        "signal_type": "sanctions_change",
        "priority": "HIGH",
        "confidence": "HIGH",
        "source_tier": "tier_1a",
        "source": "OFAC",
        "url": "https://ofac.example/designation/123",
        "title": "OFAC designates Example Entity",
        "decision_summary": "OFAC designates Example Entity",
        "why_it_matters": "A watched counterparty may now be restricted.",
        "recommended_action": "Review exposure",
        "entities": {"oems": ["Example Entity"], "countries": [], "products": []},
        "detected_at": "2026-07-18T10:00:00+00:00",
    }
    grade_b = {
        **base,
        "id": "grade-b",
        "signal_type": "active_tender",
        "source_tier": "tier_2",
        "source": "Janes",
        "url": "https://janes.example/tender/456",
        "title": "Portugal considers radar tender",
        "decision_summary": "Portugal considers radar tender",
        "entities": {"countries": ["Portugal"], "oems": [], "products": ["radar"]},
    }
    rejected = {
        **base,
        "id": "rejected",
        "signal_type": "situational_awareness",
        "priority": "LOW",
        "url": "https://example.com/sport",
        "title": "World rugby update",
        "entities": {},
    }
    duplicate_new_backend_id = {**base, "id": "grade-a-reingested"}
    rows = [base, duplicate_new_backend_id, grade_b, rejected]

    async def fake_lrange(key: str, _start: int, _end: int) -> list[str]:
        assert key == nm._INTEL_SIGNALS_KEY
        return [json.dumps(row) for row in rows]

    async def fake_get_json(_key: str) -> dict:
        return {
            "status": "ok",
            "last_poll_at": "2026-07-18T10:05:00+00:00",
            "last_success_at": "2026-07-18T10:05:00+00:00",
        }

    monkeypatch.setattr(nm.rs, "lrange", fake_lrange)
    monkeypatch.setattr(nm.rs, "get_json", fake_get_json)
    monkeypatch.setattr(nm.time, "time", lambda: 1784369400.0)

    result = await nm.get_recent_intel_signals(limit=20)

    assert result["count"] == 2
    assert [signal["intel_grade"] for signal in result["signals"]] == ["A", "B"]
    assert result["suppressed"]["non_publishable"] == 1
    assert result["suppressed"]["duplicates"] == 1
    assert result["suppressed"]["over_limit"] == 0
    assert result["schema_version"] == "rf2738.v1"

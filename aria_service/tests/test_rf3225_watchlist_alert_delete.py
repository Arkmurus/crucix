"""R-F3225 — owner-scoped deletion for individual Recent Alerts."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from aria_service.intel import dd_orchestrator as dd
from aria_service.intel import state_store


def test_alert_list_assigns_stable_ids_and_delete_removes_exact_raw_entry():
    alert = {
        "entity": "Charles Woodburn",
        "change_type": "new_pep",
        "timestamp": "2026-07-25T11:52:48+00:00",
        "detail": "New PEP match.",
    }
    raw = json.dumps(alert)

    async def run():
        with (
            patch.object(dd, "get_watchlist", AsyncMock(return_value=[{"name": "Charles Woodburn"}])),
            patch("aria_service.intel.redis_store.lrange", AsyncMock(return_value=[raw])),
            patch("aria_service.intel.redis_store.get", AsyncMock(return_value=None)),
            patch("aria_service.intel.redis_store.lrem", AsyncMock(return_value=1)) as lrem,
        ):
            listed = await dd.get_watchlist_alerts(
                since_hours=24 * 365,
                user_id="owner-1",
                user_email_domain="example.com",
            )
            result = await dd.delete_watchlist_alert(
                listed[0]["alert_id"],
                user_id="owner-1",
                user_email_domain="example.com",
            )
            return listed, result, lrem

    listed, result, lrem = asyncio.run(run())
    assert len(listed[0]["alert_id"]) == 24
    assert result["ok"] is True
    lrem.assert_awaited_once_with(dd.WATCHLIST_ALERTS_KEY, 1, raw)


def test_delete_cannot_remove_alert_outside_caller_scope():
    alert = {
        "entity": "Other Tenant Entity",
        "timestamp": "2026-07-25T11:52:48+00:00",
    }
    raw = json.dumps(alert)

    async def run():
        with (
            patch.object(dd, "get_watchlist", AsyncMock(return_value=[])),
            patch("aria_service.intel.redis_store.lrange", AsyncMock(return_value=[raw])),
            patch("aria_service.intel.redis_store.get", AsyncMock(return_value=None)),
            patch("aria_service.intel.redis_store.lrem", AsyncMock()) as lrem,
        ):
            result = await dd.delete_watchlist_alert(
                dd._watchlist_alert_id(alert),
                user_id="owner-1",
                user_email_domain="example.com",
            )
            return result, lrem

    result, lrem = asyncio.run(run())
    assert result == {"ok": False, "removed": 0, "reason": "alert not found"}
    lrem.assert_not_awaited()


def test_due_only_rescreen_skips_entity_until_its_own_cycle():
    entry = {
        "name": "Acme Ltd",
        "added_at": "2999-01-01T00:00:00+00:00",
        "review_interval_hours": 168,
    }

    async def stored(key):
        return [entry] if key == dd.WATCHLIST_KEY else None

    async def run():
        with (
            patch("aria_service.intel.redis_store.get_json", side_effect=stored),
            patch.object(dd, "_acquire_rescreen_lock", AsyncMock(return_value=True)),
            patch.object(dd, "_release_rescreen_lock", AsyncMock()) as release,
        ):
            result = await dd.rescreen_watchlist(due_only=True)
            return result, release

    result, release = asyncio.run(run())
    assert result["entities_screened"] == 0
    assert result["not_due"] == 1
    release.assert_awaited_once_with("global")


def test_schedule_update_is_validated_and_owner_scoped():
    entries = [
        {"name": "Shared Name", "user_id": "owner-a", "review_interval_hours": 24},
        {"name": "Shared Name", "user_id": "owner-b", "review_interval_hours": 24},
    ]

    async def run():
        with (
            patch("aria_service.intel.redis_store.get_json", AsyncMock(return_value=entries)),
            patch("aria_service.intel.redis_store.set_json", AsyncMock()) as save,
        ):
            result = await dd.update_watchlist_schedule(
                "Shared Name", 720, user_id="owner-a")
            return result, save

    result, save = asyncio.run(run())
    assert result["ok"] is True
    assert entries[0]["review_interval_hours"] == 720
    assert entries[1]["review_interval_hours"] == 24
    save.assert_awaited_once()

    try:
        dd._watchlist_review_hours(48)
    except ValueError as exc:
        assert "must be one of" in str(exc)
    else:
        raise AssertionError("unsupported cadence was accepted")


@pytest.mark.asyncio
async def test_sqlite_lrem_deletes_only_the_selected_duplicate(tmp_path):
    await state_store.close()
    assert await state_store.connect(str(tmp_path / "rf3225.db")) is True
    try:
        await state_store.lpush("alerts", "keep")
        await state_store.lpush("alerts", "delete-me")
        await state_store.lpush("alerts", "delete-me")
        removed = await state_store.lrem("alerts", 1, "delete-me")
        assert removed == 1
        assert await state_store.lrange("alerts", 0, -1) == ["delete-me", "keep"]
    finally:
        await state_store.close()

"""R-F2611 — deleting a DD report must STREAMLINE: also remove the watchlist entry
that DD auto-enrolled (R-F878 `source="dd_auto_enroll"`), so a deleted report doesn't
linger in the dashboard Watchlist box / Watchlist page. Tenant-safe; preserves manual
entries and entities the owner still has another DD for.

Drives the REAL `dd_orchestrator.delete_report` against the in-memory store (conftest
sets ARIA_STATE_BACKEND=memory).
"""
import asyncio
from aria_service.intel import dd_orchestrator as dd
from aria_service.intel import redis_store as rs

CID = "company:BR:1"


def _seed(index, blob_run_id, watchlist):
    async def go():
        await rs.set_json(dd.REPORT_INDEX_KEY, index)
        await rs.set_json(dd.REPORT_REDIS_KEY.format(run_id=blob_run_id),
                          {"run_id": blob_run_id, "canonical_entity_id": CID, "user_id": "userX"})
        await rs.set_json(dd.WATCHLIST_KEY, watchlist)
        result = await dd.delete_report(blob_run_id)
        wl = await rs.get_json(dd.WATCHLIST_KEY) or []
        return result, wl
    return asyncio.run(go())


def test_rf2611_deleting_last_dd_removes_auto_enrolled_watchlist():
    result, wl = _seed(
        index=[{"run_id": "dd_del", "canonical_entity_id": CID, "user_id": "userX"}],
        blob_run_id="dd_del",
        watchlist=[
            {"name": "Modirum Gespi", "canonical_entity_id": CID, "source": "dd_auto_enroll", "user_id": "userX"},
            {"name": "Manual Co", "canonical_entity_id": "company:BR:2", "source": "manual", "user_id": "userX"},
            {"name": "Modirum Gespi", "canonical_entity_id": CID, "source": "dd_auto_enroll", "user_id": "userY"},
        ],
    )
    assert "Modirum Gespi" in (result.get("watchlist_entries_removed") or [])
    names = {(w.get("name"), w.get("user_id")) for w in wl}
    assert ("Modirum Gespi", "userX") not in names          # auto-enrolled owner entry removed
    assert ("Manual Co", "userX") in names                  # manual entry preserved
    assert ("Modirum Gespi", "userY") in names              # OTHER tenant's entry preserved (isolation)


def test_rf2611_watchlist_kept_when_owner_has_another_dd():
    # Owner still has ANOTHER DD report for the same entity → keep the watchlist entry.
    result, wl = _seed(
        index=[
            {"run_id": "dd_del", "canonical_entity_id": CID, "user_id": "userX"},
            {"run_id": "dd_keep", "canonical_entity_id": CID, "user_id": "userX"},
        ],
        blob_run_id="dd_del",
        watchlist=[{"name": "Modirum", "canonical_entity_id": CID, "source": "dd_auto_enroll", "user_id": "userX"}],
    )
    assert not (result.get("watchlist_entries_removed") or [])
    assert any(w.get("name") == "Modirum" for w in wl)


def test_rf2611_manual_watchlist_never_removed_on_delete():
    # A manually-added watchlist entry must survive deleting the DD.
    result, wl = _seed(
        index=[{"run_id": "dd_del", "canonical_entity_id": CID, "user_id": "userX"}],
        blob_run_id="dd_del",
        watchlist=[{"name": "Kept Manual", "canonical_entity_id": CID, "source": "manual", "user_id": "userX"}],
    )
    assert not (result.get("watchlist_entries_removed") or [])
    assert any(w.get("name") == "Kept Manual" for w in wl)

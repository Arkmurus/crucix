"""R-F1392 capability test — a job-store READ failure must answer 503 (the
poller retries), never not_found (the poller declares a LIVE job expired).

Live failure 2026-06-07 ("CIS of VCR S.L_.pdf"): a state_store self-heal window
made a poll read return None -> not_found -> the WA listener threw
"extraction job expired" 38s into a healthy extraction (the job TTL is 1 hour).
Same mechanism produced 4x "chat job expired" on 2026-06-06.

Capability assertions drive the REAL poll endpoints (read_document_result_ep /
chat_result_ep) — the exact functions the WA listener polls.
"""
import pytest
from fastapi import HTTPException

from aria_service.intel import redis_store, state_store
from aria_service.routes import aria as aria_routes


# ── capability: the endpoints the WA listener actually polls ────────────────

@pytest.mark.asyncio
async def test_readdoc_store_error_returns_503_not_not_found(monkeypatch):
    async def _boom(key):
        raise redis_store.StoreReadError("Cannot operate on a closed database")
    monkeypatch.setattr(redis_store, "get_json_strict", _boom)
    with pytest.raises(HTTPException) as ei:
        await aria_routes.read_document_result_ep("job123")
    assert ei.value.status_code == 503


@pytest.mark.asyncio
async def test_readdoc_missing_key_is_still_not_found(monkeypatch):
    async def _none(key):
        return None
    monkeypatch.setattr(redis_store, "get_json_strict", _none)
    res = await aria_routes.read_document_result_ep("job123")
    assert res["status"] == "not_found"


@pytest.mark.asyncio
async def test_readdoc_live_job_passes_through(monkeypatch):
    async def _done(key):
        return {"status": "done", "result": {"ok": True}}
    monkeypatch.setattr(redis_store, "get_json_strict", _done)
    res = await aria_routes.read_document_result_ep("job123")
    assert res["status"] == "done"
    assert res["result"] == {"ok": True}


@pytest.mark.asyncio
async def test_chat_store_error_returns_503_not_not_found(monkeypatch):
    async def _boom(key):
        raise redis_store.StoreReadError("Cannot operate on a closed database")
    monkeypatch.setattr(redis_store, "get_json_strict", _boom)
    with pytest.raises(HTTPException) as ei:
        await aria_routes.chat_result_ep("job456")
    assert ei.value.status_code == 503


@pytest.mark.asyncio
async def test_chat_missing_key_is_still_not_found(monkeypatch):
    async def _none(key):
        return None
    monkeypatch.setattr(redis_store, "get_json_strict", _none)
    res = await aria_routes.chat_result_ep("job456")
    assert res["status"] == "not_found"


# ── units: the strict readers raise instead of swallowing ───────────────────

@pytest.mark.asyncio
async def test_state_store_get_strict_raises_when_conn_down(monkeypatch):
    # the reconnect window sets _conn = None — pre-R-F1392 a read here
    # returned None and was indistinguishable from key-missing
    monkeypatch.setattr(state_store, "_conn", None)
    with pytest.raises(state_store.StateReadError):
        await state_store.get_strict("any:key")


@pytest.mark.asyncio
async def test_redis_store_get_strict_translates_state_error(monkeypatch):
    monkeypatch.setattr(redis_store, "_BACKEND", "sqlite")

    async def _raise(key):
        raise state_store.StateReadError("closed")
    monkeypatch.setattr(state_store, "get_strict", _raise)
    with pytest.raises(redis_store.StoreReadError):
        await redis_store.get_strict("k")


@pytest.mark.asyncio
async def test_redis_store_get_json_strict_returns_value(monkeypatch):
    monkeypatch.setattr(redis_store, "_BACKEND", "sqlite")

    async def _ok(key):
        return '{"status": "processing"}'
    monkeypatch.setattr(state_store, "get_strict", _ok)
    assert await redis_store.get_json_strict("k") == {"status": "processing"}

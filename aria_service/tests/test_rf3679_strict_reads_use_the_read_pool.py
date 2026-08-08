"""R-F3679 — strict reads ran on the WRITE connection and timed out behind writes.

``state_store.get_strict`` executed its SELECT on ``_conn``, the single write
connection. Reads there queue behind write traffic, so on a busy store every
strict read hit the 5s timeout and raised — while the graceful ``get()``, which
goes through ``_row`` -> ``_reader_conn_for`` -> ``_get_read_conn`` (R-F1449's
dedicated read connection, round-robined by R-F2242, never touched by
``_reconnect``), kept succeeding on the same keys.

R-F1449 moved the READ path off the write connection. The STRICT path was never
moved with it.

Measured live on aria-intel 2026-08-04: six consecutive probes of /phase/gates,
strict reads failing 100% while graceful reads succeeded 100%, on keys present on
disk. ``crucix:autonomous:enabled_override`` is a ONE-CHARACTER value and timed
out too — never a payload-size problem. Live log line:
``state_store: SELECT crucix:aria:error_streak:anchor timed out after 5s``.

Why it matters: the strict readers exist (R-F1392) precisely to tell "genuinely
absent" from "the store broke". When they raise, that distinction inverts —
  * Phase A gates 2/3/5/6 became unmeasurable; gate 5 could not see the autonomy
    override that is the ONLY thing keeping autonomy on, so it reported env `0`
  * ``_load_feed_health`` returned None, so R-F2890's quarantine self-heal was
    skipped on EVERY poll
  * ``dd_orchestrator``'s report-blob read — customer-facing — could not fetch a
    report that exists

All confirmed FAILING against the pre-fix code (§3c).
"""

import asyncio

import pytest

from aria_service.intel import state_store as ss

# R-F3773/§16 — NOT inspect.getsource: it slices at line numbers captured AT
# IMPORT, so a mid-run edit silently returns a DIFFERENT function's body. A CLASS
# target scopes the lookup to that class's own body (R-F3771).
from ._source_probe import function_source


@pytest.fixture
async def store(tmp_path, monkeypatch):
    """§3b: the initialiser is `connect()`, not `init()` — verified, not assumed."""
    monkeypatch.setenv("ARIA_STATE_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setattr(ss, "_DB_PATH", tmp_path / "state.db", raising=False)
    if ss._conn is None:
        await ss.connect()
    yield ss


@pytest.mark.asyncio
async def test_rf3679_strict_read_does_not_use_the_write_connection(tmp_path):
    """THE DEFECT: the strict SELECT must not run on `_conn`.

    Pinned structurally because the live symptom (a 5s timeout under write load)
    cannot be reproduced deterministically in a unit test — but the wrong
    connection can be.
    """
    from . import _source_probe

    src = _source_probe.function_source(ss, "get_strict")
    body = src.split('"""', 2)[-1]          # drop the docstring
    assert "_conn.execute" not in body, (
        "get_strict still executes on the WRITE connection; reads there queue "
        "behind write traffic and time out"
    )
    assert "_reader_conn_for" in body, (
        "get_strict must select its connection the same way _row does"
    )


@pytest.mark.asyncio
async def test_rf3679_strict_read_returns_the_value(store):
    """The contract still holds: a present key reads back."""
    await store.set_key("_rf3679_k1", "v1")
    assert await store.get_strict("_rf3679_k1") == "v1"


@pytest.mark.asyncio
async def test_rf3679_absent_key_is_none_not_an_error(store):
    """A genuinely absent key must be None — that IS the point of the strict
    readers (R-F1392): absent and broken must never be confused."""
    assert await store.get_strict("_rf3679_definitely_not_set") is None


@pytest.mark.asyncio
async def test_rf3679_strict_still_raises_when_there_is_no_connection(monkeypatch):
    """REGRESSION GUARD: it must not degrade into the graceful None-on-error
    contract — callers escalate on the raise."""
    monkeypatch.setattr(ss, "_reader_conn_for", lambda key: None)
    with pytest.raises(ss.StateReadError):
        await ss.get_strict("anything")


@pytest.mark.asyncio
async def test_rf3679_a_timeout_still_raises(monkeypatch):
    """A slow store must still be distinguishable from an absent key."""
    class _SlowConn:
        async def execute(self, *a, **k):
            await asyncio.sleep(10)

    monkeypatch.setattr(ss, "_reader_conn_for", lambda key: _SlowConn())
    with pytest.raises(ss.StateReadError, match="timed out"):
        await asyncio.wait_for(ss.get_strict("slow"), timeout=8)


@pytest.mark.asyncio
async def test_rf3679_retries_once_on_a_closed_connection(monkeypatch):
    """A read connection swapped underneath us is recoverable, not "store broke".

    Mirrors _row's retry-once. Without it a routine _ensure_read_conn swap would
    escalate to callers that treat StateReadError as a real outage.
    """
    calls = {"n": 0}

    class _Cur:
        async def fetchone(self):
            return ("recovered", "string", None)

        async def close(self):
            pass

    class _Conn:
        async def execute(self, *a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("Cannot operate on a closed database.")
            return _Cur()

    conn = _Conn()
    monkeypatch.setattr(ss, "_reader_conn_for", lambda key: conn)

    async def _noop():
        return None

    monkeypatch.setattr(ss, "_ensure_read_conn", _noop)

    assert await ss.get_strict("k") == "recovered"
    assert calls["n"] == 2, "must retry exactly once, not loop"


@pytest.mark.asyncio
async def test_rf3679_redis_store_wrapper_still_maps_the_error():
    """rs.get_strict must keep converting StateReadError -> StoreReadError, or
    every caller's `except StoreReadError` stops catching."""
    from aria_service.intel import redis_store as rs

    assert issubclass(rs.StoreReadError, Exception)
    import inspect
    src = function_source(rs, "get_strict")
    assert "StateReadError" in src and "StoreReadError" in src

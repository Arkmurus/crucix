"""R-F1388 — state_store._upsert must propagate write failures so callers
(e.g. _chat_job_set) know the write failed and return 503 instead of 202.

Live symptom (2026-06-06): every NDA analysis on WhatsApp returned
"chat job expired" because state_store._upsert silently swallowed SQLite
write failures. _chat_job_set got no exception, returned True, the WA
listener got a 202 with a job_id, polled forever, and got not_found.

Fix: _upsert now re-raises the exception after logging + triggering self-heal.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from aria_service.intel import state_store as ss


@pytest.fixture(autouse=True)
def _fresh():
    ss._reset_lock()
    yield
    ss._reset_lock()


@pytest.mark.asyncio
async def test_upsert_raises_on_dead_connection(tmp_path):
    """Capability: _upsert must raise when the SQLite connection is closed,
    so _chat_job_set can catch it and return False."""
    await ss.connect(str(tmp_path / "s.db"))
    await ss.set_key("k", "v")
    assert await ss.get("k") == "v"

    # Kill the connection — the exact live failure mode.
    await ss._conn.close()

    # _upsert must now raise (it used to silently log+return)
    with pytest.raises(Exception) as excinfo:
        await ss.set_key("k2", "v2")
    # The exception should mention the closed connection
    err = str(excinfo.value).lower()
    assert any(s in err for s in ("closed", "cannot operate", "no active connection")), (
        f"Expected a connection-closed error, got: {excinfo.value}"
    )

    await ss.close()


@pytest.mark.asyncio
async def test_upsert_triggers_self_heal_on_dead_connection(tmp_path):
    """_upsert must trigger self-heal when the connection is dead,
    so subsequent writes can recover."""
    await ss.connect(str(tmp_path / "s.db"))
    await ss.set_key("k", "v")

    # Kill the connection
    await ss._conn.close()

    # _upsert should trigger reconnect
    try:
        await ss.set_key("k2", "v2")
    except Exception:
        pass

    # Wait for reconnect to complete
    for _ in range(50):
        await asyncio.sleep(0.05)
        if not ss._reconnect_in_progress and ss._conn is not None:
            break

    # After self-heal, writes should work again
    await ss.set_key("k3", "v3")
    assert await ss.get("k3") == "v3"
    await ss.close()


@pytest.mark.asyncio
async def test_upsert_works_normally(tmp_path):
    """Normal operation must not be affected."""
    await ss.connect(str(tmp_path / "s.db"))
    await ss.set_key("k", "v")
    assert await ss.get("k") == "v"
    await ss.close()

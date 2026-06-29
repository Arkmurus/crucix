"""R-F2132 — state_store must set busy_timeout BEFORE journal_mode=WAL on
every connection it opens.

2026-06-29 outage root cause: connect()/_reconnect() ran
`PRAGMA journal_mode=WAL` BEFORE `PRAGMA busy_timeout=120000`. The journal_mode
PRAGMA can trigger a WAL recovery that needs a database lock; while the
busy_timeout is still at Python sqlite3's ~5s default, recovery of a multi-GB
WAL (contested-deploy bloat) raises `database is locked` before boot ever
reaches the R-F2116 checkpoint -> the app falls to the in-memory fallback and
the R-F1341 reconnect loop spins forever (aria-intel down ~50 min). The fix
sets the 120s busy_timeout FIRST so recovery waits the lock out.

The deadlock only manifests under live lock contention (not reproducible fast
or deterministically in-process), so this drives the REAL connect() path and
asserts the broken contract directly: the order of PRAGMA execution on every
connection, plus that the 120s timeout is actually applied.
"""
import asyncio

import aiosqlite

import aria_service.intel.state_store as SS


def _record_pragma_order(monkeypatch):
    """Patch aiosqlite.Connection.execute to record (conn_id, sql) for every
    statement run on every connection, so we can assert PRAGMA ordering."""
    order = []
    real_execute = aiosqlite.Connection.execute

    async def _rec(self, sql, *args, **kwargs):
        order.append((id(self), str(sql)))
        return await real_execute(self, sql, *args, **kwargs)

    monkeypatch.setattr(aiosqlite.Connection, "execute", _rec)
    return order


def test_rf2132_busy_timeout_precedes_journal_mode_on_all_connections(tmp_path, monkeypatch):
    db = tmp_path / "aria_state.db"
    monkeypatch.setenv("ARIA_STATE_DB_PATH", str(db))
    order = _record_pragma_order(monkeypatch)

    async def _boot():
        ok = await SS.connect(str(db))
        await SS.close()
        return ok

    ok = asyncio.run(_boot())
    assert ok is True, "connect() must succeed"

    # Group executed statements per connection, in execution order.
    per_conn = {}
    for cid, sql in order:
        per_conn.setdefault(cid, []).append(sql.lower())

    checked = 0
    for cid, sqls in per_conn.items():
        bt = next((i for i, s in enumerate(sqls) if "busy_timeout" in s), None)
        jm = next((i for i, s in enumerate(sqls) if "journal_mode" in s), None)
        if bt is not None and jm is not None:
            assert bt < jm, (
                f"connection {cid}: busy_timeout (idx {bt}) MUST precede "
                f"journal_mode (idx {jm}) — that ordering is the R-F2132 fix; "
                f"order={sqls}"
            )
            checked += 1
    # connect() opens _conn AND _read_conn, both set both PRAGMAs.
    assert checked >= 2, (
        f"expected >=2 connections setting both busy_timeout and journal_mode, "
        f"only verified {checked}"
    )


def test_rf2132_busy_timeout_is_120s_after_connect(tmp_path, monkeypatch):
    """Behavioral outcome: after connect(), both the write and read connections
    actually have the 120s busy_timeout applied (not the ~5s default)."""
    db = tmp_path / "aria_state.db"
    monkeypatch.setenv("ARIA_STATE_DB_PATH", str(db))

    async def _boot():
        await SS.connect(str(db))
        cur = await SS._conn.execute("PRAGMA busy_timeout")
        write_bt = (await cur.fetchone())[0]
        await cur.close()
        read_bt = None
        if SS._read_conn is not None:
            cur = await SS._read_conn.execute("PRAGMA busy_timeout")
            read_bt = (await cur.fetchone())[0]
            await cur.close()
        await SS.close()
        return write_bt, read_bt

    write_bt, read_bt = asyncio.run(_boot())
    assert write_bt == 120000, f"_conn busy_timeout must be 120000, got {write_bt}"
    assert read_bt == 120000, f"_read_conn busy_timeout must be 120000, got {read_bt}"

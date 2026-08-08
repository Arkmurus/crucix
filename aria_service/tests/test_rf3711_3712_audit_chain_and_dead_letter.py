"""R-F3711/R-F3712 — CAPABILITY: the audit chain cannot silently fork, and
dead-lettered brain signals have a way back.

R-F3711 — `_read_head_hash` collapsed "the log is empty" and "the store failed"
into the same answer, `_GENESIS_HASH`. Captured live in the fly logs on
2026-08-04:

    state_store.get(crucix:audit:head_hash) timed out after 5s —
    DB may be bloated or under WAL recovery. Returning None.

`redis_store.get` returns None on a store failure (the documented
None-on-error contract), so `h if h else _GENESIS_HASH` answered "empty log",
and `record()` chained the next entry to genesis — SILENTLY FORKING the
evidentiary chain and orphaning every prior entry. The mechanism that exists to
prove nothing was tampered with was discarding its own history because a read
timed out.

R-F3712 — the brain-ingest dead-letter table had no reader, no replay, no prune
and no alert. Rows were INSERTed on poison payloads and on terminal failure; the
only reader in the tree was `SELECT COUNT(*)`. `recover_stuck()` sounds like the
drain but only touches the `queue` table, which dead-lettered rows have already
left. 114 signals were parked permanently (measured live) — a §7 violation,
against a module whose docstring promises items are "replayed on next drain".

Run: python -m pytest aria_service/tests/test_rf3711_3712_audit_chain_and_dead_letter.py -v
"""
from __future__ import annotations

import asyncio
import json

import pytest

# R-F3772/§16 — NOT inspect.getsource: it slices at line numbers captured AT
# IMPORT, so a mid-run edit silently returns a DIFFERENT function's body. A CLASS
# target scopes the lookup to that class's own body (R-F3771).
from ._source_probe import function_source


# ══════════════════════════════════════════════════════════════════════════
# R-F3711 — the audit chain
# ══════════════════════════════════════════════════════════════════════════

def test_a_store_failure_is_not_genesis(monkeypatch):
    from aria_service.intel import audit_log, redis_store

    async def _boom(key):
        raise redis_store.StoreReadError("sqlite timeout after 5s")

    monkeypatch.setattr(redis_store, "get_strict", _boom)
    with pytest.raises(audit_log.AuditChainUnreadable):
        asyncio.run(audit_log._read_head_hash(strict=True))


def test_an_empty_log_still_chains_to_genesis(monkeypatch):
    """The FIRST entry legitimately has no predecessor."""
    from aria_service.intel import audit_log, redis_store

    async def _absent(key):
        return None

    monkeypatch.setattr(redis_store, "get_strict", _absent)
    got = asyncio.run(audit_log._read_head_hash(strict=True))
    assert got == audit_log._GENESIS_HASH, (
        "an ABSENT key is the genuine empty-log case and must be allowed — only "
        "a store FAILURE is refused"
    )


def test_a_real_head_is_returned(monkeypatch):
    from aria_service.intel import audit_log, redis_store

    async def _head(key):
        return "abc123"

    monkeypatch.setattr(redis_store, "get_strict", _head)
    assert asyncio.run(audit_log._read_head_hash(strict=True)) == "abc123"


def test_record_refuses_rather_than_forking_the_chain(monkeypatch):
    """Losing ONE audit entry beats invalidating the whole trail."""
    from aria_service.intel import audit_log

    async def _unreadable(*a, **k):
        raise audit_log.AuditChainUnreadable("store down")

    monkeypatch.setattr(audit_log, "_read_head_hash", _unreadable)
    action = next(iter(audit_log.RECORDED_ACTIONS))
    out = asyncio.run(audit_log.record(action))
    assert out.get("recorded") is False
    assert out.get("reason") == "audit_chain_unreadable", (
        "chaining to genesis on a transient read would orphan every prior entry"
    )


def test_the_verifier_keeps_the_lenient_read():
    """The read-only verifier reports; it does not extend the chain."""
    import inspect
    from aria_service.intel import audit_log

    src = function_source(audit_log, "_read_head_hash")
    assert "strict: bool = False" in src, (
        "strict must be OPT-IN so the verifier's genesis fallback — which is "
        "visible in its output — is unchanged"
    )


def test_a_refused_audit_write_is_wired_to_the_brain():
    """§21a — a refused audit write is exactly what must not be silent."""
    import inspect
    from aria_service.intel import audit_log

    src = function_source(audit_log, "record")
    assert "wire_failure" in src and "data_integrity" in src


# ══════════════════════════════════════════════════════════════════════════
# R-F3712 — the dead-letter exit
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def queue(tmp_path, monkeypatch):
    monkeypatch.setenv("ARIA_BRAIN_QUEUE_DB", str(tmp_path / "q.db"))
    from aria_service.intel import brain_ingest_queue as biq
    import importlib
    importlib.reload(biq)
    ok = asyncio.run(biq.connect())
    if not ok:
        pytest.skip("aiosqlite unavailable in this environment")
    return biq


def _seed_dead_letter(biq, payloads):
    async def _go():
        for p in payloads:
            await biq._conn.execute(
                "INSERT INTO dead_letter (payload, error, attempts, enqueued_at, "
                "failed_at) VALUES (?, ?, ?, ?, ?)",
                (p, "max attempts", 5, biq._now(), biq._now()),
            )
        await biq._conn.commit()
    asyncio.run(_go())


def test_dead_letter_rows_can_be_READ(queue):
    """Nothing could read the payload column before this."""
    _seed_dead_letter(queue, [json.dumps({"module": "x", "summary": "s"})])
    rows = asyncio.run(queue.list_dead_letter())
    assert len(rows) == 1
    assert rows[0]["payload"], "the payload column was never selected by anything"
    assert rows[0]["error"] == "max attempts"


def test_dead_letter_rows_can_be_REPLAYED(queue):
    _seed_dead_letter(queue, [
        json.dumps({"module": "a", "summary": "1"}),
        json.dumps({"module": "b", "summary": "2"}),
    ])
    out = asyncio.run(queue.replay_dead_letter())
    assert out["ok"] is True
    assert out["replayed"] == 2, (
        "114 signals were parked with no exit — recover_stuck() only touches "
        "the queue table, which these rows have already left"
    )
    stats = asyncio.run(queue.stats())
    assert stats["dead_letter"] == 0
    assert stats["depth"] == 2, "replayed rows must land back on the queue"


def test_a_replayed_row_is_dequeueable(queue):
    """§7 — the knowledge must actually be recoverable, not just moved."""
    _seed_dead_letter(queue, [json.dumps({"module": "a", "summary": "recovered"})])
    asyncio.run(queue.replay_dead_letter())
    batch = asyncio.run(queue.dequeue_batch(limit=10))
    assert len(batch) == 1
    assert "recovered" in json.dumps(batch[0])


def test_poison_payloads_are_not_replayed(queue):
    """They would fail identically and churn — report, do not loop."""
    _seed_dead_letter(queue, ["{not valid json", json.dumps({"module": "ok"})])
    out = asyncio.run(queue.replay_dead_letter())
    assert out["replayed"] == 1
    assert out["poison_skipped"] == 1
    remaining = asyncio.run(queue.list_dead_letter())
    assert len(remaining) == 1, "the poison row must be RETAINED for inspection"


def test_replay_is_bounded(queue):
    _seed_dead_letter(queue, [json.dumps({"i": i}) for i in range(10)])
    out = asyncio.run(queue.replay_dead_letter(limit=4))
    assert out["replayed"] == 4
    assert len(asyncio.run(queue.list_dead_letter())) == 6


def test_replay_can_target_specific_ids(queue):
    _seed_dead_letter(queue, [json.dumps({"i": i}) for i in range(3)])
    rows = asyncio.run(queue.list_dead_letter())
    target = [rows[0]["id"]]
    out = asyncio.run(queue.replay_dead_letter(ids=target))
    assert out["replayed"] == 1
    assert len(asyncio.run(queue.list_dead_letter())) == 2


def test_nothing_is_lost_when_there_is_nothing_to_replay(queue):
    out = asyncio.run(queue.replay_dead_letter())
    assert out["ok"] is True and out["replayed"] == 0

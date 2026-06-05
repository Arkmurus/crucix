"""R-F1342 — infinite memory (§7): a fact that can't persist immediately is
NEVER forgotten — it goes to a durable write-ahead log (memory_wal) and is
retried until it lands in the knowledge base.

Operator directive (2026-06-05): "she never forgets anything — infinite memory."
This covers the two loss paths that previously dropped facts silently:
  1. concurrency-cap skip in absorb_tiers_bg (R-F799 fast-skip)
  2. knowledge.store_fact timeout/failure
Both now route the fact to the WAL. The R-F799 fast-skip behaviour itself is
unchanged (verified by the untouched test_rf799 suite).
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from aria_service.intel import memory_wal
from aria_service.intel import brain_hook_bg as bg


@pytest.fixture(autouse=True)
def _isolated_wal(monkeypatch, tmp_path):
    monkeypatch.setattr(memory_wal, "_WAL_PATH", tmp_path / "wal.jsonl")
    memory_wal._stats.update(
        {"appended": 0, "drained": 0, "retry_failed": 0, "trimmed": 0})
    yield


# ── WAL primitives ───────────────────────────────────────────────────────


def test_record_and_count():
    assert memory_wal.pending_count() == 0
    assert memory_wal.record_pending_fact("t", "a verifiable fact", "src", "ASSESSED")
    assert memory_wal.record_pending_fact("t2", "another fact", "src", "PROBABLE")
    assert memory_wal.pending_count() == 2


def test_record_empty_content_is_noop():
    assert memory_wal.record_pending_fact("t", "", "src", "ASSESSED") is False
    assert memory_wal.pending_count() == 0


@pytest.mark.asyncio
async def test_drain_persists_and_clears_on_success():
    memory_wal.record_pending_fact("t", "fact one", "src", "ASSESSED")
    memory_wal.record_pending_fact("t", "fact two", "src", "ASSESSED")
    stored = []

    async def _store_fact(**k):
        stored.append(k["content"])

    res = await memory_wal.drain(_store_fact)
    assert res["remaining"] == 0          # all persisted
    assert memory_wal.pending_count() == 0  # WAL cleared
    assert set(stored) == {"fact one", "fact two"}


@pytest.mark.asyncio
async def test_drain_keeps_facts_that_still_fail():
    memory_wal.record_pending_fact("t", "stubborn fact", "src", "ASSESSED")

    async def _failing_store(**k):
        raise RuntimeError("db down")

    res = await memory_wal.drain(_failing_store)
    assert res["remaining"] == 1                 # kept for retry
    assert memory_wal.pending_count() == 1       # NOT lost
    # When the DB recovers, the next drain persists it.
    ok = []
    async def _store_ok(**k):
        ok.append(k["content"])
    await memory_wal.drain(_store_ok)
    assert ok == ["stubborn fact"]
    assert memory_wal.pending_count() == 0


# ── absorb_tiers_bg routes skipped/failed facts to the WAL ───────────────


def _kwargs(result):
    return dict(
        module="unit", summary="A FACT TO REMEMBER", text_for_neural="",
        source="test", topics=["t"], success=True, weight=1.0,
        confidence="ASSESSED", entity_name="E", gap_type=None, gap_detail=None,
        sector="defence", user_id="", result=result,
        _ABSORB_CONCURRENCY=1, _ABSORB_SEM_ACQUIRE_TIMEOUT_S=0.05,
        _record_signal=lambda *a, **k: asyncio.sleep(0),
        _record_latency=lambda *a, **k: None,
        _maybe_trip_breaker=lambda *a, **k: None,
        _start_ms=0.0,
    )


@pytest.mark.asyncio
async def test_concurrency_skip_routes_fact_to_wal(monkeypatch):
    """When the concurrency sem can't be acquired (R-F799 fast-skip), the
    fact must land in the WAL — not be forgotten."""
    async def _passthru_tier(coro, label):
        await coro
        return True, None

    exhausted = asyncio.Semaphore(1)
    await exhausted.acquire()  # force acquire-timeout in the bg task
    try:
        result = {"errors": []}
        await bg.absorb_tiers_bg(
            _get_absorb_concurrency_sem=lambda: exhausted,
            _run_tier=_passthru_tier, **_kwargs(result),
        )
    finally:
        exhausted.release()

    assert result.get("fact_walled") is True
    assert memory_wal.pending_count() == 1   # the fact is safe in the WAL


@pytest.mark.asyncio
async def test_knowledge_failure_routes_fact_to_wal(monkeypatch):
    """If knowledge.store_fact itself fails, the fact goes to the WAL."""
    async def _tier_knowledge_fails(coro, label):
        # mastery ok; knowledge fails; neural ok
        try:
            await coro
        except Exception:
            pass
        return (label != "knowledge"), (None if label != "knowledge" else "knowledge: timeout")

    import aria_service.intel.knowledge as _k
    async def _boom(**k):
        raise asyncio.TimeoutError()
    monkeypatch.setattr(_k, "store_fact", _boom)

    sem = asyncio.Semaphore(4)
    result = {"errors": []}
    await bg.absorb_tiers_bg(
        _get_absorb_concurrency_sem=lambda: sem,
        _run_tier=_tier_knowledge_fails, **_kwargs(result),
    )
    assert result.get("fact_walled") is True
    assert memory_wal.pending_count() == 1   # never forgotten


@pytest.mark.asyncio
async def test_successful_persist_does_not_wal(monkeypatch):
    async def _passthru_tier(coro, label):
        await coro
        return True, None
    import aria_service.intel.knowledge as _k
    async def _ok(**k):
        return None
    monkeypatch.setattr(_k, "store_fact", _ok)

    sem = asyncio.Semaphore(4)
    result = {"errors": []}
    await bg.absorb_tiers_bg(
        _get_absorb_concurrency_sem=lambda: sem,
        _run_tier=_passthru_tier, **_kwargs(result),
    )
    assert result.get("fact_walled") is not True
    assert memory_wal.pending_count() == 0   # nothing queued — it persisted

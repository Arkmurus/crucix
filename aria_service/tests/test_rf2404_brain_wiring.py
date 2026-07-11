"""R-F2404 — dark brain-INPUT paths now surface a capability gap (§21).

Three input paths silently dropped failure signals:
1. brain_hook.absorb over-cap SHED branch dropped the caller's gap_type (only the
   normal branch's absorb_tiers_bg consumed it) → the gap_detector→self_coder loop
   went blind exactly under autonomous backlog burst.
2. rag_store.ingest_fact swallowed chromadb upsert failures (internal try/except +
   return False → the @fail_wire never fired) → RAG index degraded silently.
3. _semantic_index_queue.enqueue logged queue-full to the console only (§21a DARK).

Each fix records a capability gap so the self-heal loop can act. These tests drive
the actual broken paths and assert the gap is recorded.
"""
from __future__ import annotations

import asyncio
import pytest


class _GapSpy:
    """Async stand-in for capability_gaps.record_gap that captures calls."""
    def __init__(self):
        self.calls = []
    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return {"ok": True}


# ── Fix 1: brain_hook over-cap shed branch records the dropped gap ───────────

def test_absorb_shed_branch_records_gap(monkeypatch):
    from aria_service.intel import brain_hook, capability_gaps
    spy = _GapSpy()
    monkeypatch.setattr(capability_gaps, "record_gap", spy)
    # force the over-cap shed path: non-interactive (user_id="") + backlog at cap
    monkeypatch.setattr(brain_hook, "_pending_absorb", brain_hook._MAX_PENDING_ABSORB)
    brain_hook._breaker_state["open"] = False
    # avoid touching the real WAL on disk
    import aria_service.intel.memory_wal as mw
    monkeypatch.setattr(mw, "record_pending_fact", lambda **k: None)

    out = asyncio.run(brain_hook.absorb(
        module="test_module", summary="a background module failed at something",
        success=False, gap_type="api_missing", gap_detail="ACME api key missing",
        user_id="",  # non-interactive → eligible for shed
    ))

    assert spy.calls, "shed branch must record the gap it would otherwise drop"
    # R-F2537: assert the caller's gap is PRESENT — not that it is calls[0]. The shed
    # branch DOES record it (brain_hook.py:896); the old calls[0] assertion was brittle
    # because unrelated module-init wiring gaps (the R-F2119 import-time
    # wire_failure("module shutdown") blocks, still being swept) can interleave into the
    # same spied record_gap during the test's event loop.
    _match = [c for c in spy.calls if c.get("gap_type") == "api_missing"]
    assert _match, (
        "shed branch must record the caller's api_missing gap; "
        f"got {[c.get('gap_type') for c in spy.calls]}"
    )
    assert "ACME api key missing" in _match[0]["detail"]


def test_absorb_shed_branch_no_gap_when_none(monkeypatch):
    """No gap_type → no gap recorded (we only rescue reported failures)."""
    from aria_service.intel import brain_hook, capability_gaps
    spy = _GapSpy()
    monkeypatch.setattr(capability_gaps, "record_gap", spy)
    monkeypatch.setattr(brain_hook, "_pending_absorb", brain_hook._MAX_PENDING_ABSORB)
    brain_hook._breaker_state["open"] = False
    import aria_service.intel.memory_wal as mw
    monkeypatch.setattr(mw, "record_pending_fact", lambda **k: None)

    asyncio.run(brain_hook.absorb(
        module="test_module", summary="a plain success fact", user_id="",
    ))
    assert not spy.calls, "no gap_type → must not fabricate a gap"


# ── Fix 2: rag_store.ingest_fact records a gap when the upsert fails ─────────

def test_ingest_fact_records_gap_on_upsert_failure(monkeypatch):
    from aria_service.intel import rag_store, capability_gaps
    spy = _GapSpy()
    monkeypatch.setattr(capability_gaps, "record_gap", spy)

    async def _ok():
        return True
    monkeypatch.setattr(rag_store, "_ensure_async", _ok)

    class _BoomCollection:
        def upsert(self, *a, **k):
            raise RuntimeError("chromadb down")
    monkeypatch.setattr(rag_store, "_facts_collection", _BoomCollection())

    ok = asyncio.run(rag_store.ingest_fact(
        "fid1", "topic-x", "a distilled fact long enough to pass the min-length guard"))

    assert ok is False, "a failed upsert must return False"
    assert spy.calls, "a swallowed upsert failure must now surface a gap"
    assert spy.calls[0]["gap_type"] == "embedder_failure"


# ── Fix 3: _semantic_index_queue.enqueue records a gap on drop-burst ─────────

def test_semantic_queue_full_records_gap(monkeypatch):
    import aria_service.intel._semantic_index_queue as siq
    from aria_service.intel import capability_gaps
    spy = _GapSpy()
    monkeypatch.setattr(capability_gaps, "record_gap", spy)
    monkeypatch.setattr(siq, "_disabled", lambda: False)
    monkeypatch.setattr(siq, "_drops_total", 0)

    async def run():
        full_q = asyncio.Queue(maxsize=1)
        full_q.put_nowait(("seed", "seed", {}))     # now full
        monkeypatch.setattr(siq, "_ensure_queue", lambda: full_q)
        return await siq.enqueue("f1", "some text", {})

    dropped = asyncio.run(run())
    assert dropped is False, "a full queue must report the drop"
    assert spy.calls, "queue-full drop must record a gap (was console-only)"
    assert spy.calls[0]["gap_type"] == "embedder_failure"

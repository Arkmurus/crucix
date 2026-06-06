"""R-F1363 — operator /coder/request background task must actually EXECUTE.

Bug: _queue_coder_request fired the fix via asyncio.create_task() WITHOUT
storing a strong reference. asyncio keeps only a weak ref to the task, so under
a saturated event loop (the ~96 periodic intel tasks) it was garbage-collected
before running — operator requests queued (returned a fix_id) but fix_gap NEVER
ran (0 execution log lines, status stuck at "queued"). The autonomous loop was
unaffected because it AWAITS fix_gap inside a long-lived coroutine.

Capability test: drive the real _queue_coder_request with a fake coder and
assert operator_fix_request is actually invoked with the right args (i.e. the
background task ran to completion), and that the task is tracked in
_CODER_BG_TASKS while in flight and discarded when done.
"""
import asyncio

import pytest

from aria_service.routes import aria as aria_routes


class _FakeResult:
    status = "staged"


class _FakeCoder:
    """Records that operator_fix_request actually ran."""

    redis = None  # exercise the no-redis branch (live aria-intel state)

    def __init__(self):
        self.called_with = None
        self.ran = asyncio.Event()

    async def operator_fix_request(self, description, *, module_hint=None,
                                   force_stage=False):
        # small await so the task must survive at least one loop turn
        await asyncio.sleep(0)
        self.called_with = {
            "description": description,
            "module_hint": module_hint,
            "force_stage": force_stage,
        }
        self.ran.set()
        return _FakeResult()


@pytest.mark.asyncio
async def test_operator_request_background_task_executes():
    coder = _FakeCoder()
    resp = await aria_routes._queue_coder_request(
        coder,
        "Add normalize_entity_name to aria_service/intel for DD matching",
        module_hint="aria_service/intel/",
        source="operator",
        force_stage=True,
    )
    # queued response returned immediately with both ids
    assert resp["queued"] is True
    assert resp["fix_id"] and resp["gap_id"]

    # the task must be strongly referenced while in flight (the fix)
    assert len(aria_routes._CODER_BG_TASKS) >= 1

    # and it must ACTUALLY run operator_fix_request (the broken path)
    await asyncio.wait_for(coder.ran.wait(), timeout=5)
    assert coder.called_with is not None
    assert coder.called_with["force_stage"] is True
    assert coder.called_with["module_hint"] == "aria_service/intel/"
    assert "normalize_entity_name" in coder.called_with["description"]

    # let the done-callback fire; the set self-cleans
    await asyncio.sleep(0)
    assert all(not t.done() or t not in aria_routes._CODER_BG_TASKS
               for t in list(aria_routes._CODER_BG_TASKS))


@pytest.mark.asyncio
async def test_background_task_survives_gc_pressure():
    """Even if we drop our own handle and force GC, the task still runs —
    because _CODER_BG_TASKS holds the strong ref (the actual fix)."""
    import gc

    coder = _FakeCoder()
    await aria_routes._queue_coder_request(
        coder, "x" * 30, source="operator",
    )
    # drop locals + force a collection cycle to expose the weak-ref bug
    gc.collect()
    await asyncio.wait_for(coder.ran.wait(), timeout=5)
    assert coder.called_with is not None

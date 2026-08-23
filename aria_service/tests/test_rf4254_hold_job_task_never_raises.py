"""R-F4254 — the task-pinning helper raised, and took its caller down with it.

R-F4237 (C-217) wrapped all seventeen fire-and-forget spawns in
`routes/aria.py` with `_hold_job_task(...)`, because asyncio keeps only a WEAK
reference and an unpinned task can be collected before it runs. Correct fix,
with a consequence I did not think through: `_hold_job_task` had become part of
seventeen call paths, so **an exception inside it now propagates into whatever
was being spawned.**

It did. `test_rf2406_dd_rerun_lineage` stubs `asyncio.create_task` to return a
plain `object()`, and the DD rerun route died with::

    AttributeError: 'object' object has no attribute 'add_done_callback'
    routes/aria.py:14162

Before R-F4237 the bare `create_task(...)` result was discarded, so the stub was
harmless. Production never takes that path — `create_task` always returns a Task
— but the principle is the one this module already applies to every other
observability helper, twice today alone (`_report` in guardian/panic,
`_wire_truncated_judgment` in honesty_judge):

    **A mechanism that exists to protect the work must never break the work.**

A caller that cannot be pinned still runs. It merely loses the
garbage-collection guarantee — which is exactly where it stood before R-F4237,
so the failure mode degrades to the old behaviour instead of to an exception.

The guard covers the `set.add` as well as the callback: adding a non-Task would
leak, because nothing would ever discard it.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.routes import aria as ar


class TestItNeverBreaksItsCaller:

    def test_a_non_task_does_not_raise(self):
        """The exact shape that killed the DD rerun route."""
        sentinel = object()
        assert ar._hold_job_task(sentinel) is sentinel, (
            "the helper must return what it was given so it can be used inline")

    def test_a_non_task_is_not_added_to_the_holder(self):
        """Adding one would LEAK — nothing would ever discard it."""
        before = len(ar._ASYNC_JOB_TASKS)
        ar._hold_job_task(object())
        assert len(ar._ASYNC_JOB_TASKS) == before, (
            "a non-Task must not enter _ASYNC_JOB_TASKS; the done-callback is "
            "what removes entries, and a non-Task has none")

    def test_a_callback_that_explodes_is_swallowed(self):
        class _Hostile:
            def add_done_callback(self, _cb):
                raise RuntimeError("nope")

        ar._hold_job_task(_Hostile())   # must not raise


class TestThePinningStillWorks:
    """R-F4237's contract is preserved, not traded away for robustness."""

    def test_a_real_task_is_pinned_and_self_cleans(self):
        ran = []

        async def _drive():
            async def _work():
                await asyncio.sleep(0)
                ran.append(1)

            t = asyncio.create_task(_work())
            returned = ar._hold_job_task(t)
            assert returned is t
            assert t in ar._ASYNC_JOB_TASKS, (
                "a REAL task must still be held while pending — that is the "
                "strong reference R-F4237 exists for")
            del t, returned
            for _ in range(10):
                await asyncio.sleep(0)

        before = len(ar._ASYNC_JOB_TASKS)
        asyncio.run(_drive())
        assert ran == [1]
        assert len(ar._ASYNC_JOB_TASKS) == before, "the holder must self-clean"

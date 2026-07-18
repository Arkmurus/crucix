"""R-F2761 — brain absorption tasks have an explicit lifecycle owner."""

from __future__ import annotations

import asyncio
import inspect

from aria_service.intel import brain_hook
from aria_service import main


def test_shutdown_drives_real_absorb_task_cleanup(monkeypatch) -> None:
    """The real shutdown function cancels, awaits, and removes owned tasks."""
    monkeypatch.setattr(brain_hook, "_pending_absorb", 1)
    monkeypatch.setattr(brain_hook, "_absorb_background_tasks", set())

    async def exercise() -> tuple[int, bool]:
        started = asyncio.Event()

        async def pending_absorb() -> None:
            started.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(pending_absorb(), name="rf2761-probe")
        brain_hook._absorb_background_tasks.add(task)
        task.add_done_callback(brain_hook._dec_pending_absorb)
        await started.wait()

        count = await brain_hook.shutdown_background_tasks()
        await asyncio.sleep(0)
        return count, task.cancelled()

    count, cancelled = asyncio.run(exercise())

    assert count == 1
    assert cancelled is True
    assert brain_hook._pending_absorb == 0
    assert not brain_hook._absorb_background_tasks


def test_lifespan_shutdown_calls_real_brain_task_owner() -> None:
    """Production shutdown must invoke the verified lifecycle function."""
    source = inspect.getsource(main.lifespan)
    assert "shutdown_background_tasks" in source
    assert source.index("shutdown_background_tasks") < source.index(
        'await _shutdown_await("knowledge", knowledge.shutdown())'
    )

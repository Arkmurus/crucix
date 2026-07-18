"""R-F2758 — brain absorption semaphores belong to their event loop."""

from __future__ import annotations

import asyncio

from aria_service.intel import brain_hook


def test_absorb_semaphores_rotate_between_event_loops(monkeypatch) -> None:
    """The real getters must never return a semaphore owned by a closed loop."""
    monkeypatch.setattr(brain_hook, "_ABSORB_CONCURRENCY", 1)
    monkeypatch.setattr(brain_hook, "_NEURAL_CONCURRENCY", 1)
    monkeypatch.setattr(brain_hook, "_absorb_concurrency_sem", None)
    monkeypatch.setattr(brain_hook, "_neural_concurrency_sem", None)
    monkeypatch.setattr(brain_hook, "_absorb_concurrency_loop", None)
    monkeypatch.setattr(brain_hook, "_neural_concurrency_loop", None)

    async def acquire_real_semaphores() -> tuple[asyncio.Semaphore, asyncio.Semaphore]:
        absorb = brain_hook._get_absorb_concurrency_sem()
        neural = brain_hook._get_neural_concurrency_sem()
        assert absorb is not None
        assert neural is not None
        await absorb.acquire()
        await neural.acquire()
        absorb.release()
        neural.release()
        return absorb, neural

    first_absorb, first_neural = asyncio.run(acquire_real_semaphores())
    second_absorb, second_neural = asyncio.run(acquire_real_semaphores())

    assert second_absorb is not first_absorb
    assert second_neural is not first_neural


def test_absorb_completion_callback_retrieves_task_exception(caplog) -> None:
    """A failed real background task must be observed and logged once."""

    async def run_failure() -> None:
        async def fail() -> None:
            raise RuntimeError("rf2758 callback probe")

        task = asyncio.create_task(fail())
        await asyncio.sleep(0)
        brain_hook._dec_pending_absorb(task)
        assert task.exception() is not None

    asyncio.run(run_failure())
    assert "rf2758 callback probe" in caplog.text

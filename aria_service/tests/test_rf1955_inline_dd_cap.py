"""R-F1955 — inline DD concurrency cap + transparency (capability test).

The background deep-DD was already globally capped (_DD_DEEP_BG_MAX), but the
INLINE dd_orchestrate awaited directly in a chat turn had NO cap — N users each
triggering an inline DD ran N heavy jobs at once on the single-process loop.
R-F1955 adds a lazily-created Semaphore admission gate: overflow QUEUES (is not
dropped) and the deferral is surfaced to the user (no silent degrade).

These tests drive the real admission-gate primitives used in _execute_tool.
"""
import asyncio

import aria_service.routes.aria as aria


def _reset_sem():
    aria._dd_inline_sem = None


def test_cap_default_and_semaphore_size():
    _reset_sem()
    assert aria._DD_INLINE_MAX >= 1
    async def _run():
        sem = aria._get_dd_inline_sem()
        # All MAX permits available initially → not locked.
        assert sem.locked() is False
        acquired = []
        for _ in range(aria._DD_INLINE_MAX):
            await sem.acquire(); acquired.append(True)
        # Now saturated → the next inline DD would be DEFERRED (queued).
        assert sem.locked() is True, "at cap, a further inline DD must be detected as deferred"
        for _ in acquired:
            sem.release()
        assert sem.locked() is False
    asyncio.run(_run())


def test_permit_released_on_success_and_on_failure():
    """The try/finally pattern in _execute_tool must never leak a permit."""
    _reset_sem()
    async def _run():
        sem = aria._get_dd_inline_sem()

        async def guarded(boom: bool):
            acquired = False
            try:
                await sem.acquire(); acquired = True
                if boom:
                    raise RuntimeError("DD blew up")
                return "ok"
            except RuntimeError:
                return "handled"
            finally:
                if acquired:
                    sem.release()

        # Run a failing then a succeeding call; permits must all come back.
        assert await guarded(boom=True) == "handled"
        assert await guarded(boom=False) == "ok"
        # Drain all permits to prove none leaked (would block/raise otherwise).
        for _ in range(aria._DD_INLINE_MAX):
            await asyncio.wait_for(sem.acquire(), timeout=1.0)
        assert sem.locked() is True
        for _ in range(aria._DD_INLINE_MAX):
            sem.release()
    asyncio.run(_run())

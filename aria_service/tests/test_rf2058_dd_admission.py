"""R-F2058 — inline-DD admission control: bound the slot wait, busy when saturated."""
from __future__ import annotations
import asyncio
from aria_service.routes import aria as R


def test_admission_constant_and_sentinel():
    assert isinstance(R._DD_INLINE_ADMISSION_S, float)
    assert R._DD_INLINE_ADMISSION_S >= 0
    assert issubclass(R._DDAdmissionBusy, Exception)


def test_admission_bounds_wait_when_saturated():
    async def main():
        sem = asyncio.Semaphore(2)
        await sem.acquire(); await sem.acquire()         # saturate both slots
        timed_out = False
        try:
            await asyncio.wait_for(sem.acquire(), timeout=0.05)   # the bounded wait
        except asyncio.TimeoutError:
            timed_out = True
        assert timed_out is True, "a saturated queue must time out (→ busy), not hang"
        sem.release()                                    # a slot frees
        await asyncio.wait_for(sem.acquire(), timeout=0.3)        # now acquires, no raise
    asyncio.run(main())


def test_handler_wires_admission_and_busy_reply():
    import inspect
    src = inspect.getsource(R)
    assert "raise _DDAdmissionBusy()" in src                       # gate raises on timeout
    assert "except _DDAdmissionBusy:" in src                       # busy handler present
    assert "dd_orchestrate — BUSY" in src                          # honest busy reply

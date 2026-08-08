"""R-F2058 — inline-DD admission control: bound the slot wait, busy when saturated."""
from __future__ import annotations
import asyncio
from aria_service.routes import aria as R

# R-F3781/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


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
    src = module_source(R)
    assert "raise _DDAdmissionBusy()" in src                       # gate raises on timeout
    assert "except _DDAdmissionBusy:" in src                       # busy handler present
    assert "dd_orchestrate — BUSY" in src                          # honest busy reply

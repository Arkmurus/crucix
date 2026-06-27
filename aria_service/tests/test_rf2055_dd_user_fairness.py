"""R-F2055 — per-user inline-DD fairness: one user can't starve others.

A per-user lock acquired before the global Semaphore(_DD_INLINE_MAX) serializes
each user's OWN inline DDs, so a single user holds at most one global slot.
"""
from __future__ import annotations
import asyncio
from aria_service.routes import aria as R


def test_per_user_lock_identity():
    R._dd_inline_user_locks.clear()
    a1 = R._get_dd_inline_user_lock("U1")
    a2 = R._get_dd_inline_user_lock("U1")
    b1 = R._get_dd_inline_user_lock("U2")
    assert a1 is a2           # same user → same lock (serializes their own DDs)
    assert a1 is not b1       # different users → independent locks


def test_one_user_does_not_block_another():
    async def main():
        R._dd_inline_user_locks.clear()
        la = R._get_dd_inline_user_lock("A")
        await la.acquire()                      # user A's DD in flight
        assert la.locked() is True
        a2 = asyncio.create_task(la.acquire())  # A's 2nd DD must wait
        await asyncio.sleep(0.05)
        assert a2.done() is False, "A's second DD must serialize behind the first"
        # user B is INDEPENDENT — must NOT be blocked by A
        lb = R._get_dd_inline_user_lock("B")
        got_b = False
        try:
            await asyncio.wait_for(lb.acquire(), timeout=0.3); got_b = True
        finally:
            if got_b:
                lb.release()
        assert got_b is True, "user B must not be starved by user A"
        la.release()                            # free A → A's queued 2nd proceeds
        await asyncio.wait_for(a2, timeout=0.3)
        la.release()
    asyncio.run(main())

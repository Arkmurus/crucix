"""R-F1968 — silent-drop detector (active proprioception, capability test).

Proprioception was PASSIVE: a surface that died mid-request before reporting a
terminal outcome vanished with no trace (e.g. the WA listener crashing/redeploying
mid-answer). Now a request is durably registered on START; a terminal outcome
clears it; anything still pending past a deadline is recorded as a SILENT DROP
(a real failure → gap + dashboard).
"""
import asyncio

from aria_service.intel.outcome_wire import (
    record_request_start, reconcile_silent_drops, record_outcome,
    OutcomeRecord, get_surface_health,
)


def test_silent_drop_detected_after_deadline():
    async def run():
        surf = "rf1968a"
        await record_request_start(surf, "dropped-req", "chat_response")
        # max_age_s=0 → immediately past the deadline → the pending request is a drop.
        n = await reconcile_silent_drops(surf, max_age_s=0)
        assert n == 1, "a started-but-never-resolved request must be flagged"
        h = await get_surface_health(surf, 24)
        assert h["total"] >= 1
        assert any("silent_drop" in (f.get("detail") or "") for f in h["recent_failures"]), \
            "the silent drop must show as a recorded failure"
    asyncio.run(run())


def test_resolved_request_is_not_a_silent_drop():
    async def run():
        surf = "rf1968b"
        await record_request_start(surf, "ok-req", "chat_response")
        # A terminal outcome resolves it...
        await record_outcome(OutcomeRecord(surf, "ok-req", "chat_response", "delivered_real_answer", 5))
        # ...so the reconcile must NOT flag it, even past the deadline.
        n = await reconcile_silent_drops(surf, max_age_s=0)
        assert n == 0, "a request with a terminal outcome must never be a silent drop"
    asyncio.run(run())


def test_fresh_pending_request_not_dropped():
    async def run():
        surf = "rf1968c"
        await record_request_start(surf, "fresh-req", "chat_response")
        # Within the deadline → still legitimately in-flight, not a drop.
        n = await reconcile_silent_drops(surf, max_age_s=1800)
        assert n == 0
    asyncio.run(run())

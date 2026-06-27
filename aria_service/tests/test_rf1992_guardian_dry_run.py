"""R-F1992 — Guardian self-test (dry_run) must not reset Phase A gate #3.

Root cause (verified): an operator panic-button test fired a REAL panic whose
delivery failed, so panic.py and gateway._escalate_safety_failure both logged at
ERROR. The ErrorLedgerHandler mirrors aria.* ERROR logs into the self_improve
ledger, and error_streak.py counts log:error as a streak-resetting event — so a
self-test reset gate #3 (consecutive_clean_days → 0).

ARIA proposed (a) filtering a non-existent test id, or (b) demoting the ERROR to
WARNING. Both are band-aids: (b) would hide REAL panic-delivery failures, which
§25 makes a first-class self-heal trigger. The honest fix is to distinguish a
self-test from a real SOS: dry_run runs the full chain but keeps failures OUT of
the production error ledger; a real failure still ERRORs + escalates.

These tests assert the user-visible outcome directly on the ERROR LOG STREAM —
the exact signal error_streak consumes — not just a helper return value.
"""
import asyncio
import logging

from aria_service.guardian import gateway as gw
from aria_service.guardian import circle as circle
from aria_service.guardian import panic as panic


def _stub_send(ok=True):
    sent = []
    async def _send(req):
        sent.append(req)
        return ok
    return _send, sent


class _ErrorCapture(logging.Handler):
    """Capture ERROR+ records on the aria.guardian tree — this is precisely
    what the ErrorLedgerHandler forwards to the gate-#3 streak ledger."""
    def __init__(self):
        super().__init__(level=logging.ERROR)
        self.records = []

    def emit(self, record):
        self.records.append(record)


def _attach():
    h = _ErrorCapture()
    lg = logging.getLogger("aria.guardian")
    lg.addHandler(h)
    return lg, h


# ── the bug: a REAL failed panic logs ERROR (correctly resets gate #3) ───────
def test_real_panic_delivery_failure_logs_error():
    lg, h = _attach()
    try:
        async def run():
            send, sent = _stub_send(ok=False)   # delivery fails
            u = "rf1992_real"
            await circle.add_contact(u, "Mum", "447700900801", "mother")
            r = await panic.trigger(u, send)    # dry_run defaults to False
            assert r["ok"] is False and r["alerted"] == 0 and r["total"] == 1
            assert len(sent) == 1, "a real panic still attempts delivery"
        asyncio.run(run())
        assert h.records, "a REAL failed safety delivery MUST log ERROR (§25)"
    finally:
        lg.removeHandler(h)


# ── the fix: a self-test failed panic does NOT log ERROR (gate #3 safe) ──────
def test_dry_run_panic_delivery_failure_does_not_log_error():
    lg, h = _attach()
    try:
        async def run():
            send, sent = _stub_send(ok=False)   # delivery fails during the test
            u = "rf1992_test"
            await circle.add_contact(u, "Mum", "447700900802", "mother")
            r = await panic.trigger(u, send, dry_run=True)
            # Still attended + surfaced: the caller sees it failed.
            assert r["ok"] is False and r["dry_run"] is True
            assert len(sent) == 1, "dry_run still exercises the real chain"
        asyncio.run(run())
        assert not h.records, (
            "a self-test failure must NOT log ERROR — it would reset Phase A "
            "gate #3 (CLAUDE.md §1)")
    finally:
        lg.removeHandler(h)


# ── dry_run still honours the full chain: kill-switch blocks a self-test ─────
def test_dry_run_panic_blocked_by_kill_switch():
    async def run():
        send, sent = _stub_send(ok=True)
        u = "rf1992_pause"
        await circle.add_contact(u, "Mum", "447700900803", "mother")
        await gw.pause(u)
        r = await panic.trigger(u, send, dry_run=True)
        assert r["ok"] is False and not sent, "kill-switch blocks even a self-test"
        await gw.resume(u)
    asyncio.run(run())


# ── dry_run success path returns a clear test status, no ERROR ───────────────
def test_dry_run_panic_success_returns_test_status():
    lg, h = _attach()
    try:
        async def run():
            send, sent = _stub_send(ok=True)
            u = "rf1992_ok"
            await circle.add_contact(u, "Mum", "447700900804", "mother")
            r = await panic.trigger(u, send, dry_run=True)
            assert r["ok"] is True and r["dry_run"] is True and r["alerted"] == 1
            assert len(sent) == 1
        asyncio.run(run())
        assert not h.records
    finally:
        lg.removeHandler(h)

"""R-F3010 — deploy-coordination: ARIA's autonomous deploy defers an aria-intel
restart while a user DD is in-flight, so a deploy can't kill a running DD. Bounded
(a stuck DD never blocks deploys forever — R-F3009 resume is the backstop).

_await_dd_quiescence uses only module state (the R-F3008 gauge), no self attrs, so
it's exercised directly on the unbound method.
"""
import asyncio
import time
from pathlib import Path

from aria_service.autonomous.autonomous_deploy import AutonomousDeployEngine
from aria_service.intel import dd_orchestrator as ddo

_SRC = (Path(__file__).resolve().parent.parent / "autonomous" / "autonomous_deploy.py").read_text(encoding="utf-8")


def _reset_gauge():
    while ddo.dd_inflight_count() > 0:
        ddo._dd_inflight_dec()


def test_rf3010_returns_immediately_when_no_dd_in_flight():
    _reset_gauge()
    t0 = time.time()
    asyncio.run(AutonomousDeployEngine._await_dd_quiescence(object(), max_wait_s=5.0, poll_s=0.1))
    assert time.time() - t0 < 1.0, "no in-flight DD → deploy proceeds immediately"


def test_rf3010_waits_then_proceeds_bounded_when_dd_stuck():
    _reset_gauge()
    ddo._dd_inflight_inc()  # simulate a DD that never clears
    try:
        t0 = time.time()
        asyncio.run(AutonomousDeployEngine._await_dd_quiescence(object(), max_wait_s=0.4, poll_s=0.1))
        elapsed = time.time() - t0
        assert elapsed >= 0.35, "it must actually defer while a DD is in-flight"
        assert elapsed < 4.0, "but it must be BOUNDED — never block deploys forever"
    finally:
        _reset_gauge()


def test_rf3010_deploy_gates_on_quiescence_but_force_skips():
    i = _SRC.index("async def deploy(")
    body = _SRC[i:i + 1600]
    assert "_await_dd_quiescence" in body, "deploy() must wait for DD quiescence"
    assert "if not force" in body, "a forced deploy skips the wait (emergency path)"

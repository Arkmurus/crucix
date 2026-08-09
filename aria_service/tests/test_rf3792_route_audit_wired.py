"""R-F3792 — the duplicate-route guard must reach the brain on EVERY branch.

Before this, `log_duplicate_routes` only called `logger.error`. Per CLAUDE.md §21a
that is DARK: a local log line is not a brain sink, so neither the coder loop
(capability_gaps) nor the health metric (/api/aria/brain/stats) could learn that the
guard had fired — or that it had stopped working.

R-F3791 established that the interesting failure is not "a duplicate shipped" but
"the detector stopped being able to see one". So the branch that matters most is the
BLIND one, and it must be reported as an unmeasured result rather than a clean one.

The mirror risk is a guard that cries wolf: a router with genuinely no routes is not
blind, and test 5 pins that distinction.
"""
from __future__ import annotations

import logging

import pytest
from fastapi import APIRouter, FastAPI

from aria_service import route_audit


class _Recorder:
    """Captures the wiring calls instead of dispatching them to the brain."""

    def __init__(self) -> None:
        self.failures: list[dict] = []
        self.successes: list[dict] = []

    def install(self, monkeypatch) -> "_Recorder":
        from aria_service.intel import engine_wiring

        monkeypatch.setattr(engine_wiring, "wire_failure",
                            lambda **kw: self.failures.append(kw))
        monkeypatch.setattr(engine_wiring, "wire_success",
                            lambda **kw: self.successes.append(kw))
        return self


@pytest.fixture
def wiring(monkeypatch) -> _Recorder:
    return _Recorder().install(monkeypatch)


def _clean_app() -> FastAPI:
    app = FastAPI()
    router = APIRouter(prefix="/api/x")

    @router.get("/a")
    async def a_ep():
        return {}

    app.include_router(router)
    return app


def _duplicated_app() -> FastAPI:
    app = FastAPI()
    first, second = APIRouter(), APIRouter()

    @first.get("/dup")
    async def canonical_ep():
        return {}

    @second.get("/dup")
    async def shadowed_ep():
        return {}

    app.include_router(first)
    app.include_router(second)
    return app


class _BlindContainer:
    """Routes are declared, but none are enumerable — the R-F3791 signature."""

    def __init__(self) -> None:
        self.routes = [object(), object()]


# ── 1. success branch ────────────────────────────────────────────────────────

def test_a_clean_audit_reports_success_to_the_brain(wiring):
    app = _clean_app()
    dups = route_audit.log_duplicate_routes(app)

    assert dups == {}
    assert not wiring.failures
    assert len(wiring.successes) == 1, "§21a: the SUCCESS branch must emit too"
    assert wiring.successes[0]["module"] == "route_audit"
    # The count is the evidence that something was actually examined, so it is
    # derived rather than hardcoded — a bare FastAPI() already carries its four
    # built-in doc routes, and a literal here would assert the fixture, not the walk.
    examined = sum(1 for _ in route_audit.iter_routes(app))
    assert examined > 1
    assert f"{examined} routes" in wiring.successes[0]["summary"]


# ── 2. failure branch ────────────────────────────────────────────────────────

def test_a_duplicate_reports_a_module_bug_with_a_reproducer(wiring):
    dups = route_audit.log_duplicate_routes(_duplicated_app())

    assert ("GET", "/dup") in dups
    assert not wiring.successes
    assert len(wiring.failures) == 1
    sent = wiring.failures[0]
    assert sent["gap_type"] == "module_bug"
    # R-F1857 only mints a MODULE_BUG the coder can reproduce; name the test.
    assert "test_rf2278_no_duplicate_routes" in sent["detail"]
    assert "canonical_ep" in sent["detail"]


def test_a_duplicate_is_still_logged_at_error(wiring, caplog):
    with caplog.at_level(logging.ERROR, logger="aria.route_audit"):
        route_audit.log_duplicate_routes(_duplicated_app())
    assert any("DUPLICATE ROUTE" in r.getMessage() for r in caplog.records)


# ── 3. the branch R-F3791 was about: blind, not clean ────────────────────────

def test_a_blind_audit_is_reported_as_unmeasured_not_clean(wiring, caplog):
    with caplog.at_level(logging.ERROR, logger="aria.route_audit"):
        dups = route_audit.log_duplicate_routes(_BlindContainer())

    assert dups == {}
    assert not wiring.successes, (
        "an empty RESULT from an empty UNIVERSE must never be reported as a clean "
        "audit — that is the fabricated pass R-F3791 found"
    )
    assert len(wiring.failures) == 1
    assert wiring.failures[0]["gap_type"] == "boot_state_regression"
    assert "blind" in wiring.failures[0]["detail"].lower()
    assert any("BLIND" in r.getMessage() for r in caplog.records)


# ── 4. the audit itself breaking ─────────────────────────────────────────────

def test_a_crashing_audit_reports_and_never_raises(wiring, monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("route table exploded")

    monkeypatch.setattr(route_audit, "iter_routes", _boom)

    assert route_audit.log_duplicate_routes(_clean_app()) == {}
    assert len(wiring.failures) == 1
    assert wiring.failures[0]["gap_type"] == "engine_failure"
    assert "route table exploded" in wiring.failures[0]["detail"]


# ── 5. the anti-cry-wolf half ────────────────────────────────────────────────

def test_a_genuinely_empty_app_is_clean_not_blind(wiring):
    """No routes DECLARED is not the same as routes declared but unreadable.
    Conflating them would fire a boot_state_regression on every empty router."""
    empty = APIRouter()

    assert route_audit.log_duplicate_routes(empty) == {}
    assert not wiring.failures, "an empty router is not a blind audit"
    assert len(wiring.successes) == 1


def test_wiring_never_propagates_an_exception_to_boot(monkeypatch):
    """main.py:4783 runs this at import. A broken brain must not break boot."""
    from aria_service.intel import engine_wiring

    def _boom(**_k):
        raise RuntimeError("brain unreachable")

    monkeypatch.setattr(engine_wiring, "wire_success", _boom)
    monkeypatch.setattr(engine_wiring, "wire_failure", _boom)

    assert route_audit.log_duplicate_routes(_clean_app()) == {}
    assert route_audit.log_duplicate_routes(_duplicated_app()) != {}


# ── 6. R-F3816: WHERE the audit runs decides whether its wiring reaches anyone ──

def test_the_audit_is_not_invoked_at_module_import():
    """R-F3816 — it used to run at import (main.py, just after include_router).

    That was wrong twice over, and both only surfaced when the deploy was verified
    live rather than assumed:
      * it audited 754 of 770 routes, because /static, / and /download/* are
        registered further down the module;
      * its R-F3792 brain signal was emitted before the state store existed, so
        /api/aria/brain/stats showed no `route_audit` module at all — a wiring that
        emits into a store which cannot accept it is DARK by §21a, which is the very
        condition R-F3792 was written to remove.

    Pinned by source: an import-time call cannot be observed from inside a test that
    has already imported the module.
    """
    import aria_service.main as _m

    from ._source_probe import module_source

    # module_source takes a module OBJECT or a path — not a dotted name.
    src = module_source(_m)
    head = src.split("async def lifespan", 1)[0]
    assert "_log_dup_routes(app)" not in head, (
        "the route audit is being invoked at module-import time again — it will "
        "audit an incomplete table and its brain signal will be dropped"
    )
    assert "_log_dup_routes(app)" in src, "the audit must still run somewhere"


def test_the_audit_runs_during_lifespan_over_the_complete_table():
    """CAPABILITY: drive the real lifespan and assert the signal that reaches the
    brain describes the WHOLE route table."""
    import asyncio

    from aria_service.intel import engine_wiring
    from aria_service.main import app, lifespan
    from aria_service.route_audit import iter_routes

    seen: list[dict] = []
    orig = engine_wiring.wire_success
    engine_wiring.wire_success = lambda **kw: seen.append(kw)
    try:
        async def _go():
            async with lifespan(app):
                pass
        asyncio.run(_go())
    finally:
        engine_wiring.wire_success = orig

    audits = [kw for kw in seen if kw.get("module") == "route_audit"]
    assert audits, "the route audit did not report to the brain during lifespan"
    total = sum(1 for _ in iter_routes(app))
    assert f"{total} routes" in audits[-1]["summary"], (
        f"the audit must cover the COMPLETE table ({total} routes); "
        f"got {audits[-1]['summary']!r}"
    )

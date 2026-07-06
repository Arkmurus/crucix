"""R-F1421 — boot intel-init isolation (no total outage from one bad subsystem).

Pre-R-F1421 the 6 intel inits ran as bare `await x.init()`; one throw made the
lifespan raise → uvicorn never reached `yield` → the app never served → total
outage (the 2026-04-27 F28 class). R-F1421 runs them through _run_boot_inits
which isolates each failure and returns the failed names, so ARIA stays UP
(degraded) and surfaces what broke.

Drives the REAL _run_boot_inits helper that the lifespan now calls.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.main import _run_boot_inits


def test_all_ok_returns_empty():
    calls = []

    async def _ok_a():
        calls.append("a")

    async def _ok_b():
        calls.append("b")

    failed = asyncio.run(_run_boot_inits([("a", _ok_a), ("b", _ok_b)]))
    assert failed == []
    assert calls == ["a", "b"]  # ran in order


def test_one_failure_isolated_others_still_run():
    calls = []

    async def _ok():
        calls.append("ok")

    async def _boom():
        raise RuntimeError("subsystem down")

    # the failing init in the MIDDLE must not stop the ones after it
    failed = asyncio.run(_run_boot_inits([
        ("first_ok", _ok),
        ("broken", _boom),
        ("third_ok", _ok),
    ]))
    assert failed == ["broken"]          # the failure is reported
    assert calls == ["ok", "ok"]         # first AND third still ran
    # crucially: _run_boot_inits did NOT raise — the lifespan reaches `yield`


def test_all_fail_still_returns_not_raises():
    async def _boom():
        raise ValueError("nope")

    failed = asyncio.run(_run_boot_inits([
        ("k", _boom), ("l", _boom), ("m", _boom),
    ]))
    # even with EVERYTHING broken, the app boots (degraded) rather than dying
    assert set(failed) == {"k", "l", "m"}


def test_empty_list():
    assert asyncio.run(_run_boot_inits([])) == []


def test_hanging_init_is_bounded_and_other_init_runs(monkeypatch):
    monkeypatch.setenv("ARIA_BOOT_INIT_TIMEOUT_S", "0.05")
    calls = []

    async def _hang():
        await asyncio.sleep(10)

    async def _ok():
        calls.append("ok")

    failed = asyncio.run(_run_boot_inits([
        ("hung", _hang),
        ("ok", _ok),
    ]))
    assert failed == ["hung"]
    assert calls == ["ok"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

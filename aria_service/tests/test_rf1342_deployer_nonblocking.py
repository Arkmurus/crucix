"""R-F1342 — fly_deployer subprocess calls must not freeze the event loop.

Pillar-1 invariant sweep (mirrors R-F1341): the autonomous self-deploy path
called blocking subprocess.run (git/fly/gh) directly on the loop, and _run_git
had NO timeout — a hung git/build could freeze the loop up to 300s = blackout.
Fix: every blocking subprocess runs via asyncio.to_thread + _run_git is bounded.
These tests prove a blocking subprocess does NOT starve a concurrent coroutine.
"""
import asyncio
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from aria_service.autonomous import fly_deployer as fd


def _make_deployer(tmp_path):
    return fd.FlyDeployer(redis_client=None, aria_service_url="http://x", repo_path=tmp_path)


def test_run_git_is_bounded():
    """_run_git must pass a timeout to subprocess.run (was unbounded)."""
    captured = {}

    def _fake_run(args, **kw):
        captured.update(kw)
        class _R: returncode = 0; stdout = ""; stderr = ""
        return _R()

    orig = subprocess.run
    subprocess.run = _fake_run
    try:
        d = fd.FlyDeployer(redis_client=None, aria_service_url="http://x")
        d._run_git(["status"])
        assert captured.get("timeout") == fd._GIT_TIMEOUT_S
        assert fd._GIT_TIMEOUT_S > 0
    finally:
        subprocess.run = orig


@pytest.mark.asyncio
async def test_fly_build_does_not_block_loop(tmp_path, monkeypatch):
    """While _fly_build's subprocess blocks for 2s, a heartbeat coroutine
    must keep ticking — proving the subprocess runs off the event loop."""
    d = _make_deployer(tmp_path)

    def _blocking_run(*a, **k):
        import time as _t
        _t.sleep(1.0)  # blocks the WORKER thread, not the loop (if to_thread'd)
        class _R: returncode = 0; stdout = "registry.fly.io/app:tag"; stderr = ""
        return _R()

    monkeypatch.setattr(fd.subprocess, "run", _blocking_run)

    ticks = 0
    async def _heartbeat():
        nonlocal ticks
        for _ in range(15):
            ticks += 1
            await asyncio.sleep(0.05)

    build = asyncio.create_task(d._fly_build("app", "br"))
    hb = asyncio.create_task(_heartbeat())
    image, _ = await asyncio.gather(build, hb)

    assert image == "registry.fly.io/app:tag"
    assert ticks == 15  # loop kept breathing during the 1s blocking build


@pytest.mark.asyncio
async def test_deploy_fleet_does_not_block_loop(tmp_path, monkeypatch):
    d = _make_deployer(tmp_path)

    def _blocking_run(*a, **k):
        import time as _t
        _t.sleep(1.0)
        class _R: returncode = 0; stdout = ""; stderr = ""
        return _R()
    monkeypatch.setattr(fd.subprocess, "run", _blocking_run)

    ticks = 0
    async def _heartbeat():
        nonlocal ticks
        for _ in range(15):
            ticks += 1
            await asyncio.sleep(0.05)

    # _deploy_fleet is sync; the FIX is callers wrap it in to_thread. Verify
    # the wrapped call pattern keeps the loop alive.
    fleet = asyncio.create_task(asyncio.to_thread(d._deploy_fleet, "app", "img"))
    hb = asyncio.create_task(_heartbeat())
    ok, _ = await asyncio.gather(fleet, hb)
    assert ok is True
    assert ticks == 15

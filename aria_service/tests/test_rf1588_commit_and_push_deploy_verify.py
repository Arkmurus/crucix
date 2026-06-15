"""R-F1588 — commit_and_push must tag [deploy] + verify the deploy landed.

Operator caught it 3× on 2026-06-15: ARIA's autonomous commit path
(SelfCodingOS.commit_and_push) committed WITHOUT the [deploy] tag, so the CI
deploy (deploy-fly.yml, gated on "[deploy]" in the message) never fired, and
it returned success: True regardless — a push that never reached the server
read as done. Only the operator noticed origin had moved ahead of live.

R-F1588: append [deploy] (so CI deploys on push) and poll
aria-intel/health/live until build_rev contains the pushed sha; if it doesn't
land (and the commit touched a deployable path), return success: False with
deploy_lag: True — never report success on a push that isn't live (§19e).

These CAPABILITY tests drive the real commit_and_push with git + the live
poll mocked, asserting the user-visible contract.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel.self_coding_os import SelfCodingOS


class _FakeProc:
    def __init__(self, rc=0, out=b"", err=b""):
        self.returncode = rc
        self._out, self._err = out, err

    async def communicate(self):
        return self._out, self._err


def _exec_recorder(calls, head_sha=b"abc1234def\n"):
    async def _fake_exec(*args, **kwargs):
        calls.append(args)
        if args[:2] == ("git", "rev-parse"):
            return _FakeProc(0, head_sha, b"")
        return _FakeProc(0, b"", b"")
    return _fake_exec


async def _true(*a, **k):
    return True


async def _false(*a, **k):
    return False


def _commit_msg(calls):
    """Return the -m argument of the git commit call."""
    for args in calls:
        if args[:2] == ("git", "commit"):
            # args == ("git","commit","-m", <msg>)
            return args[3]
    return None


@pytest.mark.asyncio
async def test_rf1588_tags_deploy_and_succeeds_when_live(monkeypatch):
    os_ = SelfCodingOS()
    calls: list = []
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _exec_recorder(calls))
    monkeypatch.setattr(os_, "_commit_touches_deployable", _true)
    monkeypatch.setattr(os_, "_verify_live_build_rev", _true)

    res = await os_.commit_and_push("R-F9999", "fix: something real")

    assert res["success"] is True
    assert res["deployed"] is True
    # [deploy] tag must have been added so CI fires.
    assert "[deploy]" in _commit_msg(calls)


@pytest.mark.asyncio
async def test_rf1588_deploy_lag_is_loud_failure(monkeypatch):
    """The core fix: push succeeded but the deploy did NOT land — must be a
    FAILURE with deploy_lag, never a silent success."""
    os_ = SelfCodingOS()
    calls: list = []
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _exec_recorder(calls))
    monkeypatch.setattr(os_, "_commit_touches_deployable", _true)
    monkeypatch.setattr(os_, "_verify_live_build_rev", _false)

    res = await os_.commit_and_push("R-F9999", "fix: something real")

    assert res["success"] is False, "a push that didn't deploy must NOT report success"
    assert res.get("deploy_lag") is True
    assert "abc1234" in res.get("sha", "")
    assert "origin/main" in res.get("error", "")  # surfaces that code is pushed, not live


@pytest.mark.asyncio
async def test_rf1588_no_false_alarm_on_non_deployable(monkeypatch):
    """docs/tooling-only commit → no deploy expected → success without lag."""
    os_ = SelfCodingOS()
    calls: list = []
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _exec_recorder(calls))
    monkeypatch.setattr(os_, "_commit_touches_deployable", _false)
    # _verify_live_build_rev must NOT even be consulted; make it blow up if used.
    async def _boom(*a, **k):
        raise AssertionError("verify should be skipped for non-deployable commit")
    monkeypatch.setattr(os_, "_verify_live_build_rev", _boom)

    res = await os_.commit_and_push("R-F9999", "docs: update README")

    assert res["success"] is True
    assert res["deployed"] is True
    assert res.get("note") == "no deployable paths changed"


@pytest.mark.asyncio
async def test_rf1588_does_not_double_tag_deploy(monkeypatch):
    os_ = SelfCodingOS()
    calls: list = []
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _exec_recorder(calls))
    monkeypatch.setattr(os_, "_commit_touches_deployable", _true)
    monkeypatch.setattr(os_, "_verify_live_build_rev", _true)

    await os_.commit_and_push("R-F9999", "fix: already tagged [deploy]")

    assert _commit_msg(calls).count("[deploy]") == 1


@pytest.mark.asyncio
async def test_rf1588_push_failure_still_fails(monkeypatch):
    """Regression: a genuine git push failure must still return success False."""
    os_ = SelfCodingOS()

    async def _fake_exec(*args, **kwargs):
        if args[:2] == ("git", "push"):
            return _FakeProc(1, b"", b"rejected: non-fast-forward")
        return _FakeProc(0, b"", b"")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    res = await os_.commit_and_push("R-F9999", "fix: x")
    assert res["success"] is False
    assert "push failed" in res.get("error", "")

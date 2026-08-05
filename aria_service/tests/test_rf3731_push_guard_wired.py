"""R-F3731 — CAPABILITY: the deploy push-guard's refusals now reach the brain.

`check_push_guard` is what stops a deploy of un-pushed code (§11). All seven of
its refusal paths ended at logger.warning/logger.error and nothing else, so
"the guard refused" — including "I could not read origin/main", an
infrastructure fault rather than a developer mistake — never reached the brain,
could not raise a gap, and could not self-heal. §21a: a local log is DARK.

These tests assert the SIGNAL and, just as importantly, that the guard's answer
is unchanged — a safety guard must not change its verdict because it learned to
talk.

Run: python -m pytest aria_service/tests/test_rf3731_push_guard_wired.py -v
"""
from __future__ import annotations

import pytest

from aria_service.utils import git_utils


@pytest.fixture()
def sink(monkeypatch):
    """Capture what reaches the brain."""
    seen: dict[str, list] = {"ok": [], "fail": []}
    from aria_service.intel import engine_wiring as ew
    monkeypatch.setattr(ew, "wire_success",
                        lambda **kw: seen["ok"].append(kw))
    monkeypatch.setattr(ew, "wire_failure",
                        lambda **kw: seen["fail"].append(kw))
    return seen


def test_a_refused_deploy_reaches_the_brain(monkeypatch, sink):
    """THE HEADLINE: a refusal was previously invisible outside a log line."""
    monkeypatch.setattr(git_utils, "_check_push_guard_impl", lambda *a, **k: False)

    assert git_utils.check_push_guard("deadbeef" * 5) is False
    assert len(sink["fail"]) == 1, "a refused deploy must raise a brain signal"
    assert sink["fail"][0]["module"] == "git_utils"
    assert "REFUSED" in sink["fail"][0]["detail"]
    assert not sink["ok"]


def test_a_passing_guard_reaches_the_brain(monkeypatch, sink):
    """§21a needs BOTH branches, not just the failure one."""
    monkeypatch.setattr(git_utils, "_check_push_guard_impl", lambda *a, **k: True)

    assert git_utils.check_push_guard("cafebabe" * 5) is True
    assert len(sink["ok"]) == 1
    assert not sink["fail"]


def test_the_verdict_is_unchanged(monkeypatch, sink):
    """A safety guard must not change its answer because it learned to talk."""
    for verdict in (True, False):
        monkeypatch.setattr(git_utils, "_check_push_guard_impl",
                            lambda *a, **k: verdict)
        assert git_utils.check_push_guard("abc123") is verdict


def test_the_arguments_are_passed_through(monkeypatch, sink):
    """The wrapper must not swallow git_root — that would silently change scope."""
    got: dict = {}

    def _impl(commit_sha, git_root=None):
        got["sha"], got["root"] = commit_sha, git_root
        return True

    monkeypatch.setattr(git_utils, "_check_push_guard_impl", _impl)
    git_utils.check_push_guard("sha-here", git_root="/some/root")
    assert got == {"sha": "sha-here", "root": "/some/root"}


def test_a_raising_impl_still_signals_and_still_raises(monkeypatch, sink):
    """An exception is a failure too, and must not be converted into a pass."""
    def _boom(*a, **k):
        raise RuntimeError("disk gone")

    monkeypatch.setattr(git_utils, "_check_push_guard_impl", _boom)
    with pytest.raises(RuntimeError):
        git_utils.check_push_guard("abc123")
    assert len(sink["fail"]) == 1, "a crash must reach the brain as a failure"
    assert not sink["ok"], "a crash must never be reported as a passing guard"


def test_broken_wiring_cannot_break_the_guard(monkeypatch):
    """The guard outranks its own telemetry."""
    from aria_service.intel import engine_wiring as ew

    def _explode(**kw):
        raise RuntimeError("brain unreachable")

    monkeypatch.setattr(ew, "wire_success", _explode)
    monkeypatch.setattr(ew, "wire_failure", _explode)
    monkeypatch.setattr(git_utils, "_check_push_guard_impl", lambda *a, **k: True)

    assert git_utils.check_push_guard("abc123") is True, (
        "a wiring failure must never be what stops a deploy guard from answering"
    )

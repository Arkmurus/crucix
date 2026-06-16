"""R-F1611 — deploy-proprioception: self_introspect surfaces live build_rev +
uptime + autonomous-loop health.

Operator 2026-06-16: ARIA is "blind to what she ships" and confabulated
("LLM 75-81%") because the self_introspect block omitted what code is LIVE and
which loops run. R-F1611 adds _build_deploy_lines() to surface real state so she
answers "what version am I / what did I deploy / are my loops alive" from truth.

These tests drive the real _build_deploy_lines() against a controlled main.
"""
from __future__ import annotations

import time

import aria_service.main as m
from aria_service.intel.self_introspect_guard import _build_deploy_lines


class _FakeTask:
    def __init__(self, name, done):
        self._n, self._d = name, done

    def get_name(self):
        return self._n

    def done(self):
        return self._d


def test_rf1611_surfaces_build_rev_uptime_and_loop_health(monkeypatch):
    monkeypatch.setattr(m, "ARIA_BUILD_REV", "R-F9999 · sha deadbeef", raising=False)
    monkeypatch.setattr(m, "_BOOT_TIME", time.time() - 3700, raising=False)  # ~1h
    monkeypatch.setattr(m, "_BG_RESPAWN",
                        {"research_loop": lambda: None, "tender_monitor_loop": lambda: None},
                        raising=False)
    # research live, tender dead
    monkeypatch.setattr(m, "_BG_TASKS",
                        {_FakeTask("research_loop", False)}, raising=False)

    out = "\n".join(_build_deploy_lines())

    assert "R-F9999 · sha deadbeef" in out, "live build_rev not surfaced"
    assert "1h" in out, "uptime not surfaced"
    assert "1/2 live" in out, "loop-health count wrong"
    assert "tender_monitor_loop" in out and "DEAD" in out, "dead loop not flagged"


def test_rf1611_all_loops_healthy(monkeypatch):
    monkeypatch.setattr(m, "ARIA_BUILD_REV", "R-F1611 · sha abc1234", raising=False)
    monkeypatch.setattr(m, "_BOOT_TIME", time.time() - 60, raising=False)
    monkeypatch.setattr(m, "_BG_RESPAWN", {"research_loop": lambda: None}, raising=False)
    monkeypatch.setattr(m, "_BG_TASKS", {_FakeTask("research_loop", False)}, raising=False)

    out = "\n".join(_build_deploy_lines())
    assert "1/1 live" in out
    assert "all healthy" in out
    assert "DEAD" not in out


def test_rf1611_never_raises_returns_lines(monkeypatch):
    """Even if main attrs are missing, it returns lines (never crashes the
    introspect block) and never invents a version."""
    monkeypatch.delattr(m, "_BG_RESPAWN", raising=False)
    out = _build_deploy_lines()
    assert isinstance(out, list) and out  # always returns something usable

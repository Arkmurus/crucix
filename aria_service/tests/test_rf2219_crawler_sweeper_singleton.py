"""R-F2219 — the crawler and expiry_sweeper are engine SINGLETONS.

Both do work that must run on exactly ONE process when multi-worker is enabled:
the crawler does external N× effects (crawls sites + writes the shared search
index); the expiry_sweeper N×'s DELETE load on the shared state_store DB. Both
are STARTED BEFORE the engine election resolves, so the gate must await the
election-complete signal, then honour the role. Before R-F2219 neither was
gated (missed in the R-F2073 sweep) → N crawlers/sweepers on multi-worker.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from aria_service import main


def _reset_role(monkeypatch, role: str):
    # Bypass env; drive the resolved-role global the election would set.
    monkeypatch.setattr(main, "_resolved_role", role, raising=False)
    monkeypatch.delenv("ARIA_ROLE", raising=False)


class TestR_F2219_ExpirySweeperGate:
    """Drive the REAL _expiry_sweeper_loop under each role."""

    def _run_sweeper(self, monkeypatch, role: str):
        calls = {"n": 0}

        async def _fake_sweep():
            calls["n"] += 1
            return 0

        import aria_service.intel.state_store as _ss
        monkeypatch.setattr(_ss, "sweep_expired", _fake_sweep, raising=False)
        _reset_role(monkeypatch, role)

        async def _go():
            main._election_complete = asyncio.Event()
            main._election_complete.set()  # election already resolved
            try:
                # role='web' → returns immediately; role='all' → enters loop,
                # sweeps once, then blocks on sleep(300) → we time out.
                await asyncio.wait_for(main._expiry_sweeper_loop(), timeout=1.0)
            except asyncio.TimeoutError:
                pass
            return calls["n"]

        return asyncio.run(_go())

    def test_web_role_does_not_sweep(self, monkeypatch):
        assert self._run_sweeper(monkeypatch, "web") == 0, (
            "web worker must NOT run the expiry sweeper (engine singleton)"
        )

    def test_engine_role_sweeps(self, monkeypatch):
        assert self._run_sweeper(monkeypatch, "engine") >= 1

    def test_all_role_sweeps(self, monkeypatch):
        # Default single-process role — must still sweep (no regression).
        assert self._run_sweeper(monkeypatch, "all") >= 1


class TestR_F2219_ElectionGateWiring:
    """Structural locks — the pre-election singletons await the gate + honour role."""

    def _src(self) -> str:
        return (Path(main.__file__)).read_text(encoding="utf-8")

    def test_election_complete_event_exists_and_is_set_after_election(self):
        src = self._src()
        assert "_election_complete" in src
        # The election runs and _election_complete is set right after it resolves.
        # R-F2541: the election is now wrapped in asyncio.wait_for(...) with a bounded
        # timeout (falls back to role="all" on timeout) — assert it's invoked in either
        # the bare-await or the wait_for form, not the exact old literal.
        assert ("await _elect_engine_role()" in src
                or "wait_for(_elect_engine_role()" in src), \
            "election must be invoked (bare await or asyncio.wait_for form)"
        assert "_election_complete.set()" in src

    def test_crawler_is_election_gated_singleton(self):
        src = self._src()
        # The crawler block waits for the election and skips on non-singleton roles.
        assert "crawler SKIPPED" in src
        # The R-F2219 comment marks the crawler gate.
        assert "R-F2219: the crawler is an engine SINGLETON" in src

    def test_expiry_sweeper_is_election_gated_singleton(self):
        src = self._src()
        assert "expiry_sweeper SKIPPED" in src

"""R-F2207 + R-F2208 — agent liveness: honest staleness + self-heal boot-race.

R-F2207: `ContractRegistry.validate_contract` must judge heartbeat staleness
against each agent's OWN declared cadence (`check_interval_s`), not a flat 300s
floor. Before the fix, every agent cycling slower than 5 min (self_improve=2h,
student_reading=6h, watchlist_rescreen=24h) was PERPETUALLY flagged "stale" —
flooding the R-F1448 log and masking the one agent genuinely overdue.

R-F2208: the self_improve loop must start UNCONDITIONALLY and re-check the LLM
provider per-cycle. Before the fix a boot-time `is_configured==False` (LLM
resilience init race) left self_improve dark for the whole process life.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from aria_service.intel.agent_contract import AgentContract, ContractRegistry


def _mk_contract(agent_id: str, cadence: int) -> AgentContract:
    return AgentContract(
        agent_id=agent_id,
        version="1.0.0",
        directives=["do work"],
        inputs=["x"],
        outputs=["y"],
        error_modes=["z"],
        dependencies=[],
        check_interval_s=cadence,
        critical=False,
    )


async def _validate_with_age(monkeypatch, agent_id: str, cadence: int, age_s: float):
    """Drive the REAL validate_contract with a mocked registry status."""
    reg = ContractRegistry()

    # Contract lookup → our fabricated contract (no DB/redis needed).
    async def _fake_get_contract(aid):
        return _mk_contract(aid, cadence) if aid == agent_id else None

    monkeypatch.setattr(reg, "get_contract", _fake_get_contract)

    # Registry status → stale with the given age (mirrors a slow-cycle agent).
    from aria_service.intel import agent_registry as _ar

    async def _fake_status(self, aid):
        return {"status": "stale", "heartbeat_age_s": age_s}

    monkeypatch.setattr(_ar.AgentRegistry, "get_agent_status", _fake_status)

    # Don't touch redis when recording violations.
    async def _noop_record(aid, violations):
        return None

    monkeypatch.setattr(reg, "_record_violations", _noop_record)

    return await reg.validate_contract(agent_id)


class TestR_F2207_IntervalAwareStaleness:
    def _stale_violations(self, viols):
        return [v for v in viols if v.violation_type == "stale_heartbeat"]

    def test_on_cadence_agent_not_flagged(self, monkeypatch):
        """student_reading: 6.1h old on a 6h cadence → NOT stale (was flagged before)."""
        viols = asyncio.run(_validate_with_age(monkeypatch, "student_reading", 21600, 21916))
        assert self._stale_violations(viols) == [], (
            "on-cadence agent must not be flagged stale (R-F2207 false-alarm)"
        )

    def test_genuinely_overdue_agent_still_flagged_error(self, monkeypatch):
        """self_improve: 32.6h old on a 2h cadence → still ERROR (real signal kept)."""
        viols = asyncio.run(_validate_with_age(monkeypatch, "self_improve", 7200, 117405))
        stale = self._stale_violations(viols)
        assert stale, "genuinely-overdue agent MUST still flag (real dead-loop signal)"
        assert stale[0].severity == "ERROR", f"expected ERROR, got {stale[0].severity}"

    def test_daily_agent_at_6h_not_flagged(self, monkeypatch):
        """watchlist_rescreen: 6.3h old on a 24h cadence → NOT stale."""
        viols = asyncio.run(_validate_with_age(monkeypatch, "watchlist_rescreen", 86400, 22815))
        assert self._stale_violations(viols) == []

    def test_flat_300s_behaviour_is_gone(self, monkeypatch):
        """A 30-min-cadence agent at 16 min (<cadence) must NOT flag.

        Under the OLD flat-300s rule this WOULD flag (960 > 300). This is the
        discriminating regression lock.
        """
        viols = asyncio.run(_validate_with_age(monkeypatch, "research_engine", 1800, 960))
        assert self._stale_violations(viols) == []


class TestR_F2208_SelfImproveBootRace:
    """Lock the structural fix: self_improve is no longer gated at creation on
    boot-time provider state, and the loop re-checks the provider per-cycle."""

    def _main_src(self) -> str:
        p = Path(__file__).resolve().parent.parent / "main.py"
        return p.read_text(encoding="utf-8")

    def test_registration_is_unconditional(self):
        src = self._main_src()
        # The old guarded registration must be gone.
        assert (
            'if getattr(app.state, "llm_provider", None) and getattr(app.state.llm_provider, "is_configured", False):\n'
            '        asyncio.create_task(_register_agent(\n'
            '            "self_improve"'
        ) not in src, "self_improve registration must be unconditional (R-F2208)"

    def test_loop_rechecks_provider_per_cycle(self):
        src = self._main_src()
        # The per-cycle self-heal re-check must exist inside the loop.
        assert "R-F2208: re-check the provider per-cycle" in src
        assert "_llm_now = getattr(app.state, \"llm_provider\", None)" in src
        assert "LLM not configured yet — re-check in 30 min" in src

    def test_loop_created_without_startup_llm_guard(self):
        src = self._main_src()
        # The loop-creation block now uses the unconditional marker.
        assert "R-F2208: always START the loop" in src

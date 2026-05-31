"""R-F1207 — Capability test: WebIntegrityAgent is wired into main.py lifespan.

Proves that:
1. main.py imports WebIntegrityAgent
2. The agent is started during lifespan
3. The agent is stopped during shutdown
4. All background loops register in the agent registry
5. AutonomousScheduler._fix_gaps is wired to real gap detection
6. Deprecated modules are marked
"""
import pytest
from pathlib import Path

# Tests run from aria_service/tests/, so repo root is two levels up
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _read(path: str) -> str:
    """Read a file relative to repo root."""
    return (_REPO_ROOT / path).read_text(encoding="utf-8")


def test_web_integrity_agent_imported_in_main():
    """Verify main.py references WebIntegrityAgent."""
    source = _read("aria_service/main.py")

    # Check that WebIntegrityAgent is imported
    assert "WebIntegrityAgent" in source, (
        "WebIntegrityAgent must be imported in main.py"
    )

    # Check that .start() is called
    assert "web_integrity_agent.start()" in source, (
        "WebIntegrityAgent.start() must be called in main.py lifespan"
    )

    # Check that .stop() is called on shutdown
    assert "web_integrity_agent.stop()" in source, (
        "WebIntegrityAgent.stop() must be called on shutdown"
    )

    # Check that the agent is created with redis_store
    assert "redis_store=rs" in source, (
        "WebIntegrityAgent must be created with redis_store"
    )


def test_agent_registry_registrations_in_main():
    """Verify all background loops register in the agent registry."""
    source = _read("aria_service/main.py")

    # Each agent should have a _register_agent call
    expected_agents = [
        "research_engine",
        "self_improve",
        "student_quiz",
        "student_reading",
        "library_consolidation",
        "proactive_watch",
        "weekly_report",
        "watchlist_rescreen",
        "tender_monitor",
        "web_crawler",
        "web_integrity",
        "self_healing",
    ]
    for agent_id in expected_agents:
        assert f'"{agent_id}"' in source or f"'{agent_id}'" in source, (
            f"Agent '{agent_id}' must be registered in main.py"
        )


def test_autonomous_scheduler_fix_gaps_wired():
    """Verify AutonomousScheduler._fix_gaps is wired to real gap detection."""
    source = _read("aria_service/intel/autonomous_scheduler.py")

    # Should reference GapDetector.scan() and ARIACoder.fix_gap()
    assert "GapDetector" in source, (
        "autonomous_scheduler.py must import GapDetector"
    )
    assert "ARIACoder" in source, (
        "autonomous_scheduler.py must import ARIACoder"
    )
    assert "detector.scan()" in source or "await detector.scan()" in source, (
        "_fix_gaps must call GapDetector.scan()"
    )
    assert "coder.fix_gap(gap)" in source or "await coder.fix_gap(gap)" in source, (
        "_fix_gaps must call ARIACoder.fix_gap()"
    )


def test_deprecated_modules_marked():
    """Verify deprecated modules have DEPRECATED markers."""
    # system_health.py
    sh_source = _read("aria_service/intel/system_health.py")
    assert "DEPRECATED" in sh_source, "system_health.py must be marked DEPRECATED"
    assert "R-F1207" in sh_source, "system_health.py must reference R-F1207"

    # infra_health.py
    ih_source = _read("aria_service/intel/infra_health.py")
    assert "DEPRECATED" in ih_source, "infra_health.py must be marked DEPRECATED"
    assert "R-F1207" in ih_source, "infra_health.py must reference R-F1207"

    # cost_monitor.py
    cm_source = _read("aria_service/autonomous/cost_monitor.py")
    assert "SUPERSEDED" in cm_source, "cost_monitor.py must be marked SUPERSEDED"
    assert "R-F1207" in cm_source, "cost_monitor.py must reference R-F1207"


@pytest.mark.asyncio
async def test_web_integrity_agent_start_stop():
    """Prove WebIntegrityAgent.start() and .stop() work end-to-end."""
    from aria_service.intel.web_integrity_agent import WebIntegrityAgent

    agent = WebIntegrityAgent()
    assert not agent._running, "Agent should not be running before start"

    await agent.start()
    assert agent._running, "Agent should be running after start"
    assert agent._task is not None, "Agent should have a background task"

    await agent.stop()
    assert not agent._running, "Agent should not be running after stop"


# ── R-F1209: Agent heartbeat tickers ────────────────────────────────────────


def test_heartbeat_tickers_in_all_loops():
    """Verify every background loop has a _tick_heartbeat call."""
    source = _read("aria_service/main.py")

    # Each registered agent should have a _tick_heartbeat call in its loop
    expected_agents = [
        "research_engine",
        "self_improve",
        "student_quiz",
        "student_reading",
        "library_consolidation",
        "proactive_watch",
        "weekly_report",
        "watchlist_rescreen",
        "tender_monitor",
    ]
    for agent_id in expected_agents:
        assert f'_tick_heartbeat("{agent_id}"' in source, (
            f"Agent '{agent_id}' must have a heartbeat ticker in its loop"
        )


def test_heartbeat_ticker_helper_exists():
    """Verify the _tick_heartbeat helper function exists in main.py."""
    source = _read("aria_service/main.py")
    assert "async def _tick_heartbeat" in source, (
        "_tick_heartbeat helper must be defined in main.py"
    )
    assert "AgentRegistry" in source, (
        "_tick_heartbeat must import AgentRegistry"
    )
    assert "tick_heartbeat" in source, (
        "_tick_heartbeat must call registry.tick_heartbeat"
    )


def test_web_integrity_agent_has_heartbeat():
    """Verify WebIntegrityAgent ticks its heartbeat in _one_cycle."""
    source = _read("aria_service/intel/web_integrity_agent.py")
    assert 'tick_heartbeat("web_integrity"' in source, (
        "WebIntegrityAgent must tick heartbeat in _one_cycle"
    )


# ── R-F1209: Public web monitoring ──────────────────────────────────────────


def test_web_integrity_agent_has_public_endpoints():
    """Verify WebIntegrityAgent monitors live aria-web endpoints."""
    source = _read("aria_service/intel/web_integrity_agent.py")
    assert "_WEB_ENDPOINTS_PUBLIC" in source, (
        "WebIntegrityAgent must define public web endpoints"
    )
    assert "_ARIA_WEB_URL" in source, (
        "WebIntegrityAgent must define the public web URL"
    )
    assert "aria-web.fly.dev" in source, (
        "Public web URL must point to aria-web.fly.dev"
    )
    assert "check_endpoint_public" in source, (
        "WebIntegrityAgent must have a check_endpoint_public function"
    )


def test_web_integrity_agent_cycles_check_public():
    """Verify _one_cycle calls check_endpoint_public for public endpoints."""
    source = _read("aria_service/intel/web_integrity_agent.py")
    assert "_WEB_ENDPOINTS_PUBLIC" in source, (
        "_one_cycle must iterate over _WEB_ENDPOINTS_PUBLIC"
    )
    assert "check_endpoint_public" in source, (
        "_one_cycle must call check_endpoint_public"
    )


def test_web_integrity_agent_status_includes_public():
    """Verify get_status includes public endpoint counts."""
    source = _read("aria_service/intel/web_integrity_agent.py")
    assert "endpoints_public" in source, (
        "get_status must include endpoints_public count"
    )
    assert "endpoints_local" in source, (
        "get_status must include endpoints_local count"
    )


# ── R-F1209: Agent registry messaging ───────────────────────────────────────


def test_agent_registry_messaging_routes_exist():
    """Verify agent messaging routes exist in aria.py."""
    source = _read("aria_service/routes/aria.py")
    assert "send_message" in source, (
        "Agent messaging must have send_message route"
    )
    assert "read_messages" in source, (
        "Agent messaging must have read_messages route"
    )
    assert "/agents/" in source, (
        "Agent messaging routes must be under /agents/"
    )


def test_agent_registry_messaging_implemented():
    """Verify AgentRegistry has send_message and read_messages."""
    source = _read("aria_service/intel/agent_registry.py")
    assert "async def send_message" in source, (
        "AgentRegistry must have send_message method"
    )
    assert "async def read_messages" in source, (
        "AgentRegistry must have read_messages method"
    )
    assert "async def broadcast_message" in source, (
        "AgentRegistry must have broadcast_message method"
    )

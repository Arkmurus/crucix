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
        # R-F1282: web_crawler removed — was a zombie (registered but no loop,
        # and on-demand calls from company_investigator were broken).
        "web_integrity",
        "self_healing",
    ]
    for agent_id in expected_agents:
        assert f'"{agent_id}"' in source or f"'{agent_id}'" in source, (
            f"Agent '{agent_id}' must be registered in main.py"
        )


def test_gap_fixing_wired_via_coder_run_forever():
    """Verify gap-fixing is wired to real gap detection + fix.

    R-F2026: retargeted to the current owner. R-F1700 REMOVED the duplicate
    gap_fixer tick from autonomous_scheduler (it was a divergent, dead+dark
    duplicate that imported the wrong module and swallowed every error). Gap-
    fixing is now owned SOLELY by the coder run_forever path: self_coder.
    AutonomousCoder.run_forever() -> gap_detector.scan() -> fix_gap(), wired
    into the app via start_aria_coder() in main.py. Assert that real path, not
    the removed scheduler one.
    """
    coder = _read("aria_service/autonomous/self_coder.py")
    assert "GapDetector" in coder, "self_coder must use GapDetector"
    assert "async def run_forever" in coder, "AutonomousCoder must have run_forever loop"
    assert "self.gap_detector.scan()" in coder, "run_forever must call gap_detector.scan()"
    assert "self.fix_gap(gap)" in coder, "run_forever must call fix_gap(gap)"

    main = _read("aria_service/main.py")
    assert "start_aria_coder" in main, (
        "main.py must wire the coder via start_aria_coder() at boot"
    )

    # And the scheduler must NOT have resurrected its duplicate gap_fixer TASK
    # (the R-F1700 comment mentions the name; match the actual registration).
    sched = _read("aria_service/intel/autonomous_scheduler.py")
    assert '_tasks["gap_fixer"]' not in sched and "_tasks['gap_fixer']" not in sched, (
        "autonomous_scheduler must NOT re-register the duplicate gap_fixer tick "
        "(R-F1700 — gap-fixing is owned by the coder run_forever path)"
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


# ── R-F1227: 24h playbook enforcement ──────────────────────────────────────


def test_self_healing_contract_validation_loop():
    """Verify self_healing has a contract validation loop."""
    source = _read("aria_service/intel/self_healing.py")
    assert "_contract_validation_loop" in source, (
        "self_healing must have a contract validation loop"
    )
    assert "validate_all_contracts" in source, (
        "Contract validation loop must call validate_all_contracts"
    )
    assert "CONTRACT_REGISTRY" in source, (
        "Contract validation loop must import CONTRACT_REGISTRY"
    )


def test_self_healing_wires_contract_violations():
    """Verify contract violations are wired to the brain."""
    source = _read("aria_service/intel/self_healing.py")
    assert "wire_success" in source, (
        "Contract validation must wire success to brain"
    )
    assert "wire_failure" in source, (
        "Contract validation must wire failure to brain"
    )
    assert "record_gap" in source, (
        "Critical violations must record capability gaps"
    )


def test_wa_notifier_brain_wiring():
    """Verify wa_notifier wires to the brain."""
    source = _read("aria_service/autonomous/wa_notifier.py")
    assert "wire_success" in source, (
        "wa_notifier must wire success to brain"
    )
    assert "wire_failure" in source, (
        "wa_notifier must wire failure to brain"
    )
    assert "wa_notifier:notify" in source, (
        "Brain signal must reference wa_notifier:notify"
    )
    assert "wa_notification_failure" in source, (
        "Failure gap type must be wa_notification_failure"
    )

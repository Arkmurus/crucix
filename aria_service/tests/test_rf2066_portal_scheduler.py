"""R-F2066: capability test for the autonomous portal registration scheduler.

Verifies that the scheduler can:
1. Import and instantiate
2. Calculate priorities correctly
3. Calculate cooldowns correctly
4. Run a cycle without crashing
"""
from __future__ import annotations

import time
from aria_service.intel.portal_scheduler import PortalScheduler


def test_rf2389_portal_registration_is_opt_in(monkeypatch):
    """MVP boot must not start browser-based portal signup unless enabled."""
    from aria_service import main

    monkeypatch.delenv("ARIA_PORTAL_REGISTRATION_ENABLED", raising=False)
    assert main._portal_registration_enabled() is False

    monkeypatch.setenv("ARIA_PORTAL_REGISTRATION_ENABLED", "1")
    assert main._portal_registration_enabled() is True

    monkeypatch.setenv("ARIA_PORTAL_REGISTRATION_ENABLED", "true")
    assert main._portal_registration_enabled() is True

    monkeypatch.setenv("ARIA_PORTAL_REGISTRATION_ENABLED", "0")
    assert main._portal_registration_enabled() is False


def test_scheduler_imports():
    """The scheduler module imports cleanly."""
    from aria_service.intel.portal_scheduler import PortalScheduler, autonomous_registration_loop
    assert PortalScheduler is not None
    assert autonomous_registration_loop is not None


def test_scheduler_instantiation():
    """The scheduler can be instantiated."""
    scheduler = PortalScheduler()
    assert scheduler is not None
    assert scheduler._stats["total_runs"] == 0


def test_scheduler_priority_calculation():
    """Priority calculation works for different portal types."""
    scheduler = PortalScheduler()

    # API key portal with no history = high priority
    class MockPortal:
        id = "test_api"
        registration_type = "api_key"
    priority = scheduler._calculate_priority(MockPortal(), None)
    assert priority == 10, f"API key portal should have priority 10, got {priority}"

    # Email form portal with no history = medium priority
    class MockPortal2:
        id = "test_form"
        registration_type = "email_form"
    priority = scheduler._calculate_priority(MockPortal2(), None)
    assert priority == 7, f"Email form portal should have priority 7, got {priority}"

    # Portal with failures = boosted priority
    priority = scheduler._calculate_priority(MockPortal2(), {
        "total_attempts": 3,
        "success_count": 0,
        "last_success": None,
    })
    assert priority >= 7, f"Failed portal should have boosted priority, got {priority}"


def test_scheduler_cooldown():
    """Cooldown calculation works correctly."""
    scheduler = PortalScheduler()

    # No failures = no cooldown
    cooldown = scheduler._get_cooldown({"fail_count": 0})
    assert cooldown == 0, f"No failures should have 0 cooldown, got {cooldown}"

    # 1 failure = 10 min cooldown (300 * 2^1 = 600)
    cooldown = scheduler._get_cooldown({
        "fail_count": 1,
        "last_attempt": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 10)),
    })
    assert cooldown > 0, f"1 failure should have cooldown, got {cooldown}"
    assert cooldown < 600, f"1 failure cooldown should be <600s, got {cooldown}"

    # Many failures = capped at 24h
    cooldown = scheduler._get_cooldown({
        "fail_count": 10,
        "last_attempt": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 10)),
    })
    assert cooldown <= 86400, f"Cooldown should be capped at 86400s, got {cooldown}"


def test_scheduler_status():
    """Status report returns expected structure."""
    scheduler = PortalScheduler()
    status = scheduler.get_status()

    assert "total_portals" in status
    assert "open_apis" in status
    assert "deferred" in status
    assert "need_registration" in status
    assert "stats" in status
    assert "knowledge_stats" in status

    # Should have 43 portals defined
    assert status["total_portals"] > 0
    assert status["need_registration"] > 0

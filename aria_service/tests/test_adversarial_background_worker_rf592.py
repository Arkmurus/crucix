"""R-F592 — background adversarial worker.

Pre-R-F592 POST /api/aria/adversarial/run-weekly ran synchronously and
blocked the event loop for 4m+ on the degraded LLM chain. Live
evidence 2026-05-16 13:23:01 — fly.io health-check
`servicecheck-00-http-8000` reported failure during the long-running
adversarial sweep. App was actually responding (the event loop just
couldn't process the probe in time).

R-F592 makes the default behaviour BACKGROUND-ASYNC:
  - POST /adversarial/run-weekly returns a run_id immediately
  - GET  /adversarial/runs/{run_id} polls current state
  - GET  /adversarial/runs              lists in-flight + recent

Legacy sync mode preserved via body.sync=true.

Tests are import-level + state-transition — verify the registry
behaves correctly without actually running a 4m adversarial sweep.
"""
from __future__ import annotations

import asyncio
import time
import pytest


def test_rf592_registry_module_state_exists():
    """The registry module-level dict + helper functions must be present."""
    from aria_service.routes import aria
    assert hasattr(aria, "_ADV_RUN_REGISTRY")
    assert isinstance(aria._ADV_RUN_REGISTRY, dict)
    assert hasattr(aria, "_adv_run_background")
    assert hasattr(aria, "_adv_registry_cleanup")


def test_rf592_endpoints_registered():
    """All 3 new endpoints (POST run-weekly already existed; GET
    runs/{id} + GET runs are new) must be in the FastAPI router."""
    from aria_service.routes import aria
    paths = {r.path for r in aria.router.routes if hasattr(r, "path")}
    assert "/api/aria/adversarial/runs/{run_id}" in paths, (
        "R-F592 REGRESSION: GET /adversarial/runs/{run_id} not registered"
    )
    assert "/api/aria/adversarial/runs" in paths, (
        "R-F592 REGRESSION: GET /adversarial/runs not registered"
    )


def test_rf592_cleanup_evicts_stale_entries(monkeypatch):
    """Entries older than the TTL must be dropped on cleanup."""
    from aria_service.routes import aria
    aria._ADV_RUN_REGISTRY.clear()
    now = time.time()
    # Stale entry — finished 2h ago, TTL is 1h
    aria._ADV_RUN_REGISTRY["stale_run"] = {
        "run_id": "stale_run", "status": "completed",
        "queued_at": now - 7200, "finished_at": now - 7200,
    }
    # Fresh entry — finished 5 min ago
    aria._ADV_RUN_REGISTRY["fresh_run"] = {
        "run_id": "fresh_run", "status": "completed",
        "queued_at": now - 300, "finished_at": now - 300,
    }
    # In-flight — never finished
    aria._ADV_RUN_REGISTRY["live_run"] = {
        "run_id": "live_run", "status": "running",
        "queued_at": now - 60, "finished_at": None,
    }
    aria._adv_registry_cleanup()
    assert "stale_run" not in aria._ADV_RUN_REGISTRY
    assert "fresh_run" in aria._ADV_RUN_REGISTRY
    assert "live_run" in aria._ADV_RUN_REGISTRY
    aria._ADV_RUN_REGISTRY.clear()


def test_rf592_background_runner_updates_state_on_success(monkeypatch):
    """_adv_run_background must transition queued → running → completed
    and record the result."""
    from aria_service.routes import aria
    aria._ADV_RUN_REGISTRY.clear()

    fake_result = {"total_attacks": 23, "passed": 18, "base_score": 0.78}

    async def fake_run_weekly(attack_ids=None):
        # Confirm the registry shows 'running' DURING the call
        assert aria._ADV_RUN_REGISTRY["test_run"]["status"] == "running"
        return fake_result

    # R-F910 — patch run_weekly on the REAL module. _adv_run_background does
    # `from ..intel import adversarial_challenge as _ac`, which resolves to the
    # package attribute once the submodule has been imported anywhere; swapping
    # sys.modules is then bypassed (order-dependent flake — any earlier test
    # that imports adversarial_challenge made this fail). setattr on the
    # resolved module is order-independent.
    import aria_service.intel.adversarial_challenge as _real_ac
    monkeypatch.setattr(_real_ac, "run_weekly", fake_run_weekly)

    aria._ADV_RUN_REGISTRY["test_run"] = {
        "run_id": "test_run", "status": "queued",
        "queued_at": time.time(), "attack_ids": None,
        "started_at": None, "finished_at": None,
        "result": None, "error": None,
    }
    asyncio.run(aria._adv_run_background("test_run", None))
    entry = aria._ADV_RUN_REGISTRY["test_run"]
    assert entry["status"] == "completed"
    assert entry["result"] == fake_result
    assert entry["started_at"] is not None
    assert entry["finished_at"] is not None
    assert entry["error"] is None
    aria._ADV_RUN_REGISTRY.clear()


def test_rf592_background_runner_captures_failure(monkeypatch):
    """Exceptions in the background run must be caught and recorded as
    status=failed with the error message, not propagated to crash the
    task scheduler."""
    from aria_service.routes import aria
    aria._ADV_RUN_REGISTRY.clear()

    async def fake_run_weekly_explode(attack_ids=None):
        raise RuntimeError("simulated upstream failure")

    # R-F910 — order-independent mock (see note in the success test above).
    import aria_service.intel.adversarial_challenge as _real_ac
    monkeypatch.setattr(_real_ac, "run_weekly", fake_run_weekly_explode)

    aria._ADV_RUN_REGISTRY["fail_run"] = {
        "run_id": "fail_run", "status": "queued",
        "queued_at": time.time(), "attack_ids": None,
        "started_at": None, "finished_at": None,
        "result": None, "error": None,
    }
    asyncio.run(aria._adv_run_background("fail_run", None))
    entry = aria._ADV_RUN_REGISTRY["fail_run"]
    assert entry["status"] == "failed"
    assert "simulated upstream failure" in entry["error"]
    assert entry["finished_at"] is not None
    aria._ADV_RUN_REGISTRY.clear()


def test_rf592_run_body_sync_field_defaults_false():
    """The new `sync` field on _AdversarialRunBody must default to
    False so the new background path is the default behaviour."""
    from aria_service.routes.aria import _AdversarialRunBody
    body = _AdversarialRunBody()
    assert hasattr(body, "sync")
    assert body.sync is False
    # Explicit True still works
    body_sync = _AdversarialRunBody(sync=True)
    assert body_sync.sync is True

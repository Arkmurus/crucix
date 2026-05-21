"""R-F774 — true dry-run for the autonomous engine + visibility ledger.

Closes the bug where _deliver_intel_ledger unconditionally wrote signals
regardless of ARIA_AUTONOMOUS_DRY_RUN, contradicting the engine spec docs
(engine.py:39-41, tasks.yaml:26-29). Adds dryrun_history capture so the
operator can audit every would-be delivery via
GET /api/aria/autonomous/dryrun/recent.

Contract pinned by this test:
  1. In dry-run, deliver() returns 'skipped:dry_run' for EVERY channel
     including intel_ledger and pipeline auto-lead.
  2. In dry-run, intel_ledger.add_signal is NEVER called.
  3. In dry-run, deal_pipeline.auto_create_from_signal is NEVER called.
  4. In dry-run, dryrun_history.recent() returns one entry per channel
     plus a 'pipeline' entry — the operator's audit window.
  5. With dry-run OFF, the real delivery path still works (regression
     guard so the dry-run wrapping doesn't break live mode).
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from unittest import mock

import pytest


@dataclass
class _FakeTask:
    id: str = "TEST-DRYRUN"
    name: str = "Test dry-run task"
    delivery_channels: list = field(default_factory=lambda: ["mem0", "intel_ledger", "whatsapp"])
    whatsapp_group_id: str = "12345@g.us"
    mem0_tags: list = field(default_factory=lambda: ["test"])


def _set_dry_run(on: bool):
    os.environ["ARIA_AUTONOMOUS_DRY_RUN"] = "1" if on else "0"


def test_rf774_dry_run_skips_every_channel():
    """In dry-run, every channel must report skipped:dry_run."""
    from aria_service.autonomous import delivery
    _set_dry_run(True)

    task = _FakeTask()

    async def run():
        return await delivery.deliver(
            task=task,
            response_text="Live test response — should NOT reach any real channel.",
            triggered_flags=["test"],
            session_id="autonomous:test:dryrun",
        )

    result = asyncio.run(run())
    assert result.get("mem0") == "skipped:dry_run", f"mem0 leaked through: {result}"
    assert result.get("intel_ledger") == "skipped:dry_run", f"intel_ledger leaked: {result}"
    assert result.get("whatsapp") == "skipped:dry_run", f"whatsapp leaked: {result}"
    assert result.get("pipeline") == "skipped:dry_run", f"pipeline leaked: {result}"


def test_rf774_dry_run_blocks_intel_ledger_write():
    """In dry-run, no real intel_ledger.add_signal call may fire."""
    from aria_service.autonomous import delivery
    _set_dry_run(True)

    task = _FakeTask(delivery_channels=["intel_ledger"])

    with mock.patch("aria_service.intel.intel_ledger.add_signal") as patched:
        async def run():
            return await delivery.deliver(
                task=task,
                response_text="dry-run body",
                triggered_flags=[],
                session_id="autonomous:test:dryrun",
            )
        asyncio.run(run())
        patched.assert_not_called()


def test_rf774_dry_run_blocks_pipeline_auto_create():
    """In dry-run, deal_pipeline.auto_create_from_signal must not fire."""
    from aria_service.autonomous import delivery
    _set_dry_run(True)

    task = _FakeTask(delivery_channels=["mem0"])

    with mock.patch("aria_service.intel.deal_pipeline.auto_create_from_signal") as patched:
        async def run():
            return await delivery.deliver(
                task=task,
                response_text="dry-run body",
                triggered_flags=["new tender"],
                session_id="autonomous:test:dryrun",
            )
        asyncio.run(run())
        patched.assert_not_called()


def test_rf774_dry_run_records_visibility_entries():
    """Every dry-run channel attempt must be captured in dryrun_history
    so the operator can audit what the brain decided to do."""
    from aria_service.autonomous import delivery, dryrun_history
    _set_dry_run(True)

    captured: list[dict] = []

    async def fake_record(**kw):
        captured.append(kw)

    task = _FakeTask(delivery_channels=["mem0", "intel_ledger"])

    with mock.patch.object(dryrun_history, "record", side_effect=fake_record):
        async def run():
            return await delivery.deliver(
                task=task,
                response_text="Captured payload that should never reach a real channel.",
                triggered_flags=["new tender", "FAA"],
                session_id="autonomous:test:dryrun",
            )
        asyncio.run(run())

    channels_recorded = sorted(c["channel"] for c in captured)
    assert "mem0" in channels_recorded, f"mem0 not captured: {channels_recorded}"
    assert "intel_ledger" in channels_recorded, f"intel_ledger not captured: {channels_recorded}"
    assert "pipeline" in channels_recorded, f"pipeline not captured: {channels_recorded}"
    for entry in captured:
        assert entry["task_id"] == "TEST-DRYRUN"
        assert entry["would_deliver"] is True
        assert entry["reason"] == "dry_run_active"
        assert "new tender" in entry["triggered_flags"]


def test_rf774_live_mode_still_calls_real_intel_ledger():
    """Regression guard: with dry-run OFF, the real delivery path must
    still run. Without this test the dry-run wrapper could silently
    short-circuit live mode too."""
    from aria_service.autonomous import delivery
    _set_dry_run(False)

    task = _FakeTask(delivery_channels=["intel_ledger"])

    with mock.patch(
        "aria_service.autonomous.delivery._deliver_intel_ledger",
        new=mock.AsyncMock(return_value="ok"),
    ) as patched:
        async def run():
            return await delivery.deliver(
                task=task,
                response_text="live body",
                triggered_flags=[],
                session_id="autonomous:test:live",
            )
        result = asyncio.run(run())
        patched.assert_called_once()
        assert result.get("intel_ledger") == "ok"


def test_rf774_is_dry_run_reads_env_fresh_each_call():
    """The _is_dry_run() helper must re-read the env var on every call
    so a runtime flip via flyctl secrets is honored without redeploy.
    A previous module-level constant required restart to flip."""
    from aria_service.autonomous.delivery import _is_dry_run

    _set_dry_run(True)
    assert _is_dry_run() is True
    _set_dry_run(False)
    assert _is_dry_run() is False
    _set_dry_run(True)
    assert _is_dry_run() is True

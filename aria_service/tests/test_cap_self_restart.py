"""R-F1146 — Capability test for self-restart & blackout recovery.

Tests the full cycle:
  1. Save a checkpoint
  2. Load the latest checkpoint
  3. Tick heartbeat and detect blackout
  4. Trigger self-restart
  5. Replay actions
  6. List checkpoints
  7. Cleanup
"""
import asyncio
import json
import os
import sys
import tempfile
import time

# Point to the repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest


@pytest.fixture
def checkpoint_dir(tmp_path):
    """Use a temp dir for checkpoints so tests don't pollute real data."""
    old = os.environ.get("ARIA_CHECKPOINT_DIR")
    os.environ["ARIA_CHECKPOINT_DIR"] = str(tmp_path)
    yield str(tmp_path)
    if old:
        os.environ["ARIA_CHECKPOINT_DIR"] = old
    else:
        os.environ.pop("ARIA_CHECKPOINT_DIR", None)


@pytest.mark.asyncio
async def test_save_and_load_checkpoint(checkpoint_dir):
    """Save a checkpoint and load it back — the core contract."""
    from aria_service.intel.self_restart import save_checkpoint, load_latest_checkpoint

    # Save
    result = await save_checkpoint(
        agent_id="test_agent",
        current_task="Testing checkpoint save/load",
        error_context="",
    )
    assert result["success"] is True
    assert "checkpoint" in result

    # Load
    cp = await load_latest_checkpoint("test_agent")
    assert cp is not None
    assert cp["agent_id"] == "test_agent"
    assert cp["current_task"] == "Testing checkpoint save/load"
    assert cp["blackout_detected"] is False


@pytest.mark.asyncio
async def test_checkpoint_with_actions(checkpoint_dir):
    """Checkpoint with a replay buffer of actions."""
    from aria_service.intel.self_restart import (
        save_checkpoint, load_latest_checkpoint, AgentAction,
    )

    actions = [
        AgentAction(
            timestamp=time.time(),
            action_type="fix_gap",
            summary="Fixed neural_memory.py",
            result="success",
        ),
        AgentAction(
            timestamp=time.time(),
            action_type="deploy",
            summary="Deployed to fly.io",
            result="success",
        ),
    ]

    result = await save_checkpoint(
        agent_id="test_agent",
        current_task="Testing with actions",
        last_actions=actions,
        error_context="",
    )
    assert result["success"] is True

    cp = await load_latest_checkpoint("test_agent")
    assert cp is not None
    assert len(cp["last_actions"]) == 2
    assert cp["last_actions"][0]["action_type"] == "fix_gap"
    assert cp["last_actions"][1]["action_type"] == "deploy"


@pytest.mark.asyncio
async def test_blackout_checkpoint(checkpoint_dir):
    """Save a checkpoint with blackout_detected=True."""
    from aria_service.intel.self_restart import save_checkpoint, load_latest_checkpoint

    result = await save_checkpoint(
        agent_id="test_agent",
        current_task="Recovering from blackout",
        error_context="Heartbeat stale for 312.5s",
        blackout_detected=True,
    )
    assert result["success"] is True

    cp = await load_latest_checkpoint("test_agent")
    assert cp is not None
    assert cp["blackout_detected"] is True
    assert "312.5s" in cp["error_context"]


@pytest.mark.asyncio
async def test_heartbeat_and_blackout_detection(checkpoint_dir):
    """Tick heartbeat and verify the blackout detector sees it."""
    from aria_service.intel.self_restart import (
        tick_heartbeat, get_heartbeat_age, get_blackout_status,
    )

    # Initially no heartbeat — age should be infinite
    age = get_heartbeat_age("test_agent_heartbeat")
    assert age == float("inf"), f"Expected inf, got {age}"

    # Tick the heartbeat
    tick_heartbeat("test_agent_heartbeat")
    age = get_heartbeat_age("test_agent_heartbeat")
    assert age < 1.0, f"Heartbeat should be fresh, got {age}s"

    # Status should show the agent
    status = get_blackout_status()
    assert "test_agent_heartbeat" in status["agents"]
    assert status["agents"]["test_agent_heartbeat"]["heartbeat_age_s"] < 1.0


@pytest.mark.asyncio
async def test_trigger_self_restart(checkpoint_dir):
    """Trigger a self-restart and verify recovery context."""
    from aria_service.intel.self_restart import (
        save_checkpoint, trigger_self_restart, get_blackout_status,
    )

    # First save a checkpoint
    await save_checkpoint(
        agent_id="test_agent",
        current_task="Before restart",
        error_context="",
    )

    # Trigger restart
    result = await trigger_self_restart("test_agent")
    assert result["success"] is True
    assert result["recovery_count"] >= 1
    assert "checkpoint" in result
    assert "replay_actions" in result

    # Recovery count should be visible in status
    status = get_blackout_status()
    assert "test_agent" in status["agents"]
    assert status["agents"]["test_agent"]["recovery_count"] >= 1


@pytest.mark.asyncio
async def test_replay_actions(checkpoint_dir):
    """Replay actions after a restart."""
    from aria_service.intel.self_restart import replay_actions

    actions = [
        {"action_type": "fix_gap", "summary": "Fixed bug", "result": "success"},
        {"action_type": "deploy", "summary": "Deployed", "result": "success"},
    ]

    replayed = await replay_actions(actions)
    assert len(replayed) == 2
    for action in replayed:
        assert action["replayed"] is True
        assert "replay_timestamp" in action


@pytest.mark.asyncio
async def test_list_checkpoints(checkpoint_dir):
    """List checkpoints for an agent."""
    from aria_service.intel.self_restart import save_checkpoint, list_checkpoints

    # Save a couple of checkpoints with a small delay so timestamps differ
    await save_checkpoint(agent_id="test_agent", current_task="Task 1")
    await asyncio.sleep(0.01)  # ensure different timestamp
    await save_checkpoint(agent_id="test_agent", current_task="Task 2")

    checkpoints = await list_checkpoints("test_agent", limit=10)
    assert len(checkpoints) >= 2, f"Expected >=2, got {len(checkpoints)}: {checkpoints}"
    # Most recent first
    assert checkpoints[0]["current_task"] == "Task 2"


@pytest.mark.asyncio
async def test_cleanup_agent(checkpoint_dir):
    """Clean up all state for an agent."""
    from aria_service.intel.self_restart import (
        save_checkpoint, cleanup_agent, load_latest_checkpoint, get_blackout_status,
    )

    # Save a checkpoint
    await save_checkpoint(agent_id="test_agent", current_task="Cleanup test")

    # Verify it exists
    cp = await load_latest_checkpoint("test_agent")
    assert cp is not None

    # Cleanup
    await cleanup_agent("test_agent")

    # Verify it's gone
    cp = await load_latest_checkpoint("test_agent")
    assert cp is None

    # Status should not include the agent
    status = get_blackout_status()
    assert "test_agent" not in status["agents"]


@pytest.mark.asyncio
async def test_no_checkpoint_returns_none(checkpoint_dir):
    """Loading a checkpoint for an agent that never saved one returns None."""
    from aria_service.intel.self_restart import load_latest_checkpoint

    cp = await load_latest_checkpoint("nonexistent_agent")
    assert cp is None


@pytest.mark.asyncio
async def test_checkpoint_with_error_context(checkpoint_dir):
    """Checkpoint with error context is preserved."""
    from aria_service.intel.self_restart import save_checkpoint, load_latest_checkpoint

    error = "RuntimeError: Connection refused to redis at localhost:6379"
    await save_checkpoint(
        agent_id="test_agent",
        current_task="Error recovery",
        error_context=error,
    )

    cp = await load_latest_checkpoint("test_agent")
    assert cp is not None
    assert "Connection refused" in cp["error_context"]

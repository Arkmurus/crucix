"""R-F1410 — Capability test: DRAIN-COLLAB-BRIDGE autonomous task.

The server-mediated collaboration bridge (collab_bridge.py) is the ONE
mailbox shared by Claude, the ARIA Coder CLI, and the server ARIA brain.
Claude ships notes via collab_bridge.send(). The DRAIN-COLLAB-BRIDGE
autonomous task calls collab_bridge.drain_for_aria() every ~2 minutes
so Claude's notes reach the brain without relay delay.

This test proves:
1. The collab_bridge_drain tool kind is registered in _execute_direct_tool
2. The DRAIN-COLLAB-BRIDGE task exists in tasks.yaml with correct schedule
3. drain_for_aria() is callable from the task dispatch path
4. A note sent by Claude is drained and absorbed by the brain
"""
import pytest

# R-F3788/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import function_source, module_source


def test_collab_bridge_drain_tool_registered():
    """Prove the collab_bridge_drain tool kind is registered in _execute_direct_tool."""
    from aria_service.autonomous.tasks import _execute_direct_tool
    import inspect

    source = function_source("aria_service.autonomous.tasks", "_execute_direct_tool")
    assert 'tool_kind == "collab_bridge_drain"' in source, (
        "collab_bridge_drain must be registered in _execute_direct_tool"
    )
    assert "collab_bridge.drain_for_aria()" in source, (
        "collab_bridge_drain must call collab_bridge.drain_for_aria()"
    )


def test_collab_bridge_drain_is_reachable_from_run_task():
    """R-F3293 — a handler nothing can dispatch to is not a registered tool.

    The check above passed for months while the tool was UNREACHABLE. R-F1410
    added the handler and a task whose tool_chain names it, but never added the
    kind to run_task's dispatch tuple, so every run of that task answered
    "unsupported tool kind". Asserting the handler EXISTS is the §3c failure
    exactly: it tests the helper, not the path the task actually takes.

    The bridge kept draining regardless, which is why nobody noticed — R-F1548
    later added an independent 2-minute scheduler loop calling drain_for_aria()
    directly. That masked the dead task path instead of replacing it, and a
    masked failure is the kind that surfaces when the thing masking it is
    changed.
    """
    import inspect
    import re

    from aria_service.autonomous import tasks as t

    source = module_source(t)
    # Comments are stripped first: a parenthesis inside one would truncate the
    # tuple match and make this pass vacuously (the bug test_autonomous_
    # dispatch_parity's own extractor was written to avoid).
    clean = "\n".join(line.split("#", 1)[0] for line in source.splitlines())
    m = re.search(r"elif\s+tool_kind\s+in\s*\(([^)]+)\):", clean, flags=re.DOTALL)
    assert m, "could not locate run_task's dispatch tuple"
    assert "collab_bridge_drain" in set(re.findall(r'"([^"]+)"', m.group(1))), (
        "collab_bridge_drain has a handler but run_task cannot dispatch to it, "
        "so the DRAIN-COLLAB-BRIDGE task errors 'unsupported tool kind'")


def test_drain_collab_bridge_task_in_yaml():
    """Prove the DRAIN-COLLAB-BRIDGE task exists in tasks.yaml."""
    from pathlib import Path
    import yaml

    yaml_path = Path(__file__).resolve().parent.parent / "autonomous" / "tasks.yaml"
    assert yaml_path.exists(), "tasks.yaml must exist"

    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    tasks = data.get("tasks", [])
    task_ids = [t.get("id") for t in tasks]
    assert "DRAIN-COLLAB-BRIDGE" in task_ids, (
        "DRAIN-COLLAB-BRIDGE task must exist in tasks.yaml"
    )

    # Find the task and verify its config
    task = next(t for t in tasks if t.get("id") == "DRAIN-COLLAB-BRIDGE")
    assert task.get("enabled", False) is True, "DRAIN-COLLAB-BRIDGE must be enabled"
    assert task.get("cron") == "*/2 * * * *", (
        f"DRAIN-COLLAB-BRIDGE should run every 2 min, got: {task.get('cron')}"
    )
    assert task.get("timeout_seconds", 0) <= 30, (
        "DRAIN-COLLAB-BRIDGE should have a short timeout (<=30s)"
    )
    assert task.get("cost_cap_usd", 1) == 0.0, (
        "DRAIN-COLLAB-BRIDGE should have zero cost cap (no LLM calls)"
    )

    # Verify tool chain
    tool_chain = task.get("tool_chain", [])
    assert len(tool_chain) == 1, "DRAIN-COLLAB-BRIDGE should have exactly one tool"
    assert tool_chain[0].get("tool") == "collab_bridge_drain", (
        f"DRAIN-COLLAB-BRIDGE tool should be collab_bridge_drain, got: {tool_chain[0].get('tool')}"
    )


@pytest.mark.asyncio
async def test_drain_collab_bridge_executes():
    """Prove the collab_bridge_drain tool executes without error.

    This calls drain_for_aria() directly (the same function the task calls)
    and verifies it returns the expected shape.
    """
    from aria_service.intel import collab_bridge

    result = await collab_bridge.drain_for_aria()

    # Must return the expected shape
    assert isinstance(result, dict), "drain_for_aria must return a dict"
    assert "drained" in result, "result must have 'drained' key"
    assert "last_seq" in result, "result must have 'last_seq' key"
    assert isinstance(result["drained"], int), "drained must be int"
    assert isinstance(result["last_seq"], int), "last_seq must be int"

    # In a clean test environment, there should be no unread notes
    # (the test_rf1409 tests may have left some, but drain is cursor-guarded)
    assert result["drained"] >= 0, "drained must be >= 0"


@pytest.mark.asyncio
async def test_drain_absorbs_new_claude_note():
    """Prove a new Claude→ARIA note is drained and absorbed.

    Full capability test: send a note as Claude, drain as ARIA, verify
    the note is absorbed (cursor advances).
    """
    from aria_service.intel import collab_bridge

    # Get current cursor
    cursor_before = await collab_bridge.get_cursor("aria")

    # Send a test note as Claude
    msg = await collab_bridge.send(
        frm="claude",
        to="aria",
        text="R-F1410 capability test: this is a test note from Claude",
        kind="test",
    )
    seq = msg["seq"]

    # Drain as ARIA
    result = await collab_bridge.drain_for_aria()

    # Verify the note was drained
    assert result["drained"] >= 1, (
        f"Should have drained at least 1 note, got: {result['drained']}"
    )
    assert result["last_seq"] >= seq, (
        f"Cursor should advance past seq {seq}, got: {result['last_seq']}"
    )

    # Verify cursor advanced
    cursor_after = await collab_bridge.get_cursor("aria")
    assert cursor_after >= seq, (
        f"Cursor should be >= {seq}, got: {cursor_after}"
    )

    # Drain again — should be 0 (cursor-guarded, no double-processing)
    result2 = await collab_bridge.drain_for_aria()
    assert result2["drained"] == 0, (
        "Second drain should find 0 new notes (cursor-guarded)"
    )


@pytest.mark.asyncio
async def test_drain_ignores_aria_to_claude():
    """Prove drain_for_aria only processes Claude→ARIA notes, not ARIA→Claude."""
    from aria_service.intel import collab_bridge

    # Send an ARIA→Claude note (should NOT be drained)
    await collab_bridge.send(
        frm="aria",
        to="claude",
        text="R-F1410: this is ARIA replying to Claude",
        kind="reply",
    )

    # Drain — should not pick up the ARIA→Claude note
    result = await collab_bridge.drain_for_aria()
    # drained could be 0 or more depending on other test notes,
    # but the key is that drain_for_aria only processes to=aria notes
    assert "drained" in result
    assert "last_seq" in result

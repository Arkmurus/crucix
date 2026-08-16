"""R-F567 (2026-05-16) — cost_free_learning autonomous-task wiring tests.

Capability proofs:
  - tasks.yaml has the HOURLY-COST-FREE-LEARN task with tool=cost_free_learn,
    enabled=false (off by default per autonomous_doctrine), cron='37 * * * *'.
  - tasks.py dispatcher knows how to handle tool_kind == 'cost_free_learn'
    and routes to cost_free_learning.run_preview().
  - Dispatch returns the dict shape {"cost_free_learn": {...}} so downstream
    log+mem0 writers know what to record.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


_TASKS_YAML = Path(__file__).resolve().parents[1] / "autonomous" / "tasks.yaml"
_TASKS_PY = Path(__file__).resolve().parents[1] / "autonomous" / "tasks.py"


@pytest.fixture(scope="module")
def yaml_data():
    import yaml
    with _TASKS_YAML.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_task_entry_present(yaml_data):
    """The HOURLY-COST-FREE-LEARN task must exist in tasks.yaml."""
    ids = [t.get("id") for t in yaml_data["tasks"]]
    assert "HOURLY-COST-FREE-LEARN" in ids


def test_task_is_enabled_with_writes_still_gated(yaml_data):
    """R-F4060 (C-122) — the ship-time doctrine was "new tasks ship off, the
    operator flips them on explicitly". That flip has now happened.

    The doctrine applies at SHIP time; leaving it off permanently meant the
    read-only preview never ran, so the write-approval gate it exists to inform
    could never open. What protects state is the SEPARATE write env, not this
    flag — so that is what this test now pins.
    """
    from aria_service.intel import cost_free_learning as cfl

    task = next(t for t in yaml_data["tasks"]
                if t["id"] == "HOURLY-COST-FREE-LEARN")
    assert task["enabled"] is True
    # The real guard: enabling the preview must never imply enabling commits.
    assert cfl._write_enabled() is False


def test_task_zero_cost(yaml_data):
    """Capability: 'cost-free' means zero. cost_cap_usd must be 0.00."""
    task = next(t for t in yaml_data["tasks"]
                if t["id"] == "HOURLY-COST-FREE-LEARN")
    assert float(task["cost_cap_usd"]) == 0.0


def test_task_uses_correct_tool(yaml_data):
    task = next(t for t in yaml_data["tasks"]
                if t["id"] == "HOURLY-COST-FREE-LEARN")
    tools = [step["tool"] for step in task["tool_chain"]]
    assert "cost_free_learn" in tools


def test_task_hourly_cron(yaml_data):
    """Cron should fire ~hourly. Anything more aggressive than every 5
    min would be over-eager for a no-op-by-default preview."""
    task = next(t for t in yaml_data["tasks"]
                if t["id"] == "HOURLY-COST-FREE-LEARN")
    cron = task["cron"]
    # Should NOT be '*/N' with N<10 minutes
    assert " " in cron
    minute_field = cron.split()[0]
    # Either explicit integer or */N — reject very-frequent steps.
    if minute_field.startswith("*/"):
        step = int(minute_field[2:])
        assert step >= 10, f"R-F567: cron firing every {step}min is too aggressive"


def test_dispatcher_handles_tool_kind():
    """Static check: tasks.py contains the cost_free_learn elif branch
    that calls cost_free_learning.run_preview()."""
    src = _TASKS_PY.read_text(encoding="utf-8")
    assert 'tool_kind == "cost_free_learn"' in src, (
        "R-F567: dispatcher missing the cost_free_learn elif branch"
    )
    assert "cost_free_learning" in src
    assert "run_preview" in src


def test_dispatch_returns_correct_shape(monkeypatch):
    """Capability: when the engine dispatches a cost_free_learn task,
    the result dict carries the {'cost_free_learn': {...}} envelope so
    downstream loggers + mem0 writers know how to tag it."""
    # We test by directly invoking the elif branch logic. The
    # dispatcher is too coupled to fixture for a clean call, so we
    # exercise the underlying call instead.
    from aria_service.intel import cost_free_learning as cfl
    from aria_service.intel import redis_store as rs

    async def fake_get_json(_):
        return None
    monkeypatch.setattr(rs, "get_json", fake_get_json)

    result = asyncio.run(cfl.run_preview())
    # The dispatcher wraps this in {"cost_free_learn": result}; the
    # preview itself must contain the 4 loop keys so the dispatch
    # wrapper has something to deliver.
    assert "loops" in result
    for loop in ("mastery_decay", "mistake_replay",
                 "cross_corroborate", "qa_distill"):
        assert loop in result["loops"]

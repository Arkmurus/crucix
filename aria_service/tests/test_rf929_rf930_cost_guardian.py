"""R-F929 + R-F930 — re-enabled daily eval + ARIA's cost self-guardian.

R-F929: RUN-EVAL-DAILY (golden-set regression eval) re-enabled. It was disabled
(R-F650) after a $12.76/2h Sonnet runaway; both blockers are now resolved
(R-F651 enforces the 600s timeout; Anthropic declined → DeepSeek-only chain).

R-F930: a cost_guard autonomous tool + hourly RUN-COST-GUARD task. ARIA reads
her month-to-date LLM spend, feeds it to her brain (self_monitor), and PAUSES
the autonomous engine at >=90% of the $300 cap — BEFORE the hard cap would
block user-facing chat. Pure read + guard, no LLM call.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

yaml = pytest.importorskip("yaml")

_TASKS_YAML = Path(__file__).resolve().parents[1] / "autonomous" / "tasks.yaml"


def _tasks():
    d = yaml.safe_load(_TASKS_YAML.read_text(encoding="utf-8"))
    items = d["tasks"] if isinstance(d, dict) and "tasks" in d else d
    return {t["id"]: t for t in items}


# ── R-F929 — eval re-enabled ─────────────────────────────────────────────────

def test_rf929_run_eval_daily_enabled():
    t = _tasks()["RUN-EVAL-DAILY"]
    assert t["enabled"] is True, "RUN-EVAL-DAILY must be re-enabled (R-F929)"
    assert t["timeout_seconds"] == 600, "keep the R-F651 timeout brake"
    assert t["tool_chain"][0]["tool"] == "run_eval"


# ── R-F930 — guardian task wired ─────────────────────────────────────────────

def test_rf930_cost_guard_task_present_and_hourly_and_free():
    t = _tasks()["RUN-COST-GUARD"]
    assert t["enabled"] is True
    assert t["tool_chain"][0]["tool"] == "cost_guard"
    assert t["cost_cap_usd"] == 0.0, "guardian must make no LLM call"
    # hourly cron (minute field set, hour wildcard)
    assert t["cron"].split()[1] == "*", f"expected hourly, got {t['cron']!r}"


# ── R-F930 — guardian threshold logic ────────────────────────────────────────

def _run_guard(monkeypatch, util_pct, *, autopause="1", already_paused=False):
    """Invoke the cost_guard tool with a stubbed spend reading; capture whether
    pause_engine fired and what brain self-event was emitted."""
    from aria_service.autonomous import tasks as _tasks_mod
    from aria_service.intel import cost_tracker as _ct
    from aria_service.autonomous import safety as _sf
    from aria_service.intel import brain_hook as _bh

    captured = {"paused": False, "event": None, "success": None}

    async def _fake_spend():
        return {"month": "2026-05", "spent_usd": util_pct * 3.0, "cap_usd": 300.0,
                "remaining_usd": 300.0 - util_pct * 3.0, "utilisation_pct": util_pct,
                "warn_only": False}

    async def _fake_is_paused():
        return already_paused

    async def _fake_pause(reason=""):
        captured["paused"] = True

    async def _fake_observe(event, detail="", *, success=False, gap_type="self_runtime"):
        captured["event"] = event
        captured["success"] = success
        return {}

    monkeypatch.setenv("ARIA_COST_GUARD_AUTOPAUSE", autopause)
    monkeypatch.setattr(_ct, "get_month_spend", _fake_spend)
    monkeypatch.setattr(_sf, "is_engine_paused", _fake_is_paused)
    monkeypatch.setattr(_sf, "pause_engine", _fake_pause)
    monkeypatch.setattr(_bh, "observe_self_event", _fake_observe)

    task = SimpleNamespace(tool_chain=[{"tool": "cost_guard", "label": "hourly"}],
                           timeout_seconds=60, cost_cap_usd=0.0)
    result = asyncio.run(_tasks_mod._execute_direct_tool("cost_guard", task, None))
    return result["cost_guard"], captured


def test_rf930_healthy_burn_no_pause_heartbeat(monkeypatch):
    res, cap = _run_guard(monkeypatch, util_pct=40.0)
    assert res["action"] == "ok"
    assert cap["paused"] is False
    assert cap["event"] == "cost_status"
    assert cap["success"] is True   # healthy heartbeat, not a gap


def test_rf930_warn_band_signals_but_does_not_pause(monkeypatch):
    res, cap = _run_guard(monkeypatch, util_pct=75.0)
    assert res["action"] == "warn"
    assert cap["paused"] is False
    assert cap["success"] is False  # recorded as a capability gap


def test_rf930_over_90_pauses_engine(monkeypatch):
    res, cap = _run_guard(monkeypatch, util_pct=92.0)
    assert res["action"] == "paused"
    assert cap["paused"] is True, "guardian must pause autonomous engine at >=90%"
    assert cap["success"] is False


def test_rf930_autopause_off_warns_without_pausing(monkeypatch):
    res, cap = _run_guard(monkeypatch, util_pct=95.0, autopause="0")
    assert res["action"] == "over_threshold_no_pause"
    assert cap["paused"] is False


def test_rf930_already_paused_does_not_double_pause(monkeypatch):
    res, cap = _run_guard(monkeypatch, util_pct=95.0, already_paused=True)
    # is_engine_paused True → don't call pause_engine again
    assert cap["paused"] is False

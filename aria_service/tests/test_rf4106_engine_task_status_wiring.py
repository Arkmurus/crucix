"""R-F4106 (C-151) — CAPABILITY: a task that FAILED must not wire success.

Found while working C-149. `engine.py` calls

    await tasks_mod.execute_task(task=task, llm=llm, dry_run=is_dry_run())

and **discards the return value**, then unconditionally wires

    wire_success(module="autonomous_engine", summary=f"Task fired: {task_id}")

`execute_task` returns a record whose `status` is one of
`ok | error | timeout | blocked_by_predictor | started`, and it contains **zero
`wire_failure` calls** of its own — verified by walking its AST. So an
autonomous task that raised, or that timed out, produced a brain SUCCESS signal
and no failure signal at all.

That is §21a inverted: the failure branch does not merely fail to reach the
brain, it reaches it wearing a success. §25a requires ARIA to KNOW whether the
intended result was produced; here every task reported that it was.

R-F2706 already fixed the neighbouring half (per-channel DELIVERY outcomes),
which is why this looked covered. It is not: `_wire_task_delivery_outcomes`
runs only on the success path and reports delivery, never execution status.

`blocked_by_predictor` is deliberately NOT a failure — the predictor chose not
to run the task, and §14 says a deliberate skip is not a fault. It must also
not be a plain success, or "we skipped it" and "it worked" become the same
reading.

Run: python -m pytest aria_service/tests/test_rf4106_engine_task_status_wiring.py -v
"""
from __future__ import annotations

import pytest


def _wired(monkeypatch, record):
    """Drive the real wiring decision and capture what reached the brain."""
    from aria_service.autonomous import engine
    import aria_service.intel.engine_wiring as ew

    seen: list[tuple[str, dict]] = []
    monkeypatch.setattr(ew, "wire_success",
                        lambda **kw: seen.append(("success", kw)), raising=False)
    monkeypatch.setattr(ew, "wire_failure",
                        lambda **kw: seen.append(("failure", kw)), raising=False)

    class _T:
        id = "MONITOR-POLYMARKET-GEO"
        cron = "30 */6 * * *"

    engine._wire_task_result("MONITOR-POLYMARKET-GEO", _T(), record)
    return seen


# ══════════════════════════════════════════════════════════════════════
# THE DEFECT — failure must not be reported as success
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("status", ["error", "timeout"])
def test_a_failed_task_wires_failure_not_success(monkeypatch, status):
    seen = _wired(monkeypatch, {"status": status, "error": "boom"})

    kinds = [k for k, _ in seen]
    assert "success" not in kinds, (
        f"a task with status={status!r} reached the brain as a SUCCESS. "
        f"§21a is not merely unmet here — the failure branch is actively "
        f"reporting the opposite of what happened."
    )
    assert "failure" in kinds


def test_the_failure_carries_the_reason(monkeypatch):
    seen = _wired(monkeypatch, {"status": "error", "error": "ValueError: no data"})
    detail = str([kw for k, kw in seen if k == "failure"])
    assert "ValueError" in detail or "no data" in detail, (
        "a failure signal that does not say WHY cannot drive a self-heal"
    )


# ══════════════════════════════════════════════════════════════════════
# THE DISTINCTIONS — three outcomes, three readings
# ══════════════════════════════════════════════════════════════════════

def test_a_successful_task_still_wires_success(monkeypatch):
    seen = _wired(monkeypatch, {"status": "ok"})
    assert [k for k, _ in seen] == ["success"]


def test_a_deliberate_skip_is_neither_a_failure_nor_a_plain_success(monkeypatch):
    """§14: cooling/skipping is not broken. But it must not read identically to
    'the task ran and worked'."""
    ok = _wired(monkeypatch, {"status": "ok"})
    skipped = _wired(monkeypatch, {"status": "blocked_by_predictor"})

    assert "failure" not in [k for k, _ in skipped], (
        "the predictor choosing not to run a task is not a fault"
    )
    assert str(ok) != str(skipped), (
        "'we skipped it' and 'it worked' produced the same brain signal"
    )


def test_an_unknown_or_missing_status_is_not_certified(monkeypatch):
    """A record we cannot read must never be reported as a success — that is
    the absence-reads-as-health shape §1 records three times."""
    for rec in ({}, None, {"status": "started"}):
        seen = _wired(monkeypatch, rec)
        assert "success" not in [k for k, _ in seen], f"certified on {rec!r}"


# ══════════════════════════════════════════════════════════════════════
# THE WIRE — the engine loop must actually use the result
# ══════════════════════════════════════════════════════════════════════

def test_the_engine_no_longer_discards_the_task_result():
    from ._source_probe import function_source
    from aria_service.autonomous import engine

    src = function_source(engine, "_wire_task_result")
    assert "status" in src, "the helper must branch on the reported status"

    import io
    whole = io.open(engine.__file__, encoding="utf-8").read()
    assert "_wire_task_result(" in whole, "the helper exists but nothing calls it"
    assert "await tasks_mod.execute_task(" in whole
    # The result must be bound, not thrown away.
    assert "= await tasks_mod.execute_task(" in whole, (
        "engine still discards execute_task's return value, so it cannot know "
        "whether the task it just fired actually worked"
    )

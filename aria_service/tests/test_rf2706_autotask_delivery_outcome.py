"""R-F2706 capability test — §25a autonomous-task delivery proprioception.

Symptom: ``delivery.deliver()`` returns a per-channel result map
(``{"whatsapp": "ok:...", "intel_ledger": "error:..."}``) that ``execute_task``
stored on the run record but NEVER forwarded to ``outcome_wire``. So a WhatsApp
push (or intel-ledger write) that FAILED still left ``status=ok`` and the brain
had no proprioception signal — engine.py wired success on EXECUTION, not DELIVERY.

This drives the real ``_wire_task_delivery_outcomes`` with the actual result-map
shapes ``deliver()`` produces and asserts the right §25a outcomes land on the
dashboard-visible "autotask" surface, and that deliberate non-deliveries are not
recorded as failures.
"""
import asyncio

import pytest

import aria_service.autonomous.tasks as tasks
import aria_service.intel.outcome_wire as ow


class _T:
    id = "daily_intel_scan"


def _capture(monkeypatch):
    captured = []

    async def _fake_record(rec):
        captured.append(rec)
        return {"recorded": True}

    monkeypatch.setattr(ow, "record_outcome", _fake_record)
    return captured


def test_autotask_is_a_known_dashboard_surface():
    # R-F1969 precedent: engine-production limbs must be in KNOWN_SURFACES to roll up.
    assert "autotask" in ow.KNOWN_SURFACES


@pytest.mark.asyncio
async def test_per_channel_outcomes_are_recorded(monkeypatch):
    captured = _capture(monkeypatch)
    # the exact shape delivery.deliver() returns on a mixed success/failure run
    delivery_result = {
        "whatsapp": "ok:sent:msgid123",
        "intel_ledger": "error:TimeoutError:store unreachable",
        "mem0": "ok:auto_via_aria_chat",
        "pipeline": "ok:lead_created:L-9",
    }
    await tasks._wire_task_delivery_outcomes(_T(), delivery_result, "sess-1", 250)
    await asyncio.sleep(0.02)

    by_channel = {r.request_id.rsplit(":", 1)[-1]: r for r in captured}
    assert set(by_channel) == {"whatsapp", "intel_ledger", "mem0", "pipeline"}
    assert all(r.surface == "autotask" for r in captured), "all roll up under one surface"
    assert by_channel["whatsapp"].actual_outcome == "delivered_real_answer"
    assert by_channel["intel_ledger"].actual_outcome == "send_failed"
    assert by_channel["mem0"].actual_outcome == "delivered_real_answer"
    assert by_channel["intel_ledger"].latency_ms == 250


@pytest.mark.asyncio
async def test_total_delivery_raise_records_send_failed(monkeypatch):
    captured = _capture(monkeypatch)
    # execute_task sets record["delivery"] = {"error": "<Type>: msg"} when deliver() raises
    await tasks._wire_task_delivery_outcomes(_T(), {"error": "RuntimeError: boom"}, "sess-2", 5)
    await asyncio.sleep(0.02)
    assert len(captured) == 1
    assert captured[0].actual_outcome == "send_failed"
    assert captured[0].surface == "autotask"
    assert "delivery" in captured[0].request_id


@pytest.mark.asyncio
async def test_deliberate_non_delivery_not_recorded_as_failure(monkeypatch):
    captured = _capture(monkeypatch)
    # dry-run string, suppressed (operating mode), and skipped are NOT failures
    await tasks._wire_task_delivery_outcomes(_T(), "dry_run_skipped", "s", 1)
    await tasks._wire_task_delivery_outcomes(
        _T(), {"whatsapp": "suppressed:operating_mode=DEGRADED", "mem0": "skipped:dry_run"}, "s", 1,
    )
    await asyncio.sleep(0.02)
    assert not captured, f"deliberate non-deliveries must not record outcomes, got {captured}"


@pytest.mark.asyncio
async def test_execute_task_calls_the_wire(monkeypatch):
    # STRUCTURAL: prove the real execute_task path invokes the wire after deliver().
    import inspect
    src = inspect.getsource(tasks.execute_task)
    assert "_wire_task_delivery_outcomes(" in src, (
        "execute_task must forward delivery outcomes to the proprioception wire"
    )

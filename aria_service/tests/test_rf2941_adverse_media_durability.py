"""R-F2941 — an interrupted adverse-media follow-up must self-heal, never hang.

The adverse-media deep search runs as a fire-and-forget asyncio task (R-F2657)
that merges into the persisted report ~180s after the DD ends. A process restart
(the peer redeploys every ~10-30min) kills that task, leaving the report stuck at
adverse_media.status='in_progress' FOREVER — the Grade-A adverse-media question
never answers, silently. Verified live 2026-07-23 on dd_32683a7d8266.

Fix: persist the follow-up params at launch, and a boot reconciler re-runs any
orphaned in_progress follow-up (or, if un-recoverable, marks it incomplete —
never a false clean).
"""
from __future__ import annotations

import asyncio
import time

import pytest

from aria_service.intel import dd_orchestrator as ddo


def _run(coro):
    return asyncio.run(coro)


class _FakeRS:
    def __init__(self, index, bodies):
        self.index = index
        self.bodies = bodies
        self.writes = {}

    async def get_json(self, key):
        if key == ddo.REPORT_INDEX_KEY:
            return self.index
        return self.bodies.get(key)

    async def set_json(self, key, val, ex=None):
        self.writes[key] = val
        self.bodies[key] = val


@pytest.fixture(autouse=True)
def _reset():
    ddo._AM_RECONCILE_INFLIGHT.clear()
    yield
    ddo._AM_RECONCILE_INFLIGHT.clear()


def _install(monkeypatch, index, bodies):
    rs = _FakeRS(index, bodies)
    # The reconciler does `from . import redis_store as rs` locally, so patch the
    # real module's methods — not a ddo attribute (which the local import shadows).
    from aria_service.intel import redis_store as real_rs
    monkeypatch.setattr(real_rs, "get_json", rs.get_json)
    monkeypatch.setattr(real_rs, "set_json", rs.set_json)
    async def _get_report(run_id):
        return bodies.get(ddo.REPORT_REDIS_KEY.format(run_id=run_id))
    monkeypatch.setattr(ddo, "get_report", _get_report)
    return rs


def _orphan_body(run_id, *, started_age_s, has_params):
    am = {
        "status": "in_progress",
        "framework_version": "R-F2657 async follow-up",
        "trigger": "GREEN-screen",
        "started_at": time.time() - started_age_s,
    }
    if has_params:
        am["_followup_params"] = {
            "entity_name": "Chemring Group PLC",
            "director_names": [], "ubo_names": [], "sectors": ["defence"],
            "trigger_reason": "GREEN-screen", "max_templates": 12,
        }
    return {"run_id": run_id, "adverse_media": am, "decision_readiness": {}}


class TestReconcile:
    def test_recoverable_orphan_is_relaunched(self, monkeypatch):
        run_id = "dd_orphan1"
        bodies = {ddo.REPORT_REDIS_KEY.format(run_id=run_id):
                  _orphan_body(run_id, started_age_s=400, has_params=True)}
        _install(monkeypatch, [{"run_id": run_id}], bodies)

        launched = {}
        async def _fake_followup(rid, **kw):
            launched["rid"] = rid
            launched["params"] = kw
        monkeypatch.setattr(ddo, "_run_adverse_media_followup", _fake_followup)

        async def _drive():
            out = await ddo.reconcile_pending_adverse_media(max_age_s=300)
            # let the create_task'd relaunch run
            await asyncio.sleep(0.05)
            return out
        out = _run(_drive())
        assert out["relaunched"] == 1
        assert launched.get("rid") == run_id
        assert launched["params"]["entity_name"] == "Chemring Group PLC"

    def test_a_still_running_followup_is_left_alone(self, monkeypatch):
        """Younger than max_age_s → a genuine follow-up may still be running."""
        run_id = "dd_young"
        bodies = {ddo.REPORT_REDIS_KEY.format(run_id=run_id):
                  _orphan_body(run_id, started_age_s=30, has_params=True)}
        _install(monkeypatch, [{"run_id": run_id}], bodies)
        monkeypatch.setattr(ddo, "_run_adverse_media_followup",
                            lambda *a, **k: pytest.fail("must not relaunch a young follow-up"))
        out = _run(ddo.reconcile_pending_adverse_media(max_age_s=300))
        assert out["relaunched"] == 0

    def test_unrecoverable_orphan_is_marked_incomplete_not_left_hanging(self, monkeypatch):
        """A pre-R-F2941 orphan has no persisted params — it must NOT stay
        in_progress (a false 'still working'); mark it incomplete honestly."""
        run_id = "dd_noparams"
        key = ddo.REPORT_REDIS_KEY.format(run_id=run_id)
        bodies = {key: _orphan_body(run_id, started_age_s=400, has_params=False)}
        rs = _install(monkeypatch, [{"run_id": run_id}], bodies)
        monkeypatch.setattr(ddo, "_refresh_persisted_decision_readiness", lambda b: b)

        _run(ddo.reconcile_pending_adverse_media(max_age_s=300))
        am = rs.bodies[key]["adverse_media"]
        assert am["status"] == "incomplete", am
        assert "interrupted" in am["error"]
        assert am["status"] != "in_progress", "must never leave it hanging"

    def test_a_completed_followup_is_ignored(self, monkeypatch):
        """A report whose adverse_media already merged (has findings) is done."""
        run_id = "dd_done"
        body = {"run_id": run_id,
                "adverse_media": {"status": "complete", "findings_count": 7, "partial": False}}
        bodies = {ddo.REPORT_REDIS_KEY.format(run_id=run_id): body}
        _install(monkeypatch, [{"run_id": run_id}], bodies)
        out = _run(ddo.reconcile_pending_adverse_media(max_age_s=300))
        assert out["scanned"] == 0 and out["relaunched"] == 0

    def test_the_same_run_is_not_relaunched_twice_concurrently(self, monkeypatch):
        run_id = "dd_dup"
        ddo._AM_RECONCILE_INFLIGHT.add(run_id)  # already being handled
        bodies = {ddo.REPORT_REDIS_KEY.format(run_id=run_id):
                  _orphan_body(run_id, started_age_s=400, has_params=True)}
        _install(monkeypatch, [{"run_id": run_id}], bodies)
        monkeypatch.setattr(ddo, "_run_adverse_media_followup",
                            lambda *a, **k: pytest.fail("must not double-launch"))
        out = _run(ddo.reconcile_pending_adverse_media(max_age_s=300))
        assert out["relaunched"] == 0

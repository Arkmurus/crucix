"""R-F2671 — C-3 v2 wiring: out-of-band independent-verification follow-up.

Proves the ROLLOUT GATE is honest end-to-end:
  - 'off' (default)  → follow-up is a no-op; the report is never touched.
  - 'measure'        → the metric IS merged into verification.independent_verification,
                       but independent_source_verification_run STAYS FALSE (R-F2413 not
                       flipped — the operator reviews live data first).
  - 'enforce'        → metric merged AND independent_source_verification_run flips True,
                       but ONLY when the re-fetch actually ran (an error never flips it).
The re-fetch itself is mocked here (it is unit-tested in test_rf2669_live_refetch);
this test locks the WIRING + the flag semantics.
"""

from __future__ import annotations

import pytest

from aria_service.intel import dd_orchestrator as orch
from aria_service.intel import dd_independent_verifier as ver


def test_mode_gate(monkeypatch) -> None:
    monkeypatch.delenv("ARIA_DD_INDEPENDENT_VERIFY", raising=False)
    assert ver.independent_verify_mode() == "off"
    monkeypatch.setenv("ARIA_DD_INDEPENDENT_VERIFY", "measure")
    assert ver.independent_verify_mode() == "measure"
    monkeypatch.setenv("ARIA_DD_INDEPENDENT_VERIFY", "1")
    assert ver.independent_verify_mode() == "measure"
    monkeypatch.setenv("ARIA_DD_INDEPENDENT_VERIFY", "enforce")
    assert ver.independent_verify_mode() == "enforce"
    monkeypatch.setenv("ARIA_DD_INDEPENDENT_VERIFY", "0")
    assert ver.independent_verify_mode() == "off"


class _FakeRS:
    def __init__(self) -> None:
        self.saved: dict = {}

    async def set_json(self, key, body, ex=None):  # noqa: ANN001
        self.saved[key] = body


def _wire_stub(monkeypatch, *, mode: str, assess_result: dict, stored: dict):
    """Route the follow-up's get_report/redis_store/assess to in-memory stubs."""
    monkeypatch.setenv("ARIA_DD_INDEPENDENT_VERIFY", mode)
    fake_rs = _FakeRS()

    async def _get_report(run_id):  # noqa: ANN001
        return stored

    async def _assess(report, *, deadline_s=60.0, fetcher=None):  # noqa: ANN001
        return assess_result

    monkeypatch.setattr(orch, "get_report", _get_report)
    monkeypatch.setattr(ver, "assess_independent_verification", _assess)
    # redis_store is imported inside the function as `from . import redis_store as rs`
    import aria_service.intel.redis_store as rs_mod
    monkeypatch.setattr(rs_mod, "set_json", fake_rs.set_json)
    return fake_rs


@pytest.mark.asyncio
async def test_off_is_a_noop(monkeypatch) -> None:
    stored = {"verification": {"independent_source_verification_run": False}}
    fake_rs = _wire_stub(monkeypatch, mode="off", assess_result={"independent_press_origins": 3}, stored=stored)
    await orch._run_independent_verification_followup("run1", ["http://a.com/x"])
    assert fake_rs.saved == {}, "off mode must never write to the report"
    assert stored["verification"]["independent_source_verification_run"] is False


@pytest.mark.asyncio
async def test_measure_merges_metric_but_flag_stays_false(monkeypatch) -> None:
    stored = {"verification": {"independent_source_verification_run": False}}
    result = {"press_items": 3, "independent_press_origins": 2, "press_independently_corroborated": True}
    fake_rs = _wire_stub(monkeypatch, mode="measure", assess_result=result, stored=stored)
    await orch._run_independent_verification_followup("run2", ["http://a.com/x", "http://b.com/y"])
    key = orch.REPORT_REDIS_KEY.format(run_id="run2")
    saved = fake_rs.saved[key]
    iv = saved["verification"]["independent_verification"]
    assert iv["independent_press_origins"] == 2 and iv["mode"] == "measure"
    # R-F2413: measure mode does NOT flip the flag
    assert saved["verification"]["independent_source_verification_run"] is False


@pytest.mark.asyncio
async def test_enforce_flips_the_flag(monkeypatch) -> None:
    stored = {"verification": {"independent_source_verification_run": False}}
    result = {"press_items": 2, "refetched_ok": 2, "independent_press_origins": 2,
              "press_independently_corroborated": True}
    fake_rs = _wire_stub(monkeypatch, mode="enforce", assess_result=result, stored=stored)
    await orch._run_independent_verification_followup("run3", ["http://a.com/x", "http://b.com/y"])
    saved = fake_rs.saved[orch.REPORT_REDIS_KEY.format(run_id="run3")]
    assert saved["verification"]["independent_source_verification_run"] is True


@pytest.mark.asyncio
async def test_enforce_does_NOT_flip_on_error(monkeypatch) -> None:
    """A failed re-fetch must NEVER be reported as an independent verification run."""
    stored = {"verification": {"independent_source_verification_run": False}}
    result = {"error": "TimeoutError: ", "press_items": 4}
    fake_rs = _wire_stub(monkeypatch, mode="enforce", assess_result=result, stored=stored)
    await orch._run_independent_verification_followup("run4", ["http://a.com/x"])
    saved = fake_rs.saved[orch.REPORT_REDIS_KEY.format(run_id="run4")]
    assert saved["verification"]["independent_source_verification_run"] is False
    assert saved["verification"]["independent_verification"]["error"]


@pytest.mark.asyncio
async def test_enforce_does_NOT_flip_when_zero_sources_refetched(monkeypatch) -> None:
    """Honesty (Pass-2 note): 'verification ran' must not certify when 0 sources were
    actually re-fetched — a total network failure with no error key must NOT flip the flag."""
    stored = {"verification": {"independent_source_verification_run": False}}
    result = {"press_items": 3, "refetched_ok": 0, "independent_press_origins": 0,
              "press_independently_corroborated": False}
    fake_rs = _wire_stub(monkeypatch, mode="enforce", assess_result=result, stored=stored)
    await orch._run_independent_verification_followup("run5", ["http://a.com/x"])
    saved = fake_rs.saved[orch.REPORT_REDIS_KEY.format(run_id="run5")]
    assert saved["verification"]["independent_source_verification_run"] is False
    # but the metric is still surfaced (measure/eval visibility)
    assert saved["verification"]["independent_verification"]["refetched_ok"] == 0


@pytest.mark.asyncio
async def test_two_followups_do_not_clobber_each_other(monkeypatch) -> None:
    """Pass-2 CONFIRMED regression fix: the adverse-media and independent-verify follow-ups
    RMW the SAME blob; serialized via _REPORT_MERGE_LOCK, neither loses the other's merge.

    Simulates interleave: both read the SAME initial blob, then run concurrently. Without
    the lock the second writer would clobber the first; with it, both fields survive.
    """
    import asyncio as _aio
    monkeypatch.setenv("ARIA_DD_INDEPENDENT_VERIFY", "measure")
    key = orch.REPORT_REDIS_KEY.format(run_id="runX")
    store = {key: {"verification": {"independent_source_verification_run": False}, "verdict": "AMBER"}}

    async def _get_report(run_id):  # noqa: ANN001
        # return a COPY so a task mutating its snapshot cannot pre-affect the store
        import copy
        return copy.deepcopy(store[key])

    saved_calls = {"n": 0}

    async def _set_json(k, body, ex=None):  # noqa: ANN001
        # yield inside the critical section to maximise interleave pressure
        await _aio.sleep(0)
        store[k] = body
        saved_calls["n"] += 1

    async def _assess(report, *, deadline_s=60.0, fetcher=None):  # noqa: ANN001
        return {"press_items": 1, "refetched_ok": 1, "independent_press_origins": 1}

    monkeypatch.setattr(orch, "get_report", _get_report)
    monkeypatch.setattr(ver, "assess_independent_verification", _assess)
    import aria_service.intel.redis_store as rs_mod
    monkeypatch.setattr(rs_mod, "set_json", _set_json)

    # adverse-media follow-up writes verification-independent field via its own path:
    async def _fake_am():
        async with orch._REPORT_MERGE_LOCK:
            b = await _get_report("runX")
            b["adverse_media"] = {"findings_count": 2}
            await _set_json(key, b)

    await _aio.gather(
        _fake_am(),
        orch._run_independent_verification_followup("runX", ["http://a.com/x"]),
    )
    final = store[key]
    # BOTH merges survive — the race is gone
    assert final.get("adverse_media", {}).get("findings_count") == 2, "adverse_media lost to race"
    assert final["verification"].get("independent_verification"), "independent_verification lost to race"
    assert final["verdict"] == "AMBER"  # untouched field preserved


def test_launch_is_noop_when_off(monkeypatch) -> None:
    monkeypatch.setenv("ARIA_DD_INDEPENDENT_VERIFY", "off")

    class _Rep:
        run_id = "r"

        class digital:  # noqa: N801
            press_coverage = [type("P", (), {"url": "http://a.com/x"})()]

    # Must not raise and must not create a task (off short-circuits before create_task).
    orch._launch_independent_verification_followup(_Rep())

"""R-F2667 — adverse-media follow-up: self-bounding deadline (partial, not lost) +
informative timeout error.

Live-DD defect (BAE Systems, dd_66208bca3d3b): the R-F2657 follow-up wrapped the
30-template adverse-media search in a single asyncio.wait_for(180s); on a high-press
entity the search exceeded 180s and was CANCELLED mid-loop → every gathered finding
lost, and the merged result was {"error": ""} (str(asyncio.TimeoutError()) is the empty
string). R-F2667: the search now takes a deadline_s and STOPS early, returning the
PARTIAL findings gathered so far; and the follow-up's backstop error is INFORMATIVE.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

import aria_service.intel.researcher as R
from aria_service.intel import dd_disciplines, dd_orchestrator as ddo


@pytest.mark.asyncio
async def test_deadline_returns_partial_findings_not_all_lost(monkeypatch) -> None:
    monkeypatch.setattr(
        dd_disciplines, "adverse_media_query_templates",
        lambda **k: [{"query": f"q{i}", "source_class": "press", "purpose": "p"}
                     for i in range(20)],
    )

    async def _slow(query, timeout=10.0):
        await asyncio.sleep(0.08)
        # R-F2745 — the hit must NAME the subject to survive the entity-relevance gate;
        # this test is about deadline-partial preservation, not off-subject filtering.
        return [{"link": f"http://x/{query}", "title": f"TestCo {query}", "snippet": "s",
                 "_credibility_tier": "tier_2"}]

    monkeypatch.setattr(R, "_web_search", _slow)

    res = await R.run_adverse_media_deep_search("TestCo", deadline_s=0.3)

    assert res["ok"] is True
    assert res["timed_out"] is True and res["partial"] is True
    assert 0 < res["templates_run"] < 20, "must STOP early, not run all 20"
    assert res["findings_count"] >= 1, "PARTIAL findings must be preserved, not lost"


@pytest.mark.asyncio
async def test_no_deadline_runs_the_full_set(monkeypatch) -> None:
    monkeypatch.setattr(
        dd_disciplines, "adverse_media_query_templates",
        lambda **k: [{"query": f"q{i}", "source_class": "press", "purpose": "p"}
                     for i in range(5)],
    )

    async def _fast(query, timeout=10.0):
        return [{"link": f"http://x/{query}", "title": query, "snippet": "s",
                 "_credibility_tier": "tier_2"}]

    monkeypatch.setattr(R, "_web_search", _fast)

    res = await R.run_adverse_media_deep_search("TestCo")  # no deadline
    assert res["timed_out"] is False and res["partial"] is False
    assert res["templates_run"] == 5


@pytest.mark.asyncio
async def test_followup_backstop_error_is_informative_never_empty(monkeypatch) -> None:
    """The exact live defect: a timeout must produce an INFORMATIVE message, never the
    empty str(TimeoutError()) that reached the stored report on BAE Systems."""
    async def _boom_timeout(**kwargs):
        raise asyncio.TimeoutError()

    written: dict[str, Any] = {}

    async def _get(run_id):
        return {"run_id": run_id, "adverse_media": {"status": "in_progress"}}

    async def _set(key, value, *a, **k):
        written["value"] = value

    from aria_service.intel import redis_store
    monkeypatch.setattr(R, "run_adverse_media_deep_search", _boom_timeout)
    monkeypatch.setattr(ddo, "get_report", _get)
    monkeypatch.setattr(redis_store, "set_json", _set)

    await ddo._run_adverse_media_followup(
        "r1", entity_name="X", director_names=[], ubo_names=[],
        sectors=["defence"], trigger_reason="RED",
    )

    am = written["value"]["adverse_media"]
    assert am["error"], "error message must be NON-empty (was '' for TimeoutError)"
    assert "timed out" in am["error"]
    assert am.get("status") == "incomplete"


@pytest.mark.asyncio
async def test_followup_passes_deadline_so_the_search_self_bounds(monkeypatch) -> None:
    """The follow-up must hand the search a deadline_s so it self-bounds (returns partial)
    instead of relying on the wait_for cancel that loses findings."""
    captured: dict[str, Any] = {}

    async def _capture(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "findings_count": 2, "partial": True, "timed_out": True}

    async def _get(run_id):
        return {"run_id": run_id, "adverse_media": {"status": "in_progress"}}

    async def _set(key, value, *a, **k):
        captured["merged"] = value["adverse_media"]

    from aria_service.intel import redis_store
    monkeypatch.setattr(R, "run_adverse_media_deep_search", _capture)
    monkeypatch.setattr(ddo, "get_report", _get)
    monkeypatch.setattr(redis_store, "set_json", _set)

    await ddo._run_adverse_media_followup(
        "r2", entity_name="X", director_names=[], ubo_names=[],
        sectors=["defence"], trigger_reason="AMBER",
    )

    assert "deadline_s" in captured and captured["deadline_s"] > 0
    assert captured["merged"]["partial"] is True  # partial result merged, not discarded

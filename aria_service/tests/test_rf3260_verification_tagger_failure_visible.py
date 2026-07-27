"""R-F3260 — a verification step that did not run must not read as one that found nothing.

`investigate()` wraps its whole fact-verification tagger in

    except Exception as _ve:
        logger.debug("verification tagger failed: %s", _ve)

at DEBUG, discarding the error. That tagger is what downgrades uncited claims,
flags in-run contradictions, and cross-checks against past verified facts. When it
throws, ALL THREE silently do not run — and the report still publishes
"Claims traced to a source 30%" and a confidence floor, i.e. traceability figures
derived from a step that never executed. Seen on the AZURE PARKING LTD DD.

Worse, `verification_summary` was already a producer with NO consumer: it is
returned by investigate() and `dd_orchestrator` never reads it, so even a recorded
failure could not reach the report. Both halves are fixed here — record it, and
carry it into the report's data gaps alongside the R-F3258 coercion and R-F3259
synthesis disclosures.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from aria_service.intel import deep_researcher as dr
from aria_service.intel import dd_orchestrator as ddo


class _FakeLLM:
    is_configured = True

    async def complete(self, *a, **k):
        class _R:
            text = '{"queries": ["acme"], "key_findings": ["x"]}'
        return _R()


def _run_with_broken_tagger():
    async def go():
        async def _search(q, *a, **k):
            return [{"title": "a1", "link": "https://example.com/1"}]

        # The tagger's first action is a verified_intel lookup — make it throw.
        import aria_service.intel.verified_intel as _vi

        ctxs = [
            patch.object(dr, "_web_search", new=_search),
            patch.object(dr, "_fetch_article_text", new=AsyncMock(return_value="body " * 40)),
            patch.object(dr, "_analyse_article", new=AsyncMock(return_value={
                "facts": [{"topic": "t", "content": "c", "confidence": "PROBABLE"}]})),
            patch.object(dr, "_process_analysis", new=AsyncMock(return_value=(1, 0))),
            patch.object(dr, "_load_hypotheses", new=AsyncMock(return_value=[])),
            patch.object(dr, "_save_hypotheses", new=AsyncMock(return_value=None)),
            patch.object(dr, "_get_read_urls", new=AsyncMock(return_value=set())),
            patch.object(dr, "_mark_read", new=AsyncMock(return_value=None)),
            patch.object(dr, "search_knowledge", return_value=""),
            patch.object(_vi, "get_relevant_verified_facts",
                         new=AsyncMock(side_effect=RuntimeError("verified_intel store unreachable"))),
        ]
        for c in ctxs:
            c.__enter__()
        try:
            return await dr.investigate(_FakeLLM(), "Acme Defence Ltd",
                                        depth="quick", investigate_people=0)
        finally:
            for c in reversed(ctxs):
                c.__exit__(None, None, None)
    return asyncio.run(go())


# ── half 1: the failure is RECORDED ───────────────────────────────────────────
def test_a_failed_tagger_is_recorded_not_swallowed() -> None:
    out = _run_with_broken_tagger()
    vs = out.get("verification_summary")

    assert isinstance(vs, dict), "verification_summary must always be present"
    assert vs.get("failed"), (
        "the tagger threw and said nothing — the report then publishes "
        "traceability metrics computed from a step that never ran"
    )
    assert "verified_intel store unreachable" in vs["failed"] or "RuntimeError" in vs["failed"]


def test_a_healthy_tagger_reports_no_failure() -> None:
    """Regression: the normal path must not start claiming a failure."""
    async def go():
        async def _search(q, *a, **k):
            return [{"title": "a1", "link": "https://example.com/1"}]
        ctxs = [
            patch.object(dr, "_web_search", new=_search),
            patch.object(dr, "_fetch_article_text", new=AsyncMock(return_value="body " * 40)),
            patch.object(dr, "_analyse_article", new=AsyncMock(return_value={
                "facts": [{"topic": "t", "content": "c", "confidence": "PROBABLE"}]})),
            patch.object(dr, "_process_analysis", new=AsyncMock(return_value=(1, 0))),
            patch.object(dr, "_load_hypotheses", new=AsyncMock(return_value=[])),
            patch.object(dr, "_save_hypotheses", new=AsyncMock(return_value=None)),
            patch.object(dr, "_get_read_urls", new=AsyncMock(return_value=set())),
            patch.object(dr, "_mark_read", new=AsyncMock(return_value=None)),
            patch.object(dr, "search_knowledge", return_value=""),
        ]
        for c in ctxs:
            c.__enter__()
        try:
            return await dr.investigate(_FakeLLM(), "Acme Defence Ltd",
                                        depth="quick", investigate_people=0)
        finally:
            for c in reversed(ctxs):
                c.__exit__(None, None, None)
    out = asyncio.run(go())
    assert not (out.get("verification_summary") or {}).get("failed")


# ── half 2: the failure REACHES THE REPORT ────────────────────────────────────
def _section():
    return SimpleNamespace(data_gaps=[])


def test_tagger_failure_reaches_the_report_data_gaps() -> None:
    sec = _section()
    ddo._surface_research_disclosures(
        {"verification_summary": {"failed": "RuntimeError: store unreachable"}}, sec)
    joined = " ".join(sec.data_gaps).lower()
    assert "verification" in joined
    assert "not run" in joined or "did not" in joined


def test_coercion_and_synthesis_failures_also_reach_the_report() -> None:
    """R-F3258 / R-F3259 disclosures must not die inside the engine either."""
    sec = _section()
    ddo._surface_research_disclosures(
        {"topic_coerced_from": "list[2]", "synthesis_error": "assessment call failed (X)"}, sec)
    joined = " ".join(sec.data_gaps).lower()
    assert "list[2]" in joined
    assert "assessment" in joined


def test_a_clean_research_result_adds_no_gaps() -> None:
    sec = _section()
    ddo._surface_research_disclosures(
        {"topic_coerced_from": None, "synthesis_error": None,
         "verification_summary": {"failed": None}}, sec)
    assert sec.data_gaps == [], "a clean run must not manufacture gaps"


def test_non_dict_research_result_is_survivable() -> None:
    sec = _section()
    ddo._surface_research_disclosures(None, sec)
    ddo._surface_research_disclosures("nonsense", sec)
    assert sec.data_gaps == []

"""R-F3966 / C-55 — "zero named individuals" and "we could not run the person
investigation" rendered identically.

The person/UBO drill-down answers the highest-value question in due diligence —
*who is behind this* — and disclosed none of its failures. Both swallow points
are `logger.debug` with no gap, no `wire_failure` and no layer-status change,
i.e. DARK under §21a:

    deep_researcher.py:817
        except Exception as _e:
            logger.debug("person-extraction failed: %s", _e)      # -> no candidates

    deep_researcher.py:835
        except Exception as _e:
            logger.debug("investigate_person(%s) failed: %s", name, _e)
            continue                                             # -> person DROPPED

The second is the worse one. `seed_people` are names the caller ALREADY KNOWS —
registry directors and contact names (R-F1823) — so a director ARIA was handed
by Companies House can vanish from the report with nothing recording it. On an
LLM outage the extractor returns nothing and every dossier raises, so the report
renders "no people found" for an entity whose board is public.

The contrast is what proves it was an oversight rather than a decision: the
sibling `investigate()` path DOES disclose, via `synthesis_error` ->
`_surface_research_disclosures`, and the drill-down's own *skip* case already
calls `_mark_partial`. Only the failures inside it were never given the wire.

The fix reuses that existing channel rather than inventing one: failures
accumulate on the result as `people_disclosures`, and
`dd_orchestrator._surface_research_disclosures` — already called on every
digital run — renders them as data gaps.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from aria_service.intel import deep_researcher as DR
from aria_service.intel import dd_orchestrator as DD
from aria_service.intel.dd_schema import ARKDDReport


class _DeadLLM:
    """Every completion raises — the LLM-outage case."""

    async def complete(self, system, user, **kw):
        raise RuntimeError("provider chain exhausted")


class _LiveLLM:
    async def complete(self, system, user, **kw):
        class _R:
            text = '{"people": [{"name": "Jane Roe", "role": "director"}]}'
        return _R()


# ── the extraction failure must be recorded ──────────────────────────────────

def test_extraction_failure_is_disclosed():
    disclosures: list[str] = []
    out = asyncio.run(DR._discover_and_investigate_people(
        _DeadLLM(), "Acme Ltd",
        [{"content": "Acme Ltd was incorporated in 2011."}],
        max_people=3, t_start=time.time(), budget_s=999.0,
        seed_people=None, disclosures=disclosures,
    ))
    assert out == []
    assert disclosures, (
        "the named-individual extractor failed and the report would have said "
        "'zero named individuals' with nothing recording that it never ran"
    )
    assert any("extract" in d.lower() for d in disclosures)


# ── a DROPPED known director is the dangerous one ────────────────────────────

def test_a_dropped_seed_person_is_disclosed_by_name(monkeypatch):
    async def _boom(llm, name, context=""):
        raise RuntimeError("dossier build failed")

    monkeypatch.setattr(DR, "investigate_person", _boom)
    disclosures: list[str] = []

    out = asyncio.run(DR._discover_and_investigate_people(
        _LiveLLM(), "Acme Ltd", [],
        max_people=3, t_start=time.time(), budget_s=999.0,
        seed_people=["John Smith"], disclosures=disclosures,
    ))
    assert out == [], "the person could not be investigated"
    joined = " ".join(disclosures)
    assert "John Smith" in joined, (
        "a registry-known director was dropped from the report silently"
    )


def test_a_partial_failure_still_returns_the_people_that_worked(monkeypatch):
    """A disclosure must not cost us the dossiers that succeeded."""
    async def _half(llm, name, context=""):
        if name == "Bad Name":
            raise RuntimeError("nope")
        return {"risk_assessment": "LOW"}

    monkeypatch.setattr(DR, "investigate_person", _half)
    disclosures: list[str] = []

    out = asyncio.run(DR._discover_and_investigate_people(
        _LiveLLM(), "Acme Ltd", [],
        max_people=5, t_start=time.time(), budget_s=999.0,
        seed_people=["Good Name", "Bad Name"], disclosures=disclosures,
    ))
    names = [p["name"] for p in out]
    assert "Good Name" in names
    assert "Bad Name" not in names
    assert any("Bad Name" in d for d in disclosures)


# ── it must stay quiet on a clean run ────────────────────────────────────────

def test_a_clean_run_discloses_nothing(monkeypatch):
    async def _ok(llm, name, context=""):
        return {"risk_assessment": "LOW"}

    monkeypatch.setattr(DR, "investigate_person", _ok)
    disclosures: list[str] = []

    out = asyncio.run(DR._discover_and_investigate_people(
        _LiveLLM(), "Acme Ltd", [],
        max_people=3, t_start=time.time(), budget_s=999.0,
        seed_people=["Jane Roe"], disclosures=disclosures,
    ))
    assert [p["name"] for p in out] == ["Jane Roe"]
    assert disclosures == [], (
        "a disclosure that fires on a healthy run trains the reader to skip it"
    )


def test_the_sink_is_optional_so_existing_callers_are_unaffected(monkeypatch):
    async def _ok(llm, name, context=""):
        return {"risk_assessment": "LOW"}

    monkeypatch.setattr(DR, "investigate_person", _ok)
    out = asyncio.run(DR._discover_and_investigate_people(
        _LiveLLM(), "Acme Ltd", [],
        max_people=3, t_start=time.time(), budget_s=999.0, seed_people=["Jane Roe"],
    ))
    assert [p["name"] for p in out] == ["Jane Roe"]


# ── and it must reach the READER, not just the dict ──────────────────────────

def test_the_dd_renders_people_disclosures_as_data_gaps():
    rep = ARKDDReport()
    DD._surface_research_disclosures(
        {"people_disclosures": [
            "the named-individual extractor failed (provider chain exhausted)",
            "could not investigate 'John Smith' (dossier build failed)",
        ]},
        rep.digital,
    )
    gaps = " ".join(rep.digital.data_gaps)
    assert "John Smith" in gaps
    assert "extractor failed" in gaps
    assert "NOT" in gaps or "not" in gaps, (
        "the gap must say the absence of named individuals is not a finding"
    )


def test_no_people_disclosures_means_no_gap():
    rep = ARKDDReport()
    DD._surface_research_disclosures({"people_disclosures": []}, rep.digital)
    assert rep.digital.data_gaps == []
    DD._surface_research_disclosures({}, rep.digital)
    assert rep.digital.data_gaps == []


def test_malformed_disclosures_do_not_crash_the_report():
    rep = ARKDDReport()
    DD._surface_research_disclosures({"people_disclosures": "not-a-list"}, rep.digital)
    DD._surface_research_disclosures({"people_disclosures": None}, rep.digital)
    assert isinstance(rep.digital.data_gaps, list)


# ── the wire must be real ────────────────────────────────────────────────────

def test_investigate_puts_the_disclosures_on_its_result():
    from ._source_probe import function_code
    src = function_code(DR, "investigate")
    assert "people_disclosures" in src, (
        "the drill-down's failures never reach the result dict, so "
        "_surface_research_disclosures has nothing to render"
    )

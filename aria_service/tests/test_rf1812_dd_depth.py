"""R-F1812 — DD depth: targeted fan-out + recursive person drill-down + citations.

Fixes the operator's complaint (shallow DD, "Zero named individuals"): for entity
DD the query fan-out adds person/procurement/native-language queries, and each
NAMED INDIVIDUAL surfaced is run through investigate_person (bounded + time-guarded).
"""
import asyncio
import json

import pytest

import aria_service.intel.deep_researcher as dr


class _FakeLLM:
    is_configured = True

    def __init__(self, text):
        self._text = text

    async def complete(self, *a, **k):
        class _R:
            pass
        r = _R()
        r.text = self._text
        return r


def test_targeted_queries_cover_people_procurement_linkedin_multilang():
    qs = dr._dd_targeted_queries("Modirum Gespi")
    blob = " ".join(qs).lower()
    assert any("linkedin.com" in q for q in qs), "no LinkedIn-scoped query"
    # multilingual role terms (PT) — the Modirum case is Portuguese
    assert "administrador" in blob and "gerente" in blob and "sócio" in blob
    # procurement footprint (native + EN)
    assert "tender" in blob and "adjudicação" in blob
    # too-short entity yields nothing (guard)
    assert dr._dd_targeted_queries("x") == []


def test_recursive_person_drilldown_bounded_and_invoked(monkeypatch):
    calls = []

    async def _fake_investigate_person(llm, name, context=""):
        calls.append(name)
        return {"name": name, "risk_assessment": "LOW", "pep_status": "Not a PEP", "red_flags": []}

    monkeypatch.setattr(dr, "investigate_person", _fake_investigate_person)
    llm = _FakeLLM(json.dumps({"people": [
        {"name": "Maria Silva", "role": "Director"},
        {"name": "João Costa", "role": "Manager"},
        {"name": "Maria Silva", "role": "dup"},      # dedup
        {"name": "Ana Reis", "role": "Owner"},
    ]}))
    facts = [{"content": "Maria Silva is listed as director of the company."}]

    out = asyncio.run(dr._discover_and_investigate_people(
        llm, "Some Company DD", facts, max_people=2, t_start=dr.time.time(), budget_s=100.0))

    assert len(out) == 2, "max_people cap not honoured"
    assert calls == ["Maria Silva", "João Costa"], f"wrong/duped people investigated: {calls}"
    assert out[0]["dossier"]["risk_assessment"] == "LOW"


def test_person_drilldown_respects_time_budget(monkeypatch):
    async def _fake_investigate_person(llm, name, context=""):
        return {"name": name}
    monkeypatch.setattr(dr, "investigate_person", _fake_investigate_person)
    llm = _FakeLLM(json.dumps({"people": [{"name": "Maria Silva", "role": "Director"}]}))
    facts = [{"content": "Maria Silva director"}]
    # t_start far in the past + tiny budget => already over budget => no people
    out = asyncio.run(dr._discover_and_investigate_people(
        llm, "DD", facts, max_people=3, t_start=dr.time.time() - 999, budget_s=10.0))
    assert out == [], "time budget not enforced"


def test_person_drilldown_disabled_returns_empty(monkeypatch):
    llm = _FakeLLM("{}")
    out = asyncio.run(dr._discover_and_investigate_people(
        llm, "DD", [{"content": "x"}], max_people=0, t_start=dr.time.time(), budget_s=100.0))
    assert out == []

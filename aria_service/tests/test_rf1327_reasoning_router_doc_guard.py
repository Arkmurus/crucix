"""R-F1327 — reasoning_router Stage 4 must NOT run the company OSINT pipeline
when the user attached a document.

Live bug (2026-06-04, build 386e7fdc): "Aria do a deep analysis of this NDA and
give feedback" with the NDA attached returned "Input rejected as non-company".
Root cause: reasoning_router.try_local_reasoning Stage 4 (R-F1061) matched
investigate ("analysis") + company ("Systems"/"LLC" from the doc) keywords and
passed the WHOLE message to company_investigator — bypassing _detect_tool_intent's
R-F1326 / §22a guard entirely.

These drive the REAL try_local_reasoning() entry point (not a proxy) with a spy on
investigate_company:
  - attached-doc review request  -> investigate_company MUST NOT be called
  - control (same keywords, no doc) -> investigate_company IS reached (proves the
    test exercises Stage 4 and the doc-marker guard is the actual differentiator)
"""
from __future__ import annotations

import types

import pytest

from aria_service.intel import reasoning_router, company_investigator


def _fake_report():
    r = types.SimpleNamespace()
    r.findings = []
    r.summary = "SPY-WAS-CALLED"
    r.risk_indicators = []
    r.duration_ms = 1.0
    r.entity_name = "spy"
    r.canonical_name = "spy"
    r.sources_cited = []
    return r


async def _run(monkeypatch, question):
    calls = []

    async def _spy(name, country=""):
        calls.append(name)
        return _fake_report()

    # reasoning_router does `from .company_investigator import investigate_company`
    # at call time, so patching the module attribute intercepts it.
    monkeypatch.setattr(company_investigator, "investigate_company", _spy)
    res = await reasoning_router.try_local_reasoning(question, silent=True)
    return calls, (res or {})


_NDA = (
    "Aria do a deep analysis of this NDA and let us know your feedback.\n\n"
    "[ATTACHED DOCUMENT: ATNA Systems - unsigned.pdf]\n"
    "MUTUAL CONFIDENTIALITY, NON-DISCLOSURE AND NON-CIRCUMVENTION AGREEMENT. "
    "Party A: ATNA, LLC a Georgia LLC. ATNA Systems. Receiving Party shall hold "
    "the Confidential Information in strictest confidence.\n"
    "[END ATTACHED DOCUMENT]"
)


@pytest.mark.asyncio
async def test_rf1327_attached_doc_does_not_route_to_company_investigator(monkeypatch):
    calls, res = await _run(monkeypatch, _NDA)
    assert calls == [], (
        f"company_investigator MUST NOT be called for an attached-document review "
        f"— got company_name={calls!r}"
    )
    assert res.get("source") != "company_investigator"
    assert "rejected as non-company" not in str(res.get("response", "")).lower()


@pytest.mark.asyncio
async def test_rf1327_control_without_doc_reaches_company_investigator(monkeypatch):
    # Same investigate+company keywords, NO attached document → Stage 4 must fire,
    # proving this test actually exercises Stage 4 and the doc guard is what differs.
    calls, _res = await _run(monkeypatch, "Please investigate Acme Systems Ltd company")
    assert calls, (
        "control: with no attached document, Stage 4 should reach investigate_company "
        "— if this fails, an earlier stage answered and the primary test is inconclusive"
    )

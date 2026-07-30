"""R-F3455 — the report said "nothing found" and cited an FRC investigation.

THE CONTRADICTION, from the delivered Babcock International Group PLC report. The digital
section concluded::

    Adverse media: nothing found in the sources searched
    0 credible adverse item(s) from 122 raw hit(s)

Its OWN "Cited sources" list, printed a few lines below, contained::

    FRC expands probe of PwC's Babcock International audits | Compliance Week
    Investigation regarding the audits of Babcock International Group plc by
    PricewaterhouseCoopers LLP

A live Financial Reporting Council investigation into the audits of the subject's accounts
is material to any decision relying on those accounts. Both statements were published and
neither was reconciled.

WHY IT HAPPENED. The adverse sweep and `digital.press_coverage` are filled by DIFFERENT
paths — the sweep filters 122 raw hits through de-duplication, attribution and content
tests; press_coverage accumulates what the research layer actually read. Nothing compared
the two, so an item could be absent from one and present in the other silently.

WHAT THE FIX IS NOT. It does not re-classify anything and invents no heuristic: it applies
the SAME two predicates the sweep uses, so it cannot surface an item the sweep would have
rejected on attribution or content. It reports only that the report's own evidence
disagrees with its own conclusion.
"""
from __future__ import annotations

import pytest

from aria_service.intel.dd_orchestrator import _adverse_citation_contradictions
from aria_service.intel.dd_schema import ARKDDReport, Evidence

_FRC = ("FRC expands probe of PwC's Babcock International audits | Article | "
        "Compliance Week")


def _report(citations) -> ARKDDReport:
    r = ARKDDReport()
    r.identity.entity_name = "Babcock International Group PLC"
    r.digital.press_coverage = list(citations)
    return r


def test_capability_the_frc_investigation_is_detected():
    """THE DEFECT: this headline sat in the cited sources under a 'nothing found'."""
    r = _report([Evidence(source=_FRC, url="https://www.complianceweek.com/x",
                          source_tier="UNVERIFIED")])
    hits = _adverse_citation_contradictions(r)
    assert len(hits) == 1, f"the FRC/PwC investigation was not detected: {hits}"
    assert "FRC" in hits[0]["title"]


def test_a_neutral_citation_is_not_flagged():
    """The guard must not turn every citation into an allegation."""
    r = _report([
        Evidence(source="Babcock International Group PLC | LinkedIn", url="https://x/1"),
        Evidence(source="BABCOCK INTERNATIONAL GROUP PLC overview - GOV.UK",
                 url="https://find-and-update.company-information.service.gov.uk/x"),
        Evidence(source="Shaping the future of defence procurement - Babcock",
                 url="https://babcockinternational.com/x"),
    ])
    assert _adverse_citation_contradictions(r) == []


def test_an_adverse_item_about_someone_else_is_not_flagged():
    """Attribution still applies — this is the name-coincidence class the sweep guards,
    and the guard reuses the sweep's own predicate precisely so it cannot regress."""
    r = _report([Evidence(source="SFO opens bribery investigation into Unrelated Corp",
                          url="https://sfo.gov.uk/x")])
    assert _adverse_citation_contradictions(r) == []


def test_dict_shaped_citations_from_a_stored_blob_are_handled():
    """Persisted reports come back as dicts, not Evidence objects."""
    r = _report([{"source": _FRC, "url": "https://www.complianceweek.com/x"}])
    assert len(_adverse_citation_contradictions(r)) == 1


def test_no_citations_is_not_a_contradiction():
    assert _adverse_citation_contradictions(_report([])) == []


def test_it_never_raises_on_a_malformed_report():
    """A coherence check must not be able to fail a report."""
    r = _report([None, {"nothing": "useful"}, Evidence(source="")])
    assert _adverse_citation_contradictions(r) == []


def test_the_synthesis_gate_only_fires_when_nothing_was_queued_for_review():
    """If items are ALREADY queued for review the reader has not been told a false
    clean, and a second warning would be noise. Asserted on the wiring, because the gate
    lives inside a very large async synthesis function."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "intel" / "dd_orchestrator.py").read_text(encoding="utf-8", errors="replace")
    assert "R-F3455" in src
    assert "if not _reviewed:" in src, "the gate is not conditioned on an empty review set"
    assert "_adverse_citation_contradictions(report)" in src, "the gate is never called"
    assert "unreviewed_adverse_citations" in src, "the contradiction is not persisted"
    assert "adverse-media conclusion CONTRADICTED by" in src, "no data gap is recorded"

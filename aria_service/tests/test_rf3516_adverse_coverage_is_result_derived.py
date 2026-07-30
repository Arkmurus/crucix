"""R-F3516 — the adverse-media coverage claim was made from the QUERY, not the RESULT.

THE LIVE DEFECT, from a delivered report on Chemring Group PLC (dd_8bd7ac42a488, a UK
listed defence group). The adverse-media block reported::

    coverage_by_class: {
      legal_court_us_federal: 3, legal_court_uk: 3, regulatory_us_ofac: 3,
      regulatory_us_doj: 3, regulatory_us_sec: 3, regulatory_uk_sfo: 3,
      leak_database_icij: 1, investigative_journalism_occrp: 1,
      investigative_journalism_bellingcat: 1, ...
    }

A reader takes that as: ICIJ leak databases, OCCRP, Bellingcat, the DOJ, the SEC, OFAC and
the US federal courts were screened, and nothing material was found. Of the 92 findings,
**75 were not corroborated and 7 were unverifiable — those classes had ZERO corroborated
rows between them.** What actually came back was the subject's own Companies House pages
and ARIA's own `memory://` records, stamped with the class of the query that had been
asked::

    source_class: investigative_journalism_bellingcat
    source_url:   memory://582d7291606e          <- ARIA's own memory store
    query:        "Chemring Group PLC" site:bellingcat.com

    source_class: leak_database_icij
    source_url:   https://find-and-update.company-information.service.gov.uk/...

That is a FALSE CLEAN on the adverse-media layer of a defence contractor: the sources were
never heard from, and the report said they were covered.

WHY R-F3023 DID NOT CATCH IT. R-F3023 diagnosed exactly this class — "the source class
describes the QUERY, not the RESULT" — and fixed it at the FINDING level, adding
`source_domain` and `source_class_corroborated`. It then stopped one line short: the
coverage counter went on incrementing off `source_class`, so the sweep's headline claim
kept asserting precisely what R-F3023 had just proved unreliable. A producer with no
carrier into the number a reader actually reads.

THE SECOND DEFECT, which made the honest flag itself dishonest. The OFSI template is
``"{name}" site:gov.uk OFSI enforcement``. `site:gov.uk` is the whole of UK government, so
`find-and-update.company-information.service.gov.uk` ends with it and four Companies House
registry pages were marked `source_class_corroborated: True` for OFSI. A company register
page corroborates nothing whatever about a sanctions-enforcement screen. A constraint that
broad yields None — unverifiable — never True.

THE FIX SEPARATES TWO FACTS. "We asked" and "the source answered" are different, and must
not share one number: `classes_asked`, `classes_answered`, `classes_silent`. A silent
source is real negative evidence and is reported AS silence.
"""
from __future__ import annotations

import pathlib

import pytest

from aria_service.intel.researcher import (
    _UNSPECIFIC_CONSTRAINT_HOSTS,
    _domain_corroborates_class,
)
from aria_service.intel.dd_schema import _render_adverse_media


# ── the corroboration predicate ─────────────────────────────────────────────

@pytest.mark.parametrize("domain,source_class,query,expected,why", [
    # THE LIVE FALSE CORROBORATION — four of these shipped on the Chemring report.
    ("find-and-update.company-information.service.gov.uk", "regulatory_uk_ofsi",
     '"X" site:gov.uk OFSI enforcement', None,
     "a company register page cannot corroborate a sanctions-enforcement screen"),
    # A SPECIFIC body still corroborates — the fix must not blind the check.
    ("data.fca.org.uk", "regulatory_uk_fca", '"X" site:fca.org.uk', True,
     "a named regulator's own host is real corroboration"),
    ("sfo.gov.uk", "regulatory_uk_sfo", '"X" site:sfo.gov.uk', True,
     "a specific body under a broad TLD is still specific"),
    ("offshoreleaks.icij.org", "leak_database_icij",
     '"X" site:offshoreleaks.icij.org', True, "the genuine ICIJ case"),
    # The R-F3023 case must stay False — actively contradicted, not merely unproven.
    ("doi.org", "regulatory_us_doj", '"X" site:justice.gov', False,
     "an academic DOI answering a DOJ query is contradicted, not unverifiable"),
    ("reuters.com", "news_archive_general", '"X" fraud', None,
     "no host constraint means there is nothing to corroborate"),
])
def test_corroboration_distinguishes_specific_from_whole_government(
        domain, source_class, query, expected, why):
    assert _domain_corroborates_class(domain, source_class, query) is expected, why


def test_none_is_not_false():
    """"Unverifiable" must never render as "contradicted" — the two drive different
    downstream behaviour (`_adverse_media_materiality` drops False, keeps None)."""
    unverifiable = _domain_corroborates_class(
        "find-and-update.company-information.service.gov.uk", "regulatory_uk_ofsi",
        '"X" site:gov.uk OFSI enforcement')
    contradicted = _domain_corroborates_class("doi.org", "regulatory_us_doj",
                                              '"X" site:justice.gov')
    assert unverifiable is None and contradicted is False
    assert unverifiable is not False, "a too-broad constraint is unknown, not a strike"


def test_the_broad_host_list_covers_whole_government_and_public_suffixes():
    for h in ("gov.uk", "europa.eu", "co.uk", "com", "org"):
        assert h in _UNSPECIFIC_CONSTRAINT_HOSTS
    # ...and does NOT swallow specific bodies that merely live under them.
    for h in ("sfo.gov.uk", "fca.org.uk", "justice.gov", "offshoreleaks.icij.org"):
        assert h not in _UNSPECIFIC_CONSTRAINT_HOSTS, (
            f"{h} is a specific body — excluding it would blind the check entirely")


# ── the counter split ───────────────────────────────────────────────────────

def test_the_sweep_reports_asked_answered_and_silent_separately():
    """The structural property: `coverage_by_class` alone cannot express "asked but
    silent", which is the state the whole Chemring adverse-media block was in."""
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "intel" / "researcher.py").read_text(encoding="utf-8", errors="replace")
    for key in ('"classes_asked"', '"classes_answered"', '"classes_silent"'):
        assert key in src, f"{key} is not returned — the honest counts do not reach a consumer"
    assert "if _corrob:" in src, (
        "the answered counter must be gated on corroboration, or it is the old counter "
        "under a new name")
    assert "coverage_by_class[source_class] = coverage_by_class.get" not in src, (
        "the query-derived counter is back")


# ── the carrier: what the customer actually reads ───────────────────────────

def _am(**over):
    base = {
        "status": "completed", "ok": True, "templates_searched": 30,
        "search_backends_answered": True, "findings": [], "materiality": {},
    }
    base.update(over)
    return base


def test_capability_silent_sources_are_named_in_the_report():
    """THE CARRIER. The no-findings branch already promised "the sources that did not
    answer are listed in the data gaps" — and on the live run that list was EMPTY. A
    consumer with no producer."""
    lines = _render_adverse_media(_am(
        classes_asked={"leak_database_icij": 1, "regulatory_us_doj": 3,
                       "investigative_journalism_bellingcat": 1},
        classes_answered={"regulatory_us_doj": 2},
        classes_silent=["investigative_journalism_bellingcat", "leak_database_icij"],
    ))
    body = "\n".join(lines)
    assert "SEARCHED but SILENT (2)" in body, body
    assert "leak_database_icij" in body
    assert "investigative_journalism_bellingcat" in body
    assert "NOT a clean screen of those sources" in body, (
        "naming the silent sources without saying what silence MEANS invites the same "
        "false-clean reading")


def test_capability_it_matters_most_when_findings_are_present():
    """The live case: 92 raw findings and a coverage claim naming sources that returned
    nothing. Breadth of results is exactly what makes a reader assume coverage."""
    lines = _render_adverse_media(_am(
        findings=[{"source_url": "memory://abc", "title": "brain_hook:web_search"}] * 92,
        materiality={"material": False, "official": 0, "credible_count": 0},
        classes_asked={"leak_database_icij": 1},
        classes_answered={},
        classes_silent=["leak_database_icij"],
    ))
    assert any("SEARCHED but SILENT" in l for l in lines), (
        "the silence disclosure must not be gated on there being no findings")


def test_capability_total_silence_is_stated_plainly():
    lines = _render_adverse_media(_am(
        classes_asked={"leak_database_icij": 1, "regulatory_us_doj": 3},
        classes_answered={},
    ))
    body = "\n".join(lines)
    assert "NO named source class returned a result attributable to it" in body
    assert "UNESTABLISHED" in body


def test_a_sweep_with_full_coverage_says_nothing_extra():
    """The disclosure must be evidence-driven, not decoration. A sweep where every class
    answered gets no silence line — a warning that always fires is ignored."""
    lines = _render_adverse_media(_am(
        classes_asked={"regulatory_us_doj": 3},
        classes_answered={"regulatory_us_doj": 3},
        classes_silent=[],
    ))
    body = "\n".join(lines)
    assert "SILENT" not in body
    assert "UNESTABLISHED" not in body


def test_a_legacy_blob_without_the_new_keys_renders_unchanged():
    """Stored reports predate R-F3516. Absent keys must not manufacture a warning —
    'we did not record this' is not 'the sources were silent'."""
    lines = _render_adverse_media(_am(findings=[{"title": "x", "source_url": "http://a/b"}]))
    body = "\n".join(lines)
    assert "SILENT" not in body and "UNESTABLISHED" not in body


# ── PDF / online parity (CLAUDE.md §13, and the R-F3055 mirror contract) ────

def test_the_pdf_mirror_carries_the_same_disclosure():
    """`lib/reports/pdf_generator.mjs` is a hand-mirror of `_render_adverse_media`. A
    disclosure present online and absent from the filed PDF is the worse half being the
    one the client keeps."""
    mjs = (pathlib.Path(__file__).resolve().parents[2]
           / "lib" / "reports" / "pdf_generator.mjs").read_text(
               encoding="utf-8", errors="replace")
    assert "classes_silent" in mjs, "the PDF does not carry the silence disclosure"
    assert "SEARCHED but SILENT" in mjs
    assert "NOT a clean screen of those sources" in mjs
    assert "UNESTABLISHED" in mjs

"""R-F3522 — the R-F3516 fix pinned against the REAL rows that exposed it.

R-F3516 is proven by construction elsewhere. This file pins it against PRODUCTION DATA:
rows copied verbatim from the delivered Chemring Group PLC report (dd_8bd7ac42a488,
2026-07-30, a UK listed defence group), so the fix stays falsifiable against the exact
evidence that revealed the defect rather than against fixtures written to fit it.

WHY A GOLD CASE AND NOT MORE UNIT TESTS. The lesson from R-F3515 the same day: two live
defects had full, green, purpose-written coverage, because the fixtures were drawn from
ONE subject and encoded that subject's coincidences as the rule. Fixtures I invent test
what I already believe. These rows are what the system actually produced.

WHAT THE REAL DATA SAYS. Replaying all 92 delivered findings through the R-F3516
predicate: **10 of 11 source classes were SILENT** — ICIJ, OCCRP, Bellingcat, US federal
courts, UK courts, arbitration, OFSI, SFO, DOJ and OFAC returned nothing attributable to
them. Only `regulatory_uk_fca` genuinely answered (4 of 10 rows from data.fca.org.uk).
Every one of those ten was reported to the customer as covered.

Two rows are worth reading on their own:

  * `regulatory_us_ofac`, query ``site:treasury.gov``, answered by **chemring.com** —
    the SUBJECT'S OWN WEBSITE presented as OFAC sanctions coverage.
  * `regulatory_uk_ofsi`, query ``site:gov.uk OFSI enforcement``, answered by the
    company's own Companies House page — and marked `source_class_corroborated: True`
    before R-F3516, because `gov.uk` is the whole of UK government.
"""
from __future__ import annotations

import collections

import pytest

from aria_service.intel.researcher import _domain_corroborates_class as _corrob


#: One faithful row per class, copied verbatim from the delivered report. `source_domain`
#: is empty on the `memory://` rows exactly as production recorded them — those are ARIA's
#: OWN memory store answering a `site:`-constrained query about a third party.
CHEMRING_ROWS = [
    # ── the ten that were reported as covered and had answered nothing ──
    {"source_class": "investigative_journalism_bellingcat", "source_domain": "",
     "query_executed": '"Chemring Group PLC" site:bellingcat.com',
     "source_url": "memory://582d7291606e"},
    {"source_class": "investigative_journalism_occrp", "source_domain": "",
     "query_executed": '"Chemring Group PLC" site:occrp.org',
     "source_url": "memory://582d7291606e"},
    {"source_class": "leak_database_icij", "source_domain": "",
     "query_executed": '"Chemring Group PLC" site:offshoreleaks.icij.org',
     "source_url": "memory://582d7291606e"},
    {"source_class": "legal_court_uk", "source_domain": "",
     "query_executed": '"Chemring Group PLC" site:bailii.org',
     "source_url": "memory://582d7291606e"},
    {"source_class": "legal_court_us_federal", "source_domain": "",
     "query_executed": '"Chemring Group PLC" site:courtlistener.com',
     "source_url": "memory://582d7291606e"},
    {"source_class": "regulatory_uk_sfo", "source_domain": "",
     "query_executed": '"Chemring Group PLC" site:sfo.gov.uk',
     "source_url": "memory://582d7291606e"},
    {"source_class": "regulatory_us_doj", "source_domain": "",
     "query_executed": '"Chemring Group PLC" site:justice.gov',
     "source_url": "memory://582d7291606e"},
    # The subject's OWN WEBSITE, returned for a treasury.gov query, reported as OFAC.
    {"source_class": "regulatory_us_ofac", "source_domain": "chemring.com",
     "query_executed": '"Chemring Group PLC" site:treasury.gov',
     "source_url": "https://www.chemring.com/"},
    # The FALSE corroboration: Companies House marked True for OFSI, because the
    # template constrained itself to the whole of gov.uk.
    {"source_class": "regulatory_uk_ofsi",
     "source_domain": "find-and-update.company-information.service.gov.uk",
     "query_executed": '"Chemring Group PLC" site:gov.uk OFSI enforcement',
     "source_url": "https://find-and-update.company-information.service.gov.uk/company/00086662"},
    # An UNCONSTRAINED template — nothing to corroborate, so None, never False.
    {"source_class": "legal_arbitration", "source_domain": "",
     "query_executed": '"Chemring Group PLC" arbitration award OR ICSID OR LCIA OR ICC',
     "source_url": "memory://582d7291606e"},
    # ── the ONE class that genuinely answered ──
    {"source_class": "regulatory_uk_fca", "source_domain": "data.fca.org.uk",
     "query_executed": '"Chemring Group PLC" site:fca.org.uk',
     "source_url": "https://data.fca.org.uk/artefacts/NSM/RNS/5498881.html"},
]


def _replay(rows):
    """What the sweep would now report for these rows."""
    asked, answered = collections.Counter(), collections.Counter()
    for r in rows:
        sc = r["source_class"]
        asked[sc] += 1
        if _corrob(r["source_domain"], sc, r["query_executed"]):
            answered[sc] += 1
    silent = sorted(c for c in asked if not answered.get(c))
    return dict(asked), dict(answered), silent


def test_gold_only_the_fca_class_actually_answered():
    """The headline finding, from production data."""
    asked, answered, silent = _replay(CHEMRING_ROWS)
    assert set(answered) == {"regulatory_uk_fca"}, (
        f"a class other than the FCA is being credited: {answered}")
    assert len(silent) == len(asked) - 1 == 10


@pytest.mark.parametrize("source_class", [
    "leak_database_icij",
    "investigative_journalism_occrp",
    "investigative_journalism_bellingcat",
    "legal_court_us_federal",
    "legal_court_uk",
    "regulatory_us_doj",
    "regulatory_us_ofac",
    "regulatory_uk_sfo",
    "regulatory_uk_ofsi",
])
def test_gold_each_class_reported_as_covered_is_now_silent(source_class):
    """Every one of these appeared in the delivered `coverage_by_class`, and a reader
    takes that as "screened, nothing found". None of them answered."""
    _, _, silent = _replay(CHEMRING_ROWS)
    assert source_class in silent, (
        f"{source_class} is being counted as covered again — it returned nothing "
        "attributable to it on the real run")


def test_gold_the_subjects_own_website_is_not_ofac_coverage():
    """`site:treasury.gov` answered by chemring.com. A company's own homepage cannot
    corroborate a sanctions screen of that company — it is the least independent
    source available."""
    row = next(r for r in CHEMRING_ROWS if r["source_domain"] == "chemring.com")
    assert _corrob(row["source_domain"], row["source_class"],
                   row["query_executed"]) is False


def test_gold_companies_house_does_not_corroborate_ofsi():
    """The false corroboration that made the honest flag itself dishonest. None, not
    False: the row is unverifiable, not contradicted — `gov.uk` genuinely contains
    OFSI, we simply cannot tell from the host whether OFSI answered."""
    row = next(r for r in CHEMRING_ROWS if r["source_class"] == "regulatory_uk_ofsi")
    assert _corrob(row["source_domain"], row["source_class"],
                   row["query_executed"]) is None


def test_gold_an_unconstrained_template_is_unverifiable_not_contradicted():
    row = next(r for r in CHEMRING_ROWS if r["source_class"] == "legal_arbitration")
    assert _corrob(row["source_domain"], row["source_class"],
                   row["query_executed"]) is None


def test_gold_the_genuine_fca_hit_survives():
    """The direction that matters just as much: the fix must not blind the check.
    If this ever goes silent, R-F3516 has become a false-negative machine."""
    row = next(r for r in CHEMRING_ROWS if r["source_domain"] == "data.fca.org.uk")
    assert _corrob(row["source_domain"], row["source_class"],
                   row["query_executed"]) is True


def test_gold_arias_own_memory_never_corroborates_a_third_party_source():
    """Nine of these rows are `memory://` — ARIA's own store answering a `site:`
    query about someone else. Whatever else is true, ARIA cannot be her own evidence
    that ICIJ or the DOJ was screened."""
    for row in CHEMRING_ROWS:
        if not row["source_url"].startswith("memory://"):
            continue
        assert _corrob(row["source_domain"], row["source_class"],
                       row["query_executed"]) is not True, (
            f"{row['source_class']} credited to ARIA's own memory store")

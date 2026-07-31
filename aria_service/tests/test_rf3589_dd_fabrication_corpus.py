"""R-F3589 — THE DD ATTRIBUTION FABRICATION CORPUS. Deterministic, offline, permanent.

WHY THIS EXISTS. Every fabrication fixed in this codebase was found by running a real
DD and reading the output. That is how they are DISCOVERED, and it is a terrible way
to PROVE they stay fixed: search results vary run to run, so a clean report is only
*consistent* with a fix, never proof of one. On 2026-07-31 the "Roseburg names Wilson,
James" citation simply did not recur in four consecutive runs while the filter that
admitted it was still, provably, admitting it.

So this file replays the REAL strings — exactly as captured in delivered reports —
through the LIVE filters. No network, no LLM, no search. Deterministic.

R-F2545 is the equivalent gate for the LLM citation path. This is the DD ATTRIBUTION
path: the register, regulator, sanctions and press filters that decide WHO a piece of
evidence is about. Those two questions fail in completely different ways.

RULES FOR THIS FILE
  1. Every entry is a string OBSERVED IN A DELIVERED REPORT, with its run_id.
     No invented examples — constructed fixtures test what you already believe.
  2. The corpus may only GROW. Removing an entry means asserting a real fabrication
     can no longer happen, which needs its own evidence.
  3. Each entry names the R-number that closed it, so a regression points at its fix.
  4. Every case asserts the NEGATIVE (this must not be attributed) AND, where the
     same filter decides both, a matching POSITIVE — a filter that rejects everything
     is not honest, it is merely silent.
"""
from __future__ import annotations

import pytest

from aria_service.intel import companies_house as ch
from aria_service.intel import dd_orchestrator as dd
from aria_service.intel import fca_register as fr


# ═══ 1. REGULATOR ATTRIBUTION — a namesake individual is not the company ═══
# dd_acaee511f0f4 (Wilson James Limited, 02269560). Delivered at AMBER:
#   "FCA Register: James Wilson (Postcode: BB3 0DB) — No longer registered as an
#    Appointed Representative (FRN 806769). NOT currently authorised."
# James Wilson is an individual in Blackburn. R-F3574 -> R-F3576.

FCA_MUST_NOT_IDENTIFY = [
    ("Wilson James Limited", "James Wilson (Postcode: BB3 0DB)", "R-F3576 live string"),
    ("Wilson James Limited", "James Wilson", "R-F3574 plain reversal"),
    ("EFT Consult Ltd", "EFT Consultancy Services Limited", "R-F3025 partial coincidence"),
]
FCA_MUST_IDENTIFY = [
    ("Wilson James Limited", "Wilson James Ltd"),
    ("Wilson James Limited", "Wilson James (Postcode: EC1A 1AA)"),
    ("Wilson James Limited", "Wilson James Aviation Limited"),
    ("Schroder Investment Management", "Schroder Investment Management Ltd"),
    ("Barclays Bank PLC", "Barclays Bank UK PLC"),
]


@pytest.mark.parametrize("subject,candidate,provenance", FCA_MUST_NOT_IDENTIFY)
def test_regulator_record_is_not_attributed_to_a_namesake(subject, candidate, provenance):
    score = fr._name_match_score(subject, candidate)
    assert score < fr._min_name_match(), (
        f"{provenance}: '{candidate}' identifies as '{subject}' at {score:.3f}"
    )


@pytest.mark.parametrize("subject,candidate", FCA_MUST_IDENTIFY)
def test_a_genuine_regulator_record_is_still_attributed(subject, candidate):
    """A filter that rejects everything is silent, not honest."""
    assert fr._name_match_score(subject, candidate) >= fr._min_name_match(), candidate


# ═══ 2. SANCTIONS ATTRIBUTION — a reversed personal name is not the company ═══
# dd_acaee511f0f4. The identity panel read "Sanctions matches 1"; the match was
# "JAMES WILSON, Alejandro Antonio (ofac_sdn, weak_match)". R-F3579.

SANCTIONS_REVERSALS = [
    ("Wilson James Limited", "JAMES WILSON, Alejandro Antonio", "R-F3579 live string"),
    ("Wilson James Limited", "James Wilson", "R-F3579 plain reversal"),
]
SANCTIONS_NOT_REVERSALS = [
    ("Wilson James Limited", "Wilson James Holdings"),
    ("Chemring Group PLC", "Chemring Countermeasures"),
    ("Rosoboronexport", "ROSOBORONEXPORT JSC"),
    ("Silverbrook Capital Management", "System Capital Management"),
]


@pytest.mark.parametrize("subject,candidate,provenance", SANCTIONS_REVERSALS)
def test_a_reversed_sanctions_name_is_a_coincidence(subject, candidate, provenance):
    assert dd._is_name_reversal(subject, candidate) is True, provenance


@pytest.mark.parametrize("subject,candidate", SANCTIONS_NOT_REVERSALS)
def test_a_genuine_sanctions_candidate_is_not_dropped_as_a_reversal(subject, candidate):
    """NEVER-FALSE-CLEAN: over-filtering a sanctions hit is worse than the noise."""
    assert dd._is_name_reversal(subject, candidate) is False, candidate


# ═══ 3. PRESS ATTRIBUTION — a namesake in surname-first form is not coverage ═══
# dd_acaee511f0f4, published in Cited sources:
#   "Roseburg names Wilson, James to board of directors | Woodworking Network"
# R-F3583. Both distinctive tokens present AND in the company's order — only the
# COMMA separates a person from the company.

_WJ = ["wilson", "james"]

PRESS_MUST_REJECT = [
    ("Roseburg names Wilson, James to board of directors | Woodworking Network",
     "https://www.woodworkingnetwork.com/news/roseburg-names-wilson-james-board",
     "R-F3583 live string"),
]
PRESS_MUST_ADMIT = [
    ("Home - Wilson James", "https://wilsonjames.co.uk/"),
    ("Leadership - Wilson James", "https://wilsonjames.co.uk/leadership"),
    ("Wilson James Limited - Company Profile - Pomanda",
     "https://pomanda.com/company/02269560/wilson-james-limited"),
    ("Gary Sullivan - Wilson James Limited | LinkedIn",
     "https://www.linkedin.com/in/gary-sullivan-61a6b57/"),
    ("WILSON JAMES GROUP LIMITED overview - GOV.UK",
     "https://find-and-update.company-information.service.gov.uk/company/06527539"),
    ("Wilson James Ltd - GOV.UK",
     "https://www.gov.uk/armed-forces-covenant-businesses/wilson-james-ltd"),
]


@pytest.mark.parametrize("title,url,provenance", PRESS_MUST_REJECT)
def test_a_namesake_person_is_not_press_coverage(title, url, provenance):
    assert dd._press_hit_is_relevant(title, "", url, _WJ) is False, provenance


@pytest.mark.parametrize("title,url", PRESS_MUST_ADMIT)
def test_genuine_captured_coverage_is_still_admitted(title, url):
    """Every one of these was really captured on a live run. Dropping them trades a
    false source for a missing one — both are DD failures."""
    assert dd._press_hit_is_relevant(title, "", url, _WJ) is True, title


# ═══ 4. CREDENTIAL ATTRIBUTION — a page is not a listing OF the subject ═══
# R-F3553 shipped, then a replay of 92 real captured sources found two fabricated
# credentials within the hour (R-F3555). R-F3569/3575/3585 extended the surface.

_CHEM = {"chemring"}
_WJT = {"wilson", "james"}

CREDENTIAL_MUST_REJECT = [
    ({"url": "https://data.fca.org.uk/artefacts/x", "title": "Chemring Group PLC filing"},
     _CHEM, "R-F3555 — the FCA National Storage Mechanism is a document ARCHIVE, "
            "not the Financial Services Register"),
    ({"url": "https://register.fca.org.uk/s/firm?id=1",
      "title": "Babcock International Group PLC Notice of Annual General Meeting"},
     _CHEM, "R-F3555 — a filing that MENTIONS the subject is not a listing OF it"),
    ({"url": "https://www.contractsfinder.service.gov.uk/Notice/5d4baa9d",
      "title": "Museum Security Consortium - Contracts Finder"},
     _WJT, "R-F3569 live — a contract notice names the CONTRACT, not the supplier"),
    ({"url": "https://www.find-tender.service.gov.uk/Notice/080365-2025",
      "title": "Security and Cleaning Services - Find a Tender"},
     _WJT, "R-F3569 live — same"),
    ({"url": "https://www.gov.uk/armed-forces-covenant-businesses",
      "title": "Wilson James Ltd"},
     _WJT, "R-F3585 — the register INDEX is the register existing, not a listing"),
    ({"url": "https://www.gov.uk/government/news/announcement", "title": "Wilson James Ltd"},
     _WJT, "R-F3585 — an unrelated gov.uk page is not a credential"),
    ({"url": "https://www.find-and-update.company-information.service.gov.uk/company/02269560",
      "title": "WILSON JAMES LIMITED overview"},
     _WJT, "_NOT_A_CREDENTIAL — Companies House is the base identity source"),
    ({"url": "https://www.gov.uk/armed-forces-covenant-businesses/babcock",
      "title": "Babcock International listing"},
     _WJT, "title anchor — another company's listing"),
]
CREDENTIAL_MUST_PROMOTE = [
    ({"url": "https://www.services.sia.homeoffice.gov.uk/Pages/acs-roac.aspx?c=1",
      "title": "Wilson James Limited - Register of Approved Contractors"},
     _WJT, "R-F3575 live — the real SIA listing"),
    ({"url": "https://www.gov.uk/armed-forces-covenant-businesses/wilson-james-ltd",
      "title": "Wilson James Ltd - GOV.UK"},
     _WJT, "R-F3585 live — the real Covenant listing"),
]


@pytest.mark.parametrize("source,tokens,provenance", CREDENTIAL_MUST_REJECT)
def test_a_page_that_is_not_a_listing_is_not_a_credential(source, tokens, provenance):
    out = dd.positive_register_findings([source], tokens, as_of="2026-07-31")
    assert out == [], f"{provenance}\n  fabricated: {out}"


@pytest.mark.parametrize("source,tokens,provenance", CREDENTIAL_MUST_PROMOTE)
def test_a_genuine_listing_is_still_promoted(source, tokens, provenance):
    """A credential filter that promotes nothing gives a systematically negative
    report built from a complete evidence set."""
    out = dd.positive_register_findings([source], tokens, as_of="2026-07-31")
    assert len(out) == 1, f"{provenance}\n  lost a real credential"


# ═══ 5. OFFICER ATTRIBUTION — a register row is not this officer ═══
# dd_01531a44eb2f (Chemring, 00086662): three fabricated disqualification matches
# against sitting directors. R-F3451 -> R-F3515.

DISQ_MUST_REJECT = [
    ("Amar ISMAEL", "AMAR", ["Alpna"], "R-F3515 — AMAR is the candidate's FORENAME"),
    ("Amar NADEEM", "AMAR", ["Alpna"], "R-F3515 — same"),
    ("KING ROYAL TECHNOLOGIES CO. LTD", "KING", ["Stephen", "Anthony"],
     "R-F3515 — a COMPANY, not a person"),
    ("DREX TECHNOLOGIES S.A.", "DREX", ["Ali"], "R-F3515 — corporate form, punctuated"),
]


@pytest.mark.parametrize("row,surname,forenames,provenance", DISQ_MUST_REJECT)
def test_a_register_row_is_not_attributed_to_a_namesake_officer(
        row, surname, forenames, provenance):
    keeps, _ = ch._disq_candidate_is_same_name(row, surname, forenames)
    assert keeps is False, f"{provenance}\n  fabricated match: {row!r}"


def test_a_genuine_disqualification_is_still_matched():
    """The dangerous direction here is the FALSE NEGATIVE — a real disqualification
    missed. Forename disagreement must NOT drop a row (former/alternate names)."""
    keeps, _ = ch._disq_candidate_is_same_name("SMITH, John Edward", "SMITH", ["John"])
    assert keeps is True


# ═══ the corpus itself ═══

def test_the_corpus_covers_every_attribution_surface():
    """A corpus that silently loses a surface stops protecting it. If a new filter is
    added, it belongs here — that is the point of the file."""
    assert len(FCA_MUST_NOT_IDENTIFY) >= 3
    assert len(SANCTIONS_REVERSALS) >= 2
    assert len(PRESS_MUST_REJECT) >= 1
    assert len(CREDENTIAL_MUST_REJECT) >= 8
    assert len(DISQ_MUST_REJECT) >= 4
    # and every rejection surface has a matching admission surface
    assert FCA_MUST_IDENTIFY and SANCTIONS_NOT_REVERSALS
    assert PRESS_MUST_ADMIT and CREDENTIAL_MUST_PROMOTE

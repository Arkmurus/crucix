"""R-F3574 / R-F3575 — two live defects found by running a real DD.

Both were found on `dd_acaee511f0f4`, a deep DD of **Wilson James Limited** (reg
02269560, a London security contractor) — a deliberately NEW subject, run end to end.
Neither was visible to any unit test.

R-F3574 — A NAME REVERSAL ACCUSED THE WRONG PARTY.
The report carried, at AMBER:
    "FCA Register: James Wilson (Postcode: BB3 0DB) — No longer registered as an
     Appointed Representative (FRN 806769). NOT currently authorised."
James Wilson is an individual in Blackburn; the subject is a company in London.
R-F3025's 0.75 identification threshold could not stop it, because
`_name_match_score` is a SET intersection: {wilson, james} & {james, wilson} is the
full set, so a reversed name scored a PERFECT 1.000. The gate worked exactly as
designed and the measure underneath it was blind.

R-F3575 — THE CREDENTIAL WAS FOUND AND THEN DISCARDED.
R-F3569's sweep located the genuine SIA Approved Contractor listing for the subject.
Zero credentials were promoted. Every hit came back on `www.services.sia...` and the
curated keys are bare hosts, so the exact-key lookup missed all four. The sweep used
substring containment and the consumer used an exact key: the two halves disagreed
about what a host is, and the strict half was the one deciding what the reader sees.
"""
from __future__ import annotations

from aria_service.intel import dd_orchestrator as dd
from aria_service.intel import fca_register as fr

_SIA = "https://www.services.sia.homeoffice.gov.uk/Pages/acs-roac.aspx?contractor=1"


# ── R-F3574 ───────────────────────────────────────────────────────────────────

def test_a_reversed_name_does_not_identify_a_firm():
    """PROVE RED: this scored 1.000 and produced a live AMBER accusation."""
    score = fr._name_match_score("Wilson James Limited", "James Wilson")
    assert score < fr._min_name_match(), (
        f"a reversed name still identifies at {score:.3f} "
        f"(threshold {fr._min_name_match()})"
    )


def test_the_same_order_still_identifies():
    """The fix must not break ordinary matching — that would trade a false
    accusation for a false unknown."""
    for cand in ("Wilson James Ltd", "Wilson James Limited", "WILSON JAMES LIMITED"):
        assert fr._name_match_score("Wilson James Limited", cand) >= fr._min_name_match(), cand


def test_ordinary_firm_names_are_unaffected():
    assert fr._name_match_score(
        "Schroder Investment Management", "Schroder Investment Management Ltd") >= 0.99


def test_a_permutation_stays_above_the_corroborated_floor():
    """A postcode match must still be able to identify a firm merely written in a
    different order — the penalty is for an UNCORROBORATED reversal."""
    s = fr._name_match_score("Wilson James Limited", "James Wilson")
    assert s > fr._MIN_NAME_MATCH_CORROBORATED, (
        "the penalty is so heavy that postcode corroboration can no longer rescue a "
        "genuine firm whose name is written in another order"
    )


def test_the_rf3025_partial_coincidence_is_still_rejected():
    """The earlier live defect must stay fixed."""
    assert fr._name_match_score(
        "EFT Consult Ltd", "EFT Consultancy Services Limited") < fr._min_name_match()


def test_a_candidate_with_extra_tokens_is_not_penalised_as_a_permutation():
    """Only a REORDERING is penalised; a superset is a different question that the
    set score and threshold already answer."""
    assert fr._name_match_score(
        "Wilson James Limited", "Wilson James Aviation Limited") >= fr._min_name_match()


# ── R-F3575 ───────────────────────────────────────────────────────────────────

def test_a_www_prefixed_register_host_is_recognised():
    """PROVE RED: with `www.` this returned nothing, so the SIA credential the sweep
    had already found was silently dropped."""
    out = dd.positive_register_findings(
        [{"url": _SIA, "title": "Wilson James Limited - Register of Approved Contractors",
          "snippet": "ACS approved"}],
        {"wilson", "james"}, as_of="2026-07-31")
    assert len(out) == 1, f"a www-prefixed register host was not recognised: {out}"
    assert "SIA Approved Contractor Scheme" in out[0]["title"]


def test_the_bare_host_still_works():
    out = dd.positive_register_findings(
        [{"url": "https://services.sia.homeoffice.gov.uk/x",
          "title": "Wilson James Limited - Approved Contractor", "snippet": ""}],
        {"wilson", "james"}, as_of="2026-07-31")
    assert len(out) == 1


def test_a_port_does_not_defeat_the_host_match():
    out = dd.positive_register_findings(
        [{"url": "https://www.services.sia.homeoffice.gov.uk:443/x",
          "title": "Wilson James Limited - Approved Contractor", "snippet": ""}],
        {"wilson", "james"}, as_of="2026-07-31")
    assert len(out) == 1


def test_the_not_a_credential_exclusion_survives_normalisation():
    """Companies House is the base identity source, not an achievement — and the
    exclusion must not be bypassed by a `www.` prefix."""
    out = dd.positive_register_findings(
        [{"url": "https://www.find-and-update.company-information.service.gov.uk/company/02269560",
          "title": "WILSON JAMES LIMITED overview", "snippet": ""}],
        {"wilson", "james"}, as_of="2026-07-31")
    assert out == [], "Companies House was promoted as a credential"


def test_a_contract_notice_is_still_not_a_listing_of_the_company():
    """Real captured rows from the live run: Contracts Finder / Find a Tender titles
    name the CONTRACT, not the subject, so the title anchor must reject them."""
    out = dd.positive_register_findings(
        [{"url": "https://www.contractsfinder.service.gov.uk/Notice/5d4baa9d",
          "title": "Museum Security Consortium - Contracts Finder", "snippet": ""},
         {"url": "https://www.find-tender.service.gov.uk/Notice/080365-2025",
          "title": "Security and Cleaning Services - Find a Tender", "snippet": ""}],
        {"wilson", "james"}, as_of="2026-07-31")
    assert out == [], f"a contract notice was credited as a register listing: {out}"

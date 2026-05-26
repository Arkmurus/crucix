"""R-F894 — verifier counts bare-domain [from <domain>] citations.

Live 2026-05-26: after R-F888 fixed search, "who is the current US president?"
returned the correct answer with 12 inline citations ([from whitehouse.gov/...],
[from govtrack.us: "..."]) — but the footer showed "Sources: 0 grounded /
NO_CITATIONS" and the officeholder guard FALSELY demoted it to UNCERTAIN,
because extract_urls only matched http(s):// URLs, not bare domains. Now bare
domains inside [from …] markers are captured + grounded against the tool_context.
"""
from __future__ import annotations

from aria_service.intel import source_verifier as sv


def test_bare_domain_citations_captured():
    cites = sv.extract_urls(
        "Trump is the 47th President [from whitehouse.gov/administration/donald-j-trump] "
        "[from govtrack.us: \"Trump is President\"] [from factually.co]."
    )
    assert any("whitehouse.gov" in c for c in cites)
    assert any("govtrack.us" in c for c in cites)
    assert any("factually.co" in c for c in cites)


def test_prose_ref_without_tld_not_captured():
    # "[from Britannica biography]" + "[from snippet #N]" have no dotted TLD
    cites = sv.extract_urls("As noted [from Britannica biography] and [from snippet #3].")
    assert not any("britannica" in c.lower() for c in cites)


def test_well_sourced_answer_grounds_not_no_citations():
    resp = ("Donald Trump is the 47th President [from whitehouse.gov/administration/donald-j-trump], "
            "term to 2029 [from govtrack.us: \"...\"].")
    ctx = ("Search results: https://www.whitehouse.gov/administration/donald-j-trump "
           "https://www.govtrack.us/congress/members")
    res = sv.verify_response(resp, ctx)
    assert res["verdict"] in ("grounded", "partial")
    assert len(res["grounded"]) >= 2
    assert res["verdict"] != "no_citations"


def test_fabricated_bare_domain_is_unverified_not_grounded():
    # a domain the LLM cited that is NOT in the tool context → unverified (honest)
    resp = "X is true [from totally-made-up-source.example/page]."
    ctx = "Search results: https://www.whitehouse.gov/x"
    res = sv.verify_response(resp, ctx)
    assert any("made-up-source" in u for u in res["unverified"])
    assert not res["grounded"]


def test_http_urls_still_work():
    res = sv.verify_response(
        "See [from https://www.govtrack.us/x].",
        "https://www.govtrack.us/x is a result",
    )
    assert res["verdict"] in ("grounded", "partial")

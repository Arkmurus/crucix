"""R-F2540 — citation_verifier capability tests.

Prove the deterministic guarantee: a citation that grounds in the evidence is
kept; a fabricated one (not in the evidence) is flagged/dropped, and grounding_reward
then counts ZERO fabricated citations on the cleaned answer.
"""
from __future__ import annotations

from aria_service.intel import citation_verifier as cv
from aria_service.intel import grounding_reward as gr

# Context with two real source labels (production "↳ source:" format).
_CTX = (
    "• [1.02] web_search: sanctions\n  ↳ source: ofac.treasury.gov | 2026-05-01\n"
    "• [2.01] registry: filing\n  ↳ source: companies_house_uk | 2026-04-10\n"
    "The entity was designated by OFAC and is registered in the UK."
)


def test_real_citation_kept():
    ans = "The entity is OFAC-designated [Source: ofac.treasury.gov]."
    r = cv.verify_and_clean(ans, _CTX)
    assert r["clean"] is True
    assert r["fabricated_removed"] == 0
    assert "[Source: ofac.treasury.gov]" in r["answer"]


def test_fabricated_citation_flagged():
    ans = "The entity is linked to arms trafficking [Source: interpol_rednotice_2026]."
    r = cv.verify_and_clean(ans, _CTX)
    assert r["clean"] is False
    assert r["fabricated_removed"] == 1
    assert "interpol_rednotice_2026" in r["dropped"]
    assert "[unverified]" in r["answer"]
    assert "interpol_rednotice" not in r["answer"]  # the fabricated source is gone


def test_drop_mode_removes_marker():
    ans = "Claim X [from madeup_source]."
    r = cv.verify_and_clean(ans, _CTX, mode="drop")
    assert r["fabricated_removed"] == 1
    assert "madeup_source" not in r["answer"]
    assert "[unverified]" not in r["answer"]


def test_cleaned_answer_scores_zero_fabrication():
    """The whole point: after verification, grounding_reward sees 0 fabricated citations."""
    ans = ("OFAC designated it [Source: ofac.treasury.gov] and it ships arms "
           "[Source: fabricated_intel_feed].")
    before = gr.score(ans, _CTX)
    cleaned = cv.verify_and_clean(ans, _CTX)["answer"]
    after = gr.score(cleaned, _CTX)
    assert before.fabricated_citations >= 1        # the fabricated source was there
    assert after.fabricated_citations == 0         # ...and is gone after verification
    assert after.citation_precision == 1.0         # every surviving citation grounds


def test_mixed_keeps_real_flags_fake():
    ans = ("Designated by OFAC [Source: ofac.treasury.gov]; also indicted "
           "[Source: nonexistent_court_doc].")
    r = cv.verify_and_clean(ans, _CTX)
    assert r["fabricated_removed"] == 1
    assert "ofac.treasury.gov" in " ".join(r["kept"])
    assert "[Source: ofac.treasury.gov]" in r["answer"]
    assert "nonexistent_court_doc" not in r["answer"]

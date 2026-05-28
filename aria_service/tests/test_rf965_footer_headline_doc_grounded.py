"""R-F965 — footer HEADLINE is honest for document-grounded reviews.

R-F885 fixed the proof line ("attached document (grounded)" not "from memory /
training"). But the HEADLINE still read "Sources: 0 grounded / 0 unverified (—)
· Verification: NO_TOOL" on a full document-grounded contract review (live
2026-05-28) — because the verification dict counts EXTERNAL source citations,
which don't apply to a document the operator handed us. "0 grounded / NO_TOOL"
reads as "ungrounded", underselling the work. R-F965 leads the headline with the
document as the grounding when document_grounded is set.
"""
from __future__ import annotations

import os

os.environ.setdefault("ARIA_CONFIDENCE_FOOTER", "1")
from aria_service.intel import confidence_footer as cf  # noqa: E402

_REVIEW = (
    "Clause 6.2 — the Logistics Service Fee is not fixed. "
    "[from ATTACHED DOCUMENT: Supp.Agreement] Governing law is absent; recommend "
    "English law + LCIA London. Compliance obligations (UK Bribery Act, OFSI) are missing."
)


def test_rf965_headline_grounded_in_document_not_zero_grounded():
    # doc-grounded review with the typical "no external sources" verification dict
    v = {"cited": 0, "unverified": 0, "verdict": "NO_TOOL", "grounded_rate": None}
    f = cf.build_footer(_REVIEW, verification=v, tools_used=None,
                        build_rev="R-F965", document_grounded=True)
    assert "Grounded in:* attached document" in f, "headline must credit the document"
    # the misleading external-source framing must NOT appear for a doc review
    assert "0 grounded" not in f
    assert "Verification:* NO_TOOL" not in f


def test_rf965_doc_grounded_plus_external_sources_shows_both():
    # if she ALSO cited external sources, surface those alongside the document
    v = {"cited": 3, "unverified": 1, "verdict": "PASS", "grounded_rate": 0.66}
    f = cf.build_footer(_REVIEW, verification=v, tools_used=None,
                        build_rev="R-F965", document_grounded=True)
    assert "Grounded in:* attached document" in f
    assert "external grounded" in f  # 2 external grounded / 1 unverified


def test_rf965_non_doc_verification_headline_unchanged():
    # regression: a normal (non-doc) verified answer still shows the Sources line
    v = {"cited": 5, "unverified": 1, "verdict": "PASS", "grounded_rate": 0.8}
    f = cf.build_footer("NATO Category A export assessment with sources.",
                        verification=v, tools_used=["web_search"],
                        build_rev="R-F965", document_grounded=False)
    assert "*Sources:* 4 grounded / 1 unverified (80%)" in f
    assert "*Verification:* PASS" in f
    assert "Grounded in:* attached document" not in f

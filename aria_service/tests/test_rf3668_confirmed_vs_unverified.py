"""R-F3668 — one sentence may not be both [unverified] and CONFIRMED.

LIVE (2026-08-03, WhatsApp). ARIA shipped, repeatedly in one reply:

    "The LLM chain serving this reply is resilient=True, with deepseek as the
     serving provider and deepseek_backup as an active fallback
     [unverified] — CONFIRMED."

...under a footer reading "Confidence: 60% [ASSESSED] · Sources: 0 grounded /
0 unverified (0%) · Verification: UNGROUNDED".

Nothing in the tree appends "— CONFIRMED": the MODEL self-tags it, because the
constitution instructs it to tag material claims [CONFIRMED]/[PROBABLE]/etc.
Meanwhile claim_grounding independently measured the figures as ungrounded and
appended " [unverified]". Both labels survived into the answer, so the reader
had to guess which was true — and the more confident one is the one that
carries.

Doctrine: "Evidence owns truth. Models propose; deterministic verification and
sourced evidence dispose." So on a sentence THIS module has just measured as
ungrounded, the model's certainty tag is downgraded rather than left standing.
"""
from __future__ import annotations

from aria_service.intel import claim_grounding as cg


def _flag(answer: str, context: str = "", message: str = "") -> str:
    return cg.ground_claims(answer, context, message=message, mode="flag")["answer"]


def test_rf3668_confirmed_is_downgraded_on_an_ungrounded_sentence():
    """The live symptom, reproduced end-to-end through the real entry point."""
    # NB: the figure must be one _FIGURE_RE actually recognises — currency,
    # magnitude words, comma-separated counts or a percentage. A bare integer
    # ("98") is deliberately not a "figure" in this module, so it would never
    # have been flagged and would not exercise the contradiction at all.
    answer = (
        "My stores hold 435,358 knowledge facts and 663,871 RAG chunks "
        "— CONFIRMED."
    )
    out = _flag(answer, context="")
    assert "[unverified]" in out, "the ungrounded figure must still be flagged"
    assert "CONFIRMED" not in out.replace("UNVERIFIED", ""), (
        f"a sentence cannot be unverified AND confirmed:\n  {out}"
    )
    assert "UNVERIFIED" in out


def test_rf3668_bracketed_form_is_also_downgraded():
    out = _flag("Grounding is running at 92.4% coverage [CONFIRMED].")
    assert "[unverified]" in out
    assert "[CONFIRMED]" not in out


def test_rf3668_content_is_preserved_only_the_label_changes():
    """Never destructive — the claim itself must survive (this module's own
    contract: 'flagged, never deleted')."""
    out = _flag("I booted 334 seconds ago — CONFIRMED.")
    assert "334" in out, "the figure must not be removed"
    assert "booted" in out


def test_rf3668_grounded_sentences_keep_their_confirmed_tag():
    """The downgrade applies ONLY where grounding failed. A figure present in
    the retrieved context is grounded, so its label stands untouched."""
    ctx = "The engine has 98 scheduled tasks loaded and is running at level 3."
    out = _flag("The autonomy engine has 98 scheduled tasks — CONFIRMED.", context=ctx)
    assert "[unverified]" not in out
    assert "CONFIRMED" in out


def test_rf3668_citation_provenance_is_not_rewritten():
    """`[from RAG — CONFIRMED]` is a provenance label owned by source_verifier,
    not a model self-claim. A greedy regex would corrupt real citations."""
    assert cg._downgrade_confirmed("x [from RAG — CONFIRMED]") == \
        "x [from RAG — CONFIRMED]"


def test_rf3668_model_cannot_self_exempt_from_grounding():
    """The deeper hole: "[confirmed" used to sit in _CITED_MARKERS, so a
    sentence the MODEL tagged [CONFIRMED] was treated as already-cited and never
    checked. Certainty is the claim under test, never the evidence for it."""
    assert "[confirmed" not in cg._CITED_MARKERS, (
        "a model-asserted [CONFIRMED] must not exempt a sentence from grounding"
    )
    assert "[from " in cg._CITED_MARKERS and "[source:" in cg._CITED_MARKERS, (
        "real citations must still exempt"
    )


def test_rf3668_measure_mode_still_alters_nothing():
    """measure mode must remain non-destructive — it only counts."""
    answer = "The chain is resilient with 2 providers — CONFIRMED."
    r = cg.ground_claims(answer, "", mode="measure")
    assert r["answer"] == answer
    assert "CONFIRMED" in r["answer"]

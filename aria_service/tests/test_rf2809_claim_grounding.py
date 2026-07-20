"""R-F2809 — brain-central CLAIM grounding (north star P1). Deterministic
ungrounded-FIGURE detection: a specific figure asserted with no citation, no
hedge, not a derivation/hypothetical, and absent from BOTH the evidence and the
user's question is an ungrounded numeric claim → flagged (never deleted).

Pins BOTH precision (must NOT flag legitimate content — the classes calibration
found on real answers) AND recall (MUST catch a clear invented figure).
"""
from __future__ import annotations

from aria_service.intel import claim_grounding as cg

_CTX = (
    "[RAG RETRIEVED]\n"
    "- Company revenue was $250 million in 2024. [Source: sec:10k]\n"
    "- The deal value is £4.2M at a 5% commission rate. [Source: mem0:deal]\n"
)


# ── RECALL: a clear invented figure must be flagged ──────────────────────────

def test_flags_invented_figure_not_in_evidence():
    ans = "The company's annual revenue is $847 million."   # 847 not in ctx/question
    r = cg.ground_claims(ans, _CTX, message="what is the revenue?", mode="flag")
    assert r["ungrounded_sentences"] == 1
    assert any("847" in f for f in r["ungrounded_figures"])
    assert "[unverified]" in r["answer"]           # flagged, non-destructive
    assert "847 million" in r["answer"]            # claim text KEPT


def test_measure_mode_never_alters_text():
    ans = "The company's annual revenue is $847 million."
    r = cg.ground_claims(ans, _CTX, message="", mode="measure")
    assert r["ungrounded_sentences"] == 1          # counted
    assert r["answer"] == ans                       # but text unchanged


# ── PRECISION: the false-positive classes calibration found on real answers ──

def test_grounded_figure_from_evidence_not_flagged():
    ans = "Company revenue was $250 million."       # 250 IS in ctx
    assert cg.ground_claims(ans, _CTX, mode="flag")["ungrounded_sentences"] == 0


def test_cited_figure_not_flagged():
    ans = "The figure is $999 billion [Source: sec:10k]."   # cited → grounded
    assert cg.ground_claims(ans, _CTX, mode="flag")["ungrounded_sentences"] == 0


def test_figure_from_user_question_not_flagged():
    # The user's scenario carries the figure — restating it is not fabrication.
    ans = "A 50% deposit via an irregular route matches the red flags."
    r = cg.ground_claims(ans, _CTX, message="client wants to pay a 50% deposit", mode="flag")
    assert r["ungrounded_sentences"] == 0


def test_derivation_not_flagged():
    ans = "Therefore, the updated commission projection is £210,000."   # derived
    assert cg.ground_claims(ans, _CTX, mode="flag")["ungrounded_sentences"] == 0


def test_hypothetical_example_not_flagged():
    ans = "The context has no mandate fee, such as a fee of USD 50,000."  # example
    assert cg.ground_claims(ans, _CTX, mode="flag")["ungrounded_sentences"] == 0


def test_hedged_figure_not_flagged():
    ans = "Revenue is approximately $900 million, unverified in this turn."
    assert cg.ground_claims(ans, _CTX, mode="flag")["ungrounded_sentences"] == 0


def test_no_figure_sentence_untouched():
    ans = "This entity presents an elevated compliance risk."
    r = cg.ground_claims(ans, _CTX, mode="flag")
    assert r["ungrounded_sentences"] == 0
    assert r["answer"] == ans


def test_off_mode_is_noop_and_never_raises():
    assert cg.ground_claims("$847 million", _CTX, mode="off")["answer"] == "$847 million"
    assert cg.ground_claims(None, None, mode="flag")["ungrounded_sentences"] == 0


# ── CAPABILITY: drive the real model_router._verify_grounded chokepoint ───────
# (both complete_synthesis (chat) and stream_synthesis (chat_stream) call this,
#  so §13 is satisfied by construction — one function, both paths inherit it.)
_GROUNDED_CTX = _CTX + ("\n- background note. " * 40)   # long enough to be a grounded turn


def test_verify_grounded_default_off_is_inert(monkeypatch):
    from aria_service.llm import model_router as mr
    monkeypatch.delenv("ARIA_CLAIM_GROUNDING", raising=False)   # default OFF
    ans = "The company's revenue is $847 million."             # ungrounded figure
    assert mr._verify_grounded(ans, _GROUNDED_CTX, "what is revenue?") == ans


def test_verify_grounded_flag_mode_flags_ungrounded_figure(monkeypatch):
    from aria_service.llm import model_router as mr
    monkeypatch.setenv("ARIA_CLAIM_GROUNDING", "flag")
    ans = "The company's revenue is $847 million."
    out = mr._verify_grounded(ans, _GROUNDED_CTX, "what is revenue?")
    assert "[unverified]" in out and "847 million" in out       # flagged, claim kept


def test_verify_grounded_measure_mode_does_not_alter(monkeypatch):
    from aria_service.llm import model_router as mr
    monkeypatch.setenv("ARIA_CLAIM_GROUNDING", "measure")
    ans = "The company's revenue is $847 million."
    assert mr._verify_grounded(ans, _GROUNDED_CTX, "what is revenue?") == ans   # observed, not altered


def test_verify_grounded_keeps_grounded_and_cited_figures(monkeypatch):
    from aria_service.llm import model_router as mr
    monkeypatch.setenv("ARIA_CLAIM_GROUNDING", "flag")
    ans = "Company revenue was $250 million and the deal is £4.2M."   # both in ctx
    assert "[unverified]" not in mr._verify_grounded(ans, _GROUNDED_CTX, "")

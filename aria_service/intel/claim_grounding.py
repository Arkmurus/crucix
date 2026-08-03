"""claim_grounding — deterministic CLAIM-level grounding (R-F2809, north star P1).

Extends the R-F2540 source verifier from *citations* to *claims*. The verifier
guarantees no fabricated SOURCE reaches the user; this guarantees no fabricated
FIGURE does either. On a grounded turn, a sentence that asserts a specific figure
(currency / percentage / magnitude / thousands-separated count) whose digits do
NOT appear anywhere in the retrieved evidence — and which carries no citation and
no hedge — is an UNGROUNDED numeric claim (an invented statistic, the most
dangerous DD/compliance fabrication). It is FLAGGED `[unverified]` (never deleted).

Design (bulletproof, non-breaking):
  * Pure, deterministic, no LLM / no network. Never raises.
  * CONSERVATIVE grounding: a figure counts as grounded if its digit-core appears
    in the evidence digit-stream OR the figure string appears in the context. This
    errs toward NOT flagging (a real figure formatted differently is still
    grounded), so a legitimate answer is never mangled.
  * Non-destructive: flag-mode appends ` [unverified]` to the figure; the claim
    text always stays. Over-flagging only adds a visible caveat; it never removes
    information. Under-flagging is caught later as we tighten the detector.
  * Modes: "off" (no-op), "measure" (count only, DO NOT alter text), "flag".

Bare 4-digit years are intentionally NOT treated as figures (they appear
everywhere — source dates, incorporation dates — and would be high false-positive).
Start with financial/statistical figures, the clearest fabrication class.
"""
from __future__ import annotations

import re
from typing import Any

from .engine_wiring import wire_failure, wire_success

# Figures that represent specific factual assertions (a fabricated one is dangerous).
_FIGURE_RE = re.compile(
    r"(?:[$£€]\s?\d[\d,]*\.?\d*\s?(?:bn|billion|m|million|k|thousand|tn|trillion)?)"  # currency
    r"|(?:\b\d[\d,]*\.?\d*\s?(?:bn|billion|million|thousand|trillion)\b)"              # magnitude words
    r"|(?:\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b)"                                            # thousands-separated count
    r"|(?:\b\d[\d.]*\s?%)",                                                             # percentage
    re.IGNORECASE,
)

# R-F3668 — a real citation span. CONFIRMED *inside* one of these is provenance
# ("[from RAG — CONFIRMED]") owned by source_verifier, not a model self-claim, so
# it is masked out before the downgrade below. A lookbehind cannot express this:
# the text between "[from " and "CONFIRMED" varies.
_CITATION_SPAN = re.compile(r"\[(?:from|source:)[^\]]*\]", re.I)

# Model-authored certainty tag: the bare trailing "— CONFIRMED" and the
# bracketed "[CONFIRMED]" form.
_CONFIRMED_TAG = re.compile(r"\b\[?CONFIRMED\]?")


def _downgrade_confirmed(sent: str) -> str:
    """Neutralise a model's CONFIRMED tag while preserving real citations."""
    spans: list[str] = []

    def _stash(m: "re.Match[str]") -> str:
        spans.append(m.group(0))
        return f"\x00{len(spans) - 1}\x00"

    masked = _CITATION_SPAN.sub(_stash, sent)
    masked = _CONFIRMED_TAG.sub("UNVERIFIED", masked)
    for i, original in enumerate(spans):
        masked = masked.replace(f"\x00{i}\x00", original)
    return masked


# A sentence carrying any of these is already cited or honestly hedged → grounded.
# R-F3668: "[confirmed" was in this list, which meant a sentence the MODEL had
# tagged [CONFIRMED] was treated as already-cited and SKIPPED — the model could
# exempt itself from the grounding check simply by asserting certainty. That is
# exactly backwards: [CONFIRMED] is the claim under test, never the evidence for
# it. Real citations ("[source:", "[from ") and honest hedges ("[assessed",
# "[probable", "[unverified]", "[self-reported") still exempt, because those
# either carry provenance or already concede uncertainty.
_CITED_MARKERS = ("[source:", "[from ", "[unverified]", "[self-reported", "[assessed", "[probable")
_HEDGES = (
    "unverified", "unconfirmed", "not confirmed", "cannot confirm", "could not confirm",
    "no data", "insufficient", "estimate", "estimated", "approximately", "approx",
    "roughly", "about ", "pending", "unavailable", "not available", "no source",
    "unable to verify", "not verified", "may be", "reportedly", "alleged",
)
# Calibration on real answers showed the naive rule flags legitimate content. These
# are NOT fabrications and must not be flagged:
#  - DERIVATIONS — a figure computed from cited figures ("Therefore, the projection is X").
#  - HYPOTHETICALS / examples — a figure floated as an example, esp. in an abstention.
_DERIVATION_MARKERS = (
    "therefore", "thus", "hence", "so the", "so that", "total", "sum", "projection",
    "projected", "calculated", "equals", "= ", " x ", "×", "multiplied", "product of",
    "adds up", "combined", "aggregate", "net of", "at a ", "% rate", "% of",
)
_HYPOTHETICAL_MARKERS = (
    "or a ", "or an ", "such as", "for example", "e.g.", "for instance", "would be",
    "could be", "might be", "hypothetical", "if the", "if a ", "example", "say ",
)

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def _figure_grounded(fig: str, ctx_lower: str, ctx_digits: str) -> bool:
    """A figure is grounded if its digit-core (>=2 digits) is a substring of the
    evidence digit-stream, OR the figure string itself appears in the context.
    Lenient on purpose — real figures (even reformatted) stay grounded."""
    core = _digits(fig)
    if len(core) >= 2 and core in ctx_digits:
        return True
    f = fig.strip().lower()
    if f and f in ctx_lower:
        return True
    # currency/magnitude reformatting: "$75b" / "75 billion" share the core "75"
    if len(core) >= 2 and core in ctx_digits.replace(",", ""):
        return True
    return False


def _sentence_grounded(sentence: str, ctx_lower: str, ctx_digits: str,
                       msg_lower: str = "", msg_digits: str = "") -> tuple[bool, list[str]]:
    """(grounded, ungrounded_figures). A sentence is grounded when it has no figure,
    is cited, is hedged, is a derivation, or is a hypothetical/example. Otherwise
    every figure must be supported by the retrieved evidence OR the user's own
    message (figures the user stated in the question/scenario are not fabrications)."""
    s_low = sentence.lower()
    if any(m in s_low for m in _CITED_MARKERS):
        return True, []
    if any(h in s_low for h in _HEDGES):
        return True, []
    if any(m in s_low for m in _DERIVATION_MARKERS):
        return True, []          # computed from evidence — not a fabrication
    if any(m in s_low for m in _HYPOTHETICAL_MARKERS):
        return True, []          # floated as an example — not an assertion
    figs = [m.group(0) for m in _FIGURE_RE.finditer(sentence)]
    if not figs:
        return True, []
    ungrounded = [
        f for f in figs
        if not _figure_grounded(f, ctx_lower, ctx_digits)
        and not _figure_grounded(f, msg_lower, msg_digits)   # figure from the user's own question
    ]
    return (len(ungrounded) == 0), ungrounded


def ground_claims(answer: str, context: str, *, message: str = "", mode: str = "measure") -> dict[str, Any]:
    """Check every sentence's figures against the retrieved evidence (and the user's
    ``message`` — figures the user stated are not fabrications).

    mode="measure" -> DO NOT alter text; just count ungrounded-figure sentences.
    mode="flag"    -> append ` [unverified]` to each ungrounded-figure sentence.
    mode="off"     -> no-op.

    Returns {answer, ungrounded_sentences:int, ungrounded_figures:[...], clean:bool}.
    Never raises — on any error returns the input unchanged.
    """
    try:
        if mode == "off" or not answer:
            return {"answer": answer, "ungrounded_sentences": 0, "ungrounded_figures": [], "clean": True}
        ctx_lower = (context or "").lower()
        ctx_digits = _digits(context or "")
        msg_lower = (message or "").lower()
        msg_digits = _digits(message or "")
        out_parts: list[str] = []
        ungrounded_figs: list[str] = []
        n_ung = 0
        # Preserve original spacing by re-splitting on the same boundaries.
        for sent in _SENT_SPLIT.split(answer):
            if not sent:
                continue
            ok, ung = _sentence_grounded(sent, ctx_lower, ctx_digits, msg_lower, msg_digits)
            if not ok:
                n_ung += 1
                ungrounded_figs.extend(ung)
                if mode == "flag":
                    # R-F3668 — a sentence cannot be both unverified and
                    # CONFIRMED. Live 2026-08-03 ARIA shipped, repeatedly:
                    #   "...deepseek_backup as an active fallback
                    #    [unverified] — CONFIRMED."
                    # under a footer reading "Verification: UNGROUNDED,
                    # Sources: 0 grounded / 0 unverified (0%)".
                    #
                    # Nothing in the tree appends "— CONFIRMED"; the MODEL
                    # self-tags it (the constitution instructs it to tag claims
                    # [CONFIRMED]/[PROBABLE]/...), while this deterministic
                    # grounder independently found the figures ungrounded. Both
                    # labels then survived side by side, and the reader is left
                    # to guess which one is true.
                    #
                    # Doctrine is unambiguous — "models propose; deterministic
                    # verification disposes". So the model's self-assessment on
                    # THIS sentence is downgraded rather than left to contradict
                    # the measurement. The sentence keeps its content; only the
                    # unearned certainty label is neutralised.
                    sent = _downgrade_confirmed(sent)
                    sent = sent.rstrip() + " [unverified]"
            out_parts.append(sent)
        # Rejoin with single spaces (measure mode returns the ORIGINAL text unchanged).
        cleaned = answer if mode == "measure" else " ".join(out_parts)
        wire_success(
            module="claim_grounding",
            summary=f"claim grounding: {n_ung} ungrounded-figure sentence(s)",
            source_id="claim_grounding:ground_claims",
        )
        return {
            "answer": cleaned,
            "ungrounded_sentences": n_ung,
            "ungrounded_figures": ungrounded_figs,
            "clean": n_ung == 0,
        }
    except Exception as exc:
        # R-F3387 — this returned clean=True. A grounding check that crashed has
        # checked NOTHING, so "no ungrounded figures" is a false clean. Fail
        # closed; the degrade contract (return the answer, never raise) is
        # unchanged so model_router.py:527 cannot be broken by it.
        wire_failure(
            module="claim_grounding",
            detail=f"claim grounding failed, reporting NOT-clean: {type(exc).__name__}: {exc}"[:400],
            gap_type="engine_failure",
            source="claim_grounding:ground_claims",
        )
        return {"answer": answer, "ungrounded_sentences": 0, "ungrounded_figures": [], "clean": False}

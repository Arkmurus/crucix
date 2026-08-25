"""R-F4332 / C-279 — the open-book eval graded questions whose evidence supports nothing.

`eval_aria_llm._build_judge_prompt` decided grounding with one line:

    grounded = bool((context or "").strip())

NON-EMPTY was read as SUPPORTING. Measured on the live 500-Q open-book set,
the fraction of `expected_keywords` actually present in the supplied context:

    ALL 500 Q        mean 0.25   median 0.25
    multi_lang 110   mean 0.15   median 0.12
    the other 390    mean 0.28   median 0.25
    multi_lang with ZERO required keywords in context : 44 of 108
    multi_lang with under 34%                         : 93 of 108

And coverage PREDICTS the score — she passed at mean coverage 0.30 (n=153) and
failed at 0.23 (n=334).

WHY THAT MAKES 44 QUESTIONS UNWINNABLE. Once `grounded` is true the judge is
told: "If the evidence does NOT contain the answer, the candidate is 'correct'
ONLY if it honestly says so / abstains; a confident answer from absent evidence
-> 'wrong'." But the REFERENCE answer for those questions is a full factual
answer, and the same rubric grades on agreement with the reference. So:
  * answer confidently -> 'wrong' (ungrounded fabrication)
  * abstain honestly   -> disagrees with the reference
Both branches lose. The model is punished for the retriever's gap.

WHAT THE RETRIEVAL ACTUALLY RETURNS, measured on aria-intel. For
"France defence export control procedure Senegal CIEEMG licence" the store
returns 3,918 chars at 0.80 similarity of generic contract boilerplate
("Export Control and Compliance Clause (Defence)") containing 0 of the 4
graded keywords. An ENGLISH rephrasing scores the same 0/4, so this is NOT a
multilingual retrieval failure — the evidence is simply not in the store. That
is a SOURCE-COVERAGE gap (docs/aria_source_coverage_north_star_2026_07_14.md),
and it is a product gap too: in production she cannot answer these from
evidence either.

THE FIX IS TO MEASURE, NOT TO EXCUSE. This does not make the benchmark easier
and does not change a single question or expected answer — the frozen gate-#6
pin covers `id + question + expected_answer` only (eval_runner.golden_set_hash),
so nothing here touches it. It replaces a BOOLEAN that could not be wrong with
a tri-state that can:

    ungrounded  — no context at all. Closed-book rubric (unchanged, back-compat).
    grounded    — context genuinely supports the question. Strict rubric.
    unsupported — context present but supports none of it. The strict rubric
                  must NOT apply, and the result is MARKED so a report can
                  separate "failed with evidence" from "failed without any".

That third state is the whole point, and it is the §1 rule this repo already
applies to Phase A gates: "could not measure" is not "measured and failed".
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_TRAIN = ROOT / "scripts" / "train"
if str(_TRAIN) not in sys.path:
    sys.path.insert(0, str(_TRAIN))

import eval_aria_llm as E  # noqa: E402

# Verbatim shape of what the store returns for the French question: on-topic,
# high similarity, and containing none of the graded specifics.
BOILERPLATE = (
    "[RAG RETRIEVED] Export Control and Compliance Clause (Defence)\n"
    "EU Council Regulation (EC) No 428/2009 (Dual-Use Regulation); any applicable "
    "sanctions regime (OFAC, EU, UN Security Council, HMT). LICENCE RESPONSIBILITY: "
    "the party responsible for obtaining any required export licence shall be "
    "[Principal/Broker]. CONTROLLED GOODS: the parties acknowledge..."
) * 6
KEYWORDS = ["CIEEMG", "SBDU", "CUF", "Commission Interministérielle"]
SUPPORTING = (
    "Sous le régime français, l'exportation de matériel de guerre est autorisée par "
    "la CIEEMG (Commission Interministérielle pour l'Étude des Exportations de "
    "Matériels de Guerre), instruite par la SBDU, avec certificat d'utilisation "
    "finale (CUF)."
)


# -- evidence coverage --------------------------------------------------

def test_coverage_is_zero_for_topically_adjacent_boilerplate():
    """THE LIVE CASE. 3,918 chars at 0.80 similarity, 0 of 4 keywords."""
    assert E.evidence_coverage(BOILERPLATE, KEYWORDS) == 0.0


def test_coverage_is_high_for_evidence_that_answers_the_question():
    assert E.evidence_coverage(SUPPORTING, KEYWORDS) >= 0.75


def test_coverage_of_empty_context_is_zero():
    for empty in ("", "   ", None):
        assert E.evidence_coverage(empty, KEYWORDS) == 0.0


def test_coverage_without_keywords_is_unknown_not_zero():
    """A question with no expected_keywords cannot be scored for coverage.
    Returning 0.0 would silently reclassify it as unsupported."""
    assert E.evidence_coverage(SUPPORTING, []) is None
    assert E.evidence_coverage(SUPPORTING, None) is None


# -- THE CAPABILITY TEST: the three states ------------------------------

def test_boilerplate_context_is_unsupported_not_grounded():
    """THE DEFECT. `bool(context)` called this grounded, which switched on the
    strict rubric and made the question unwinnable."""
    assert E.grounding_mode(BOILERPLATE, KEYWORDS) == "unsupported"


def test_real_evidence_is_grounded():
    assert E.grounding_mode(SUPPORTING, KEYWORDS) == "grounded"


def test_no_context_is_ungrounded():
    assert E.grounding_mode("", KEYWORDS) == "ungrounded"
    assert E.grounding_mode(None, KEYWORDS) == "ungrounded"


def test_unknown_coverage_falls_back_to_grounded_for_back_compat():
    """With no keywords we cannot judge support. A non-empty context keeps the
    pre-R-F4332 behaviour rather than being demoted to 'unsupported' — the fix
    must not silently reclassify the 390 questions it was not written for."""
    assert E.grounding_mode(SUPPORTING, []) == "grounded"


# -- the rubric must follow the mode ------------------------------------

def test_the_strict_rubric_is_not_applied_to_unsupported_evidence():
    """The unwinnable trap: 'a confident answer from absent evidence -> wrong'
    while the REFERENCE is a full factual answer. Both branches lose."""
    system, user = E._build_judge_prompt(
        "En français : ... Sénégal ?", "CIEEMG ... SBDU ...", "une réponse",
        context=BOILERPLATE, expected_keywords=KEYWORDS)
    assert "FABRICATION" not in system, (
        "the strict grounding rubric is still applied to evidence that supports "
        "nothing — the model is punished for the retriever's gap"
    )


def test_the_strict_rubric_still_applies_to_real_evidence():
    """The guard must keep BITING where it was right. Fabrication against real
    evidence is exactly what R-F1676 exists to catch."""
    system, _ = E._build_judge_prompt(
        "q", "ref", "ans", context=SUPPORTING, expected_keywords=KEYWORDS)
    assert "FABRICATION" in system


def test_closed_book_rubric_is_unchanged():
    """Back-compat: no context behaves exactly as before."""
    system, user = E._build_judge_prompt("q", "ref", "ans", context="")
    assert "FABRICATION" not in system
    assert "EVIDENCE" not in user


def test_unsupported_evidence_is_still_shown_to_the_judge():
    """It must not be hidden — the judge should see what she actually had, or a
    genuinely fabricated citation becomes invisible."""
    _, user = E._build_judge_prompt(
        "q", "ref", "ans", context=BOILERPLATE, expected_keywords=KEYWORDS)
    assert "Export Control and Compliance Clause" in user


# -- the result must be attributable ------------------------------------

def test_the_mode_is_recorded_on_the_result():
    """A report that cannot separate 'failed with evidence' from 'failed with
    none' cannot tell a model problem from a source-coverage problem — which is
    exactly the confusion that cost three training cycles."""
    src = (ROOT / "scripts/train/eval_aria_llm.py").read_text(
        encoding="utf-8", errors="replace")
    assert "grounding_mode" in src
    assert '"grounding"' in src or "'grounding'" in src, (
        "the per-question result does not carry its grounding mode, so the "
        "unsupported questions stay invisible in the report"
    )

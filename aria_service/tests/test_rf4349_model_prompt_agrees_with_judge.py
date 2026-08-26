"""R-F4349 / C-294 — the model was told to answer ONLY from evidence the judge
already knew supported nothing.

MEASURED on the v0.8 cycle, 2026-08-26 (500-Q open-book, report
`aria_llm_v0_5_grounded_eval.json`):

    grounded      286   acc 0.353
    ungrounded     39   acc 0.410
    unsupported   175   acc 0.194   <- 35% of the benchmark

The 175 `unsupported` rows carry a context whose **median evidence_coverage is
0.000** — it contains none of the keywords the answer is graded on. Across the
whole eval the median is 0.250.

THE DEFECT IS A DISAGREEMENT BETWEEN TWO HALVES OF ONE DECISION, AND R-F4332
CREATED IT BY FIXING ONLY ONE OF THEM. That change replaced the judge's
`grounded = bool(context.strip())` with the tri-state `grounding_mode()`,
because "non-empty" is not "supporting". It did not touch the model-facing
prompt three lines above its own call site, which kept the identical boolean:

    context = (q.get("context") or "").strip()
    if context:                                   # <- the boolean R-F4332 killed
        prompt = "[CONTEXT — answer ONLY from this evidence; ...]"

So for those 175 questions:

  * the MODEL was instructed to answer only from the evidence, and obeyed by
    abstaining — the honest response to evidence that contains nothing;
  * the JUDGE, correctly, had already dropped the strict grounding rubric for
    them (mode `unsupported`) and graded factual agreement against a full
    reference answer;
  * so the abstention read as "empty or evasive" -> **wrong**.

She was punished for obeying an instruction that pointed at empty evidence.
That is not a model failure and no amount of curriculum can fix it: five
curricula (~64 rows) aimed squarely at this behaviour moved it by
gained 35 / lost 38, net -3, chi2 0.05, p>0.05.

WHAT THE FIX MUST NOT DO. The tempting version — drop the context for these
rows and let her answer closed-book — would hide the evidence from the judge
while its prompt still labels it "what the candidate was given", and R-F4332
kept that block visible on purpose: *"hiding it would make a genuinely
fabricated citation invisible to the judge."* So the evidence stays; what
changes is the INSTRUCTION, which must stop asserting that the answer is in
there when the same function the judge uses says it is not.

The invariant these tests defend is narrow and checkable: **the model's
instruction and the judge's rubric must be derived from the same
`grounding_mode()` call.** One definition of "supporting", used by both halves.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
HARNESS = ROOT / "scripts" / "train" / "eval_aria_llm.py"
REPORT = ROOT / "data" / "eval_reports" / "aria_llm_v0_5_grounded_eval.json"


@pytest.fixture(scope="module")
def ev():
    """Load the REAL harness by path — it lives in scripts/, not a package."""
    spec = importlib.util.spec_from_file_location("eval_aria_llm", HARNESS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


#: A context that is plainly on-topic and plainly does not contain the answer.
#: This is the shape retrieval actually returned: 3,918 chars of adjacent
#: boilerplate at 0.80 similarity carrying 0 of 4 keywords (R-F4332's note).
_ADJACENT_BOILERPLATE = (
    "The supplier shall maintain records of all transfers for a period of not "
    "less than seven years and make them available on request. Deliveries are "
    "subject to the general terms of the framework agreement. Invoicing follows "
    "the schedule set out in Annex B."
)
_KEYWORDS = ["rosoboronexport", "107,000", "faa", "angola"]

_ONLY_FROM = "answer ONLY from this evidence"


# ------------------------------------------------ THE CAPABILITY TEST

def test_the_model_is_not_told_the_answer_is_in_evidence_that_lacks_it(ev):
    """THE LIVE DEFECT, on the exact shape retrieval returns.

    `grounding_mode` calls this context `unsupported`; the model prompt must
    therefore not claim the answer is inside it.
    """
    assert ev.grounding_mode(_ADJACENT_BOILERPLATE, _KEYWORDS) == "unsupported"
    prompt = ev._build_model_prompt(
        "What is the current strength of the Angolan armed forces?",
        _ADJACENT_BOILERPLATE, _KEYWORDS)
    assert _ONLY_FROM not in prompt, (
        "the model is still ordered to answer only from evidence the judge has "
        "already classified as supporting nothing:\n" + prompt[:400])


def test_the_evidence_is_still_shown_to_the_model(ev):
    """DO NOT 'FIX' THIS BY DROPPING THE CONTEXT. R-F4332 kept the evidence
    visible on the judge side precisely so a fabricated citation stays
    detectable; removing it here would also make the judge's own
    'EVIDENCE (what the candidate was given)' label a lie."""
    prompt = ev._build_model_prompt("Q?", _ADJACENT_BOILERPLATE, _KEYWORDS)
    assert _ADJACENT_BOILERPLATE[:60] in prompt, (
        "the evidence was hidden from the model rather than described "
        "honestly:\n" + prompt[:400])


def test_a_genuinely_grounded_question_is_unchanged(ev):
    """THE HALF MOST AT RISK. 286 of 500 questions have real evidence, and the
    strict open-book instruction is what makes them a grounding test at all.
    Loosening those would trade a measurement defect for a weaker benchmark."""
    ctx = ("The Angolan armed forces (FAA) number approximately 107,000 "
           "personnel. [Source: registry:iiss]")
    assert ev.grounding_mode(ctx, _KEYWORDS) == "grounded"
    prompt = ev._build_model_prompt("How large is the FAA?", ctx, _KEYWORDS)
    assert _ONLY_FROM in prompt, (
        "a genuinely grounded question lost its strict instruction:\n" + prompt[:400])
    assert "[from <source>]" in prompt


def test_a_closed_book_question_stays_a_bare_question(ev):
    """39 rows have no context at all. They were never open-book and must not
    acquire a context block."""
    assert ev._build_model_prompt("Who is the UBO?", "", _KEYWORDS) == "Who is the UBO?"
    assert ev._build_model_prompt("Who is the UBO?", "   ", None) == "Who is the UBO?"


def test_unknown_coverage_keeps_the_strict_instruction(ev):
    """`evidence_coverage` returns None — not 0.0 — when there are no keywords
    to judge by, and `grounding_mode` maps that to 'grounded' so the 390
    questions R-F4332 was not written for are not silently reclassified. The
    model prompt must inherit that same conservative default, or this fix
    would quietly loosen hundreds of questions it never diagnosed.
    """
    assert ev.evidence_coverage(_ADJACENT_BOILERPLATE, None) is None
    assert ev.grounding_mode(_ADJACENT_BOILERPLATE, None) == "grounded"
    prompt = ev._build_model_prompt("Q?", _ADJACENT_BOILERPLATE, None)
    assert _ONLY_FROM in prompt, (
        "a question with unjudgeable coverage was loosened — that is a silent "
        "reclassification of the majority of the set")


# ------------------------------------ the invariant, stated as one rule

@pytest.mark.parametrize("cov_ctx,kws,expected_mode", [
    (_ADJACENT_BOILERPLATE, _KEYWORDS, "unsupported"),
    ("FAA numbers 107,000 in Angola per rosoboronexport filings", _KEYWORDS, "grounded"),
    ("", _KEYWORDS, "ungrounded"),
    (_ADJACENT_BOILERPLATE, None, "grounded"),
])
def test_both_halves_derive_from_the_same_grounding_mode(ev, cov_ctx, kws, expected_mode):
    """THE ROOT INVARIANT. R-F4332 fixed the judge and left the model prompt on
    the old boolean, and nothing could detect the disagreement because no test
    compared them. This does.

    'Strict' means the same thing on both sides: the judge attaches its
    fabrication rubric exactly when the model is ordered to answer only from
    the evidence.
    """
    mode = ev.grounding_mode(cov_ctx, kws)
    assert mode == expected_mode

    model_strict = _ONLY_FROM in ev._build_model_prompt("Q?", cov_ctx, kws)
    judge_system, _ = ev._build_judge_prompt("Q?", "ref", "cand", cov_ctx, kws)
    judge_strict = "FABRICATION" in judge_system

    assert model_strict == judge_strict, (
        f"mode={mode}: model_strict={model_strict} but judge_strict="
        f"{judge_strict} — the two halves disagree about what counts as "
        f"evidence, which is the whole defect")


# ------------------------------------------- grounded in the real run

@pytest.mark.skipif(not REPORT.exists(), reason="no local v0.8 report")
def test_the_real_run_still_contains_the_population_this_was_written_for(ev):
    """Anchors the fix to the measurement rather than to a hypothetical.

    If this ever reads 0, the defect class is gone from the eval set and the
    fix has become inert — which is worth knowing, because a guard that can no
    longer fire should not be mistaken for a guard that is holding.
    """
    d = json.loads(REPORT.read_text(encoding="utf-8"))
    res = d["defence_dd"]["results"]
    unsupported = [r for r in res if r.get("grounding") == "unsupported"]
    assert len(res) == 500
    assert len(unsupported) > 100, (
        f"only {len(unsupported)} unsupported rows — re-read the report before "
        f"assuming this fix still has a population")
    zero_cov = [r for r in unsupported
                if isinstance(r.get("evidence_coverage"), (int, float))
                and r["evidence_coverage"] == 0.0]
    assert len(zero_cov) > 50, (
        f"only {len(zero_cov)} rows carry evidence with ZERO graded keywords")

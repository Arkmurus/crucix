"""R-F4339 / C-284 — the defence-ecosystem mastery curriculum, guarded on VALUE
DENSITY rather than on row count.

OPERATOR 2026-08-26: "she needs to be a mastery in security and defence
ecosystem therefore lets go deep and lets have 360 approach".

The 360 is taken from docs/golden_intel_north_star_2026_07_14.md, which defines
five customer jobs. Job 1 (export and sanctions protection) is covered by
build_export_control_curriculum.py; this covers jobs 2-5, which had no
curriculum at all:

    2. procurement    — qualify bid/no-bid, identify partner, monitor deadline
    3. counterparty   — run DD, update rating, request documents, stop
    4. market         — engage, hold, monitor, re-price
    5. source_health  — wait, seek corroboration, treat as directional only

WHAT THESE TESTS ACTUALLY GUARD. The north star names the gap precisely:

    "The gap is not the guard. The gap is value density. The live feed can
     still produce generic source-derived items with templated impact such as
     'Assess country risk.' That is not enough to be ARIA's USP."

A curriculum can pass every structural check — valid JSON, right roles, right
language — and still teach the encyclopedia answer the north star rejects. So
the guards here are semantic: every answer must reach a DECISION, and none may
use the templated impact the north star names as the failure. Row count is not
a quality measure and is deliberately not asserted as one.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
BUILDER = ROOT / "scripts" / "train" / "build_defence_ecosystem_curriculum.py"
NORTH_STAR = ROOT / "docs" / "golden_intel_north_star_2026_07_14.md"


@pytest.fixture(scope="module")
def rows(tmp_path_factory):
    out = tmp_path_factory.mktemp("eco") / "e.jsonl"
    r = subprocess.run([sys.executable, str(BUILDER), "--out", str(out)],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[:600]
    return [json.loads(ln) for ln in
            out.read_text(encoding="utf-8").splitlines() if ln.strip()]


#: Phrasings that carry a decision. An answer with none of these has stopped at
#: description, which is the encyclopedia entry the north star rejects.
_ACTION = (
    "what to do", "the action", "should do", "what this means", "what it obliges",
    "must say", "should be handled", "act on", "posture", "what to do next",
    "obliges", "the operator", "requires of", "exigés", "should",
)

#: The north star names this exact anti-pattern as the thing that is NOT enough.
_TEMPLATED = (
    "assess country risk", "conduct due diligence generally",
    "monitor the situation", "further investigation is recommended",
    "seek professional advice", "it depends",
)


# -- THE CAPABILITY TEST: value density ---------------------------------

def test_every_answer_reaches_a_decision(rows):
    """THE NORTH-STAR TEST. 'I know ... what to do next.' An answer that names
    the mechanism and stops is what a generic model already produces, so
    training it adds nothing."""
    missing = [r["topic"] for r in rows
               if not any(a in r["messages"][2]["content"].lower() for a in _ACTION)]
    assert not missing, (
        f"answers with no operator action: {missing[:5]} — the north star calls "
        f"this out as insufficient value density, which is the named USP gap"
    )


def test_no_answer_uses_the_templated_impact_the_north_star_rejects(rows):
    """'Assess country risk' is quoted in the north star as the failure."""
    bad = []
    for r in rows:
        a = r["messages"][2]["content"].lower()
        hits = [t for t in _TEMPLATED if t in a]
        if hits:
            bad.append((r["topic"], hits))
    assert not bad, f"templated impact found: {bad[:5]}"


def test_answers_are_dense_enough_to_carry_a_mechanism(rows):
    """A mechanism plus a decision does not fit in two sentences. This is a
    floor against a future edit thinning the corpus into slogans."""
    thin = [(r["topic"], len(r["messages"][2]["content"])) for r in rows
            if len(r["messages"][2]["content"]) < 400]
    assert not thin, f"answers too thin to carry a mechanism: {thin[:5]}"


def test_answers_name_something_specific(rows):
    """Mastery is naming the instrument, threshold or body — but SPECIFICITY IS
    NOT ONE SHAPE, and the first version of this test got that wrong.

    It required an acronym or a percentage in EVERY answer and failed the five
    source-health and typology rows. Those were not deficient: diversion
    red-flags and staleness discipline are specific practitioner knowledge that
    simply is not acronym-shaped. A guard demanding acronyms everywhere would
    push a future author to bolt a fake instrument onto a reasoning answer —
    worse than the gap it was closing, and in a due-diligence product that is a
    fabrication.

    So the requirement is scoped to the job: subjects that ARE named
    instruments must name one; subjects that are epistemic discipline must
    carry the uncertainty vocabulary that is their specificity.
    """
    import re
    INSTRUMENT_JOBS = {"procurement", "counterparty", "market"}
    DISCIPLINE = ("incomplete", "directional", "corroborat", "as of",
                  "single-sourced", "unresolved", "tri-state", "stale")
    for r in rows:
        a = r["messages"][2]["content"]
        if r["job"] in INSTRUMENT_JOBS:
            named = re.search(r"\b[A-Z]{2,}\b", a) or re.search(r"\d+\s?%", a)
            enumerated = a.count(";") >= 3   # an explicit indicator list
            assert named or enumerated, (
                f"{r['topic']}: names no instrument, threshold or explicit "
                f"indicator list")
        else:
            assert any(d in a.lower() for d in DISCIPLINE), (
                f"{r['topic']}: a source-health answer must carry the "
                f"uncertainty vocabulary that is its specificity")


# -- the 360 must actually be 360 ---------------------------------------

def test_all_four_remaining_customer_jobs_are_covered(rows):
    """Job 1 lives in the export-control curriculum; 2-5 must all appear here,
    or the '360 approach' has a hole in it."""
    jobs = {r["job"] for r in rows}
    assert jobs == {"procurement", "counterparty", "market", "source_health"}, (
        f"customer jobs covered: {sorted(jobs)} — the north star defines five, "
        f"and job 1 is the export-control curriculum"
    )


def test_no_job_is_token_covered(rows):
    """One row per job would satisfy the set check and teach nothing."""
    import collections
    per = collections.Counter(r["job"] for r in rows)
    thin = {j: n for j, n in per.items() if n < 3}
    assert not thin, f"jobs with fewer than 3 examples: {thin}"


def test_the_north_star_document_still_exists():
    """These guards quote it. If it moves, they are asserting against a memory
    rather than a source — the §20/R-F4234 'binding read that does not exist'
    failure this repo has already had once."""
    assert NORTH_STAR.is_file(), (
        f"{NORTH_STAR.name} is missing — the customer jobs these tests encode "
        f"came from it, and a guard quoting a vanished document is not a guard")


# -- fabrication discipline ---------------------------------------------

def test_only_high_confidence_rows_ship(rows):
    assert rows and all(r["confidence"] == "high" for r in rows)


def test_the_system_prompt_forbids_inventing_a_threshold(rows):
    """Thresholds are the specific thing this curriculum teaches (25% UBO, OFAC
    50%), so a wrong one is the most damaging fabrication available."""
    for r in rows:
        s = r["messages"][0]["content"].lower()
        assert "never invent" in s and "threshold" in s


def test_rows_are_valid_chat_training_rows(rows):
    for r in rows:
        assert [m["role"] for m in r["messages"]] == ["system", "user", "assistant"]
        assert all(m["content"].strip() for m in r["messages"])
        assert r["source"].startswith("claude_authored:")

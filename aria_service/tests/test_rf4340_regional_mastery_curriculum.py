"""R-F4340 / C-285 — the regional mastery curriculum, guarded on the properties
that make it honest rather than on row count.

TARGETED FROM THE LIVE HEATMAP. `GET /api/aria/student/mastery/heatmap` on
aria-intel, 2026-08-26: 14 topics x 16 regions, 219 sampled, 211 measured, and
83 MEASURED WEAK cells against a gate-2 floor target of 0.70. The zeros cluster
by topic — sanctions across six regions, relationships across six — which is
what makes them teachable rather than random noise.

THE PROPERTY THAT MATTERS MOST HERE IS DURABILITY, and it is why these tests
are not just "does it mention the region".

Regional sanctions and procurement facts change by executive action and by
resolution. A curriculum that taught today's designation list as a permanent
fact would train ARIA to state STALE designations with confidence — which in a
due-diligence product is worse than not knowing, because a confident wrong
clearance is acted on. So every answer must name the MECHANISM and the issuing
body, and must tell the reader where to check the live instrument.

AND IT MUST NOT GAME GATE #2. CLAUDE.md §1 records that the heatmap floor "is
measuring STARVATION, not capability" — a 0.000 cell was never observed, not
failed — and lists the closure routes that are FORBIDDEN: dropping "artifact"
regions, truncating the breach list, leaving cells hidden, or using the seed
knob. Teaching the region is the one honest route, and these tests assert the
curriculum takes it.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
BUILDER = ROOT / "scripts" / "train" / "build_regional_mastery_curriculum.py"


@pytest.fixture(scope="module")
def rows(tmp_path_factory):
    """Drive the REAL builder — §3c capability test, not a fixture copy."""
    out = tmp_path_factory.mktemp("reg") / "r.jsonl"
    r = subprocess.run([sys.executable, str(BUILDER), "--out", str(out)],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[:600]
    return [json.loads(ln) for ln in
            out.read_text(encoding="utf-8").splitlines() if ln.strip()]


#: The zero cells read off the live heatmap. If the curriculum stops covering
#: these, it has drifted off the measured gap and back onto guesswork.
_ZERO_CELLS = {
    ("sanctions", "latam_non_lusophone"), ("sanctions", "south_asia"),
    ("sanctions", "balkans"), ("sanctions", "southeast_asia"),
    ("sanctions", "lusophone"), ("compliance", "latam_lusophone"),
    ("procurement", "europe"), ("procurement", "nato"),
    ("procurement", "latam_non_lusophone"), ("technical", "southern_africa"),
    ("competitor_intel", "north_africa"),
}


# -- THE CAPABILITY TEST: it hits the measured gaps ---------------------

def test_it_targets_cells_that_were_actually_measured_weak(rows):
    """THE POINT. Curriculum aimed at cells that already score well would move
    nothing; this must cover the 0.000 cells read off the live heatmap."""
    covered = {(r["mastery_topic"], r["region"]) for r in rows}
    missed = _ZERO_CELLS - covered
    assert not missed, (
        f"zero-mastery cells with no curriculum: {sorted(missed)} — the "
        f"curriculum has drifted off the measured heatmap gap")


def test_every_row_declares_the_cell_it_targets(rows):
    """Without topic+region on the row, nobody can check afterwards whether the
    cell moved — the measurement would be unattributable."""
    for r in rows:
        assert r.get("mastery_topic") and r.get("region"), r["topic"]


def test_no_cell_is_covered_twice_at_the_expense_of_another(rows):
    """14 rows against 83 weak cells is thin, so duplication is waste."""
    cells = [(r["mastery_topic"], r["region"]) for r in rows]
    assert len(cells) == len(set(cells)), "a cell is covered more than once"


# -- durability: the property that stops it teaching stale facts --------

_LIVE_CHECK = ("check the current", "must be checked", "rather than relying on a cached",
               "rather than relying on historical", "check the current programme",
               "consolidated list", "issuing body", "check whether")


def test_answers_point_at_the_live_instrument_rather_than_asserting_a_snapshot(rows):
    """Designations change by executive action and by resolution. An answer
    that states a list as permanent fact trains ARIA to give a confidently
    STALE clearance, which is acted on and is worse than not knowing.

    SCOPED TO THE TOPICS WHERE LISTS ACTUALLY CHANGE. A first version demanded
    the live-check pointer on every row and failed the relationships and
    procurement rows — wrongly. Those teach structural facts (how ECOWAS
    mandates a mission, how the FCPA reaches an agent, how NSPA frameworks
    gate a bid) that do not expire on a designation cycle, and demanding a
    staleness caveat there would train a reflexive hedge rather than judgement.
    Sanctions and compliance are the topics whose subject matter is a list.
    """
    VOLATILE = {"sanctions", "compliance"}
    stale = [r["topic"] for r in rows
             if r["mastery_topic"] in VOLATILE
             and not any(p in r["messages"][2]["content"].lower() for p in _LIVE_CHECK)]
    assert not stale, (
        f"sanctions/compliance rows asserting a snapshot with no pointer to "
        f"the live instrument: {stale[:5]}")


def test_the_system_prompt_requires_the_live_check(rows):
    for r in rows:
        s = r["messages"][0]["content"].lower()
        assert "designation lists change" in s
        assert "never invent" in s


# -- value density (the north-star USP test) ----------------------------

_ACTION = ("what to do", "what to check", "what to plan", "what the exporter",
           "should do", "what actually decides", "the action", "what to expect")
_TEMPLATED = ("assess country risk", "monitor the situation",
              "seek professional advice", "further investigation is recommended")


def test_every_answer_reaches_a_decision(rows):
    missing = [r["topic"] for r in rows
               if not any(a in r["messages"][2]["content"].lower() for a in _ACTION)]
    assert not missing, f"regional knowledge with no operator action: {missing[:5]}"


def test_no_answer_uses_the_templated_impact(rows):
    bad = [r["topic"] for r in rows
           if any(t in r["messages"][2]["content"].lower() for t in _TEMPLATED)]
    assert not bad, f"templated impact — the named USP gap: {bad[:5]}"


def test_every_answer_names_a_body_or_instrument(rows):
    import re
    thin = [r["topic"] for r in rows
            if not re.search(r"\b[A-Z]{2,}\b", r["messages"][2]["content"])]
    assert not thin, f"names no body, instrument or threshold: {thin[:5]}"


def test_answers_are_dense_enough_to_carry_a_mechanism(rows):
    thin = [(r["topic"], len(r["messages"][2]["content"])) for r in rows
            if len(r["messages"][2]["content"]) < 500]
    assert not thin, f"too thin to carry a regional mechanism: {thin[:5]}"


# -- fabrication discipline ---------------------------------------------

def test_only_high_confidence_rows_ship(rows):
    assert rows and all(r["confidence"] == "high" for r in rows)


def test_rows_are_valid_chat_training_rows(rows):
    for r in rows:
        assert [m["role"] for m in r["messages"]] == ["system", "user", "assistant"]
        assert all(m["content"].strip() for m in r["messages"])
        assert r["source"].startswith("claude_authored:")

"""R-F4345 / C-289 — she abstains where ACQUISITION is the correct move.

THE FINDING, from the first measurement ever taken against the real adapter
(peer session aria-61, 2026-08-26, immediately after the base/LoRA name
collision was fixed and the fine-tune became addressable for the first time):

    tool calling 4/8 correct with the adapter vs ~3/8 base — but the failure
    MODE changed. She no longer invents fake Python. She DECLINES:
        "I cannot perform a full live log review without access to the
         specific logs."

She declined while holding a working `grep`. The grounded-refusal DPO is
bleeding into agentic use: it was trained for CLOSED-BOOK question answering,
where "no reliable basis -> abstain" is exactly right and is what stops
confident fabrication. In an AGENTIC context the correct response to a missing
fact is to CALL THE TOOL THAT FETCHES IT. She is refusing the very step that
would give her the grounding she says she lacks.

THE DISCRIMINATOR IS WHETHER ACQUISITION IS POSSIBLE, not whether the fact is
absent. aria-61's measurement is precise on this: she declined "review the
logs" (a `grep` away) but CORRECTLY declined "date of birth of Andrew Martin"
(no tool can supply it). Both behaviours come from one rule applied with and
without regard to context, so the two cases must sit adjacent in the corpus or
the model has no boundary to learn.

WHAT MUST NOT HAPPEN — and it is the obvious fix, which is why it is pinned.
Training "be more willing to answer" would soften the refusal contract, trade a
good property for a bad one, and walk straight back into the confident
fabrication R-F4325 recorded in production. So the corpus teaches a
DISTINCTION, and the abstention rows are as load-bearing as the acquisition
rows: a corpus showing only acquisition would teach "always act", which is the
same failure in the other direction.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
BUILDER = ROOT / "scripts" / "train" / "build_acquisition_vs_abstention_curriculum.py"


@pytest.fixture(scope="module")
def rows(tmp_path_factory):
    out = tmp_path_factory.mktemp("acq") / "a.jsonl"
    r = subprocess.run([sys.executable, str(BUILDER), "--out", str(out)],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[:600]
    return [json.loads(ln) for ln in
            out.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _acq(rows):
    return [r for r in rows if r["mode"] == "acquire"]


def _abs(rows):
    return [r for r in rows if r["mode"] == "abstain"]


# -- THE CAPABILITY TEST ------------------------------------------------

_TOOL_ACTION = ("i will run", "i will read", "i will search", "i will look",
                "call the tool", "should have called")


def test_acquisition_rows_take_the_tool_step(rows):
    """THE LIVE SYMPTOM: 'I cannot review the logs without access to the logs'
    while holding a grep. Every acquire row must reach for the tool."""
    missing = [r["topic"] for r in _acq(rows)
               if not any(p in r["messages"][2]["content"].lower() for p in _TOOL_ACTION)]
    assert not missing, (
        f"acquire rows that do not take the tool step: {missing} — this is the "
        f"exact behaviour being corrected")


def test_acquisition_rows_do_not_simply_refuse(rows):
    """An acquire row that opens with a refusal teaches the defect."""
    for r in _acq(rows):
        first = r["messages"][2]["content"].strip().lower()[:80]
        assert not first.startswith("i cannot"), (
            f"{r['topic']}: opens with a refusal on a fact a tool can fetch")


# -- the refusal contract must SURVIVE ----------------------------------

def test_abstention_rows_still_refuse(rows):
    """THE PROPERTY MOST AT RISK. The obvious fix — 'be more willing to answer'
    — would soften refusal and bring back confident fabrication. The abstain
    rows must still plainly refuse."""
    weak = [r["topic"] for r in _abs(rows)
            if not any(p in r["messages"][2]["content"].lower()
                       for p in ("cannot", "must not", "will not"))]
    assert not weak, f"abstention rows that stopped refusing: {weak}"


def test_abstention_rows_say_what_is_missing(rows):
    """A refusal that does not name the gap is not actionable — the operator
    cannot close it."""
    for r in _abs(rows):
        a = r["messages"][2]["content"].lower()
        assert any(p in a for p in ("missing", "no source", "not published",
                                    "could not be reached", "no tool")), r["topic"]


def test_the_corpus_teaches_BOTH_sides(rows):
    """A corpus of only acquisition teaches 'always act' — the same failure
    inverted. Both modes must be present in strength."""
    assert len(_acq(rows)) >= 3, "too few acquisition examples"
    assert len(_abs(rows)) >= 3, "too few abstention examples — the refusal "\
                                 "contract would erode"


def test_the_discriminator_is_tool_availability_not_fact_absence(rows):
    """aria-61's measurement: she declined 'review the logs' (a grep away) but
    correctly declined a private DOB (no tool can supply it). The corpus must
    state the test explicitly, or the model learns a disposition instead of a
    rule."""
    joined = " ".join(r["messages"][2]["content"].lower() for r in rows)
    assert "no tool" in joined or "no available tool" in joined
    assert any(p in joined for p in
               ("can a tool i have", "whether a tool", "a tool that fetches",
                "tool i have establish"))


def test_no_row_licenses_fabrication(rows):
    """Neither branch may resolve the gap by guessing."""
    for r in rows:
        s = r["messages"][0]["content"].lower()
        assert "never invent" in s, r["topic"]


def test_rows_are_valid_chat_training_rows(rows):
    for r in rows:
        assert [m["role"] for m in r["messages"]] == ["system", "user", "assistant"]
        assert all(m["content"].strip() for m in r["messages"])
        assert r["source"].startswith("claude_authored:")
        assert r["confidence"] == "high"

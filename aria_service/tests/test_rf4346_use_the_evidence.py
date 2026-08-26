"""R-F4346 / C-290 — she abstains beside evidence that answers the question.

MEASURED on the first honest eval of the real adapter (2026-08-26, after the
base/LoRA collision was fixed and the fine-tune became addressable):

    defence_dd          base 0.308  ->  adapter 0.258
      grounded          base 0.353  ->  adapter 0.280   <- the entire loss
      ungrounded        base 0.359  ->  adapter 0.462   <- large gain
      injection leak    base 0.30-0.40 -> adapter 0.20  <- large gain

    248 of 500 answers abstain; 163 of those are graded WRONG.

The judge names the defect exactly:

    "The candidate claims no registered address is available, but the evidence
     explicitly provides the address."

She abstained while the answer was IN THE CONTEXT. Converting a third of those
163 reaches ~0.366 — above base and above the 0.316 gate — without changing any
other behaviour.

THE DESIGN CONSTRAINT IS WHAT MAKES THIS HARD. The obvious fix, "abstain less",
would hand back the 0.462 closed-book gain and the 0.20 injection leak — the two
things the fine-tune actually bought, and both worth more than eval points. So
the corpus teaches READING, not confidence, and these tests exist to stop a
later edit turning it into the easy version:

    evidence contains it         -> answer + inline citation
    evidence contains PART       -> answer that part, name the gap
    evidence does not contain it -> abstain, name what is missing

The PARTIAL case is the one a confidence-based fix gets wrong in both
directions: she currently abstains on the whole question when only part is
unsupported, and an over-corrected model would answer the unsupported part too.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
BUILDER = ROOT / "scripts" / "train" / "build_use_the_evidence_curriculum.py"


@pytest.fixture(scope="module")
def rows(tmp_path_factory):
    out = tmp_path_factory.mktemp("ev") / "e.jsonl"
    r = subprocess.run([sys.executable, str(BUILDER), "--out", str(out)],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[:600]
    return [json.loads(ln) for ln in
            out.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _by(rows, mode):
    return [r for r in rows if r["mode"] == mode]


_REFUSAL_OPENERS = ("i cannot", "i do not have", "the context does not",
                    "i am unable", "no information")


# -- THE CAPABILITY TEST ------------------------------------------------

def test_use_rows_answer_instead_of_abstaining(rows):
    """THE LIVE DEFECT: 163 wrong abstentions, one of them beside an address
    that was printed in the context."""
    bad = [r["topic"] for r in _by(rows, "use")
           if r["messages"][2]["content"].strip().lower()[:60]
           .startswith(_REFUSAL_OPENERS)]
    assert not bad, (
        f"rows that open by refusing while the evidence answers them: {bad}")


def test_use_rows_cite_inline(rows):
    """A third observed failure mode: she found the fact and cited it in the
    wrong shape, so the answer failed anyway. The citation must sit against the
    claim, not be appended to the response."""
    for r in _by(rows, "use"):
        assert "[from " in r["messages"][2]["content"], (
            f"{r['topic']}: no inline [from <source>] citation")


def test_use_rows_quote_something_from_their_own_context(rows):
    """Guards against an answer that is right but ungrounded — it must draw on
    the supplied evidence, not on parametric memory."""
    for r in _by(rows, "use"):
        ctx = r["messages"][1]["content"].lower()
        ans = r["messages"][2]["content"].lower()
        tokens = [t for t in ("shelton street", "sdn", "embargo", "uae",
                              "2008/944", "companies_house") if t in ctx]
        assert any(t in ans for t in tokens), (
            f"{r['topic']}: answer does not use its own evidence")


# -- the properties the fine-tune BOUGHT must survive -------------------

def test_abstention_rows_still_abstain(rows):
    """The 0.462 closed-book score and the 0.20 injection leak came FROM this
    disposition. A corpus that taught 'abstain less' would give both back."""
    for r in _by(rows, "abstain"):
        a = r["messages"][2]["content"].lower()
        assert any(p in a for p in
                   ("does not answer", "will not infer", "not established",
                    "cannot")), r["topic"]


def test_abstention_rows_name_what_is_missing(rows):
    for r in _by(rows, "abstain"):
        a = r["messages"][2]["content"].lower()
        assert "missing" in a or "would need" in a, r["topic"]


def test_the_partial_case_is_taught(rows):
    """The case a confidence-based fix gets wrong in BOTH directions."""
    partial = _by(rows, "partial")
    assert partial, "no partial-evidence example — the hardest case is untaught"
    for r in partial:
        a = r["messages"][2]["content"].lower()
        assert "[from " in r["messages"][2]["content"], r["topic"]
        assert any(p in a for p in ("not established", "does not", "not "
                                    "answer", "will not infer")), r["topic"]


def test_the_corpus_teaches_all_three_branches(rows):
    """The three EVIDENCE branches, plus a 'lesson' row that states the rule.

    The lesson row deliberately carries no context block — it explains WHY the
    abstention was wrong — so the citation and quote-your-evidence guards above
    are scoped away from it. An earlier version filed it as `use` and those
    guards failed it, which was the guard over-fitting, not the row being bad:
    demanding a citation on a row with nothing to cite would have pushed a
    later author to bolt on a fake source.
    """
    modes = {r["mode"] for r in rows}
    assert {"use", "partial", "abstain"} <= modes, (
        f"branches present: {sorted(modes)} — all three evidence branches are "
        f"required or the model learns a disposition rather than a reading rule")


def test_no_row_licenses_invention(rows):
    for r in rows:
        assert "never invent" in r["messages"][0]["content"].lower(), r["topic"]


def test_rows_are_valid_chat_training_rows(rows):
    for r in rows:
        assert [m["role"] for m in r["messages"]] == ["system", "user", "assistant"]
        assert all(m["content"].strip() for m in r["messages"])
        assert r["source"].startswith("claude_authored:")
        assert r["confidence"] == "high"

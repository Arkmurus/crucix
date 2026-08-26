"""R-F4360 / C-306 — ARIA cannot describe her own ARK-DD layer stack.

MEASURED on the clean v0.8 eval (2026-08-26, the first run through the repaired
harness): **`dd_layer` is 100 of 500 rows — 20% of the benchmark — and 93
fail.** The single largest failure class, and not a retrieval gap. Asked "What
does Layer 2 (Network) do?" she answered with the OSI model ("Data Link Layer …
reliable communication between two devices"). Asked about Layer 4 she could not
identify which stack was meant.

WHY IT IS THE NORTH-STAR TARGET. `docs/golden_intel_north_star_2026_07_14.md`:
"The gap is not the guard. The gap is value density" — every item must say what
the customer should DO. The ARK-DD layers are the mechanism that produces those
decisions, so a verdict she cannot describe is one she cannot justify to the
operator who acts on it.

THESE TESTS GUARD THE THINGS THAT WOULD MAKE THE CURRICULUM WORSE THAN NOTHING,
because teaching a confidently-wrong stack is worse than teaching none:

1. **The numbering.** `DD_LAYER_NAMES` lists verification 11th, but every
   numbered label in the source reads `Layer 3 (Verification)` /
   `Layer 4 (Compliance)`. The tuple is an ITERATION ORDER, not a numbering.
   The test derives the expected mapping from the source labels themselves, so
   a future renumbering in code fails this file rather than silently leaving
   the corpus wrong.

2. **The Layer 3 honesty ruling.** R-F393 records "verification" as a Phase A
   honesty bug, found when a Lukoil DD returned 0% grounded while the layer
   self-reported as wired. Layer 3 triangulates and detects conflicts; it does
   NOT re-fetch sources to re-confirm truth, and `grounded_rate` is a
   corroboration count, not a URL-verification rate. A row teaching "Layer 3
   verifies" re-introduces the exact overclaim the repo already fixed.

3. **Value density.** A row that defines a layer without saying what the
   operator does with it is precisely the "templated impact" the north star
   names as the USP gap.
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
BUILDER = ROOT / "scripts" / "train" / "build_ark_dd_layer_curriculum.py"
ORCH = ROOT / "aria_service" / "intel" / "dd_orchestrator.py"


@pytest.fixture(scope="module")
def rows(tmp_path_factory):
    """Drive the REAL builder — §3c capability test, not a fixture copy."""
    out = tmp_path_factory.mktemp("ark") / "a.jsonl"
    r = subprocess.run([sys.executable, str(BUILDER), "--out", str(out)],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[:600]
    return [json.loads(ln) for ln in
            out.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _text(rows):
    """QUESTION **and** answer text.

    Mutation testing caught this: scanning only the assistant turn let a
    mislabelled QUESTION through — "What does Layer 3 (Compliance) do?" paired
    with a correct triangulation answer passed every numbering guard, while
    teaching the model that Layer 3 is called Compliance. The label the user
    says is part of what is learned.
    """
    return " ".join(r["messages"][1]["content"] + " " + r["messages"][2]["content"]
                    for r in rows).lower()


# ------------------------------------- THE NUMBERING, DERIVED FROM SOURCE

def _source_numbering() -> dict[str, str]:
    """Layer number -> name, read out of the orchestrator's own labels.

    Deriving this rather than hardcoding it is the point: if the stack is ever
    renumbered in code, this test fails and the corpus gets corrected, instead
    of the corpus quietly teaching a stack that no longer exists.
    """
    src = ORCH.read_text(encoding="utf-8")
    found: dict[str, str] = {}
    for num, name in re.findall(r"Layer (\d+) \(([A-Za-z_ -]+)\)", src):
        n = name.strip().lower().replace("_", " ")
        if n in ("person mode", "a", "report assembly"):
            continue
        found.setdefault(num, n)
    return found


def test_the_curriculum_matches_the_numbering_in_the_source(rows):
    """THE HIGHEST-STAKES PROPERTY. She currently answers OSI for Layer 2; a
    corpus with the wrong numbering would replace 'no knowledge' with
    'confident wrong knowledge', which is strictly worse."""
    numbering = _source_numbering()
    assert numbering, "could not read any numbered layer label from the source"
    body = _text(rows)
    for num, name in sorted(numbering.items(), key=lambda kv: int(kv[0])):
        pat = re.compile(rf"layer {num}\b[^.]{{0,40}}{re.escape(name.split()[0])}")
        assert pat.search(body), (
            f"source says Layer {num} = {name}, but the curriculum never pairs "
            f"them — teaching a wrong number is worse than teaching nothing")


def test_it_does_not_teach_the_iteration_order_as_the_numbering(rows):
    """`DD_LAYER_NAMES` has compliance 3rd and verification 11th. Anyone
    reading that tuple as the numbering gets Layers 3 and 4 backwards."""
    body = _text(rows)
    assert not re.search(r"layer 3[^.]{0,30}compliance", body), (
        "Layer 3 taught as Compliance — that is the DD_LAYER_NAMES iteration "
        "order, not the numbering")
    assert not re.search(r"layer 4[^.]{0,30}verification", body)


# ------------------------------------------- THE HONESTY RULING (R-F393)

def test_layer_3_is_not_taught_as_independent_verification(rows):
    """R-F393: the 'verification' name was a Phase A honesty bug. Teaching the
    overclaim would undo a fix the repo already paid for once."""
    l3 = [r for r in rows if r["layer"] == "3"]
    assert l3, "Layer 3 is not covered at all"
    a = l3[0]["messages"][2]["content"].lower()
    assert "does not" in a and "re-fetch" in a, (
        "Layer 3 row never states what it does NOT do:\n" + a[:300])
    assert "not a url-verification rate" in a or "corroboration" in a, (
        "grounded_rate is taught without the corroboration-vs-verification "
        "distinction, which is the whole honesty point")


def test_no_row_claims_a_layer_verifies_what_it_only_triangulates(rows):
    for r in rows:
        a = r["messages"][2]["content"].lower()
        if "grounded_rate" in a:
            assert "not a url-verification rate" in a or "corroboration count" in a, (
                f"{r['topic']}: grounded_rate presented without its boundary")


# ------------------------------------------------- THE OSI CONFUSION

def test_the_osi_confusion_is_addressed_head_on(rows):
    """The observed failure verbatim. A corpus that teaches the right answer
    without naming the wrong one leaves the competing association intact."""
    dis = [r for r in rows if r["mode"] == "disambiguation"]
    assert dis, "no row directly contrasts ARK-DD Layer 2 with the OSI layer 2"
    # WORD BOUNDARY, and scoped to the row that is supposed to do the work.
    # Mutation testing caught a bare `"osi" in body` passing on "prop-OSI-tion"
    # in the Layer 2 row — the guard was satisfied by an unrelated word while
    # the disambiguation had been removed entirely.
    joined = " ".join(r["messages"][2]["content"] for r in dis).lower()
    assert re.search(r"\bosi\b", joined), (
        "the disambiguation row never names OSI — her actual failure was "
        "answering the OSI model, and a corpus that teaches the right answer "
        "without naming the wrong one leaves the competing association intact")


# --------------------------------------- VALUE DENSITY (the north star)

_ACTION = ("operator action", "should stop", "escalate", "the operator",
           "action is", "what the operator")
_TEMPLATED = ("assess country risk", "monitor the situation",
              "seek professional advice", "further investigation is recommended")


def test_every_row_reaches_an_operator_action(rows):
    """North star: "The gap is not the guard. The gap is value density." A
    definition with no decision attached is the templated-impact failure."""
    missing = [r["topic"] for r in rows
               if not any(a in r["messages"][2]["content"].lower() for a in _ACTION)]
    assert not missing, f"layer knowledge with no operator action: {missing}"


def test_no_row_uses_the_templated_impact(rows):
    bad = [r["topic"] for r in rows
           if any(t in r["messages"][2]["content"].lower() for t in _TEMPLATED)]
    assert not bad, f"templated impact — the named USP gap: {bad}"


# ------------------------------------------------------- COVERAGE

def test_every_numbered_layer_in_the_source_is_covered(rows):
    """A partial stack is how she ends up confidently describing four layers
    and inventing the rest."""
    covered = {r["layer"] for r in rows}
    expected = set(_source_numbering()) | {"7"}
    missing = expected - covered
    assert not missing, (
        f"layers present in the source but absent from the curriculum: "
        f"{sorted(missing, key=int)}")


def test_5b_and_5c_are_taught_with_their_dominant_meanings(rows):
    """`5b` is used for TWO things in the code — deception scoring
    (commercial_coherence.py, dd_orchestrator:16238) and a sweep-intelligence
    helper (dd_orchestrator:12492). The deception meaning is what the rest of
    the stack references. The collision is a code defect to rename, not an
    ambiguity to train."""
    body = _text(rows)
    assert re.search(r"5b[^.]{0,40}deception", body), "5b not taught as deception scoring"
    assert re.search(r"5c[^.]{0,60}coherence", body), "5c not taught as commercial coherence"
    assert "sweep intelligence" not in body, (
        "the sweep-intelligence 5b label is a code inconsistency; training it "
        "would teach the ambiguity instead of the meaning")


# ------------------------------------------- FABRICATION DISCIPLINE

def test_the_applied_row_keeps_the_refusal_contract(rows):
    """The applied shape must teach the SHAPE of an answer without teaching her
    to invent findings. Trading that away gives back what the fine-tune bought."""
    app = [r for r in rows if r["mode"] == "applied"]
    assert app, "no applied row — she also fails 'run a Layer 1 check on X'"
    for r in app:
        a = r["messages"][2]["content"].lower()
        assert "will not" in a or "unless" in a, (
            f"{r['topic']}: applied row does not bound what it will assert")


def test_no_row_licenses_invention(rows):
    for r in rows:
        s = r["messages"][0]["content"].lower()
        assert "never invent" in s, r["topic"]


def test_rows_are_valid_chat_training_rows(rows):
    for r in rows:
        assert [m["role"] for m in r["messages"]] == ["system", "user", "assistant"]
        assert all(m["content"].strip() for m in r["messages"])
        assert r["source"].startswith("claude_authored:")
        assert r["confidence"] == "high"

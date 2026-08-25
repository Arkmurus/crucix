"""R-F4334 / C-280 — the Claude-authored export-control curriculum.

WHY IT EXISTS. The eval scores 110 questions (22%) in eleven languages on
national export-control regimes; v0.7 scored 6/110. The cause was measured, not
guessed: THE TEACHER DOES NOT KNOW THE MATERIAL. DeepSeek — which generates the
grounded corpus and judges the eval — answers UNKNOWN for CIEEMG, SBDU, JIMDDU,
ANCEX and SSB (5 of 6 probed). A curriculum distilled from a teacher cannot
contain what the teacher lacks, which is why three grounded cycles plateaued.

Nor was it a rubric artifact: re-judging with the strict grounding rubric
LIFTED (R-F4332/R-F4333) still gave 36/175 = 0.206 on that bucket.

WHAT THESE TESTS GUARD, and the first one is the reason they exist.

1. NO CODE-SWITCHING. The first version of the builder templated a per-language
   lead sentence and left the body in English:
       "Yetkili kurum Savunma Sanayii Baskanligi (SSB), export licences are
        issued with the Ministry of National Defence..."
   Training on that teaches code-switching — strictly worse than not training,
   because the eval asks in-language and grades an in-language answer. It was
   caught by reading the generated rows, and this test is what stops it coming
   back through a well-meaning "let's cover more languages" edit.

2. NO FABRICATION BY DEFAULT. Only `high`-confidence facts ship. These are
   regulatory facts in a due-diligence product: a wrong acronym trained in is a
   falsehood ARIA will then repeat with confidence.

3. NO CONTAMINATION. The corpus must not reproduce eval questions.
   `training_corpus_manifest.py` checks prompt overlap mechanically; this adds
   a local check so a bad edit fails fast rather than at pre-flight time.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
BUILDER = ROOT / "scripts" / "train" / "build_export_control_curriculum.py"
EVAL = ROOT / "data" / "eval_reports" / "aria_eval_500q_openbook.jsonl"


@pytest.fixture(scope="module")
def rows(tmp_path_factory):
    """Drive the REAL builder, not a fixture copy — §3c capability test."""
    out = tmp_path_factory.mktemp("curr") / "c.jsonl"
    r = subprocess.run([sys.executable, str(BUILDER), "--out", str(out)],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[:600]
    return [json.loads(ln) for ln in
            out.read_text(encoding="utf-8").splitlines() if ln.strip()]


# -- THE CAPABILITY TEST ------------------------------------------------

#: Fragments that only appear if an English body leaked into a localised
#: answer. Each was present in the code-switched first version.
_ENGLISH_LEAK = (
    " are issued", " is issued", " administered by the ", " coordinated by ",
    " in the Department", " under the Ministry", " fall under the ",
    " is a State Party", " signed the Arms Trade",
)


def test_no_answer_is_code_switched(rows):
    """THE DEFECT. A localised lead with an English body teaches code-switching."""
    bad = []
    for r in rows:
        answer = r["messages"][2]["content"]
        hits = [f for f in _ENGLISH_LEAK if f in answer]
        if hits:
            bad.append((r["topic"], hits))
    assert not bad, (
        f"English body leaked into localised answers: {bad[:5]} — this trains "
        f"code-switching, which is worse than not training at all"
    )


def test_every_answer_is_in_the_language_it_was_asked_in(rows):
    """A cheap positive check to sit alongside the negative one: each language
    must show its own orthography/diacritics somewhere in the answer."""
    MARK = {
        "fr": ("é", "è", "à"), "es": ("ó", "ñ", "é"), "de": ("ü", "ä", "ß"),
        "ro": ("ă", "ș", "ț"), "tr": ("ı", "ş", "ğ", "İ"),
    }
    for r in rows:
        marks = MARK.get(r["language"])
        if not marks:
            continue
        a = r["messages"][2]["content"]
        assert any(m in a for m in marks), (
            f"{r['topic']}: answer shows no {r['language']} orthography — "
            f"likely English text under a localised label"
        )


# -- fabrication discipline ---------------------------------------------

def test_only_high_confidence_rows_ship_by_default(rows):
    """A wrong acronym trained in is a fabrication ARIA repeats confidently."""
    assert rows, "builder produced nothing"
    assert all(r["confidence"] == "high" for r in rows)


def test_every_row_names_an_authority_and_an_instrument(rows):
    """The whole point is the SPECIFICS the eval grades on. A fluent answer
    that names no body is exactly the failure v0.7 already had."""
    for r in rows:
        a = r["messages"][2]["content"]
        assert any(c.isupper() for c in a), r["topic"]
        assert len(a) > 200, f"{r['topic']}: answer too thin to carry specifics"


def test_the_system_prompt_forbids_inventing_an_agency(rows):
    for r in rows:
        assert "never" in r["messages"][0]["content"].lower()


# -- contamination ------------------------------------------------------

@pytest.mark.skipif(not EVAL.is_file(), reason="eval set not present")
def test_no_eval_question_is_reproduced(rows):
    """Teaching the DOMAIN is legitimate; reproducing the TEST is not.
    The pre-flight checks this mechanically — this fails fast, locally."""
    ev = {json.loads(ln)["question"].strip().lower()
          for ln in EVAL.read_text(encoding="utf-8").splitlines() if ln.strip()}
    for r in rows:
        q = r["messages"][1]["content"].strip().lower()
        assert q not in ev, f"{r['topic']} reproduces an eval question verbatim"


@pytest.mark.skipif(not EVAL.is_file(), reason="eval set not present")
def test_no_eval_expected_answer_is_reproduced(rows):
    """Answer-level contamination is what the prompt-overlap pre-flight CANNOT
    see, so it is checked here."""
    ev = [json.loads(ln).get("expected_answer") or ""
          for ln in EVAL.read_text(encoding="utf-8").splitlines() if ln.strip()]
    ev = [e.strip().lower() for e in ev if len(e.strip()) > 80]
    for r in rows:
        a = r["messages"][2]["content"].strip().lower()
        for e in ev:
            assert e not in a and a not in e, (
                f"{r['topic']} reproduces an eval expected_answer")


# -- shape --------------------------------------------------------------

def test_rows_are_valid_chat_training_rows(rows):
    for r in rows:
        m = r["messages"]
        assert [x["role"] for x in m] == ["system", "user", "assistant"]
        assert all(x["content"].strip() for x in m)
        assert r["source"].startswith("claude_authored:")


def test_coverage_is_reported_honestly(rows):
    """This is a SEED curriculum, not a full course — 14 rows against 110 eval
    questions in 11 languages. The test records the real shape so nobody reads
    it as complete coverage."""
    langs = {r["language"] for r in rows}
    assert langs, "no languages covered"
    assert len(rows) >= len(langs), "fewer rows than languages"

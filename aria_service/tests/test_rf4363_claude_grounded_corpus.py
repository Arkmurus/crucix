"""R-F4363 / C-309 — a Claude-authored replacement for the DeepSeek-generated
grounded corpus.

THE DIRECTIVE, and how far it was from the reality. The operator's instruction
is that Claude trains ARIA, not DeepSeek. Measured on the v0.9 corpus: **664 of
928 rows carried `source: grounded_deepseek_v1`**. The directive was being
honoured for new work while the bulk of what actually trains her was not.

WHAT THOSE ROWS BOUGHT, because replacing them blind would give it back:

    grounded            435   answer FROM the evidence, cited inline
    grounded_abstain    196   evidence present but does not support -> abstain
    abstain              33   no evidence -> abstain
    refusal_authority_spoof   reject a fabricated directive
    refusal_premise_injection reject a false premise AND correct it

The clean v0.8 run measured an injection leak_rate of 0.1 — the best recorded —
and that discipline is what stops confident fabrication. A replacement that
loses any of these five is a regression wearing a directive's clothes, which is
why this file tests behaviours rather than row counts.

THE PROPERTY THAT MAKES THE GENERATOR SAFE, and the one most worth guarding:
**context and answer are composed from ONE fact record.** A generator that wrote
evidence and answers independently would eventually emit an answer its evidence
does not support — the exact defect the corpus exists to train against, injected
into the training data. Every assertion below checks that the composition held.
"""
from __future__ import annotations

import collections
import json
import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
BUILDER = ROOT / "scripts" / "train" / "build_claude_grounded_corpus.py"


@pytest.fixture(scope="module")
def rows(tmp_path_factory):
    out = tmp_path_factory.mktemp("cg") / "c.jsonl"
    r = subprocess.run([sys.executable, str(BUILDER), "--out", str(out)],
                       capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stderr[:800]
    return [json.loads(ln) for ln in
            out.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _by(rows, topic):
    return [r for r in rows if r["topic"] == topic]


def _src(r):
    """The source label the row's own evidence carries.

    The RAG header itself contains the literal string "[Source: ...]" as an
    INSTRUCTION ("Cite each fact inline using its [Source: ...] label"), and a
    naive first-match regex returns "..." from that header rather than the real
    label. The test then fails against a corpus that is correct — which is how
    this nearly got read as a corpus defect instead of a test defect.
    """
    found = [s for s in re.findall(r"\[Source: ([^\]]+)\]",
                                   r["messages"][0]["content"])
             if s.strip() != "..."]
    return found[-1] if found else None


# ------------------------------------ THE STRUCTURAL GROUNDEDNESS PROPERTY

def test_every_answer_cites_the_source_its_own_evidence_carries(rows):
    """THE LOAD-BEARING INVARIANT. Not merely 'an answer contains a citation' —
    the citation must be the source label from THAT ROW's evidence block.

    A generator that drew answers from one pool and evidence from another would
    satisfy a looser check while teaching her to cite a source that is not in
    front of her, which is fabricated-citation behaviour taught directly.
    """
    for r in rows:
        src = _src(r)
        assert src, f"{r['topic']}: evidence block carries no [Source: ...] label"
        assert f"[from {src}]" in r["messages"][1]["content"], (
            f"{r['topic']}: answer cites something other than its own evidence "
            f"(expected [from {src}])")


def test_no_row_carries_a_deepseek_source(rows):
    """The whole point of the R-number. If this ever fails, the corpus has been
    re-mixed with generated rows and the directive is being broken again."""
    bad = [r["topic"] for r in rows if "deepseek" in str(r.get("source", "")).lower()]
    assert not bad, f"DeepSeek-sourced rows present: {bad[:5]}"
    assert all(str(r.get("source", "")).startswith("claude_authored:") for r in rows)


# ------------------------------------------- THE FIVE BEHAVIOURS

def test_abstain_rows_name_what_is_missing_and_do_not_answer_it(rows):
    """`grounded_abstain` is 196 rows of the original corpus and is the
    discipline that keeps her honest when evidence is present but thin. An
    abstention that does not name the gap is not actionable — the operator
    cannot close what they cannot see."""
    ab = [r for r in rows if r["label"] == "grounded_abstain"]
    assert len(ab) >= 50, f"only {len(ab)} abstain rows — the discipline is thin"
    # THE OPENING, not merely a phrase somewhere in the text. Mutation testing
    # showed an answer beginning "Yes. It establishes that ..." passing, because
    # a later clause still contained "does not cover". An abstention that opens
    # by asserting has already answered; where the refusal sits is the whole
    # behaviour.
    OPENERS = ("the evidence does not answer", "не отвечают", "ne répondent",
               "no responde", "لا تجيب")
    for r in ab:
        head = r["messages"][1]["content"].strip().lower()[:90]
        assert any(o in head for o in OPENERS), (
            f"{r['topic']}: abstain row does not OPEN by declining — it begins "
            f"{head[:60]!r}")


def test_spoof_rows_refuse_and_do_not_comply(rows):
    """The fabricated-directive shape: an instruction that arrives INSIDE the
    request, unsupported by the evidence. This is half of what bought the 0.1
    leak rate."""
    sp = _by(rows, "refusal_authority_spoof")
    assert len(sp) >= 20, "too few authority-spoof rows"
    for r in sp:
        a = r["messages"][1]["content"].lower()
        assert "cannot act on that" in a, f"{r['topic']}: does not refuse"
        assert "no such authorisation" in a, (
            f"{r['topic']}: refuses without saying the evidence lacks it")
        for comply in ("i have released", "processing the payment",
                       "proceeding as instructed", "authorisation confirmed"):
            assert comply not in a, f"{r['topic']}: complies with the spoof"


def test_spoof_rows_reject_urgency_as_a_reason(rows):
    """Urgency is the most common wrapper for a fabricated directive, and a
    model that treats it as a reason to skip verification has learned the
    wrong lesson from being helpful."""
    joined = " ".join(r["messages"][1]["content"].lower()
                      for r in _by(rows, "refusal_authority_spoof"))
    assert "urgency does not change that" in joined, (
        "no spoof row addresses urgency, which is the pressure the shape uses")


def test_injection_rows_correct_the_premise_rather_than_only_refusing(rows):
    """THE SUBTLE ONE. `refusal_premise_injection` does not merely decline — it
    states what the evidence ACTUALLY says. A bare refusal leaves the operator
    holding the false belief they arrived with, which is the outcome the row
    exists to prevent."""
    inj = _by(rows, "refusal_premise_injection")
    assert len(inj) >= 20, "too few premise-injection rows"
    for r in inj:
        a = r["messages"][1]["content"]
        assert "the premise is not what the evidence says" in a.lower(), r["topic"]
        assert "What it establishes is that" in a, (
            f"{r['topic']}: refuses without correcting — the operator is left "
            f"holding the false premise")
        assert f"[from {_src(r)}]" in a


def test_multilingual_rows_keep_the_citation_verbatim(rows):
    """THE GROUNDING CONTRACT IS LANGUAGE-INVARIANT. 67 of 68 multi_lang rows
    failed on the clean eval, so this axis cannot be left empty — but the
    citation and source label must stay VERBATIM. A translated source label is
    not the label, and translating it would break citation grounding while
    looking more polished."""
    ml = [r for r in rows if r["topic"].startswith("multi_lang_")]
    assert len(ml) >= 40, f"only {len(ml)} multilingual rows"
    langs = {r["topic"].split("_")[-1] for r in ml}
    assert len(langs) >= 3, f"only {sorted(langs)} — too few languages"
    for r in ml:
        src = _src(r)
        assert f"[from {src}]" in r["messages"][1]["content"], (
            f"{r['topic']}: citation was translated or dropped")


# --------------------------------------------- CORPUS QUALITY

def test_no_question_maps_to_two_different_answers(rows):
    """The augmentation failure R-F4360 hit: context-free templates colliding
    across facts, teaching that one question has several unrelated correct
    answers."""
    by_q = collections.defaultdict(set)
    for r in rows:
        by_q[r["messages"][0]["content"]].add(r["messages"][1]["content"])
    conflicts = [q for q, a in by_q.items() if len(a) > 1]
    assert not conflicts, f"{len(conflicts)} question(s) map to >1 answer"


def test_no_mangled_acronyms(rows):
    """A blind first-letter lowercase produced "jSC Rosoboronexport" and would
    have produced "eU", "uN", "oFAC". The model learns the mangling with the
    fact, and it surfaces in customer-facing output."""
    bad = [(r["topic"], m.group(0)) for r in rows
           for m in [re.search(r"\b[a-z][A-Z]{2,}", r["messages"][1]["content"])]
           if m]
    assert not bad, f"malformed acronyms: {bad[:5]}"


def test_the_grounding_block_matches_production(rows):
    """Train/eval/serve format consistency. A model taught on a different
    wrapper has to generalise across it at inference for no reason."""
    for r in rows:
        u = r["messages"][0]["content"]
        assert u.startswith("[CONTEXT — answer ONLY from this evidence"), r["topic"]
        assert "[Source: " in u


def test_domain_spread_is_preserved(rows):
    """The replaced corpus spanned sanctions, procurement, defence, cyber,
    trade finance, corruption, diversion and intelligence. Collapsing to one
    domain would teach the contract on a narrower world than it is used in."""
    domains = {r["domain"] for r in rows}
    assert len(domains) >= 8, f"only {sorted(domains)}"


def test_it_is_large_enough_to_replace_what_it_removes(rows):
    """664 DeepSeek rows are being withdrawn. A token replacement would leave
    the grounding contract materially thinner than the run that measured
    leak_rate 0.1."""
    assert len(rows) >= 350, (
        f"only {len(rows)} rows against 664 withdrawn — the contract would be "
        f"trained on materially less evidence than the run it replaces")


def test_rows_are_valid_chat_training_rows(rows):
    for r in rows:
        assert [m["role"] for m in r["messages"]] == ["user", "assistant"]
        assert all(m["content"].strip() for m in r["messages"])
        assert r["label"] in ("grounded", "grounded_abstain", "abstain")

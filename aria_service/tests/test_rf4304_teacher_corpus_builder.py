"""R-F4304 / C-257 - the teacher signal was stored but never distilled.

C-254 fixed the amplification and C-255 built the carrier, so the corpus is now
clean and filling. It still had no consumer: `brave_distill` has `brave_student`
(a 6h trainer loop plus an on-demand train endpoint) and `claude_distill` had
only a stats endpoint. 30 MB captured, nothing learned.

A LITERAL MIRROR OF brave_student WOULD BE WRONG, which is why this is a builder
and not a "claude_student". brave_student learns DOMAIN-PREFERENCE WEIGHTS into a
model.json and reranks search results - a statistical reranker. The Claude corpus
is reasoning TEXT; distilling it means SFT/DPO rows feeding the pipeline that
already exists in scripts/train/. Copying the pattern would have produced a
plausible-looking module that cannot distil anything.

THE SCHEMA WAS INCOMPLETE FOR SFT, and that is the substantive finding here. An
SFT row is (instruction, response). `capture()` stored Claude's text, a msg_id
and a `reply_to` ID - never the parent's TEXT. So a reply could be linked but not
paired, and the corpus was untrainable no matter what consumed it.

The parent was always recoverable and nothing looked: `_read_log()` returns BOTH
directions and `drain_for_aria` already calls it. Resolving the parent at capture
time makes each record self-contained.

WHAT MUST NOT HAPPEN is synthesising the missing half. The tempting move for a
note with no recoverable parent is to invent an instruction ("Review this and
report findings") so it can be used. That fabricates the question ARIA was asked
and teaches her to answer prompts nobody wrote. Unpaired records are REPORTED and
DROPPED, never imagined - the same rule as an honest NOT_RUN over a clean line.

A BUILDER THAT SILENTLY SHRINKS ITS INPUT IS A GUARD THAT CERTIFIES OVER A
SMALLER WORLD. Every drop is counted by reason, and the counts are part of the
output.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.train import build_claude_teacher_corpus as B  # noqa: E402


def _rec(text, *, prompt="", kind="note", msg_id="cb_1", ts=1.0):
    return {"ts": ts, "source": "claude_teacher", "direction": "claude->aria",
            "kind": kind, "msg_id": msg_id, "reply_to": "", "prompt": prompt,
            "text": text}


# -- dedup: the corpus carries historical 450x duplication ------------------

def test_identical_records_collapse_to_one() -> None:
    long = "A" * 900
    rows = [_rec(long, prompt="what did you find?", msg_id="cb_39") for _ in range(1250)]
    out, report = B.build(rows)
    assert len(out) == 1, "1,250 copies of cb_39 must yield one row"
    assert report["dropped"]["duplicate"] == 1249


def test_the_same_id_with_different_text_is_not_a_duplicate() -> None:
    """An edited note is new content, not a re-capture."""
    rows = [_rec("X" * 900, prompt="q", msg_id="cb_5"),
            _rec("Y" * 900, prompt="q", msg_id="cb_5")]
    out, _ = B.build(rows)
    assert len(out) == 2


# -- the quality floor ------------------------------------------------------

def test_fragments_are_dropped() -> None:
    """The corpus holds 'A', 'like this', 'ship Phase A' - bridge-probe exhaust.
    Training on them teaches nothing and dilutes what is real."""
    rows = [_rec("A", prompt="q"), _rec("like this", prompt="q"),
            _rec("ship Phase A", prompt="q")]
    out, report = B.build(rows)
    assert out == []
    assert report["dropped"]["too_short"] == 3


def test_a_substantial_note_survives() -> None:
    rows = [_rec("R-F2689 REVIEW - I independently re-ran the suite. " * 20,
                 prompt="cross-check the gold-lane gate")]
    out, report = B.build(rows)
    assert len(out) == 1
    assert report["kept"] == 1


def test_the_floor_is_named_not_a_magic_number() -> None:
    assert isinstance(B.MIN_CHARS, int) and B.MIN_CHARS >= 200


# -- never fabricate the missing half ---------------------------------------

def test_an_unpaired_note_is_dropped_not_invented() -> None:
    """THE RULE THAT MATTERS. Synthesising an instruction fabricates the question
    ARIA was asked and teaches her to answer prompts nobody wrote."""
    rows = [_rec("a long and genuinely useful review. " * 30, prompt="")]
    out, report = B.build(rows)
    assert out == [], "a note with no recoverable prompt must not be emitted"
    assert report["dropped"]["no_prompt"] == 1


def test_no_synthetic_instruction_appears_anywhere() -> None:
    rows = [_rec("substantive content. " * 40, prompt="the real question ARIA asked")]
    out, _ = B.build(rows)
    user = out[0]["messages"][0]["content"]
    assert user == "the real question ARIA asked"


# -- the emitted row must feed the EXISTING pipeline ------------------------

def test_the_row_shape_matches_the_existing_corpora() -> None:
    """aria_grounded_v3.jsonl and friends are
    {messages:[user,assistant], topic, grounded, label, source}. A new shape
    would be an island the training scripts cannot read."""
    rows = [_rec("teacher content. " * 40, prompt="q")]
    out, _ = B.build(rows)
    r = out[0]
    assert [m["role"] for m in r["messages"]] == ["user", "assistant"]
    assert r["messages"][1]["content"].startswith("teacher content")
    assert r["source"] == "claude_teacher"
    assert "label" in r and "topic" in r


def test_output_is_json_serialisable() -> None:
    rows = [_rec("teacher content. " * 40, prompt="q")]
    out, _ = B.build(rows)
    json.dumps(out[0])          # must not raise


# -- the report is part of the output ---------------------------------------

def test_every_drop_is_counted_by_reason() -> None:
    rows = [
        _rec("A", prompt="q"),                                    # too_short
        _rec("keep me. " * 80, prompt="q", msg_id="cb_1"),        # kept
        _rec("keep me. " * 80, prompt="q", msg_id="cb_1"),        # duplicate
        _rec("unpaired. " * 80, prompt=""),                       # no_prompt
    ]
    out, report = B.build(rows)
    assert report["seen"] == 4
    assert report["kept"] == len(out) == 1
    assert report["dropped"] == {"too_short": 1, "duplicate": 1, "no_prompt": 1}
    assert report["seen"] == report["kept"] + sum(report["dropped"].values()), (
        "the ledger must balance - an uncounted drop is a silent shrink")


def test_an_empty_corpus_reports_zero_rather_than_failing() -> None:
    out, report = B.build([])
    assert out == [] and report["seen"] == 0 and report["kept"] == 0


def test_malformed_records_are_counted_not_crashed() -> None:
    out, report = B.build([{"nope": 1}, "not a dict", None])
    assert out == []
    assert report["dropped"].get("malformed") == 3


# -- capture() must store the parent, or none of the above can work ---------

def test_capture_accepts_and_stores_a_prompt(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ARIA_CLAUDE_DISTILL_DIR", str(tmp_path))
    monkeypatch.setenv("ARIA_CLAUDE_DISTILL_ENABLED", "1")
    import importlib
    from aria_service.intel import claude_distill as cd
    cd = importlib.reload(cd)
    assert cd.capture("the answer", prompt="the question", msg_id="cb_9") is True
    shard = next(tmp_path.glob("*.jsonl"))
    rec = json.loads(shard.read_text(encoding="utf-8").splitlines()[-1])
    assert rec["prompt"] == "the question"
    assert rec["text"] == "the answer"


def test_the_drain_resolves_the_parent_text() -> None:
    """A capability nothing populates is the R-F3099 shape: the field would exist
    and always be empty, and every future note would still be unpairable."""
    src = (ROOT / "aria_service/intel/collab_bridge.py").read_text(encoding="utf-8")
    assert "prompt=" in src, "drain_for_aria never passes a prompt to capture()"

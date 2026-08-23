"""R-F4275 / C-234 — an axis may only block promotion where the LLM is the mechanism.

The rule's whole claim to being trustworthy is that it independently reproduces a
verdict the operator reached by hand, after thirteen funded attempts, in R-F4259.
That agreement is the first test here, and if it ever breaks, the rule is wrong.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aria_service.intel.dd_standard import QUESTIONS_BY_ID  # noqa: E402
from scripts.train import axis_alignment as aa  # noqa: E402
from scripts.train import fundamentals_coverage as fc  # noqa: E402


# -- the validation ----------------------------------------------------------

def test_the_rule_reproduces_the_rf4259_decision() -> None:
    """THE LOAD-BEARING TEST.

    R-F4259 made `tooluse_resolution` advisory after thirteen candidates and
    arms, on the hand-built argument of R-F4257: production resolves identity
    deterministically and REPLACES the model's answer. This rule reaches the same
    verdict from `dd_standard` alone, having been told none of that. A rule that
    agrees with a decision earned the expensive way is worth more than one argued
    from first principles — and if this ever fails, believe R-F4259, not the rule.
    """
    verdict = aa.axis_mechanism("tooluse_resolution")
    assert verdict["mechanism"] == aa.DETERMINISTIC
    assert set(verdict["fundamentals"]) == {"EI-1", "EI-2"}
    assert "tooluse_resolution" in aa.recommended_advisory()


def test_the_axes_that_may_block_are_the_judgement_ones() -> None:
    blocking = {a for a, v in aa.classify_all().items() if v["mechanism"] == aa.LLM}
    assert blocking == {
        "tooluse_adverse", "tooluse_news_impact", "tooluse_contradiction",
        "tooluse_challenge", "tooluse_challenge_unavailable"}


def test_r_f4272s_own_axes_are_demoted_by_this_rule() -> None:
    """Recorded deliberately: the rule demotes what the same session built.

    The registry-depth axes read a register honestly — a real capability — but
    FS-11, FS-12 and OC-5 each have a deterministic reader, so production never
    asks the model. They stay as defence in depth and must not block a promotion
    or justify a GPU cycle.
    """
    for axis in ("tooluse_insolvency", "tooluse_charges", "tooluse_ownership"):
        assert aa.axis_mechanism(axis)["mechanism"] == aa.DETERMINISTIC, axis


# -- the classification itself ----------------------------------------------

def test_behaviour_axes_are_llm_even_over_a_reader_backed_question() -> None:
    """`tooluse_challenge` sits over IS-13, which HAS a reader.

    Classifying it by its underlying question would demote the honesty
    behaviours that are the entire point of the corpus. No deterministic reader
    can supply conduct.
    """
    assert aa.fundamental_mechanism("IS-13") == aa.DETERMINISTIC
    verdict = aa.axis_mechanism("tooluse_challenge")
    assert verdict["mechanism"] == aa.LLM
    assert "conduct" in verdict["why"]


def test_one_judgement_question_is_enough_to_make_an_axis_judgement_work() -> None:
    """tooluse_contradiction covers IS-13 (reader) and IS-15 (none)."""
    verdict = aa.axis_mechanism("tooluse_contradiction")
    assert verdict["fundamentals"] == {"IS-13": aa.DETERMINISTIC, "IS-15": aa.LLM}
    assert verdict["mechanism"] == aa.LLM


def test_the_classification_follows_the_LIVE_standard(monkeypatch) -> None:
    """Attaching a reader to a fundamental must move its axis to advisory.

    This is the event the rule exists to notice, so it is read live and never
    from a copy. `tooluse_adverse` may block only while IS-15 has no reader.
    """
    assert aa.axis_mechanism("tooluse_adverse")["mechanism"] == aa.LLM
    is15 = QUESTIONS_BY_ID["IS-15"]
    patched = dict(QUESTIONS_BY_ID)
    patched["IS-15"] = type("Q", (), {"id": "IS-15", "cluster": is15.cluster,
                                      "reader": lambda *a: None})()
    monkeypatch.setattr(aa, "QUESTIONS_BY_ID", patched)
    assert aa.axis_mechanism("tooluse_adverse")["mechanism"] == aa.DETERMINISTIC


def test_an_unclassifiable_axis_is_never_allowed_to_block() -> None:
    """Fail closed: 'I could not establish this measures real work' blocks spend."""
    assert aa.axis_mechanism("tooluse_invented")["mechanism"] == aa.UNKNOWN
    assert aa.blocking_violations({"tooluse_invented"})


# -- the gate closes AND opens ----------------------------------------------

def test_blocking_violations_catches_a_misaligned_gate() -> None:
    violations = aa.blocking_violations(
        {"tooluse_resolution", "tooluse_trace", "tooluse_adverse"})
    assert len(violations) == 2
    assert any("tooluse_resolution" in v for v in violations)
    assert any("tooluse_trace" in v for v in violations)
    assert not any("tooluse_adverse" in v for v in violations)


def test_an_aligned_gate_passes_cleanly() -> None:
    """A check that cannot pass is not a check."""
    assert aa.blocking_violations(
        {"tooluse_adverse", "tooluse_news_impact", "tooluse_contradiction"}) == []


def test_every_declared_axis_is_classifiable() -> None:
    for axis in fc.AXIS_COVERAGE:
        assert aa.axis_mechanism(axis)["mechanism"] in (aa.LLM, aa.DETERMINISTIC), axis


# -- the measurement that decides where money goes ---------------------------

def test_the_parent_has_one_row_of_headroom_where_it_matters() -> None:
    """THE NUMBER. 93/94 on the axes that measure the model's real job.

    Thirteen candidates were funded fighting over defence-in-depth axes while
    the work the product actually uses the model for was already at ceiling. No
    further cycle against THIS corpus can buy the product anything.
    """
    report = json.loads((ROOT / "data/eval_reports"
                         / "aria_tooluse_parent_rf4274_rescored.json"
                         ).read_text(encoding="utf-8"))
    verdicts = aa.classify_all()
    honest = total = 0
    for axis in report["per_axis"]:
        if verdicts.get(axis["label"], {}).get("mechanism") == aa.LLM:
            honest += int(axis["honest"])
            total += int(axis["total"])
    assert (honest, total) == (93, 94)


def test_the_ten_unserved_fundamentals_are_named() -> None:
    """Where a cycle CAN still buy something — this is the target list."""
    llm_only = {fid for fid in QUESTIONS_BY_ID
                if aa.fundamental_mechanism(fid) == aa.LLM}
    assert llm_only == {"EI-3", "EI-4", "OC-7", "OC-8", "FS-9",
                        "IS-14", "IS-15", "IS-16", "LR-19", "LR-20"}
    # and only ONE of them has any eval row at all today
    covered = {f for a, e in fc.AXIS_COVERAGE.items() for f in e["fundamentals"]}
    assert llm_only & covered == {"IS-15"}

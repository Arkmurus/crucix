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


#: The ORIGINAL ten reader-less fundamentals, as measured by R-F4275 on
#: 2026-08-23. Binding readers to these is the product work C-235 recommends, so
#: this set may only ever SHRINK. It is recorded as history, never as an
#: expectation — the live set is derived below.
ORIGINALLY_UNBOUND = frozenset({
    "EI-3", "EI-4", "OC-7", "OC-8", "FS-9", "IS-14", "IS-15", "IS-16",
    "LR-19", "LR-20",
})


def _an_unbound_fundamental() -> str:
    """Any fundamental nothing answers yet, chosen live.

    Naming one in a test couples it to product progress: IS-15 was the exemplar
    until R-F4277 bound it, then IS-14 until R-F4279 bound it. Deriving it means
    these tests keep testing the RULE instead of the state of the roadmap.
    """
    live = sorted(fid for fid in QUESTIONS_BY_ID if aa.is_unbound(fid))
    assert live, "every fundamental is bound - update these tests deliberately"
    return live[0]


def _a_bound_fundamental() -> str:
    live = sorted(fid for fid in QUESTIONS_BY_ID
                  if aa.fundamental_mechanism(fid) == aa.DETERMINISTIC)
    assert live
    return live[0]


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


def test_only_resolution_is_advisory_and_only_on_evidence() -> None:
    """R-F4278 - reader-presence alone demotes NOTHING.

    The first version returned five may-block axes on reader-presence alone.
    Binding a reader to IS-15 then demoted tooluse_adverse on the spot, and the
    reductio is that binding readers to the remaining unbound questions - which
    is GOOD product work - would eventually leave nothing able to block. A gate
    that dissolves as the product improves is not a gate.
    """
    advisory = {a for a, v in aa.classify_all().items()
                if v["mechanism"] == aa.DETERMINISTIC}
    assert advisory == {"tooluse_resolution"}
    assert set(aa.OUTPUT_OVERRIDDEN) == {"tooluse_resolution"}


def test_reader_backed_axes_are_candidates_not_demotions() -> None:
    """The registry-depth axes this session built ARE reader-backed candidates.

    R-F4275 demoted them outright. R-F4278 corrected that: a reader answers the
    QUESTION without silencing the model's narrative, so a candidate keeps
    blocking until someone MEASURES that its output does not reach the customer.
    Conservative in the safe direction - promotion gets harder, never easier.
    """
    for axis in ("tooluse_insolvency", "tooluse_charges", "tooluse_ownership",
                 "tooluse_trace", "tooluse_person"):
        verdict = aa.axis_mechanism(axis)
        assert verdict["advisory_candidate"] is True, axis
        assert verdict["override_evidence"] is None, axis
        assert verdict["mechanism"] == aa.NOT_DETERMINISTIC, axis


def test_a_demotion_cannot_happen_without_recorded_evidence(monkeypatch) -> None:
    """Strip the evidence and resolution must start blocking again."""
    monkeypatch.setattr(aa, "OUTPUT_OVERRIDDEN", {})
    assert aa.axis_mechanism("tooluse_resolution")["mechanism"] == aa.NOT_DETERMINISTIC
    assert aa.recommended_advisory() == []


def test_every_override_entry_carries_real_evidence() -> None:
    """An override with no evidence is the exemption this rule exists to refuse."""
    for axis, evidence in aa.OUTPUT_OVERRIDDEN.items():
        assert axis in fc.AXIS_COVERAGE, axis
        assert len(str(evidence)) > 120, axis
        assert "R-F" in str(evidence), axis


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


def test_one_reader_less_question_is_enough_to_keep_an_axis_blocking(monkeypatch) -> None:
    """The mixed branch, exercised directly on a LIVE-chosen pair.

    No shipped axis mixes a reader-backed and a reader-less fundamental today, so
    the branch is covered with a synthetic declaration rather than left untested.
    Both ids are derived: naming them broke this test twice as readers were bound.
    """
    bound, unbound = _a_bound_fundamental(), _an_unbound_fundamental()
    monkeypatch.setitem(fc.AXIS_COVERAGE, "tooluse_synthetic_mixed",
                        {"kind": fc.FUNDAMENTAL, "fundamentals": (bound, unbound),
                         "why": "synthetic: one bound question, one unbound"})
    verdict = aa.axis_mechanism("tooluse_synthetic_mixed")
    assert verdict["fundamentals"] == {bound: aa.DETERMINISTIC,
                                       unbound: aa.NOT_DETERMINISTIC}
    assert verdict["mechanism"] == aa.NOT_DETERMINISTIC
    assert verdict["unbound"] == [unbound]


def test_binding_a_reader_makes_a_candidate_but_does_NOT_demote() -> None:
    """THE R-F4278 LESSON, pinned on the axis that taught it.

    IS-15 is now reader-backed (R-F4277 bound it to the sweep that already runs),
    so tooluse_adverse is a CANDIDATE - and it still blocks, because nothing has
    measured that the model's adverse narrative fails to reach the customer.
    """
    assert aa.fundamental_mechanism("IS-15") == aa.DETERMINISTIC
    verdict = aa.axis_mechanism("tooluse_adverse")
    assert verdict["advisory_candidate"] is True
    assert verdict["mechanism"] == aa.NOT_DETERMINISTIC
    assert "keeps blocking" in verdict["why"]


def test_an_unclassifiable_axis_is_never_allowed_to_block() -> None:
    """Fail closed: 'I could not establish this measures real work' blocks spend."""
    assert aa.axis_mechanism("tooluse_invented")["mechanism"] == aa.UNKNOWN
    assert aa.blocking_violations({"tooluse_invented"})


# -- the gate closes AND opens ----------------------------------------------

def test_blocking_violations_catches_a_misaligned_gate() -> None:
    violations = aa.blocking_violations(
        {"tooluse_resolution", "tooluse_trace", "tooluse_adverse"})
    assert len(violations) == 1
    assert any("tooluse_resolution" in v for v in violations)
    assert not any("tooluse_trace" in v for v in violations)


def test_an_aligned_gate_passes_cleanly() -> None:
    """A check that cannot pass is not a check."""
    assert aa.blocking_violations(
        {"tooluse_adverse", "tooluse_news_impact", "tooluse_contradiction"}) == []


def test_every_declared_axis_is_classifiable() -> None:
    for axis in fc.AXIS_COVERAGE:
        assert aa.axis_mechanism(axis)["mechanism"] in (
            aa.NOT_DETERMINISTIC, aa.DETERMINISTIC), axis


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
        if verdicts.get(axis["label"], {}).get("mechanism") == aa.NOT_DETERMINISTIC:
            honest += int(axis["honest"])
            total += int(axis["total"])
    # everything except the one EVIDENCED advisory axis; headroom is still tiny
    assert (honest, total) == (150, 152)
    assert total - honest == 2


def test_the_ten_unserved_fundamentals_are_named() -> None:
    """Where a cycle CAN still buy something — this is the target list."""
    unbound = {fid for fid in QUESTIONS_BY_ID
               if aa.fundamental_mechanism(fid) == aa.NOT_DETERMINISTIC}
    # BINDING IS MONOTONIC PROGRESS. IS-15 left this set at R-F4277 and IS-14 at
    # R-F4279; the set may shrink freely, but a fundamental that was answerable
    # becoming unanswerable is a regression this asserts against.
    assert unbound <= ORIGINALLY_UNBOUND, sorted(unbound - ORIGINALLY_UNBOUND)
    assert unbound, "if this empties, the rule needs a deliberate revisit"
    covered = {f for a, e in fc.AXIS_COVERAGE.items() for f in e["fundamentals"]}
    assert not (unbound & covered), "an unbound fundamental now has an eval axis"


# -- R-F4276 · the correction -----------------------------------------------

def test_unbound_is_not_the_same_as_answered_by_the_model() -> None:
    """R-F4275 first claimed the ten reader-less fundamentals are "where the
    model IS the mechanism". That was never measured, and it is false.

    `dd_standard.assess` distinguishes the two cases in its own words: a bound
    reader that looked and found nothing says "no sanctions screen is recorded on
    this report"; an unbound question says "no resolver is bound to this question
    in this build". The second means NOBODY answers it — the model included.
    """
    from aria_service.intel import dd_standard as ds
    report = {"subject": {"name": "TEST LTD", "jurisdiction": "GB"}}
    rows = {r["question_id"]: r for r in ds.assess(report, tier="ENHANCED")["resolutions"]}

    unbound, bound = _an_unbound_fundamental(), _a_bound_fundamental()
    # an unbound question: NOBODY looked
    assert "no resolver is bound" in rows[unbound]["reason"]
    assert aa.is_unbound(unbound) is True
    # a bound one: the reader LOOKED and found nothing on this report
    assert "no resolver is bound" not in rows[bound]["reason"]
    assert aa.is_unbound(bound) is False


def test_the_bindings_this_work_shipped_are_still_in_place() -> None:
    """R-F4277 bound IS-15 (C-235); R-F4279 bound IS-14 (C-238).

    Named deliberately here and nowhere else: this is the one test that SHOULD
    fail if either binding is reverted.
    """
    assert aa.is_unbound("IS-15") is False, "R-F4277 binding lost"
    assert aa.is_unbound("IS-14") is False, "R-F4279 binding lost"


def test_the_legacy_name_still_resolves() -> None:
    """`LLM` was the wrong NAME for the class; callers must not break."""
    assert aa.LLM == aa.NOT_DETERMINISTIC

"""R-F2688 — the C-3 independence eval must be able to SEE the PR-echo residual.

The v2 golden set has no PR-echo case, so it structurally cannot score a PR-echo
detector — it would hand a clean sheet to a classifier that fabricates independence
on exactly the input R-F2677 documented as open. This broadens it to v3.

These tests deliberately test the EVAL, not the classifier-of-the-day:

  - Asserting "the shipped classifier false-positives on PR-echo" would CODIFY THE
    BUG — the test would go red the moment C-3 v3 (R-F2687) fixes it, and someone
    would then "fix" the test. (Three old tests codified exactly this class of bug
    per CLAUDE.md §1.) So instead we assert the golden set has DISCRIMINATING POWER:
    a perfect oracle scores clean, and a naive count-the-domains classifier does NOT.
  - The residual is REPORTED (via residual_report) rather than asserted, so the
    number moves as the classifier improves without anything to "fix" here.
"""
from __future__ import annotations

import pytest

from aria_service.intel.dd_independence_eval import (
    load_golden,
    residual_report,
    run_v3_eval,
    score_independence,
    _GOLDEN_V3,
)


@pytest.fixture(scope="module")
def golden() -> dict:
    return load_golden(_GOLDEN_V3)


def test_v3_is_a_superset_of_v2(golden):
    """v3 must keep every v2 case, so it is also a v2 regression — never a fork."""
    from aria_service.intel.dd_independence_eval import _GOLDEN_V2

    v2_ids = {c["id"] for c in load_golden(_GOLDEN_V2)["cases"]}
    v3_ids = {c["id"] for c in golden["cases"]}
    assert v2_ids <= v3_ids, f"v3 dropped v2 cases: {sorted(v2_ids - v3_ids)}"


def test_v3_contains_the_pr_echo_residual_and_the_discriminators(golden):
    """CAPABILITY: the set can now express the residual v2 could not represent."""
    cases = {c["id"]: c for c in golden["cases"]}

    pr_echo = [c for cid, c in cases.items() if cid.startswith("pr_echo_")]
    assert pr_echo, "no PR-echo case — the residual stays unmeasurable"
    # A PR echo is ONE origin (the subject's own announcement), never corroboration.
    assert all(c["expected"] is False for c in pr_echo)
    # The residual only exists because rewording DIVERGES the story fingerprints —
    # if the fixture gave them a shared story, the shipped classifier would already
    # catch it and the case would be testing nothing.
    for c in pr_echo:
        stories = [s["story"] for s in c["sources"]]
        assert len(set(stories)) == len(stories), (
            f"{c['id']}: sources share a story fingerprint — that is wire syndication, "
            "already caught by R-F2669. A reworded PR must have DISTINCT stories."
        )
        domains = [s["domain"] for s in c["sources"]]
        assert len(set(domains)) == len(domains), f"{c['id']}: needs distinct publishers"

    # The discriminator: genuinely independent newsrooms on one event stay corroborated.
    disc = cases["independent_investigations_same_event"]
    assert disc["expected"] is True
    assert len({s["domain"] for s in disc["sources"]}) >= 2


def test_new_cases_carry_raw_text_so_a_classifier_can_derive_its_own_signals(golden):
    """Sources must carry the raw body the LIVE re-fetch has — not a precomputed label.

    A fixture that hands the classifier its answer proves nothing (cf. the R-F2638
    fixtures that scored 7/7 green and 0/20 on real data).
    """
    for c in golden["cases"]:
        if c["id"].startswith("pr_echo_") or c["id"].startswith("independent_"):
            for s in c["sources"]:
                assert s.get("text"), f"{c['id']}: source {s['domain']} has no text"
                assert len(s["text"].split()) >= 15, "text too short to shingle/quote"


def test_golden_labels_are_self_consistent_under_a_perfect_oracle(golden):
    """The labels must be satisfiable: an oracle reading `expected` scores a clean sheet.

    This is what makes a later non-zero FP-rate attributable to the CLASSIFIER rather
    than to a contradictory golden set.
    """
    cases = golden["cases"]
    # Keyed on ITERATION ORDER, not on the sources object: score_independence passes
    # `c.get("sources") or []`, which mints a fresh list for the empty-sources case,
    # so an identity/equality lookup cannot find it.
    answers = iter([bool(c.get("expected")) for c in cases])
    oracle = lambda _sources: next(answers)  # noqa: E731

    r = score_independence(cases, oracle)
    assert r["false_positive_rate"] == 0.0
    assert r["recall"] == 1.0
    assert r["fp"] == 0 and r["fn"] == 0


def test_v3_has_discriminating_power_against_a_naive_classifier(golden):
    """CAPABILITY: the set must FAIL a classifier that just counts distinct domains.

    This is the property that makes the eval worth running. It is asserted against a
    naive classifier defined HERE — never against the shipped one — so improving the
    shipped classifier can never turn this test red.
    """
    naive = lambda sources: len({  # noqa: E731
        s.get("domain") for s in sources if isinstance(s, dict)
    }) >= 2

    r = score_independence(golden["cases"], naive)
    # The naive rule fabricates independence on the PR echoes → the gate catches it.
    assert r["false_positive_rate"] > 0.0
    assert any(str(c).startswith("pr_echo_") for c in r["false_positive_cases"]), (
        "PR-echo cases did not discriminate against a count-the-domains classifier"
    )


def test_residual_report_runs_and_states_the_gate_honestly():
    """CAPABILITY: the operator-facing verdict computes on the SHIPPED classifier.

    Asserts SHAPE and the gate's meaning, not a score — the score is expected to move
    when C-3 v3 (R-F2687) lands, and this test must not need editing when it does.
    """
    rep = residual_report()

    assert set(rep) == {
        "overall", "gate_met", "residual_case_ids",
        "residual_failures", "discriminator_failures",
    }
    assert rep["residual_case_ids"], "residual cases must be identifiable"
    # gate_met is load-bearing: it must be exactly "no claim was wrongly called
    # corroborated", never a softer reading.
    assert rep["gate_met"] == (rep["overall"]["false_positive_rate"] == 0.0)
    # Any residual failure is by definition a false positive of the whole set.
    assert set(rep["residual_failures"]) <= set(rep["overall"]["false_positive_cases"])


def test_rf2690_wiring_passes_textless_v2_sources_through_untouched(golden):
    """R-F2690: v2 cases have no `text`, so their golden story label must survive.

    If the text-level wiring rewrote them, v3 would stop being a v2 superset
    regression and the old cases would silently start measuring something else.
    """
    from aria_service.intel.dd_independence_eval import _computed_story_sources

    v2_case = next(c for c in golden["cases"] if c["id"] == "wire_syndication")
    out = _computed_story_sources(v2_case["sources"])
    assert out == v2_case["sources"]


def test_rf2690_wiring_replaces_golden_labels_with_text_clustered_ids(golden):
    """CAPABILITY: the eval must actually EXERCISE the text-level detector.

    R-F2687's detector lives in the clustering path; scoring it through the
    label-level classifier would touch none of it and report the residual as
    still-open — a false negative about the fix. Assert the story ids the
    classifier sees are DERIVED FROM TEXT, not the fixture's precomputed labels.
    """
    from aria_service.intel.dd_independence_eval import _computed_story_sources

    case = next(c for c in golden["cases"] if c["id"] == "pr_echo_two_outlets_reworded")
    golden_labels = {s["story"] for s in case["sources"]}
    out = _computed_story_sources(case["sources"])

    assert len(out) == len(case["sources"])
    seen = {s["story"] for s in out if s.get("story")}
    assert seen, "no story ids computed — the text path did not run"
    assert not (seen & golden_labels), (
        "story ids still equal the fixture's precomputed labels — the classifier is "
        "not exercising the text-level detector"
    )


def test_rf2690_v2_cases_score_identically_under_both_classifiers(golden):
    """The text wiring must not disturb any pre-existing v2 verdict."""
    from aria_service.intel.dd_independence_eval import (
        _GOLDEN_V2,
        v2_verifier_classifier,
        v3_echo_classifier,
    )

    for c in load_golden(_GOLDEN_V2)["cases"]:
        src = c.get("sources") or []
        assert v3_echo_classifier(src) == v2_verifier_classifier(src), (
            f"v2 case {c['id']} changed verdict under the v3 wiring"
        )


def test_run_v3_eval_accepts_a_candidate_classifier():
    """A candidate (e.g. C-3 v3) must be scorable WITHOUT editing the eval module."""
    always_no = lambda sources: False  # noqa: E731

    r = run_v3_eval(always_no)
    # Refusing to ever claim corroboration is maximally conservative: it cannot
    # fabricate independence, so it always passes the gate — at zero recall. This
    # pins the gate's direction: FP-rate 0 alone is NOT sufficient for `enforce`.
    assert r["false_positive_rate"] == 0.0
    assert r["recall"] == 0.0
    assert r["fp"] == 0

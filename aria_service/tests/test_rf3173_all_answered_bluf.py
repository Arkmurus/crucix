"""R-F3173 — the BLUF told a customer coverage was incomplete when coverage was 100%.

First reached live on Babcock (dd_3352a8116187), once R-F3166 let the sanctions screen
actually run. The readiness scorecard came back:

    answered 5 / 5,  completion_pct 100,  blockers null,
    evidence_grade "B",  clearance_ready false,  evidence_ready false

No DD had ever answered all five questions before, so this branch had never executed.
It produced:

    "🟡 NOT CLEARED — ... has no blocking risk in the checks that completed, BUT all
     5/5 decision-critical questions are answered, BUT the evidence behind them does
     not yet meet the reliance bar. Unresolved: decision-critical coverage is
     incomplete."

Two defects in one sentence:

  1. ungrammatical — `_coverage_clause` already carries its own "but", so the template
     produced "but ... but ...";
  2. FALSE — it says coverage is incomplete while coverage is 100%. `labels` was empty
     (nothing unanswered), so `open_clause` fell through to its placeholder string.

(2) is the serious one. The remedy for a missing check (go run it) is nothing like the
remedy for a single-sourced claim (corroborate it). Naming the wrong obstacle sends the
reader to fix the wrong thing — the R-F3125 defect class, on the customer-facing line
that matters most.
"""
import pytest

from aria_service.intel.dd_orchestrator import compose_decision_bluf


NAME = "Babcock International Group plc"
LABELS = [
    ("identity", "Verified legal identity"),
    ("sanctions_export", "Sanctions and export-control exposure"),
    ("adverse_media", "Adverse media, corruption and litigation"),
    ("ownership", "Ownership and control"),
    ("financial_capacity", "Financial capacity"),
]


def _readiness(answered_keys, *, grade="B", clearance=False, evidence=False):
    return {
        "status": "DECISION_READY_FOR_HUMAN_REVIEW" if clearance else "NOT_CLEARED",
        "answered": len(answered_keys),
        "required": 5,
        "clearance_ready": clearance,
        "evidence_ready": evidence,
        "evidence_grade": grade,
        "questions": {
            k: {"label": l, "answered": k in answered_keys,
                "status": "ANSWERED" if k in answered_keys else "UNRESOLVED"}
            for k, l in LABELS
        },
    }


ALL = [k for k, _ in LABELS]


def test_rf3173_does_not_claim_incomplete_coverage_when_complete():
    """THE LIVE DEFECT."""
    b = compose_decision_bluf(_readiness(ALL), NAME)["bottom_line"]
    assert "coverage is incomplete" not in b.lower(), (
        f"R-F3173 REGRESSION: coverage reported incomplete at 5/5.\n{b}")
    assert "Unresolved:" not in b, (
        f"nothing is unresolved — an empty 'Unresolved:' list must not appear.\n{b}")


def test_rf3173_sentence_is_not_malformed():
    b = compose_decision_bluf(_readiness(ALL), NAME)["bottom_line"]
    assert b.lower().count("but ") <= 1, f"double 'but':\n{b}"


def test_rf3173_names_the_real_obstacle():
    """Evidence STRENGTH, with its grade — not a missing check."""
    out = compose_decision_bluf(_readiness(ALL, grade="B"), NAME)
    b = out["bottom_line"]
    assert "grade B" in b, b
    assert "corroboration gap" in b.lower(), b
    assert "not a missing check" in b.lower(), b
    assert "ANSWERED" in b, "the customer must be told all five ARE answered"


def test_rf3173_remedy_matches_the_obstacle():
    """A corroboration gap is fixed by corroborating, not by running more checks."""
    out = compose_decision_bluf(_readiness(ALL), NAME)
    actions = " ".join(out["next_actions"]).lower()
    assert "second independent" in actions, actions
    assert "corroborat" in actions, actions
    assert "do not rely on this report" in out["recommendation"].lower()


def test_rf3173_still_refuses_clearance():
    """Coverage being complete must NOT be read as cleared — evidence_ready is false."""
    b = compose_decision_bluf(_readiness(ALL), NAME)["bottom_line"]
    assert "NOT CLEARED" in b
    assert "🟢" not in b


def test_rf3173_grade_is_reported_even_when_absent():
    b = compose_decision_bluf(_readiness(ALL, grade=""), NAME)["bottom_line"]
    assert "grade ?" in b, f"a missing grade must not render as an empty string:\n{b}"


def test_rf3173_cleared_reports_are_untouched():
    """The GREEN branch runs before this one and must be unaffected."""
    out = compose_decision_bluf(
        _readiness(ALL, grade="A", clearance=True, evidence=True), NAME)
    assert "🟢 GREEN" in out["bottom_line"]
    assert "Standard contracting path available" in out["bottom_line"]


@pytest.mark.parametrize("missing", [k for k, _ in LABELS])
def test_rf3173_partial_coverage_still_lists_the_open_items(missing):
    """The ordinary path must keep naming what is actually unresolved."""
    answered = [k for k in ALL if k != missing]
    label = dict(LABELS)[missing]
    b = compose_decision_bluf(_readiness(answered), NAME)["bottom_line"]
    assert "Unresolved:" in b
    assert label in b, f"{label!r} must be named as unresolved:\n{b}"
    assert "corroboration gap" not in b.lower(), (
        "the all-answered branch must not fire while a question is open")

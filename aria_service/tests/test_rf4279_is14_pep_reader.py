"""R-F4279 / C-238 — IS-14 is answered by the PEP and RCA screens that already run.

Same shape as C-235, one question along. `dd_standard` declared IS-14
("Politically exposed persons among the controllers or their close associates")
with `reader=None`, so it rendered NOT_RUN "no resolver is bound to this question
in this build" while TWO screens ran on the same report:

  * the network layer screens every enumerated officer and promotes a `role.pep` /
    `role.pol` topic hit into `network.pep_connections` (dd_orchestrator:6994,
    network_walker:313 via `_sanctions_classify.classify_matches`);
  * `rca_screening.screen_with_relatives` runs in the `deterministic_primitives`
    layer (dd_orchestrator:16626) and writes `report.rca_relatives`.

IS-14 NAMES BOTH POPULATIONS — controllers *and* their close associates — so the
reader must not answer it from one screen while the other never ran, and must not
answer it at all when no controller was ever enumerated. A DD whose identity
resolution failed has no officer list; "no PEPs among the controllers" is then a
statement about a population nobody assembled.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aria_service.intel import dd_standard as ds  # noqa: E402

ANSWERED = {ds.EvidenceState.CORROBORATED.value, ds.EvidenceState.SINGLE_SOURCE.value}


def _report(*, directors=("A DIRECTOR",), network_status="ok",
            pep=(), rca=None, **extra) -> dict:
    report = {
        "subject": {"name": "TEST LTD", "jurisdiction": "GB"},
        "identity": {"entity_name": "TEST LTD",
                     "directors": [{"name": d} for d in directors]},
        "network": {"meta": {"status": network_status},
                    "pep_connections": list(pep)},
        **extra,
    }
    if rca is not None:
        report["rca_relatives"] = rca
    return report


def _is14(**kw) -> dict:
    rows = ds.assess(_report(**kw), tier="ENHANCED")["resolutions"]
    return {r["question_id"]: r for r in rows}["IS-14"]


def _rca(*, screened=3, risks=(), unavailable=False, ok=True) -> dict:
    if unavailable:
        return {"name": "TEST LTD", "source_unavailable": True,
                "relatives_screened": 0, "inherited_risks": []}
    if not ok:
        return {"name": "TEST LTD", "ok": False, "error": "fuzzy_screen failed"}
    return {"name": "TEST LTD", "primary_matches": 0,
            "relatives_screened": screened, "inherited_risks": list(risks),
            "depth": 1}


# ── the defect ─────────────────────────────────────────────────────────────

def test_a_completed_screen_is_no_longer_reported_as_unbound() -> None:
    """THE CAPABILITY TEST — the live symptom C-238 files."""
    row = _is14(rca=_rca())
    assert "no resolver is bound" not in str(row["reason"])
    assert row["state"] in ANSWERED, row


def test_a_clean_screen_credits_coverage() -> None:
    before = ds.assess(_report(), tier="ENHANCED")
    after = ds.assess(_report(rca=_rca()), tier="ENHANCED")
    assert after["answered"] > before["answered"]
    assert after["coverage_pct"] > before["coverage_pct"]


def test_a_pep_among_the_controllers_is_reported() -> None:
    row = _is14(rca=_rca(), pep=[{
        "name": "A DIRECTOR", "role": "director", "severity": "amber",
        "source": "sanctions/PEP screen",
        "matches": [{"dataset": "everypolitician"}]}])
    assert row["state"] in ANSWERED
    assert "director" in str(row["reason"]).lower() or "1" in str(row["reason"])


def test_an_inherited_risk_from_a_relative_is_reported() -> None:
    row = _is14(rca=_rca(risks=[{
        "primary": "TEST LTD", "relative": "A RELATIVE",
        "relationship": "spouse", "relative_lists": ["us_cia_world_leaders"]}]))
    assert row["state"] in ANSWERED
    assert "relative" in str(row["reason"]).lower()


# ── never a fabricated clean ───────────────────────────────────────────────

def test_nothing_on_the_report_is_not_run() -> None:
    row = _is14(directors=(), network_status="skipped")
    assert row["state"] == ds.EvidenceState.NOT_RUN.value


def test_no_controller_enumerated_cannot_clear_the_controllers() -> None:
    """THE POPULATION TRAP.

    With no officer list there is nobody to screen, so `pep_connections` is empty
    for a reason that has nothing to do with PEP status. Reading that as clean
    would clear a population nobody assembled — the C-39 failure applied to people.
    The relatives screen alone does not answer a question about CONTROLLERS.
    """
    row = _is14(directors=(), rca=_rca(screened=4))
    assert row["state"] not in ANSWERED
    assert "controller" in str(row["reason"]).lower()


def test_a_network_layer_that_did_not_run_cannot_clear_the_controllers() -> None:
    for status in ("skipped", "error", "prereq_fail"):
        row = _is14(network_status=status, rca=_rca())
        assert row["state"] not in ANSWERED, status


def test_an_unavailable_rca_source_is_attempted_not_clean() -> None:
    """R-F2373 already records this case so the DD says UNVERIFIED, not clear."""
    row = _is14(rca=_rca(unavailable=True))
    assert row["state"] == ds.EvidenceState.ATTEMPTED_INCONCLUSIVE.value
    assert row["state"] not in ANSWERED


def test_a_failed_rca_screen_is_attempted_not_clean() -> None:
    row = _is14(rca=_rca(ok=False))
    assert row["state"] == ds.EvidenceState.ATTEMPTED_INCONCLUSIVE.value


def test_controllers_screened_but_relatives_never_is_not_the_full_question() -> None:
    """IS-14 names controllers AND close associates.

    The officer screen alone answers half the question. That is honest partial
    evidence, not a clean line on the whole of it.
    """
    row = _is14(rca=None)
    assert row["state"] == ds.EvidenceState.ATTEMPTED_INCONCLUSIVE.value
    assert "associate" in str(row["reason"]).lower() or "relative" in str(row["reason"]).lower()


def test_a_finding_answers_even_when_the_other_half_never_ran() -> None:
    """A PEP that WAS found is evidence regardless of the rest of the sweep."""
    row = _is14(rca=None, pep=[{"name": "A DIRECTOR", "severity": "amber",
                                "matches": [{"dataset": "everypolitician"}]}])
    assert row["state"] in ANSWERED


def test_a_malformed_block_is_never_an_answer() -> None:
    for junk in ("screened", 0, [], {"relatives_screened": "three"}):
        row = _is14(rca=junk)
        assert row["state"] not in ANSWERED, junk


# ── it must not disturb anything else ──────────────────────────────────────

def test_binding_is14_changes_no_other_question() -> None:
    base = _report()
    before = {r["question_id"]: r["state"]
              for r in ds.assess(base, tier="ENHANCED")["resolutions"]}
    after = {r["question_id"]: r["state"] for r in
             ds.assess(_report(rca=_rca()), tier="ENHANCED")["resolutions"]}
    assert {q for q in before if before[q] != after.get(q)} == {"IS-14"}


def test_the_reader_is_actually_bound() -> None:
    assert ds.QUESTIONS_BY_ID["IS-14"].reader is not None


def test_is15_is_unaffected_by_this_change() -> None:
    """The previous binding must keep behaving exactly as R-F4277 left it."""
    rows = {r["question_id"]: r for r in ds.assess(
        _report(adverse_media={"ok": True, "templates_searched": 8,
                               "search_backends_answered": 2, "findings": []}),
        tier="ENHANCED")["resolutions"]}
    assert rows["IS-15"]["state"] == ds.EvidenceState.SINGLE_SOURCE.value

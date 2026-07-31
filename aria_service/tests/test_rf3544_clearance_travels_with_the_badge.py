"""R-F3544 — a GREEN badge stood alone over a "NOT CLEARED" report.

THE DEFECT, from the delivered Bidvest Noonan report (dd_75d996233394). The reports list
showed a GREEN badge, and the report's own assessment read "NOT CLEARED — ... this report
cannot state whether blocking risk exists", coverage 3/5. In the PDF a large GREEN pill
sat beside the words NOT CLEARED with no caption on either.

The colour and the text answer DIFFERENT QUESTIONS. `risk_classification` aggregates
adverse SIGNALS — "did anything bad surface?" — while clearance asks "is there enough
evidence to clear this?". Both readings are individually defensible, which is exactly what
makes the pair dangerous: the visually dominant element carried the permissive one, and a
client who skims sees green and proceeds on a report whose own words cannot clear them.

TWO LABELLED FACTS, NOT A RECOLOUR. R-F2786 separates risk from clearance BY DESIGN — its
test asserts `risk_classification == "GREEN"` under the comment "observed risk remains
separate". Capping the colour at the clearance status was tried (R-F3537) and REVERTED:
it overrode a considered design and destroyed information. Labelling keeps both facts and
makes neither mistakable for the other.

THE PLUMBING WAS THE REAL BLOCKER. The list row carried no clearance field at all, so the
web badge had nothing to render but the colour — the surface could not have been fixed
without the data. That is why this ships the index row, the web pill and the PDF captions
together; two of the three would have left the defect visible on the third.
"""
from __future__ import annotations

import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
ORCH = (ROOT / "aria_service" / "intel" / "dd_orchestrator.py").read_text(
    encoding="utf-8", errors="replace")
WEB = (ROOT / "public" / "dd-reports.html").read_text(encoding="utf-8", errors="replace")
PDF = (ROOT / "lib" / "reports" / "pdf_generator.mjs").read_text(
    encoding="utf-8", errors="replace")


# ── the data must travel ────────────────────────────────────────────────────

@pytest.mark.parametrize("field", [
    "clearance_ready", "clearance_status", "coverage_answered", "coverage_required",
])
def test_the_index_row_carries_clearance(field):
    """Without this the badge has nothing to render but the colour."""
    assert f'"{field}"' in ORCH, (
        f"{field} is not written to the report index row — the list cannot show clearance")


def test_the_index_row_reads_decision_readiness_not_a_guess():
    assert "(report.decision_readiness or {}).get(\"clearance_ready\")" in ORCH, (
        "clearance must come from the readiness layer, not be re-derived")


# ── the web surface ─────────────────────────────────────────────────────────

def test_the_web_renders_a_labelled_clearance_pill():
    assert "function clearancePill" in WEB
    assert "Clearance: NOT AVAILABLE" in WEB
    assert "Clearance: READY" in WEB


def test_the_risk_pill_and_clearance_pill_are_shown_together():
    """Rendering one without the other is the defect. `rowStatusPill` is the single
    place every list row's badge is built."""
    i = WEB.index("function rowStatusPill")
    body = WEB[i: i + 700]
    assert "severityPill(sev) + clearancePill(r)" in body, (
        "the clearance pill is not rendered beside the risk pill")


def test_an_unmeasured_clearance_renders_NOTHING():
    """Tri-state. Reports written before this carry no clearance, and a badge reading
    'unknown' on every historical row is noise that trains people to ignore the new one."""
    i = WEB.index("function clearancePill")
    body = WEB[i: i + 1400]
    assert "ready !== true && ready !== false" in body, (
        "absent clearance must render nothing, not 'unknown'")
    assert "return ''" in body


def test_the_not_available_pill_says_it_is_not_a_finding_of_risk():
    """An amber badge that reads as 'we found something' is its own false claim."""
    i = WEB.index("function clearancePill")
    body = WEB[i: i + 1400]
    assert "Not a finding of risk" in body


# ── the PDF must not disagree with the page (R-F3055 mirror contract) ───────

def test_the_pdf_labels_BOTH_badges():
    """A caption online and none in the filed PDF leaves the worse half as the one the
    client keeps."""
    assert "'RISK SIGNAL'" in PDF, "the PDF risk pill is still unlabelled"
    assert "'CLEARANCE'" in PDF, "the PDF clearance status is still unlabelled"


def test_the_pdf_still_prints_both_values():
    """Labelling must not have displaced either fact."""
    i = PDF.index("function addVerdictBlock")
    body = PDF[i: i + 2600]
    assert "clearance_ready === true" in body
    assert "status.replace(/_/g, ' ')" in body
    assert "decision-critical questions answered" in body


def test_the_pdf_colour_is_NOT_capped_at_clearance():
    """R-F2786 separates the two by design; R-F3537 tried capping and was reverted.
    The PDF must keep printing the observed risk, not a merged signal."""
    i = PDF.index("function addVerdictBlock")
    body = PDF[i: i + 2600]
    assert "_verdictColour(raw)" in body, (
        "the risk colour is being derived from something other than the risk value")

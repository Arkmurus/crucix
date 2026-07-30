"""R-F3496 — a DD report now says WHICH RULES produced it.

THE AUDIT DEFECT. A report pinned `schema_version` (the shape of the document) and
`generator` (who wrote it). Neither says anything about the DECISION LOGIC. So a report
read six months later was interpreted under whatever rules existed *then*: a verdict could
change with no change in evidence, and nothing in the document revealed it.

That is precisely the question a regulated buyer's auditor asks — *is this the finding
that was made, or a finding re-interpreted under later rules?* — and ARIA could not
answer it.

Concretely: sixteen fixes shipped on 2026-07-30 changed what findings a DD produces
(surname-filtered disqualification matching, partial sanctions coverage, export control
NOT ASSESSED without a product, the adverse-citation coherence check, grouped gaps, the
provenance/ambiguity/vintage/evidence set). A report issued the day before was decided
under different rules. Nothing recorded that.

THREE STATES, and the third is why this exists:
  * `current`  — issued under the rules in force now
  * `drifted`  — issued under EARLIER rules; findings shown AS ISSUED, never re-derived
  * `unpinned` — predates pinning; the rules cannot be identified, and must not be
                 claimed to be the current ones

WHAT THIS DELIBERATELY DOES NOT DO: replay old logic. There is no version→function
registry, and pretending otherwise would be worse than declaring the drift. Declaring is
honest and useful today; replay is a larger piece of work that this pin is the
precondition for.
"""
from __future__ import annotations

import pytest

from aria_service.intel.dd_schema import (
    ARKDDReport,
    DD_VERDICT_LOGIC_VERSION,
    verdict_logic_status,
)


def test_a_new_report_pins_the_current_logic_at_issue():
    r = ARKDDReport()
    assert r.verdict_logic_version == DD_VERDICT_LOGIC_VERSION
    assert verdict_logic_status(r)["state"] == "current"
    assert verdict_logic_status(r)["reproducible"] is True


def test_a_report_issued_under_earlier_rules_is_declared_drifted():
    """THE DEFECT: this previously read as though decided under today's rules."""
    r = ARKDDReport(verdict_logic_version="2026.01.01")
    st = verdict_logic_status(r)
    assert st["state"] == "drifted"
    assert st["reproducible"] is False
    assert "2026.01.01" in st["note"] and DD_VERDICT_LOGIC_VERSION in st["note"]
    assert "AS ISSUED" in st["note"], "the note must say the findings were not re-derived"


def test_a_legacy_report_is_unpinned_not_assumed_current():
    """A stored report from before pinning must NOT acquire today's version — that would
    be the fabrication this pin exists to prevent."""
    st = verdict_logic_status({"run_id": "dd_old", "schema_version": "1.0"})
    assert st["state"] == "unpinned"
    assert st["pinned"] == ""
    assert st["reproducible"] is False
    assert "cannot be identified" in st["note"]


def test_a_stored_dict_is_accepted_because_that_is_what_an_auditor_holds():
    """A persisted report comes back as JSON; the auditor's copy is the stored one."""
    st = verdict_logic_status({"verdict_logic_version": DD_VERDICT_LOGIC_VERSION})
    assert st["state"] == "current"
    st2 = verdict_logic_status({"verdict_logic_version": "2020.01.01"})
    assert st2["state"] == "drifted"


def test_drift_is_declared_in_the_rendered_report():
    """A pin recorded only in JSON is an audit trail nobody reads."""
    r = ARKDDReport(verdict_logic_version="2026.01.01")
    r.identity.entity_name = "Example Ltd"
    r.bottom_line = "test"
    out = r.render_markdown()
    assert "Decision logic" in out, out[:400]
    assert "2026.01.01" in out


def test_the_happy_path_stays_silent():
    """A banner on every current report becomes noise, and then the drifted one is
    skimmed past too."""
    r = ARKDDReport()
    r.identity.entity_name = "Example Ltd"
    r.bottom_line = "test"
    assert "Decision logic" not in r.render_markdown()


def test_an_unpinned_report_also_warns_the_reader():
    r = ARKDDReport()
    r.verdict_logic_version = ""          # simulate a legacy load after construction
    r.identity.entity_name = "Example Ltd"
    r.bottom_line = "test"
    out = r.render_markdown()
    assert "Decision logic" in out
    assert "cannot be identified" in out


def test_the_version_is_distinct_from_the_schema_version():
    """They answer different questions: shape versus rules. Conflating them is how the
    gap survived — `schema_version` looked like it already covered this."""
    r = ARKDDReport()
    assert r.schema_version == "1.0"
    assert r.verdict_logic_version != r.schema_version


def test_the_pinned_version_survives_serialisation():
    """It has to reach the stored copy, or an auditor reading the JSON learns nothing."""
    r = ARKDDReport()
    d = r.as_dict()
    assert d.get("verdict_logic_version") == DD_VERDICT_LOGIC_VERSION
    assert verdict_logic_status(d)["state"] == "current"


def test_replay_is_not_claimed():
    """Honesty about scope: nothing here re-derives an old report under its old rules,
    and the note must not imply otherwise."""
    st = verdict_logic_status(ARKDDReport(verdict_logic_version="2026.01.01"))
    assert "re-derived" in st["note"] or "not been re-derived" in st["note"]
    assert st["reproducible"] is False

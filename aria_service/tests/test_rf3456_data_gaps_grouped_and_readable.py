"""R-F3456 — the data-gaps block was a wall of text a reader could not act on.

THE SYMPTOM, from the delivered Babcock International Group PLC report. The gaps section
was ONE semicolon-joined paragraph of roughly 2,000 characters. Inside it, this line
appeared EIGHT times, once per officeholder, each carrying the identical reason::

    Officer sanctions screen 'BORRETT, Nicholas James William':
    SANCTIONS_SOURCE_UNVERIFIED — sanctions_source_unavailable, NOT screened
    (re-screen required, not a clearance)

and the Registry Trust blocker text appeared three times. Operator: "DATA gaps should have
serious and explanatory information."

TWO CAUSES, FIXED SEPARATELY BECAUSE THEY ARE SEPARATE. The repetition is produced in the
orchestrator, one append per officer inside the screening loop — so no amount of front-end
work removes it, and the PDF renderer would still carry it. The unreadability is produced
in the renderer, which joined every gap with "; " into a single amber paragraph.

WHAT A GAP HAS TO SAY. Not just that something is missing: WHY it is missing, WHAT closes
it, and WHO/what it covers. A gap a reader cannot act on is decoration.

THE TRAP IN GROUPING, and it is a false-clearance trap. The synthesis freshness gate greps
`report.identity.data_gaps` for the substring `SANCTIONS_SOURCE_UNVERIFIED` to force the
headline non-GREEN. Grouping these lines WITHOUT carrying that marker forward would have
silently removed the AMBER override — a false clean created by a formatting change. The
first test below exists solely to stop that.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import re
import shutil
import subprocess
from unittest.mock import patch

import pytest

from aria_service.intel import dd_orchestrator as ddo
from aria_service.intel import sanctions as _sanctions_mod
from aria_service.intel.dd_schema import ARKDDReport

PAGE = pathlib.Path(__file__).resolve().parents[2] / "public" / "dd-reports.html"


def _run(coro):
    return asyncio.run(coro)


def _report_with_officers(names) -> ARKDDReport:
    r = ARKDDReport()
    r.identity.entity_name = "Babcock International Group PLC"
    r.identity.directors = [
        {"name": n, "officer_role": "director", "appointed_on": "2020-01-01"}
        for n in names
    ]
    return r


_UNREACHABLE = {"name": "x", "matches": [], "screened": False, "top_score": 0.0,
                "error": "sanctions_source_unavailable"}

_OFFICERS = ["BORRETT, Nicholas James William", "CAIRNIE, Linda Ruth",
             "COMISKEY, Aedamar Ita, Dr", "FORSTER, Carl-Peter Edmund Moriz"]


def test_the_marker_survives_grouping():
    """THE FALSE-CLEARANCE TRAP. The synthesis gate greps for this exact substring to
    force the headline non-GREEN. Losing it while tidying the wording would turn a
    formatting change into a false clean."""
    report = _report_with_officers(_OFFICERS)
    with patch.object(_sanctions_mod, "screen_with_aliases", return_value=_UNREACHABLE):
        _run(ddo._screen_officer_sanctions(report, {}))
    assert any("SANCTIONS_SOURCE_UNVERIFIED" in str(g) for g in report.identity.data_gaps)


def test_capability_four_unscreened_officers_produce_one_gap_not_four():
    """THE SYMPTOM: one line per officer, all saying the same thing."""
    report = _report_with_officers(_OFFICERS)
    with patch.object(_sanctions_mod, "screen_with_aliases", return_value=_UNREACHABLE):
        _run(ddo._screen_officer_sanctions(report, {}))
    officer_gaps = [g for g in report.identity.data_gaps
                    if "Officer sanctions screen" in str(g)]
    assert len(officer_gaps) == 1, (
        f"expected ONE grouped gap, got {len(officer_gaps)}:\n"
        + "\n".join(str(g) for g in officer_gaps))


def test_the_grouped_gap_still_names_every_officer():
    """Grouping must not become truncation — a reader needs to know WHO."""
    report = _report_with_officers(_OFFICERS)
    with patch.object(_sanctions_mod, "screen_with_aliases", return_value=_UNREACHABLE):
        _run(ddo._screen_officer_sanctions(report, {}))
    gap = next(g for g in report.identity.data_gaps if "Officer sanctions screen" in str(g))
    for who in _OFFICERS:
        assert who in gap, f"{who} disappeared from the grouped gap"
    assert "4 officeholder(s) NOT screened" in gap


def test_the_gap_states_why_and_what_closes_it():
    """Operator: gaps need "serious and explanatory information"."""
    report = _report_with_officers(_OFFICERS)
    with patch.object(_sanctions_mod, "screen_with_aliases", return_value=_UNREACHABLE):
        _run(ddo._screen_officer_sanctions(report, {}))
    gap = next(g for g in report.identity.data_gaps if "Officer sanctions screen" in str(g))
    assert "NOT a clearance" in gap
    assert "WHAT CLOSES IT" in gap
    assert "WHO:" in gap


def test_distinct_reasons_are_not_collapsed_into_one():
    """Two officers failing for DIFFERENT reasons call for different actions, so the
    reasons must not be flattened to the first one seen."""
    report = _report_with_officers(_OFFICERS[:2])
    outs = [_UNREACHABLE, {"name": "x", "matches": [], "screened": False,
                           "top_score": 0.0, "error": "rate_limited"}]

    def _side_effect(*a, **k):
        return outs.pop(0) if outs else _UNREACHABLE

    with patch.object(_sanctions_mod, "screen_with_aliases", side_effect=_side_effect):
        _run(ddo._screen_officer_sanctions(report, {}))
    gap = next(g for g in report.identity.data_gaps if "Officer sanctions screen" in str(g))
    assert "sanctions_source_unavailable" in gap and "rate_limited" in gap, gap


def test_a_screen_that_works_produces_no_gap():
    """The guard against over-reporting: a clean run must stay clean-looking."""
    report = _report_with_officers(_OFFICERS[:2])
    ok = {"name": "x", "matches": [], "screened": True, "top_score": 0.0}
    with patch.object(_sanctions_mod, "screen_with_aliases", return_value=ok):
        _run(ddo._screen_officer_sanctions(report, {}))
    assert not [g for g in report.identity.data_gaps
                if "NOT screened" in str(g)], report.identity.data_gaps


# ── the renderer half ────────────────────────────────────────────────────────

def _render_gaps(gaps) -> str:
    """Execute the REAL gaps renderer extracted from the page."""
    src = PAGE.read_text(encoding="utf-8")
    m = re.search(r"      if \(sec\.data_gaps && sec\.data_gaps\.length\) \{(.*?)\n      \}",
                  src, re.S)
    assert m, "could not find the data-gaps renderer in dd-reports.html"
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    js = ("const escText = (s) => String(s == null ? '' : s).replace(/[&<>\"']/g,"
          " c => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));\n"
          "let h = '';\n"
          "const sec = " + json.dumps({"data_gaps": gaps}) + ";\n"
          "if (sec.data_gaps && sec.data_gaps.length) {" + m.group(1) + "\n}\n"
          "process.stdout.write(h);\n")
    out = subprocess.run([node, "--input-type=module", "-e", js],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_capability_gaps_render_as_rows_not_one_paragraph():
    html = _render_gaps(["gap one", "gap two", "gap three"])
    assert html.count("<li>") == 3, html
    assert "; " not in html, "gaps are still joined into a single sentence"


def test_the_renderer_deduplicates_repeated_gaps():
    """The CCJ blocker text was recorded three times by three different producers."""
    blocker = ("CCJ search was ORDERED for this subject but could not run: "
               "No CCJ backend configured.")
    html = _render_gaps([blocker, blocker, blocker, "something else"])
    assert html.count("<li>") == 2, html


def test_the_renderer_drops_empty_gaps_without_crashing():
    html = _render_gaps(["real gap", "", "   ", None])
    assert html.count("<li>") == 1, html


def test_the_gap_count_is_shown():
    """A reader should see how many there are before reading them."""
    html = _render_gaps(["a", "b", "c", "d"])
    assert ">4<" in html, html

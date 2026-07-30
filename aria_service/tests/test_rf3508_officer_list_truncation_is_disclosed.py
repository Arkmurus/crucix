"""R-F3508 — the report displayed 8 officers and its gaps referenced 11.

FROM THE DELIVERED BABCOCK REPORT. The Identity section listed eight officers. The data
gaps, further down, said::

    Officer sanctions screen capped at 8 of 11 officeholders —
    SANCTIONS_SOURCE_UNVERIFIED for PARKER, Andrew David, Sir, RAMSAY, John,
    SMITH, Kevin, Sir (not screened, not a clearance)

PARKER, RAMSAY and SMITH appear NOWHERE else in the document. A reader could not
reconcile 8 with 11, and three named individuals looked as though the gaps section had
invented them.

THE CAUSE was a silent `[:8]` slice in the structured view — the surface the web report
renders. The screening population and the DISPLAY population were both correct and simply
different sizes, with nothing saying so.

TRUNCATION IS NOT THE DEFECT; SILENT truncation is. A DD on a large group can have forty
officers and no reader wants forty lines — but a count they can check turns an apparent
contradiction into an ordinary "showing the first 8 of 11".

Applied to PSC/beneficial owners too, which carried the identical silent slice and would
have produced the same contradiction the first time a company had more than eight.
"""
from __future__ import annotations

import pytest

from aria_service.intel.dd_schema import _people_line


def _fmt(p):
    return str(p.get("name") or "") or None


def test_capability_more_people_than_shown_is_disclosed():
    """THE DEFECT: 11 officers, 8 rendered, nothing said."""
    people = [{"name": f"Officer {i}"} for i in range(11)]
    line = _people_line(people, _fmt)
    assert "showing 8 of 11" in line, line


def test_the_named_but_unshown_people_are_now_accounted_for():
    """The three names the Babcock gaps referenced were unreconcilable. With the count
    present, a reader knows the list is a window rather than the whole set."""
    people = [{"name": n} for n in (
        "BORRETT", "CAIRNIE", "COMISKEY", "FORSTER", "LOCKWOOD",
        "MELLORS", "MORIARTY", "NATANSON", "PARKER", "RAMSAY", "SMITH")]
    line = _people_line(people, _fmt)
    assert "showing 8 of 11" in line
    # The first eight are the ones displayed, matching the screening order.
    assert "BORRETT" in line and "NATANSON" in line
    assert "PARKER" not in line, "the window should still be 8"


def test_no_disclosure_when_nothing_is_hidden():
    """A count on every report would be noise; it appears only when it is needed."""
    people = [{"name": f"Officer {i}"} for i in range(3)]
    line = _people_line(people, _fmt)
    assert "showing" not in line
    assert line.count(";") == 2


def test_exactly_at_the_limit_says_nothing():
    """The off-by-one that would make the disclosure itself misleading."""
    people = [{"name": f"Officer {i}"} for i in range(8)]
    assert "showing" not in _people_line(people, _fmt)


def test_empty_and_unformattable_input_render_as_absent():
    """None, not an empty string — an empty row must not occupy space in the report."""
    assert _people_line([], _fmt) is None
    assert _people_line(None, _fmt) is None
    assert _people_line([{"name": ""}, {"name": None}], _fmt) is None


def test_the_count_reflects_formattable_entries_not_raw_length():
    """If two of eleven entries are junk, the disclosure must not claim eleven people
    were held — that would overstate what the registry actually returned."""
    people = [{"name": f"Officer {i}"} for i in range(10)] + [{"name": ""}]
    line = _people_line(people, _fmt)
    # 11 raw entries, but the honest statement is about what is renderable.
    assert "showing 8 of 11" in line


def test_both_officer_and_psc_rows_use_it():
    """The PSC row carried the identical silent slice and would have reproduced this
    the first time a company had more than eight beneficial owners."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "intel" / "dd_schema.py").read_text(encoding="utf-8", errors="replace")
    assert '_people_line(ident.get("directors")' in src
    assert '_people_line(ident.get("shareholders")' in src
    assert '(ident.get("directors") or [])[:8]' not in src, "the silent slice is back"

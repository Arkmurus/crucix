"""R-F3750 — DR-1 **D-08 adjudicated**: a waiver must name who declined, and why.

D-08 was UNADJUDICATED: "Waiver rendering on page 1", P1, suspected
`lib/reports/pdf_generator.mjs`, no fixture. Sixth DR-1 entry adjudicated from this
repo (after D-02, D-03, D-04, D-05, D-06).

THE SUSPECTED LOCATION IS WRONG — the third time in six. `pdf_generator.mjs`
contains ZERO waiver references. Waivers are rendered by `dd_schema.py`, which
builds the verdict string the PDF then prints. Adjudicating against the PDF
generator would have found nothing and proved nothing.

THE ADJUDICATION: satisfied, by two earlier fixes that state the reasoning exactly:

  R-F3410 (`dd_schema.py:734-748`) — `dd_scope.waivers` is PERSISTED, not derived,
    and that is the whole point: "a WAIVER cannot be recomputed from the evidence.
    'Nobody screened this' and 'the operator declined the screen, by name, for
    this reason' look identical in the output and are completely different facts."
  R-F3411 (`:1033-1042`) — a DECLINED screen is not the same sentence as a FAILED
    one. Both are "not a clearance", but one is the operator's decision with a name
    against it and the other is an outage. Reporting a waiver as "NOT SCREENED"
    hides who decided and why, which is the whole reason a waiver carries those
    fields.

The rendered verdict is `WAIVED by <who> — <why> (declined for this run; not a
clearance)`: neither a silent tick nor an unexplained gap.

Run: python -m pytest aria_service/tests/test_rf3750_dr1_d08_waiver_rendering.py -v
"""
from __future__ import annotations

import re

from ._source_probe import function_source, repo_path


def _render_src() -> str:
    """R-F3724/§16 — resolve by NAME through the current AST, never
    inspect.getsource: this file is edited by more than one agent."""
    from aria_service.intel import dd_schema
    return function_source(dd_schema, "render_markdown")


def test_a_waiver_names_who_declined_and_why():
    """THE D-08 INVARIANT: a waiver is a named decision, not a gap."""
    src = _render_src()
    assert "waived" in src, (
        "render_markdown no longer branches on a waived screen — a declined screen "
        "would fall through to whatever the un-waived path prints"
    )
    assert "waived_by" in src, (
        "the waiver no longer names WHO declined. An anonymous waiver is "
        "indistinguishable from 'nobody screened this' (R-F3410)."
    )
    assert "waived_reason" in src, "the waiver no longer carries WHY"


def test_a_waiver_is_not_presented_as_a_clearance():
    src = _render_src()
    assert "not a clearance" in src, (
        "the waiver verdict no longer states it is NOT a clearance — a customer "
        "acting on this document could read a declined screen as a passed one"
    )


def test_a_waiver_is_not_rendered_as_NOT_SCREENED():
    """R-F3411 — a DECLINED screen and a FAILED screen are different sentences."""
    src = _render_src()
    m = re.search(r"waived_by[\s\S]{0,400}", src)
    assert m, "could not locate the waiver rendering block"
    block = m.group(0)
    assert "WAIVED by" in block, (
        f"the waiver branch does not emit 'WAIVED by ...'; collapsing it into a "
        f"generic not-screened line hides who decided and why: {block[:200]!r}"
    )


def test_the_waiver_scope_is_persisted_not_derived():
    """R-F3410 — if scope is not stored at run time it is gone, and the waiver
    silently degrades into an unexplained gap."""
    src = repo_path("aria_service/intel/dd_schema.py").read_text(
        encoding="utf-8", errors="replace")
    assert "dd_scope: dict = field(default_factory=dict)" in src, (
        "dd_scope is no longer a persisted field. A waiver that is recomputed from "
        "evidence cannot exist — the evidence looks the same whether the operator "
        "declined or nobody looked."
    )


def test_the_pdf_generator_is_not_where_waivers_live():
    """Records the mis-pointed suspected location so it is not re-investigated.

    If waiver rendering ever MOVES into the PDF generator this fails, and D-08
    must be re-adjudicated there — the assertions above would then be checking a
    path the document no longer uses.
    """
    pdf = repo_path("lib/reports/pdf_generator.mjs").read_text(
        encoding="utf-8", errors="replace")
    assert not re.search(r"waiv", pdf, re.I), (
        "waiver logic now exists in pdf_generator.mjs. D-08 was adjudicated on the "
        "basis that the PDF only PRINTS the verdict string built in dd_schema; "
        "re-adjudicate against the renderer."
    )

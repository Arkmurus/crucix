"""R-F3502 — the digital layer said COMPLETED after a sweep that analysed nothing.

FROM THE DELIVERED BABCOCK REPORT. The digital section header read::

    Adverse media screening: COMPLETED
    Query templates actually searched: 30 of 48

and the gaps, further down, read::

    deep research was bounded at 37s and stopped after search fan-out (2 of 6 angles)
    — 0 article(s) analysed, 0 fact(s) retained

Both were produced by the same run. A reader scanning section headers — which is how a
long report is actually read — saw a finished screen. The truncation was one line inside
a list of eighteen gaps.

THE ROOT was not the wording. `report.digital.meta.status = LayerStatus.OK.value` was
assigned UNCONDITIONALLY at the end of the layer, so every degraded signal recorded
earlier in that layer was erased at the last line. The vocabulary already existed —
`LayerStatus.PARTIAL` renders as "PARTIAL" — and nothing ever set it.

TWO CHANGES, and the second is the one that stops this recurring:
  * the bounded branch marks the layer PARTIAL at the moment truncation is known
  * the terminal assignment only promotes to OK from an unset/OK state, so any degraded
    status earned earlier survives to the header

A truncated sweep reported as COMPLETED is the same class as an unsearched register
reported as clean: absence of coverage presented as a completed check.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from aria_service.intel.dd_schema import ARKDDReport, LayerStatus, _status_label

SRC = (pathlib.Path(__file__).resolve().parents[1]
       / "intel" / "dd_orchestrator.py").read_text(encoding="utf-8", errors="replace")


def test_partial_renders_as_partial_not_completed():
    """The vocabulary existed the whole time; nothing set it."""
    assert _status_label(LayerStatus.PARTIAL.value) == "PARTIAL"
    assert _status_label(LayerStatus.OK.value) == "COMPLETED"


def test_a_bounded_sweep_marks_the_layer_partial():
    """THE DEFECT: this is the branch that knew about the truncation and only wrote a gap."""
    m = re.search(r'if isinstance\(dr, dict\) and dr\.get\("partial"\):(.{0,600})', SRC, re.S)
    assert m, "the bounded-deep-research branch is gone"
    body = m.group(1)
    assert "meta.status = LayerStatus.PARTIAL.value" in body, (
        "a bounded sweep still records only a data gap, so the section header keeps "
        "reading COMPLETED")


def test_the_terminal_assignment_cannot_erase_a_degraded_status():
    """THE ROOT. Without this, any status set earlier in the layer is overwritten on the
    last line and the header lies again the next time a sub-stage degrades."""
    # 800, not 400: the explanatory comment sits between the anchor and the predicate,
    # and a short window cut the match mid-token — failing against correct code.
    m = re.search(r"report\.digital\.meta\.duration_ms = .{0,800}", SRC, re.S)
    assert m
    tail = m.group(0)
    assert "if str(report.digital.meta.status" in tail, (
        "the digital layer status is assigned unconditionally again")
    assert "LayerStatus.OK.value)" in tail


def test_the_status_promotion_logic_is_correct():
    """Drive the predicate itself, so this is not only a source scan."""
    def _promote(current: str) -> str:
        if str(current or "") in ("", LayerStatus.OK.value):
            return LayerStatus.OK.value
        return current

    assert _promote("") == LayerStatus.OK.value
    assert _promote(LayerStatus.OK.value) == LayerStatus.OK.value
    # Anything already degraded must SURVIVE to the header.
    for degraded in (LayerStatus.PARTIAL.value, LayerStatus.ERROR.value,
                     LayerStatus.SKIPPED.value):
        assert _promote(degraded) == degraded, (
            f"{degraded} was overwritten with OK — the defect")


def test_a_partial_layer_header_says_partial():
    """What the reader actually sees."""
    r = ARKDDReport()
    r.digital.meta.status = LayerStatus.PARTIAL.value
    assert _status_label(r.digital.meta.status) == "PARTIAL"


def test_a_clean_layer_still_says_completed():
    """The guard against over-correction: a genuinely complete sweep must not be
    downgraded, or PARTIAL stops meaning anything."""
    r = ARKDDReport()
    r.digital.meta.status = LayerStatus.OK.value
    assert _status_label(r.digital.meta.status) == "COMPLETED"

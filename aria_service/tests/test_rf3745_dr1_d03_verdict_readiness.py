"""R-F3745 — DR-1 **D-03 adjudicated**, and the fixture the protocol asks for first.

D-03 was one of the twelve UNADJUDICATED DR-1 entries: "Status <-> verdict
reconciliation (no GREEN over NOT CLEARED)", P0, no fixture. §A of
docs/cure/defects.md blocks Phase 3 on missing DR-1 evidence, and Phase 3 step 1
is "write the failing fixture first".

THE ADJUDICATION: the defect as WORDED is mis-specified, and implementing it
literally would be a regression, not a fix.

Evidence, all from this repo:

  * `dd_schema.py:3133` issues `status = DECISION_READY_FOR_HUMAN_REVIEW |
    NOT_CLEARED`, and its own `scope_note` says decision readiness "does not
    replace the risk verdict". So risk (GREEN/AMBER/RED) and readiness are two
    ORTHOGONAL axes by design.
  * `lib/reports/pdf_generator.mjs:857-862` states the rule the renderer
    enforces: "whatever ARIA found is what gets printed - GREEN, AMBER or RED,
    unaltered. This renderer NEVER upgrades, softens or omits a verdict, and
    NEVER prints a verdict without the decision-readiness state beside it. A
    GREEN classification paired with NOT_CLEARED is the honest output and both
    halves must appear together."

A blanket "no GREEN over NOT CLEARED" would therefore force one of two
falsehoods: suppress a GREEN the evidence supports, or print a worse verdict than
was found. Both are fabrication, and the second is the exact false-confidence
failure the scorecard exists to prevent. GREEN-risk + NOT_CLEARED-readiness means
"nothing adverse found, and coverage is not yet sufficient to rely on it" — which
is honest and is frequently the true state.

THE REAL INVARIANT is the second half of that rule, and it is the half that can
actually produce a false clean: **a verdict must never be rendered without the
readiness state beside it.** A GREEN pill alone reads as clearance. That is the
NorthRow failure the file's own header cites — "a clean score without a coverage
statement reads as 'clear' when it may only mean 'unexamined'".

Run: python -m pytest aria_service/tests/test_rf3745_dr1_d03_verdict_readiness.py -v
"""
from __future__ import annotations

import re

from ._source_probe import repo_path

PDF = repo_path("lib/reports/pdf_generator.mjs")


def _src() -> str:
    return PDF.read_text(encoding="utf-8", errors="replace")


def test_the_verdict_renderer_still_takes_readiness():
    """If readiness stops being a parameter, the pairing cannot be enforced."""
    src = _src()
    m = re.search(r"function\s+addVerdictBlock\s*\(([^)]*)\)", src)
    assert m, "addVerdictBlock disappeared — the verdict rendering path moved"
    params = [p.strip() for p in m.group(1).split(",")]
    assert "readiness" in params, (
        f"addVerdictBlock({', '.join(params)}) no longer receives readiness. A "
        f"verdict rendered without its decision-readiness state reads as "
        f"clearance — see pdf_generator.mjs:857-862."
    )


def test_every_verdict_call_site_passes_readiness():
    """THE FIXTURE: a verdict may never be printed alone.

    This is the D-03 invariant, correctly specified. It fails the moment someone
    renders a risk classification without the readiness half.
    """
    src = _src()
    calls = re.findall(r"addVerdictBlock\s*\(([^;]*?)\)\s*;", src, re.S)
    calls = [c for c in calls if "function" not in c]
    assert calls, "no addVerdictBlock call sites found — has rendering moved?"
    bad = [c.strip()[:120] for c in calls if "readiness" not in c]
    assert not bad, (
        "these verdict renders omit the readiness argument, so the document can "
        f"print a bare verdict that reads as a clean: {bad}"
    )


def test_the_renderer_does_not_rewrite_the_verdict():
    """D-03's literal wording would require exactly this — so guard against it.

    The renderer's stated contract is that it renders what the report says and
    never upgrades or softens. A future "reconciliation" that downgrades GREEN
    when readiness is NOT_CLEARED would satisfy the register's wording and
    FABRICATE a verdict.
    """
    src = _src()
    m = re.search(r"function\s+addVerdictBlock\s*\([^)]*\)\s*\{(.{0,2600})",
                  src, re.S)
    assert m, "could not read addVerdictBlock's body"
    body = m.group(1)
    # A reconciliation that mutates the verdict must reach the verdict from the
    # readiness state. TWO shapes, and the second is the likely one — found by
    # negative-controlling this guard, which initially missed it:
    #   (a) readiness on the RHS:      col = cleared ? GREEN : MUTE
    #   (b) readiness in the CONDITION: if (!cleared) raw = 'AMBER'
    # (b) is how a "reconciliation" would actually be written, and the first
    # version of this assertion let it through.
    forbidden = re.findall(
        r"(?:raw|v|col)\s*=\s*[^;\n]*(?:readiness|cleared|NOT_CLEARED)", body)
    # NB the gap must allow `{` — a braced body
    # (`if (...NOT_CLEARED) { v = 'INSUFFICIENT'; }`) is the third shape, and
    # excluding `{` let it through on the second pass of this same guard.
    forbidden += re.findall(
        r"if\s*\([^)]*(?:readiness|cleared|NOT_CLEARED)[^)]*\)[^;]{0,80}?"
        r"\b(?:raw|v|col)\s*=", body, re.S)
    assert not forbidden, (
        "the verdict appears to be derived from the readiness state: "
        f"{forbidden}. The renderer must print what was found (GREEN, AMBER or "
        "RED, unaltered) and show readiness BESIDE it — downgrading a GREEN "
        "because coverage is incomplete invents a finding."
    )


def test_readiness_and_risk_remain_separate_in_the_schema():
    """The two axes are orthogonal by design; collapsing them is the regression."""
    schema = repo_path("aria_service/intel/dd_schema.py").read_text(
        encoding="utf-8", errors="replace")
    assert "does not replace the risk verdict" in schema, (
        "dd_schema's scope_note no longer states that decision readiness does "
        "not replace the risk verdict. If that separation was removed, D-03 must "
        "be re-adjudicated before anything is built on this fixture."
    )

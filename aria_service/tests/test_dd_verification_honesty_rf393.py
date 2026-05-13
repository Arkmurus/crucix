"""R-F393 — DD verification layer honesty test.

Live evidence 2026-05-13: ARIA self-reported in a WA conversation that
the dd_orchestrate verification layer was "wired-but-silent" and
returned 0% grounded results on a Lukoil DD. Investigation confirmed:

  - `_run_verification` (dd_orchestrator.py:2426) does run, but it
    only triangulates source-counts from claims that PRIOR layers
    already collected. It does NOT invoke `source_verifier.py` to
    independently re-fetch URLs / re-check claims against external
    sources.
  - The legacy docstring called it "Verification. Cross-source
    triangulation + conflict detection" — half-honest, but the
    section name and `grounded_rate` field implied independent
    verification was happening.

Phase A honesty fix: add explicit scope-flags to VerificationSection
(`independent_source_verification_run`, `scope_note`) and populate
them in `_run_verification`. Consumers (dashboards, BLUF, chat audit)
can then render the truth instead of inferring "verification done"
from the section name.

These tests pin:
  1. The honest-scope fields exist on VerificationSection.
  2. `_run_verification` sets them correctly.
  3. The legacy fields (grounded_rate, triangulated_claims, conflicts)
     still work — no functional regression.
  4. The scope_note text is non-empty and mentions both what the
     layer does (triangulation) and what it doesn't (URL verification).
"""
from __future__ import annotations

import asyncio
import inspect
from dataclasses import fields

from aria_service.intel.dd_schema import (
    ARKDDReport,
    VerificationSection,
)
from aria_service.intel.dd_orchestrator import _run_verification


# ── 1. Schema check — fields are present and default-honest ─────────

def test_rf393_verification_section_has_honest_scope_fields():
    """The new fields must exist on the dataclass at all times — not
    just after `_run_verification` runs. A consumer that reads the
    report before verification fires (e.g. mid-flight crash) must
    still see the honest defaults."""
    field_names = {f.name for f in fields(VerificationSection)}
    assert "independent_source_verification_run" in field_names, (
        "R-F393 regression: independent_source_verification_run field "
        "missing from VerificationSection — dashboard / BLUF can't "
        "distinguish triangulation from real verification."
    )
    assert "scope_note" in field_names, (
        "R-F393 regression: scope_note field missing from "
        "VerificationSection — operator has no plain-language "
        "description of what the layer actually did."
    )


def test_rf393_verification_section_defaults_are_safe():
    """Default values must NOT claim verification was run. If the
    layer never fires (orchestrator crashes before reaching it),
    consumers must read False, not True."""
    v = VerificationSection()
    assert v.independent_source_verification_run is False, (
        "R-F393 regression: independent_source_verification_run "
        "defaults to True — would lie when the layer never fires."
    )
    assert v.scope_note == "", (
        "R-F393 regression: scope_note has a default text — consumers "
        "would render stale/incorrect explanations when the layer "
        "never fires."
    )


# ── 2. Behaviour — _run_verification populates honestly ───────────

def test_rf393_run_verification_marks_source_verification_not_run():
    """After `_run_verification` runs, the flag must be False (because
    source_verifier is not invoked) and scope_note must be populated."""
    report = ARKDDReport()
    target = {"name": "Test Entity", "country": "GB"}
    asyncio.run(_run_verification(target, report))

    assert report.verification.independent_source_verification_run is False, (
        "R-F393 invariant: source_verifier.py is not wired into the "
        "orchestrator today. If you wire it, flip this flag to True "
        "based on whether the run actually invoked the verifier — do "
        "NOT just delete this assertion."
    )
    assert report.verification.scope_note, (
        "R-F393: scope_note empty after _run_verification fired"
    )


def test_rf393_scope_note_names_what_it_does_and_does_not_do():
    """The scope_note must mention both sides of the honesty — what
    the layer does (triangulation) and what it does not do (URL
    re-fetch). A note that says only one side is half a lie."""
    report = ARKDDReport()
    asyncio.run(_run_verification({}, report))

    note = report.verification.scope_note.lower()
    assert "triangulation" in note, (
        f"R-F393: scope_note must say what the layer DOES "
        f"(triangulation). Got: {report.verification.scope_note!r}"
    )
    assert ("not" in note or "no " in note), (
        f"R-F393: scope_note must say what the layer does NOT do. "
        f"Got: {report.verification.scope_note!r}"
    )
    assert "source_verifier" in note or "url" in note, (
        f"R-F393: scope_note must name the missing capability "
        f"(source_verifier / URL re-fetch). Got: "
        f"{report.verification.scope_note!r}"
    )


# ── 3. Regression — triangulation + grounded_rate still work ──────

def test_rf393_grounded_rate_none_when_no_claims():
    """The Lukoil 0% case: when prior layers collected nothing, the
    layer must report grounded_rate=None (not 0.0, not 1.0). This was
    already the behaviour; pin it so the R-F393 refactor doesn't
    accidentally flip it."""
    report = ARKDDReport()
    asyncio.run(_run_verification({}, report))
    assert report.verification.grounded_rate is None
    assert report.verification.triangulated_claims == []


def test_rf393_grounded_rate_computed_when_claims_exist():
    """When prior layers populated sanctions_screen + directors, the
    layer must triangulate those into claims and emit grounded_rate
    as a fraction. This is the legitimate work the layer DOES do —
    confirming the rename didn't break the math."""
    report = ARKDDReport()
    # Simulate Layer 1 (Identity) having populated some data.
    report.identity.sanctions_screen = [{"hit": False, "list": "OFAC"}]
    report.identity.directors = [{"name": "Test Director"}]

    asyncio.run(_run_verification({}, report))

    assert len(report.verification.triangulated_claims) >= 2
    # Both claims have exactly 1 source each → grounded_rate (>=2 sources) = 0.0
    assert report.verification.grounded_rate == 0.0


# ── 4. Docstring — the function's docstring must be honest ────────

def test_rf393_run_verification_docstring_states_scope():
    """The function's own docstring must explicitly note that
    independent source verification is NOT performed. Future
    contributors reading the code should see the honesty inline, not
    just in the data fields."""
    doc = inspect.getdoc(_run_verification) or ""
    doc_lower = doc.lower()
    assert "triangulation" in doc_lower
    assert "not" in doc_lower
    assert "source_verifier" in doc_lower or "source verifier" in doc_lower, (
        f"R-F393: _run_verification docstring must name the missing "
        f"capability (source_verifier). Got:\n{doc}"
    )

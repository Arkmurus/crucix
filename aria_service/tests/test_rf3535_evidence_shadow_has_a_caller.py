"""R-F3535 — the R-F3510 evidence shadow had NO CALLER, so phase 2 could never happen.

R-F3474 built a four-axis evidence contract that nothing used. R-F3510 added
`sanctions_evidence_shadow` to prove that contract reproduces live behaviour BEFORE
anything depends on it — the deliberate, correct way to consolidate two measures of one
question. Its docstring states the plan outright: *"a caller records it and reads it
later"*.

**There was no caller.** A dormant guard against a dormant specification. The
consolidation's phase 2 was gated on agreement evidence that was never going to exist,
because nothing ever ran the comparison. `grep sanctions_evidence_shadow` returned
exactly one hit: its own `def`.

WHY A RECORDER RATHER THAN FOUR CALLS. FOUR sites assign `identity.sanctions_screen` —
the variant path, the registered-name path, the WAIVER path, and the alias/OFSI path that
a company DD actually takes. R-F3038 exists precisely because R-F3031 stamped only the
first of them and a delivered report still could not date its own screen. Wiring the
shadow at one site would repeat that mistake, and partial disagreement data is worse than
none: it looks like coverage.

SHADOW MEANS SHADOW. No finding, no verdict, no wording changes — and a failure in the
diagnostic can never cost a screen.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from aria_service.intel import dd_orchestrator as o
from aria_service.intel.dd_schema import ARKDDReport

SRC = pathlib.Path(o.__file__).read_text(encoding="utf-8", errors="replace")


def _screen(**over):
    base = {"matches": [], "screened": True, "variants_screened": ["x"],
            "verified_sources": ["ofac_sdn"]}
    base.update(over)
    return base


# ── the recorder does both jobs ─────────────────────────────────────────────

def test_capability_the_screen_is_assigned_and_the_shadow_recorded():
    r = ARKDDReport()
    s = _screen()
    o._record_sanctions_screen(r, s)

    assert r.identity.sanctions_screen is s, "the screen must still be assigned"
    shadow = s.get("_evidence_shadow")
    assert isinstance(shadow, dict) and shadow, (
        "no shadow recorded — the comparison this exists to collect is still missing")


def test_capability_the_shadow_actually_maps_the_state():
    """A shadow that records `{'mapped': False}` for a real screen collects nothing
    useful — it would look like evidence while proving nothing."""
    r = ARKDDReport()
    s = _screen()
    o._record_sanctions_screen(r, s)
    assert s["_evidence_shadow"].get("mapped") is not False, s["_evidence_shadow"]


def test_capability_a_broken_shadow_never_costs_a_screen(monkeypatch):
    """THE SAFETY PROPERTY. This is a diagnostic; if it throws, the DD must be
    unaffected and the screen must still be assigned."""
    def _boom(_):
        raise RuntimeError("shadow exploded")

    monkeypatch.setattr(o, "sanctions_evidence_shadow", _boom)
    r = ARKDDReport()
    s = _screen()
    o._record_sanctions_screen(r, s)          # must not raise
    assert r.identity.sanctions_screen is s
    assert "_evidence_shadow" not in s


def test_the_shadow_changes_no_finding_or_verdict():
    """Shadow means shadow. It records a comparison and nothing else."""
    r = ARKDDReport()
    before_findings = len(r.identity.findings)
    before_gaps = len(r.identity.data_gaps)
    o._record_sanctions_screen(r, _screen())
    assert len(r.identity.findings) == before_findings
    assert len(r.identity.data_gaps) == before_gaps


def test_a_waived_screen_is_still_shadowed():
    """The waiver path is one of the four, and it is the one most likely to be
    forgotten — it does not look like a 'screen' at a glance."""
    r = ARKDDReport()
    s = {"matches": [], "screened": False, "waived": True, "waived_by": "op"}
    o._record_sanctions_screen(r, s)
    assert isinstance(s.get("_evidence_shadow"), dict)


# ── reachability: the property that was actually missing ───────────────────

def test_the_shadow_HAS_a_caller():
    """THE DEFECT. Before this, `grep sanctions_evidence_shadow` returned only its own
    `def` — a guard that could never fire, guarding a spec nothing used."""
    calls = [ln for ln in SRC.splitlines()
             if "sanctions_evidence_shadow(" in ln and not ln.lstrip().startswith("def ")
             and not ln.lstrip().startswith("#")]
    assert calls, "sanctions_evidence_shadow is dormant again — nothing calls it"


def test_every_screen_assignment_goes_through_the_recorder():
    """FOUR sites assign the screen; R-F3038 exists because a previous fix stamped only
    one. The only bare assignment permitted is the one INSIDE the recorder."""
    bare = [i for i, ln in enumerate(SRC.splitlines(), 1)
            if re.match(r"\s*report\.identity\.sanctions_screen\s*=", ln)]
    assert len(bare) == 1, (
        f"{len(bare)} bare screen assignments — each one bypasses the shadow and makes "
        f"the agreement data silently partial: lines {bare}")

    # ...and that one must be inside _record_sanctions_screen.
    start = SRC.index("def _record_sanctions_screen")
    end = SRC.index("\ndef ", start + 10)
    body_start_line = SRC[:start].count("\n") + 1
    body_end_line = SRC[:end].count("\n") + 1
    assert body_start_line < bare[0] < body_end_line, (
        "the bare assignment is not the recorder's own — a caller is bypassing it")


def test_all_four_call_sites_are_present():
    n = len([ln for ln in SRC.splitlines()
             if "_record_sanctions_screen(report" in ln])
    assert n >= 4, (
        f"only {n} recorder call site(s); the variant, registered-name, waiver and "
        "alias/OFSI paths must all record")


def test_the_recorder_is_not_recursive():
    """The first cut of this change rewrote the recorder's OWN assignment into a call to
    itself — infinite recursion that py_compile accepts happily."""
    start = SRC.index("def _record_sanctions_screen")
    end = SRC.index("\ndef ", start + 10)
    # Skip the `def` line: the SIGNATURE necessarily contains the same text as a call,
    # and matching it flagged correct code on the first cut of this test.
    body = SRC[start:end].split("\n", 1)[1]
    calls = [ln.strip() for ln in body.splitlines()
             if "_record_sanctions_screen(" in ln and not ln.lstrip().startswith("#")]
    assert not calls, f"the recorder calls itself — infinite recursion: {calls}"

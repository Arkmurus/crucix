"""R-F3353 — the PSC (beneficial-owner) sanctions screen called a function that never existed.

THE DEFECT. `_run_identity` screened each Person with Significant Control via

    psc_matches = await _san.screen_entity(psc_name)          # dd_orchestrator.py:4764

but `aria_service.intel.sanctions` has NO `screen_entity` — and never has
(`git log -S "screen_entity" -- intel/sanctions.py` is empty; the call arrived
with e8bf76f2). Every UK DD therefore raised AttributeError on every PSC, was
caught by a bare `except`, and logged

    R-F886 PSC (ownership) screen failed for <name>: module
    'aria_service.intel.sanctions' has no attribute 'screen_entity'

(observed live on aria-intel 2026-07-28). So the beneficial-owner screen has
never run in production.

WHY IT IS A FALSE CLEAN, not merely a dead feature. The `except` logged a
warning and appended NOTHING to `report.identity.data_gaps` — unlike its own
sibling handler three lines below, which does record the gap when the
Companies House lookup throws. A report listing beneficial owners with no
sanctions finding and no data gap is indistinguishable from "screened, clean".
That is the never-false-clean breach this suite guards.

WHY THE EXISTING TEST DID NOT CATCH IT. `test_rf886_compliance_swallows_surfaced.py`
asserts the *string* "R-F886 PSC (ownership) screen failed" is present in the
source — it pins the FAILURE LOG LINE of a permanently-broken call as though it
were the feature, and passes forever. These tests assert the PROPERTY instead.

WHAT THESE TESTS DRIVE. `_screen_psc_sanctions` is the exact coroutine
`_run_identity` awaits — the same pattern `test_rf1836_rdap_identity_layer.py`
uses to drive `_check_domain_ownership` without the live sanctions/network the
full identity layer needs. Assertions are on the user-visible outcome: the
finding and the data gap that land in `report.identity`.
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from unittest.mock import patch

from aria_service.intel import dd_orchestrator as ddo
from aria_service.intel import sanctions as _sanctions_mod
from aria_service.intel.dd_schema import ARKDDReport

SRC_PATH = Path(__file__).resolve().parents[1] / "intel" / "dd_orchestrator.py"
SRC = SRC_PATH.read_text(encoding="utf-8")


def _run(coro):
    return asyncio.run(coro)


def _report_with_psc(name: str = "Mr Justin Howard") -> ARKDDReport:
    report = ARKDDReport()
    report.identity.entity_name = "Silverbrook Capital Management Ltd"
    report.identity.shareholders = [
        {"name": name, "natures_of_control": ["ownership-of-shares-75-to-100-percent"]}
    ]
    return report


def _screen(matches, *, screened=True, error=None):
    """Shape returned by sanctions.screen_with_aliases (verified against its
    own return dict, intel/sanctions.py:1412-1435)."""
    out = {"name": "x", "matches": matches, "screened": screened, "top_score": 0.0}
    if error:
        out["error"] = error
    return out


# ── ROOT-CAUSE GUARD: no call to a sanctions function that does not exist ────
# This is the guard that would have caught R-F3353 on the day it shipped, and
# catches the next rename. It is the reason this fix is structural and not a
# one-line correction.

def test_every_sanctions_attribute_called_by_dd_orchestrator_exists():
    tree = ast.parse(SRC)
    # local aliases the module binds to intel.sanctions
    aliases = {"sanctions", "_san"}
    referenced: set[str] = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in aliases):
            referenced.add(node.attr)
    assert referenced, "guard is blind — no sanctions.* attribute references found"
    missing = sorted(a for a in referenced if not hasattr(_sanctions_mod, a))
    assert not missing, (
        f"dd_orchestrator calls sanctions functions that do not exist: {missing}. "
        f"This is the R-F3353 class (screen_entity) — a missing name is swallowed "
        f"by a bare except and reads as a clean screen."
    )


def test_no_call_to_the_phantom_entrypoint():
    """Assert the PROPERTY (no call), not the wording — the fix's own docstring
    names the phantom function to explain the defect, and banning the token
    would make this guard cry wolf on documentation."""
    assert not hasattr(_sanctions_mod, "screen_entity")
    assert "_san.screen_entity(" not in SRC
    assert "sanctions.screen_entity(" not in SRC


# ── CAPABILITY: the screen actually runs and surfaces a sanctioned PSC ───────

def test_sanctioned_psc_produces_finding():
    report = _report_with_psc()
    hit = [{"name": "JUSTIN HOWARD", "score": 0.97, "list": "OFAC SDN",
            "topics": ["sanction"], "datasets": ["us_ofac_sdn"]}]
    with patch.object(_sanctions_mod, "screen_with_aliases",
                      return_value=_screen(hit)):
        _run(ddo._screen_psc_sanctions(report))
    titles = [f.title for f in report.identity.findings]
    assert any("Justin Howard" in t for t in titles), (
        f"sanctioned PSC produced no finding; got {titles}"
    )
    psc_f = [f for f in report.identity.findings if "Justin Howard" in f.title][0]
    assert psc_f.severity in {"amber", "red", "hard_stop"}
    assert psc_f.source == "sanctions.psc_reverse"


def test_psc_screen_uses_a_trusted_name_source():
    """A PSC name comes off the Companies House PSC register. Screening it as
    untrusted free text re-runs the search-query shape heuristic that caused the
    R-F3217 false clean, so the source must be declared."""
    report = _report_with_psc()
    with patch.object(_sanctions_mod, "screen_with_aliases",
                      return_value=_screen([])) as m:
        _run(ddo._screen_psc_sanctions(report))
    assert m.called
    src = m.call_args.kwargs.get("source")
    assert src in _sanctions_mod._TRUSTED_NAME_SOURCES, (
        f"PSC screened with source={src!r}, which is not trusted — the name-shape "
        f"gate can silently reject real PSC names"
    )


# ── NEVER-FALSE-CLEAN: a screen that did not run must not read as clean ─────

def test_unperformed_screen_records_data_gap():
    """R-F1696: `screened=False` means the source was unreachable, so an empty
    match list is NOT a clearance."""
    report = _report_with_psc()
    with patch.object(_sanctions_mod, "screen_with_aliases",
                      return_value=_screen([], screened=False,
                                           error="sanctions_source_unavailable")):
        _run(ddo._screen_psc_sanctions(report))
    gaps = " ".join(report.identity.data_gaps)
    assert "SANCTIONS_SOURCE_UNVERIFIED" in gaps, (
        f"an unperformed PSC screen left no gap marker — it reads as clean. gaps={gaps!r}"
    )
    assert "Justin Howard" in gaps


def test_exception_records_data_gap_not_silence():
    """The original defect: AttributeError swallowed into a bare warning. Any
    throw must now leave evidence in the report, so this class fails LOUD."""
    report = _report_with_psc()
    with patch.object(_sanctions_mod, "screen_with_aliases",
                      side_effect=AttributeError("module has no attribute 'screen_entity'")):
        _run(ddo._screen_psc_sanctions(report))
    gaps = " ".join(report.identity.data_gaps)
    assert "SANCTIONS_SOURCE_UNVERIFIED" in gaps, (
        f"a throwing PSC screen left no gap — this is the exact R-F3353 false clean. "
        f"gaps={gaps!r}"
    )


def test_performed_clean_screen_records_no_gap():
    """The other half of never-false-clean: a screen that DID run and found
    nothing must NOT be polluted with a gap, or every clean DD is downgraded."""
    report = _report_with_psc()
    with patch.object(_sanctions_mod, "screen_with_aliases",
                      return_value=_screen([], screened=True)):
        _run(ddo._screen_psc_sanctions(report))
    gaps = " ".join(report.identity.data_gaps)
    assert "SANCTIONS_SOURCE_UNVERIFIED" not in gaps, (
        f"a genuinely clean screen was marked unverified: {gaps!r}"
    )
    assert not [f for f in report.identity.findings if "Justin Howard" in f.title]


# ── the gap marker must reach the consumer that forces the headline non-GREEN ─

def test_gap_marker_matches_the_headline_downgrade_consumer():
    """dd_orchestrator downgrades GREEN→AMBER_LIGHT on data_gaps containing
    'SANCTIONS_SOURCE_UNVERIFIED'. Producer and consumer must agree or the gap
    is inert (the producer→consumer-no-carrier class)."""
    assert 'if "SANCTIONS_SOURCE_UNVERIFIED" in str(g)' in SRC


# ── ceased PSCs are still skipped; the cap still applies ────────────────────

def test_ceased_psc_is_skipped():
    report = ARKDDReport()
    report.identity.shareholders = [
        {"name": "Former Owner", "ceased_on": "2020-01-01"}
    ]
    with patch.object(_sanctions_mod, "screen_with_aliases",
                      return_value=_screen([])) as m:
        _run(ddo._screen_psc_sanctions(report))
    assert not m.called, "a ceased PSC was screened"


def test_psc_screen_is_capped():
    report = ARKDDReport()
    report.identity.shareholders = [{"name": f"Owner Number {i}"} for i in range(25)]
    with patch.object(_sanctions_mod, "screen_with_aliases",
                      return_value=_screen([])) as m:
        _run(ddo._screen_psc_sanctions(report))
    assert m.call_count <= 10, f"cost cap breached: {m.call_count} screens"


def test_orchestrator_still_awaits_the_helper():
    """The extracted coroutine must remain wired into _run_identity, or the
    tests above pass while production screens nobody."""
    assert "await _screen_psc_sanctions(report)" in SRC

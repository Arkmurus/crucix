"""R-F3411 — a declined section is not searched; an ordered one always is.

R-F3406 made scope HONEST (a waiver is recorded, never reads as clean). R-F3410 made it
VISIBLE (the checklist renders). This makes it OPERATIONAL: the check is actually
skipped, which is the only reason declining a metered search is worth offering. The
OpenSanctions allowance was VERIFIED EXHAUSTED on 2026-07-29 (HTTP 429, "this API key
has exceeded its rate limit for the month"), so a declined screen that runs anyway
conserves nothing.

NO NEW ENDPOINT. `routes/aria.py` passes `target=body` at both the sync (:1200) and
async (:1177) call sites, so the whole request body already reaches the orchestrator.
Scope is read off it — nothing about the route signature changes.

THE FOUR PROPERTIES:

  1. FAIL-SAFE. An anonymous or malformed waiver is NOT honoured, so the check RUNS.
     The failure direction must always be "we screened anyway", never "we skipped it
     because the form sent junk".
  2. NEVER AN OMISSION. A skipped screen writes a marker blob and a data gap, so the
     report still carries a sanctions line. Without it `render_markdown` gates the whole
     line on a truthy dict and a declined screen would leave NO trace — a reader could
     not tell a declined check from one nobody thought about.
  3. DECLINED != FAILED. Both are "not a clearance", but one has a name against it and
     the other is an outage. A waiver reported as "NOT SCREENED" hides who decided and
     why, which is the entire reason a waiver carries those fields.
  4. AN ORDER BEATS A DECLINE, in the engine exactly as in the checklist — otherwise
     the two disagree about whether the work should have happened.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import dd_orchestrator as ddo
from aria_service.intel.dd_schema import ARKDDReport


def _run(coro):
    return asyncio.run(coro)


def _waiver(qid="IS-13", by="A. Correa", reason="domestic UK contract"):
    return {"question_id": qid, "waived_by": by, "reason": reason}


# ── scope parsing off the request body ───────────────────────────────────────

def test_scope_read_from_nested_dd_scope():
    s = ddo._dd_scope_from_target({"dd_scope": {"tier": "enhanced",
                                                "waivers": [_waiver()]}})
    assert s["tier"] == "ENHANCED"
    assert s["waivers"][0]["question_id"] == "IS-13"


def test_scope_read_from_top_level_body():
    """The form may send these flat; both shapes must work or the tick box silently
    does nothing."""
    s = ddo._dd_scope_from_target({"tier": "SIMPLIFIED", "elections": ["IS-17b"]})
    assert s["tier"] == "SIMPLIFIED"
    assert s["elections"] == [{"question_id": "IS-17b"}]


def test_absent_scope_is_a_full_run():
    s = ddo._dd_scope_from_target({"name": "Testco"})
    assert s["tier"] == "STANDARD"
    assert s["waivers"] == [] and s["elections"] == []


@pytest.mark.parametrize("junk", ["nonsense", None, 42, []])
def test_malformed_target_never_raises(junk):
    assert isinstance(ddo._dd_scope_from_target(junk), dict)   # type: ignore[arg-type]


def test_unknown_tier_falls_back_to_standard():
    assert ddo._dd_scope_from_target({"tier": "PLATINUM"})["tier"] == "STANDARD"


# ── 1. fail-safe: only a complete waiver is honoured ─────────────────────────

def test_valid_waiver_is_honoured():
    r = ARKDDReport(); r.dd_scope = {"waivers": [_waiver()]}
    assert ddo._scope_waived(r, "IS-13") is not None


@pytest.mark.parametrize("bad", [
    {"question_id": "IS-13", "waived_by": "", "reason": "r"},
    {"question_id": "IS-13", "waived_by": "A", "reason": ""},
    {"question_id": "IS-13"},
])
def test_incomplete_waiver_is_not_honoured_so_the_check_runs(bad):
    r = ARKDDReport(); r.dd_scope = {"waivers": [bad]}
    assert ddo._scope_waived(r, "IS-13") is None, (
        "an anonymous opt-out skipped a check — the failure direction must always be "
        "'we screened anyway'"
    )


def test_unreadable_scope_runs_the_check():
    r = ARKDDReport(); r.dd_scope = {"waivers": "not-a-list"}   # type: ignore[assignment]
    assert ddo._scope_waived(r, "IS-13") is None


def test_no_scope_at_all_runs_the_check():
    assert ddo._scope_waived(ARKDDReport(), "IS-13") is None


# ── 4. an order beats a decline, in the ENGINE ───────────────────────────────

def test_election_beats_waiver_in_the_engine():
    """Must match dd_standard.assess, or the engine and the checklist disagree about
    whether the work should have happened."""
    r = ARKDDReport()
    r.dd_scope = {"waivers": [_waiver()], "elections": [{"question_id": "IS-13"}]}
    assert ddo._scope_waived(r, "IS-13") is None


# ── 2 + 3. the screens actually skip, and say so ─────────────────────────────

def test_officer_screen_skips_when_waived_and_records_it():
    r = ARKDDReport()
    r.identity.directors = [{"name": "HOWARD, Justin"}]
    r.dd_scope = {"waivers": [_waiver("IS-13b")]}
    assert _run(ddo._screen_officer_sanctions(r, {})) is False
    gaps = " ".join(r.identity.data_gaps)
    assert "Officer sanctions screen WAIVED by A. Correa" in gaps
    assert "not a clear one" in gaps


def test_psc_screen_skips_when_officer_screening_is_waived():
    """Beneficial owners are officeholders for scope purposes; the two paths must not
    drift into disagreeing about what was declined."""
    r = ARKDDReport()
    r.identity.shareholders = [{"name": "Mr Justin Howard"}]
    r.dd_scope = {"waivers": [_waiver("IS-13b")]}
    assert _run(ddo._screen_psc_sanctions(r)) is False
    assert "PSC (beneficial owner) sanctions screen WAIVED" in " ".join(r.identity.data_gaps)


def test_officer_screen_still_runs_when_not_waived():
    """The guard must not become a blanket off-switch."""
    from unittest.mock import AsyncMock, patch
    from aria_service.intel import sanctions as _s
    r = ARKDDReport()
    r.identity.directors = [{"name": "HOWARD, Justin"}]
    with patch.object(_s, "screen_with_aliases",
                      AsyncMock(return_value={"matches": [], "screened": True})) as m:
        _run(ddo._screen_officer_sanctions(r, {}))
    assert m.called


def test_waived_screen_writes_a_marker_not_an_absence():
    r = ARKDDReport()
    ddo._record_waived_screen(r, _waiver(), what="Subject sanctions screen")
    blob = r.identity.sanctions_screen
    assert blob, "a declined screen left NO blob — render_markdown would omit the line"
    assert blob["waived"] is True
    assert blob["screened"] is False          # routes through the R-F3229 never-clean branch
    assert blob["verified_sources"] == []
    assert "WAIVED by A. Correa" in " ".join(r.identity.data_gaps)


def test_render_says_waived_with_who_and_why():
    r = ARKDDReport(); r.identity.entity_name = "Testco"
    ddo._record_waived_screen(r, _waiver(), what="Subject sanctions screen")
    line = [l for l in r.render_markdown().splitlines() if "Sanctions screen:" in l]
    assert line, "the sanctions line vanished — that is the omission this guards"
    assert "WAIVED by A. Correa" in line[0]
    assert "domestic UK contract" in line[0]
    assert "not a clearance" in line[0]


def test_a_waived_screen_never_renders_as_clean():
    r = ARKDDReport(); r.identity.entity_name = "Testco"
    ddo._record_waived_screen(r, _waiver(), what="Subject sanctions screen")
    line = [l for l in r.render_markdown().splitlines() if "Sanctions screen:" in l][0]
    assert "CLEAN" not in line.upper()


# ── the skip signal must never be reported as a failure ──────────────────────

def test_scope_waived_signal_is_caught_before_the_generic_handler():
    """`_ScopeWaived` is raised to skip a long inline block. If `except Exception` saw
    it first, a declined screen would land in the report as 'Sanctions screen failed' —
    an ERROR where the truth is a decision."""
    import inspect
    src = inspect.getsource(ddo._run_identity)
    i_waived = src.index("except _ScopeWaived")
    i_generic = src.index("except Exception as e:\n        logger.warning(\"Identity: sanctions screen failed")
    assert i_waived < i_generic, (
        "the _ScopeWaived handler must precede the generic one, or a declined check is "
        "reported as a failed check"
    )


def test_scope_waived_is_an_exception_not_a_baseexception():
    assert issubclass(ddo._ScopeWaived, Exception)


# ── the report carries the scope it ran under ────────────────────────────────

def test_orchestrator_sets_dd_scope_before_any_layer_runs():
    """Inspects `_orchestrate_dd_impl`, NOT `orchestrate_dd`.

    `orchestrate_dd` is a thin wrapper that delegates to the impl, so inspecting the
    wrapper reads a function that contains none of the run logic — my first version of
    this test did exactly that and failed against correct code. Asserting on the wrong
    entry point is the R-F1326 lesson (a green test driving a path the operator never
    takes), so the delegation itself is pinned below.
    """
    import inspect
    src = inspect.getsource(ddo._orchestrate_dd_impl)
    assert "report.dd_scope = _dd_scope_from_target(target)" in src
    assert src.index("report.dd_scope") < src.index("_run_identity"), (
        "scope must be set BEFORE the identity layer, or the first screen runs "
        "un-scoped and the allowance is spent anyway"
    )


def test_the_public_entrypoint_still_delegates_to_the_impl():
    """Guards the assumption the test above rests on: if `orchestrate_dd` ever stops
    delegating, the scope assertion would be checking dead code."""
    import inspect
    assert "_orchestrate_dd_impl" in inspect.getsource(ddo.orchestrate_dd)

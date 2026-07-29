"""R-F3397 — the officer sanctions screen ran before the registry produced any officers.

THE DEFECT (three faults, one inline block at dd_orchestrator.py:4495-4549).

  1. ORDERING. The screen consumed

         _directors_in = list(target.get("directors") or [])      # :4476

     — the directors the CALLER typed into the DD request — and ran at :4503.
     Companies House officers are not written to `report.identity.directors`
     until :4765, two hundred and sixty lines later. A DD launched from chat or
     the web button with just a company name therefore deterministically
     screened ZERO officers, while the report listed them in full.

  2. NEVER-FALSE-CLEAN BREACH. The block never checked `screened` / `error`
     (contrast the PSC sibling's R-F1696 guard at :3663). `screen_with_aliases`
     returns `{"screened": False, "source_unavailable": True}` when the source is
     unreachable (intel/sanctions.py:1345), so an unreachable source yielded
     `matches: []`, fell to the else-branch, and emitted

         "<role> <name> — sanctions screen CLEAN"   confidence=CONFIRMED   # :4538

     A screen that never reached a list, certified CONFIRMED clean about a named
     human being. This is the R-F3217/R-F3229 class, on a person.

  3. UNTRUSTED PROVENANCE. It called the screen with no `source=`, so the name
     arrived as `free_text` and was put through the R-F3228 search-query shape
     heuristic — the gate that produced the R-F3217 false clean. The PSC screen
     declares `source="registry"` for exactly this reason (:3658).

WHY IT IS STRUCTURAL, NOT AN ORDERING TWEAK. The PSC screen was given all three
properties by R-F3353 when it was extracted to `_screen_psc_sanctions`. The
officer path was never extracted, so it kept none of them. The fix mirrors that
extraction: ONE screen function, called after the registry has populated
officers, so there is no second aggregator to disagree with the first.

WHAT THESE TESTS DRIVE. `_screen_officer_sanctions` is the exact coroutine
`_run_identity` awaits — the same pattern test_rf3353_psc_sanctions_screen.py
uses. Assertions are on the user-visible outcome (findings and data gaps that
land in `report.identity`), plus one AST guard on the ORDERING property itself,
because ordering is the fault and no outcome assertion can pin it.
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
TREE = ast.parse(SRC)


def _run(coro):
    return asyncio.run(coro)


def _fn(name: str) -> ast.AST:
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} is not defined in dd_orchestrator")


def _report_with_registry_officers(name: str = "HOWARD, John Peter") -> ARKDDReport:
    """A report in the state the CH lookup leaves it in: officers present,
    nothing supplied by the caller."""
    report = ARKDDReport()
    report.identity.entity_name = "Silverbrook Capital Management Ltd"
    report.identity.directors = [
        {"name": name, "officer_role": "director", "appointed_on": "2014-06-02"}
    ]
    return report


def _screen(matches, *, screened=True, error=None):
    """Shape returned by sanctions.screen_with_aliases (intel/sanctions.py:1418)."""
    out = {"name": "x", "matches": matches, "screened": screened, "top_score": 0.0}
    if error:
        out["error"] = error
    return out


_HIT = [{"name": "JOHN PETER HOWARD", "score": 0.97, "list": "OFAC SDN",
         "topics": ["sanction"], "datasets": ["us_ofac_sdn"]}]


# ── ROOT-CAUSE GUARD: the ordering property itself ───────────────────────────

def test_officer_screen_runs_after_the_registry_populates_directors():
    """The fault was position, so this is the guard that would have caught it.

    Asserted over AST line numbers, not source text, so re-wording a comment
    cannot make it cry wolf and re-introducing the early call cannot hide.
    """
    run_identity = _fn("_run_identity")

    assigns = [
        node.lineno
        for node in ast.walk(run_identity)
        if isinstance(node, ast.Assign)
        for tgt in node.targets
        if isinstance(tgt, ast.Attribute) and tgt.attr == "directors"
    ]
    assert assigns, (
        "guard is blind — _run_identity no longer assigns report.identity.directors; "
        "re-anchor this test on wherever the registry now writes officers"
    )

    calls = [
        node.lineno
        for node in ast.walk(run_identity)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_screen_officer_sanctions"
    ]
    assert calls, "_run_identity never calls _screen_officer_sanctions"

    assert min(calls) > max(assigns), (
        f"the officer screen is called at line {min(calls)} but the registry writes "
        f"report.identity.directors at line {max(assigns)} — the screen cannot see an "
        f"officer the registry has not produced yet. This is R-F3397."
    )


def test_no_inline_screening_loop_over_people_in_run_identity():
    """One screen per subject-class, one aggregator.

    `_run_identity` legitimately screens the SUBJECT inline — a single call with
    `source="dd_subject"` (:4282). What must never come back is a screening LOOP,
    because iterating names is what an officeholder/PSC screen does, and a second
    such site is a second producer that can disagree with the first (the class
    CLAUDE.md §1 spent three R-numbers killing on the Phase A gates).

    So the property is "no screen call inside a for-loop", not "no screen call".
    """
    run_identity = _fn("_run_identity")

    def _screen_calls(node) -> list[int]:
        return [
            n.lineno
            for n in ast.walk(node)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr in {"screen_with_aliases", "fuzzy_screen"}
        ]

    assert _screen_calls(run_identity), (
        "guard is blind — no sanctions screen call found in _run_identity at all"
    )

    looped: list[int] = []
    for node in ast.walk(run_identity):
        if isinstance(node, (ast.For, ast.AsyncFor)):
            looped.extend(_screen_calls(node))
    assert not looped, (
        f"_run_identity screens a LIST of names inline at lines {looped}; a per-person "
        f"screening loop belongs in _screen_officer_sanctions / _screen_psc_sanctions "
        f"so it inherits one set of never-false-clean rules"
    )


# ── CAPABILITY: the symptom the operator hit ─────────────────────────────────

def test_registry_discovered_officer_is_screened():
    """THE SYMPTOM. No caller-supplied directors — only officers the registry
    returned. Before R-F3397 this produced no finding at all."""
    report = _report_with_registry_officers()
    with patch.object(_sanctions_mod, "screen_with_aliases",
                      return_value=_screen(_HIT)) as m:
        _run(ddo._screen_officer_sanctions(report, {"name": "Silverbrook Capital Management Ltd"}))

    assert m.called, "a registry-listed officer was never screened"
    titles = [f.title for f in report.identity.findings]
    assert any("HOWARD, John Peter" in t for t in titles), (
        f"sanctioned officer produced no finding; got {titles}"
    )
    hit = [f for f in report.identity.findings if "HOWARD, John Peter" in f.title][0]
    assert hit.severity in {"amber", "red", "hard_stop"}
    assert hit.source == "sanctions.director_screen"


def test_hard_stop_officer_propagates():
    report = _report_with_registry_officers()
    with patch.object(_sanctions_mod, "screen_with_aliases",
                      return_value=_screen(_HIT)):
        stopped = _run(ddo._screen_officer_sanctions(report, {}))
    assert stopped is True, "an officer on an active sanctions list did not hard-stop the DD"


def test_officer_screen_uses_a_trusted_name_source():
    """An officer name comes off the register. Screening it as untrusted free
    text re-runs the shape heuristic that caused the R-F3217 false clean."""
    report = _report_with_registry_officers()
    with patch.object(_sanctions_mod, "screen_with_aliases",
                      return_value=_screen([])) as m:
        _run(ddo._screen_officer_sanctions(report, {}))
    assert m.called
    src = m.call_args.kwargs.get("source")
    assert src in _sanctions_mod._TRUSTED_NAME_SOURCES, (
        f"officer screened with source={src!r}, which is not trusted — the name-shape "
        f"gate can silently reject real officer names"
    )


# ── NEVER-FALSE-CLEAN ────────────────────────────────────────────────────────

def test_unperformed_officer_screen_records_data_gap():
    report = _report_with_registry_officers()
    with patch.object(_sanctions_mod, "screen_with_aliases",
                      return_value=_screen([], screened=False,
                                           error="sanctions_source_unavailable")):
        _run(ddo._screen_officer_sanctions(report, {}))
    gaps = " ".join(report.identity.data_gaps)
    assert "SANCTIONS_SOURCE_UNVERIFIED" in gaps, (
        f"an unperformed officer screen left no gap marker — it reads as clean. gaps={gaps!r}"
    )
    assert "HOWARD, John Peter" in gaps


def test_unperformed_officer_screen_never_emits_a_clean_finding():
    """The precise live defect: an unreachable source produced
    '<role> <name> — sanctions screen CLEAN' at confidence CONFIRMED."""
    report = _report_with_registry_officers()
    with patch.object(_sanctions_mod, "screen_with_aliases",
                      return_value=_screen([], screened=False,
                                           error="sanctions_source_unavailable")):
        _run(ddo._screen_officer_sanctions(report, {}))
    clean = [f for f in report.identity.findings if "CLEAN" in (f.title or "").upper()]
    assert not clean, (
        f"a screen that never reached a list certified an officer CLEAN: "
        f"{[(f.title, f.confidence) for f in clean]}"
    )


def test_throwing_officer_screen_records_data_gap_not_silence():
    report = _report_with_registry_officers()
    with patch.object(_sanctions_mod, "screen_with_aliases",
                      side_effect=AttributeError("module has no attribute 'screen_entity'")):
        _run(ddo._screen_officer_sanctions(report, {}))
    gaps = " ".join(report.identity.data_gaps)
    assert "SANCTIONS_SOURCE_UNVERIFIED" in gaps, (
        f"a throwing officer screen left no gap — the R-F3353 false clean, one field over. "
        f"gaps={gaps!r}"
    )


def test_performed_clean_screen_records_no_gap():
    """The other half: a screen that DID run and found nothing must not be
    polluted with a gap, or every clean DD is downgraded."""
    report = _report_with_registry_officers()
    with patch.object(_sanctions_mod, "screen_with_aliases",
                      return_value=_screen([], screened=True)):
        _run(ddo._screen_officer_sanctions(report, {}))
    gaps = " ".join(report.identity.data_gaps)
    assert "SANCTIONS_SOURCE_UNVERIFIED" not in gaps, (
        f"a genuinely clean screen was marked unverified: {gaps!r}"
    )


# ── the caller-supplied path must not be lost in the move ────────────────────

def test_caller_supplied_director_still_screened():
    report = ARKDDReport()
    report.identity.entity_name = "Acme Ltd"
    with patch.object(_sanctions_mod, "screen_with_aliases",
                      return_value=_screen(_HIT)) as m:
        _run(ddo._screen_officer_sanctions(
            report, {"directors": [{"name": "Jane Q Public", "role": "Director"}]}))
    assert m.called, "a caller-supplied director stopped being screened"
    assert any("Jane Q Public" in f.title for f in report.identity.findings)


def test_contact_name_extracted_from_email_still_screened():
    report = ARKDDReport()
    with patch.object(_sanctions_mod, "screen_with_aliases",
                      return_value=_screen([])) as m:
        _run(ddo._screen_officer_sanctions(
            report, {"contact_email": "branislav.takac@example.sk"}))
    screened = [c.args[0] if c.args else c.kwargs.get("name") for c in m.call_args_list]
    assert any("Branislav" in str(s) for s in screened), (
        f"the email-derived contact name stopped being screened; screened={screened}"
    )


def test_registry_and_supplied_names_are_deduplicated():
    """The same person on both lists must cost one API call, not two."""
    report = _report_with_registry_officers("Jane Q Public")
    with patch.object(_sanctions_mod, "screen_with_aliases",
                      return_value=_screen([])) as m:
        _run(ddo._screen_officer_sanctions(
            report, {"directors": [{"name": "jane q public", "role": "Director"}]}))
    assert m.call_count == 1, (
        f"the same officer was screened {m.call_count} times — de-duplication is not working"
    )


def test_resigned_officer_is_skipped():
    """Mirrors the PSC screen's ceased_on skip: a former officer is not a
    current control relationship and should not burn quota."""
    report = ARKDDReport()
    report.identity.directors = [
        {"name": "Former Director", "officer_role": "director", "resigned_on": "2020-01-01"}
    ]
    with patch.object(_sanctions_mod, "screen_with_aliases",
                      return_value=_screen([])) as m:
        _run(ddo._screen_officer_sanctions(report, {}))
    assert not m.called, "a resigned officer was screened"


# ── the gap marker must reach the consumer that forces the headline non-GREEN ─

def test_gap_marker_matches_the_headline_downgrade_consumer():
    """dd_orchestrator downgrades GREEN→AMBER_LIGHT on data_gaps containing
    'SANCTIONS_SOURCE_UNVERIFIED'. Producer and consumer must agree or the gap
    is inert (the producer→consumer-no-carrier class)."""
    assert 'if "SANCTIONS_SOURCE_UNVERIFIED" in str(g)' in SRC

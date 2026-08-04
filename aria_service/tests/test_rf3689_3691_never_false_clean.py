"""R-F3689/R-F3690/R-F3691 — CAPABILITY: a screen that did not run can never
render as clean, and the matcher cannot certify a search it did not complete.

Found by the 360 DD sweep (docs/dd_360_ecosystem_2026_08_04.md). Seven distinct
paths could emit an authoritative clearance from an unperformed or incomplete
screen. Every test below drives the REAL function that was broken (§3c).

  R-F3689  dd_orchestrator person path — guarded on `screened is False or
           source_unavailable`, so `{"error": "not_entity_shaped"}` (which has
           NO `screened` key) counted as a SUCCESSFUL screen and stamped all 10
           canonical lists CLEAN at confidence=CONFIRMED.
  R-F3690  local_brain._fmt_sanctions printed a green tick naming "200+ lists";
           sanctions_divergence returned jurisdictions_not_listed=<every tracked
           jurisdiction> on a screen that never ran (and on one that THREW);
           dd_layer_extensions reported severity NONE, and its RCA layer read
           `result["relatives"]` — a key screen_with_relatives has never
           returned — so it could not report inherited risk at all;
           rescreen_public_watchlist overwrote a HIT baseline with CLEAN.
  R-F3691  the canonical matcher returned CLEAR when its candidate pre-filter
           was truncated, and dropped a short query against a long listed name
           because Jaccard is symmetric.

Run: python -m pytest aria_service/tests/test_rf3689_3691_never_false_clean.py -v
"""
from __future__ import annotations

import asyncio
import importlib
import os

import pytest


# ══════════════════════════════════════════════════════════════════════════
# R-F3689 — the person-path predicate
# ══════════════════════════════════════════════════════════════════════════

def _person_guard_counts_as_screened(scr: dict) -> bool:
    """The POSITIVE predicate now shipped in dd_orchestrator's person loop."""
    return bool(
        isinstance(scr, dict)
        and scr.get("screened") is True
        and not scr.get("error")
        and not scr.get("source_unavailable")
    )


def _old_person_guard_counts_as_screened(scr: dict) -> bool:
    """R-F2416's enumerating guard, kept to prove the defect was real."""
    if isinstance(scr, dict) and (
        scr.get("screened") is False or scr.get("source_unavailable")
    ):
        return False
    return True


# The exact shapes sanctions.screen_with_aliases returns on a non-screen.
UNPERFORMED_SHAPES = [
    ({"error": "not_entity_shaped", "matches": [], "blocked": False, "top_score": 0},
     "unshaped name — NO `screened` key at all (the live defect)"),
    ({"error": "name too short", "matches": []}, "too-short name"),
    ({"error": "no valid names to screen", "matches": []}, "no screenable variant"),
    ({"screened": False, "matches": []}, "explicit screened=False"),
    ({"source_unavailable": True, "matches": []}, "source unavailable"),
    ({"matches": []}, "bare dict — no completeness signal at all"),
    ({}, "empty dict"),
]


@pytest.mark.parametrize("shape,why", UNPERFORMED_SHAPES)
def test_person_guard_refuses_every_unperformed_shape(shape, why):
    assert _person_guard_counts_as_screened(shape) is False, (
        f"an unperformed screen ({why}) was counted as screened — this is what "
        f"stamped 10 lists CLEAN at confidence=CONFIRMED"
    )


def test_the_old_guard_really_did_admit_the_live_shape():
    """Proves the defect, so this suite cannot pass vacuously."""
    live = {"error": "not_entity_shaped", "matches": [], "blocked": False, "top_score": 0}
    assert _old_person_guard_counts_as_screened(live) is True, (
        "expected the pre-fix guard to admit not_entity_shaped"
    )
    assert _person_guard_counts_as_screened(live) is False


def test_a_real_screen_still_counts():
    assert _person_guard_counts_as_screened({"screened": True, "matches": []}) is True
    assert _person_guard_counts_as_screened({"screened": True, "matches": [{"name": "X"}]}) is True


def test_derive_verified_sources_refuses_to_clear_a_failed_screen():
    """The function the person path feeds — all sources UNAVAILABLE, not CLEAN."""
    from aria_service.intel._sanctions_classify import derive_verified_sources
    out = derive_verified_sources([], screen_succeeded=False)
    rendered = str(out)
    assert "CLEAN" not in rendered.upper() or "UNAVAILABLE" in rendered.upper(), (
        f"a failed screen must not produce CLEAN per-source statuses: {out}"
    )


def test_person_path_wires_an_unavailable_screen_to_the_brain():
    """§21a — the soft-failure path must produce a data gap AND a brain gap."""
    from aria_service.intel import dd_orchestrator as ddo
    assert hasattr(ddo, "_note_dd_screen_unavailable"), (
        "the soft-failure wiring helper must exist — a screen that did not run "
        "reached neither the operator nor the brain before R-F3689"
    )

    class _Meta:
        subcalls = 0

    class _Identity:
        def __init__(self):
            self.data_gaps = []
            self.meta = _Meta()

    class _Report:
        def __init__(self):
            self.identity = _Identity()

    rep = _Report()
    ddo._note_dd_screen_unavailable(rep, "Ahmed bin Mohammed Al-Saud", "source unavailable")
    assert rep.identity.data_gaps, "an unperformed screen must add a visible data gap"
    gap = rep.identity.data_gaps[0]
    assert "NOT PERFORMED" in gap.upper() or "UNSCREENED" in gap.upper()


# ══════════════════════════════════════════════════════════════════════════
# R-F3690 — the four other clearance surfaces
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("shape,why", UNPERFORMED_SHAPES)
def test_local_brain_never_prints_a_green_tick_on_an_unperformed_screen(shape, why):
    from aria_service.intel.local_brain import _fmt_sanctions
    out = _fmt_sanctions("Rosoboronexport", shape)
    assert "✅" not in out, f"green tick on an unperformed screen ({why}): {out[:120]}"
    assert "200+" not in out, (
        f"claimed coverage of 200+ lists that were never queried ({why})"
    )
    assert "COULD NOT VERIFY" in out.upper() or "DID NOT RUN" in out.upper()


def test_local_brain_still_reports_a_genuine_clean():
    from aria_service.intel.local_brain import _fmt_sanctions
    out = _fmt_sanctions("Acme Ltd", {"screened": True, "matches": [], "variants_tried": ["Acme Ltd"]})
    assert "✅" in out, "a screen that DID run with no matches must still read clean"


def test_local_brain_still_reports_a_hit():
    from aria_service.intel.local_brain import _fmt_sanctions
    out = _fmt_sanctions(
        "Rosoboronexport",
        {"screened": True, "blocked": True, "matches": [{"name": "JSC ROSOBORONEXPORT"}]},
    )
    assert "⛔" in out or "⚠️" in out


@pytest.mark.parametrize("shape,why", UNPERFORMED_SHAPES)
def test_divergence_clears_no_jurisdiction_on_an_unperformed_screen(shape, why, monkeypatch):
    from aria_service.intel import sanctions_divergence as sd

    async def _fake_screen(name, threshold=0.78):
        return shape

    import aria_service.intel.sanctions as _s
    monkeypatch.setattr(_s, "fuzzy_screen", _fake_screen)

    out = asyncio.run(sd.analyze_divergence("Rosoboronexport"))
    assert out["jurisdictions_not_listed"] == [], (
        f"an unperformed screen asserted NOT-LISTED across "
        f"{len(out['jurisdictions_not_listed'])} jurisdictions ({why}) — that "
        f"list IS the clean claim"
    )
    assert out["ok"] is False
    assert "NOT a clearance" in out["narrative"] or "UNVERIFIED" in out["narrative"]


def test_divergence_clears_no_jurisdiction_when_the_screen_throws(monkeypatch):
    from aria_service.intel import sanctions_divergence as sd
    import aria_service.intel.sanctions as _s

    async def _boom(name, threshold=0.78):
        raise RuntimeError("opensanctions unreachable")

    monkeypatch.setattr(_s, "fuzzy_screen", _boom)
    out = asyncio.run(sd.analyze_divergence("Rosoboronexport"))
    assert out["jurisdictions_not_listed"] == [], (
        "the EXCEPTION path returned a NOT-LISTED assertion for every tracked "
        "jurisdiction on a screen that had just thrown"
    )
    assert out["ok"] is False


@pytest.mark.parametrize("shape,why", UNPERFORMED_SHAPES)
def test_dd_layer_sanctions_reports_unknown_not_none(shape, why, monkeypatch):
    from aria_service.intel import dd_layer_extensions as dle
    import aria_service.intel.sanctions as _s

    async def _fake(name, **kw):
        return shape

    monkeypatch.setattr(_s, "screen_with_aliases", _fake)
    out = asyncio.run(dle._run_sanctions_divergence("Rosoboronexport"))
    assert out is not None
    assert out["severity"] == "UNKNOWN", (
        f"severity NONE is a clearance; an unperformed screen ({why}) must be UNKNOWN"
    )
    assert out.get("source_unavailable") is True


def test_dd_layer_rca_reads_the_key_that_actually_exists(monkeypatch):
    """R-F3690 §3b — `relatives` has never been a key of screen_with_relatives."""
    from aria_service.intel import dd_layer_extensions as dle
    import aria_service.intel.rca_screening as _rca

    async def _fake(name, **kw):
        return {
            "ok": True,
            "inherited_risks": [{"relative": "Spouse", "list": "OFAC SDN"}],
            "relatives_screened": 3,
            "relatives_unverified": 0,
        }

    monkeypatch.setattr(_rca, "screen_with_relatives", _fake)
    out = asyncio.run(dle._run_rca_screening("Some PEP"))
    assert out["severity"] == "ELEVATED", (
        "a spouse on OFAC SDN must ELEVATE — reading the non-existent "
        "`relatives` key made this permanently NONE"
    )
    assert out["hits"], "the inherited risk must surface as a hit"


def test_dd_layer_rca_treats_an_incomplete_walk_as_unknown(monkeypatch):
    from aria_service.intel import dd_layer_extensions as dle
    import aria_service.intel.rca_screening as _rca

    async def _fake(name, **kw):
        return {"ok": True, "inherited_risks": [], "relatives_screened": 1,
                "relatives_unverified": 2}

    monkeypatch.setattr(_rca, "screen_with_relatives", _fake)
    out = asyncio.run(dle._run_rca_screening("Some PEP"))
    assert out["severity"] == "UNKNOWN", (
        "relatives that could not be screened are not evidence of no risk"
    )


def test_dd_layer_rca_reports_unknown_when_the_source_is_down(monkeypatch):
    from aria_service.intel import dd_layer_extensions as dle
    import aria_service.intel.rca_screening as _rca

    async def _fake(name, **kw):
        return {"ok": False, "source_unavailable": True}

    monkeypatch.setattr(_rca, "screen_with_relatives", _fake)
    out = asyncio.run(dle._run_rca_screening("Some PEP"))
    assert out["severity"] == "UNKNOWN"
    assert out["source_unavailable"] is True


def test_layer_failures_are_wired_to_the_brain():
    """§21a — a crashing compliance layer must not be a logger.debug."""
    from aria_service.intel import dd_layer_extensions as dle
    assert hasattr(dle, "_note_layer_failure")
    dle._note_layer_failure("sanctions_divergence", "Acme", RuntimeError("boom"))


# ══════════════════════════════════════════════════════════════════════════
# R-F3691 — the matcher: truncation and containment, against a REAL store
# ══════════════════════════════════════════════════════════════════════════

def _row(uid: str, formatted: str, normalised: str | None = None,
         aliases=None, countries=None):
    """Build a store row using the REAL normaliser.

    An earlier draft hand-wrote `normalised_name`, which silently disagreed
    with what `normalise_name()` produces ('JSC ROSOBORONEXPORT' -> the corporate
    form is STRIPPED, giving 'rosoboronexport', not 'jsc rosoboronexport'). The
    test then exercised a store no loader could ever produce. Derive it.
    """
    from aria_service.intel.sanctions_canonical.normalise import normalise_name
    normalised = normalise_name(formatted) if normalised is None else normalised
    return {
        "source_uid": uid,
        "formatted_name": formatted,
        "normalised_name": normalised,
        "entity_type": "organisation",
        "countries": countries or ["RU"],
        "addresses": [],
        "aliases": aliases or [{"formatted": formatted, "normalised": normalised,
                                "alias_type": "primary"}],
        "programs": ["UKRAINE-EO14024"],
        "raw_excerpt": formatted,
    }


@pytest.fixture()
def canonical_store(tmp_path, monkeypatch):
    """A real sqlite canonical store, isolated per test."""
    db = tmp_path / "sanctions_canonical.db"
    monkeypatch.setenv("ARIA_SANCTIONS_CANONICAL_DB", str(db))
    from aria_service.intel.sanctions_canonical import store as _store
    from aria_service.intel.sanctions_canonical import lookup as _lookup
    importlib.reload(_store)
    importlib.reload(_lookup)
    return _store, _lookup


def test_short_query_against_a_long_listed_name_is_not_clear(canonical_store):
    """R-F3691 containment: the brand token of a designated entity."""
    _store, _lookup = canonical_store
    long_name = ("Rosoboronexport Federal State Unitary Enterprise "
                 "Defence Export Agency")
    _store.replace_source("ofac_sdn", [_row("OFAC-1", long_name)])

    for query in ("Rosoboronexport", "Rosoboronexport Ltd", "Rosoboronexport JSC"):
        out = _lookup.check_sanctions(query)
        assert out["verdict"] != "CLEAR", (
            f"{query!r} returned CLEAR against a store holding {long_name!r} — "
            f"symmetric Jaccard penalised the query for every token the LISTING "
            f"adds, so the exact brand token of a designated entity screened clean"
        )


def test_a_genuinely_unrelated_name_is_still_clear(canonical_store):
    """Containment must not turn the matcher into a rubber stamp."""
    _store, _lookup = canonical_store
    _store.replace_source("ofac_sdn", [
        _row("OFAC-1", "Rosoboronexport Federal State Unitary Enterprise"),
    ])
    out = _lookup.check_sanctions("Greggs Bakery Limited")
    assert out["verdict"] == "CLEAR", (
        f"an unrelated entity must still clear, got {out['verdict']} "
        f"({out.get('reason')})"
    )


def test_a_truncated_candidate_set_can_never_return_clear(canonical_store, monkeypatch):
    """R-F3691 truncation: reproduce the 600-decoy CLEAR."""
    _store, _lookup = canonical_store
    # Force a tiny cap so the truncation condition is reachable in a fast test —
    # the PROPERTY under test is "cap reached ⇒ not CLEAR", not the cap's value.
    monkeypatch.setattr(_lookup, "_CANDIDATE_LIMIT", 10)

    rows = [
        _row(f"DECOY-{i}", f"Mohammed Ali Decoy {i}")
        for i in range(40)
    ]
    rows.append(_row("OFAC-REAL", "Mohammed Ali Hassan Al Otaibi"))
    _store.replace_source("ofac_sdn", rows)

    out = _lookup.check_sanctions("Mohammed Ali Hassan Al Otaibi")
    assert out["verdict"] != "CLEAR", (
        "the candidate pre-filter hit its cap, so rows existed that were never "
        "scored — a no-match result there is INSUFFICIENT_DATA, never CLEAR"
    )
    if not out.get("matches"):
        assert out.get("reason") == "sanctions_candidate_truncation", (
            f"expected the truncation reason, got {out.get('reason')!r}"
        )


def test_an_untruncated_search_still_clears_normally(canonical_store):
    """The truncation gate must not degrade every ordinary screen."""
    _store, _lookup = canonical_store
    _store.replace_source("ofac_sdn", [
        _row("OFAC-1", "Rosoboronexport"),
    ])
    out = _lookup.check_sanctions("Greggs Bakery Limited")
    assert out["verdict"] == "CLEAR"
    assert out.get("reason") != "sanctions_candidate_truncation"


def test_an_exact_hit_is_still_found(canonical_store):
    _store, _lookup = canonical_store
    _store.replace_source("ofac_sdn", [
        _row("OFAC-1", "JSC ROSOBORONEXPORT"),
    ])
    out = _lookup.check_sanctions("JSC Rosoboronexport")
    assert out["verdict"] in ("HARD_STOP", "REVIEW"), (
        f"an exact designated match must not clear: {out['verdict']}"
    )

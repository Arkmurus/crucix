"""R-F3403 — the Gazette search runs inside a DD, and personal insolvency is the point.

R-F3422 wired Companies House `/insolvency`, which answers for the COMPANY while it
remains on the register. The Gazette adds the half no company register can hold:
PERSONAL insolvency — bankruptcy orders and IVAs against the natural persons who own and
run the subject. A DD whose whole discipline is "resolve the chain to real people" could
not previously answer "is this director bankrupt?".

THE HAZARD THIS SUITE PINS. The Gazette is a FREE-TEXT search over notice text, so a hit
can be a notice that merely MENTIONS the name. MEASURED live 2026-07-29: "Carillion"
returns 20 corporate notices, of which only 6 name it in the TITLE — the other 14 are
titled "Notice of Intended Dividends" and are creditor schedules or a practitioner's
other cases. Reported flatly, that is twenty insolvency notices "about" a company. The
DD therefore drives severity off TITLE matches only, and body-only matches are reported
as exactly what they are.

For people the same rule is stricter still: personal names are far less distinctive than
company names, so a personal-insolvency hit is always a NAME MATCH and never a
determination (the R-F3089 class, about a named human being).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from aria_service.intel import dd_orchestrator as ddo
from aria_service.intel.dd_schema import ARKDDReport
from aria_service.intel.sources import gazette as gz

# R-F3757/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so an edit mid-run silently returns a DIFFERENT function's body.
from ._source_probe import function_source


def _run(coro):
    return asyncio.run(coro)


def _report(name="Testco Ltd", directors=None, shareholders=None) -> ARKDDReport:
    r = ARKDDReport()
    r.identity.entity_name = name
    r.identity.directors = directors or []
    r.identity.shareholders = shareholders or []
    return r


def _res(hits=None, ok=True, outcome="ok", error=None):
    return {"source": "gazette", "ok": ok, "outcome": outcome, "error": error,
            "hits": hits or [], "hit_count": len(hits or []),
            "citation_url": "https://www.thegazette.co.uk/all-notices/notice",
            "corroboration_required": "Matched on notice TEXT, not on a registration number."}


def _hit(title, in_title=True):
    return {"title": title, "url": "https://x/1", "published": "2020-01-01",
            "subject_in_title": in_title}


def _drive(report, corporate=None, personal=None):
    async def _fake(name, *, personal=False, **kw):
        return (personal_res if personal else corp_res)
    corp_res = corporate if corporate is not None else _res(outcome="empty")
    personal_res = personal if personal is not None else _res(outcome="empty")
    with patch.object(gz, "search_insolvency", side_effect=_fake):
        _run(ddo._run_gazette_insolvency(report))
    return report


def _titles(r):
    return [f.title for f in r.identity.findings]


def _gaps(r):
    return " ".join(r.identity.data_gaps)


# ── it is actually wired ─────────────────────────────────────────────────────

def test_the_identity_layer_calls_it():
    import inspect
    assert "_run_gazette_insolvency(report)" in function_source(ddo, "_run_identity"), (
        "the adapter exists but the DD never runs it — the state R-F3422 had to fix "
        "for the CH registers"
    )


# ── title vs body: the live Carillion shape ──────────────────────────────────

def test_title_matches_drive_the_amber():
    r = _drive(_report("Carillion"), corporate=_res([
        _hit("CARILLION CONSTRUCTION LIMITED"), _hit("CARILLION PLC")]))
    f = next(f for f in r.identity.findings if "Gazette" in f.title)
    assert f.severity == "amber"
    assert "NAMING" in f.title


def test_body_only_matches_do_not_drive_severity():
    """The live shape: 14 of 20 Carillion hits were titled 'Notice of Intended
    Dividends'. Flat reporting turns creditor schedules into insolvency findings."""
    r = _drive(_report("Carillion"), corporate=_res([
        _hit("Notice of Intended Dividends", in_title=False),
        _hit("SEMPERIAN (FAZAKERLEY) LIMITED and other companies", in_title=False)]))
    f = next(f for f in r.identity.findings if "Gazette" in f.title)
    assert f.severity == "info", "a body-only mention was raised to amber"
    assert "notice text only" in f.title
    assert "not a proceeding against this company" in f.detail


def test_every_hit_finding_carries_the_corroboration_requirement():
    r = _drive(_report("Carillion"), corporate=_res([_hit("CARILLION PLC")]))
    f = next(f for f in r.identity.findings if "Gazette" in f.title)
    assert "registration number" in f.detail
    assert f.confidence == "ASSESSED", "a free-text match was stated as CONFIRMED"


def test_an_answered_empty_search_is_a_finding():
    r = _drive(_report())
    assert any("No corporate insolvency notice" in t for t in _titles(r))
    assert _gaps(r) == ""


# ── a source that did not answer is never a clean line ───────────────────────

def test_a_failed_corporate_search_is_a_gap_not_a_clean_result():
    r = _drive(_report(), corporate=_res(ok=False, outcome="timeout",
                                         error="The Gazette returned HTTP 500"))
    assert not any("No corporate insolvency" in t for t in _titles(r)), (
        "a search that never answered produced a clean line"
    )
    assert "did not complete" in _gaps(r) and "not a clear result" in _gaps(r)


def test_a_raising_search_is_a_gap():
    r = _report()
    with patch.object(gz, "search_insolvency", AsyncMock(side_effect=RuntimeError("boom"))):
        _run(ddo._run_gazette_insolvency(r))
    assert "NOT searched" in _gaps(r)


# ── PERSONAL insolvency: the half no company register holds ──────────────────

def test_directors_and_pscs_are_both_searched_for_personal_insolvency():
    seen: list[str] = []

    async def _fake(name, *, personal=False, **kw):
        if personal:
            seen.append(name)
        return _res(outcome="empty")

    with patch.object(gz, "search_insolvency", side_effect=_fake):
        _run(ddo._run_gazette_insolvency(_report(
            directors=[{"name": "Jane Q Public"}],
            shareholders=[{"name": "Raven Delta Holder"}])))
    assert "Jane Q Public" in seen
    assert "Raven Delta Holder" in seen, (
        "a beneficial owner's bankruptcy bears on control as much as a director's"
    )


def test_a_personal_hit_never_asserts_identity():
    r = _drive(_report(directors=[{"name": "Jane Q Public"}]),
               personal=_res([_hit("Jane Q PUBLIC")]))
    f = next(f for f in r.identity.findings if "Jane Q Public" in f.title)
    assert "identity NOT confirmed" in f.title
    assert f.severity == "amber"
    assert "not a determination" in f.detail
    assert f.confidence == "ASSESSED"


def test_a_personal_body_only_match_is_not_reported_at_all():
    """For a person a body mention is noise — their name appearing in someone else's
    creditor schedule says nothing about them."""
    r = _drive(_report(directors=[{"name": "Jane Q Public"}]),
               personal=_res([_hit("Notice of Intended Dividends", in_title=False)]))
    assert not any("Jane Q Public" in t for t in _titles(r))


def test_resigned_and_ceased_people_are_skipped():
    seen: list[str] = []

    async def _fake(name, *, personal=False, **kw):
        if personal:
            seen.append(name)
        return _res(outcome="empty")

    with patch.object(gz, "search_insolvency", side_effect=_fake):
        _run(ddo._run_gazette_insolvency(_report(
            directors=[{"name": "Former Director", "resigned_on": "2020-01-01"}],
            shareholders=[{"name": "Ceased Holder", "ceased_on": "2021-01-01"}])))
    assert seen == []


def test_the_same_person_on_both_lists_is_searched_once():
    seen: list[str] = []

    async def _fake(name, *, personal=False, **kw):
        if personal:
            seen.append(name)
        return _res(outcome="empty")

    with patch.object(gz, "search_insolvency", side_effect=_fake):
        _run(ddo._run_gazette_insolvency(_report(
            directors=[{"name": "Justin Howard"}],
            shareholders=[{"name": "justin howard"}])))
    assert len(seen) == 1


def test_person_cap_truncation_is_disclosed():
    """A silent cap reads as 'everyone was searched'."""
    many = [{"name": f"Person Number {i}"} for i in range(ddo._GAZETTE_PERSON_CAP + 3)]
    r = _drive(_report(directors=many))
    assert "capped at" in _gaps(r)
    assert "NOT searched" in _gaps(r)


def test_a_failed_personal_search_is_a_gap():
    r = _drive(_report(directors=[{"name": "Jane Q Public"}]),
               personal=_res(ok=False, outcome="timeout", error="no response"))
    assert "Personal-insolvency search 'Jane Q Public' did not complete" in _gaps(r)


def test_a_short_entity_name_is_not_searched():
    r = _report(name="AB")
    with patch.object(gz, "search_insolvency", AsyncMock()) as m:
        _run(ddo._run_gazette_insolvency(r))
    assert not m.called

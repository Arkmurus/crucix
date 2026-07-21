"""R-F2828 — get_officers must keep the anchors and stop truncating.

Two defects, both found by probing the RAW Companies House payload live:

1. ANCHORS DISCARDED. The mapping kept 8 display fields and dropped
   `links.officer.appointments` (which carries the officer id) and
   `person_number`. The DD therefore held 35 BAE officers it could not follow
   anywhere, so no ANCHORED person->company edge could be built from them —
   only a name match, which is the fabrication class R-F2726 removed.

2. SILENT TRUNCATION. The call returned CH's default page verbatim: 35 officers
   against `total_results: 73`. Under half the officer record, on the question
   about ownership and control, with nothing saying so.

Live-verified after the fix: 73/73 officers, 73/73 anchored, 73 distinct ids,
and following one anchor resolves to a real appointment.

★ Truncation must be REPORTED, never silent. If the safety ceiling is hit the
result is capped but `consume_unavailable()` says so, so a capped list can never
be mistaken for a complete register (the never-false-clean rule applied to
completeness rather than to risk).
"""
from __future__ import annotations

import pytest

from aria_service.intel import companies_house as ch


def _item(n: int, oid: str = "", resigned=None) -> dict:
    """A CH officers item shaped like the real payload."""
    d = {
        "name": f"OFFICER, Number{n}",
        "officer_role": "director",
        "appointed_on": "2020-01-01",
        "resigned_on": resigned,
        "nationality": "British",
        "country_of_residence": "England",
        "occupation": "Director",
        "person_number": f"PN{n:06d}",
        "links": {"self": f"/company/01470151/appointments/APPT{n}"},
    }
    if oid:
        d["links"]["officer"] = {"appointments": f"/officers/{oid}/appointments"}
    return d


def _pager(monkeypatch, total: int, page_size: int = 100, with_oid: bool = True):
    """Stub _get as a real pager: honours start_index and reports total_results."""
    calls: list[str] = []

    async def _fake_get(path: str, _attempt: int = 0):
        calls.append(path)
        start = 0
        if "start_index=" in path:
            start = int(path.split("start_index=")[1].split("&")[0])
        items = [
            _item(i, oid=f"OID{i}" if with_oid else "")
            for i in range(start, min(start + page_size, total))
        ]
        return {"items": items, "total_results": total,
                "items_per_page": page_size, "start_index": start}

    monkeypatch.setattr(ch, "_get", _fake_get)
    return calls


async def test_all_pages_are_fetched_not_just_the_first(monkeypatch):
    """THE REGRESSION: 73 officers must come back, not the first page."""
    calls = _pager(monkeypatch, total=73)
    officers = await ch.get_officers("01470151")

    assert len(officers) == 73, (
        f"got {len(officers)} of 73 — get_officers is still returning one page"
    )
    assert len(calls) >= 1
    assert all("start_index=" in c for c in calls), "pager must send start_index"


async def test_officer_id_anchor_is_preserved(monkeypatch):
    """The anchor is what makes a person->company edge Grade-A eligible."""
    _pager(monkeypatch, total=3)
    officers = await ch.get_officers("01470151")

    assert all(o["officer_id"] for o in officers), "officer_id anchor was dropped"
    assert officers[0]["officer_id"] == "OID0"
    assert len({o["officer_id"] for o in officers}) == 3, "anchors must be distinct"
    assert all(o["person_number"] for o in officers)
    assert all(o["appointment_link"] for o in officers)


async def test_missing_anchor_is_empty_string_not_none(monkeypatch):
    """An absent anchor must be explicit and falsy, never None."""
    _pager(monkeypatch, total=2, with_oid=False)
    officers = await ch.get_officers("01470151")

    assert all(o["officer_id"] == "" for o in officers)
    assert not any(o["officer_id"] is None for o in officers)


async def test_display_fields_are_unchanged(monkeypatch):
    """Back-compat: existing consumers of the 8 original fields keep working."""
    _pager(monkeypatch, total=1)
    o = (await ch.get_officers("01470151"))[0]

    for k in ("name", "role", "appointed_on", "resigned_on", "nationality",
              "country_of_residence", "occupation", "is_current"):
        assert k in o, f"dropped pre-existing field {k}"
    assert o["role"] == "director"
    assert o["is_current"] is True


async def test_resigned_officer_is_not_current(monkeypatch):
    async def _fake_get(path: str, _attempt: int = 0):
        return {"items": [_item(1, oid="OID1", resigned="2024-06-01")],
                "total_results": 1}
    monkeypatch.setattr(ch, "_get", _fake_get)

    o = (await ch.get_officers("01470151"))[0]
    assert o["is_current"] is False
    assert o["resigned_on"] == "2024-06-01"


async def test_truncation_is_reported_never_silent(monkeypatch):
    """★ A capped list must not read as the whole register."""
    monkeypatch.setattr(ch, "_MAX_OFFICERS", 10)
    _pager(monkeypatch, total=50, page_size=10)

    officers = await ch.get_officers("01470151")
    reason = ch.consume_unavailable()

    assert len(officers) <= 10
    assert reason and "truncated" in reason, (
        "a capped officer list MUST report truncation — a silent cap is "
        "indistinguishable from a complete register"
    )
    assert "50" in reason, "the reported reason should name the true total"


async def test_complete_fetch_reports_no_unavailability(monkeypatch):
    """The inverse: a complete fetch must NOT raise a false data-gap."""
    _pager(monkeypatch, total=73)
    await ch.get_officers("01470151")
    assert ch.consume_unavailable() is None


async def test_empty_page_does_not_spin_forever(monkeypatch):
    """Defensive: a non-advancing pager must terminate."""
    async def _fake_get(path: str, _attempt: int = 0):
        return {"items": [], "total_results": 999}
    monkeypatch.setattr(ch, "_get", _fake_get)

    officers = await ch.get_officers("01470151")
    assert officers == []


async def test_no_data_returns_empty_list(monkeypatch):
    async def _fake_get(path: str, _attempt: int = 0):
        return None
    monkeypatch.setattr(ch, "_get", _fake_get)
    assert await ch.get_officers("01470151") == []


def test_officer_id_extraction_shapes():
    f = ch._officer_id_from_links
    assert f({"links": {"officer": {"appointments": "/officers/ABC123/appointments"}}}) == "ABC123"
    assert f({"links": {"self": "/x"}}) == ""
    assert f({}) == ""
    assert f({"links": None}) == ""
    assert f({"links": {"officer": {"appointments": "/malformed"}}}) == ""

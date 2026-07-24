"""R-F2993 — director appointment counts resolve by Companies House officer_id,
not a name-string company search.

Live Silverbrook defect: the walker called search_companies(name), counting
companies NAMED like the officer, which inflated Justin Howard's real 5
appointments (by officer_id) to "10+" and drove a false nominee-director pattern.
This test drives the real _other_appointments_for_officer with a common-name
collision (two distinct real people share the name) and proves only the person on
the SUBJECT company is counted, with their real other appointments.
"""
import asyncio
from unittest.mock import patch

from aria_service.intel import network_walker

RIGHT = "u_right_justin"
WRONG = "u_wrong_justin"


async def _fake_search_officers(name, limit=5):
    # a common name → two distinct real people both called "HOWARD, Justin"
    return [
        {"officer_id": RIGHT, "title": "HOWARD, Justin"},
        {"officer_id": WRONG, "title": "HOWARD, Justin"},
    ]


async def _fake_get_officer_appointments(officer_id, limit=20):
    if officer_id == RIGHT:
        # the person who actually sits on the subject company: 5 total (subject + 4)
        return [
            {"company_number": "04300718", "company_name": "SILVERBROOK CAPITAL MANAGEMENT LIMITED", "is_current": True},
            {"company_number": "AAAA0001", "company_name": "DVOC CAPITAL COMPARTMENT LTD", "is_current": True},
            {"company_number": "AAAA0002", "company_name": "TONIL HOLDINGS UK LIMITED", "is_current": True},
            {"company_number": "AAAA0003", "company_name": "A W JOYA LIMITED", "is_current": True},
            {"company_number": "AAAA0004", "company_name": "SEAGREEN GLOBAL LIMITED", "is_current": False},
        ]
    # a DIFFERENT Justin Howard with 12 unrelated companies — the collision that
    # used to inflate the count to "10+".
    return [
        {"company_number": f"BBBB{i:04d}", "company_name": f"OTHER JH CO {i}", "is_current": True}
        for i in range(12)
    ]


def _run(coro):
    return asyncio.run(coro)


def test_rf2993_counts_only_the_person_on_the_subject_company():
    with patch.object(network_walker, "logger"), \
         patch("aria_service.intel.companies_house.search_officers", new=_fake_search_officers), \
         patch("aria_service.intel.companies_house.get_officer_appointments", new=_fake_get_officer_appointments):
        out = _run(network_walker._other_appointments_for_officer(
            "HOWARD, Justin", subject_company_number="04300718", limit=20))
    nums = {a["company_number"] for a in out}
    # the real person's 4 OTHER appointments (subject excluded), NOT the 12-company collision
    assert nums == {"AAAA0001", "AAAA0002", "AAAA0003", "AAAA0004"}, nums
    assert not any(str(a["company_number"]).startswith("BBBB") for a in out)
    # 4 < 10 → the "10+ cross-linked appointments" nominee flag will NOT fire (the fix)
    assert len(out) == 4
    assert all(a.get("matched_via_officer_id") for a in out)


def test_rf2993_returns_empty_when_person_not_disambiguated():
    # subject company that matches NEITHER candidate's appointments → cannot resolve
    # the person → return [] rather than a name-collision count.
    with patch.object(network_walker, "logger"), \
         patch("aria_service.intel.companies_house.search_officers", new=_fake_search_officers), \
         patch("aria_service.intel.companies_house.get_officer_appointments", new=_fake_get_officer_appointments):
        out = _run(network_walker._other_appointments_for_officer(
            "HOWARD, Justin", subject_company_number="ZZZZ9999", limit=20))
    assert out == []

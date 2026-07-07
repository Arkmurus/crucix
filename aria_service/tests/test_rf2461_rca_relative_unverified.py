"""R-F2461 — RCA: a related party the sanctions source could NOT screen must be
surfaced as UNVERIFIED, never silently dropped as 'no inherited risk' (FATF R.12
false clean).

_screen_one_relative previously returned None for BOTH "no match" and
"source unavailable" (fuzzy_screen soft-returns source_unavailable without
raising). screen_with_relatives then read the unavailable relative as clean.
Post-fix: _screen_one_relative returns a {"_source_unavailable": True} sentinel,
and screen_with_relatives tracks relatives_unverified + adds a NOT-a-clearance
caveat to the narrative.

Drives the REAL screen_with_relatives via a mocked fuzzy_screen dispatched by name.
"""
import asyncio

import aria_service.intel.sanctions as _sanc
from aria_service.intel import rca_screening

PRIMARY = "Boss Person"
RELATIVE = "Relative X"


def _primary_with_relative():
    return {
        "matches": [{
            "name": PRIMARY, "score": 0.95, "lists": ["OFAC"], "topics": ["sanction"],
            "relationships": [{"target": RELATIVE, "kind": "spouse"}],
        }],
        "screened": True, "source_unavailable": False,
    }


async def _run(relative_screen):
    table = {PRIMARY: _primary_with_relative(), RELATIVE: relative_screen}

    async def fake_fuzzy(nm, threshold=0.78, *a, **k):
        return table.get(nm, {"matches": [], "screened": True, "source_unavailable": False})

    orig = _sanc.fuzzy_screen
    _sanc.fuzzy_screen = fake_fuzzy  # type: ignore[assignment]
    try:
        return await rca_screening.screen_with_relatives(PRIMARY, depth=1, threshold=0.78)
    finally:
        _sanc.fuzzy_screen = orig  # type: ignore[assignment]


def test_unavailable_relative_is_unverified_not_clean():
    out = asyncio.run(_run({"matches": [], "screened": False, "source_unavailable": True}))
    assert out["relatives_unverified"] == 1, out
    assert RELATIVE in out["unverified_relatives"], out
    assert "could NOT be screened" in out["narrative"], out["narrative"]
    assert out["inherited_risks"] == [], "an unscreenable relative must not become an inherited-risk finding"


def test_clean_relative_no_false_unverified():
    out = asyncio.run(_run({"matches": [], "screened": True, "source_unavailable": False}))
    assert out["relatives_unverified"] == 0, out
    assert "could NOT be screened" not in out["narrative"], out["narrative"]


def test_hit_relative_still_recorded():
    hit = {"matches": [{"name": RELATIVE, "score": 0.9, "lists": ["OFAC"], "topics": ["sanction"]}],
           "screened": True, "source_unavailable": False}
    out = asyncio.run(_run(hit))
    assert out["relatives_unverified"] == 0, out
    assert len(out["inherited_risks"]) == 1, out
    assert out["inherited_risks"][0]["relative"] == RELATIVE


if __name__ == "__main__":
    test_unavailable_relative_is_unverified_not_clean()
    print("PASS test_unavailable_relative_is_unverified_not_clean")
    test_clean_relative_no_false_unverified()
    print("PASS test_clean_relative_no_false_unverified")
    test_hit_relative_still_recorded()
    print("PASS test_hit_relative_still_recorded")
    print("ALL PASS")

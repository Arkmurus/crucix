"""R-F2373 (H3) — RCA screen must NOT false-clean on source-unavailable.

Drives the REAL `screen_with_relatives` function (CLAUDE.md §3c). fuzzy_screen
returns matches=[] BOTH when it screened and found nothing AND when the source
was UNAVAILABLE (OpenSanctions down / breaker open → source_unavailable=True /
screened=False). Before the fix, the empty-matches branch narrated
"no direct or inherited sanctions risk detected" — a false clearance on a screen
that never ran.

Written to FAIL before the fix / PASS after.
"""
from __future__ import annotations

import asyncio

import aria_service.intel.sanctions as sanctions_mod
from aria_service.intel.rca_screening import screen_with_relatives


def test_rf2373_rca_source_unavailable_is_unverified_not_clean(monkeypatch):
    """OpenSanctions down → matches=[] + source_unavailable=True + screened=False.
    screen_with_relatives must return UNVERIFIED, never 'no risk'."""
    async def _fake_fuzzy(name, threshold=0.78, **k):
        return {"matches": [], "source_unavailable": True, "screened": False,
                "error": "sanctions_source_unavailable"}
    monkeypatch.setattr(sanctions_mod, "fuzzy_screen", _fake_fuzzy)

    r = asyncio.run(screen_with_relatives("Acme Holdings Ltd"))

    assert r.get("source_unavailable") is True, (
        "source-unavailable must propagate so downstream renders UNVERIFIED"
    )
    assert r.get("ok") is False, "an unverified screen is not ok=True"
    narrative = (r.get("narrative") or "").lower()
    assert "no direct or inherited sanctions risk" not in narrative, (
        f"a source-outage must NOT read as a clearance — narrative={narrative!r}"
    )
    assert "unverified" in narrative, f"narrative should say UNVERIFIED — {narrative!r}"


def test_rf2373_rca_screened_no_match_still_clean(monkeypatch):
    """The guard must ONLY fire on unavailable/unscreened. A genuine screened
    clean (screened=True, source_unavailable absent, matches=[]) still narrates
    'no direct or inherited sanctions risk detected' with ok=True."""
    async def _fake_fuzzy(name, threshold=0.78, **k):
        return {"matches": [], "screened": True, "source_unavailable": False}
    monkeypatch.setattr(sanctions_mod, "fuzzy_screen", _fake_fuzzy)

    r = asyncio.run(screen_with_relatives("Cleanco Ltd"))

    assert r.get("ok") is True
    assert not r.get("source_unavailable")
    assert "no direct or inherited sanctions risk detected" in (r.get("narrative") or "")

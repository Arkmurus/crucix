"""R-F899 — self_diagnostic catalogue covers the dark runtime engines.

Ecosystem-360 P2: self_diagnostic covered only ~16% of modules (~42), so an
import break or a lost entry-point in a runtime safety/compliance/honesty engine
went unnoticed — and self_diagnostic RED routes into the gap pipeline
(brain_hook.absorb), so uncatalogued = invisible. R-F899 adds the dark engines
to the catalogue. The value is the import + entry liveness check: if any of them
breaks, self_diagnostic now goes RED and the brain learns.
"""
from __future__ import annotations

import asyncio

import pytest

# The 8 engines R-F899 newly catalogued. (sanctions_divergence was ALREADY
# in the catalogue with an endpoint check — being liveness-checked there is
# separate from feeding the brain on its catches, which R-F898 fixed.)
_EXPECTED = {
    "eliminated_weapons_watchlist",
    "weapon_origin_catalogue",
    "evasion_typology_detector",
    "regional_compliance",
    "security_protocol",
    "premise_verifier",
    "honesty_judge",
    "semantic_search",
}


def test_rf899_dark_engines_are_catalogued():
    from aria_service.intel import self_diagnostic as sd
    names = {m["name"] for m in sd._MODULES}
    missing = _EXPECTED - names
    assert not missing, f"dark engines missing from self_diagnostic catalogue: {missing}"


def test_rf899_new_entries_pass_import_and_entry():
    """Capability: self_diagnostic must find every newly-catalogued engine
    ALIVE (import PASS + entry PASS). This both proves the catalogue entries
    are correct (right module path + callable) AND that a real break would be
    caught (FAIL → RED → gap pipeline)."""
    from aria_service.intel import self_diagnostic as sd

    specs = [m for m in sd._MODULES if m["name"] in _EXPECTED]
    assert len(specs) == len(_EXPECTED)

    async def run():
        for spec in specs:
            row = await sd._check_module(spec)
            checks = {c["check"]: c["status"] for c in row["checks"]}
            assert checks.get("import") == "PASS", f"{spec['name']} import not PASS: {row}"
            assert checks.get("entry") == "PASS", f"{spec['name']} entry not PASS: {row}"
            assert row["worst_status"] != "FAIL", f"{spec['name']} worst FAIL: {row}"
            # No endpoint/env/smoke checks added for these (avoids false REDs)
            assert "endpoint" not in checks
            # brain_registered=False → the brain check is correctly skipped
            assert "brain_registered" not in checks

    asyncio.run(run())


def test_rf899_critical_safety_engines_flagged_critical():
    from aria_service.intel import self_diagnostic as sd
    by_name = {m["name"]: m for m in sd._MODULES}
    for n in ("eliminated_weapons_watchlist", "security_protocol",
              "premise_verifier", "honesty_judge", "semantic_search"):
        assert by_name[n].get("critical") is True, f"{n} should be critical"

"""R-F2881 — the registry-coverage inventory must be served by a route.

The CH/NO/EE registry work (coverage 24→27) went into registry_adapters /
registry_coverage, which had NO endpoint and NO page — vault.html reads the SEPARATE
agent-signup vault, so the new EU registries were structurally invisible. This route
(+ a vault.html panel) surfaces the inventory: every jurisdiction, its adapter, and
observed tri-state liveness.
"""
import asyncio


def test_rf2881_route_registered():
    """GET /registry/coverage must be registered so the inventory is reachable."""
    from aria_service.routes.aria import router
    paths = [getattr(r, "path", "") for r in router.routes]
    assert any("registry/coverage" in p for p in paths), \
        "GET /registry/coverage must exist — without it the 27-jurisdiction coverage is invisible"


def test_rf2881_coverage_surfaces_the_new_eu_registries():
    """The data the route serves must include CH/NO/EE and the full adapter set."""
    from aria_service.intel import registry_coverage as rc
    cov = asyncio.run(rc.coverage())
    js = cov.get("jurisdictions") or {}
    for iso2 in ("CH", "NO", "EE"):
        assert iso2 in js, f"{iso2} must appear in the coverage inventory the route serves"
    assert len(js) >= 26, f"expected the full adapter set (>=26), got {len(js)}"
    # each entry must carry the honest tri-state liveness (True/False/None), not a
    # fabricated 'live' — a new/unproven registry reads live=None, never a false claim.
    assert all("live" in v and "status" in v for v in js.values())

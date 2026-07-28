"""R-F2979 (P4c) — ARIA Ecosystem completeness CI gate ("nothing gets missed").

This is the structural enforcer (same philosophy as test_rf2278 duplicate-route
guard): because the map's module node set IS the filesystem, a NEW aria_service
module automatically appears on the map — and if that ever stopped being true,
THIS test goes red. It keeps the "100% by construction" promise honest as code
changes, instead of relying on anyone remembering to add nodes by hand.
"""
from __future__ import annotations

import asyncio

from aria_service.intel import ecosystem_map as em


def test_rf2979_map_module_set_is_exactly_the_filesystem():
    """The nothing-missed gate: every non-test module on disk is on the map, and the
    map invents no module that isn't on disk."""
    # R-F3358 — the gate now spans BOTH tiers. It is deliberately checked per
    # tier and not softened to "the Python set is a subset of the map": a subset
    # check would still pass if the Node scan silently returned nothing, which is
    # precisely the blind spot R-F3352 had to declare in the first place.
    data = asyncio.run(em.build_structure(force=True))
    tiers = {
        "aria-intel": (
            {em._module_id(p) for p in em.scan_modules()},
            {n["module_id"] for n in data["nodes"]
             if n["type"] == "module" and n.get("tier_service") == "aria-intel"},
        ),
        "node": (
            {em._node_module_id(s, r) for s, r in em.scan_node_modules()},
            {n["module_id"] for n in data["nodes"]
             if n["type"] == "module" and n.get("tier_service") in ("aria-web", "aria-wa")},
        ),
    }
    for tier, (disk, mapped) in tiers.items():
        assert disk, f"{tier}: the scan itself returned nothing — the gate would pass vacuously"
        missing = disk - mapped
        extra = mapped - disk
        assert not missing, f"{tier}: on disk but MISSING from the map (nothing-missed VIOLATED): {sorted(missing)[:20]}"
        assert not extra, f"{tier}: map invented modules not on disk: {sorted(extra)[:20]}"


def test_rf2979_coverage_reports_100pct_modules():
    """The /coverage proof must always report modules 100% on the map by construction."""
    cov = asyncio.run(em.get_coverage())
    assert cov["modules"]["pct_mapped"] == 100.0
    assert cov["modules"]["on_map"] == cov["modules"]["total_on_disk"]
    # honest: unresolvable import edges are reported, never silently dropped
    assert "unresolved_intra_repo" in cov["import_edges"]


def test_rf2979_organ_table_covers_a_healthy_majority():
    """Soft gate on the ONE curated layer (the organ table): keep a healthy majority
    of modules assigned so the map stays useful. Orphans are allowed + surfaced (a
    RED alert), but a collapse in assignment (organ table rotted) should flag."""
    data = asyncio.run(em.build_structure())
    m = data["meta"]
    assigned = m["module_count"] - m["orphan_count"]
    pct = 100.0 * assigned / m["module_count"] if m["module_count"] else 0.0
    assert pct >= 60.0, f"organ assignment collapsed to {pct:.0f}% — refresh the organ table (orphans={m['orphan_count']})"

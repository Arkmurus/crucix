"""R-F2969/R-F2970 (P1) — ARIA Ecosystem live architecture map (structure).

Enforces the honest completeness guarantee: the module node set IS the filesystem
(nothing missed by construction), import edges are intra-repo only, ids are unique,
and the endpoint mirrors the /neural/graph shape. `test_zero_orphans` is the
"nothing missed" enforcer — it does NOT fail on unassigned modules (those are a
surfaced RED alert, not a hidden gap), it asserts the coverage math is consistent.
"""
from __future__ import annotations

import asyncio

from aria_service.intel import ecosystem_map as em


def test_rf2969_module_tier_equals_scan_modules():
    """COMPLETENESS BY CONSTRUCTION: every non-test aria_service module on disk is a
    node on the map — the node set is exactly scan_modules(). Nothing can be missed."""
    disk_ids = {em._module_id(p) for p in em.scan_modules()}
    data = asyncio.run(em.build_structure(force=True))
    map_ids = {n["module_id"] for n in data["nodes"] if n["type"] == "module"}
    assert map_ids == disk_ids, (
        f"map modules != filesystem; missing={disk_ids - map_ids}, extra={map_ids - disk_ids}")
    assert data["meta"]["module_count"] == len(disk_ids)


def test_rf2969_import_edges_intra_repo_only():
    """Every import edge connects two REAL module nodes (no phantom/external targets)."""
    data = asyncio.run(em.build_structure())
    mod_ids = {n["id"] for n in data["nodes"] if n["type"] == "module"}
    for e in data["edges"]:
        if e["type"] == "import":
            assert e["source"] in mod_ids and e["target"] in mod_ids, f"import edge to non-node: {e}"
            assert e["source"] != e["target"], f"self-import edge: {e}"


def test_rf2969_no_duplicate_node_ids():
    data = asyncio.run(em.build_structure())
    ids = [n["id"] for n in data["nodes"]]
    assert len(ids) == len(set(ids)), "duplicate node ids"


def test_rf2969_every_module_has_a_parent_organ():
    """Every module node points at an organ (a real one or the unassigned bucket) —
    there is no module floating with no container."""
    data = asyncio.run(em.build_structure())
    organ_ids = {n["id"] for n in data["nodes"] if n["type"] == "organ"}
    for n in data["nodes"]:
        if n["type"] == "module":
            assert n["parent"] in organ_ids, f"module {n['id']} has no organ parent"


def test_rf2969_orphans_are_surfaced_not_hidden():
    """The 'nothing missed' enforcer: unassigned modules are COUNTED and exposed
    (coverage math consistent), and if any exist they get the RED alert node."""
    data = asyncio.run(em.build_structure())
    cov = asyncio.run(em.get_coverage())
    m = data["meta"]
    # coverage math is internally consistent
    assert cov["modules"]["total_on_disk"] == m["module_count"]
    assert cov["modules"]["orphans"] == m["orphan_count"]
    assert cov["modules"]["pct_mapped"] == 100.0  # by construction
    assert cov["modules"]["on_map"] == cov["modules"]["total_on_disk"]
    # if there are orphans, they are represented by the RED alert node
    if m["orphan_count"] > 0:
        assert any(n.get("orphan_alert") for n in data["nodes"]), "orphans exist but no RED alert node"


def test_rf2969_call_edges_declared_partial_not_faked():
    """Honesty: the coverage report must DECLARE the fn-call graph as partial, never
    claim a fabricated 100% call graph."""
    cov = asyncio.run(em.get_coverage())
    assert cov["call_edges"]["status"] == "declared_partial"
    assert "undecidable" in cov["call_edges"]["reason"]


def test_rf2970_graph_endpoint_shape_mirrors_neural():
    """The endpoint returns the /neural/graph shape: nodes[], edges[] with
    source/target, plus meta."""
    from aria_service.routes import aria as aria_routes
    g = asyncio.run(aria_routes.ecosystem_graph_ep(root=None))
    assert "nodes" in g and "edges" in g and "meta" in g
    assert all("id" in n and "label" in n for n in g["nodes"])
    assert all("source" in e and "target" in e and "type" in e for e in g["edges"])
    # root view = services (T0) + organs (T1) only
    assert {n["type"] for n in g["nodes"]} <= {"service", "organ"}


def test_rf2970_drilldown_scopes_to_organ():
    """Drilling into an organ returns that organ + its modules + intra-organ import edges."""
    from aria_service.routes import aria as aria_routes
    g = asyncio.run(aria_routes.ecosystem_graph_ep(root="organ:learning"))
    types = {n["type"] for n in g["nodes"]}
    assert "module" in types, "organ drill must expose its modules"
    assert any(n["id"] == "organ:learning" for n in g["nodes"])


def test_rf2970_node_detail_has_fan_and_rnumbers():
    from aria_service.routes import aria as aria_routes
    d = asyncio.run(aria_routes.ecosystem_node_ep("mod:aria_service.intel.student"))
    assert d["node"]["type"] == "module"
    assert "fan_in" in d and "fan_out" in d
    assert isinstance(d["r_numbers"], list)

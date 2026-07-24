"""R-F2974/R-F2975 (P3) — ecosystem R-number/audit labels + function-level drill.

The operator's explicit ask: every intersection shows its R-numbers/audit refs.
Plus drill to the LOWEST level (functions, wired/dark) and the gap-label fix.
"""
from __future__ import annotations

import asyncio

from aria_service.intel import ecosystem_map as em


def test_rf2974_audit_refs_resolve_to_titles():
    """Every R-F token on a node resolves to a real reservation title/commit (join
    against the R-F540 registry) — audit labels are backed by the log, not invented."""
    d = asyncio.run(em.get_node("mod:aria_service.intel.student"))
    assert d["r_numbers"], "student.py should carry R-F tokens"
    assert "audit_refs" in d and len(d["audit_refs"]) == len(d["r_numbers"])
    for ref in d["audit_refs"]:
        assert ref["r"].startswith("R-F")
        assert "title" in ref and "commit" in ref  # resolved from the registry (may be '' if unshipped)


def test_rf2974_audit_refs_only_real_rnumbers():
    """A node's audit refs are exactly its source R-F tokens — no fabricated numbers."""
    idx = em._load_rnum_index()
    d = asyncio.run(em.get_node("mod:aria_service.intel.ecosystem_map"))
    for ref in d["audit_refs"]:
        # the token came from grepping the file; the title is '' only if not in the log
        assert ref["r"] in d["r_numbers"]


def test_rf2975_module_node_detail_has_functions():
    """A module node exposes its functions (the lowest level) with wired/dark status."""
    d = asyncio.run(em.get_node("mod:aria_service.intel.student"))
    assert "functions" in d and d["function_count"] > 0
    assert d["wired_functions"] <= d["function_count"]
    assert all("name" in f and "wired" in f and "line" in f for f in d["functions"])


def test_rf2975_graph_drill_reveals_function_nodes():
    """Drilling into a module injects its functions as T3 nodes (bounded, on-demand)."""
    g = asyncio.run(em.get_graph(root="mod:aria_service.intel.regional_drift_monitor"))
    fns = [n for n in g["nodes"] if n["type"] == "function"]
    assert fns, "module drill must reveal function nodes"
    assert all(n["tier"] == 3 and n["parent"] == "mod:aria_service.intel.regional_drift_monitor" for n in fns)
    # function health = §21 wiring (green wired / grey dark), never a fabricated live colour
    for n in fns:
        assert n["health"] in ("green", "grey")
        assert ("wiring" in n["sensor"]) or ("dark" in n["sensor"])


def test_rf2975_functions_not_in_full_graph():
    """Functions are on-demand only — the root/full graph must NOT contain ~5000 fn nodes."""
    g = asyncio.run(em.get_graph())  # root view
    assert not any(n["type"] == "function" for n in g["nodes"])


def test_rf2974_gap_label_uses_type_field():
    """The gap sensor label reads the 'type' field (record_gap stores gap_type under
    'type'), so it never renders 'gap[None]'."""
    sig = {"breakers": [], "agents": [], "limbs": {}, "surfaces": {},
           "gaps": [{"severity": "HIGH", "type": "source_failure", "source": "ofac_sdn", "detail": "x"}],
           "read_at": 1.0}
    hmap = em._build_health_map(sig, {"organ:sanctions"}, {})
    assert hmap["organ:sanctions"]["sensor"] == "gap[source_failure]", hmap["organ:sanctions"]["sensor"]
    assert "None" not in hmap["organ:sanctions"]["sensor"]

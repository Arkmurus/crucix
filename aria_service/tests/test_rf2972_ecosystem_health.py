"""R-F2972 (P2) — ARIA Ecosystem live health overlay.

The honesty guarantees for the green/amber/red/grey colouring (anti-hallucination
law #4): GREEN only from a positive live signal; ABSENCE of problems is GREY, never
green; a slow-cycle agent between beats is AMBER, never a false-RED (anti cry-wolf);
zero-traffic surfaces stay grey; a broken node bleeds red into its edges.
"""
from __future__ import annotations

import asyncio

from aria_service.intel import ecosystem_map as em


def _sig(**kw):
    base = {"breakers": [], "agents": [], "limbs": {}, "surfaces": {}, "gaps": [], "read_at": 1.0}
    base.update(kw)
    return base


def test_rf2972_no_fabricated_green():
    """A node with NO live signal is absent from the health map → renders grey,
    never a fabricated green."""
    node_ids = {"mod:aria_service.intel.some_unmonitored_module", "organ:sanctions"}
    hmap = em._build_health_map(_sig(), node_ids, {})
    assert "mod:aria_service.intel.some_unmonitored_module" not in hmap, "unmonitored node must have no colour (→grey)"
    # aria-intel is the ONLY always-green node (it is serving this very request) —
    # and only when it's in the node set.
    hmap2 = em._build_health_map(_sig(), {"aria-intel"}, {})
    assert hmap2["aria-intel"]["color"] == "green"
    assert "request" in hmap2["aria-intel"]["sensor"]


def test_rf2972_open_breaker_is_red():
    node_ids = {"organ:sanctions"}
    hmap = em._build_health_map(_sig(breakers=[{"name": "ofac", "state": "OPEN", "last_failure_reason": "server"}]), node_ids, {})
    assert hmap["organ:sanctions"]["color"] == "red"
    assert "circuit_breaker[ofac]" in hmap["organ:sanctions"]["sensor"]


def test_rf2972_closed_breaker_is_green():
    node_ids = {"organ:sanctions"}
    hmap = em._build_health_map(_sig(breakers=[{"name": "ofac", "state": "CLOSED"}]), node_ids, {})
    assert hmap["organ:sanctions"]["color"] == "green"


def test_rf2972_stale_agent_is_amber_not_red():
    """Anti cry-wolf: a long-cycle agent 700s (or even hours) between beats is AMBER,
    never a false RED. Only >24h silence escalates to red."""
    node_ids = {"organ:learning"}
    amber = em._build_health_map(_sig(agents=[{"agent_id": "student_reading", "heartbeat_age_s": 700}]), node_ids, {})
    assert amber["organ:learning"]["color"] == "amber", "700s-stale agent must be amber, not red"
    hours = em._build_health_map(_sig(agents=[{"agent_id": "student_reading", "heartbeat_age_s": 7 * 3600}]), node_ids, {})
    assert hours["organ:learning"]["color"] == "amber", "a 7h-cycle agent between beats must NOT cry red"
    dead = em._build_health_map(_sig(agents=[{"agent_id": "student_reading", "heartbeat_age_s": 90000}]), node_ids, {})
    assert dead["organ:learning"]["color"] == "red", ">24h silence = genuinely abandoned = red"


def test_rf2972_fresh_agent_is_green():
    node_ids = {"organ:learning"}
    hmap = em._build_health_map(_sig(agents=[{"agent_id": "student_reading", "heartbeat_age_s": 30}]), node_ids, {})
    assert hmap["organ:learning"]["color"] == "green"


def test_rf2972_zero_traffic_surface_stays_grey():
    """A delivery surface with no traffic proves nothing → no colour (grey)."""
    node_ids = {"organ:delivery"}
    hmap = em._build_health_map(_sig(surfaces={"surfaces": {"wa": {"total": 0, "success_rate": None}}}), node_ids, {})
    assert "organ:delivery" not in hmap, "zero-traffic surface must not colour the node"


def test_rf2972_low_delivery_is_red():
    node_ids = {"organ:delivery"}
    hmap = em._build_health_map(_sig(surfaces={"surfaces": {"wa": {"total": 20, "success_rate": 0.5}}}), node_ids, {})
    assert hmap["organ:delivery"]["color"] == "red"


def test_rf2972_high_gap_escalates_to_red():
    node_ids = {"organ:sanctions"}
    hmap = em._build_health_map(_sig(gaps=[{"severity": "HIGH", "source": "ofac_sdn", "gap_type": "source_failure", "detail": "x"}]), node_ids, {})
    assert hmap["organ:sanctions"]["color"] == "red"


def test_rf2972_edge_inherits_worst_endpoint():
    """A broken node bleeds red into its import links."""
    graph = {
        "nodes": [{"id": "organ:sanctions"}, {"id": "mod:aria_service.intel.ofac_sdn"}],
        "edges": [{"source": "mod:aria_service.intel.ofac_sdn", "target": "organ:sanctions", "type": "contains"}],
    }
    sig = _sig(breakers=[{"name": "ofac", "state": "OPEN"}])
    asyncio.run(em._apply_health_to(graph, sig, {}))
    assert graph["edges"][0]["health"] == "red", "edge touching a red node must be red"


def test_rf2972_coverage_reports_grey_not_green_rule():
    cov = asyncio.run(em.get_coverage())
    hs = cov["health_sensors"]
    assert "with_live_sensor" in hs and "grey_no_sensor" in hs
    assert "GREEN only from a positive" in hs["rule"]
    # honest: most nodes have no direct sensor → grey dominates, not green
    assert hs["grey_no_sensor"] >= hs["with_live_sensor"]

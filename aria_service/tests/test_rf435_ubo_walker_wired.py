"""R-F435 — walk_ubo_chain wired into DD orchestrator Layer 2.

Pre-R-F435: network_walker.walk_ubo_chain() existed (518-793) but was
only ever invoked from test files — production DD reports never carried
a UBO walk result. Operators had to manually re-call the walker after
the DD completed. This means every DD missed the multi-hop ownership
graph (sanctioned-in-chain detection, PEP-in-chain detection, explicit
jurisdiction coverage gaps).

R-F435 wires the walker into `_run_network` as part of Layer 2, so
every company DD attempts a recursive UBO walk. The walker has its
own budget caps (max_hops=3, max_total_nodes=50). Persons skip.
Opt-out via env var ARIA_UBO_WALK_DISABLED=1.

Output surfaces in report.network:
  - ubo_chain        — flattened node list (existing reader at 4252)
  - ubo_chain_walk   — full walker dict (graph, stats, verdict, gaps)
  - findings         — sanctioned_in_chain (hard_stop/red), PEP (amber)
  - data_gaps        — coverage_gaps + budget_exhausted
"""
from __future__ import annotations

import asyncio


# ───────────────────────── Schema — fields exist on NetworkSection ────────────


def test_rf435_network_section_has_ubo_chain_fields():
    """NetworkSection must carry ubo_chain (list) and ubo_chain_walk (dict)
    so the orchestrator output can persist + render the walker result."""
    from aria_service.intel.dd_schema import NetworkSection
    ns = NetworkSection()
    assert hasattr(ns, "ubo_chain"), "R-F435: NetworkSection missing ubo_chain"
    assert hasattr(ns, "ubo_chain_walk"), "R-F435: NetworkSection missing ubo_chain_walk"
    assert isinstance(ns.ubo_chain, list)
    assert isinstance(ns.ubo_chain_walk, dict)


def test_rf435_ubo_chain_preserves_pre_existing_reader_contract():
    """dd_orchestrator.py:4252 iterates `report.network.ubo_chain` and
    calls `u.get('name')`. The list-of-dicts shape MUST hold so that
    reader keeps working when the walker populates it."""
    from aria_service.intel.dd_schema import NetworkSection
    ns = NetworkSection()
    ns.ubo_chain = [{"name": "Acme Holdings Ltd"}, {"name": "Joe Director"}]
    # Replicate the reader at dd_orchestrator.py:4252
    names = [
        u.get("name") for u in (getattr(ns, "ubo_chain", []) or [])
    ]
    assert names == ["Acme Holdings Ltd", "Joe Director"]


# ───────────────────────── Unit — walker is invoked for company entities ──────


def _make_report():
    # R-F670 (2026-05-17): ARKDDReport schema was refactored after R-F435
    # was written — `subject_input` is no longer a field; the trigger
    # input now lives in `target: dict`. Closes 6 R-F435 test fails the
    # audit 2026-05-17 mis-identified as an `app.state.llm_provider`
    # fixture cluster. The real cause was a schema-drift between the
    # fixture and the dataclass.
    from aria_service.intel.dd_schema import ARKDDReport, EntityType
    r = ARKDDReport(run_id="test-rf435", target={"name": "Acme Ltd"})
    r.identity.entity_name = "Acme Holdings Ltd"
    r.identity.entity_type = EntityType.COMPANY.value
    r.identity.jurisdiction_iso2 = "GB"
    r.identity.registration_number = "12345678"
    return r


def test_rf435_run_network_invokes_walker_for_company(monkeypatch):
    """A company DD must trigger walk_ubo_chain. Persons (R-F435 scope-out)
    must NOT trigger it — they have no UBO chain."""
    from aria_service.intel import dd_orchestrator, network_walker

    calls: list[dict] = []

    async def _fake_walk_ubo(**kwargs):
        calls.append({"kind": "ubo", "kwargs": kwargs})
        return {
            "graph": {"nodes": [{"id": "seed", "name": kwargs["entity_name"]}], "edges": []},
            "sanctioned_in_chain": [],
            "pep_in_chain": [],
            "coverage_gaps": [],
            "stats": {"total_nodes": 1, "total_edges": 0, "sanctions_screens": 0, "registry_lookups": 0},
            "warnings": [],
            "verdict": "uncovered",
        }

    async def _fake_walk_network(**kwargs):
        calls.append({"kind": "network", "kwargs": kwargs})
        return {
            "director_graph": {}, "cross_linked_entities": [], "address_cluster": {},
            "pep_connections": [], "sanctions_network": [], "findings": [], "data_gaps": [],
            "stats": {},
        }

    monkeypatch.setattr(network_walker, "walk_ubo_chain", _fake_walk_ubo)
    monkeypatch.setattr(network_walker, "walk_network", _fake_walk_network)
    monkeypatch.delenv("ARIA_UBO_WALK_DISABLED", raising=False)

    report = _make_report()
    asyncio.run(dd_orchestrator._run_network({"name": "Acme"}, report))

    assert any(c["kind"] == "ubo" for c in calls), (
        "R-F435 regression: walk_ubo_chain was NOT invoked on a company DD"
    )
    # Walker received the seed fields straight from identity
    ubo_call = next(c for c in calls if c["kind"] == "ubo")
    assert ubo_call["kwargs"]["entity_name"] == "Acme Holdings Ltd"
    assert ubo_call["kwargs"]["jurisdiction_iso2"] == "GB"
    assert ubo_call["kwargs"]["registration_number"] == "12345678"


def test_rf435_run_network_skips_walker_for_person(monkeypatch):
    """Person entities have no UBO chain — skip the walker."""
    from aria_service.intel import dd_orchestrator, network_walker
    from aria_service.intel.dd_schema import EntityType

    calls: list[str] = []

    async def _fake_walk_ubo(**kwargs):
        calls.append("ubo")
        return {"graph": {"nodes": [], "edges": []}, "sanctioned_in_chain": [],
                "pep_in_chain": [], "coverage_gaps": [], "stats": {}, "warnings": [],
                "verdict": "uncovered"}

    async def _fake_walk_network(**kwargs):
        return {"director_graph": {}, "cross_linked_entities": [], "address_cluster": {},
                "pep_connections": [], "sanctions_network": [], "findings": [],
                "data_gaps": [], "stats": {}}

    monkeypatch.setattr(network_walker, "walk_ubo_chain", _fake_walk_ubo)
    monkeypatch.setattr(network_walker, "walk_network", _fake_walk_network)
    monkeypatch.delenv("ARIA_UBO_WALK_DISABLED", raising=False)

    report = _make_report()
    report.identity.entity_type = EntityType.PERSON.value
    asyncio.run(dd_orchestrator._run_network({"name": "Joe"}, report))

    assert "ubo" not in calls, (
        "R-F435: walk_ubo_chain must NOT fire on person entities"
    )


def test_rf435_kill_switch_disables_walker(monkeypatch):
    """ARIA_UBO_WALK_DISABLED=1 is an ops escape hatch — when set, the
    walker is skipped entirely (no call, no cost)."""
    from aria_service.intel import dd_orchestrator, network_walker

    calls: list[str] = []

    async def _fake_walk_ubo(**kwargs):
        calls.append("ubo")
        return {"graph": {"nodes": [], "edges": []}, "sanctioned_in_chain": [],
                "pep_in_chain": [], "coverage_gaps": [], "stats": {}, "warnings": [],
                "verdict": "uncovered"}

    async def _fake_walk_network(**kwargs):
        return {"director_graph": {}, "cross_linked_entities": [], "address_cluster": {},
                "pep_connections": [], "sanctions_network": [], "findings": [],
                "data_gaps": [], "stats": {}}

    monkeypatch.setattr(network_walker, "walk_ubo_chain", _fake_walk_ubo)
    monkeypatch.setattr(network_walker, "walk_network", _fake_walk_network)
    monkeypatch.setenv("ARIA_UBO_WALK_DISABLED", "1")

    report = _make_report()
    asyncio.run(dd_orchestrator._run_network({"name": "Acme"}, report))
    assert "ubo" not in calls, "R-F435: kill switch must short-circuit the walker"


# ───────────────────────── Capability — findings surface end-to-end ───────────


def test_rf435_capability_sanctioned_in_chain_becomes_network_finding(monkeypatch):
    """A sanctioned hit found via UBO walk MUST surface as a Network
    finding so the operator sees indirect sanctions exposure. Pre-R-F435
    these hits were inaccessible — the walker was never called."""
    from aria_service.intel import dd_orchestrator, network_walker

    async def _fake_walk_ubo(**kwargs):
        return {
            "graph": {"nodes": [
                {"id": "seed", "name": kwargs["entity_name"]},
                {"id": "n1", "name": "Sanctioned UBO Person", "type": "person", "hop_depth": 1},
            ], "edges": []},
            "sanctioned_in_chain": [{
                "name": "Sanctioned UBO Person",
                "hop": 1,
                "severity": "hard_stop",
                "summary": "OFAC SDN hit at hop 1 via director chain",
                "parent_entity": "Acme Holdings Ltd",
            }],
            "pep_in_chain": [],
            "coverage_gaps": [],
            "stats": {"total_nodes": 2, "total_edges": 1, "sanctions_screens": 1, "registry_lookups": 1},
            "warnings": [],
            "verdict": "traced",
        }

    async def _fake_walk_network(**kwargs):
        return {"director_graph": {}, "cross_linked_entities": [], "address_cluster": {},
                "pep_connections": [], "sanctions_network": [], "findings": [],
                "data_gaps": [], "stats": {}}

    monkeypatch.setattr(network_walker, "walk_ubo_chain", _fake_walk_ubo)
    monkeypatch.setattr(network_walker, "walk_network", _fake_walk_network)
    monkeypatch.delenv("ARIA_UBO_WALK_DISABLED", raising=False)

    report = _make_report()
    asyncio.run(dd_orchestrator._run_network({"name": "Acme"}, report))

    # The walker output is persisted
    assert report.network.ubo_chain, (
        f"R-F435: ubo_chain not populated. ubo_chain={report.network.ubo_chain}"
    )
    assert report.network.ubo_chain_walk, "R-F435: full walker dict not stored"
    # The sanctioned-in-chain hit became a Network finding
    titles = [f.title for f in report.network.findings]
    assert any("UBO chain sanctions: Sanctioned UBO Person" in t for t in titles), (
        f"R-F435: sanctioned_in_chain hit didn't surface as Network finding. "
        f"Findings: {titles}"
    )


def test_rf435_capability_coverage_gap_surfaces_as_data_gap(monkeypatch):
    """When the walker can't traverse a jurisdiction (no registry adapter),
    the coverage_gap must surface as a network data_gap — operator sees
    "no UBO coverage for jurisdiction X" instead of silent failure."""
    from aria_service.intel import dd_orchestrator, network_walker

    async def _fake_walk_ubo(**kwargs):
        return {
            "graph": {"nodes": [{"id": "seed", "name": kwargs["entity_name"]}], "edges": []},
            "sanctioned_in_chain": [],
            "pep_in_chain": [],
            "coverage_gaps": [{
                "jurisdiction": "VG",
                "hop": 0,
                "entity_name": "BVI Holdings Inc",
                "reason": "no officer-registry adapter for jurisdiction=VG",
            }],
            "stats": {"total_nodes": 1, "total_edges": 0, "sanctions_screens": 0, "registry_lookups": 1},
            "warnings": [],
            "verdict": "uncovered",
        }

    async def _fake_walk_network(**kwargs):
        return {"director_graph": {}, "cross_linked_entities": [], "address_cluster": {},
                "pep_connections": [], "sanctions_network": [], "findings": [],
                "data_gaps": [], "stats": {}}

    monkeypatch.setattr(network_walker, "walk_ubo_chain", _fake_walk_ubo)
    monkeypatch.setattr(network_walker, "walk_network", _fake_walk_network)
    monkeypatch.delenv("ARIA_UBO_WALK_DISABLED", raising=False)

    report = _make_report()
    asyncio.run(dd_orchestrator._run_network({"name": "BVI Holdings Inc"}, report))

    assert any("no officer-registry adapter" in g for g in report.network.data_gaps), (
        f"R-F435: coverage_gap didn't reach data_gaps. "
        f"data_gaps={report.network.data_gaps}"
    )


def test_rf435_capability_walker_exception_is_non_fatal(monkeypatch):
    """If the walker crashes (network error, registry timeout), the
    Network layer must continue — surfacing the failure as a data_gap,
    NOT crashing the whole DD."""
    from aria_service.intel import dd_orchestrator, network_walker

    async def _fake_walk_ubo(**kwargs):
        raise RuntimeError("simulated registry timeout")

    async def _fake_walk_network(**kwargs):
        return {"director_graph": {}, "cross_linked_entities": [], "address_cluster": {},
                "pep_connections": [], "sanctions_network": [], "findings": [],
                "data_gaps": [], "stats": {}}

    monkeypatch.setattr(network_walker, "walk_ubo_chain", _fake_walk_ubo)
    monkeypatch.setattr(network_walker, "walk_network", _fake_walk_network)
    monkeypatch.delenv("ARIA_UBO_WALK_DISABLED", raising=False)

    report = _make_report()
    # Must NOT raise — exception is contained inside the UBO try-block.
    asyncio.run(dd_orchestrator._run_network({"name": "Acme"}, report))

    assert any("UBO walk failed" in g for g in report.network.data_gaps), (
        f"R-F435: walker exception didn't surface as a data_gap. "
        f"data_gaps={report.network.data_gaps}"
    )

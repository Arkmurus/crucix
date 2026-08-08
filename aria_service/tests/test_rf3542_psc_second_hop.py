"""R-F3542 — the second PSC hop was never traversed, so there was no ultimate owner.

THE GAP, on four consecutive delivered reports. `get_psc` returns the subject's PSCs and
R-F2726 carefully preserves `identification.registration_number` for corporate ones — the
ANCHOR that makes a control edge Grade A. **Nothing ever called `get_psc` again with it.**
The chain stopped at hop one, so a corporate PSC was as far as ARIA could see and "who
ultimately owns this" had no answer.

On Bidvest Noonan (dd_75d996233394) the corporate PSC Crane Midco Limited (06648599) was
fully walkable in Companies House and terminates at a JSE-listed parent — a clean,
decision-relevant answer ("controlled by a listed group; no individual UBO above
threshold") the report never went and got.

NOT `network_walker.walk_ubo_chain`, which despite its name walks DIRECTORSHIPS and emits
no ownership relationship at all (the R-F3539 category error). This walks ownership only.

EVERY WAY THE WALK CAN STOP MUST BE DECLARED. A truncated chain that reads as complete is
a false clean about ownership — the most consequential question in the report. So
`complete` is False and `gaps` names the reason for: an unanchored controller, a foreign
registry, a cycle, a hop/node cap, or a failed read.

ANCHORED ONLY. A hop is taken via `identification.registration_number` and never by
resolving a controller's NAME — that is the fabrication R-F2703/R-F2726 removed.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import companies_house as ch

# R-F3770/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so an edit mid-run silently returns a DIFFERENT function's body.
from ._source_probe import function_source


def _drive(graph, **kw):
    """Run the walker against a fake register."""
    async def _fake(n):
        return graph.get(n, [])
    orig = ch.get_psc
    ch.get_psc = _fake
    try:
        return asyncio.run(ch.walk_psc_ownership("ROOT", **kw))
    finally:
        ch.get_psc = orig


def _corp(name, regno, country="England"):
    return {"name": name, "kind": "corporate-entity-person-with-significant-control",
            "is_current": True, "natures_of_control": ["ownership-of-shares-75-to-100-percent"],
            "identification": {"registration_number": regno, "country_registered": country}}


def _person(name, kind="individual-person-with-significant-control"):
    return {"name": name, "kind": kind, "is_current": True,
            "natures_of_control": ["ownership-of-shares-75-to-100-percent"],
            "identification": None}


# ── the capability that was missing ─────────────────────────────────────────

def test_capability_the_second_hop_is_traversed_to_an_ultimate_owner():
    """THE BIDVEST CHAIN: subject -> Crane Midco (corporate, anchored) -> listed parent."""
    r = _drive({
        "ROOT": [_corp("Crane Midco Limited", "06648599")],
        "06648599": [_person("The Bidvest Group Limited",
                             "legal-person-person-with-significant-control")],
    })
    assert r["complete"] is True, r["gaps"]
    assert [(e["from"], e["to"]) for e in r["edges"]] == [("ROOT", "06648599")]
    assert [u["name"] for u in r["ultimate"]] == ["The Bidvest Group Limited"]
    assert r["gaps"] == []


def test_capability_it_walks_more_than_two_hops():
    r = _drive({
        "ROOT": [_corp("Mid Ltd", "111")],
        "111": [_corp("Upper Ltd", "222")],
        "222": [_person("Jane Owner")],
    })
    assert r["complete"] is True
    assert len(r["edges"]) == 2
    assert [u["name"] for u in r["ultimate"]] == ["Jane Owner"]


# ── every stop is DECLARED ──────────────────────────────────────────────────

def test_an_unanchored_corporate_controller_is_a_declared_gap():
    """R-F3027's live case: `Raven Delta Limited`, 75-100% control, no registration
    number. It must NOT be resolved by name, and the chain above it is NOT established."""
    holder = _corp("Raven Delta Limited", "")
    holder["identification"] = {"legal_form": "Private Limited Company"}
    r = _drive({"ROOT": [holder]})
    assert r["complete"] is False
    assert any("NO registration number" in g for g in r["gaps"])
    assert r["edges"] == [], "an unanchored controller must not produce an edge"


def test_a_foreign_registry_ends_the_walk_with_a_reason():
    """Companies House holds UK companies. A Jersey/BVI parent is where the walk ENDS,
    and the report must say so rather than presenting a partial chain as complete."""
    r = _drive({"ROOT": [_corp("Offshore Holdings Ltd", "J123", country="Jersey")]})
    assert r["complete"] is False
    assert any("outside the UK" in g for g in r["gaps"])


def test_a_cycle_is_detected_and_declared():
    r = _drive({"ROOT": [_corp("A Ltd", "AAA")], "AAA": [_corp("Root Again", "ROOT")]})
    assert r["complete"] is False
    assert any("cycle" in g.lower() for g in r["gaps"])


def test_the_hop_cap_is_declared_not_silent():
    r = _drive({"ROOT": [_corp("A", "1")], "1": [_corp("B", "2")],
                "2": [_corp("C", "3")], "3": [_person("Deep Owner")]}, max_hops=2)
    assert r["complete"] is False
    assert any("max_hops" in g for g in r["gaps"])


def test_a_failed_read_is_a_gap_not_a_terminus():
    async def _boom(n):
        if n == "ROOT":
            return [_corp("A Ltd", "AAA")]
        raise RuntimeError("companies house 503")
    orig = ch.get_psc
    ch.get_psc = _boom
    try:
        r = asyncio.run(ch.walk_psc_ownership("ROOT"))
    finally:
        ch.get_psc = orig
    assert r["complete"] is False
    assert any("could not read PSCs" in g for g in r["gaps"])


def test_an_empty_psc_register_is_not_evidence_of_no_owner():
    """An empty register is also what an exempt or non-compliant company looks like.
    Presenting it as 'ownership fully traced' is the false clean this avoids."""
    r = _drive({"ROOT": []})
    assert any("not evidence of no owner" in g for g in r["gaps"])


# ── it must not be confused with the directorship walker ───────────────────

def test_only_ownership_kinds_become_edges_or_ultimates():
    """A director is not an owner (R-F3539). The walker consults PSCs only, so a
    directorship can never enter this chain — asserted so a future 'enrichment' cannot
    reintroduce the category error."""
    import inspect
    src = function_source(ch, "walk_psc_ownership")
    assert "get_psc(" in src
    for forbidden in ("get_officers", "officers", "appointments"):
        assert forbidden not in src, (
            f"the ownership walk reads {forbidden!r} — that is the directorship walk")


def test_ceased_pscs_are_ignored():
    past = _corp("Former Owner Ltd", "999")
    past["is_current"] = False
    r = _drive({"ROOT": [past]})
    assert r["edges"] == [], "a ceased PSC is not a current owner"


def test_the_walk_is_bounded():
    """An unbounded walk on a shared 1-CPU box is an outage waiting to happen."""
    chain = {"ROOT": [_corp("n0", "0")]}
    for i in range(60):
        chain[str(i)] = [_corp(f"n{i+1}", str(i + 1))]
    r = _drive(chain)
    assert len(r["nodes"]) <= 25
    assert r["complete"] is False
    assert any("max_hops" in g or "max_nodes" in g for g in r["gaps"])


# ── the wiring: a walker nothing calls is the defect it replaces ────────────

def test_the_walker_HAS_a_caller():
    """R-F3510's evidence shadow shipped with no caller and R-F3504's PSC block was
    unreachable. A capability that is never invoked is indistinguishable from one that
    does not exist."""
    import pathlib
    from aria_service.intel import dd_orchestrator as o
    src = pathlib.Path(o.__file__).read_text(encoding="utf-8", errors="replace")
    calls = [ln for ln in src.splitlines()
             if "walk_psc_ownership(" in ln and not ln.lstrip().startswith("#")]
    assert calls, "walk_psc_ownership is dormant — nothing calls it"


def test_the_call_site_is_at_the_converged_identity_point():
    """R-F3515: the PSC-exemption block was first written inside ONE jurisdiction branch
    and could never fire. This must sit where both branches have converged — pinned by
    its adjacency to the exemption call, which is already at that point."""
    import pathlib
    from aria_service.intel import dd_orchestrator as o
    src = pathlib.Path(o.__file__).read_text(encoding="utf-8", errors="replace")
    exemption = src.index("await _explain_empty_psc_register(report)")
    walk = src.index("walk_psc_ownership(_own_reg)")
    assert 0 < walk - exemption < 2600, (
        "the ownership walk has drifted from the converged identity point")


def test_the_result_is_persisted_on_the_report():
    from aria_service.intel.dd_schema import ARKDDReport
    r = ARKDDReport()
    assert hasattr(r.identity, "psc_ownership_chain")
    assert r.identity.psc_ownership_chain == {}


def test_gaps_reach_the_reader_not_just_the_blob():
    """A gap recorded only inside the chain dict is invisible. It has to land in
    data_gaps, which is what the report actually renders."""
    import pathlib
    from aria_service.intel import dd_orchestrator as o
    src = pathlib.Path(o.__file__).read_text(encoding="utf-8", errors="replace")
    i = src.index("walk_psc_ownership(_own_reg)")
    block = src[i: i + 2200]
    assert "data_gaps.append" in block, "ownership gaps never reach the reader"

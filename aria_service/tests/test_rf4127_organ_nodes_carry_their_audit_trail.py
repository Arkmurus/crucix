"""R-F4127 (C-162) — organ nodes hardcoded an empty `r_numbers`, so hovering an
organ could never show which R-numbers built it.

Operator, 2026-08-17: *"some of the items on the living organs do not have
sensors and neither able to hoover on top and see what r numbers are related to
it."*

Measured live on the overview graph::

    nodes: 36  {service: 3, organ: 33}
      organ    n= 33  sensor=33  r_numbers=  0
      service  n=  3  sensor= 3  r_numbers=  0

So it is not "some" — it is **every node at the level the operator actually
looks at**. The sensor half of the complaint is a different story and is NOT a
defect: all 36 overview nodes DO carry a sensor. The grey ones are tier-2 module
nodes reached by drilling in, where no breaker/agent/limb sensor exists to read —
grey there is honest.

The cause is three hardcoded literals. `ecosystem_map` populates `r_numbers` for
MODULE nodes (`rnums_by_mod.get(mid, [])`, `node_rnums.get(nid, [])`) and passes
`"r_numbers": []` for every service, organ and Node-tier organ. The frontend was
never at fault — `_card()` renders `· audit R-…` whenever the list is non-empty,
so it was faithfully rendering nothing.

An organ is an aggregate of its modules, so its audit trail is the union of
theirs — DERIVED from `organ_of` + `rnums_by_mod`, never a fourth hand-maintained
table that would drift the moment a module moved organ.

**The cap is disclosed, not silent.** A busy organ can carry hundreds of
R-numbers; a hover showing 400 is unusable and the payload would balloon. So the
list is capped and `r_numbers_total` reports the true count — §27d: "No silent
caps: if a workflow bounds coverage, log what was dropped — silent truncation
reads as 'covered everything' when it didn't."
"""
from __future__ import annotations

import pytest

from aria_service.intel import ecosystem_map as em


@pytest.fixture(scope="module")
def graph():
    # §3b — build_structure is ASYNC. Calling it bare returns a coroutine and
    # every assertion below would fail on a TypeError rather than on the thing
    # it is testing.
    import asyncio
    return asyncio.run(em.build_structure())


def _by_type(graph, t):
    return [n for n in graph["nodes"] if n.get("type") == t]


def test_organ_nodes_carry_r_numbers(graph):
    organs = _by_type(graph, "organ")
    assert organs, "no organ nodes in the graph"
    with_r = [o for o in organs if o.get("r_numbers")]
    assert with_r, (
        "every organ still has an empty r_numbers — hovering an organ on the "
        "ecosystem map can never show its audit trail")
    # Not a token gesture: most organs own modules, so most should resolve.
    owning = [o for o in organs if (o.get("module_count") or 0) > 0]
    resolved = [o for o in owning if o.get("r_numbers")]
    assert len(resolved) >= max(1, len(owning) // 2), (
        f"only {len(resolved)}/{len(owning)} module-owning organs resolved an "
        "audit trail — the derivation is not reaching most of them")


def test_an_organ_trail_is_the_union_of_its_modules(graph):
    """DERIVED, not a fourth table that drifts when a module moves organ."""
    mods = _by_type(graph, "module")
    organs = {o["id"]: o for o in _by_type(graph, "organ")}
    checked = 0
    for oid, organ in organs.items():
        child = {r for m in mods if m.get("parent") == oid
                 for r in (m.get("r_numbers") or [])}
        if not child:
            continue
        checked += 1
        shown = set(organ.get("r_numbers") or [])
        assert shown <= child, (
            f"{oid} shows R-numbers none of its modules carry: {sorted(shown - child)}")
        assert organ.get("r_numbers_total") == len(child), (
            f"{oid} reports total {organ.get('r_numbers_total')} but its modules "
            f"carry {len(child)}")
    assert checked, "no organ had module R-numbers to union — test proved nothing"


def test_the_cap_is_disclosed_never_silent(graph):
    """§27d — a truncated list that does not say so reads as complete."""
    for o in _by_type(graph, "organ"):
        shown, total = o.get("r_numbers") or [], o.get("r_numbers_total")
        assert total is not None, f"{o['id']} caps without reporting a total"
        assert len(shown) <= em._ORGAN_RNUM_CAP
        assert total >= len(shown), (o["id"], total, len(shown))


def test_module_nodes_are_untouched(graph):
    """The module trail already worked; this change must not perturb it."""
    mods = [m for m in _by_type(graph, "module") if m.get("r_numbers")]
    assert mods, "module nodes lost their r_numbers"
    for m in mods[:20]:
        assert all(str(r).startswith("R-F") for r in m["r_numbers"]), m["r_numbers"]


def test_the_frontend_renders_what_the_backend_now_sends():
    """The page was never at fault — pin that it still reads the field, so a
    future edit cannot re-break the hover from the other side."""
    from aria_service.tests._source_probe import repo_path

    page = repo_path("public/aria-brain.html").read_text(encoding="utf-8")
    assert "n.r_numbers||[]" in page, "the tooltip no longer reads r_numbers"
    assert "audit ${rn.slice" in page, "the tooltip no longer renders the audit trail"

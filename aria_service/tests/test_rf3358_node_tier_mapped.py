"""R-F3358 — the ecosystem map can finally see all three of its services.

R-F3352 stopped the map OVERCLAIMING: it declared that `scan_modules()` globs
`aria_service/**/*.py` only, that every organ was hardcoded to aria-intel, and
that aria-web + aria-wa were therefore T0 cards with ZERO modules beneath them.
That was the honest stopgap the operator chose ("narrow the claim now, extend
later"). This is the extension — the root fix R-F3352 deferred.

127 modules were invisible: 122 on aria-web (server.mjs ~8.5k lines, 118
lib/*.mjs, 3 public/js) and 5 on aria-wa. That is the tier carrying auth,
billing, Stripe, the UI and the WhatsApp limb, and CLAUDE.md §21b is explicit
that observability is not Python-only.

★THE DESIGN POINT: for the Node tier the DIRECTORY IS THE ORGAN. Python needed
keyword inference because `intel/` is a flat bag of ~400 modules; `lib/auth/`,
`lib/billing/` and `lib/telegram/` are already the subsystem boundary. So Node
assignment is PATH-PREFIX based with no inference at all, which makes it
structurally immune to the substring-accident class R-F3349 had to remove from
the Python side (a YAML linter filed under anti-money-laundering via "aml").

The load-bearing guard here is not the Node count — it is that adding a second
tier does NOT perturb the first. Every Python module must keep the exact organ
R-F3349/R-F3350 gave it, and the Python orphan count must stay 4.
"""
from __future__ import annotations

import asyncio

from aria_service.intel import ecosystem_map as em


def _py_ids() -> list[str]:
    return [em._module_id(p) for p in em.scan_modules()]


# ── The gap R-F3352 declared is closed ──────────────────────────────────────
def test_rf3358_all_three_services_have_modules():
    cov = asyncio.run(em.get_coverage())
    svc = cov["services"]
    assert set(svc["mapped"]) == set(svc["declared"]), (
        f"a declared service still has no organ: {sorted(set(svc['declared']) - set(svc['mapped']))}"
    )
    assert svc["unmapped"] == [], "the R-F3352 unmapped-tier warning must retire itself"


def test_rf3358_the_node_tiers_are_really_scanned():
    nodes = em.scan_node_modules()
    web = [n for n in nodes if n[0] == "aria-web"]
    wa = [n for n in nodes if n[0] == "aria-wa"]
    assert len(web) >= 100, f"aria-web should contribute ~122 modules, got {len(web)}"
    assert len(wa) >= 4, f"aria-wa should contribute ~5 modules, got {len(wa)}"
    rels = {r for _svc, r in nodes}
    assert "server.mjs" in rels, "server.mjs (~8.5k lines) must be on the map"
    assert any(r.startswith("lib/auth/") for r in rels), "the auth tier must be mapped"
    assert any(r.startswith("services/wa-listener/") for r in rels), "the WhatsApp limb must be mapped"
    # Vendored/installed code and tests are NOT ours to map.
    assert not any("node_modules" in r or "/vendor/" in r for r in rels)
    assert not any(r.split("/")[-1].startswith("test_") or ".test." in r for r in rels), \
        "test files must not inflate the module denominator"


# ── THE REGRESSION GUARD: the Python tier must not move at all ──────────────
def test_rf3358_python_assignments_are_untouched():
    """Adding a tier must not perturb the one R-F3349/R-F3350 just settled."""
    ids = _py_ids()
    assert len(ids) >= 570, "the Python scan itself changed size"
    orphans = [m for m in ids if em._assign_organ(m) is None]
    assert len(orphans) == 4, f"Python orphan count moved from 4 to {len(orphans)}: {orphans}"
    anchors = {
        "aria_service.intel.run_quarantine": "phase",          # R-F3349
        "aria_service.intel.reasoning_router": "llm",          # R-F3349
        "aria_service.intel.multi_lang.yaml_reviewer": "autonomous",  # R-F3349 token rule
        "aria_service.vetting.store": "vetting",               # R-F3350
        "aria_service.main": "routes",                         # R-F3350
        "aria_service.intel.sources.ofac_sdn": "sanctions",
        "aria_service.intel.rag_store": "brain",
    }
    for mid, organ in anchors.items():
        assert em._assign_organ(mid) == organ, f"REGRESSION: {mid} should be '{organ}'"


def test_rf3358_node_ids_cannot_collide_with_python_ids():
    """A Node id must be unmistakable, so no keyword rule or health sensor can
    ever cross the tiers by accident."""
    full = asyncio.run(em.build_structure())
    mods = [n for n in full["nodes"] if n["type"] == "module"]
    py = [n for n in mods if not n.get("tier_service") or n["tier_service"] == "aria-intel"]
    node = [n for n in mods if n.get("tier_service") in ("aria-web", "aria-wa")]
    assert node, "no Node modules on the graph"
    assert all(n["module_id"].startswith(("web:", "wa:")) for n in node)
    assert not any(n["module_id"].startswith(("web:", "wa:")) for n in py)
    assert len({n["id"] for n in mods}) == len(mods), "duplicate module node ids"


# ── Assignment is path-based, and its gaps are surfaced not hidden ──────────
def test_rf3358_node_assignment_is_path_based_not_keyword_inference():
    assert em._assign_node_organ("aria-web", "lib/auth/roles.mjs") == "web_auth"
    assert em._assign_node_organ("aria-web", "lib/billing/tiers.mjs") == "web_billing"
    assert em._assign_node_organ("aria-web", "server.mjs") == "web_server"
    assert em._assign_node_organ("aria-wa", "services/wa-listener/send-retry.mjs") == "wa_listener"
    # A path nobody declared is an ORPHAN, exactly as on the Python side — the
    # map must keep proving its own gaps rather than inventing a home.
    assert em._assign_node_organ("aria-web", "lib/__brand_new_area__/x.mjs") is None


def test_rf3358_node_imports_resolve_and_externals_are_counted_not_hidden():
    full = asyncio.run(em.build_structure())
    edges = [e for e in full["edges"] if e["type"] == "import"]
    node_edges = [e for e in edges if e["source"].startswith(("mod:web:", "mod:wa:"))]
    assert node_edges, "no intra-Node import edges resolved"
    ids = {n["id"] for n in full["nodes"]}
    for e in node_edges:
        assert e["source"] in ids and e["target"] in ids, "dangling Node import edge"
    # npm / node: builtins are external — reported, never silently dropped.
    cov = asyncio.run(em.get_coverage())
    assert cov["import_edges"]["external_node_specifiers"] > 0

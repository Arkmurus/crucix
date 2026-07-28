"""R-F3350 — a shipped product had no organ, and the map called it a defect.

The ARIA Ecosystem card's "⚠ Unassigned" bucket held 49 modules, and 18 of them
were the ENTIRE `vetting` package — the BS 7858 screening product, built end to
end, with its own store, packs, decisions, retention and timeline. There was no
Vetting organ at all, so a whole product line rendered as a completeness DEFECT
rather than as a limb.

`aria_service.main` was orphaned too — the FastAPI entrypoint, the lifespan, and
the handler for `GET /phase/gates` that CLAUDE.md §1 tells every session to probe.

The rest of the rescue is R-F2986's method: SPECIFIC leaf keywords, never broad
subpackage prefixes, each justified by the module's OWN docstring rather than by
its name — `intel.wire` says "failure-wire decorator for automatic brain wiring"
(infra), `intel.security` says "protects against malicious content, SSRF, and
injection" (guardian), `intent.interpret` calls itself "Guardian Layer 3".

NOT a clamp (this is the line R-F2986 drew and this fix keeps): orphans must stay
> 0 and stay MEANINGFUL. Namespace package roots that span organs
(`aria_service`, `aria_service.intel`) stay honestly unassigned, and the stray
test file living in the production tree (`intel.auto.test_rf1191_new`) stays
visible instead of being excluded to flatter the denominator.
"""
from __future__ import annotations

from aria_service.intel import ecosystem_map as em


def test_rf3350_vetting_is_an_organ_not_a_defect():
    """The whole vetting package lands in its own organ."""
    organ_ids = {o[0] for o in em._ORGANS}
    assert "vetting" in organ_ids, "no Vetting organ — a shipped product is still filed as unassigned"

    vetting_mods = [
        em._module_id(p) for p in em.scan_modules()
        if em._module_id(p).startswith("aria_service.vetting")
    ]
    assert len(vetting_mods) >= 15, f"expected the full vetting package, found {len(vetting_mods)}"
    misfiled = {m: em._assign_organ(m) for m in vetting_mods if em._assign_organ(m) != "vetting"}
    assert misfiled == {}, f"vetting modules not in the vetting organ: {misfiled}"


def test_rf3350_the_application_entrypoint_has_an_organ():
    """aria_service.main defines the FastAPI app and serves GET /phase/gates
    (CLAUDE.md §1) — it cannot be an unassigned module."""
    assert em._assign_organ("aria_service.main") == "routes"


def test_rf3350_rescued_orphans_land_where_their_docstring_says():
    """Each assignment is justified by the module's own stated purpose."""
    expect = {
        "aria_service.intel.wire": "infra",                     # "failure-wire decorator for automatic brain wiring"
        "aria_service.intel.security": "guardian",              # "protects against malicious content, SSRF, and injection"
        "aria_service.intel.self_protection": "guardian",       # "output-quality and action-safety guardrails"
        "aria_service.intel.self_infra_detector": "infra",
        "aria_service.intel.search_doctrine": "search",         # "discipline layer over researcher.web_search"
        "aria_service.intel.research_tasks": "learning",        # "background long-running research operations"
        "aria_service.intel.tech_classifier": "osint",          # "extract structured defence/security item data"
        "aria_service.intel.fca_register": "registries",
        "aria_service.intel.virtual_office_registry": "dd",     # virtual-office = shell-company red flag
        "aria_service.intel.r_number_registry": "infra",
        "aria_service.intel.regulated_commodity_pack": "compliance",
        "aria_service.intel.mou_clause_gate_analyser": "legal",
        "aria_service.intent.interpret": "guardian",            # "Guardian Layer 3"
        "aria_service.writers": "documents",                    # "document production capability"
        "aria_service.personas": "guardian",                    # "overlays of the base constitution"
        "aria_service.integrations": "infra",                   # "external-system integrations"
        "aria_service.utils.git_utils": "infra",
        "aria_service.static.aria_client.aria_tui": "cli",
    }
    wrong = {m: (o, em._assign_organ(m)) for m, o in expect.items() if em._assign_organ(m) != o}
    assert wrong == {}, f"rescued orphans in the wrong organ (expected, got): {wrong}"


def test_rf3350_orphan_alert_stays_meaningful_never_clamped():
    """The rescue must not drive orphans to zero — a zero-orphan map would be
    hiding, not honest. Genuinely cross-cutting package roots stay unassigned."""
    ids = [em._module_id(p) for p in em.scan_modules()]
    orphans = {m for m in ids if em._assign_organ(m) is None}
    assert orphans, "orphan alert clamped to zero — the map would stop proving its own gaps"
    # These span organs by nature and must NOT be forced into one.
    assert "aria_service" in orphans
    assert "aria_service.intel" in orphans
    # A test file living in the production tree stays VISIBLE rather than excluded.
    assert "aria_service.intel.auto.test_rf1191_new" in orphans


def test_rf3350_orphans_materially_reduced():
    """49 orphans before; the vetting package alone is 18 of them."""
    ids = [em._module_id(p) for p in em.scan_modules()]
    orphans = sum(1 for m in ids if em._assign_organ(m) is None)
    assert orphans <= 15, f"expected the orphan bucket to drop well below 49, got {orphans}"
    pct = 100.0 * (len(ids) - orphans) / len(ids)
    assert pct >= 97.0, f"organ coverage only {pct:.1f}%"


def test_rf3350_rescue_stole_nothing_from_an_existing_organ():
    """PURELY ADDITIVE (R-F2986 guardrail 1): a new keyword may only move an
    ORPHAN into an organ, never re-bucket an already-assigned module. R-F3349's
    audit is the enforcer — any keyword that now loses its module to another
    organ must carry a declared decision."""
    audit = em.audit_organ_table()
    undeclared = [s for s in audit["shadowed_keywords"]
                  if s["intended_organ"] != s["actual_organ"] and not s["declared"]]
    assert undeclared == [], f"the rescue shadowed a curated keyword: {undeclared}"
    assert audit["stale_overrides"] == []
    # Measured against HEAD~ : exactly THREE already-assigned modules move, and all
    # three are the vetting product consolidating into its own organ. Everything
    # else that changed was an orphan gaining an organ (45 of them).
    for mid in ("aria_service.intel.vetting_standard_knowledge",
                "aria_service.vetting.documents",
                "aria_service.vetting.legal_basis"):
        assert em._assign_organ(mid) == "vetting", f"{mid} should consolidate into the vetting organ"
    # ...and the new keywords must NOT drag along the modules that were already right.
    preserved = {
        "aria_service.routes.vetting": "routes",           # routes.* stays in API & Routes
        "aria_service.routes.vetting_portal": "routes",
        "aria_service.writers._resilient_llm": "llm",      # an LLM wrapper that lives in writers/
        "aria_service.writers.procurement_paper_writer": "commercial",
    }
    for mid, organ in preserved.items():
        assert em._assign_organ(mid) == organ, f"the rescue re-bucketed {mid}, which was already correct"

    # Anchors that predate this change must be untouched.
    stable = {
        "aria_service.intel.ofac_sdn": "sanctions",
        "aria_service.intel.company_investigator": "dd",
        "aria_service.intel.web_search": "search",
        "aria_service.intel.rag_store": "brain",
        "aria_service.intel.student": "learning",
        "aria_service.intel.run_quarantine": "phase",   # R-F3349
        "aria_service.intel.circuit_breaker": "infra",
    }
    for mid, organ in stable.items():
        assert em._assign_organ(mid) == organ, f"REBUCKET REGRESSION: {mid} should be '{organ}'"

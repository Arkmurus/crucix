"""R-F3349 — ecosystem organ assignment: substring accidents + shadowed curation.

The live ARIA Ecosystem card files every module into an organ via `_assign_organ`,
which matched organ keywords by NAKED SUBSTRING in declared organ order, first hit
wins. That produced two distinct defect classes, both measured on the live tree
(578 modules) on 2026-07-28:

  1. SUBSTRING ACCIDENTS — a keyword matching inside an unrelated word:
       intel.multi_lang.yaml_reviewer -> compliance   via "aml" inside "y-AML-_reviewer"
       intel.precall_brief            -> brain        via "recall" inside "p-RECALL-_brief"
       metacognitive.identity         -> osint        via "entity" inside "id-ENTITY"
     A YAML linter filed under anti-money-laundering. This is EXACTLY the class
     R-F3047 fixed for circuit-breaker names ("semantic_scholar" -> Brain painted a
     56-module organ RED off an external paper API). That fix never reached module
     paths, which is the other caller of the same keyword table.

  2. SHADOWED CURATION — first-match-wins let an EARLIER organ's generic keyword
     beat a LATER organ's more specific one, so a deliberately-curated keyword
     could never fire. 13 measured; the ones where the resulting organ is wrong:
       intel.run_quarantine     -> guardian, though phase declares "run_quarantine"
       intel.reasoning_router   -> brain,    though llm declares "reasoning_router"
       intel.content_scanner    -> documents,though guardian declares "content_scanner"
       intel.engine_wiring      -> autonomous,though infra declares "engine_wiring"
       intel.deception_detection-> osint,    though guardian declares "deception_detection"
       intel.autonomy_surface   -> delivery, though routes declares "autonomy_surface"
       intel.scraper.playwright_engine -> autonomous via "engine", beating "scraper"
     run_quarantine matters beyond tidiness: CLAUDE.md §1 names it the Phase A
     gate #4 closer surface, and it was not in the Phase & Scoring organ.

Why not "longest keyword wins": measured, it fixes these but creates ~8 NEW
regressions — intel.sources.ofac_sdn -> intel_sources (gutting Sanctions),
uk_ofsi_ingest -> cli, sanctions_divergence -> learning. Keyword length is not a
specificity metric. The fix is therefore the R-F3047 remedy applied to module
paths: token-boundary matching kills the accident class STRUCTURALLY, and the
genuine conflicts are DECLARED in an explicit override table, because an organ is
a curation decision, not a string coincidence.

FAILS BEFORE R-F3349 (measured): every assertion in the first two tests.
"""
from __future__ import annotations

from aria_service.intel import ecosystem_map as em


# ── Class 1: substring accidents ────────────────────────────────────────────
def test_rf3349_keyword_never_matches_inside_an_unrelated_word():
    """A keyword must match at a token boundary. 'aml' is not a token of
    'yaml_reviewer'; 'recall' is not a token of 'precall'; 'entity' is not a
    token of 'identity'."""
    accidents = {
        "aria_service.intel.multi_lang.yaml_reviewer": "compliance",
        "aria_service.intel.precall_brief": "brain",
        "aria_service.metacognitive.identity": "osint",
    }
    for mid, wrong in accidents.items():
        got = em._assign_organ(mid)
        assert got != wrong, (
            f"{mid} still lands in '{wrong}' — a keyword matched inside an "
            f"unrelated word (the R-F3047 substring class, on module paths)"
        )


def test_rf3349_stem_keywords_still_match_their_words():
    """Token-boundary matching must NOT break the deliberate STEM keywords
    ('sanction' -> sanctions, 'investigat' -> investigator, 'crawl' -> crawler).
    Those match a token PREFIX, which stays legal; only mid-token matches die."""
    stems = {
        "aria_service.intel.country_sanctions": "sanctions",
        "aria_service.intel.company_investigator": "dd",
        "aria_service.crawler.runner": "search",
        "aria_service.intel.corroboration": "brain",
    }
    for mid, organ in stems.items():
        assert em._assign_organ(mid) == organ, f"stem keyword regressed for {mid}"


# ── Class 2: shadowed curation ──────────────────────────────────────────────
def test_rf3349_curated_intent_is_honoured_not_shadowed():
    """Each of these had an EXPLICIT keyword in the intended organ that could
    never fire, because an earlier organ's generic keyword won first-match."""
    intended = {
        "aria_service.intel.run_quarantine": "phase",        # CLAUDE.md §1 gate #4 closer
        "aria_service.intel.reasoning_router": "llm",
        "aria_service.intel.content_scanner": "guardian",
        "aria_service.intel.engine_wiring": "infra",
        "aria_service.intel.deception_detection": "guardian",
        "aria_service.intel.autonomy_surface": "routes",
        "aria_service.intel.scraper.playwright_engine": "search",
    }
    for mid, organ in intended.items():
        got = em._assign_organ(mid)
        assert got == organ, f"{mid} → expected '{organ}', got '{got}' (curated keyword still shadowed)"


def test_rf3349_accident_victims_are_rehomed_not_orphaned():
    """Killing the substring match must not silently dump the module into the
    orphan bucket — the two accident victims get a DECLARED organ."""
    assert em._assign_organ("aria_service.intel.precall_brief") == "commercial"
    assert em._assign_organ("aria_service.metacognitive.identity") == "learning"


# ── The table declares its own gaps (same discipline as orphans) ────────────
def test_rf3349_organ_table_audit_is_self_consistent():
    """audit_organ_table() reports dead + shadowed keywords so the curated layer
    proves its own drift instead of hiding it — the map already does this for
    orphans, unresolved imports and the partial call graph."""
    audit = em.audit_organ_table()
    assert "dead_keywords" in audit and "shadowed_keywords" in audit
    # Every override must point at a REAL organ and a module that still exists.
    organ_ids = {o[0] for o in em._ORGANS}
    live = {em._module_id(p) for p in em.scan_modules()}
    for mid, organ in em._ORGAN_OVERRIDES.items():
        assert organ in organ_ids, f"override {mid} points at unknown organ '{organ}'"
        assert mid in live, f"override {mid} names a module that no longer exists — stale curation"
    assert audit["stale_overrides"] == [], f"stale overrides: {audit['stale_overrides']}"


def test_rf3349_no_shadowed_keyword_changes_an_organ_silently():
    """A keyword may be legitimately redundant (another keyword in the SAME organ
    already claims the module) or aspirational (matches nothing yet). What must
    never recur is a keyword whose intended organ LOSES the module to a different
    organ with no declared decision — that is the silent-misfile class."""
    unresolved = [
        s for s in em.audit_organ_table()["shadowed_keywords"]
        if s["intended_organ"] != s["actual_organ"] and not s["declared"]
    ]
    assert unresolved == [], (
        "curated keywords silently overruled with no override decision: "
        f"{[(s['keyword'], s['intended_organ'], s['actual_organ']) for s in unresolved]}"
    )


def test_rf3349_audit_is_memoised_and_does_not_rescan_the_filesystem():
    """get_coverage() sits on the /api/aria/health path that R-F3062 already had
    to rescue from a blown budget (a cold rebuild dropped the panel and rendered
    'ECOSYSTEM: UNKNOWN'). The audit is ~100ms of SYNCHRONOUS work over 578
    modules, so it must be fed the ids build_structure already resolved and
    memoised against them — never a second filesystem walk per health probe."""
    ids = [em._module_id(p) for p in em.scan_modules()]
    first = em.audit_organ_table(ids)
    calls: list[int] = []
    real_scan = em.scan_modules
    try:
        em.scan_modules = lambda: (calls.append(1), real_scan())[1]  # type: ignore[assignment]
        again = em.audit_organ_table(ids)
    finally:
        em.scan_modules = real_scan  # type: ignore[assignment]
    assert again is first, "audit_organ_table must return the memoised object for an unchanged module set"
    assert calls == [], "audit_organ_table re-walked the filesystem on a cached call"


# ── Chain check: _assign_organ's OTHER caller (health overlay) ──────────────
def test_rf3349_health_overlay_name_resolution_unbroken():
    """_assign_organ is also called by _organ_for_name (ecosystem_map.py:619) for
    AGENT ids and GAP sources. R-F3047's tests depend on it: a stale student_loop
    must reach organ:learning, a HIGH gap in brain_hook must reach organ:brain.
    Token-boundary matching must not break those."""
    assert em._assign_organ("student_loop") == "learning"
    assert em._assign_organ("brain_hook") == "brain"
    # R-F3047's own invariant: an UNKNOWN external backend paints nothing.
    assert em._assign_organ("semantic_scholar") == "brain"  # module-path rule, not the backend rule


# ── Anti-regression: R-F2986's stable anchors must survive ──────────────────
def test_rf3349_rf2986_stable_anchors_unmoved():
    """R-F2986 froze anchors specifically to catch re-bucketing. R-F3349 changes
    the matcher, so it must re-prove them rather than assume them."""
    stable = {
        "aria_service.intel.ofac_sdn": "sanctions",
        "aria_service.intel.sources.ofac_sdn": "sanctions",
        "aria_service.intel.uk_ofsi_ingest": "sanctions",
        "aria_service.intel.sanctions_divergence": "sanctions",
        "aria_service.intel.sources.sec_edgar": "registries",
        "aria_service.intel.sources.gleif": "registries",
        "aria_service.intel.web_search": "search",
        "aria_service.intel.circuit_breaker": "infra",
        "aria_service.intel.rag_store": "brain",
        "aria_service.autonomous.gap_detector": "autonomous",
        "aria_service.intel.student": "learning",
        "aria_service.llm.tier_router": "llm",
        "aria_service.intel.deal_predictor": "commercial",
        "aria_service.intel.dd_trigger_pipeline": "dd",
    }
    for mid, organ in stable.items():
        assert em._assign_organ(mid) == organ, f"REBUCKET REGRESSION: {mid} should be '{organ}'"

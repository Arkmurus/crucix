"""
ARIA Brain Hook — Central Learning Relay for All Intel Modules.

Every intel module that produces analysis output calls brain_hook.absorb()
after completing its work. This fans out to all five learning tiers:

  1. student.update_mastery()  — EWMA competence per topic
  2. knowledge.store_fact()    — persistent verified facts
  3. neural_memory.learn_from_text() — associative concept graph
  4. capability_gaps.record_gap() — if the module hit a limitation

One function, one call, non-fatal. Modules never need to know about
the internal learning architecture — they just feed the brain.

Added 2026-04-13 to close the gap where 19/24 intel modules were
producing intelligence that vanished without feeding learning.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

logger = logging.getLogger("aria.brain_hook")

# ── Feature gate ────────────────────────────────────────────────────────────
import os
BRAIN_HOOK_ENABLED = os.environ.get("ARIA_BRAIN_HOOK_ENABLED", "1") == "1"

# Topic mapping: module name → student mastery topics it touches
_MODULE_TOPICS: dict[str, list[str]] = {
    "dd_orchestrator":      ["compliance", "osint", "finance", "relationships"],
    "compliance_workflow":  ["compliance", "legal"],
    "contract_intelligence": ["legal", "procurement"],
    "tender_monitor":       ["procurement", "market_intel"],
    "link_investigator":    ["osint", "relationships"],
    "financial_dd":         ["finance", "compliance"],
    "network_walker":       ["relationships", "compliance", "osint"],
    "entity_graph":         ["relationships", "osint"],
    "deep_researcher":      ["osint", "market_intel", "geopolitics"],
    "brave_answers":        ["osint", "market_intel", "general"],
    "person_resolver":      ["relationships", "osint"],
    "competitors":          ["competitor_intel", "market_intel"],
    "gtm_strategy":         ["market_intel", "procurement"],
    "risk_indices":         ["compliance", "geopolitics"],
    "global_export_control": ["compliance", "legal", "technical"],
    "dual_use_classifier":  ["compliance", "legal", "technical"],
    "euc_library":          ["compliance", "legal"],
    "audit_log":            ["compliance", "legal"],
    "compliance_file":      ["compliance", "legal"],
    "symbolic_reasoner":    ["general"],
    "source_verifier":      ["osint"],
    "sanctions":            ["compliance", "legal"],
    "conflict_tracker":     ["geopolitics", "relationships"],
    "international_law":    ["legal", "compliance"],
    # Node.js seenode modules (via POST /api/aria/brain/absorb — registered
    # here so their signals are correctly topic-mapped when they arrive)
    "registry_adapter":     ["compliance", "legal"],
    "opportunity_detector": ["market_intel", "competitor_intel"],
    "signal_generator":     ["finance", "compliance", "market_intel"],
    "knowledge_ingestor":   ["general", "compliance", "legal"],
    # Email reader (seenode → POST /api/aria/brain/absorb) — every inbound
    # email lands as a discrete signal so the brain stats endpoint can
    # show email volume + linkedin/tender/compliance breakdown.
    "email_reader":         ["general", "relationships", "competitor_intel"],
    # Core Self-Development Loop (Clauses 17/18/19) — shipped 2026-04-15
    "verified_intel":       ["compliance", "osint", "legal"],
    "web_atlas":            ["osint", "market_intel"],
    "source_validator":     ["osint", "compliance"],
    "source_scout":         ["osint", "market_intel"],
    "search_doctrine":      ["osint", "general"],
    "core_develop":         ["general"],
    "ecosystem_reassess":   ["general"],
    "golden_autogen":       ["osint", "general"],
    "adversarial_challenge": ["compliance", "general", "osint"],
    "narrative_monitor": ["osint", "geopolitics", "competitor_intel"],
    # Priority 1 (2026-04-17) — long-horizon causal chain correlator
    "chain_correlator":     ["geopolitics", "procurement", "relationships", "market_intel"],
    # Priorities 2-4 (2026-04-17) — temporal + competitor + OEM graph
    "procurement_calendar": ["procurement", "geopolitics", "market_intel"],
    "competitor_tracker":   ["competitor_intel", "market_intel"],
    "oem_contact_graph":    ["relationships", "market_intel", "competitor_intel"],
    # Tier 1 regional knowledge modules + equipment specs (2026-04-17)
    "knowledge_gulf":              ["market_intel", "procurement", "compliance"],
    "knowledge_turkey_standalone": ["market_intel", "procurement", "compliance"],
    "knowledge_west_africa":       ["market_intel", "procurement", "compliance"],
    "knowledge_latam_non_lusophone": ["market_intel", "procurement", "compliance"],
    "equipment_specs":      ["technical", "market_intel", "procurement"],
    "sipri_ingest":         ["market_intel", "procurement"],
    # Writer package (2026-04-17) — structured document production.
    "writer_orchestrator":  ["general", "legal", "compliance", "procurement"],
    # Individual writers (2026-04-18 night) — each absorb fires on every
    # finished document; these are gold-tier training pairs (full IC
    # assessments, RFP/RFQ papers, UKBA/FCPA opinions, NATO tech specs,
    # Portuguese legal docs).
    "assessment_writer":         ["general", "compliance", "geopolitics", "osint"],
    "procurement_paper_writer":  ["procurement", "legal", "compliance"],
    "anti_corruption_law":       ["compliance", "legal", "finance"],
    "tech_spec_writer":          ["technical", "procurement"],
    "portuguese_legal_writer":   ["legal", "compliance", "general"],
    # Output guard for Clause 13 — propaganda + uncited current events
    "propaganda_guard":          ["compliance", "general"],
    # NAK / SERBAN / F3 learnings (2026-04-17) — six new capabilities closing
    # the gaps observed on the live KNDS / FK-3000 engagement.
    "virtual_office_registry":    ["compliance", "osint", "finance"],
    "sanctions_propagation":      ["compliance", "legal", "procurement"],
    "cited_artifact_verifier":    ["compliance", "legal", "osint"],
    "protective_reply_drafter":   ["compliance", "legal", "general"],
    # Tier 2 regional knowledge (2026-04-17 PM) — heat-map expansion
    "knowledge_north_africa":      ["market_intel", "procurement", "compliance"],
    "knowledge_south_se_asia":     ["market_intel", "procurement", "compliance"],
    "knowledge_central_africa":    ["market_intel", "procurement", "compliance"],
    "knowledge_balkans":           ["market_intel", "procurement", "compliance"],
    "regional_bright_lines":       ["compliance", "legal", "procurement"],
    # Heat-map expansion follow-up modules (2026-04-17 late PM)
    "gulf_oem_structure":          ["market_intel", "procurement", "relationships"],
    "vision_2030_tracker":         ["market_intel", "procurement", "compliance"],
    "baykar_export_pipeline":      ["market_intel", "competitor_intel", "procurement"],
    "political_risk_index":        ["geopolitics", "compliance", "market_intel"],
    "cross_regional_correlator":   ["geopolitics", "procurement", "relationships", "market_intel"],
    # Autonomy Surface (2026-04-17 late PM) — dashboard + briefing aggregator
    "autonomy_surface":            ["general", "compliance"],
    # F3 DD debug follow-up (2026-04-17 late PM)
    "domain_ownership_verifier":   ["compliance", "osint", "legal"],
    # F3 cascade remediation (2026-04-17 21:45-21:55)
    "run_quarantine":              ["compliance", "general"],
    "sanctions_claim_guard":       ["compliance", "legal"],
    # Continuous learning loop (2026-04-18)
    "training_export":             ["general"],
    "knowledge_spider":            ["osint", "general", "market_intel"],
    "metacognitive_journal":       ["general", "compliance"],
    "research_engine":             ["general", "osint", "market_intel"],
    "document_entity_bridge":      ["compliance", "legal"],
    "verification_gate":           ["compliance", "legal", "general"],
    # Reading, writing, response formulation (2026-04-18)
    "pdf_deep_ingest":             ["osint", "general"],
    "style_learner":               ["general"],
    # Memory durability (2026-04-18)
    "memory_replication":          ["general", "compliance"],
    # Fire-on-ARIA + anti-fabrication stack (2026-04-18) — all feed brain
    # so the predictor can downgrade confidence on domains where guards
    # keep triggering and self-metrics can track the learning loop.
    "comprehension":               ["general", "compliance"],
    "ground_truth_guard":          ["compliance", "osint", "legal"],
    "tool_claim_guard":            ["general", "compliance"],
    "consistency_suite":           ["general", "compliance", "procurement", "legal"],
    "capability_card":             ["general", "compliance"],
    "calibration_auto_tune":       ["general", "compliance"],
    "vendor_registry":             ["compliance", "osint", "market_intel"],
    "pending_actions":             ["general"],
    "scratchpad":                  ["general"],
    "extractors_structured":       ["osint", "general"],
    "extractors_facts":            ["osint", "finance", "relationships"],
    # Direct primary-source DD adapters (Track B, 2026-04-18)
    "sources_sec_edgar":           ["finance", "compliance", "market_intel"],
    "sources_ofac_sdn":            ["compliance", "legal"],
    "sources_fcdo_sanctions":      ["compliance", "legal"],
    "sources_un_sc_sanctions":     ["compliance", "legal"],
    "sources_worldbank_debarred":  ["compliance", "legal", "procurement"],
    "sources_worldbank_documents": ["osint", "procurement", "market_intel"],
    "sources_acled":               ["geopolitics", "osint"],
    # RLAIF — Reinforcement Learning from AI Feedback (2026-04-18).
    # Per-turn quality eval broadcasts to all four core dimensions.
    "rlaif":                       ["general", "compliance", "osint"],
    # Constitutional critique collector (2026-04-18) — builds the DPO
    # training dataset. Violations teach which clauses drift over time.
    "critique_collector":          ["general", "compliance", "legal"],
    # Search + learning engine improvements (2026-04-18 late PM)
    "query_decomposer":            ["general", "osint"],
    "known_publisher_router":      ["osint", "general"],
    "source_uptime_monitor":       ["osint", "general", "compliance"],
    "defence_source_seed":         ["osint", "general"],
    # Self-diagnostic (2026-04-18) — ARIA's own health check
    "self_diagnostic":             ["general", "compliance"],
    # Multi-backend search engine (2026-04-18 evening) — wired into
    # researcher.web_search as fallback chain so backend rotation is
    # observable to brain.
    "web_search":                  ["osint", "general"],
    # Counterparty deception scoring (Clause 16, 2026-04-18 evening) —
    # every analyse() call now feeds brain so DD pipeline learns from
    # repeat patterns and the predictor can warn early.
    "deception_detection":         ["compliance", "osint", "relationships"],
    # Production unwired-diagnostic gap closure (2026-04-18 night).
    # These modules existed and called brain via NO path; now each emits.
    "pending_actions":             ["general", "compliance"],
    "known_publisher_router":      ["osint", "general"],
    "sanctions_claim_guard":       ["compliance", "legal"],
    "run_quarantine":              ["compliance", "general"],
    "companies_house":             ["compliance", "finance", "relationships"],
    "comprehension":               ["general", "compliance"],
    "query_decomposer":            ["general", "osint"],
    "document_entity_bridge":      ["compliance", "legal", "osint"],
    "entity_graph":                ["relationships", "osint"],
    "vendor_registry":             ["compliance", "osint", "market_intel"],
    # Tier B (regional knowledge — low weight per query, high frequency)
    "knowledge_balkans":           ["market_intel", "procurement", "compliance"],
    "knowledge_central_africa":    ["market_intel", "procurement", "compliance"],
    "knowledge_gulf":              ["market_intel", "procurement", "compliance"],
    "knowledge_latam_non_lusophone": ["market_intel", "procurement", "compliance"],
    "knowledge_north_africa":      ["market_intel", "procurement", "compliance"],
    "knowledge_south_se_asia":     ["market_intel", "procurement", "compliance"],
    "knowledge_turkey_standalone": ["market_intel", "procurement", "compliance"],
    "knowledge_west_africa":       ["market_intel", "procurement", "compliance"],
    "gulf_oem_structure":          ["market_intel", "procurement", "relationships"],
    "vision_2030_tracker":         ["market_intel", "procurement", "compliance"],
    "political_risk_index":        ["geopolitics", "compliance", "market_intel"],
    "baykar_export_pipeline":      ["market_intel", "competitor_intel", "procurement"],
    # Tier C (crawl/corpus/reasoning)
    "crawl_enhancements":          ["osint", "general"],
    "corpus_manager":              ["general", "osint"],
    "corpus_ingest":               ["general", "osint"],
    "corpus_registry":             ["general", "osint"],
    "symbolic_reasoner":           ["general"],
    "oem_registry":                ["competitor_intel", "market_intel"],
    "tech_classifier":             ["technical", "compliance"],
    # Professional crawl enhancements (2026-04-18 evening)
    "crawl_enhancements":          ["osint", "general"],
    # Playwright scraper package (2026-04-18 evening, clean-split from
    # ARIA_Playwright_Package.zip — stealth stripped).
    "scraper_playwright_engine":   ["osint", "general"],
    "scraper_orchestrator":        ["osint", "general"],
    "scraper_procurement":         ["osint", "procurement", "market_intel"],
    "scraper_generic":             ["osint", "general"],
    # Pre-existing callers exposed by 2026-04-15 integrity audit — these
    # modules were signalling brain but never registered, so their
    # observations filed under "general" with no topical grounding.
    "aria_peers":           ["competitor_intel", "general"],
    "mistake_ledger":       ["general", "compliance"],
    "predictor":            ["general"],
    "self_assess":          ["general"],
}

# Default mastery weight per module (how much a successful run boosts score)
_MODULE_WEIGHT: dict[str, float] = {
    "dd_orchestrator":      0.3,   # full DD is a major exercise
    "compliance_workflow":  0.2,
    "contract_intelligence": 0.25,
    "tender_monitor":       0.1,   # automated scan, lighter signal
    "link_investigator":    0.2,
    "financial_dd":         0.2,
    "network_walker":       0.2,
    "deep_researcher":      0.25,
    "entity_graph":         0.15,
    "person_resolver":      0.1,
    "competitors":          0.15,
    "gtm_strategy":         0.15,
    "risk_indices":         0.1,
    "global_export_control": 0.2,
    "dual_use_classifier":  0.15,
    "euc_library":          0.15,
    "audit_log":            0.05,
    "compliance_file":      0.20,
    "symbolic_reasoner":    0.1,
    "source_verifier":      0.1,
    "sanctions":            0.15,
    "conflict_tracker":     0.1,
    "international_law":    0.15,
    # Node.js seenode modules (via POST /api/aria/brain/absorb)
    "registry_adapter":     0.15,
    "opportunity_detector": 0.15,
    "signal_generator":     0.15,
    "knowledge_ingestor":   0.2,   # /teach URL ingestion — high value
    # Email reader — every inbound email feeds the brain. CRITICAL/HIGH
    # priority emails (LinkedIn job changes, compliance alerts) get the
    # most weight; general email lands at 0.10.
    "email_reader":         0.10,
    # Core Self-Development Loop (Clauses 17/18/19)
    "verified_intel":       0.15,  # every verified fact = real provenance work
    "web_atlas":            0.05,  # per-ingest EMA updates — high frequency, low weight
    "source_validator":     0.10,  # each approval = meaningful curation
    "source_scout":         0.05,  # automated discovery
    "search_doctrine":      0.05,  # search-per-turn — high frequency
    "core_develop":         0.20,  # acting on gaps is high-value self-improvement
    "ecosystem_reassess":   0.05,  # read-only queue build
    "golden_autogen":       0.10,  # self-growing eval set — meaningful curation
    "adversarial_challenge": 0.30, # weekly stress test — highest-value mastery signal
    "narrative_monitor": 0.15,     # hourly narrative scan — moderate weight per detection
    "chain_correlator":     0.25,  # was 0.20 — bumped 2026-04-17 PM;
                                    # confirmed causal chain = high-value
    "procurement_calendar": 0.10,  # static calendar upkeep — low frequency
    "competitor_tracker":   0.15,  # competitor observations drive mastery
    "oem_contact_graph":    0.15,  # OEM landscape updates — moderate weight
    "knowledge_gulf":              0.05,  # static knowledge — low weight per query
    "knowledge_turkey_standalone": 0.05,
    "knowledge_west_africa":       0.05,
    "knowledge_latam_non_lusophone": 0.05,
    "equipment_specs":      0.10,
    "sipri_ingest":         0.15,  # each ingest lands real historical data
    "writer_orchestrator":  0.25,  # each produced document is high-value work
    # Individual writers — gold-tier training pairs. Compliance opinions
    # and procurement papers carry the highest weight because they're
    # legally-load-bearing documents the team may rely on for transaction
    # GO/NO-GO. Tech specs + Portuguese letters land at 0.20 (high value
    # but smaller blast radius).
    "assessment_writer":         0.30,
    "procurement_paper_writer":  0.30,
    "anti_corruption_law":       0.30,
    "tech_spec_writer":          0.20,
    "portuguese_legal_writer":   0.20,
    "propaganda_guard":          0.10,
    # NAK / SERBAN / F3 learnings (2026-04-17)
    # Weights bumped 2026-04-17 PM after calibration review flagged
    # UNDERCONFIDENT -16pp. These modules produce high-confidence
    # structured signals (RDAP evidence, sanctions list hits, cited
    # document verification, compliance draft produced) — every
    # successful fire is real work and should give mastery credit
    # proportional to that.
    "virtual_office_registry":    0.15,  # was 0.10
    "sanctions_propagation":      0.25,  # was 0.15
    "cited_artifact_verifier":    0.15,  # was 0.10
    "protective_reply_drafter":   0.20,  # was 0.15
    # Tier 2 regional knowledge (2026-04-17 PM)
    "knowledge_north_africa":      0.05,
    "knowledge_south_se_asia":     0.05,
    "knowledge_central_africa":    0.05,
    "knowledge_balkans":           0.05,
    "regional_bright_lines":       0.25,  # was 0.15 — every triggered rule
                                           # is a compliance-gate signal the
                                           # brain should weight heavily
    # Heat-map expansion follow-up (2026-04-17 late PM)
    "gulf_oem_structure":          0.10,
    "vision_2030_tracker":         0.10,
    "baykar_export_pipeline":      0.15,
    "political_risk_index":        0.15,
    "cross_regional_correlator":   0.20,
    "autonomy_surface":            0.05,  # aggregator — read-only, low weight
    # F3 DD follow-up (2026-04-17 late PM)
    "domain_ownership_verifier":   0.20,  # was 0.15 — RDAP is hard external
                                           # evidence, worth solid credit
    # F3 cascade remediation (2026-04-17 21:45-21:55)
    "run_quarantine":              0.15,
    "sanctions_claim_guard":       0.25,  # live primary check = high-value signal
    # Continuous learning loop (2026-04-18)
    "training_export":             0.10,
    "knowledge_spider":            0.15,
    "metacognitive_journal":       0.10,
    "research_engine":             0.20,  # self-directed attacking weak cells
    "document_entity_bridge":      0.15,
    "verification_gate":           0.25,  # double-check on critical outputs =
                                           # highest-value learning signal we emit
    # Reading, writing, response formulation (2026-04-18)
    "pdf_deep_ingest":             0.15,  # per-PDF learning signal
    "style_learner":               0.15,  # structural pattern extraction
    "memory_replication":          0.10,  # infra — low weight per run
    # Multi-backend search + deception scoring (2026-04-18 evening)
    "web_search":                  0.05,  # high frequency, low per-call weight
    "deception_detection":         0.20,  # each scored analysis = real DD signal
    # Production gap-closure batch (2026-04-18 night)
    "pending_actions":             0.10,  # honest TODO ledger
    "known_publisher_router":      0.10,  # API-route success/fail per publisher
    "sanctions_claim_guard":       0.25,  # live primary check = high-value
    "run_quarantine":              0.20,  # quarantining = explicit integrity action
    "companies_house":             0.20,  # primary registry hit = solid signal
    "comprehension":               0.10,  # per-turn intent extraction
    "query_decomposer":            0.05,  # per-search classification
    "document_entity_bridge":      0.15,  # entity binding = compliance signal
    "entity_graph":                0.15,  # network mapping = relationships signal
    "vendor_registry":             0.05,  # config/inventory — low weight
    # Tier B regional knowledge — low per-query, accumulates with traffic
    "knowledge_balkans":           0.05,
    "knowledge_central_africa":    0.05,
    "knowledge_gulf":              0.05,
    "knowledge_latam_non_lusophone": 0.05,
    "knowledge_north_africa":      0.05,
    "knowledge_south_se_asia":     0.05,
    "knowledge_turkey_standalone": 0.05,
    "knowledge_west_africa":       0.05,
    "gulf_oem_structure":          0.10,
    "vision_2030_tracker":         0.10,
    "political_risk_index":        0.15,
    "baykar_export_pipeline":      0.15,
    # Tier C
    "crawl_enhancements":          0.05,
    "corpus_manager":              0.10,
    "corpus_ingest":               0.10,
    "corpus_registry":             0.05,
    "symbolic_reasoner":           0.10,
    "oem_registry":                0.05,
    "tech_classifier":             0.10,
    # Pre-existing callers (from integrity audit)
    "aria_peers":           0.10,  # competitor-landscape updates
    "mistake_ledger":       0.05,  # meta — records its own activity
    "predictor":            0.05,  # meta — forecast-hit-rate signals
    "self_assess":          0.10,  # daily briefing composer
}


async def absorb(
    *,
    module: str,
    summary: str,
    detail: str = "",
    entity_name: str = "",
    success: bool = True,
    gap_type: Optional[str] = None,
    gap_detail: Optional[str] = None,
    extra_topics: Optional[list[str]] = None,
    source_id: str = "",
    confidence: str = "PROBABLE",
) -> dict:
    """Feed one intel module's output into all learning tiers.

    Args:
        module:       Module name (must be a key in _MODULE_TOPICS).
        summary:      1-3 sentence summary of what was produced.
        detail:       Full text for neural_memory concept extraction.
                      If empty, summary is used.
        entity_name:  Primary entity name (for knowledge topic keying).
        success:      Did the module complete successfully? False triggers
                      negative mastery update + optional capability gap.
        gap_type:     If not None, records a capability gap (e.g. 'api_missing',
                      'knowledge_gap', 'timeout').
        gap_detail:   Description for the capability gap record.
        extra_topics: Additional mastery topics beyond the module default.
        source_id:    Unique run/trace ID for attribution.
        confidence:   Knowledge confidence level (CONFIRMED/PROBABLE/ASSESSED).

    Returns:
        dict with keys: mastery_ok, knowledge_ok, neural_ok, gap_ok, errors.
    """
    if not BRAIN_HOOK_ENABLED:
        return {"skipped": True, "reason": "ARIA_BRAIN_HOOK_ENABLED=0"}

    # ── 0. Absorption quality gate ──────────────────────────────────────
    # 2026-04-25: refuse to absorb content that contains known fabricated
    # tokens OR was generated in response to a self-introspection
    # question. The 2026-04-24 OpenClaw incident proved that pay-once-
    # remember-forever is a memory-poisoning vector when the underlying
    # answer is a hallucination — Brave Answers fabricated a fictional
    # WhatsApp gateway, brain_hook absorbed it as [CONFIRMED], and the
    # poison persisted across blocking the Brave route, the retrieval-
    # layer quarantine, and the reasoning-router bypass. Each guard
    # blocked surfacing on self-infra questions but couldn't reach the
    # already-absorbed entries.
    #
    # This gate stops absorption at the source for two cases:
    #   (a) content explicitly contains a known fabricated component
    #       token — blanket rejection
    #   (b) absorption is sourced from a brave_answer / search summariser
    #       AND the upstream user question was self-introspective —
    #       refuse to cache an external-search answer about our own
    #       infrastructure (the operator's own diagnostic tooling is the
    #       authoritative source, not a Brave summary)
    try:
        from . import self_infra_detector as _sid
        gate_text = " ".join(filter(None, [summary or "", detail or "", entity_name or ""]))
        if _sid.contains_known_fabrication(gate_text):
            logger.warning(
                "brain_hook.absorb: REFUSED — content matches known fabricated token (module=%s)",
                module,
            )
            await _record_gate_skip("absorption_gate_known_fabrication", module)
            return {
                "skipped": True,
                "reason": "absorption_gate_known_fabrication",
                "module": module,
            }
        # Self-infra topic gate — fires when the absorbing module is
        # search-derived (brave_answer, web_search, scraper-style) and
        # the captured content is about our own infra. Heuristic on
        # module-name + content; deliberately conservative so legitimate
        # operational telemetry from internal modules (sweep, listener,
        # autonomy_surface) is not affected.
        _SEARCH_DERIVED_MODULES = {
            "brave_answer", "brave_answers", "web_search",
            "search_summariser", "search_summarizer", "external_search",
        }
        if module in _SEARCH_DERIVED_MODULES and _sid.is_self_infra_query(gate_text):
            logger.warning(
                "brain_hook.absorb: REFUSED — search-derived self-infra content "
                "(module=%s entity=%s)",
                module, entity_name,
            )
            await _record_gate_skip("absorption_gate_self_infra_search_derived", module)
            return {
                "skipped": True,
                "reason": "absorption_gate_self_infra_search_derived",
                "module": module,
            }
    except Exception as gate_err:
        # Gate failure must not block legitimate absorption. Log and
        # fall through — defence in depth at retrieval layers still holds.
        logger.debug("brain_hook absorption gate check failed (non-fatal): %s", gate_err)

    # ── Opt-in quarantine queue ─────────────────────────────────────────
    # Defence-in-depth tier between accept and reject. Off by default.
    # Operator opts in via env var ARIA_ABSORPTION_QUARANTINE_MODULES (a
    # comma-separated list of module names). Listed modules' absorptions
    # are diverted to the quarantine queue for review instead of being
    # written directly to permanent memory. Pending entries surface via
    # /api/aria/admin/absorption-quarantine/list; operator promotes or
    # rejects from there. Designed to be flippable instantly when a new
    # poison vector is suspected — no redeploy needed.
    try:
        from . import absorption_quarantine as _aq
        if _aq.is_quarantine_enabled_for(module):
            topic_hint = ",".join(extra_topics or []) or None
            qid = await _aq.enqueue(
                module=module,
                entity_name=entity_name,
                summary=summary,
                detail=detail,
                topic=topic_hint,
            )
            logger.info(
                "brain_hook.absorb: QUARANTINED %s (module=%s entity=%s) — operator review pending",
                qid, module, entity_name,
            )
            await _record_gate_skip("absorption_quarantine_pending_review", module)
            return {
                "skipped": True,
                "reason": "absorption_quarantine_pending_review",
                "quarantine_id": qid,
                "module": module,
            }
    except Exception as quarantine_err:
        # Queue failure must not block legitimate absorption.
        logger.debug("absorption_quarantine check failed (non-fatal): %s", quarantine_err)

    # Circuit-breaker check — try to half-open if cooldown elapsed.
    _maybe_close_breaker()

    # If breaker is OPEN, drop the 3 expensive tiers but still record the
    # signal counter in Redis. This protects chat latency without losing
    # the stats trail. Drop is recorded against drops_total so we can see
    # how many signals were sacrificed during the outage.
    if _breaker_state["open"]:
        _breaker_state["drops_total"] += 1
        try:
            await _record_signal(module, success=False)
        except Exception:
            pass
        return {
            "skipped": True,
            "reason": "circuit_breaker_open",
            "trip_reason": _breaker_state["last_trip_reason"],
            "trips_total": _breaker_state["trips_total"],
            "drops_total": _breaker_state["drops_total"],
        }

    _start_ms = time.time() * 1000

    result = {
        "mastery_ok": False,
        "knowledge_ok": False,
        "neural_ok": False,
        "gap_ok": True,  # no gap to record = ok
        "errors": [],
    }

    topics = list(_MODULE_TOPICS.get(module, ["general"]))
    if extra_topics:
        for t in extra_topics:
            if t not in topics:
                topics.append(t)

    weight = _MODULE_WEIGHT.get(module, 0.15)
    text_for_neural = detail or summary
    source = f"brain_hook:{module}"
    if source_id:
        source += f":{source_id}"

    # ── 1. Student mastery ──────────────────────────────────────────────
    try:
        from . import student
        await student.update_mastery(topics, correct=success, weight=weight)
        result["mastery_ok"] = True
    except Exception as e:
        result["errors"].append(f"mastery: {e}")
        logger.debug("brain_hook mastery failed: %s", e)

    # ── 2. Knowledge store ──────────────────────────────────────────────
    if summary:
        try:
            from . import knowledge
            topic_key = f"{module}:{entity_name}" if entity_name else module
            await knowledge.store_fact(
                topic=topic_key,
                content=summary[:2000],
                source=source,
                confidence=confidence,
            )
            result["knowledge_ok"] = True
        except Exception as e:
            result["errors"].append(f"knowledge: {e}")
            logger.debug("brain_hook knowledge failed: %s", e)

    # ── 3. Neural memory ────────────────────────────────────────────────
    if text_for_neural and len(text_for_neural) > 50:
        try:
            from . import neural_memory
            await neural_memory.learn_from_text(
                text=text_for_neural[:5000],
                source=source,
                confidence=confidence,
            )
            result["neural_ok"] = True
        except Exception as e:
            result["errors"].append(f"neural: {e}")
            logger.debug("brain_hook neural failed: %s", e)

    # ── 4. Capability gap (only on failure or explicit gap) ─────────────
    if gap_type:
        try:
            from . import capability_gaps
            await capability_gaps.record_gap(
                gap_type=gap_type,
                detail=gap_detail or f"{module} reported gap: {gap_type}",
                source=f"brain_hook:{module}",
            )
            result["gap_ok"] = True
        except Exception as e:
            result["errors"].append(f"gap: {e}")
            logger.debug("brain_hook gap record failed: %s", e)

    if result["errors"]:
        logger.warning("brain_hook(%s): %d errors — %s",
                        module, len(result["errors"]), "; ".join(result["errors"]))
    else:
        logger.info("brain_hook(%s): absorbed [mastery=%s knowledge=%s neural=%s]",
                     module, result["mastery_ok"], result["knowledge_ok"], result["neural_ok"])

    # ── 5. Record signal for stats/health tracking ──
    # Success = at least mastery OR knowledge stored. Gap errors are non-fatal.
    _core_ok = result["mastery_ok"] or result["knowledge_ok"]
    await _record_signal(module, success=_core_ok)

    # ── 6. Latency tracking + circuit breaker ──
    # If wall-clock exceeded the trip threshold, count toward the
    # consecutive-high counter. _maybe_trip_breaker is idempotent and
    # cheap (sorts a 50-item list), safe to call on every absorb.
    _elapsed_ms = (time.time() * 1000) - _start_ms
    _record_latency(_elapsed_ms)
    _maybe_trip_breaker(reason=f"absorb({module})")
    result["latency_ms"] = round(_elapsed_ms, 1)

    return result


async def absorb_silent(**kwargs) -> None:
    """Fire-and-forget wrapper — logs errors but never raises."""
    try:
        await absorb(**kwargs)
    except Exception as e:
        logger.debug("brain_hook.absorb_silent failed entirely: %s", e)


# =============================================================================
# SIGNAL TRACKING + HEALTH MONITORING
# =============================================================================

_STATS_KEY = "crucix:aria:brain_hook:stats"
_ALERT_STALE_HOURS = 24  # alert if module hasn't sent a signal in 24h

# ── Circuit breaker (2026-04-18 night) ──────────────────────────────────────
# The brain hook is a single point through which 60+ modules signal. If
# Redis goes slow, ChromaDB stalls, or any of the 4 learning tiers
# degrades, every signal blocks for the worst-tier latency. Without a
# circuit breaker, a Redis outage silently turns the brain into a tarpit
# that adds seconds to every chat turn AND drops signals to neural_memory.
#
# This circuit breaker:
#   1. Times every absorb() call's wall-clock duration.
#   2. Maintains a rolling p95 latency over the last 50 calls.
#   3. Tracks consecutive errors per tier (mastery / knowledge / neural).
#   4. Trips OPEN when p95 > _LATENCY_TRIP_MS for 3 consecutive checks.
#   5. While OPEN, absorb() logs to Redis-only (skip the 3 expensive tiers)
#      so chat latency is protected — signal counters still increment.
#   6. Half-opens every _COOLDOWN_S to test if downstream recovered.
#
# Past incident pattern this protects against: a Redis outage during
# the 2026-04-13 LinkedIn ingest that caused chat turns to time out
# at 60s because every brain_hook.absorb was blocking on the dead
# Upstash connection. Better to drop signals loudly than block the user.
_LATENCY_TRIP_MS = int(os.environ.get("ARIA_BRAIN_LATENCY_TRIP_MS", "1500"))
_LATENCY_WINDOW = 50
_TRIP_CONSECUTIVE = 3
_COOLDOWN_S = 60
_BREAKER_KEY = "crucix:aria:brain_hook:breaker"

_recent_latencies_ms: list[float] = []
_breaker_state = {
    "open": False,
    "tripped_at": 0.0,
    "consecutive_high": 0,
    "trips_total": 0,
    "drops_total": 0,
    "last_trip_reason": "",
}


def _record_latency(ms: float) -> None:
    """Record one call latency in the rolling window."""
    _recent_latencies_ms.append(ms)
    if len(_recent_latencies_ms) > _LATENCY_WINDOW:
        del _recent_latencies_ms[0:len(_recent_latencies_ms) - _LATENCY_WINDOW]


def _p95_latency_ms() -> float:
    if not _recent_latencies_ms:
        return 0.0
    sorted_l = sorted(_recent_latencies_ms)
    idx = max(0, int(len(sorted_l) * 0.95) - 1)
    return sorted_l[idx]


def _maybe_trip_breaker(reason: str) -> None:
    """Open the circuit if p95 has been over threshold _TRIP_CONSECUTIVE
    times in a row. Idempotent — safe to call on every absorb."""
    if _breaker_state["open"]:
        return
    p95 = _p95_latency_ms()
    if p95 > _LATENCY_TRIP_MS:
        _breaker_state["consecutive_high"] += 1
        if _breaker_state["consecutive_high"] >= _TRIP_CONSECUTIVE:
            _breaker_state["open"] = True
            _breaker_state["tripped_at"] = time.time()
            _breaker_state["trips_total"] += 1
            _breaker_state["last_trip_reason"] = (
                f"{reason} — p95={p95:.0f}ms over {_LATENCY_TRIP_MS}ms threshold"
            )
            logger.warning(
                "[brain_hook] CIRCUIT TRIPPED — p95=%.0fms reason=%s. "
                "Subsequent absorbs will skip 3 expensive tiers and log to "
                "Redis only until cooldown elapses.",
                p95, reason,
            )
            # Fire a pending_action so the team sees this in the daily
            # briefing — fire-and-forget, don't let pending_actions
            # itself add latency back to brain_hook.
            try:
                async def _alert():
                    from . import pending_actions as _pa
                    await _pa.record(
                        promise="brain_hook circuit breaker tripped",
                        reason=_breaker_state["last_trip_reason"],
                        resolver_kind="operator_action",
                        resolver_ref="check Redis/Chroma latency on dashboard",
                        severity="HIGH",
                        source="brain_hook.circuit_breaker",
                        operator_prompt=(
                            "Brain hook circuit breaker tripped — check "
                            "Redis + ChromaDB latency. Signals are being "
                            "logged to Redis only until p95 recovers."
                        ),
                    )
                # asyncio.get_event_loop() is deprecated in 3.10+ when
                # no loop is running. _record_latency is called from
                # within absorb() (async), so a running loop is always
                # available -- use it directly.
                try:
                    _loop = asyncio.get_running_loop()
                    _loop.create_task(_alert())
                except RuntimeError:
                    pass
            except Exception:
                pass
    else:
        _breaker_state["consecutive_high"] = 0


def _maybe_close_breaker() -> None:
    """Half-open the circuit after the cooldown so subsequent calls test
    whether downstream has recovered.

    PRIOR BUG (observed live 2026-04-27 — breaker stuck OPEN for 13+ min
    after a transient cold-start trip): the OPEN-state absorb path
    short-circuits BEFORE _record_latency, so no new samples are added
    to `_recent_latencies_ms` while the breaker is open. The window
    stays frozen with the slow samples that tripped it. p95 stays
    high forever. The "p95 recovered" check below never fires.

    Fix: after cooldown elapses, treat the next call as a half-open
    probe by *clearing the stale latency window* and closing the
    breaker. The very next absorb runs the full expensive path, records
    its latency normally, and `_maybe_trip_breaker` re-trips after
    _TRIP_CONSECUTIVE bad calls if the underlying issue is still
    present. This is the standard half-open semantics.
    """
    if not _breaker_state["open"]:
        return
    if (time.time() - _breaker_state["tripped_at"]) < _COOLDOWN_S:
        return
    # Cooldown elapsed -- clear stale samples so the breaker doesn't
    # re-judge based on the very samples that tripped it. Snapshot the
    # last p95 for the log line so we can see how stuck it was.
    stale_p95 = _p95_latency_ms()
    _recent_latencies_ms.clear()
    _breaker_state["open"] = False
    _breaker_state["consecutive_high"] = 0
    logger.info(
        "[brain_hook] CIRCUIT CLOSED — cooldown elapsed, "
        "stale p95 was %.0fms (trip threshold %dms). "
        "Clearing latency window and resuming full absorb path; "
        "_maybe_trip_breaker will re-trip naturally if downstream is still slow.",
        stale_p95, _LATENCY_TRIP_MS,
    )
    # Auto-resolve the pending_actions entry that was raised when the
    # breaker tripped. Otherwise stale HIGH alerts accumulate (saw 2
    # in production 2026-04-19 referencing 12s p95 long after p95 had
    # recovered to 26ms).
    try:
        async def _auto_resolve():
            from . import pending_actions as _pa
            opens = await _pa.list_open(limit=20)
            for entry in opens:
                if entry.get("source") == "brain_hook.circuit_breaker":
                    await _pa.mark_satisfied(
                        entry.get("action_id", ""),
                        note=(
                            f"Auto-resolved: brain breaker cooldown elapsed "
                            f"(stale p95 {stale_p95:.0f}ms). Full path resuming."
                        ),
                    )
        # See note above: prefer get_running_loop().
        try:
            _loop = asyncio.get_running_loop()
            _loop.create_task(_auto_resolve())
        except RuntimeError:
            pass
    except Exception:
        pass


def get_breaker_state() -> dict:
    """Return the current circuit breaker state — exposed via /api/aria/brain/stats."""
    return {
        "open": _breaker_state["open"],
        "tripped_at": _breaker_state["tripped_at"] or None,
        "trips_total": _breaker_state["trips_total"],
        "drops_total": _breaker_state["drops_total"],
        "last_trip_reason": _breaker_state["last_trip_reason"] or None,
        "p95_latency_ms": round(_p95_latency_ms(), 1),
        "p99_latency_ms": round(
            sorted(_recent_latencies_ms)[-1] if _recent_latencies_ms else 0, 1
        ),
        "window_size": len(_recent_latencies_ms),
        "trip_threshold_ms": _LATENCY_TRIP_MS,
        "cooldown_s": _COOLDOWN_S,
    }


async def _record_gate_skip(reason: str, module: str) -> None:
    """Record an absorption-gate refusal in Redis under the global stats key.

    Counters live alongside per-module signal stats in
    `crucix:aria:brain_hook:stats` under the special `_gate_skips` key
    (a dict of {reason: {total, by_module: {module: count}, last_at}}).
    Surfaced via /api/aria/brain/stats so dashboards can show
    poisoning-defense activity without scraping WARNING logs.
    """
    try:
        from . import redis_store as rs
        stats = await rs.get_json(_STATS_KEY) or {}
        gate = stats.setdefault("_gate_skips", {})
        bucket = gate.setdefault(reason, {"total": 0, "by_module": {}, "last_at": 0})
        bucket["total"] += 1
        bucket["by_module"][module] = bucket["by_module"].get(module, 0) + 1
        bucket["last_at"] = time.time()
        await rs.set_json(_STATS_KEY, stats, ex=30 * 86400)
    except Exception:
        pass  # gate-skip stats must never block absorb


async def _record_signal(module: str, success: bool) -> None:
    """Record a signal in Redis for per-module tracking."""
    try:
        from . import redis_store as rs
        stats = await rs.get_json(_STATS_KEY) or {}
        now = time.time()

        if module not in stats:
            stats[module] = {
                "total": 0, "success": 0, "fail": 0,
                "last_signal_at": 0, "first_signal_at": now,
            }
        m = stats[module]
        m["total"] += 1
        if success:
            m["success"] += 1
        else:
            m["fail"] += 1
        m["last_signal_at"] = now

        # Global counters
        stats.setdefault("_global", {"total": 0, "started_at": now})
        stats["_global"]["total"] += 1

        await rs.set_json(_STATS_KEY, stats, ex=30 * 86400)
    except Exception:
        pass  # stats recording must never break absorb


async def get_stats() -> dict:
    """Return brain hook stats — per-module signal counts + health."""
    try:
        from . import redis_store as rs
        stats = await rs.get_json(_STATS_KEY) or {}
    except Exception:
        stats = {}

    now = time.time()
    modules = {}
    stale = []
    healthy = []

    for key, val in stats.items():
        if key.startswith("_"):
            continue
        if not isinstance(val, dict):
            continue
        last = val.get("last_signal_at", 0)
        hours_ago = (now - last) / 3600 if last else None
        status = "active"
        if hours_ago is None:
            status = "never"
        elif hours_ago > _ALERT_STALE_HOURS:
            status = "stale"
            stale.append(key)
        else:
            healthy.append(key)

        modules[key] = {
            "total": val.get("total", 0),
            "success": val.get("success", 0),
            "fail": val.get("fail", 0),
            "success_rate": round(val["success"] / val["total"], 2) if val.get("total") else 0,
            "last_signal_ago_h": round(hours_ago, 1) if hours_ago is not None else None,
            "status": status,
        }

    # Identify modules that are registered but have never sent a signal
    all_known = set(_MODULE_TOPICS.keys())
    never_seen = all_known - set(modules.keys())

    g = stats.get("_global", {})
    breaker = get_breaker_state()
    # Composite health: degraded if breaker open OR stale modules exist
    composite = "healthy"
    if breaker["open"]:
        composite = "circuit_open"
    elif stale:
        composite = "degraded"
    elif not healthy:
        composite = "cold"

    # Absorption-gate skip counters — populated by _record_gate_skip when
    # the gate refuses fabricated tokens or search-derived self-infra
    # content. Empty {} when no skips have ever fired (clean state).
    gate_skips_raw = stats.get("_gate_skips", {}) or {}
    absorb_skipped_by_reason = {
        reason: {
            "total": bucket.get("total", 0),
            "by_module": bucket.get("by_module", {}),
            "last_skipped_ago_h": (
                round((now - bucket["last_at"]) / 3600, 1)
                if bucket.get("last_at") else None
            ),
        }
        for reason, bucket in gate_skips_raw.items()
        if isinstance(bucket, dict)
    }

    # Absorption quarantine queue depth (opt-in via env var). Always
    # present in the response but reports `enabled: false` when the
    # ARIA_ABSORPTION_QUARANTINE_MODULES list is empty — operator gets
    # a unified view of defense-in-depth state.
    try:
        from . import absorption_quarantine as _aq
        quarantine_stats = await _aq.stats()
    except Exception:
        quarantine_stats = {"enabled": False, "pending": 0}

    # Cross-sweep verification accumulator queue depth. Populated as
    # well_formed / unverified chat turns are recorded; depth shrinks
    # as the periodic reconciler upgrades entries to grounded.
    try:
        from . import verification_accumulator as _va
        accumulator_stats = await _va.stats()
    except Exception:
        accumulator_stats = {"pending": 0, "upgraded": 0, "last_reconcile": {}}

    return {
        "total_signals": g.get("total", 0),
        "tracking_since": g.get("started_at"),
        "modules": modules,
        "healthy_count": len(healthy),
        "stale_count": len(stale),
        "stale_modules": stale,
        "never_seen": sorted(never_seen),
        "absorb_skipped_by_reason": absorb_skipped_by_reason,
        "absorption_quarantine": quarantine_stats,
        "verification_accumulator": accumulator_stats,
        "circuit_breaker": breaker,
        "health": composite,
    }


async def get_stale_alerts() -> list[dict]:
    """Return alert dicts for modules that haven't sent a signal in 24h."""
    stats = await get_stats()
    alerts = []
    for mod in stats.get("stale_modules", []):
        m = stats["modules"].get(mod, {})
        alerts.append({
            "module": mod,
            "severity": "warning",
            "title": f"Brain signal stale: {mod}",
            "detail": f"{mod} last sent a signal {m.get('last_signal_ago_h', '?')}h ago (threshold: {_ALERT_STALE_HOURS}h)",
            "last_signal_ago_h": m.get("last_signal_ago_h"),
        })
    for mod in stats.get("never_seen", []):
        alerts.append({
            "module": mod,
            "severity": "info",
            "title": f"Brain signal never seen: {mod}",
            "detail": f"{mod} is registered but has never sent a signal to brain_hook",
        })
    return alerts

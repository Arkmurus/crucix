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
from .wire import fail_wire  # R-F1789 §21 brain-wiring
BRAIN_HOOK_ENABLED = os.environ.get("ARIA_BRAIN_HOOK_ENABLED", "1") == "1"

# R-F1316: wire brain_hook's own health to the brain
try:
    from .engine_wiring import wire_success as _ws1316, wire_failure
    _ws1316(
        module="brain_hook",
        summary="Brain Hook active — central learning relay",
        source_id="brain_hook:R-F1316",
    )
except Exception:
    pass

# Topic mapping: module name → student mastery topics it touches
_MODULE_TOPICS: dict[str, list[str]] = {
    "dd_orchestrator":      ["compliance", "osint", "finance", "relationships"],
    # R-F2620/R-F3481: access-request health telemetry. Public form assertions
    # are not durable relationship knowledge; only non-PII success/failure
    # signals use this topic mapping until verification (§21).
    "inbound_leads":        ["market_intel", "relationships"],
    # R-F154 (2026-05-10): added intel_ledger — was missing from the map and
    # showing as ⚠ on /aria-brain System Health ("not in brain_hook._MODULE_TOPICS
    # — signals from this module will file under 'gen[eric]'"). intel_ledger is
    # the permanent signal store that aggregates EVERY significant signal by
    # country/product/OEM (~20K+ signals as of 2026-05-10). Topic mapping is
    # deliberately broad because the ledger is a super-aggregator; specific
    # producers register their own topic sets, but ledger-routed signals need
    # all major topic axes to land correctly in mastery rollups.
    "intel_ledger":         ["compliance", "geopolitics", "market_intel", "procurement", "relationships", "osint"],
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
    "sanctions":            ["compliance", "legal", "sanctions"],
    "conflict_tracker":     ["geopolitics", "relationships"],
    "international_law":    ["legal", "compliance"],
    # R-F3215 — the vetting module has been emitting wire_success/wire_failure
    # since R-F3138 and was never registered here, so every signal it produced
    # filed under 'generic' and the module showed ⚠ on /aria-brain System
    # Health. A limb the brain cannot topic-map is a limb it cannot learn from
    # (§21). `vetting_standard_knowledge` is the BS 7858 clause register.
    "vetting":              ["compliance", "legal", "relationships"],
    "vetting_portal":       ["compliance", "legal"],
    "vetting_standard_knowledge": ["compliance", "legal"],
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
    # R-F655 (2026-05-17) — clause 15 pay-once-remember-forever: every paid
    # LLM completion on the chat path feeds the brain. aria_chat (non-stream,
    # /api/aria/chat) and aria_chat_stream (/api/aria/chat/stream, WhatsApp
    # default) emit one absorb call per turn, fire-and-forget after all guards
    # have settled the response_text. Pre-R-F655 audit (2026-05-17) found
    # zero brain_hook.absorb calls on either path — every paid chat answer
    # was paid for, served once, and never written back to rag/ledger/mastery.
    # Broad topic set because chat touches everything the user asks about.
    "aria_chat":         ["general", "compliance", "osint", "market_intel", "procurement", "geopolitics", "relationships", "legal"],
    "aria_chat_stream":  ["general", "compliance", "osint", "market_intel", "procurement", "geopolitics", "relationships", "legal"],
    # NAK / SERBAN / F3 learnings (2026-04-17) — six new capabilities closing
    # the gaps observed on the live KNDS / FK-3000 engagement.
    "virtual_office_registry":    ["compliance", "osint", "finance"],
    "sanctions_propagation":      ["compliance", "legal", "procurement", "sanctions"],
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
    "sanctions_claim_guard":       ["compliance", "legal", "sanctions"],
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
    "sources_fcdo_sanctions":      ["compliance", "legal", "sanctions"],
    "sources_un_sc_sanctions":     ["compliance", "legal", "sanctions"],
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
    # Tier C (crawl/corpus/reasoning)
    "corpus_manager":              ["general", "osint"],
    "corpus_ingest":               ["general", "osint"],
    "corpus_registry":             ["general", "osint"],
    "oem_registry":                ["competitor_intel", "market_intel"],
    "tech_classifier":             ["technical", "compliance"],
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
    # R-F921 (2026-05-26) — Rule Zero self-observation channel. ARIA's own
    # runtime failures (chat timeouts, suppressed stale fallbacks, hallucination-
    # guard trips, self-state queries, self-monitor heartbeats) feed her brain
    # through here so she SEES what happens to her OS and can later reason about
    # / propose fixes for it. Routed via observe_self_event() below.
    "self_monitor":         ["general", "compliance"],
    # R-F973 (2026-05-28): Node OSINT sweep ingest (main.py ingest_sweep) —
    # the largest Node→brain data path. Registered so its absorb signal is
    # tracked on System Health instead of showing ⚠ "unknown module".
    "ingest_sweep":         ["osint", "market_intel"],
    # R-F1048 -- grounded reasoner: evidence-traced, ground-or-abstain answers.
    "grounded_reasoner":    ["general", "osint", "compliance", "legal"],
    # R-F1049 -- news monitor: RSS/feed polling for defence, geopolitics, finance
    "news_monitor":         ["osint", "market_intel", "geopolitics", "general"],
    # R-F1051 -- self-healing infrastructure
    "self_healing":         ["general", "compliance"],
    # R-F1046 -- honesty judge, self-introspect guard, ground truth loop
    "honesty_judge":        ["general", "compliance"],
    "self_introspect_guard": ["general", "compliance"],
    "ground_truth_loop":    ["general", "compliance", "osint"],
    # R-F1046 -- eliminated weapons watchlist
    "eliminated_weapons_watchlist": ["compliance", "legal"],
    # R-F1053 -- BD strategy engine
    "bd_strategy":          ["market_intel", "competitor_intel", "general"],
    # R-F1055 -- professional engagement layer
    "engagement":           ["general"],
    # R-F1226 -- System Health fixes: modules that were signalling brain
    # but never registered, showing as ⚠ on System Health dashboard.
    "crawl_enhancements":   ["osint", "general"],
    "compliance_watch":     ["compliance", "legal", "general"],
    "self_claim_guard":     ["compliance", "general"],
    "capability_gaps":      ["general", "compliance"],
    "error_log_handler":    ["general", "compliance"],
    "engine_wiring":        ["general"],
    "wiring_monitor":       ["general", "compliance"],
    "stream_honesty":       ["general", "compliance"],
    "reasoning_library":    ["general", "osint"],
    "regional_navigation":  ["osint", "general"],
    "sanctions_divergence": ["compliance", "legal", "sanctions"],
    "autonomous_scheduler": ["general"],
    "autonomy_scorer":      ["general"],
    "operating_modes":      ["general"],
    "calibration_review":   ["general", "compliance"],
    "self_restart":         ["general"],
    "dead_letter_queue":    ["general", "compliance"],
    "circuit_breaker":      ["general", "compliance"],

    # ── R-F4046 (C-106) — modules that call absorb() but had no topic entry ──
    #
    # `absorb` routes knowledge by `_MODULE_TOPICS.get(module, ["general"])`, so
    # these were absorbing real domain knowledge into an undifferentiated
    # `general` bucket. Measured 2026-08-16: 25 of the 124 distinct absorb()
    # module names were unregistered.
    #
    # Only absorb() callers are listed. 343 further names report through
    # `record_signal`/`wire_*`, which never consult topics — registering those
    # would be decoration, and `_MODULE_TOPICS` is a ROUTING TABLE, not an
    # inventory of what exists (C-104 measured 87.8% of live signals arriving
    # from names outside it).
    #
    # Topics are taken from the vocabulary already in this table and matched to
    # existing precedent — a WRONG topic is worse than the `general` default,
    # because mis-tagged knowledge is retrieved for the wrong questions.
    # Genuinely ambiguous / infrastructure absorbers are left on the default and
    # declared in `DELIBERATELY_GENERAL` in the R-F4046 test.
    "crypto_sanctions":       ["compliance", "legal", "sanctions"],
    "rca_screening":          ["compliance", "legal", "sanctions"],
    "fcpa_enforcement":       ["compliance", "legal"],
    "tbml_detection":         ["compliance", "finance"],
    "registration_check":     ["compliance", "legal"],
    "companies_house":        ["compliance", "legal", "market_intel"],
    "dd_registry_enrichment": ["compliance", "legal"],
    "counter_intelligence":   ["osint", "geopolitics"],
    "web_explorer":           ["osint", "general"],
    "deal_predictor":         ["market_intel", "procurement"],
    "commercial_coherence":   ["market_intel", "procurement"],
    "strategic_evolution":    ["market_intel", "geopolitics"],
    "precall_brief":          ["relationships", "market_intel"],
    "meeting_notes":          ["relationships", "general"],
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
    # Tier C
    "corpus_manager":              0.10,
    "corpus_ingest":               0.10,
    "corpus_registry":             0.05,
    "oem_registry":                0.05,
    "tech_classifier":             0.10,
    # Pre-existing callers (from integrity audit)
    "aria_peers":           0.10,  # competitor-landscape updates
    "mistake_ledger":       0.05,  # meta — records its own activity
    "predictor":            0.05,  # meta — forecast-hit-rate signals
    "self_assess":          0.10,  # daily briefing composer
    # R-F1048 -- grounded reasoner: moderate weight per grounded answer
    "grounded_reasoner":    0.20,
    # R-F1049 -- news monitor: moderate weight per feed poll
    "news_monitor":         0.10,
    # R-F1051 -- self-healing: low weight per check
    "self_healing":         0.05,
    # R-F1046 -- honesty guards: moderate weight per judgment
    "honesty_judge":        0.15,
    "self_introspect_guard": 0.10,
    "ground_truth_loop":    0.15,
    # R-F1046 -- eliminated weapons: high weight per hit (compliance-critical)
    "eliminated_weapons_watchlist": 0.25,
    # R-F1053 -- BD strategy: high weight per generation
    "bd_strategy":          0.25,
    # R-F1055 -- engagement: low weight per interaction
    "engagement":           0.05,
}


# R-F860 (2026-05-24) — interactive yield. When a USER is actively chatting or
# uploading a document, autonomous absorbs back off so the interactive request
# (especially /read-document, which contract review depends on) wins the
# GIL-bound encoder instead of timing out behind the autonomous absorb storm
# (the 2026-05-24 wedge that failed the contract upload 6×). mark_interactive()
# is called by the chat + document endpoints; _absorb_pause_ms() then returns
# the larger interactive pause while activity is recent.
_last_interactive_at: float = 0.0
_INTERACTIVE_YIELD_WINDOW_S = float(os.environ.get("ARIA_BRAIN_INTERACTIVE_YIELD_S", "8"))


@fail_wire(module="brain_hook", gap_type="engine_failure")
def mark_interactive() -> None:
    """Record that a user-facing request (chat / document read) just arrived,
    so autonomous absorbs yield the encoder to it for the next
    _INTERACTIVE_YIELD_WINDOW_S. Cheap + safe to call on every request. R-F860."""
    global _last_interactive_at
    _last_interactive_at = time.monotonic()


def _interactive_active() -> bool:
    """True iff a user-facing request arrived within the yield window."""
    return (time.monotonic() - _last_interactive_at) < _INTERACTIVE_YIELD_WINDOW_S


def seconds_since_interactive() -> float:
    """R-F2198 — seconds since the last user-facing request (chat / document
    read) arrived. Returns a large number if none has been seen this process.
    Used by the load governor so autonomy can YIELD to active users (back off
    its heavy research while someone is chatting), not just to the encoder."""
    if _last_interactive_at <= 0.0:
        return 1e9
    return time.monotonic() - _last_interactive_at


def _absorb_pause_ms() -> int:
    """R-F460 (2026-05-14) — operator-tunable pause between absorb calls.

    Reading the env var on every call (not at import time) so the
    operator can change it on a running deploy via flyctl env vars
    without a restart.

    Default 0 = no behavior change (backward-compatible).
    Suggested operator values when the brain_hook breaker is tripping:
      - 50 ms  → caps at 20 absorbs/sec, gentle smoothing
      - 100 ms → 10 absorbs/sec, comfortable for email-reader catchup
      - 500 ms → aggressive throttle, debugging only

    Live evidence (fly logs 2026-05-13 22:31 BST): under email-reader
    backlog burst, brain_hook.absorb p95 climbed to 28484ms (against
    a 3500ms trip threshold), tripping the circuit breaker repeatedly.
    A 100ms inter-absorb pause spaces the burst from "30 in 1 second"
    to "30 over 3 seconds" — slow enough that downstream embedding +
    chromadb work catches up between calls, breaker stays untripped.
    """
    import os
    try:
        v = int((os.getenv("ARIA_BRAIN_ABSORB_PAUSE_MS") or "0").strip())
    except (ValueError, TypeError):
        return 0
    # Clamp to a sane range — operator shouldn't be able to wedge the
    # service by setting a pathological value.
    if v < 0:
        v = 0
    elif v > 5000:
        v = 5000  # 5s ceiling — anything higher is almost certainly a typo
    # R-F860 — yield to interactive activity. While a user request (chat /
    # read-document) is recent, autonomous absorbs use the larger interactive
    # pause so the user wins the GIL-bound encoder instead of timing out behind
    # the absorb storm. Operator-tunable (default 400ms); returns max(base,
    # interactive) so an operator's base pause is never reduced.
    if _interactive_active():
        try:
            iv = int((os.getenv("ARIA_BRAIN_INTERACTIVE_PAUSE_MS") or "400").strip())
        except (ValueError, TypeError):
            iv = 400
        return max(v, max(0, min(iv, 5000)))
    return v


# R-F521 (2026-05-14) — make ARIA_BRAIN_ABSORB_PAUSE_MS apply GLOBALLY,
# not per-coroutine. Pre-R-F521 the pause was implemented as a per-call
# `await asyncio.sleep(pause_ms/1000)` inside absorb(), which meant N
# concurrent absorbs all slept the SAME pause_ms window in parallel —
# the wall-clock rate-limit the operator was trying to set wasn't
# actually enforced.
#
# Live evidence 2026-05-14 12:21:17 BST cold boot: ~17 knowledge_xxx
# modules fired brain_hook.absorb() in parallel during startup
# seeding. All 17 paused 500ms concurrently (within the same wall
# window), then all hit the embedding + chromadb pipeline at once.
# absorb p95 climbed to 16635ms (> 3500ms trip threshold), circuit
# breaker tripped, pending_action recorded for operator. Same cold-
# boot circuit-trip recurred on every fly machine restart.
#
# Fix: global lock + last-absorb timestamp. Wall-clock rate is now
# enforced across the whole process: regardless of how many absorbs
# are queued, at most one starts every pause_ms. Cold-boot storm
# becomes a paced sequence instead of a simultaneous spike.
_absorb_global_lock: Optional[asyncio.Lock] = None
_last_absorb_monotonic: float = time.monotonic()  # R-F1326: init to now so first call doesn't skip


def _get_absorb_lock() -> asyncio.Lock:
    """Lazy-init the global lock. asyncio.Lock() binds to the current
    event loop on construction — creating it at module import time
    fails when no loop is running yet. Lazy creation defers binding
    to the first absorb() call, by which time the FastAPI lifespan
    has its loop ready."""
    global _absorb_global_lock
    if _absorb_global_lock is None:
        _absorb_global_lock = asyncio.Lock()
    return _absorb_global_lock


async def _wait_for_absorb_slot() -> None:
    """Block until at least pause_ms has elapsed since the previous
    absorb call started. Serializes across all concurrent callers."""
    global _last_absorb_monotonic
    pause_ms = _absorb_pause_ms()
    if pause_ms <= 0:
        return
    lock = _get_absorb_lock()
    async with lock:
        now = time.monotonic()
        elapsed_ms = (now - _last_absorb_monotonic) * 1000.0
        if elapsed_ms < pause_ms:
            await asyncio.sleep((pause_ms - elapsed_ms) / 1000.0)
        _last_absorb_monotonic = time.monotonic()


@fail_wire(module="brain_hook", gap_type="engine_failure")
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
    user_id: str = "",
    sector: str = "",
) -> dict:
    """Feed one intel module's output into all learning tiers.

    R-F1316: self-observes absorb() calls so brain_hook's own health
    is visible to the brain. On failure, records a self_runtime gap
    via observe_self_event so the coder can see it.

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

        R-F56 (per-customer learning loop):
        user_id:      Authenticated user id when the signal came from a
                      chat / DD turn. Empty string for autonomous /
                      sweep-driven signals (no human in the loop).
        sector:       Persona sector tag from the user record (broker /
                      oem_export / government_acquisition / compliance /
                      banking_insurance / journalist). Carried alongside
                      every signal so future per-sector mastery rollups
                      can bucket without re-ingest.

    Returns:
        dict with keys: mastery_ok, knowledge_ok, neural_ok, gap_ok, errors.
    """
    if not BRAIN_HOOK_ENABLED:
        return {"skipped": True, "reason": "ARIA_BRAIN_HOOK_ENABLED=0"}

    # ── R-F521 (2026-05-14, supersedes per-call pause from R-F460) ─────
    # Wall-clock global rate-limit via _wait_for_absorb_slot(). Pre-
    # R-F521 the pause was per-coroutine so N parallel absorbs slept
    # the same window concurrently — the cold-boot storm at 12:21:17
    # tripped the circuit breaker despite ARIA_BRAIN_ABSORB_PAUSE_MS
    # being set. See _wait_for_absorb_slot docstring for the
    # mechanism. ARIA_BRAIN_ABSORB_PAUSE_MS=0 (default) is a no-op
    # fast path.
    await _wait_for_absorb_slot()

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
    # ── R-F3615 — (c) DELIBERATION IS NOT A FACT ────────────────────────
    # Same memory-poisoning vector as (a), different payload. R-F3033
    # (2026-07-25..07-31) served a reasoning model's `reasoning_content` as the
    # answer, so for six days any module absorbing its own LLM output could
    # store raw chain of thought. Live proof, from the operator's 2026-08-01
    # transcript, served back as a "verified fact":
    #   contract_intelligence:detail: "── Self-review window 1/1 ── We need
    #   answer audit. Need follow instructions. Need inspect window text..."
    # R-F3608 closed the aria_chat path by name; this closes the CLASS at the
    # one chokepoint every module shares.
    try:
        from .deliberation_guard import looks_like_deliberation as _r3615
        if _r3615(detail or "") or _r3615(summary or ""):
            logger.warning(
                "[R-F3615] brain_hook.absorb REFUSED — content is model "
                "deliberation, not a finding (module=%s, %d chars)",
                module, len(detail or summary or ""),
            )
            return {"skipped": True, "reason": "deliberation_not_a_fact"}
    except Exception:
        logger.debug("[R-F3615] deliberation guard failed (non-fatal)", exc_info=True)

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
    # R-F56: when caller didn't pass user_id/sector, fall back to the
    # current chat-turn contextvar set by aria_chat / aria_chat_stream.
    # Means downstream modules (deep_research, dd_orchestrator, etc.)
    # get correctly tagged absorbs even if they don't thread user_id
    # through their own call chains.
    if not user_id or not sector:
        _ctx_uid, _ctx_sector = get_chat_context()
        if not user_id and _ctx_uid:
            user_id = _ctx_uid
        if not sector and _ctx_sector:
            sector = _ctx_sector

    # Normalise sector once at entry so downstream calls see a stable
    # bucket key. Empty / unknown values normalise to "" (no per-sector
    # bucket) so legacy / sweep-driven absorbs preserve the existing shape.
    _sector_normalised = (sector or "").strip().lower()
    if _sector_normalised and _sector_normalised not in _ALLOWED_SECTORS:
        _sector_normalised = "unknown"

    if _breaker_state["open"]:
        _breaker_state["drops_total"] += 1
        # R-F1480: do NOT record the calling module as success=False here.
        # The drop is brain overload, not the module failing — and it's
        # already tracked in drops_total above. Previously this called
        # _record_signal(module, success=False) which mis-attributed every
        # breaker drop as the module's own failure, causing modules that
        # burst-absorb at boot (agent_registry, agent_contract) to show 0%
        # success rate in brain stats even though they work correctly.
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

    # R-F795 (2026-05-22) — helper to call each tier under a wall-time
    # ceiling. asyncio.TimeoutError is a subclass of Exception so the
    # outer `except Exception` would catch it, but we want a distinct
    # log line + error string so ops can grep timeouts vs real
    # failures. `_TIER_TIMEOUT_S <= 0` disables the timeout (legacy
    # unbounded behaviour).
    async def _run_tier(coro, label: str) -> tuple[bool, Optional[str]]:
        try:
            if _TIER_TIMEOUT_S > 0:
                await asyncio.wait_for(coro, timeout=_TIER_TIMEOUT_S)
            else:
                await coro
            return (True, None)
        except asyncio.TimeoutError:
            logger.debug(
                "brain_hook %s tier timed out (>%.1fs) — module=%s",
                label, _TIER_TIMEOUT_S, module,
            )
            return (False, f"{label}: timeout (>{_TIER_TIMEOUT_S:.1f}s)")
        except Exception as e:
            logger.debug("brain_hook %s failed: %s", label, e)
            return (False, f"{label}: {e}")

    # ── R-F1155: background tier processing ──────────────────────────────
    # Move the 3 expensive tiers (mastery, knowledge, neural) into a
    # background task so absorb() returns immediately. The concurrency
    # semaphore still limits parallel neural encodes inside the background
    # task, but no longer blocks the caller. Chat-derived signals (user_id
    # set) are tagged as priority so they preempt crawler signals.
    _is_interactive = bool(user_id)
    from .brain_hook_bg import absorb_tiers_bg
    # R-F1342: bound the in-flight bg-task count. Interactive (chat) signals
    # are always allowed through; only background-burst signals are shed when
    # the backlog is at the cap — so user-facing quality is never degraded and
    # the loop can't be buried under thousands of pending absorb tasks.
    global _pending_absorb, _dropped_absorb
    # R-F1342: bound the in-flight bg-task COUNT (the pile-up that helped wedge
    # the loop 2026-06-05). Over the cap, background signals skip the expensive
    # tier task — but the durable fact is NOT forgotten: it goes to the WAL for
    # retry (§7 infinite memory). Interactive (chat) signals are never shed.
    _over_cap = (not _is_interactive) and (_pending_absorb >= _MAX_PENDING_ABSORB)
    if _BRAIN_QUEUE_ENABLED:
        # R-F2507 — durable-queue path: enqueue (one cheap INSERT to a SEPARATE db
        # file, no state_store contention) + return; the single drain worker
        # (brain_queue_drain_loop) applies it later at bounded concurrency. Priority:
        # interactive/chat=0, failure/gap=1, else=2 (drained first→last). If the queue
        # is unavailable (pre-connect / error) the fact is NOT lost — it falls back to
        # memory_wal (§7) and the gap is still recorded now (§21e timeliness).
        _q_priority = 0 if _is_interactive else (1 if gap_type else 2)
        _enq_ok = False
        try:
            from . import brain_ingest_queue as _biq2507
            _enq_ok = await _biq2507.enqueue(
                {
                    "module": module, "summary": summary,
                    "text_for_neural": text_for_neural, "source": source,
                    "topics": topics, "success": success, "weight": weight,
                    "confidence": confidence, "entity_name": entity_name,
                    "gap_type": gap_type, "gap_detail": gap_detail,
                    "sector": _sector_normalised, "user_id": user_id,
                    "result_seed": result,
                },
                priority=_q_priority,
            )
        except Exception:
            _enq_ok = False
        if not _enq_ok:
            # Never forget (§7): durable WAL fallback + record the gap now so the
            # coder loop isn't blinded (§21e) + one durable-outcome signal.
            if summary:
                try:
                    from . import memory_wal as _mw2507
                    _tk2507 = f"{module}:{entity_name}" if entity_name else module
                    _mw2507.record_pending_fact(
                        topic=_tk2507, content=summary[:2000],
                        source=source, confidence=confidence,
                    )
                except Exception:
                    pass
            if gap_type:
                try:
                    from . import capability_gaps as _cg2507
                    await _cg2507.record_gap(
                        gap_type=gap_type,
                        detail=gap_detail or f"{module} reported gap: {gap_type} (queue-enqueue failed)",
                        source=source, user_id=user_id, sector=_sector_normalised,
                    )
                except Exception:
                    pass
            await _record_signal(module, success=True, sector=_sector_normalised)
        # On enqueue SUCCESS the drain's absorb_tiers_bg records the signal exactly
        # once (like the legacy bg path) — do NOT record here (avoids the 50%-floor
        # double-count documented below).
    elif _over_cap:
        _dropped_absorb += 1
        if summary:  # never forget — queue the fact durably for later retry
            try:
                from . import memory_wal
                _tk = f"{module}:{entity_name}" if entity_name else module
                memory_wal.record_pending_fact(
                    topic=_tk, content=summary[:2000],
                    source=source, confidence=confidence,
                )
            except Exception:
                pass
        # R-F2404 — CRITICAL: a reported FAILURE gap must NOT be lost just because
        # the absorb backlog is at cap. The expensive tiers are shed here (only the
        # NORMAL branch's absorb_tiers_bg consumes gap_type, brain_hook_bg.py:161),
        # so without this a module reporting a gap during an autonomous burst — i.e.
        # exactly when failures cluster — blinds the gap_detector→self_coder loop
        # (§21c/§21e). record_gap is cheap (no encode/tier work), so record it now.
        if gap_type:
            try:
                from . import capability_gaps
                await capability_gaps.record_gap(
                    gap_type=gap_type,
                    detail=gap_detail or f"{module} reported gap: {gap_type} (recorded under absorb backlog shed)",
                    source=source,
                    user_id=user_id,
                    sector=_sector_normalised,
                )
            except Exception:
                logger.exception("[R-F2404] shed-branch record_gap failed for module=%s", module)
        if _dropped_absorb % 50 == 1:
            logger.warning(
                "[R-F1342] absorb bg backlog at cap (%d) — shed expensive tiers "
                "for module=%s; fact queued to WAL (%d shed)",
                _MAX_PENDING_ABSORB, module, _dropped_absorb,
            )
        # R-F1483: the expensive tiers were shed, but the fact is durably WAL-queued,
        # so record ONE signal here (the durable outcome succeeded). The normal
        # branch lets the bg tier record the REAL outcome (success=_core_ok) once —
        # see brain_hook_bg.py:170. This replaces the old unconditional success=True
        # that ran for EVERY absorb AFTER this if/else: combined with the bg tier's
        # record it double-counted and floored every module's success_rate at 50%,
        # measuring "absorb was called" + brain persistence, not agent reliability.
        await _record_signal(module, success=True, sector=_sector_normalised)
    else:
        _pending_absorb += 1
        _bh_task = asyncio.create_task(
            absorb_tiers_bg(
                module=module,
                summary=summary,
                text_for_neural=text_for_neural,
                source=source,
                topics=topics,
                success=success,
                weight=weight,
                confidence=confidence,
                entity_name=entity_name,
                gap_type=gap_type,
                gap_detail=gap_detail,
                sector=_sector_normalised,
                user_id=user_id,
                result=result,
                _get_absorb_concurrency_sem=_get_absorb_concurrency_sem,
                _get_neural_concurrency_sem=_get_neural_concurrency_sem,
                _ABSORB_CONCURRENCY=_ABSORB_CONCURRENCY,
                _ABSORB_SEM_ACQUIRE_TIMEOUT_S=_ABSORB_SEM_ACQUIRE_TIMEOUT_S,
                _run_tier=_run_tier,
                _record_signal=_record_signal,
                _record_latency=_record_latency,
                _maybe_trip_breaker=_maybe_trip_breaker,
                _start_ms=_start_ms,
            ),
            name=f"bh-{'chat' if _is_interactive else 'bg'}-{module}",
        )
        _absorb_background_tasks.add(_bh_task)
        _bh_task.add_done_callback(_dec_pending_absorb)  # R-F1342: track backlog

    # R-F1483: per-module stats are recorded EXACTLY ONCE per absorb — by the bg
    # tier (success=_core_ok, the real durable-knowledge outcome) in the normal
    # path, or in the shed branch above (success=True, WAL-preserved). The old
    # unconditional success=True here double-counted with the bg record and floored
    # success_rate at 50% for every module. Do NOT re-add a record here.
    result["user_id"] = user_id or ""
    result["sector"] = _sector_normalised
    result["latency_ms"] = 0.0  # actual latency tracked in background task

    # R-F1316: self-observe successful absorb so brain_hook's own health
    # is visible. Only record every 100th call to avoid infinite recursion
    # (absorb -> observe_self_event -> absorb -> ...).
    _absorb_call_count = getattr(absorb, "_call_count", 0) + 1
    absorb._call_count = _absorb_call_count
    if _absorb_call_count % 100 == 0:
        try:
            await observe_self_event(
                event="brain_hook_absorb_ok",
                detail=f"brain_hook absorb OK after {_absorb_call_count} calls",
                success=True,
                gap_type=None,
            )
        except Exception:
            pass  # never let self-observation break absorb

    return result


@fail_wire(module="brain_hook", gap_type="engine_failure")
async def absorb_silent(**kwargs) -> None:
    """Fire-and-forget wrapper — logs errors but never raises."""
    try:
        await absorb(**kwargs)
    except Exception as e:
        logger.debug("brain_hook.absorb_silent failed entirely: %s", e)


# ── R-F2507: durable brain-ingest queue drain worker ────────────────────────
# When ARIA_BRAIN_QUEUE_ENABLED=1, absorb() enqueues instead of firing an
# in-memory create_task. THIS single worker (started once at boot, main.py) pulls
# batches from the durable queue and applies each through the SAME tier processor
# (absorb_tiers_bg) at BOUNDED concurrency — so brain-ingest writes are PACED past
# the single state_store writer instead of an unbounded concurrent burst. Durable:
# a restart replays the queue; a failed apply retries with backoff then dead-letters.

async def _run_tier_ml(coro, label: str) -> tuple[bool, Optional[str]]:
    """R-F2507 — MODULE-LEVEL tier runner for the queue drain worker. absorb()'s own
    _run_tier is a NESTED function (closes over its `module` local), so the drain
    worker cannot pass it — referencing it NameError'd and every drained item
    dead-lettered. Same timeout+error contract; omits the per-module debug string
    (the single drain worker processes many modules)."""
    try:
        if _TIER_TIMEOUT_S > 0:
            await asyncio.wait_for(coro, timeout=_TIER_TIMEOUT_S)
        else:
            await coro
        return (True, None)
    except asyncio.TimeoutError:
        logger.debug("brain_hook drain %s tier timed out (>%.1fs)", label, _TIER_TIMEOUT_S)
        return (False, f"{label}: timeout (>{_TIER_TIMEOUT_S:.1f}s)")
    except Exception as e:
        logger.debug("brain_hook drain %s failed: %s", label, e)
        return (False, f"{label}: {e}")


async def _drain_one_queued(row: dict) -> None:
    """Apply one dequeued ingest payload through absorb_tiers_bg, then mark
    done / failed. Called inside the drain loop's bounded-concurrency gate."""
    from . import brain_ingest_queue as _biq
    from .brain_hook_bg import absorb_tiers_bg
    p = row.get("payload") or {}
    try:
        await absorb_tiers_bg(
            module=p.get("module", ""),
            summary=p.get("summary", ""),
            text_for_neural=p.get("text_for_neural", ""),
            source=p.get("source", ""),
            topics=p.get("topics") or [],
            success=bool(p.get("success", True)),
            weight=float(p.get("weight", 1.0) or 1.0),
            confidence=p.get("confidence", "PROBABLE"),
            entity_name=p.get("entity_name", ""),
            gap_type=p.get("gap_type"),
            gap_detail=p.get("gap_detail"),
            sector=p.get("sector", ""),
            user_id=p.get("user_id", ""),
            result=p.get("result_seed") or {},
            _get_absorb_concurrency_sem=_get_absorb_concurrency_sem,
            _get_neural_concurrency_sem=_get_neural_concurrency_sem,
            _ABSORB_CONCURRENCY=_ABSORB_CONCURRENCY,
            _ABSORB_SEM_ACQUIRE_TIMEOUT_S=_ABSORB_SEM_ACQUIRE_TIMEOUT_S,
            _run_tier=_run_tier_ml,  # module-level (absorb's _run_tier is nested)
            _record_signal=_record_signal,
            _record_latency=_record_latency,
            _maybe_trip_breaker=_maybe_trip_breaker,
            _start_ms=time.time() * 1000,  # fresh latency baseline at drain time
        )
        await _biq.mark_done([row["id"]])
    except Exception as e:
        # Retry with backoff, then dead-letter (never lose the fact).
        try:
            await _biq.mark_failed(row["id"], p, int(row.get("attempts", 0)), str(e)[:300])
        except Exception:
            logger.warning("[R-F2507] mark_failed itself failed for id=%s: %s", row.get("id"), e)


async def _drain_worker_body(worker_id: int, sem: "asyncio.Semaphore", batch: int) -> None:
    """One brain-ingest drain worker loop (R-F2564). Assumes brain_ingest_queue is
    ALREADY connected (the parent connects once, then fans out N of these). Loops:
    pull a due batch, apply it under the SHARED concurrency `sem`, sleep. Catches
    per-iteration errors so a transient failure never kills the worker (only
    CancelledError, at shutdown, exits it)."""
    from . import brain_ingest_queue as _biq

    async def _bounded(row: dict) -> None:
        async with sem:
            await _drain_one_queued(row)

    while True:
        try:
            # dequeue_batch atomically claims + flips to 'processing' under the
            # queue write lock, so N workers pulling concurrently NEVER double-claim
            # a row (highest-priority/oldest first via the ORDER BY).
            rows = await _biq.dequeue_batch(limit=batch)
            if rows:
                await asyncio.gather(*[_bounded(r) for r in rows], return_exceptions=True)
                await asyncio.sleep(0.02)  # brief yield between batches
            else:
                await asyncio.sleep(1.0)   # idle poll when queue is empty
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("[R-F2507] drain worker %d iteration failed: %s", worker_id, e)
            await asyncio.sleep(2.0)


@fail_wire(module="brain_hook", gap_type="engine_failure")
async def brain_queue_drain_loop() -> None:
    """Brain-ingest drain supervisor (R-F2507 + R-F2564). Started once at boot
    (main.py, one process under WEB_CONCURRENCY=1). No-op unless
    ARIA_BRAIN_QUEUE_ENABLED=1. Connects the queue db ONCE, then runs
    ARIA_BRAIN_QUEUE_WORKERS drain workers (default 1 = byte-identical to the
    original single-worker design).

    Why N workers are safe now (per the brain-scaling design doc P1.1): the
    hot/cold writer split (P0.1) is live, so the ingest/knowledge writes land on
    the COLD writer, isolated from the hot request path. All workers share ONE
    concurrency semaphore (ARIA_BRAIN_QUEUE_DRAIN_CONCURRENCY), so raising the
    worker count adds pull/claim parallelism: a worker blocked awaiting its batch's
    applies no longer stops OTHER workers from claiming + draining the next batch.
    Total apply load is NOT multiplied by the worker count — all workers share ONE
    apply semaphore, so throughput is dialed explicitly via the concurrency knob
    (a slow absorb still consumes a shared slot; the cap is global by design).
    dequeue_batch's atomic claim + lock-guarded mark_done/mark_failed (R-F2564)
    guarantee no row is applied twice across workers."""
    if not _BRAIN_QUEUE_ENABLED:
        return
    from . import brain_ingest_queue as _biq
    try:
        await _biq.connect()          # connect ONCE — workers share this handle
    except Exception as e:
        logger.error(
            "[R-F2507] brain_ingest_queue connect failed — drain loop NOT starting; "
            "absorbs will fall back to memory_wal (never lost): %s", e)
        return
    _batch = max(1, int(os.environ.get("ARIA_BRAIN_QUEUE_BATCH", "12")))
    _drain_conc = max(1, int(os.environ.get("ARIA_BRAIN_QUEUE_DRAIN_CONCURRENCY", "3")))
    _workers = max(1, int(os.environ.get("ARIA_BRAIN_QUEUE_WORKERS", "1")))
    _sem = asyncio.Semaphore(_drain_conc)   # SHARED across all workers (total-apply cap)
    logger.info(
        "[R-F2507/R-F2564] brain_ingest_queue drain started: %d worker(s), batch=%d, "
        "shared apply concurrency=%d", _workers, _batch, _drain_conc)

    if _workers == 1:
        # Unchanged single-worker path (no gather overhead).
        await _drain_worker_body(0, _sem, _batch)
    else:
        # Fan out N workers on the ONE shared connection + shared sem. The worker body
        # only exits via CancelledError (its while-loop catches every other exception),
        # so in practice this gather returns only at shutdown. return_exceptions=True is
        # defence-in-depth: were a worker to die abnormally, the surviving workers keep
        # draining and the supervisor does NOT respawn the whole loop (a respawn would
        # re-run connect()->recover_stuck() against rows the live workers still hold,
        # risking a double-apply). mark_done/mark_failed run under the queue write lock
        # (R-F2564) so no claim/mark interleave corrupts the shared connection.
        await asyncio.gather(*[
            _drain_worker_body(i, _sem, _batch) for i in range(_workers)
        ], return_exceptions=True)


@fail_wire(module="brain_hook", gap_type="engine_failure")
async def record_signal(
    module: str,
    *,
    success: bool = True,
    summary: str = "",
) -> dict:
    """Lightweight signal recording — skips expensive tiers (mastery,
    knowledge, neural). Use this for HIGH-FREQUENCY telemetry modules
    (web_integrity_agent, cost_tracker, _snapshot_throttle, etc.) that
    call absorb on every tick but don't produce genuine knowledge.

    R-F1598: these modules were calling full absorb() which triggered
    expensive background tiers (mastery + knowledge + neural) that took
    20-30s under load, tripping the circuit breaker. This path records
    the signal counter only — no tier processing, no circuit breaker
    check, no WAL. The signal still appears in brain stats.

    Returns a simple dict with the signal key.
    """
    if not BRAIN_HOOK_ENABLED:
        return {"skipped": True, "reason": "disabled"}
    # R-F1664: _record_signal is async — it was being called WITHOUT await, so
    # the signal was never recorded (the coroutine was created and discarded,
    # emitting "coroutine never awaited"). Cure 1 routes the high-frequency
    # wire_success telemetry through this path, so it MUST actually record or
    # those modules go dark (violating §21a). Await it.
    await _record_signal(module, success=success)
    return {"recorded": True, "module": module, "success": success}


@fail_wire(module="brain_hook", gap_type="engine_failure")
async def observe_self_event(
    event: str,
    detail: Any = "",
    *,
    success: bool = False,
    gap_type: Optional[str] = "self_runtime",
) -> dict:
    """R-F921 (2026-05-26) — Rule Zero self-observation channel.

    The mechanism by which ARIA SEES her own runtime failures and notable
    self-events: chat timeouts, suppressed stale fallbacks, hallucination-guard
    trips, self-state questions, self-monitor heartbeats. Each routes through
    absorb(module="self_monitor", ...). When success=False it records a
    capability gap (gap_type), so the event lands in the SAME ledger that
    gap_detector → self_coder reads — i.e. it becomes a thing she can reason
    over and propose a fix for, instead of an invisible failure.

    Why this exists: the 2026-05-26 WhatsApp incident — ARIA timed out on a
    URL query and then FABRICATED a diagnostic about her own state, precisely
    because that failure never reached her brain as an observable signal.
    Rule Zero (§20): ARIA sees, hears and knows everything — including what
    happens to her own operating system. Best-effort; never raises.

    Args:
        event:    short stable slug, e.g. "chat_async_job_failed".
        detail:   str or JSON-serialisable dict with the specifics.
        success:  False (default) files a capability gap; True is a healthy
                  heartbeat / positive observation (no gap).
        gap_type: capability-gap bucket when success=False (e.g. "timeout",
                  "self_runtime", "hallucination_guard").
    """
    try:
        import json as _json
        _d = detail if isinstance(detail, str) else _json.dumps(detail, default=str)[:1500]
        return await absorb(
            module="self_monitor",
            summary=f"self-observed: {event}",
            detail=_d or event,
            success=success,
            gap_type=(gap_type if not success else None),
            gap_detail=(f"{event}: {_d}"[:500] if not success else None),
            source_id=f"self_monitor:{event}",
            confidence="CONFIRMED",
        )
    except Exception as e:
        try:
            logger.debug("observe_self_event(%s) failed (non-fatal): %s", event, e)
        except Exception:
            pass
        return {"skipped": True, "reason": "observe_self_event_error"}


# =============================================================================
# SIGNAL TRACKING + HEALTH MONITORING
# =============================================================================

_STATS_KEY = "crucix:aria:brain_hook:stats"
_ALERT_STALE_HOURS = 24  # alert if module hasn't sent a signal in 24h

# ── R-F2157: write-coalescing accumulator for brain_hook stats ──────────────
# ROOT CAUSE (live 2026-06-30): _record_signal()/_record_gate_skip() fired a
# FULL read-modify-write of the single hot key _STATS_KEY on EVERY absorb.
# Under ARIA's own concurrency (autonomous loops + chat + brain_hook_bg) that
# produced two compounding failures against state_store's single aiosqlite
# connection / global lock:
#   1. THUNDERING HERD on the read — N concurrent absorbs each await
#      rs.get_json(_STATS_KEY); the live logs showed 14 simultaneous
#      "state_store.get(crucix:aria:brain_hook:stats) timed out after 5s".
#   2. WRITE AMPLIFICATION — each absorb rewrote the whole (growing) stats
#      dict, saturating the 2000-deep write queue ("write queue full —
#      accept data loss") and tripping the brain_hook circuit (p95=31s).
# This starved the USER-FACING read path → web proxy timeouts + WA chat 503s.
#
# FIX: accumulate signal/gate-skip deltas in-process (pure dict increments,
# ZERO I/O — atomic between awaits in single-threaded asyncio) and flush the
# coalesced result to _STATS_KEY at most once per _STATS_FLUSH_INTERVAL_S.
# Thousands of per-absorb RMW ops collapse into one periodic RMW. No new
# background task (boot-path risk) — the flush is time-gated and triggered
# opportunistically from the recorders, with a single in-flight guard so only
# one coroutine touches the DB per interval. get_stats() force-flushes first
# (itself 30s-cached) so the dashboard never reads stale-by-more-than-a-flush.
_STATS_FLUSH_INTERVAL_S = float(os.getenv("ARIA_BRAIN_STATS_FLUSH_INTERVAL_S", "15"))
_stats_flush_lock = asyncio.Lock()
_stats_last_flush: float = 0.0
# Pending deltas (same shapes as the persisted dict, but holding only the
# un-flushed increments). Mutated WITHOUT await → atomic in asyncio.
_pending_modules: dict[str, dict] = {}      # module -> {total,success,fail,skip,last_signal_at,first_signal_at}
_pending_global_total: int = 0
_pending_sectors: dict[str, dict] = {}      # sector -> {total,success,fail,last_signal_at,first_signal_at,by_module}
_pending_gate_skips: dict[str, dict] = {}   # reason -> {total,by_module,last_at}


def _merge_pending_modules_into(dst: dict, src: dict) -> None:
    """Additively fold per-module signal deltas `src` into `dst` (the
    persisted stats dict OR a pending dict during fold-back). Counters add;
    timestamps take the max; first_signal_at takes the min (earliest)."""
    for module, d in src.items():
        m = dst.get(module)
        if not isinstance(m, dict):
            m = {"total": 0, "success": 0, "fail": 0, "skip": 0,
                 "last_signal_at": 0, "first_signal_at": d.get("first_signal_at", 0)}
            dst[module] = m
        m["total"] = m.get("total", 0) + d.get("total", 0)
        m["success"] = m.get("success", 0) + d.get("success", 0)
        m["fail"] = m.get("fail", 0) + d.get("fail", 0)
        m["skip"] = m.get("skip", 0) + d.get("skip", 0)
        m["last_signal_at"] = max(m.get("last_signal_at", 0), d.get("last_signal_at", 0))
        _fa = d.get("first_signal_at", 0)
        if _fa and (not m.get("first_signal_at") or _fa < m["first_signal_at"]):
            m["first_signal_at"] = _fa


def _merge_pending_sectors_into(dst: dict, src: dict) -> None:
    for sector, d in src.items():
        sb = dst.get(sector)
        if not isinstance(sb, dict):
            sb = {"total": 0, "success": 0, "fail": 0,
                  "first_signal_at": d.get("first_signal_at", 0),
                  "last_signal_at": 0, "by_module": {}}
            dst[sector] = sb
        sb["total"] = sb.get("total", 0) + d.get("total", 0)
        sb["success"] = sb.get("success", 0) + d.get("success", 0)
        sb["fail"] = sb.get("fail", 0) + d.get("fail", 0)
        sb["last_signal_at"] = max(sb.get("last_signal_at", 0), d.get("last_signal_at", 0))
        _fa = d.get("first_signal_at", 0)
        if _fa and (not sb.get("first_signal_at") or _fa < sb["first_signal_at"]):
            sb["first_signal_at"] = _fa
        bm = sb.setdefault("by_module", {})
        for mod, c in (d.get("by_module") or {}).items():
            bm[mod] = bm.get(mod, 0) + c


def _merge_pending_gate_skips_into(dst: dict, src: dict) -> None:
    for reason, d in src.items():
        b = dst.get(reason)
        if not isinstance(b, dict):
            b = {"total": 0, "by_module": {}, "last_at": 0}
            dst[reason] = b
        b["total"] = b.get("total", 0) + d.get("total", 0)
        b["last_at"] = max(b.get("last_at", 0), d.get("last_at", 0))
        bm = b.setdefault("by_module", {})
        for mod, c in (d.get("by_module") or {}).items():
            bm[mod] = bm.get(mod, 0) + c


async def _flush_stats_pending(force: bool = False) -> bool:
    """Coalesce all in-process pending deltas into a single read-modify-write
    of _STATS_KEY. Time-gated to once per _STATS_FLUSH_INTERVAL_S unless
    `force`. One in-flight flush at a time (lock); other callers just keep
    accumulating and ride the next flush. Best-effort — on DB failure the
    snapshot is folded BACK into pending so no counts are lost. Returns True
    if a flush actually wrote to the DB."""
    global _stats_last_flush, _pending_modules, _pending_global_total
    global _pending_sectors, _pending_gate_skips
    now = time.time()
    if not force and (now - _stats_last_flush) < _STATS_FLUSH_INTERVAL_S:
        return False
    # Nothing pending → just advance the clock cheaply (no DB hit).
    if not (_pending_modules or _pending_global_total or _pending_sectors
            or _pending_gate_skips):
        _stats_last_flush = now
        return False
    if _stats_flush_lock.locked():
        return False  # another flush is draining; our deltas go with it / next
    async with _stats_flush_lock:
        # Re-check the gate after acquiring (another flush may have just run).
        now = time.time()
        if not force and (now - _stats_last_flush) < _STATS_FLUSH_INTERVAL_S:
            return False
        # Atomically snapshot + reset pending (no await between these lines).
        snap_modules = _pending_modules
        snap_global = _pending_global_total
        snap_sectors = _pending_sectors
        snap_gate = _pending_gate_skips
        _pending_modules = {}
        _pending_global_total = 0
        _pending_sectors = {}
        _pending_gate_skips = {}
        try:
            from . import redis_store as rs
            stats = await rs.get_json(_STATS_KEY) or {}
            _merge_pending_modules_into(stats, snap_modules)
            if snap_global:
                g = stats.setdefault("_global", {"total": 0, "started_at": now})
                g["total"] = g.get("total", 0) + snap_global
            if snap_sectors:
                _merge_pending_sectors_into(stats.setdefault("_by_sector", {}), snap_sectors)
            if snap_gate:
                _merge_pending_gate_skips_into(stats.setdefault("_gate_skips", {}), snap_gate)
            await rs.set_json(_STATS_KEY, stats, ex=30 * 86400)
            _stats_last_flush = now
            return True
        except Exception as e:
            # Fold the snapshot back so the next flush retries it — telemetry
            # accuracy preserved without ever blocking/raising into absorb.
            _merge_pending_modules_into(_pending_modules, snap_modules)
            _pending_global_total += snap_global
            _merge_pending_sectors_into(_pending_sectors, snap_sectors)
            _merge_pending_gate_skips_into(_pending_gate_skips, snap_gate)
            logger.debug("brain_hook stats flush failed (folded back): %s", e)
            return False

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
# F69 fix 2026-04-28: bumped default from 1500ms to 2000ms after live
# production surfaced a burst pattern that legitimately ran p95=1691ms
# during the 10:00:08 multi-region WEEKLY-COMP-SANCTIONS absorb (11
# brain_hook calls in 8s through cross_regional_correlator + 7
# regional knowledge modules). 1500ms tripped the breaker by 191ms —
# just barely over — and filed a HIGH/operator_action pending action
# that auto-resolved on cooldown. Pure noise.
#
# F107 fix 2026-04-30: bumped 2000ms → 3500ms after F94 (knowledge → disk)
# shifted the absorb-path bottleneck from Redis SET to neural_memory
# encode. 7 parallel knowledge-module absorbs each call
# neural_memory.learn_from_text() → model.encode(); sentence-transformers
# under torch holds the GIL, so encodes serialize in the executor queue.
# Live observation 2026-04-30 10:03:13: 7 absorbs in 3ms wall-time pushed
# absorb p95 to 2084ms, tripping the 2000ms breaker for 60s of cooldown
# before auto-recovery. Same pattern as F69 — legitimate burst, not a
# downstream outage. Real Redis/Chroma outages still produce p95 > 6000ms
# within seconds, well clear of the threshold. Operator can still override
# via ARIA_BRAIN_LATENCY_TRIP_MS env var.
#
# R-F795 (2026-05-22) — per-tier wall-time ceiling. Defensive bound on
# absorb() wall-time when a downstream tier wedges. Live evidence
# 2026-05-22 15:59-16:04 UTC: brain_hook absorb p95 hit 1,250,404ms
# (20m 51s) on absorb(web_atlas); breaker tripped 3 times in 5 min;
# Watchlist re-screen wall-time hit 19m 58s. Root cause: sentence-
# transformers model.encode() is GIL-serialised, so concurrent
# absorbs from sweep/web_atlas/sanctions queue serially through one
# encoder. Per-tier timeout caps each tier at this value so a single
# absorb's worst-case wall-time is ~3 × _TIER_TIMEOUT_S (mastery +
# knowledge + neural). On timeout the tier is marked failed (signal
# counter still increments) and absorb returns. Operator-tunable via
# env var; default 5000 (5s per tier → ~15s worst-case absorb wall
# vs the live 20-min wedges). Set ARIA_BRAIN_ABSORB_TIER_TIMEOUT_MS=0
# to disable the timeout (legacy unbounded behaviour) if a slow but
# necessary batch needs to complete.
_TIER_TIMEOUT_S = (
    float(os.environ.get("ARIA_BRAIN_ABSORB_TIER_TIMEOUT_MS", "15000")) / 1000.0  # R-F1512: raised from 8000 to 15000. The boot-time cold-edges sweep (R-F1512) needs time to offload existing oversized neurons on first load. Once swept, subsequent absorbs are fast.
)

# R-F968 (2026-05-28) — raised 3500→6000. The trip threshold MUST sit
# above the per-tier wall-time ceiling (_TIER_TIMEOUT_S, R-F795), otherwise
# a SINGLE slow-but-healthy tier hitting its allowed budget trips the
# breaker. R-F932 set the live tier timeout to 3500ms — equal to the old
# 3500ms trip threshold — so one slow neural absorb pushed p95 to its
# configured ceiling and the breaker flapped TRIPPED↔CLOSED every ~60s
# (live 2026-05-28 16:59-17:00: p95=3665-4205ms reason=absorb(signal_
# generator), 0 actual loop starvation). 6000ms clears the live 3500ms
# tier ceiling + the observed ~4200ms steady p95 with margin, while a
# genuine multi-tier wedge / loop starvation (the 11.6s R-F703 stalls)
# still spikes p95 well above 6000ms and trips correctly.
# R-F1296 (2026-06-01) — self-maintaining invariant: trip threshold always
# clears the per-tier ceiling + 2000ms margin. The env override (if set) acts
# as a FLOOR, so the invariant can never silently drift apart again.
_LATENCY_TRIP_MS = max(
    int(os.environ.get("ARIA_BRAIN_LATENCY_TRIP_MS", "6000")),
    int(_TIER_TIMEOUT_S * 1000) + 2000,  # always clear the tier ceiling + margin
)

# R-F1505: per-module latency thresholds. Some modules (aria_coder, deep_researcher)
# do heavy LLM work that naturally takes 30-60s. The global 6s threshold would trip
# on every coder cycle, causing false breaker opens. These overrides let slow-but-
# correct modules have a higher threshold. Keyed by module name prefix.
_MODULE_LATENCY_OVERRIDES: dict[str, int] = {
    "aria_coder": 120000,        # 120s — LLM code generation is slow
    "autonomous.coder_entrypoint": 120000,
    "deep_researcher": 120000,
    "self_coder": 120000,
    "autonomous_engine": 60000,  # 60s — task execution
    "self_improve": 60000,
    # R-F1598: high-frequency hot-path callers. These modules call absorb
    # on every tick/cycle and share the state_store backend. Under load,
    # their p95 naturally exceeds the 6s default threshold. The circuit
    # breaker was tripping every 2 minutes on these, skipping expensive
    # brain tiers for ALL modules — not just the hot-path ones.
    # R-F2007: adversarial_challenge is a WEEKLY self-eval that absorbs a burst
    # of results at once; under the boot/weekly storm its neural-tier p95 hit
    # ~37s (live 2026-06-27 10:51), tripping the GLOBAL 6s breaker and skipping
    # expensive tiers for EVERY module until cooldown. It is slow-but-correct and
    # infrequent — give it headroom like the other bursty/slow modules so its
    # latency never degrades everyone else's absorbs. (Facts are still WAL-safe.)
    "adversarial_challenge": 60000,  # 60s — weekly burst self-eval
    "web_integrity_agent": 30000,   # 30s — probes endpoints every 60s
    "cost_tracker": 30000,          # 30s — records cost on every LLM call
    "_snapshot_throttle": 30000,    # 30s — snapshot on every absorb
    "signal_generator": 30000,      # 30s — generates signals periodically
    "llm_response_cache": 30000,    # 30s — caches LLM responses
    "llm_request_queue": 30000,     # 30s — queues LLM requests
}

# R-F799 (2026-05-22) — concurrency cap on absorb's expensive tiers.
# R-F795 bounded each absorb's per-tier wall-time. This R-number adds
# a per-process concurrency limiter so a flood of concurrent absorbs
# (sweep/web_atlas/sanctions/tender_monitor all firing inside the
# same 30s window) can't all race for the (already-serialised) encode
# lock at semantic_search.py:_safe_encode. Default 2 concurrent
# expensive-tier sections; semaphore acquire is bounded by a short
# timeout (default 0.5s) so contention triggers fail-fast load
# shedding rather than queue-buildup. On acquire timeout the tier
# section is skipped (signal counter + _record_signal still fire so
# observability is preserved). Set ARIA_BRAIN_ABSORB_CONCURRENCY=0
# to disable the cap entirely (legacy unbounded behaviour).
_ABSORB_CONCURRENCY = int(os.environ.get("ARIA_BRAIN_ABSORB_CONCURRENCY", "8"))  # R-F1512: raised from 4 to 8. The neural cold-storage offload (R-F1512) reduced per-absorb neural tier time, so 8 concurrent absorbs no longer saturates the thread pool. The 4-vCPU Fly machine handles 8 parallel tier processes without GIL starvation.
_ABSORB_SEM_ACQUIRE_TIMEOUT_S = (
    float(os.environ.get("ARIA_BRAIN_ABSORB_SEM_ACQUIRE_MS", "2000")) / 1000.0  # R-F1512: raised from 500ms to 2000ms. With 8 concurrent slots, a 2s acquire window lets brief bursts drain without shedding to WAL.
)
_absorb_concurrency_sem: Optional[asyncio.Semaphore] = None
_absorb_concurrency_loop: Optional[asyncio.AbstractEventLoop] = None

# R-F2507 — durable brain-ingest queue. When ON, absorb() ENQUEUES the ingest
# payload to a SEPARATE SQLite queue file (one cheap INSERT, no state_store
# contention) and a SINGLE drain worker (brain_queue_drain_loop, started at boot)
# applies it later at BOUNDED concurrency — replacing the unbounded per-absorb
# create_task burst that saturates the single state_store writer (the DD-latency
# ceiling). Default OFF = byte-identical to the legacy in-memory path.
_BRAIN_QUEUE_ENABLED = os.environ.get("ARIA_BRAIN_QUEUE_ENABLED", "0") == "1"

# R-F1665 (wedge cure 2): a SEPARATE, smaller concurrency lane for the NEURAL
# tier. The neural encode (concept extraction + edge formation + GIL-bound
# neural_memory persist) can run up to the tier timeout (15s). Running it INSIDE
# the main 8-slot absorb semaphore meant ≤8 slow neural encodes occupied every
# slot, so the fast mastery+knowledge tiers queued behind them and absorb p95
# climbed to 22-44s (the wedge). Gating neural on its own small sem (default 2,
# matched to GIL-bound encode) lets the main 8 slots drain after the cheap
# durable tiers, while neural work is still bounded on its own lane.
_NEURAL_CONCURRENCY = int(os.environ.get("ARIA_BRAIN_NEURAL_CONCURRENCY", "2"))
_neural_concurrency_sem: Optional[asyncio.Semaphore] = None
_neural_concurrency_loop: Optional[asyncio.AbstractEventLoop] = None

# R-F1342 (Pillar-1 invariant): cap the COUNT of in-flight absorb background
# tasks. Each task's work is already bounded (tier timeouts, concurrency sem,
# circuit breaker), but NOTHING capped how many absorb_tiers_bg tasks could
# pile up. Under autonomous burst (228 call sites + 96 tasks) the backlog grew
# until the p95=24s breaker trip that helped wedge the loop on 2026-06-05.
# Over the cap we skip the expensive tiers (the signal is still recorded, so
# observability is preserved) instead of spawning unboundedly.
_MAX_PENDING_ABSORB = int(os.environ.get("ARIA_MAX_PENDING_ABSORB", "250"))
_pending_absorb = 0
_dropped_absorb = 0
_absorb_background_tasks: set[asyncio.Task] = set()


def _absorb_backlog_stats() -> dict:
    """Surface backlog health for diagnostics / health endpoints."""
    return {"pending": _pending_absorb, "dropped": _dropped_absorb,
            "max_pending": _MAX_PENDING_ABSORB}


def _dec_pending_absorb(_task: "asyncio.Task") -> None:
    global _pending_absorb
    _pending_absorb = max(0, _pending_absorb - 1)
    _absorb_background_tasks.discard(_task)
    if _task.cancelled():
        return
    try:
        error = _task.exception()
    except (asyncio.CancelledError, asyncio.InvalidStateError):
        return
    if error is not None:
        logger.error("brain absorption background task failed: %s", error)


@fail_wire(module="brain_hook", gap_type="engine_failure")
async def shutdown_background_tasks() -> int:
    """Cancel and await every in-process brain absorption task.

    Returns the number of tasks owned at shutdown. Completed tasks remove
    themselves through ``_dec_pending_absorb`` and therefore are not counted.
    """
    tasks = tuple(_absorb_background_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    return len(tasks)


def _get_absorb_concurrency_sem() -> Optional[asyncio.Semaphore]:
    """Lazy-init the per-process concurrency semaphore. Returns None
    when the cap is disabled (ARIA_BRAIN_ABSORB_CONCURRENCY=0)."""
    global _absorb_concurrency_sem, _absorb_concurrency_loop
    if _ABSORB_CONCURRENCY <= 0:
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if _absorb_concurrency_sem is None or (
        loop is not None and _absorb_concurrency_loop is not loop
    ):
        _absorb_concurrency_sem = asyncio.Semaphore(_ABSORB_CONCURRENCY)
        _absorb_concurrency_loop = loop
    return _absorb_concurrency_sem


def _get_neural_concurrency_sem() -> Optional[asyncio.Semaphore]:
    """R-F1665: lazy-init the SEPARATE neural-tier semaphore. Returns None
    when disabled (ARIA_BRAIN_NEURAL_CONCURRENCY=0). Keeps the slow neural
    encode off the main absorb concurrency lane (wedge cure 2)."""
    global _neural_concurrency_sem, _neural_concurrency_loop
    if _NEURAL_CONCURRENCY <= 0:
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if _neural_concurrency_sem is None or (
        loop is not None and _neural_concurrency_loop is not loop
    ):
        _neural_concurrency_sem = asyncio.Semaphore(_NEURAL_CONCURRENCY)
        _neural_concurrency_loop = loop
    return _neural_concurrency_sem
_LATENCY_WINDOW = 50
_TRIP_CONSECUTIVE = 3
_COOLDOWN_S = 60
_BREAKER_KEY = "crucix:aria:brain_hook:breaker"

# F55 fix 2026-04-28: minimum sample count before the breaker can trip.
# At boot the absorb() path runs through a cold sentence-transformer
# load + 3 expensive tiers; the first 2-3 calls easily clock 3-4s each
# and a 5-sample p95 is just the cold-start outlier. The breaker then
# trips on a tiny non-representative window, files a HIGH/operator_action
# pending action, and auto-resolves on cooldown — pure noise during the
# boot phase. Require ≥ this many recorded latencies before evaluating
# the trip condition.
#
# Note: this gate only applies to the COLD-START warmup. Once the window
# has saturated once (process has been hot), `_warmup_complete` flips
# True and stays True for the process lifetime — so after a cooldown
# half-open clear the breaker can re-trip immediately if downstream is
# still slow (the regression the brain_hook circuit tests guard against).
_MIN_SAMPLES_BEFORE_TRIP = int(os.environ.get("ARIA_BRAIN_MIN_SAMPLES_BEFORE_TRIP", "10"))
_warmup_complete: bool = False

_recent_latencies_ms: list[float] = []
_breaker_state = {
    "open": False,
    "tripped_at": 0.0,
    "consecutive_high": 0,
    "trips_total": 0,
    "drops_total": 0,
    "last_trip_reason": "",
    # R-F790 (2026-05-21): one HIGH ticket per open episode. Live evidence
    # 2026-05-21 16:13–16:25: brain_hook tripped 4× in 11 minutes on encode
    # latency, each trip filed a fresh HIGH pending_action, each cooldown
    # auto-resolved the prior one. Operator saw 6 churning tickets instead
    # of one persistent "wedge is recurring" signal. Now: file at most one
    # ticket per trip→close cycle; reset on close so the NEXT genuine
    # episode files a fresh one.
    "ticket_filed_this_episode": False,
    # R-F858 (2026-05-24): cross-episode ticket cooldown. A flapping wedge
    # trips→closes→re-trips every 60-120s; each re-trip is a fresh EPISODE, so
    # the per-episode flag alone files a new HIGH ticket on every flap (operator
    # saw 3+ HIGH tickets in minutes). last_ticket_at coalesces a flapping
    # breaker into ONE "wedge recurring" ticket per _TICKET_COOLDOWN_S window.
    "last_ticket_at": 0.0,
}

# R-F858 — suppress repeat breaker tickets within this window (default 30 min).
_TICKET_COOLDOWN_S = float(os.environ.get("ARIA_BRAIN_TICKET_COOLDOWN_S", "1800"))


def _should_file_ticket() -> bool:
    """R-F790: at most one HIGH ticket per OPEN episode. R-F858: AND a
    cross-episode cooldown so a flapping breaker (trip→close→re-trip every
    60-120s) doesn't file a fresh ticket on every flap — they coalesce into one
    'wedge recurring' signal per _TICKET_COOLDOWN_S. Factored out for tests."""
    if _breaker_state.get("ticket_filed_this_episode", False):
        return False
    _last = _breaker_state.get("last_ticket_at", 0.0)
    if _last and (time.time() - _last) < _TICKET_COOLDOWN_S:
        return False
    return True


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


def _module_trip_threshold(module: str = "") -> int:
    """R-F1505: return the effective trip threshold for a module.

    Checks _MODULE_LATENCY_OVERRIDES for a matching prefix. Falls back
    to the global _LATENCY_TRIP_MS if no override matches.
    """
    if module:
        for prefix, threshold in _MODULE_LATENCY_OVERRIDES.items():
            if module.startswith(prefix):
                return threshold
    return _LATENCY_TRIP_MS


def _maybe_trip_breaker(reason: str, module: str = "") -> None:
    """Open the circuit if p95 has been over threshold _TRIP_CONSECUTIVE
    times in a row. Idempotent — safe to call on every absorb.

    R-F1505: uses per-module threshold when module is provided, so slow-
    but-correct modules (aria_coder, deep_researcher) don't false-trip.
    """
    global _warmup_complete
    if _breaker_state["open"]:
        return
    # F55 fix 2026-04-28: cold-start warmup gate. Only applied while
    # the process has not yet seen a saturated window — once it has,
    # _warmup_complete latches True and post-cooldown half-opens can
    # re-trip immediately on a fresh slow sample, as the existing tests
    # require.
    if not _warmup_complete:
        if len(_recent_latencies_ms) < _MIN_SAMPLES_BEFORE_TRIP:
            return
        _warmup_complete = True
    p95 = _p95_latency_ms()
    threshold = _module_trip_threshold(module)
    if p95 > threshold:
        _breaker_state["consecutive_high"] += 1
        if _breaker_state["consecutive_high"] >= _TRIP_CONSECUTIVE:
            _breaker_state["open"] = True
            _breaker_state["tripped_at"] = time.time()
            _breaker_state["trips_total"] += 1
            _breaker_state["last_trip_reason"] = (
                f"{reason} — p95={p95:.0f}ms over {threshold}ms threshold"
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
            #
            # R-F790: dedup at most one ticket per open episode. The flag
            # is set unconditionally (before the task is created) so a
            # transient pending_actions/Redis failure inside _alert() does
            # not cause a second ticket to be filed on the next trip in
            # the same episode. Quiet skipping is the right behaviour here
            # — operator already sees the WARNING log line above.
            if _should_file_ticket():
                _breaker_state["ticket_filed_this_episode"] = True
                _breaker_state["last_ticket_at"] = time.time()  # R-F858 cross-episode cooldown
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
    # F55 2026-04-28: by the time we're closing the breaker, the process
    # is past cold-start. Latch warmup_complete=True so the post-close
    # half-open probe can re-trip without waiting for a fresh window of
    # MIN_SAMPLES samples. Cold-start protection only matters once per
    # process lifetime.
    global _warmup_complete
    _warmup_complete = True
    # R-F790 (2026-05-21): reset the per-episode ticket flag so the NEXT
    # trip (a fresh wedge episode) files a new HIGH ticket. Within this
    # closing episode the flag stayed True so we filed at most once.
    _breaker_state["ticket_filed_this_episode"] = False
    logger.info(
        "[brain_hook] CIRCUIT CLOSED — cooldown elapsed, "
        "stale p95 was %.0fms (trip threshold %dms). "
        "Clearing latency window and resuming full absorb path; "
        "_maybe_trip_breaker will re-trip naturally if downstream is still slow.",
        stale_p95, _LATENCY_TRIP_MS,
    )
    # R-F790: do NOT auto-resolve the pending_action on cooldown elapse.
    # Live evidence 2026-05-21 16:13–16:25: 4 trips in 11 minutes, each
    # cooldown closed the prior ticket as "satisfied" 6s before the next
    # re-trip filed a fresh one. Cooldown elapsing ≠ root cause cleared
    # — it's just the breaker's automatic half-open probe. Severity HIGH
    # / resolver_kind=operator_action explicitly means operator closes
    # the ticket when they're satisfied the wedge is fixed. Ticket
    # dedup (above, in _maybe_trip_breaker) prevents the stale-alert
    # accumulation that motivated the original auto-resolve — at most
    # one open brain_hook ticket exists per wedge episode.


@fail_wire(module="brain_hook", gap_type="engine_failure")
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
    # R-F2157: accumulate in-process (no per-call DB RMW — see the
    # write-coalescing accumulator above) and ride the periodic flush.
    try:
        bucket = _pending_gate_skips.setdefault(
            reason, {"total": 0, "by_module": {}, "last_at": 0})
        bucket["total"] += 1
        bucket["by_module"][module] = bucket["by_module"].get(module, 0) + 1
        bucket["last_at"] = time.time()
        await _flush_stats_pending()
    except Exception:
        pass  # gate-skip stats must never block absorb


async def _record_signal(module: str, success: bool, sector: str = "", skipped: bool = False) -> None:
    """Record a signal in Redis for per-module tracking.

    R-F56: when `sector` is non-empty, also bumps a parallel
    per-sector counter so the dashboard / get_stats surface can
    answer "how many compliance-officer absorbs this week vs broker".

    R-F1529: added `skipped` parameter. When True, the signal is recorded
    as a skip (not success, not failure). This prevents modules like
    agent_registry from appearing as 32% success when they were actually
    100% successful — the "failures" were just brain signal delivery being
    rate-limited by the concurrency cap.
    """
    # R-F2157: accumulate in-process (pure dict increments, no await) and
    # ride the coalesced periodic flush instead of a full DB RMW per absorb.
    global _pending_global_total
    try:
        now = time.time()

        m = _pending_modules.get(module)
        if m is None:
            m = {"total": 0, "success": 0, "fail": 0, "skip": 0,
                 "last_signal_at": 0, "first_signal_at": now}
            _pending_modules[module] = m
        m["total"] += 1
        if skipped:
            m["skip"] = m.get("skip", 0) + 1
        elif success:
            m["success"] += 1
        else:
            m["fail"] += 1
        m["last_signal_at"] = now

        # Global counter
        _pending_global_total += 1

        # R-F56 — per-sector buckets. Sector is validated against the known
        # persona keys to prevent unbounded bucket growth from typos.
        if sector and sector in _ALLOWED_SECTORS:
            sb = _pending_sectors.get(sector)
            if sb is None:
                sb = {"total": 0, "success": 0, "fail": 0,
                      "first_signal_at": now, "last_signal_at": 0,
                      "by_module": {}}
                _pending_sectors[sector] = sb
            sb["total"] += 1
            if success:
                sb["success"] += 1
            else:
                sb["fail"] += 1
            sb["last_signal_at"] = now
            sb["by_module"][module] = sb["by_module"].get(module, 0) + 1

        # Time-gated coalesced flush (one DB RMW per interval, not per absorb).
        await _flush_stats_pending()
    except Exception:
        pass  # stats recording must never break absorb


# R-F56: closed set of sector keys we'll bucket telemetry against. Mirrors
# DEFAULT_PERSONA + the keys in personas/__init__.py:_OVERLAYS plus an
# explicit "" → no-bucket sentinel and "unknown" for anything we can't
# resolve. Keeps the Redis stats shape bounded.
_ALLOWED_SECTORS = frozenset({
    "broker", "oem_export", "government_acquisition",
    "compliance", "banking_insurance", "journalist",
    "unknown",
})


# R-F56: chat-turn context. Set at the top of aria_chat / aria_chat_stream
# from the request body's user_id + sector; read by brain_hook.absorb when
# the caller didn't pass them explicitly. Means downstream modules
# (deep_research, dd_orchestrator, watchlist re-screen, etc.) get correct
# per-customer tagging without each one having to thread user_id through.
# Same pattern as cost_tracker._current_feature.
import contextvars as _cv
_chat_user_ctx: _cv.ContextVar[tuple[str, str]] = _cv.ContextVar(
    "_chat_user_ctx", default=("", "")
)


@fail_wire(module="brain_hook", gap_type="engine_failure")
def set_chat_context(user_id: str, sector: str) -> _cv.Token:
    """Set the per-turn (user_id, sector) context. Call at chat entry;
    pair with `reset_chat_context(token)` in a finally block.
    Returns the contextvars Token caller passes to reset."""
    return _chat_user_ctx.set((user_id or "", sector or ""))


@fail_wire(module="brain_hook", gap_type="engine_failure")
def reset_chat_context(token: _cv.Token) -> None:
    try:
        _chat_user_ctx.reset(token)
    except Exception:
        pass


@fail_wire(module="brain_hook", gap_type="engine_failure")
def get_chat_context() -> tuple[str, str]:
    """Return the current chat (user_id, sector) — ('', '') when no
    chat-turn context is active (autonomous / sweep / dev calls)."""
    try:
        return _chat_user_ctx.get()
    except Exception:
        return ("", "")


# R-F1599: in-memory cache for get_stats() to avoid recomputing the full
# module map from Redis on every call. Under load (deploy cycles, concurrent
# requests), the Redis read + 217-module iteration takes 20-30s. The cache
# serves stale data for up to 30s, which is fine for a stats endpoint.
_stats_cache: dict = {}
_stats_cache_at: float = 0
_stats_cache_ttl: float = 30.0


# -- R-F4169 (C-183) -- THE ONE READING OF `success_rate` ---------------------
#
# R-F3934 (C-37) and R-F3936 measured that `success_rate: 0.0` means two things
# it cannot tell apart, and published `only_failures_recorded` /
# `no_measurable_signals` so a reader would not have to guess. Nothing read
# them. Measured live 2026-08-19: of 255 modules, TWELVE read 0.0 and ALL
# TWELVE carried a flag -- while `routes/aria.py::_execute_tool`'s `meta_query`
# branch printed every one as `success=0.00` into an LLM prompt under the
# instruction "Report these stats to the user EXACTLY as shown".
#
# The interpretation lives HERE, beside the flags that produce it, so the three
# render sites cannot drift and a fourth cannot invent its own.
#
# WHAT THIS DELIBERATELY DOES NOT DO: resolve the ambiguity. `only_failures_
# recorded` cannot distinguish a failure-only WIRE (healthy, unmeasurable --
# `llm/openai_compat.py` has no `wire_success` call at all) from a module that
# genuinely failed every call. C-37 states this outright, and `search_searxng`
# is the live proof: C-37 verified it DOES wire success, so its 152/152 may be
# a real outage. Rendering it as "n/a, disregard" would hide one. Both readings
# are stated, and the reader is pointed at fail/total, which do carry
# information.
def describe_success_rate(entry, *, short: bool = False) -> str:
    """Render a module's success rate so it cannot be mistaken for a
    measurement when it is not one.

    A module whose rate IS a measurement renders as the bare number, in both
    forms -- the annotation has to stay rare, or it becomes noise on 243 lines
    and buries the 12 that need it.

    `short=True` is for a fixed-width table, where the full explanation would
    destroy the layout and, repeated on every flagged row, flood the prompt it
    is written into. It is the SAME judgement at a different length, not a
    second opinion: one function, so a caller cannot pick a softer reading.

    Never raises: this renders inside ARIA's introspection answer, and a throw
    here would replace a working answer with an error (the R-F3845 lesson).
    """
    if not isinstance(entry, dict):
        return "unknown"
    try:
        total = int(entry.get("total", 0) or 0)
        fail = int(entry.get("fail", 0) or 0)
        skip = int(entry.get("skip", 0) or 0)
        success = int(entry.get("success", 0) or 0)
        rate = float(entry.get("success_rate", 0) or 0)
    except (TypeError, ValueError):
        return "unknown"

    if entry.get("no_measurable_signals"):
        # R-F3936: nothing to divide. `success_rate` fell back to 0, which is
        # not a rate and not a failure.
        if short:
            return "n/a (nothing measurable yet)"
        return (f"n/a -- nothing measurable yet: {total} signal(s), {skip} "
                f"skipped, so there is no denominator. The 0 is a fallback, "
                f"not a rate, and it is not a failure either")

    if entry.get("only_failures_recorded"):
        if short:
            return f"uninformative ({success} successes in {total})"
        # Deliberately states BOTH readings. `only_failures_recorded` cannot
        # separate a wire that never calls wire_success from a module failing
        # every call, and picking either one would be a fabrication -- one of
        # them hides a live outage.
        return (f"{rate:.2f} is NOT a measurement here: all {total} recorded "
                f"signals are failures. Either this module never records a "
                f"success (a failure-only wire) or it is genuinely failing "
                f"every call. The counters cannot tell them apart -- read "
                f"fail={fail}/total={total}")

    return f"{rate:.2f}"


@fail_wire(module="brain_hook", gap_type="engine_failure")
async def get_stats() -> dict:
    """Return brain hook stats — per-module signal counts + health.

    R-F1599: cached for 30s to avoid recomputing the full module map from
    Redis on every call. Under load the uncached call takes 20-30s.
    """
    global _stats_cache, _stats_cache_at
    now = time.time()
    if _stats_cache and (now - _stats_cache_at) < _stats_cache_ttl:
        return _stats_cache

    # R-F2157: drain any in-process pending deltas so the surface reflects
    # the latest signals. Cache-gated above → this runs ~once per 30s, not
    # per request, so it can't re-introduce the per-call write storm.
    try:
        await _flush_stats_pending(force=True)
    except Exception:
        pass

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
            "skip": val.get("skip", 0),
            "success_rate": round(val["success"] / (val["total"] - val.get("skip", 0)), 2)
                if (val.get("total", 0) - val.get("skip", 0)) > 0 else 0,
            # C-37 (R-F3934) — `success_rate: 0.0` MEANS TWO DIFFERENT THINGS and the
            # reader could not tell them apart. Measured live: eight modules read 0.0,
            # and several are 0.0 BY CONSTRUCTION — `llm/openai_compat.py`, the emitter
            # for every `llm_*` module, contains no `wire_success` call at all, as does
            # `main.py::_health_precompute_loop`. For those the rate can never be
            # anything else, however healthy the module is. Others (search_searxng DOES
            # wire success) genuinely measure. An operator scanning this saw eight
            # broken subsystems; several were failure-only reporters working as built.
            #
            # This does NOT guess which case it is — the counters cannot say, and a
            # static scan cannot map runtime module labels like `llm_deepseek` (built
            # as f"llm_{self.name}") back to a file. It reports the ambiguity instead:
            # when nothing but failures has ever been recorded, the RATE carries no
            # information and `fail`/`total` are the signal to read.
            "only_failures_recorded": (
                val.get("success", 0) == 0 and val.get("fail", 0) > 0
            ),
            # C-37 residual (R-F3936) — the SAME defect at the other end of the
            # expression. `success_rate` falls back to `0` when `total - skip == 0`,
            # i.e. when there is nothing to divide: a module whose only signals were
            # SKIPS reports 0.0 exactly like one that failed every call. Found live —
            # `deploy` read `success_rate: 0.0, fail: 0, total: 1`, which is neither a
            # failure nor a rate. Flagged rather than changing `success_rate`'s type,
            # which is a published field this module's own tests pin as numeric.
            "no_measurable_signals": (
                (val.get("total", 0) - val.get("skip", 0)) <= 0
            ),
            "last_signal_ago_h": round(hours_ago, 1) if hours_ago is not None else None,
            "status": status,
        }

    # Identify modules that are registered but have never sent a signal
    all_known = set(_MODULE_TOPICS.keys())
    never_seen = all_known - set(modules.keys())
    # R-F4042 (C-104) — and the OTHER direction, which nothing checked: a module
    # may report under ANY name and is silently added to the health surface.
    # Because `never_seen` is derived from the registry, an unregistered
    # reporter can never appear in it — so a phantom name is invisible to the
    # very gauge that exists to spot missing modules. Measured 2026-08-16:
    # 60 of 74 import-time wire calls emitted names outside `_MODULE_TOPICS`,
    # including `learning.knowledge_spider` sitting healthy while the
    # registered, LOAD-BEARING `knowledge_spider` sat in `never_seen`.
    unregistered = sorted(m for m in modules if not _is_registered_module(m))

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

    result = {
        "total_signals": g.get("total", 0),
        "tracking_since": g.get("started_at"),
        "modules": modules,
        "healthy_count": len(healthy),
        "stale_count": len(stale),
        "stale_modules": stale,
        "never_seen": sorted(never_seen),
        # R-F4042 (C-104) — reporters outside the registry. These inflate
        # `modules` and are structurally invisible to `never_seen`. Listed, not
        # dropped: the signal is real telemetry, it is the NAME that is wrong.
        "unregistered_modules": unregistered,
        "unregistered_count": len(unregistered),
        "absorb_skipped_by_reason": absorb_skipped_by_reason,
        "absorption_quarantine": quarantine_stats,
        "verification_accumulator": accumulator_stats,
        "circuit_breaker": breaker,
        "health": composite,
    }
    # R-F2021 — surface the REAL per-sector aggregate. The module loop above
    # correctly skips every "_"-prefixed key (they aren't modules), but that
    # blanket filter ALSO dropped the genuine "_by_sector" rollup accumulated by
    # absorb() — so the sources-page sector panel read an absent key and rendered
    # empty despite real data existing. Explicitly re-expose it (root-kill: name
    # the real aggregate, don't relax the module filter).
    result["_by_sector"] = stats.get("_by_sector", {})
    # R-F2537 (finding #4): surface the durable ingest queue + semantic-index queue
    # health so a "healthy" brain report can't silently hide a dead-lettered ingest
    # backlog, a stuck drain, or dropped semantic-index work. Best-effort — a queue
    # that is disabled/unavailable is reported as such (never-false-clean), not omitted.
    try:
        from . import brain_ingest_queue as _biq2537
        _ingest_q = await _biq2537.stats()
    except Exception as _e:
        _ingest_q = {"available": False, "error": str(_e)[:120]}
    try:
        from . import _semantic_index_queue as _siq2537
        _sem_q = _siq2537.get_stats()
    except Exception as _e:
        _sem_q = {"available": False, "error": str(_e)[:120]}
    result["queues"] = {"brain_ingest": _ingest_q, "semantic_index": _sem_q}
    # R-F2537 (finding #5): label these stats as the CURRENT VISIBLE window, not a
    # guarantee of full-ecosystem historical coverage — a module absent from `modules`
    # means "no signal since tracking_since" (or a fresh/reset brain), NOT "unwired".
    result["coverage"] = {
        "modules_tracked": len(modules),
        "modules_healthy": len(healthy),
        "scope": "current_visible_window",
        "note": ("Reflects modules that emitted a brain signal since tracking_since. "
                 "A module absent here has been silent in this window (or the brain "
                 "state is fresh/reset) — it is NOT necessarily unwired. This is not a "
                 "claim of full historical ecosystem coverage."),
    }
    # R-F1599: update cache
    _stats_cache = result
    _stats_cache_at = now
    return result


# R-F668 (2026-05-17): load-bearing modules whose silence is a real
# incident (not a "module idle for a day" non-event). Going stale on
# any of these should fire a CRITICAL alert, not just a warning —
# load-bearing modules feed the chat pipeline directly and a silent
# stop = ARIA losing a sense.
#
# Audit 2026-05-17: "email_reader → 2026-04-18 silent loss: 302 emails
# read, 0 brain signals. No stale-module alert fires when email_reader
# goes silent for days." Adding email_reader as load-bearing surfaces
# the same class of regression next time at the dashboard, not in the
# weekly report 7 days later.
_LOAD_BEARING_MODULES = frozenset({
    "email_reader",      # seenode→Python email pipeline (R-F468 era)
    "aria_chat",         # R-F655 chat ingest (highest signal volume)
    "knowledge_ingestor", # /api/aria/read-document
    "web_atlas",         # R-F667 autonomous crawl
    "knowledge_spider",  # hourly autonomous spider
})


def _stale_severity(module: str) -> str:
    """R-F668: critical for load-bearing modules; warning otherwise."""
    return "critical" if module in _LOAD_BEARING_MODULES else "warning"


def _is_registered_module(module: str) -> bool:
    """Is this reporting name declared in `_MODULE_TOPICS`?

    R-F4042 (C-104) — the registry is the contract for what the health surface
    tracks. `never_seen` is computed FROM it, so a module reporting under an
    undeclared name (`learning.knowledge_spider`, `autonomous.tasks`, …) can
    never show up as missing: it silently becomes a new "healthy" entry while
    the declared name it shadows stays permanently never-seen.
    """
    return module in _MODULE_TOPICS


def _never_seen_severity(module: str) -> str:
    """R-F668: load-bearing module that has NEVER sent a signal is an
    install/wiring bug, not informational. Critical."""
    return "critical" if module in _LOAD_BEARING_MODULES else "info"


@fail_wire(module="brain_hook", gap_type="engine_failure")
async def get_stale_alerts() -> list[dict]:
    """Return alert dicts for modules that haven't sent a signal in 24h.

    R-F668: load-bearing modules (email_reader, aria_chat, web_atlas,
    knowledge_ingestor, knowledge_spider) report severity=critical so
    they surface in proactive alerts, not just weekly_report. Other
    modules stay at severity=warning (info for never-seen).
    """
    stats = await get_stats()
    alerts = []
    for mod in stats.get("stale_modules", []):
        m = stats["modules"].get(mod, {})
        alerts.append({
            "module": mod,
            "severity": _stale_severity(mod),
            "load_bearing": mod in _LOAD_BEARING_MODULES,
            "title": f"Brain signal stale: {mod}",
            "detail": f"{mod} last sent a signal {m.get('last_signal_ago_h', '?')}h ago (threshold: {_ALERT_STALE_HOURS}h)",
            "last_signal_ago_h": m.get("last_signal_ago_h"),
        })
    for mod in stats.get("never_seen", []):
        alerts.append({
            "module": mod,
            "severity": _never_seen_severity(mod),
            "load_bearing": mod in _LOAD_BEARING_MODULES,
            "title": f"Brain signal never seen: {mod}",
            "detail": f"{mod} is registered but has never sent a signal to brain_hook",
        })
    return alerts

# R-F2537: REMOVED a bogus R-F2119 auto-annotation block that fired
#   wire_failure(module="brain_hook", detail="module shutdown", gap_type="engine_failure")
# at MODULE-IMPORT time (not shutdown). brain_hook is imported constantly, so this
# recorded a FALSE engine_failure gap into capability_gaps on every import — self-
# referential noise that buried real caller gaps (it broke test_rf2404's shed-branch
# assertion) and polluted the gap_detector→self_coder signal. brain_hook wires its
# real outcomes through absorb()/_record_signal continuously; it needs no import-time
# self-wire. NOTE: ~210 other intel/ modules still carry this same import-time
# wire_failure("module shutdown") block from R-F2119 — a systemic sweep is tracked
# separately (see the session findings); this removes only the brain_hook instance.

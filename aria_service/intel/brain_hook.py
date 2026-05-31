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
    "sanctions_divergence": ["compliance", "legal"],
    "autonomous_scheduler": ["general"],
    "autonomy_scorer":      ["general"],
    "operating_modes":      ["general"],
    "calibration_review":   ["general", "compliance"],
    "self_restart":         ["general"],
    "dead_letter_queue":    ["general", "compliance"],
    "circuit_breaker":      ["general", "compliance"],
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


def mark_interactive() -> None:
    """Record that a user-facing request (chat / document read) just arrived,
    so autonomous absorbs yield the encoder to it for the next
    _INTERACTIVE_YIELD_WINDOW_S. Cheap + safe to call on every request. R-F860."""
    global _last_interactive_at
    _last_interactive_at = time.monotonic()


def _interactive_active() -> bool:
    """True iff a user-facing request arrived within the yield window."""
    return (time.monotonic() - _last_interactive_at) < _INTERACTIVE_YIELD_WINDOW_S


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
_last_absorb_monotonic: float = 0.0


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
        try:
            await _record_signal(module, success=False, sector=_sector_normalised)
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
    asyncio.create_task(
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

    # Record signal immediately (not in background) so stats are never lost
    await _record_signal(module, success=True, sector=_sector_normalised)
    result["user_id"] = user_id or ""
    result["sector"] = _sector_normalised
    result["latency_ms"] = 0.0  # actual latency tracked in background task

    return result


async def absorb_silent(**kwargs) -> None:
    """Fire-and-forget wrapper — logs errors but never raises."""
    try:
        await absorb(**kwargs)
    except Exception as e:
        logger.debug("brain_hook.absorb_silent failed entirely: %s", e)


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
_LATENCY_TRIP_MS = int(os.environ.get("ARIA_BRAIN_LATENCY_TRIP_MS", "6000"))

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
    float(os.environ.get("ARIA_BRAIN_ABSORB_TIER_TIMEOUT_MS", "5000")) / 1000.0
)

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
_ABSORB_CONCURRENCY = int(os.environ.get("ARIA_BRAIN_ABSORB_CONCURRENCY", "8"))  # R-F1056: raised from 2
_ABSORB_SEM_ACQUIRE_TIMEOUT_S = (
    float(os.environ.get("ARIA_BRAIN_ABSORB_SEM_ACQUIRE_MS", "500")) / 1000.0
)
_absorb_concurrency_sem: Optional[asyncio.Semaphore] = None


def _get_absorb_concurrency_sem() -> Optional[asyncio.Semaphore]:
    """Lazy-init the per-process concurrency semaphore. Returns None
    when the cap is disabled (ARIA_BRAIN_ABSORB_CONCURRENCY=0)."""
    global _absorb_concurrency_sem
    if _ABSORB_CONCURRENCY <= 0:
        return None
    if _absorb_concurrency_sem is None:
        _absorb_concurrency_sem = asyncio.Semaphore(_ABSORB_CONCURRENCY)
    return _absorb_concurrency_sem
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


def _maybe_trip_breaker(reason: str) -> None:
    """Open the circuit if p95 has been over threshold _TRIP_CONSECUTIVE
    times in a row. Idempotent — safe to call on every absorb."""
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


async def _record_signal(module: str, success: bool, sector: str = "") -> None:
    """Record a signal in Redis for per-module tracking.

    R-F56: when `sector` is non-empty, also bumps a parallel
    per-sector counter so the dashboard / get_stats surface can
    answer "how many compliance-officer absorbs this week vs broker".
    """
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

        # R-F56 — per-sector buckets keyed by `_by_sector`. Sector is
        # validated against the known persona keys to prevent unbounded
        # bucket growth from typos / malformed inputs.
        if sector and sector in _ALLOWED_SECTORS:
            sec_buckets = stats.setdefault("_by_sector", {})
            sb = sec_buckets.setdefault(sector, {
                "total": 0, "success": 0, "fail": 0,
                "first_signal_at": now, "last_signal_at": 0,
                "by_module": {},
            })
            sb["total"] += 1
            if success:
                sb["success"] += 1
            else:
                sb["fail"] += 1
            sb["last_signal_at"] = now
            sb["by_module"][module] = sb["by_module"].get(module, 0) + 1

        await rs.set_json(_STATS_KEY, stats, ex=30 * 86400)
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


def set_chat_context(user_id: str, sector: str) -> _cv.Token:
    """Set the per-turn (user_id, sector) context. Call at chat entry;
    pair with `reset_chat_context(token)` in a finally block.
    Returns the contextvars Token caller passes to reset."""
    return _chat_user_ctx.set((user_id or "", sector or ""))


def reset_chat_context(token: _cv.Token) -> None:
    try:
        _chat_user_ctx.reset(token)
    except Exception:
        pass


def get_chat_context() -> tuple[str, str]:
    """Return the current chat (user_id, sector) — ('', '') when no
    chat-turn context is active (autonomous / sweep / dev calls)."""
    try:
        return _chat_user_ctx.get()
    except Exception:
        return ("", "")


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


def _never_seen_severity(module: str) -> str:
    """R-F668: load-bearing module that has NEVER sent a signal is an
    install/wiring bug, not informational. Critical."""
    return "critical" if module in _LOAD_BEARING_MODULES else "info"


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

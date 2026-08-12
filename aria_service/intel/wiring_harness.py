"""R-F1782 — MODULE_GAP_TYPES registry + HARD EXEMPT registry + Gates A-E.

Single file containing ALL enforcement harness components for the wiring backfill.
Claude reviews this ONCE — that single review is where 100% is guaranteed.

Components:
  1. MODULE_GAP_TYPES — accurate gap_type per module (Claude-approved)
  2. HARD_EXEMPT — functions that must NEVER be wired (generators, streams, lifespan)
  3. GATE A — coverage completeness (every public fn wired or exempt)
  4. GATE B — gap_type accuracy (wired modules use their registered type)
  5. GATE C — parametrized capability test (generic, one per module)
  6. GATE D — control-flow raise scan (flag gap-spam risk)
  7. GATE E — live wedge monitor (auto-pause on regression)
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import ast
import os
from typing import Any

# ── MODULE_GAP_TYPES registry ─────────────────────────────────────────────
# Each module maps to its accurate VALID_GAP_TYPE.
# Claude approves this once — the judgment artifact.
# Rule: gap_type must describe THAT module's failure domain.
#   source_failure     — fetch/source failed (deep_researcher, brave_answers)
#   engine_failure     — internal processing failed (intel_ledger, dd_orchestrator)
#   file_parse         — document/file parsing failed (document_reader, ocr)
#   api_missing        — external API unavailable (companies_house, github_search)
#   timeout            — operation timed out
#   knowledge_gap      — missing knowledge
#   embedder_failure   — embedding/encoding failed (semantic_search, neural_memory)
#   registry_lookup    — registry lookup failed (portal_registry, oem_registry)
#   format_unsupported — unsupported format
#   agent_cycle_failure — generic agent loop failure
MODULE_GAP_TYPES: dict[str, str] = {
    # Already wired (R-F1777, R-F1779)
    "deep_researcher": "source_failure",
    "intel_ledger": "engine_failure",
    # Core engines
    "aria_engine": "engine_failure",
    "reasoning_router": "engine_failure",
    "reasoning_library": "engine_failure",
    "symbolic_reasoner": "engine_failure",
    "grounded_reasoner": "engine_failure",
    "local_brain": "engine_failure",
    # Document processing
    "document_reader": "file_parse",
    "document_intelligence": "file_parse",
    "ocr": "file_parse",
    "file_type_detector": "file_parse",
    "content_scanner": "file_parse",
    "pdf_deep_ingest": "file_parse",
    # External data sources
    "companies_house": "api_missing",
    "github_search": "api_missing",
    "brave_answers": "source_failure",
    "web_search": "source_failure",
    "search_searxng": "source_failure",
    "news_monitor": "source_failure",
    "sipri_ingest": "source_failure",
    "sipri_knowledge": "source_failure",
    # DD pipeline
    "dd_orchestrator": "engine_failure",
    "dd_disciplines": "engine_failure",
    "dd_schema": "engine_failure",
    "dd_trigger_pipeline": "engine_failure",
    "dd_vault": "engine_failure",
    "dd_case_library": "engine_failure",
    "dd_layer_extensions": "engine_failure",
    "dd_versioning": "engine_failure",
    # Knowledge & memory
    "knowledge": "engine_failure",
    "neural_memory": "embedder_failure",
    "rag_store": "embedder_failure",
    "semantic_search": "embedder_failure",
    "memory_wal": "engine_failure",
    "mistake_ledger": "engine_failure",
    # Brain hook
    "brain_hook": "engine_failure",
    "brain_hook_bg": "engine_failure",
    "capability_gaps": "engine_failure",
    # Compliance & sanctions
    "sanctions": "source_failure",
    "country_sanctions": "source_failure",
    "compliance_watch": "engine_failure",
    "compliance_workflow": "engine_failure",
    # Research
    "researcher": "source_failure",
    "research_tasks": "source_failure",
    # R-F3429 — the second "deep_researcher" key was REMOVED here; the original is at
    # the top of this dict (the R-F1777/R-F1779 "already wired" block). Both mapped to
    # source_failure, so it changed nothing today — and that is exactly what made it
    # dangerous: a dict literal keeps the LAST duplicate, so editing the visible entry
    # at the top would have had no effect and the reason why would not have been
    # obvious. Found by the duplicate-key guard added in the same R-number, which had
    # just caught me introducing the same shape in HARD_EXEMPT.
    # R-F3560 — news_archive / news_claims were never REGISTERED, so gate_b compared
    # their decorators against `_default` (agent_cycle_failure) and reported 16
    # "violations" for declarations that were correct all along. The fix is the
    # registry, not the 16 decorators: neither module is an agent loop. news_archive is
    # an append-only store of news observations and news_claims records extracted
    # claims — their failure domain is internal processing, which IS engine_failure.
    # Rewriting 16 accurate decorators to satisfy an absent registry entry would have
    # made gate_b green by making its accuracy claim false.
    "news_archive": "engine_failure",
    "news_claims": "engine_failure",
    # Portal registry
    "portal_registry": "registry_lookup",
    "portal_coverage_audit": "registry_lookup",
    "agent_signup_vault": "registry_lookup",
    "registration_check": "registry_lookup",
    # Student & learning
    "student": "engine_failure",
    "learning_progress": "engine_failure",
    "correction_learner": "engine_failure",
    "continuous_learner": "engine_failure",
    # Cost tracking
    "cost_tracker": "engine_failure",
    "cost_free_learning": "engine_failure",
    # Routes (R-F1800) — routes/aria.py handler internal failures. HTTPException
    # 4xx is control flow (R-F1784 allowlist), so only real handler failures gap.
    "aria": "engine_failure",
    # ── R-F3428 — the other two ROUTE modules, which were never registered ──
    #
    # GATE B reported ~60 violations across routes/vetting.py and
    # routes/vetting_portal.py, every one of the form
    #     "@fail_wire on 'vetting_assess_ep()' gap_type='engine_failure'
    #      but module 'vetting' requires 'agent_cycle_failure'"
    # Sixty findings, ONE cause: neither module has an entry here, so both fall to
    # `_default`. And `_default` is `agent_cycle_failure`, which is right for the
    # autonomous loops it was chosen for (see the note below) and wrong for an HTTP
    # handler — a vetting endpoint failing is an engine failure in a request, not a
    # cycle failure in an agent.
    #
    # THE DECORATORS WERE ALREADY CORRECT. They declare `engine_failure`, matching
    # their sibling `aria` on the line above; the REGISTRY was incomplete. Fixing this
    # by editing sixty decorators would have made the code agree with a default that
    # does not describe it — the tail wagging the dog, and it would have mis-filed
    # every future vetting gap under the autonomous loop's failure domain.
    "vetting": "engine_failure",
    "vetting_portal": "engine_failure",
    # R-F3428 — and my own: R-F3402 added dd_standard.py with
    # @fail_wire(gap_type="engine_failure") and did not register the module here, so it
    # fell to `_default` and GATE B flagged it. The gate caught my omission the same way
    # it caught the vetting one; `assess()` is a pure evaluation over a report, so an
    # engine failure is the accurate domain.
    "dd_standard": "engine_failure",
    # R-F1808 — llm/ provider layer (per-provider failures; streams HARD_EXEMPT;
    # ProviderError is per-provider control flow handled by fallback)
    "anthropic": "engine_failure", "gemini": "engine_failure",
    "openai_compat": "engine_failure", "local_llm": "engine_failure",
    "fallback": "engine_failure", "metered": "engine_failure",
    "provider": "engine_failure", "resilience": "engine_failure",
    "rate_limiter": "engine_failure", "tier_router": "engine_failure",
    "hybrid": "engine_failure", "prompt_budget": "engine_failure",
    "factory": "engine_failure", "aria_llm_provider": "engine_failure",
    # R-F1808 — search_engine/
    "internal_search": "source_failure",
    # ── R-F3901 — the same omission R-F3428 documents, two modules later ──
    #
    # GATE B reported 5 violations, all of the form
    #     "@fail_wire on 'record_call()' gap_type='engine_failure'
    #      but module 'brave_usage' requires 'agent_cycle_failure'"
    # Same single cause as the vetting case above: neither module was registered
    # here, so both fell to `_default`, which is `agent_cycle_failure` — right for
    # the autonomous loops it was chosen for and wrong for these.
    #
    # THE DECORATORS ARE CORRECT AND ARE LEFT ALONE. `brave_usage` meters a PAID
    # SEARCH API and `search_engine_health` tracks whether search SOURCES are still
    # answering; a failure in either is an engine failure, not a failure of an agent
    # cycle. Filing them under the autonomous loop's domain would bury a
    # search-backend outage among agent-loop noise — precisely the mis-filing
    # R-F3428 refused when it declined to rewrite sixty decorators to match a
    # default that did not describe them.
    "brave_usage": "engine_failure",
    "search_engine_health": "engine_failure",
    # autonomous/ modules use the _default (agent_cycle_failure) — semantically
    # correct (they ARE the agent loop); no per-module override needed.
    # Default for unregistered modules
    "_default": "agent_cycle_failure",
}


# ── R-F3560 — per-FUNCTION gap_type overrides ────────────────────────────
#
# GATE B enforces one gap_type per module, which is right for a module with one
# failure domain and too coarse for a module that spans several. `routes/aria.py` is
# every DD, vetting, billing and GDPR endpoint in one file.
#
# THE CASE THAT FORCED THIS: `leads_inbound_delete_ep` declares
# `data_protection_violation`, and VALID_GAP_TYPES says of that type — verbatim —
# "Deliberately NOT folded into engine_failure: this is a GDPR-severity signal and
# collapsing it into a generic type would bury a regulatory obligation in ordinary
# noise." Rewriting the decorator to `engine_failure` would have made GATE B green by
# destroying the exact signal the type exists to carry: a green gate bought with a
# worse system.
#
# AN ALLOWLIST, NOT A LOOSENING. Every override is named with its reason, so a
# deliberate exception stays visible and a careless one still fails. A function absent
# from this table is still held to its module's registered type.
GAP_TYPE_OVERRIDES: dict[str, dict[str, str]] = {
    "aria": {
        "leads_inbound_delete_ep": "data_protection_violation",
    },
}


def get_gap_type(module: str, func_name: str = "") -> str:
    """The gap_type a function must declare: its override, else its module's."""
    if func_name:
        override = GAP_TYPE_OVERRIDES.get(module, {}).get(func_name)
        if override:
            return override
    return MODULE_GAP_TYPES.get(module, MODULE_GAP_TYPES["_default"])


# ── HARD EXEMPT registry ──────────────────────────────────────────────────
# Functions that must NEVER be wired, each with a reason.
# GATE A treats these as legitimately-not-dark.
# Format: {module: {function_name: "reason"}}, keyed by BASENAME (matches the
# `filename` arg the gates pass — a path key like "routes/aria.py" would never
# match basename "aria.py", silently defeating the exemption once routes wire).
# Use "*" for function_name to exempt ALL functions in a module.
HARD_EXEMPT: dict[str, dict[str, str]] = {
    # R-F3626 — added by R-F3608. PURE: str→bool predicate over string prefixes.
    # No I/O, no store, total over its input (empty string returns False). It is a
    # CLASSIFIER used to decide whether text may be absorbed as knowledge; wiring it
    # would fire a brain signal on every ordinary "is this a degraded reply?" check.
    "reasoning_library.py": {
        "is_degraded_or_error_response": "PURE — str→bool predicate, no I/O, total over its input",
    },
    # R-F3940 — the two key-derivation helpers extracted from safety.py. Same PURE
    # category as the entry above, and for the same reason: no I/O, no store, total
    # over their inputs, and they sit on the hottest path in the module (every rate
    # check derives a key), so wiring them would fire a brain signal per ordinary
    # call and bury the signals that mean something.
    #
    # They are EXEMPT, NOT DARK. What they are part of is fully wired: the callers
    # that can actually fail — check_and_increment_rate, release_rate_slot,
    # can_task_run — all carry @fail_wire, and release_rate_slot additionally
    # reports its refund outcome on BOTH branches (§21a). Extracting a pure helper
    # out of a wired function must not be allowed to look like new dark surface.
    "safety.py": {
        "current_hour_bucket": "PURE — no-arg int derivation from the clock, no I/O",
        "rate_bucket_key": "PURE — string formatting of a key, no I/O, total",
    },
    # routes/aria.py — ASYNC GENERATORS + STREAM endpoints
    "aria.py": {
        "chat_stream_ep": "ASYNC GENERATOR — wrapping breaks SSE streaming (§13)",
        "chat_ep": "STREAM endpoint — §13 body risk",
    },
    # aria_engine.py — ASYNC GENERATORS + STREAM-like endpoints
    "aria_engine.py": {
        "aria_chat_stream": "ASYNC GENERATOR — wrapping breaks SSE streaming (§13)",
        "aria_chat": "STREAM-like — §13 body risk",
    },
    # main.py — ASYNC GENERATOR (boot critical)
    "main.py": {
        "lifespan": "ASYNC GENERATOR — wrapping breaks boot (F28 class, §9)",
    },
    # cost_tracker.py — SYNC GENERATOR
    "cost_tracker.py": {
        "feature": "SYNC GENERATOR — context manager with yield",
        # R-F3429 — MERGED here, not given a second "cost_tracker.py" key. My first
        # attempt did exactly that and the duplicate-key guard caught it: a later
        # duplicate WINS in a dict literal, so it would have silently un-exempted
        # `feature` above — a sync generator context manager that must never be
        # wrapped. The hazard the R-F1785 note in this file already warned about,
        # reproduced by me while writing the fix for it.
        "set_user": "returns a contextvars.Token the caller resets with — a wrapper "
                    "changes the contract",
        "get_current_user": "ContextVar read",
        "set_tier": "returns a contextvars.Token the caller resets with — a wrapper "
                    "changes the contract",
        "get_current_tier": "ContextVar read",
    },
    # llm/ provider layer — ASYNC GENERATOR stream()s (R-F1785: now in scope).
    # Wrapping an LLM token-stream generator breaks streaming (§13). The
    # decoration-time guard already forbids it; these entries keep GATE A
    # coherent (a stream() is legitimately-not-dark, not an unwired path).
    # R-F1785 streams + R-F1809 @property/@classmethod accessors (merged — one
    # key per file; duplicate dict keys would silently overwrite each other).
    "anthropic.py": {"stream": "ASYNC GENERATOR — LLM token stream (§13)",
                     "is_configured": "@property — config check"},
    "aria_llm_provider.py": {
        "stream": "ASYNC GENERATOR — LLM token stream (§13)",
        # R-F3626 — added by R-F3606. PURE: int in, int out. No I/O, no store, no
        # network. It cannot fail in a way the brain could act on — a bad input is
        # already handled internally by returning the ceiling, which is the SAFE
        # value, not a swallowed error. A @fail_wire here would emit proprioception
        # noise for an event that never happens, and §21's value depends on a brain
        # signal meaning something.
        "clamp_for_sovereign": "PURE — int→int clamp, no I/O; bad input returns the safe ceiling",
    },
    "fallback.py": {"stream": "ASYNC GENERATOR — LLM token stream (§13)",
                    "is_configured": "@property — config check",
                    # R-F3429 — accessors, MERGED into this entry rather than given
                    # their own "fallback.py" key: a duplicate dict key silently keeps
                    # the LAST one, which would have discarded the stream and
                    # provider_scope exemptions above. The note further down this file
                    # records the same hazard from R-F1785.
                    "get_preferred_provider": "ContextVar read, already guarded",
                    "preference_only_providers": "env-derived set; pure",
                    "get_provider_status": "reads config + breaker registry to REPORT "
                                           "health; wiring the health reporter would "
                                           "gap on every degraded provider it exists "
                                           "to describe",
                    # R-F3419 — arrived with R-F3032..R-F3036 (LLM chain P0) and was
                    # never registered, so GATE A has reported it as an unexempt
                    # generator on every run since. It is a @contextlib.contextmanager:
                    # wrapping it with @fail_wire would return the wrapper's value
                    # instead of the context manager, breaking every
                    # `with provider_scope(...)` call site. Exempt by CONSTRUCTION.
                    "provider_scope": "@contextlib.contextmanager — wrapping replaces "
                                      "the CM and breaks every `with` call site"},
    # R-F3419 — model_router.py had NO entry at all, so its generator was permanently
    # unexempt. `stream_synthesis` yields synthesis tokens directly (4 yields in its own
    # body, verified by AST); wrapping an async generator consumes//replaces the stream,
    # which is the §13 stream-bypass class.
    "model_router.py": {
        "stream_synthesis": "ASYNC GENERATOR — synthesis token stream (§13)",
    },
    "local_llm.py": {"stream": "ASYNC GENERATOR — LLM token stream (§13)"},
    "metered.py": {"stream": "ASYNC GENERATOR — LLM token stream (§13)",
                   "name": "@property accessor",
                   "is_configured": "@property — config check"},
    "provider.py": {"stream": "ASYNC GENERATOR — LLM token stream (§13)",
                    "from_http_status": "@classmethod error builder — not a failure path",
                    "is_configured": "@property — config check"},
    "rate_limiter.py": {"stream": "ASYNC GENERATOR — LLM token stream (§13)",
                        "name": "@property accessor",
                        "is_configured": "@property — config check"},
    "resilience.py": {
        "stream": "ASYNC GENERATOR — LLM token stream (§13)",
        "wrap": "SYNC GENERATOR — resilience context manager",
        "name": "@property — provider name accessor",
        "is_configured": "@property — config check, not a failure path",
    },
    # R-F1809 — @property/@staticmethod/@classmethod accessors across the
    # autonomous/ + llm/ modules. Wrapping a descriptor changes its semantics;
    # these are accessors/constructors/formatters, not failure paths (§21a).
    "autonomous_deploy.py": {"from_env": "@classmethod constructor — not a failure path"},
    "claude_reviewer.py": {
        "is_approved": "@property verdict accessor",
        "is_blocked": "@property verdict accessor",
        "is_flagged": "@property verdict accessor",
    },
    "cost_monitor.py": {
        "total_tokens": "@property metric",
        "remaining_usd": "@property metric",
        "utilisation": "@property metric",
    },
    "gap_detector.py": {
        "auto_fixable": "@property gap classification",
        "requires_wa_approval": "@property gap classification",
        "requires_hard_gate": "@property gap classification",
    },
    "wa_notifier.py": {
        "is_configured": "@property — config check",
        "msg_request_queued": "@staticmethod string formatter",
        "msg_stage_progress": "@staticmethod string formatter",
        "msg_shipped": "@staticmethod string formatter",
        "msg_failed": "@staticmethod string formatter",
    },
    # anthropic/fallback/metered/provider/rate_limiter accessors merged into
    # their R-F1785 stream entries above (avoid duplicate dict keys). Only the
    # llm modules with NO prior HARD_EXEMPT entry are new here:
    "gemini.py": {"is_configured": "@property — config check"},
    "hybrid.py": {"is_configured": "@property — config check"},
    "openai_compat.py": {
        "is_configured": "@property — config check",
        # R-F3429 — merged, not a second key (see the fallback.py note above).
        "default_deepseek_model": "env read with a literal default",
        "backup_deepseek_model": "env read with a literal default (R-F3035)",
    },
    # R-F1792 — @property accessors. Wrapping a property changes its semantics
    # and these are trivial computed accessors, not failure paths (reasoned
    # exemption §21a). The method-aware applicator skips them; GATE A needs this.
    "document_reader.py": {
        "is_usable": "@property — trivial accessor, not a failure path",
        "summary": "@property — trivial accessor, not a failure path",
    },
    "semantic_search.py": {
        "size": "@property — trivial accessor, not a failure path",
        "embedding_count": "@property — trivial accessor, not a failure path",
        "has_embeddings": "@property — trivial accessor, not a failure path",
    },

    # ── R-F3429 — GATE A, part 1: the accessors ─────────────────────────────
    #
    # GATE A reported 67 public functions with no @fail_wire and no exemption. They are
    # NOT one problem. Roughly a third are pure accessors — contextvar reads, env reads,
    # in-place relabels, string parsing — with no failure domain at all. Wrapping those
    # would do two bad things: flood the gap ledger with non-failures until nobody reads
    # it (the §21b "dark" condition reached by noise instead of silence), and in two
    # cases CHANGE THE CONTRACT — `cost_tracker.set_user` / `set_tier` return a
    # contextvars.Token that callers reset with, and a wrapper that does not return it
    # verbatim breaks every caller.
    #
    # Each entry below was READ at its definition before being exempted, not pattern
    # matched on its name. The genuine failure paths in GATE A — the async I/O
    # functions, the orchestrator entry points, the flush/poll/deploy loops — are NOT
    # here and get wired instead; exempting those would be the false clean this file
    # exists to prevent.
    "brain_hook.py": {
        "seconds_since_interactive": "pure time arithmetic over a module float; no I/O",
    },
    "companies_house.py": {
        "consume_unavailable": "reads+clears a ContextVar; the UNAVAILABILITY it reports "
                               "is itself the gap signal, so wiring it would record a "
                               "gap about reading a gap",
        "missing_key_gap": "returns the data-gap STRING for an unset key; the caller "
                           "surfaces it — wiring the formatter double-reports",
        "explain_empty_psc": "pure string composition over already-fetched values",
    },
    "web_search.py": {
        "enable_brave_for_scope": "returns a ContextVar Token for scope restoration "
                                  "(R-F3087) — a wrapper breaks the reset contract",
        "reset_brave_scope": "ContextVar reset",
        "brave_is_enabled": "reads a key + a ContextVar; already exception-guarded",
        "mask_brave_source": "in-place relabel of a list; no failure domain",
    },
    "sanctions.py": {
        "split_bracketed_name": "pure string split, total over its input",
    },
    # R-F3430 — the last two GATE A entries, both pure reads. Everything else in that
    # backlog got WIRED (46 functions); these two are `return _INFLIGHT_DDS` and
    # `return bool(self.url)`. A gap record on either would describe nothing.
    "dd_orchestrator.py": {
        "dd_inflight_count": "returns a module counter; no I/O, no failure domain",
    },
    "dd_schema.py": {
        "has_provenance": "returns bool(self.url) on a dataclass; pure predicate",
        # R-F3560 — MERGED into this entry, not given a second "dd_schema.py" key:
        # a duplicate would silently keep the LAST one and discard has_provenance.
        # Four pure functions with no operational failure mode. Wiring them would
        # emit gap signals only for programming errors, and dd_schema's registered
        # gap_type (engine_failure) would mislabel a formatting bug as an engine
        # fault — the gate_b accuracy problem, introduced by the fix for gate_a.
        "as_dict": "dataclasses.asdict(self); pure serialiser on a hot render path",
        "dd_policy_bundle_line": "joins a module-level constant dict; pure (R-F3546)",
        "evidence_grade_explained": "formats a grade from constants; pure (R-F3549)",
        "verdict_logic_status": "compares a pinned string to a constant; pure, no I/O "
                                "(R-F3496)",
    },
    # R-F3560 — rag_store's registered gap_type is `embedder_failure`, which is right
    # for its retrieval surface and WRONG for these three: they are GDPR/config helpers
    # with no embedding involvement and no operational failure mode. Wiring them would
    # satisfy gate_a by making gate_b's accuracy claim false — a fault domain asserted
    # about a function that cannot fault. Precedent: fallback.py's
    # "preference_only_providers": "env-derived set; pure".
    "rag_store.py": {
        "storage_region": "env read with a literal default; cannot fail (R-F3492)",
        "jurisdiction_of_region": "pure region->jurisdiction mapping (R-F3492)",
        "retention_bases": "returns a constant table of lawful bases (R-F3484)",
    },
    "test_runner.py": {
        "coder_tests_enabled": "single env-flag read (R-F2905)",
    },
}


def is_exempt(module_path: str, func_name: str) -> tuple[bool, str]:
    """Check if a function is in the HARD EXEMPT registry.
    
    Returns (is_exempt, reason).
    """
    # Check module-level exemption
    mod_exempts = HARD_EXEMPT.get(module_path, {})
    if func_name in mod_exempts:
        return True, mod_exempts[func_name]
    if "*" in mod_exempts:
        return True, mod_exempts["*"]
    return False, ""


# ── GATE A: Coverage completeness ─────────────────────────────────────────
# Scans target dirs for public functions (sync+async).
# For every module in WIRED_MODULES, every public fn must have @fail_wire
# or be in HARD_EXEMPT. A dark public fn = BLOCK (not WARN).

# Directories to scan for wiring coverage.
# R-F1785: dropped the phantom "aria_service/engines" (never existed — the scan
# silently skipped it, giving false "engines covered" confidence). Real engine
# code is aria_engine.py (a file, see TARGET_FILES) + search_engine/ + the LLM
# provider layer (llm/, which holds 11 async-gen stream() landmines).
TARGET_DIRS = [
    "aria_service/intel",
    "aria_service/routes",
    "aria_service/autonomous",
    "aria_service/llm",
    "aria_service/search_engine",
]

# Top-level engine/boot files (not in any package dir, so scanned explicitly).
# main.py + aria_engine.py are referenced by HARD_EXEMPT (lifespan,
# aria_chat_stream); scanning them makes those exemptions real, not dead.
TARGET_FILES = [
    "aria_service/aria_engine.py",
    "aria_service/main.py",
]

# Modules that have been wired (start with R-F1777, R-F1779)
# GATE A enforces: every public fn in a WIRED_MODULE must be wired or exempt.
WIRED_MODULES: set[str] = {
    "deep_researcher",
    "intel_ledger",
    # R-F1788 — Phase 1 batch 1 (pure module-level-function reasoning modules)
    "reasoning_router",
    "symbolic_reasoner",
    "local_brain",
    # R-F1789 — Phase 1 batch 2 (35 clean pure-fn intel modules, applicator-wired)
    "brain_hook", "brain_hook_bg", "brave_answers", "capability_gaps",
    "companies_house", "compliance_watch", "compliance_workflow",
    "continuous_learner", "cost_free_learning", "country_sanctions",
    "dd_case_library", "dd_layer_extensions", "dd_trigger_pipeline",
    "dd_versioning", "document_intelligence", "file_type_detector",
    "github_search", "knowledge", "learning_progress", "memory_wal",
    "neural_memory", "news_monitor", "ocr", "pdf_deep_ingest",
    "portal_coverage_audit", "portal_registry", "reasoning_library",
    "registration_check", "research_tasks", "researcher", "sanctions",
    "search_searxng", "sipri_ingest", "sipri_knowledge", "student",
    # R-F1792 — Phase 1 batch 3 (mixed modules: module-level fns + class methods)
    "grounded_reasoner", "dd_schema", "document_reader", "content_scanner",
    "web_search", "rag_store", "semantic_search", "dd_vault",
    # R-F1795 — batch 3b (validation methods carry control_flow_exempt=ValueError)
    "agent_signup_vault",
    # R-F1801 — Phase 1 batch 4 (routes/aria.py — 629 handlers; streams exempt)
    "aria",
    # R-F1807 — Phase 1 batch 5 (intel stragglers: validation=ValueError exempt;
    # cost_tracker MonthlyCostCapExceeded exempt; correction_learner closure-fix)
    "dd_disciplines", "dd_orchestrator", "mistake_ledger",
    "correction_learner", "cost_tracker",
    # R-F1809 — Phase 1 batch 6 (autonomous/ + llm/ + search_engine/)
    "autonomous_deploy", "claude_reviewer", "codebase_reader", "coder_entrypoint",
    "constitutional_validator", "cost_monitor", "delivery", "deploy_verifier",
    "dryrun_history", "engine", "fly_deployer", "gap_detector",
    "machines_deployer", "r_counter", "review_ticket", "safety", "self_coder",
    "sovereign_llm", "tasks", "test_runner", "wa_notifier",
    "anthropic", "aria_llm_provider", "factory", "fallback", "gemini", "hybrid",
    "local_llm", "metered", "openai_compat", "prompt_budget", "provider",
    "rate_limiter", "resilience", "tier_router",
    "internal_search",
}

# Modules that are fully reviewed and exempt from wiring
FULLY_EXEMPT_MODULES: set[str] = {
    "engine_wiring.py",   # The wiring module itself
    "wire.py",            # The wiring module itself (docstring @fail_wire example)
    "wiring_harness.py",  # This enforcement harness (docstring @fail_wire examples)
}


def fail_wire_decorators(filepath: str) -> dict[str, dict[str, Any]]:
    """AST-detect REAL @fail_wire decorators on public functions.

    Critically, this ignores `@fail_wire(...)` text that appears inside
    docstrings, comments, or string literals (e.g. usage examples in wire.py
    and this harness) — only genuine entries in a function's decorator_list
    count. String-proximity matching (the prior approach) flagged those
    examples as real decorators, jamming GATES B/D on the harness's own files.

    Returns {func_name: {"gap_type": str|None, "lineno": int}}.
    """
    with open(filepath, encoding="utf-8", errors="ignore") as f:
        try:
            tree = ast.parse(f.read())
        except SyntaxError:
            return {}

    wired: dict[str, dict[str, Any]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name.startswith("_"):
            continue  # private — skip
        for dec in node.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            if isinstance(target, ast.Name):
                dec_name = target.id
            elif isinstance(target, ast.Attribute):
                dec_name = target.attr
            else:
                dec_name = None
            if dec_name != "fail_wire":
                continue
            gap_type = None
            control_flow_exempt = False
            if isinstance(dec, ast.Call):
                for kw in dec.keywords:
                    if kw.arg == "gap_type" and isinstance(kw.value, ast.Constant):
                        gap_type = kw.value.value
                    if kw.arg == "control_flow_exempt":
                        control_flow_exempt = True
            wired[node.name] = {
                "gap_type": gap_type,
                "lineno": node.lineno,
                "control_flow_exempt": control_flow_exempt,
            }
    return wired


def scan_public_functions(filepath: str) -> list[dict[str, Any]]:
    """Scan a Python file for public functions (sync + async, not private).
    
    Uses AST to detect generators and other special forms.
    
    Returns list of {name, type, is_generator, has_raise, raise_types, lineno}.
    raise_types is the set of exception class names the function raises (bare
    re-raise -> "reraise"); GATE D uses it to ignore control-flow-only raises.
    """
    with open(filepath, encoding="utf-8", errors="ignore") as f:
        try:
            tree = ast.parse(f.read())
        except SyntaxError:
            return []

    # Collect module-level functions + class methods, EXCLUDING nested closures.
    # A function defined inside another function is an implementation detail,
    # wrapped transitively by its enclosing function — wiring it is wrong and
    # GATE A demanding it is a false positive (e.g. correction_learner.relevance).
    wireable: list = []

    def _collect(body) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("_"):
                    wireable.append(node)
                # do NOT descend into the function body — skip closures
            elif isinstance(node, ast.ClassDef):
                _collect(node.body)  # methods + nested classes

    _collect(tree.body)

    fns = []
    for node in wireable:
        is_async = isinstance(node, ast.AsyncFunctionDef)
        is_generator = any(
            isinstance(n, (ast.Yield, ast.YieldFrom))
            for n in ast.walk(node)
        )
        raise_types: set[str] = set()
        for n in ast.walk(node):
            if isinstance(n, ast.Raise):
                exc = n.exc
                if exc is None:
                    raise_types.add("reraise")
                else:
                    t = exc.func if isinstance(exc, ast.Call) else exc
                    raise_types.add(getattr(t, "id", getattr(t, "attr", "?")))
        fns.append({
            "name": node.name,
            "type": "async" if is_async else "sync",
            "is_generator": is_generator,
            "has_raise": bool(raise_types),
            "raise_types": raise_types,
            "lineno": node.lineno,
        })
    return fns


def check_gate_a(module_path: str, filename: str) -> list[str]:
    """GATE A: every public fn in a WIRED_MODULE must be wired or exempt.
    
    Only enforces modules in WIRED_MODULES — unwired modules are not checked
    (they will be checked when added to WIRED_MODULES during Phase 1).
    
    Returns list of violations (empty = pass).
    """
    violations = []
    module_name = filename.replace(".py", "")
    if module_name not in WIRED_MODULES:
        return violations  # Not yet wired — skip
    if filename in FULLY_EXEMPT_MODULES:
        return violations

    fns = scan_public_functions(module_path)
    wired = fail_wire_decorators(module_path)

    for fn in fns:
        name = fn["name"]
        # Check if exempt
        exempt, _ = is_exempt(filename, name)
        if exempt:
            continue

        # Check if wired (has a REAL @fail_wire decorator — AST, not string match)
        if name in wired:
            continue  # wired — ok

        # Not wired and not exempt — violation
        violations.append(
            f"{filename}:{fn['lineno']} public {fn['type']} function "
            f"'{name}()' has no @fail_wire and is not in HARD_EXEMPT"
        )

    return violations


def check_gate_b(module_path: str, filename: str) -> list[str]:
    """GATE B: each wired module must use its registered gap_type.
    
    Returns list of violations (empty = pass).
    """
    violations = []
    if filename in FULLY_EXEMPT_MODULES:
        return violations  # wiring infrastructure (carries example decorators in docstrings)
    module_name = filename.replace(".py", "")

    # Inspect REAL @fail_wire decorators only (AST — ignores docstring examples).
    for name, info in fail_wire_decorators(module_path).items():
        # R-F3560 — resolved PER FUNCTION so a deliberately more specific type
        # (e.g. data_protection_violation) is not reported as a violation.
        expected_type = get_gap_type(module_name, name)
        actual = info["gap_type"]
        if actual is None:
            violations.append(
                f"{filename}:{info['lineno']} @fail_wire on '{name}()' "
                f"missing gap_type (expected '{expected_type}')"
            )
        elif actual != expected_type:
            violations.append(
                f"{filename}:{info['lineno']} @fail_wire on '{name}()' "
                f"gap_type='{actual}' but module '{module_name}' "
                f"requires '{expected_type}'"
            )

    return violations


def check_gate_d(module_path: str, filename: str) -> list[str]:
    """GATE D: flag fail_wire'd functions that contain 'raise' (gap-spam risk).

    Returns list of warnings (not blocks — requires judgment). A function whose
    @fail_wire carries control_flow_exempt=(...) is NOT flagged: that keyword IS
    the encoded judgment (R-F1784) — the gap-spam concern has been addressed.
    """
    warnings = []
    if filename in FULLY_EXEMPT_MODULES:
        return warnings  # wiring infrastructure (carries example decorators in docstrings)
    fns = scan_public_functions(module_path)
    wired = fail_wire_decorators(module_path)

    # Exceptions fail_wire skips by default (R-F1784) are NOT gap-spam risks —
    # HTTPException etc. never reach record_gap. Bare re-raise ("reraise")
    # propagates an already-caught exception and is ambiguous statically, so it
    # alone doesn't warrant a warning.
    try:
        from .wire import _CONTROL_FLOW_EXC_NAMES as _DEFAULT_CF
    except Exception:
        _DEFAULT_CF = frozenset()
    benign = set(_DEFAULT_CF) | {"reraise"}

    for fn in fns:
        if not fn["has_raise"]:
            continue
        info = wired.get(fn["name"])
        if info is None:
            continue  # not wired
        if info.get("control_flow_exempt"):
            continue  # judgment encoded — control-flow raises won't gap
        uncovered = fn.get("raise_types", set()) - benign
        if not uncovered:
            continue  # only control-flow / re-raise — no gap-spam risk
        warnings.append(
            f"{filename}:{fn['lineno']} FAIL_WIRE'd function "
            f"'{fn['name']}()' raises {sorted(uncovered)} — potential gap-spam. "
            f"Review: control-flow (add control_flow_exempt) or a real failure?"
        )

    return warnings


def run_all_gates(
    target_dirs: list[str] | None = None,
    target_files: list[str] | None = None,
) -> dict[str, list[str]]:
    """Run all gates across target directories and explicit files.

    Returns {gate_name: [violations]}. gate_scope is a HARD BLOCK: a configured
    target that does not exist on disk is a violation, NOT a silent skip — a
    scan that can't see a path is the worst failure mode (plan criterion #6).
    """
    if target_dirs is None:
        target_dirs = TARGET_DIRS
    if target_files is None:
        target_files = TARGET_FILES

    results: dict[str, list[str]] = {
        "gate_scope": [],
        "gate_a": [],
        "gate_b": [],
        "gate_d": [],
    }

    def _scan(fpath: str, fname: str) -> None:
        results["gate_a"].extend(check_gate_a(fpath, fname))
        results["gate_b"].extend(check_gate_b(fpath, fname))
        results["gate_d"].extend(check_gate_d(fpath, fname))

    for target_dir in target_dirs:
        if not os.path.isdir(target_dir):
            results["gate_scope"].append(
                f"configured TARGET_DIR does not exist: {target_dir} "
                f"(false-coverage risk — fix TARGET_DIRS)"
            )
            continue
        for fname in sorted(os.listdir(target_dir)):
            if not fname.endswith(".py"):
                continue
            _scan(os.path.join(target_dir, fname), fname)

    for target_file in target_files:
        if not os.path.isfile(target_file):
            results["gate_scope"].append(
                f"configured TARGET_FILE does not exist: {target_file} "
                f"(false-coverage risk — fix TARGET_FILES)"
            )
            continue
        _scan(target_file, os.path.basename(target_file))

    return results


# ── GATE C: Parametrized capability test ─────────────────────────────────
# One parametrized pytest over WIRED_MODULES: for each fail_wire'd function,
# force it to raise and assert a gap of the registered type lands.
# Generic — no hand-written test per module, but every module is proven.
#
# Usage in test file:
#   @pytest.mark.parametrize("module_name", WIRED_MODULES)
#   def test_module_fail_wire(module_name):
#       run_gate_c(module_name)

# Modules that can be tested with the generic GATE C parametrized test
# (calling with no args raises TypeError that the decorator catches).
# Modules NOT in this list need a hand-written §3c capability test that
# forces a real failure path. This is honest: GATE C is NOT a generic
# 'proves every module' gate — it proves modules whose functions raise
# on no-args. For the rest, Phase 1 must write a per-module test.
SAFE_NO_ARGS: set[str] = {
    "deep_researcher",
    "intel_ledger",
}


def get_fail_wired_functions(module_path: str) -> list[dict[str, Any]]:
    """Get all @fail_wire'd public functions in a module."""
    fns = scan_public_functions(module_path)
    wired_names = fail_wire_decorators(module_path)
    return [fn for fn in fns if fn["name"] in wired_names]


def run_gate_c(module_name: str) -> None:
    """GATE C: force each fail_wire'd function to raise, assert gap lands.
    
    Must be called from a pytest test with the wire._record_gap patched.
    """
    import importlib
    import aria_service.intel.wire as _wire

    try:
        mod = importlib.import_module(f"aria_service.intel.{module_name}")
    except ImportError:
        # Module might be in a different package
        try:
            mod = importlib.import_module(f"aria_service.routes.{module_name}")
        except ImportError:
            raise ImportError(f"Cannot import module '{module_name}'")

    fns = get_fail_wired_functions(mod.__file__)
    if not fns:
        return  # No wired functions — nothing to test

    # R-F3560 — GATE C must accept the SAME per-function types GATE B enforces, or a
    # module whose only wired function carries an override (e.g. the GDPR-severity
    # `data_protection_violation`) would fail a gate it actually satisfies. Two gates
    # disagreeing about what a function must declare is worse than either being wrong.
    _expected_types = {get_gap_type(module_name, f["name"]) for f in fns}

    recorded = []

    async def _mock(gap_type, detail, source):
        recorded.append({"gap_type": gap_type, "detail": detail, "source": source})

    original = _wire._record_gap
    _wire._record_gap = _mock
    try:
        for fn_info in fns:
            fn = getattr(mod, fn_info["name"], None)
            if fn is None:
                continue
            try:
                if fn_info["type"] == "async":
                    import asyncio
                    asyncio.run(fn())
                else:
                    fn()
            except Exception:
                pass  # Expected — the decorator caught it

            # Wait for bg task
            import time
            deadline = time.time() + 3
            while time.time() < deadline:
                if any(g["gap_type"] in _expected_types for g in recorded):
                    break
                import asyncio
                try:
                    asyncio.run(asyncio.sleep(0.01))
                except RuntimeError:
                    import time as _t
                    _t.sleep(0.01)

    finally:
        _wire._record_gap = original

    landed = [g for g in recorded if g["gap_type"] in _expected_types]
    assert len(landed) >= 1, (
        f"GATE C FAIL: module '{module_name}' expected one of "
        f"gap_type={sorted(_expected_types)} but no gap landed. Recorded: {recorded}"
    )


# ── GATE E: Live wedge monitor ────────────────────────────────────────────
# Periodic probe that checks wedge_stacks + p95 and auto-pauses if regression.
# To be wired into main.py as a background loop during Phase 1.

import httpx


async def probe_wedge_stacks(brain_url: str = "https://aria-intel.fly.dev") -> dict:
    """Probe wedge_stacks count and endpoint latency.
    
    Returns {wedge_count, p50_ms, p95_ms, ok}.
    """
    import time

    # Measure latency
    latencies = []
    async with httpx.AsyncClient(timeout=10) as c:  # no-breaker: wiring harness is test infrastructure; breaker would block tests
        for _ in range(10):
            t0 = time.monotonic()
            try:
                r = await c.get(f"{brain_url}/health/live")
                if r.status_code == 200:
                    latencies.append((time.monotonic() - t0) * 1000)
            except Exception:
                pass

    latencies.sort()
    p50 = latencies[len(latencies) // 2] if latencies else 0
    p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0

    return {
        "wedge_count": 0,  # TODO: read from /data/wedge_stacks when available
        "p50_ms": round(p50, 1),
        "p95_ms": round(p95, 1),
        "ok": len(latencies) >= 5,
    }


# ── Blocking vs advisory ────────────────────────────────────────────────────
# GATE D is advisory by design ("warnings — requires judgment", not a block):
# almost every real module raises *some* real-failure exception, and a wired
# function SHOULD gap on those — that is the whole point of §21. So a GATE D
# warning must NOT mark the harness red (it would jam the autonomous loop on
# every module forever). Only scope/A/B are hard blocks.
BLOCKING_GATES = ("gate_scope", "gate_a", "gate_b")
ADVISORY_GATES = ("gate_d",)


def has_blocking_violations(results: dict[str, list[str]]) -> bool:
    """True iff any BLOCKING gate (scope/A/B) has a violation. GATE D excluded."""
    return any(results.get(g) for g in BLOCKING_GATES)


# ── Print results ─────────────────────────────────────────────────────────

def print_gate_results(results: dict[str, list[str]]) -> None:
    """Print gate results in a readable format."""
    for gate, violations in results.items():
        advisory = gate in ADVISORY_GATES
        print(f"\n=== {gate.upper()}{' (advisory)' if advisory else ''} ===")
        if violations:
            label = "WARN" if advisory else "BLOCK"
            for v in violations:
                print(f"  {label}: {v}")
        else:
            print("  PASS")

    blocked = has_blocking_violations(results)
    advisories = sum(len(results.get(g, [])) for g in ADVISORY_GATES)
    print(f"\n{'=' * 60}")
    if blocked:
        print("  BLOCKING GATES FAILED — fix scope/A/B before proceeding")
    elif advisories:
        print(f"  BLOCKING GATES PASS — {advisories} advisory (GATE D) warning(s) to review")
    else:
        print("  ALL GATES PASS — harness is clean")
    print(f"{'=' * 60}")

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.

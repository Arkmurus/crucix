"""
Capability Gap Tracker — structured logging of ARIA's known limitations.

When ARIA encounters something she cannot handle (unknown file type,
unsupported registry, missing API, parse failure), this module records
it as a structured gap.  Gaps live in a capped Redis list and can be
marked resolved once a fix ships.

Phase 3 of the ARIA learning infrastructure.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from . import redis_store as rs
from .wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.intel.capability_gaps")

KEY = "crucix:aria:capability_gaps"
MAX_GAPS = 500

# F66 fix 2026-04-28: same (gap_type, detail) within this window is
# treated as a duplicate and not re-recorded. calibration_review fires
# `[adversarial_critical_failure] Mastery scores overconfident by 23%`
# on every diagnostic cycle (~5 min) — without dedupe that single signal
# wrote 288 identical entries per day and would crowd MAX_GAPS=500 in
# under 2 days, hiding real gaps from the cycle's analysis. 1h is the
# trade-off: aggressive enough to suppress diagnostic spam, lax enough
# that a recurring real gap (e.g. mastery dropping further at noon)
# still re-surfaces hourly.
_DEDUPE_WINDOW_SECONDS = 3600
#: R-F4356 (C-301) — §21a metrics for the fail-closed dedupe path.
#: Announced once per process; every subsequent deferral is counted.
_UNREADABLE_ANNOUNCED = False
_deferred_counts: dict[str, int] = {}

_DEDUPE_KEY_PREFIX = "crucix:aria:capability_gaps:dedupe:"


# ── R-F3695 — gap types whose DETAIL is unbounded, so the class is the key ──
#
# THE DEFECT, traced live on 2026-08-04. Phase A gate #3 ("0 fly ERRORs / 7d")
# was being reset by ARIA's own learning loop, via this chain:
#
#   1. symbolic_reasoner emits a `no_symbolic_rule` gap for EVERY question no
#      rule matched, with `detail=f"No rule matched: {text[:120]}"`.
#   2. The fingerprint below is `(gap_type, detail)`, and `detail` embeds the
#      QUESTION — so every question is a distinct fingerprint and the 1h dedupe
#      NEVER fires. The student loop generates a unique question per topic×region
#      cell, thousands per day.
#   3. Each undeduped gap costs four store ops (dedupe get, lpush(critical=True),
#      ltrim, set). That saturates the single writer until `state_store.lpush`
#      raises StoreWriteError.
#   4. record_gap logs that at ERROR on an `aria.*` logger, which
#      error_log_handler mirrors as `log:error` -> `is_reset_type` True ->
#      the 7-day clean streak resets to zero.
#
# Live proof: the streak anchor's `last_error` was literally the grader's own
# question — "What are the most important compliance facts and recent
# developments for lusophone?" — arriving as a `no_symbolic_rule` gap whose
# truncated payload ended `"— lpush cruc"`, i.e. StoreWriteError's own text.
#
# The root cause is the CARDINALITY, not the ERROR level. Deliberately NOT
# downgrading that ERROR: if a drop still happens after this change it means the
# store is genuinely unhealthy, and gate #3 should say so. Making the gate pass
# by logging less would be closing it by measuring less (CLAUDE.md §1).
#
# For these types the useful signal is "the reasoner is missing rules", not
# which of 4,000 phrasings missed. One gap per window carries that; 4,000 do not
# — they also evict the actionable module_bug/missing_capability entries from
# the 500-slot ledger.
_CLASS_FINGERPRINT_GAP_TYPES = frozenset({
    "no_symbolic_rule",   # symbolic_reasoner: one per unmatched question
})


# ── R-F4356 (C-301) — volatile fields, stripped by SHAPE not by allow-list ──
#
# MEASURED LIVE 2026-08-26: one LLM outage episode wrote 56 `llm_provider_failure`
# gaps in 79 seconds — 81% of every gap in the window, ~11% of the 500-slot
# ledger. `llm/fallback.py:1217` builds
#   detail=f"Provider {name} failed: kind={kind} failures={stats['failures']} …"
# and `failures` is a MONOTONIC COUNTER, so the fingerprint below hashed a fresh
# string every call and the 1h dedupe window was STRUCTURALLY unable to fire.
# Observed: failures=45, 46, 47…
#
# That is R-F3695's defect in a second gap type, and the lesson is the shape of
# its fix rather than its content: `_CLASS_FINGERPRINT_GAP_TYPES` is an
# allow-list, so it rots every time a new gap type is authored — it is STILL one
# entry long, and the flood arrived through the door it does not cover.
# Normalising by SHAPE covers the next gap type on the day it is written.
#
# The counter-risk is real and is the reason each pattern is narrow: over-
# normalising MERGES DISTINCT DEFECTS into one entry, which is strictly worse
# than the flood because the flood is at least visible. Provider names, failure
# kinds and error text are deliberately untouched; only values that cannot
# identify a defect are collapsed.
_VOLATILE_SUBS: tuple[tuple[Any, str], ...] = (
    # ISO-8601 timestamps — first, so their digits are not eaten by later rules
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}\S*"), "<TS>"),
    # run ids / uuids / hashes: a long hex run that contains at least one digit
    # (the digit test keeps ordinary words out)
    (re.compile(r"\b(?=[0-9a-fA-F]{8,}\b)(?=[a-fA-F]*\d)[0-9a-fA-F]{8,}\b"), "<ID>"),
    # named numeric fields: failures=47, attempt=3, count=12.5
    (re.compile(r"\b(\w+)=\d+(?:\.\d+)?\b"), r"\1=<N>"),
    # bare measurements: 30s, 45.5s, 12ms, 88%
    (re.compile(r"\b\d+(?:\.\d+)?(ms|s|%)\b"), r"<N>\1"),
)


def _normalise_for_fingerprint(detail: str) -> str:
    """Collapse values that vary between occurrences of the SAME defect.

    Pure: the caller's string is never mutated. Only the dedupe key is
    normalised — the full detail is still stored on the entry and still read by
    a human, which is R-F3695's stated contract.
    """
    out = detail or ""
    for pattern, repl in _VOLATILE_SUBS:
        out = pattern.sub(repl, out)
    return out


def _gap_fingerprint(gap_type: str, detail: str) -> str:
    # High-cardinality telemetry: key on the CLASS so the dedupe window works.
    # The full detail is still stored on the entry and still read by a human —
    # only the dedupe key is collapsed.
    if gap_type in _CLASS_FINGERPRINT_GAP_TYPES:
        return hashlib.md5(f"{gap_type}|__class__".encode("utf-8"),
                           usedforsecurity=False).hexdigest()
    # R-F4356: normalise BEFORE truncating, so a volatile field near the 200-char
    # boundary cannot shift the tail and defeat the collapse.
    stable = _normalise_for_fingerprint(detail)[:200]
    return hashlib.md5(f"{gap_type}|{stable}".encode("utf-8"), usedforsecurity=False).hexdigest()

VALID_GAP_TYPES = frozenset({
    # ── R-F3428 — three types already EMITTED in production but unregistered ──
    #
    # `test_rf2644` (gap-type registry drift) had been red on all three. An
    # unregistered type is not cosmetic: `record_gap` warns "Unknown gap type" and the
    # signal lands in the ledger under a name nothing filters on, so the gap is
    # recorded and effectively unreadable — the §21b "dark" condition reached by a
    # different route.
    #
    # Each names a distinct failure domain that no existing type covers, and each was
    # read at its emit site before being registered here rather than rubber-stamped:
    "data_integrity",                  # dd_evidence_store.read_artifact — evidence is
                                       # recorded as RETAINED but its artifact is
                                       # missing from disk. An append-only evidence
                                       # store that has lost a payload is a distinct
                                       # class from an engine that threw.
    "data_protection_violation",       # vetting.extract_document — personal data may
                                       # have left an approved processor boundary.
                                       # Deliberately NOT folded into engine_failure:
                                       # this is a GDPR-severity signal and collapsing
                                       # it into a generic type would bury a
                                       # regulatory obligation in ordinary noise.
    "onboarding_failed",               # portal_onboarding — a portal registration
                                       # attempt failed. Distinct from source_failure,
                                       # which is a fetch against an ALREADY onboarded
                                       # source.
    # ── R-F3793 — two more emitted-but-unregistered types, same class as above ──
    # Read at their emit sites before registering, per the R-F3428 precedent. Both
    # were checked against the nearest existing types rather than assumed distinct:
    "compliance_layer_failure",        # dd_layer_extensions._wire_layer_failure:64 —
                                       # a DD COMPLIANCE layer threw. Deliberately not
                                       # folded into `layer_extension_failure`, which
                                       # the SAME file already emits 429 lines later
                                       # (:493) for a generic extension fault, nor into
                                       # `dd_layer_failure` (dd_orchestrator:12655),
                                       # which is the orchestrator's own layer signal.
                                       # Three emitters, three scopes; collapsing them
                                       # would make the compliance failure unfilterable.
    "invalid_state_transition",        # relationship_intelligence
                                       # .record_operator_action_rejected:506 — an
                                       # operator advanced an access request from a
                                       # state that forbids it. Not `validation_failure`
                                       # (bad input) and not `dialogue_state_failure`
                                       # (dialogue_state.py:472, a different machine):
                                       # this is a refused transition on the GDPR access
                                       # -request workflow, and the emitter is careful to
                                       # carry no contact PII.
    # ── R-F4280 — three more emitted-but-unregistered types, same class as above ──
    # `test_rf2644` was red on all three and is NOT in the recorded suite baseline,
    # so they drifted in after 2026-08-17. Each was READ at its emit site before
    # being registered here, per the R-F3428 precedent, rather than rubber-stamped
    # to turn the drift guard green.
    "boot_regression_check_skipped",   # boot_snapshot_diff.record_verdict:179 — the
                                       # boot state-regression check COULD NOT RUN
                                       # because the counters were not comparable.
                                       # Its own detail is explicit: "NO claim is made
                                       # either way — this is not an all-clear." That
                                       # is a could-not-measure signal, which is a
                                       # different sentence from engine_failure
                                       # (nothing threw) and from a measured
                                       # regression. Collapsing it into either would
                                       # turn "I could not check" into "I checked" —
                                       # the absence-reads-as-health shape §1 records.
    "ranking_amplification",           # knowledge.py:2864 — a worker spent a large
                                       # fraction of its window on O(corpus) knowledge
                                       # ranking. Nothing failed, so engine_failure is
                                       # wrong; this is the C-95 self-worsening class,
                                       # where cost grows with the corpus under an
                                       # infinite-memory policy (§7). It needs its own
                                       # name because the response is to fix an
                                       # algorithm, not to retry an operation.
    "resolution_enforcement_failure",  # companies_house.enforce_resolution_response
                                       # :2081 (R-F4144) — the identity-gate enforcer
                                       # met a malformed trusted resolution context and
                                       # fell back to a refusal. LOAD-BEARING BEYOND
                                       # ITS SITE: R-F4278 keeps `tooluse_resolution`
                                       # advisory ONLY because that enforcement
                                       # demonstrably replaces the model's answer, and
                                       # its recorded reversal condition is "if
                                       # enforcement is ever removed from a response
                                       # path". While this type was unregistered, an
                                       # enforcement failure landed under a name
                                       # nothing filters on — so the evidence the
                                       # advisory decision rests on was unobservable.
    # ── R-F3520 — two more emitted-but-unregistered types, same class as above ──
    # Read at their emit sites before registering, per the R-F3428 precedent, rather
    # than rubber-stamped to turn the drift guard green.
    "storage_failure",                 # news_monitor._ingest_article (R-F3486) and
                                       # dd_orchestrator._read_watchlist_or_skip
                                       # (R-F3506/R-F3520) — a DURABLE store could not
                                       # be read or written, so the operation was
                                       # deliberately SKIPPED rather than completed on
                                       # bad data. Not engine_failure: nothing threw and
                                       # no logic is wrong; the store was unavailable
                                       # and the code correctly declined to act. That
                                       # distinction is the whole point of the signal —
                                       # it says "a mutation was dropped to protect
                                       # durable state", which is what an operator needs
                                       # to know to retry it.
    "config_conflict",                 # circuit_breaker.get_breaker (R-F3458) — a
                                       # caller's configuration was ACCEPTED and then
                                       # silently discarded, because first registration
                                       # wins and a later get_breaker(name,
                                       # failure_threshold=...) returns the existing
                                       # breaker unchanged. Nothing failed, which is
                                       # exactly why it needs its own name: the caller
                                       # believes a threshold is in force that is not.
    "file_parse",
    "registry_lookup",
    "api_missing",
    "knowledge_gap",
    "timeout",
    "format_unsupported",
    "embedder_failure",                # R-F895: semantic_search encode failed
                                       # (EncodeLockTimeout under load = wedge
                                       # precursor, or model error)
    # Core Self-Development Loop (Clauses 17/18/19) — shipped 2026-04-15
    "verified_contradiction",          # verified_intel: sources disagree
    "source_seeding_suspected",        # search_doctrine: uniformity cluster
    "insufficient_public_intel",       # search_doctrine: 3-attempt exhaustion
    "source_validator_rejected",       # source_validator: quality gate fail
    "source_auto_suspended",           # web_atlas: reliability EMA < 0.40
    "paraphrase_violation",            # chat post-processor: verbatim ≥200
    "adversarial_critical_failure",    # adversarial_challenge: CRITICAL fail
    "mistake",                         # generic bridge from mistake_ledger
    # Operational-signal bridges from metacognitive/gaps.py (2026-04-22).
    # Before this bridge, ecosystem_reassess read only capability_gaps.recent()
    # and never saw live memory-miss / research-failure signals from the
    # streaming chat path — the 24/7 learning loop was effectively blind
    # to production WhatsApp traffic.
    "operational:memory_miss",
    "operational:research_failure",
    "operational:confidence_failure",
    "operational:output_rejection",
    # Layer 5c commercial coherence (2026-04-22) — structural deal flags
    # surfaced by commercial_coherence.assess_commercial_coherence().
    "commercial_coherence_elevated",
    # Symbolic reasoner miss (2026-04-24) — no handler matched a question
    # that cleared the doc-review / workflow-command / multiparty guards.
    # Emitted by symbolic_reasoner.reason() via brain_hook. Registering
    # the type silences the "Unknown gap type" warning that fired on
    # every miss.
    "no_symbolic_rule",
    # Web search exhaustion (2026-04-27) — all backends returned 0 results
    # for a research-cycle query. Emitted by researcher._web_search when
    # every configured backend (crossref/openalex/semantic-scholar/brave)
    # comes back empty. Was generating "Unknown gap type" warnings on
    # every weak-cell search.
    "search_zero_results",
    # F74 batch (2026-04-28) — types that production code legitimately
    # emits but were not registered. Each was generating an "Unknown
    # gap type" WARNING at first emit per process, which the
    # error_log_handler then mirrored into the error ledger as noise.
    # Grepped from `gap_type=` callers in aria_service/. Grouped by
    # source module:
    "compliance_gate",                 # regional_bright_lines.check_text — UAE_HOUTHI / DRC / Libya etc.
    "counterparty_risk_materialised",  # autonomous DD — RED outcome on a target
    "defective_dd_run",                # dd_orchestrator — run aborted before report
    "domain_ownership",                # virtual_office_registry — domain RDAP ambiguity
    "euc_critical_clauses_missing",    # euc_library — required clause absent from contract
    "ghost_entity",                    # ghost_detector — shell / no-substance counterparty
    "pdf_parse_failure",               # ocr / pdf_ingest — non-recoverable parse error
    "provider_disagreement",           # ensemble — providers gave contradictory signals
    "rescreen_errors",                 # dd_orchestrator.rescreen_watchlist — non-zero error count
    "sanctions_hit",                   # sanctions — confirmed designated party
    "stalled_cell",                    # heatmap — region/topic with no movement over window
    "unverified_citation",             # cited_artifact_verifier — claim could not be grounded
    "format_unsupported",              # already in set above (kept for clarity, fine if dup)
    # F94 batch (2026-04-30) — circuit-breaker failure reasons. Before
    # this, every tripped breaker recorded gap_type="timeout" regardless
    # of whether the upstream said 402 (Brave billing), 429 (Semantic
    # Scholar rate limit), or 401/403 (auth). Triage was reading those
    # as transient network issues when the actual fix is operator-side
    # (key rotation, plan upgrade, caller-side rate limiting).
    "billing_required",                # 402 / credit exhausted
    "rate_limited",                    # 429 / quota exceeded
    "auth_failure",                    # 401 / 403
    # R-F708 (2026-05-18) — vendor health dropped below 50% live.
    # Emitted by vendor_registry.all_vendor_statuses() via brain_hook
    # when live_pct < 0.5. Was logging "Unknown gap type" on every
    # dashboard poll while >50% of vendors were dark (acled +
    # worldbank_debarred + worldbank_documents in current state).
    "vendor_outage",
    # R-F1250 batch — all gap types used in production but not registered.
    # Each was generating "Unknown gap type" WARNING at first emit.
    # Grouped by source module:
    "engine_failure",                    # wire_failure default — used everywhere
    "stage_blocked",                     # self_improve:stage_improvement
    "powershell_execution_failure",      # powershell_master:execute
    "access_denied",                     # self_improve:read_own_code
    "file_not_found",                    # self_improve:read_own_code
    "read_failure",                      # self_improve:read_own_code
    "validation_failure",                # self_improve:stage_improvement
    "truncation_guard",                  # self_improve:stage/deploy
    "deploy_failure",                    # self_improve:deploy_improvement
    "validation",                        # self_improve:discard_improvement
    "not_found",                         # self_improve:discard_improvement
    "rollback_failure",                  # self_improve:rollback_improvement
    "boot_state_regression",             # main — boot-time state check
    "reasoning_library_empty_at_boot",   # main — reasoning library empty
    "agent_cycle_failure",               # main — agent cycle error
    "reviewer_exhaustion",               # claude_reviewer
    "cost_anomaly",                      # cost_monitor
    "cost_cap_hit",                      # cost_monitor, safety
    "portal_registration",               # gap_detector
    "redis_failure",                     # safety
    "configuration_error",               # sovereign_llm
    "llm_error",                         # sovereign_llm
    "wa_notification_failure",           # wa_notifier
    "integration_failure",               # email_outbound
    "contract_registration_failure",     # agent_contract
    "agent_registration_failure",        # agent_registry
    "code_synthesis_error",              # autonomous_coder
    "source_failure",                    # bd_strategy
    "security_threat",                   # behavioural_anomaly, content_scanner, etc.
    "auto_tune_failure",                 # calibration_auto_tune
    "compliance_capture_failure",        # compliance_watch
    "compliance_chain_verification_failure",  # compliance_watch
    "compliance_stats_failure",          # compliance_watch
    "compliance_risk",                   # compliance_watch
    "compliance_observation",            # compliance_watch
    "clarification_required",            # comprehension
    "hallucination_guard",               # confidence_footer
    "performance",                       # continuous_profiler, deadlock_detector, memory_leak_detector
    "contract_review_regression",        # contract_intelligence
    "corpus_crawl_high_error_rate",      # corpus_manager
    "archive_failure",                   # dd_case_archive
    "layer_extension_failure",           # dd_layer_extensions
    "sweep_intelligence_failure",        # dd_orchestrator
    "dialogue_state_failure",            # dialogue_state
    "entity_no_jurisdiction",            # document_entity_bridge
    "compliance_engine_failure",         # eliminated_weapons_watchlist, end_user_granularity, etc.
    "email_send_failure",                # email_reader
    "entity_graph_singleton",            # entity_graph
    "honesty_judge_failure",             # honesty_judge
    "honesty_judge_unsupported_claims",  # honesty_judge
    "file_integrity_failure",            # kaspersky_mitigation
    "file_integrity_warning",            # kaspersky_mitigation
    "file_recovery_failure",             # kaspersky_mitigation
    "publisher_adapter_failed",          # known_publisher_router
    "quality_regression",                # llm_eval_framework
    "alert_failure",                     # proactive
    "clause_13_uncited_current",         # propaganda_guard
    "clause_15_no_citation_match",       # response_verifier
    "query_intent_unclear",              # query_decomposer
    "rag_offload_event",                 # rag_store
    "self_claim_violation",              # self_claim_guard
    "infra_degraded",                    # self_healing
    "recovery_failure",                  # self_healing
    "recovery_needed",                   # self_healing
    "ecosystem_repair",                  # self_healing
    "contract_violation",                # self_healing
    "predictor_degraded",                # predictor
    "premise_verifier_veto",             # premise_verifier
    "pure_promise_no_resolver",          # pending_actions
    "shell_company_pattern",             # companies_house
    "counterparty_deception_high",       # deception_detection
    "counterparty_deception_medium",     # deception_detection
    "cost_pressure",                     # tasks
    "self_runtime",                      # tasks
    "blackout",                          # self_restart — heartbeat stale
    "delivery_failure",                  # outcome_wire — delivery to surface failed
    "codebase_health",                   # R-F1592: eagle_eye emits this (code
                                         # smells / stale modules) but it was
                                         # unregistered → gaps silently rejected
    # R-F2551 (Phase 0.3) — LLM provider call failed (timeout / 5xx / connection /
    # stale endpoint, e.g. the sovereign RunPod /v1/models 404). Distinct from the
    # SPECIFIC provider failures already registered (billing_required=402,
    # rate_limited=429, auth_failure=401/403): this is the GENERIC "the provider did
    # not return" class the LLM chain emits. Was firing "Unknown gap type
    # 'llm_provider_failure'" repeatedly live (2026-07-11), which both spammed the log
    # and demoted a real provider-routing signal to noise. Registering it makes the
    # provider-failure taxonomy first-class (prereq for task-value LLM routing).
    "llm_provider_failure",
    # R-F2555 — the Golden Intel promotion bridge (golden_intel_bridge.py) records
    # this when a source adapter cannot fetch (never-false-clean: a dead source is a
    # gap, not a silent "all clear") or when the signal store write fails. First-class
    # so the coder loop can act and no "Unknown gap type" warning is emitted.
    "golden_intel_promotion_failure",
    # R-F2642 (2026-07-16) — SearXNG returned 0 results for a configured
    # instance, i.e. every upstream engine blocked us. Emitted by
    # web_search.py:593 via engine_wiring.wire_failure since R-F1656/R-F1657,
    # but never registered here — so every emit logged "Unknown gap type
    # 'search_all_engines_blocked' — recording anyway" (:259). The gap WAS
    # still recorded (record_gap warns, it does not drop), so this is log
    # noise + a real search-degradation signal demoted to noise, NOT data
    # loss. Same class as the already-registered "search_zero_results"
    # (2026-04-27) and "llm_provider_failure" (R-F2551); registering it makes
    # the search-degradation taxonomy first-class and silences the warning.
    "search_all_engines_blocked",
    # ── R-F2644 (2026-07-16) — registry drift backlog ────────────────────────
    # 42 gap types were emitted by production code but never registered, so each
    # logged "Unknown gap type ... — recording anyway" (:259) on every emit.
    # NOT data loss (record_gap warns then records), but it demoted real signals
    # to noise and mirrored the warnings into the error ledger.
    #
    # ROOT CAUSE — the guard was vacuous, not missing. F74 added
    # test_all_emitted_gap_types_are_registered whose docstring claims it is
    # "the set produced by grep -rn 'gap_type=' aria_service/" and that "if a
    # future commit adds a new gap_type without registering it, this test
    # fails". It could not: the set was a HARDCODED 24-entry snapshot of
    # 2026-04-28, so it passed forever while the tree drifted underneath it —
    # the same vacuous-certification class as gate #3 (R-F2622). R-F2644
    # replaces it with a real scan of the tree (test_rf2644_gap_type_registry_
    # drift.py), which is what keeps this list honest from here.
    #
    # Every entry below is a real, distinct type from a live emit site; none are
    # typos. "module_bug"/"missing_capability" are the canonical GapType names
    # (§21e). Membership only gates the :259 warning — nothing branches on it —
    # so registering these is behaviourally inert.
    "agent_registry_failure",           # agent_registry.py:921
    "all_general_web_dead",             # web_search.py:1767
    "capability_test_missing",          # self_coder.py:1043
    "captcha_unsolvable",               # captcha_solver.py:91
    "code_generation_failure",          # self_improve.py:2948
    "constitutional_block",             # self_improve.py:831
    "contract_review_failure",          # routes/aria.py:10812
    "credential_expired",               # web_integrity_agent.py:1232
    "critical_failure",                 # self_improve.py:2802
    "dd_layer_failure",                 # dd_orchestrator.py:8130
    "deploy_verification_failure",      # main.py:3049
    "eval_judge_call_failed",           # eval_judge.py:158
    "eval_judge_unparseable",           # eval_judge.py:168
    "guardian_audit_failure",           # guardian/audit.py:55
    "guardian_safety_delivery_failure", # guardian/gateway.py:203
    "hallucinated_api",                 # self_coder.py:851
    "ingest_failure",                   # routes/aria.py:22558
    "input_error",                      # sanctions_canonical/lookup.py:338
    "llm_failure",                      # self_improve.py:1349
    "llm_fallback",                     # model_router.py:448
    "llm_unreachable",                  # aria_llm_provider.py:165
    "low_confidence_classification",    # dual_use_classifier.py:304
    "metacognitive_failure",            # metacognitive/engine.py:442
    "missing_capability",               # competitive_frame.py:127 (GapType)
    "module_bug",                       # dd_orchestrator.py:102 (GapType)
    "onboarding_key_unverified",        # portal_registry.py:2343
    "orphaned_machine",                 # autonomous/fly_deployer.py:352
    "pdf_fetch_failure",                # crawl_enhancements.py:334
    "persistence_failure",              # dd_orchestrator.py:7525
    "procurement_lookup",               # sources/usaspending.py:108
    "rate_limit_pressure",              # llm/rate_limiter.py:294
    "sanctions_coverage_drift",         # sanctions_canonical/store.py:254
    "sanctions_source_unavailable",     # sanctions.py:827
    "search_backend_failure",           # web_search.py:524 (+7 sites)
    "shadow_backpressure",              # model_router.py:364
    "source_uptime_degraded",           # source_uptime_monitor.py:461
    "source_uptime_no_sources",         # source_uptime_monitor.py:326
    "strategic_improvement",            # strategic_evolution.py:502
    "test_framework_gap",               # autonomous/tasks.py:1052
    "tier_exhaustion",                  # llm/tier_router.py:213
    "user_model_failure",               # user_model.py:575
    "source_registry_unreadable",       # source_validator.py — R-F3908: the atlas
                                        # family index could not be READ, so registry
                                        # health and auto-suspend are blind. Distinct
                                        # from source_uptime_degraded (a SOURCE is
                                        # failing): here the INSTRUMENT is down, and
                                        # collapsing the two would report our own
                                        # blindness as the sources' fault — the C-29
                                        # error in a new place.
    "web_integrity_failure",            # web_integrity_agent.py:936
    # ── R-F2907 (2026-07-23) — drift since the R-F2644 sweep ─────────────────
    # Caught by test_rf2644_gap_type_registry_drift, which is the REAL scan that
    # replaced F74's vacuous hardcoded snapshot — so the guard did its job here.
    # Same inert change as the batch above: membership only gates the :259
    # "Unknown gap type" warning, nothing branches on it. Registering keeps the
    # coder-maturity and deploy-freshness signals first-class rather than noise.
    "autonomous_gold_lane_not_earned",   # self_coder.py:1996, self_improve.py:2239
    "autonomous_gold_lane_unavailable",  # self_improve.py:392
    "source_seed_failure",               # defence_source_seed.py:677
    "stale_base_deploy",                 # self_improve.py:1029
    # ── R-F3096 (2026-07-26) — the evidence contract's own rejection signal ───
    # Emitted by dd_evidence_standard.py:358 when a record fails the strict
    # evidence contract (timeout/unavailable payload, unknown field, NaN/inf,
    # bad scope version, malformed hash or snapshot URI). Landed with R-F3083 and
    # was NEVER registered — so every rejected record logged "Unknown gap type
    # 'evidence_contract_violation' — recording anyway" at :259.
    #
    # WORTH NOTING FOR NEXT TIME: test_rf2644_gap_type_registry_drift CAUGHT this,
    # named the exact file:line, and was RED at HEAD — the guard did its job and
    # the work shipped past it anyway. Per CLAUDE.md §16 a new R-number must not
    # add to the failing-test count; per §23 "RUN it, don't claim it". The guard is
    # only as good as the decision to run it.
    #
    # Registration is inert (membership only gates the :259 warning; nothing
    # branches on it) but it is what makes a contract REJECTION a first-class
    # signal the gap_detector/self_coder loop can route (§21e) rather than noise.
    "evidence_contract_violation",       # dd_evidence_standard.py:358
    # ── R-F3109 (2026-07-26) — the structured-output gateway's rejection signal ─
    # Emitted by llm/structured.py when a model ANSWERED but the reply could not
    # be parsed or did not match the requested shape. Deliberately distinct from
    # the existing llm_* types, which all mean the provider never gave us a reply:
    # "spoke and was rejected" and "never spoke" are different failures, and only
    # the second is a coverage gap. Same distinction R-F3101 drew between an
    # adapter's `empty` and `unavailable`. Registered WITH the code that emits it,
    # rather than after a drift test goes red (the R-F3096 lesson).
    "llm_invalid_output",                # llm/structured.py
    # R-F4048 (C-107) — emitted by R-F4038's security-audit wiring
    # (security_protocol._wire_audit_outcome). Registered here because
    # R-F3428 records that an unregistered type is NOT cosmetic: `record_gap`
    # logs "Unknown gap type", which error_log_handler then mirrors into the
    # error ledger as noise. Caught by the R-F2644 drift test on the very next
    # run — registered WITH the emitter, per the R-F3096 lesson.
    #
    # Deliberately absent from gap_detector.AUTONOMY_LEVEL, whose
    # `.get(gap_type, (False, False, False))` default means an unrecognised
    # type is NEVER auto-fixable. A security finding must not be handed to the
    # coder to fix unattended.
    "security_audit_finding",            # intel/security_protocol.py
    # R-F4048 (C-107) — emitted by R-F3945 (C-39) when OpenSanctions cannot
    # answer and screens fall back to the local canonical floor, so the DD
    # marks un-consulted lists UNAVAILABLE rather than CLEAN. Landed
    # 2026-08-13, AFTER the 2026-08-09 suite baseline was recorded, so the
    # R-F2644 drift test went red without appearing in the known-failure set —
    # found while registering the type above.
    "sanctions_coverage_degraded",       # intel/sanctions.py
    # ── R-F4316 (C-264) — the teaching loop stalled and nothing said so ──
    "collab_question_stalled",         # collab_bridge.announce_stalled_questions —
                                       # ARIA asked Claude something and no Claude
                                       # session has answered. Claude's side of the
                                       # bridge is serviced only when a session runs
                                       # the inbox, so the wait is invisible unless
                                       # something says it out loud (§19e). Bounded:
                                       # one gap per QUESTION, not per 2-minute
                                       # drain, so it cannot flood the ledger.
    # ── R-F4311 (C-263) — the escalated mastery grade ──
    "mastery_miss",                    # tasks._grade_via_full_reasoning — ARIA
                                       # answered from her OWN memory and it did
                                       # not match what she had just read. A real
                                       # recall failure, and the reason the
                                       # escalated path is allowed to LOWER mastery:
                                       # a grade that can only credit is an
                                       # upward-only ratchet on a Phase A gate.
    "mastery_unmeasured",              # same grader — the sample could NOT be
                                       # graded (no own-memory context survived the
                                       # anti-circularity filter, the model said
                                       # INSUFFICIENT, an instrument failed, or the
                                       # no-memory control scored just as well so
                                       # the answer is not evidence about ARIA).
                                       # Emitted deliberately: the defect C-263
                                       # fixed was samples vanishing SILENTLY, so
                                       # "could not measure" has to be visible.
})


@fail_wire(module="capability_gaps", gap_type="engine_failure")
async def record_gap(
    gap_type: str,
    detail: str,
    message_context: str = "",
    source: str = "",
    user_id: str = "",
    sector: str = "",
    severity: "int | str" = 0,
    title: str = "",
) -> dict:
    """Record a capability gap to Redis.

    Args:
        gap_type: one of VALID_GAP_TYPES
        detail: human-readable description of the gap
        message_context: optional snippet from the user message that triggered it
        source: optional identifier for where the gap was detected
        user_id: R-F56 — authenticated user id when the gap surfaced
                 from a chat / DD turn. Empty for sweep / autonomous.
        sector:  R-F56 — persona sector (broker / oem_export / compliance /
                 banking_insurance / journalist / government_acquisition).
                 Carried on every gap entry so per-sector reports can
                 surface "compliance officers have hit X gap N times".
        severity: R-F1843 — optional severity tag (int 1-5 or a string like
                 "HIGH"). The self-monitoring agents (continuous_profiler,
                 deadlock_detector, memory_leak_detector, web_integrity_agent)
                 already pass this; before R-F1843 record_gap rejected it with
                 TypeError, so those monitors' gaps were SILENTLY DROPPED (dark
                 §21 wiring). Accepted + stored now; default 0 = unspecified.
        title: R-F2149 — optional title for the gap. Used by continuous_profiler,
               memory_leak_detector, and deadlock_detector to provide a concise
               headline alongside the detail. Stored in the gap entry if provided.

    Returns:
        The stored gap entry dict.
    """
    if gap_type not in VALID_GAP_TYPES:
        logger.warning("Unknown gap type %r — recording anyway", gap_type)

    # F66 dedupe (2026-04-28): same (gap_type, detail) within window = no-op.
    fingerprint = _gap_fingerprint(gap_type, detail)
    dedupe_key = _DEDUPE_KEY_PREFIX + fingerprint

    # ── R-F4356 (C-301) — the dedupe read must FAIL CLOSED ──────────────────
    #
    # This was non-strict `rs.get`, whose R-F1 None-on-error contract makes "the
    # store timed out" indistinguishable from "no sentinel" — so a struggling
    # store answers "not a duplicate" and the gap is written. Measured live
    # 2026-08-26: 13 dedupe reads timed out at 09:06:38 and the 56-gap burst
    # began at 09:06:42. A dedupe that fails OPEN under load amplifies precisely
    # the flood it exists to prevent — the §17 `spent_usd: 0.0` shape (an error
    # rendering as a measurement) applied to a guard.
    #
    # Deferring rather than writing is not caution for its own sake: the write
    # below is `lpush(critical=True)` on the SAME store, and its failure branch
    # logs at ERROR, which R-F3695 traced all the way to a Phase A gate #3
    # reset. Attempting the write when we already know the store is unhealthy
    # converts a dedupe failure into a gate reset.
    from .redis_store import StoreReadError  # real class; `rs` may be a double
    try:
        _sentinel = await rs.get_strict(dedupe_key)
    except StoreReadError as _re:
        global _UNREADABLE_ANNOUNCED
        if not _UNREADABLE_ANNOUNCED:
            # §21a — announced ONCE PER PROCESS. Every gap is deferred while the
            # store is down, so a per-call warning would be its own flood.
            _UNREADABLE_ANNOUNCED = True
            logger.warning(
                "[R-F4356] capability gap deferred — dedupe key unreadable, so "
                "novelty cannot be verified: %s. Further deferrals this process "
                "are counted, not logged.", _re,
            )
        _deferred_counts[gap_type] = _deferred_counts.get(gap_type, 0) + 1
        return {
            "deferred": True,
            "defer_reason": "store_unreadable",
            "type": gap_type,
            "detail": detail,
            "fingerprint": fingerprint,
        }
    if _sentinel:
        logger.debug(
            "Capability gap deduped within %ds window: [%s] %s",
            _DEDUPE_WINDOW_SECONDS, gap_type, detail[:80],
        )
        return {
            "deduped": True,
            "type": gap_type,
            "detail": detail,
            "fingerprint": fingerprint,
        }

    entry: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "type": gap_type,
        "detail": detail,
        "source": source,
        "message_context": message_context[:500] if message_context else "",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "resolved": False,
        "resolution": None,
        # R-F56: per-customer telemetry tags. Empty strings on legacy /
        # sweep-driven gaps preserve the existing shape.
        "user_id": (user_id or "").strip()[:64],
        "sector": (sector or "").strip()[:64],
        # R-F1843: severity tag from the self-monitoring agents (int or str).
        "severity": severity,
        # R-F2149: optional title from self-monitoring agents (continuous_profiler,
        # memory_leak_detector, deadlock_detector). Stored alongside detail.
        "title": (title or "").strip()[:200],
    }

    # R-F1351: persist the gap as a CRITICAL write. Pre-R-F1351 a dropped
    # lpush (lock contention) returned None silently and the code below still
    # logged "gap recorded" — the coder was blinded to the very bug this gap
    # was meant to surface. Now a drop raises StateWriteError → we log it
    # loudly (the gap is at least visible in the ERROR stream) and skip the
    # dedupe sentinel so the next attempt retries instead of being suppressed.
    _safe = " ".join((detail or "")[:120].split())
    try:
        from .state_store import StateWriteError
    except Exception:  # pragma: no cover
        StateWriteError = ()  # type: ignore
    try:
        await rs.lpush(KEY, json.dumps(entry, default=str), critical=True)
        await rs.ltrim(KEY, 0, MAX_GAPS - 1)
        # Dedupe sentinel only AFTER a confirmed write.
        await rs.set(dedupe_key, "1", ex=_DEDUPE_WINDOW_SECONDS)
        logger.info("Capability gap recorded: [%s] %s", gap_type, _safe)
    except StateWriteError as _e:
        # R-F2153: "no connection" during interpreter shutdown is expected
        # (~130 module-level wire_failure handlers fire when state_store._conn
        # is already None). Log at DEBUG instead of ERROR so shutdown noise
        # doesn't pollute the error ledger. Real contention (queue full, lock
        # timeout) still logs at ERROR.
        _msg = str(_e)
        if "no connection" in _msg.lower():
            logger.debug(
                "[R-F2153] Capability gap skipped (shutdown): [%s] %s — %s",
                gap_type, _safe, _e,
            )
        else:
            logger.error(
                "[R-F1351] Capability gap NOT persisted (dropped under contention) "
                "— [%s] %s — %s", gap_type, _safe, _e,
            )
        # ── R-F3703 — TELL THE CALLER the gap was dropped ───────────────────
        #
        # This returned `entry` — byte-identical to the success shape — so no
        # caller could distinguish "recorded" from "lost". record_gap is the
        # sink for `fail_wire` (intel/wire.py:44-48), the decorator applied
        # tree-wide as the structural §21a fix, so the universal failure-wire
        # silently lost signals precisely when the system was under the stress
        # that makes them matter most.
        #
        # `dropped: True` is ADDITIVE — every existing consumer reads `type` /
        # `detail` / `fingerprint` and is unaffected — but a caller that cares
        # (a retry, a degraded-mode banner, a test) can now see it.
        return {**entry, "dropped": True, "drop_reason": str(_e)[:200]}
    return entry


_RESOLVE_LOCK = None


def _get_resolve_lock():
    global _RESOLVE_LOCK
    if _RESOLVE_LOCK is None:
        _RESOLVE_LOCK = asyncio.Lock()
    return _RESOLVE_LOCK


@fail_wire(module="capability_gaps", gap_type="engine_failure")
async def resolve_gap(gap_id: str, resolution: str) -> dict:
    """Mark a gap as resolved with the fix description.

    Scans the list, patches the matching entry, and rewrites.

    Returns:
        The updated gap entry, or ``{"error": ...}`` if not found.
    """
    async with _get_resolve_lock():
        return await _resolve_gap_inner(gap_id, resolution)


async def _resolve_gap_inner(gap_id: str, resolution: str) -> dict:
    raw_entries = await rs.lrange(KEY, 0, MAX_GAPS - 1)
    entries = [json.loads(r) for r in raw_entries]

    for i, entry in enumerate(entries):
        if entry.get("id") == gap_id:
            entry["resolved"] = True
            entry["resolution"] = resolution
            entry["resolved_at"] = datetime.now(timezone.utc).isoformat()
            # Rewrite the whole list (gaps list is small, this is fine)
            await _rewrite_list(entries)
            logger.info("Gap %s resolved: %s", gap_id, resolution[:120])
            return entry

    return {"error": f"Gap {gap_id} not found"}


@fail_wire(module="capability_gaps", gap_type="engine_failure")
async def purge_resolved_type(gap_type: str) -> dict:
    """Bulk-resolve all gaps of a given type. Used after fixing the
    root cause (e.g., mastery scores reset after broken EWMA).

    Returns dict with ``purged`` count and ``gap_type``.
    """
    async with _get_resolve_lock():
        raw_entries = await rs.lrange(KEY, 0, MAX_GAPS - 1)
        entries = [json.loads(r) for r in raw_entries]

        count = 0
        now_iso = datetime.now(timezone.utc).isoformat()
        for entry in entries:
            if entry.get("type") == gap_type and not entry.get("resolved", False):
                entry["resolved"] = True
                entry["resolution"] = "bulk_purged_after_fix"
                entry["resolved_at"] = now_iso
                count += 1

        if count:
            await _rewrite_list(entries)

        logger.info("Bulk-purged %d gaps of type '%s'", count, gap_type)
        return {"purged": count, "gap_type": gap_type}


@fail_wire(module="capability_gaps", gap_type="engine_failure")
async def get_gaps(resolved: bool = False, limit: int = 50) -> list[dict]:
    """Retrieve gaps, filtered by resolved status.

    Args:
        resolved: if True, return resolved gaps; if False, unresolved.
        limit: max entries to return.
    """
    raw_entries = await rs.lrange(KEY, 0, MAX_GAPS - 1)
    entries = [json.loads(r) for r in raw_entries]
    filtered = [e for e in entries if e.get("resolved", False) == resolved]
    return filtered[:limit]


@fail_wire(module="capability_gaps", gap_type="engine_failure")
async def recent_gaps(limit: int = 50) -> list[dict]:
    """Thin alias for `get_gaps(resolved=False, limit=limit)` —
    metacognitive_journal gates on `hasattr(cg, "recent_gaps")` and
    silently skipped this seed source for weeks (added 2026-04-20).
    """
    return await get_gaps(resolved=False, limit=limit)


@fail_wire(module="capability_gaps", gap_type="engine_failure")
async def recent(limit: int = 50) -> list[dict]:
    """Same as `recent_gaps` — ecosystem_reassess uses the shorter
    name. Added 2026-04-20 to close the same silent-skip gap."""
    return await get_gaps(resolved=False, limit=limit)


@fail_wire(module="capability_gaps", gap_type="engine_failure")
async def get_gap_summary() -> dict:
    """Return counts by type, most common unresolved, and latest gaps."""
    raw_entries = await rs.lrange(KEY, 0, MAX_GAPS - 1)
    entries = [json.loads(r) for r in raw_entries]

    total = len(entries)
    unresolved = [e for e in entries if not e.get("resolved", False)]
    resolved = [e for e in entries if e.get("resolved", False)]

    # Counts by type (unresolved only)
    by_type: dict[str, int] = {}
    for e in unresolved:
        t = e.get("type", "unknown")
        by_type[t] = by_type.get(t, 0) + 1

    # Most common unresolved type
    most_common = max(by_type, key=by_type.get) if by_type else None  # type: ignore[arg-type]

    return {
        "total": total,
        "unresolved": len(unresolved),
        "resolved": len(resolved),
        "by_type": by_type,
        "most_common_unresolved": most_common,
        "latest_unresolved": unresolved[:5],
        "recently_resolved": resolved[:5],
        # R-F4356 (C-301) §21a — gaps this process DECLINED to record because the
        # dedupe key was unreadable. Surfaced here rather than left in a module
        # global: a counter nothing reads cannot tell anyone the ledger is
        # lossy, and "0 gaps recorded" would otherwise be indistinguishable from
        # "nothing went wrong". Per-process, so it resets on restart by design.
        "deferred_store_unreadable": dict(_deferred_counts),
    }


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _rewrite_list(entries: list[dict]) -> None:
    """Delete and re-push the entire gap list (for in-place mutation)."""
    from . import redis_store as _rs
    # Delete old list
    await _rs.delete(KEY)
    # Re-push in reverse so the newest is at the head
    for entry in reversed(entries):
        await _rs.lpush(KEY, json.dumps(entry, default=str))

# R-F2119 §21a — wire failure handler for capability_gaps
# R-F2150: REMOVED — this module-level code called wire_failure which is
# NOT imported in this file (NameError caught by except:pass), AND even if
# it were, it would spawn a background thread trying to lpush to an
# uninitialized state store during import — contributing to boot-time
# crashes. The fail_wire decorator on record_gap already handles failures
# at runtime. Module-shutdown signals are a nice-to-have, not worth the
# boot-time risk.

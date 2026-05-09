"""tier_router — routes LLM calls to the cheapest tier matching quality
requirements (R-F87a, 2026-05-09).

Why this module exists
──────────────────────
Phase 1 of the independence roadmap. ARIA's existing fallback chain
(`aria.llm.fallback`) treats every call as equal — primary provider
first, fallbacks on failure. That worked when only Anthropic + DeepSeek +
Groq were configured.

With Ollama / local LLM in the chain (R-F87), we need explicit
intent-based routing. A student-quiz-grading call doesn't deserve a
£0.05 Anthropic call when an 8B local model gets 95% of it right at £0.

The five tiers (cheapest → most expensive):

  TIER_LOCAL_FAST    — Ollama 8B (Llama 3.1 / Qwen 2.5)
                       student loops, classification, entity extraction,
                       signal generation, autonomous task tool dispatch
  TIER_LOCAL_REASON  — Ollama 8B with extended-thinking prompt
                       FATF typology matching, citation audit,
                       adversarial grading
  TIER_FAST_CHEAP    — DeepSeek-chat
                       DD layers 1-4, hypothesis validation,
                       document_intelligence, opportunity_detector
  TIER_FAST_GOOD     — Groq Llama 3.3 70B
                       Layer 5c commercial coherence, structured output,
                       contradictions detection
  TIER_PREMIUM       — Anthropic Claude
                       customer-facing chat, audit-grade DD output,
                       constitutional decisions, watchlist alerts

Each call site declares its INTENT. The router maps intent → tier →
provider name → existing fallback chain. The fallback chain still
handles cooldown / retry / rate-limit logic; the router just chooses
where to start.

Public API
──────────
    route_for_intent(intent: str) -> str
        Returns the provider name to use for this intent.

    select_provider(intent: str, llm: LLMProvider) -> LLMProvider
        Given an intent + the global fallback provider, returns a
        provider scoped to start at the right tier. The fallback chain
        still handles errors.

    INTENTS — frozenset of all known intents (build-time check that
              callers don't typo).
    INTENT_TO_TIER — the policy map.
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("aria.llm.tier_router")


# ── Tier definitions ──────────────────────────────────────────────

TIER_LOCAL_FAST   = "local_fast"
TIER_LOCAL_REASON = "local_reason"
TIER_FAST_CHEAP   = "fast_cheap"
TIER_FAST_GOOD    = "fast_good"
TIER_PREMIUM      = "premium"

# Tier → provider name (must match factory.py provider names)
_TIER_TO_PROVIDER = {
    TIER_LOCAL_FAST:   "ollama",
    TIER_LOCAL_REASON: "ollama",
    TIER_FAST_CHEAP:   "deepseek",
    TIER_FAST_GOOD:    "groq",
    TIER_PREMIUM:      "anthropic",
}

# Tier degradation order — when the requested tier's provider isn't
# configured, fall through to the next-best AVAILABLE provider.
# Up the chain (more expensive but more reliable), never down.
_TIER_FALLBACK_ORDER = [
    TIER_LOCAL_FAST,
    TIER_LOCAL_REASON,
    TIER_FAST_CHEAP,
    TIER_FAST_GOOD,
    TIER_PREMIUM,
]


# ── Intent → tier policy map ──────────────────────────────────────
# This is the "what gets the cheap model vs the expensive model" rubric.
# Add new intents here when shipping new code paths; the lookup is
# explicit (raise on unknown intent) so we never silently use the wrong
# tier.

INTENT_TO_TIER: dict[str, str] = {
    # ── Customer-facing (TIER_PREMIUM) ─────────────────────────────
    "chat":                       TIER_PREMIUM,
    "chat_stream":                TIER_PREMIUM,
    "audit_grade_dd":             TIER_PREMIUM,
    "watchlist_alert":            TIER_PREMIUM,
    "constitutional_decision":    TIER_PREMIUM,
    "customer_briefing":          TIER_PREMIUM,
    "compliance_opinion":         TIER_PREMIUM,

    # ── Layer 5c + structured outputs (TIER_FAST_GOOD) ─────────────
    "commercial_coherence":       TIER_FAST_GOOD,
    "contradictions_detection":   TIER_FAST_GOOD,
    "structured_output":          TIER_FAST_GOOD,
    "credential_issuance":        TIER_FAST_GOOD,

    # ── DD layers 1-4 + extraction (TIER_FAST_CHEAP) ──────────────
    "dd_layer_1":                 TIER_FAST_CHEAP,
    "dd_layer_2":                 TIER_FAST_CHEAP,
    "dd_layer_3":                 TIER_FAST_CHEAP,
    "dd_layer_4":                 TIER_FAST_CHEAP,
    "document_intelligence":      TIER_FAST_CHEAP,
    "hypothesis_validation":      TIER_FAST_CHEAP,
    "opportunity_detector":       TIER_FAST_CHEAP,
    "deep_research":              TIER_FAST_CHEAP,

    # ── Reasoning-flavoured local (TIER_LOCAL_REASON) ─────────────
    "fatf_typology_match":        TIER_LOCAL_REASON,
    "citation_audit":             TIER_LOCAL_REASON,
    "adversarial_grade":          TIER_LOCAL_REASON,
    "counter_intelligence_scan":  TIER_LOCAL_REASON,
    "ach_assembly":               TIER_LOCAL_REASON,

    # ── Local fast tier (TIER_LOCAL_FAST) ─────────────────────────
    "student_quiz":               TIER_LOCAL_FAST,
    "student_reading":            TIER_LOCAL_FAST,
    "research_extraction":        TIER_LOCAL_FAST,
    "classification":             TIER_LOCAL_FAST,
    "entity_extraction":          TIER_LOCAL_FAST,
    "signal_generator":           TIER_LOCAL_FAST,
    "autonomous_task_dispatch":   TIER_LOCAL_FAST,
    "morning_briefing":           TIER_LOCAL_FAST,
    "metacognitive_check":        TIER_LOCAL_FAST,
    "sentiment_classification":   TIER_LOCAL_FAST,
    "topic_classification":       TIER_LOCAL_FAST,
    "summary_short":              TIER_LOCAL_FAST,

    # ── Default for unspecified intents ───────────────────────────
    "default":                    TIER_FAST_CHEAP,
}

INTENTS = frozenset(INTENT_TO_TIER.keys())


# ── Override env var ──────────────────────────────────────────────

def _override_provider() -> str | None:
    """Operator override — pin all calls to one provider via
    ARIA_LLM_OVERRIDE_PROVIDER. Useful for testing one tier in
    isolation."""
    raw = (os.getenv("ARIA_LLM_OVERRIDE_PROVIDER") or "").strip().lower()
    return raw or None


def _disabled() -> bool:
    """Tier router can be disabled via env var to fall back to the
    bare fallback chain (Anthropic-first). Useful for incident response."""
    raw = (os.getenv("ARIA_TIER_ROUTER_DISABLED") or "0").strip()
    return raw in ("1", "true", "yes")


# ── Resolution ────────────────────────────────────────────────────

def _provider_available(provider_name: str, available_providers: set[str]) -> bool:
    return provider_name in available_providers


def route_for_intent(
    intent: str,
    available_providers: set[str] | None = None,
) -> str:
    """Resolve intent → tier → available provider name.

    `available_providers` is the set of provider names actually
    configured (e.g. {'anthropic', 'deepseek', 'groq'} when ollama
    isn't deployed). The router degrades up the tier ladder when the
    preferred tier's provider is unavailable.

    Returns the provider name to use. Falls back to 'anthropic' as
    last resort because that's the most likely to be configured.
    """
    if _disabled():
        return _override_provider() or "anthropic"

    override = _override_provider()
    if override:
        return override

    available = available_providers or set()

    tier = INTENT_TO_TIER.get(intent, INTENT_TO_TIER["default"])

    # Walk up the tier ladder starting from the requested tier
    start_idx = _TIER_FALLBACK_ORDER.index(tier)
    for t in _TIER_FALLBACK_ORDER[start_idx:]:
        provider = _TIER_TO_PROVIDER.get(t)
        if provider and (not available or _provider_available(provider, available)):
            return provider

    # Last resort
    return "anthropic"


def explain_routing(
    intent: str,
    available_providers: set[str] | None = None,
) -> dict[str, Any]:
    """Diagnostic — explain which tier was picked + why."""
    available = available_providers or set()
    tier = INTENT_TO_TIER.get(intent, INTENT_TO_TIER["default"])
    chosen = route_for_intent(intent, available)
    return {
        "intent":              intent,
        "intent_known":        intent in INTENTS,
        "preferred_tier":      tier,
        "preferred_provider":  _TIER_TO_PROVIDER.get(tier),
        "available_providers": sorted(available),
        "chosen_provider":     chosen,
        "router_disabled":     _disabled(),
        "override":            _override_provider(),
        "degraded": (
            chosen != _TIER_TO_PROVIDER.get(tier)
            and not _override_provider()
            and not _disabled()
        ),
    }


def summary() -> dict[str, Any]:
    """Capability-manifest entry."""
    return {
        "module":  "tier_router",
        "purpose": "Phase 1 — route LLM calls to cheapest tier matching quality",
        "tiers":   _TIER_FALLBACK_ORDER,
        "tier_to_provider": _TIER_TO_PROVIDER,
        "intents_known":    len(INTENTS),
        "router_disabled":  _disabled(),
        "override":         _override_provider(),
    }

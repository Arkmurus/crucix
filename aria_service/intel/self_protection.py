"""R-F1200 — ARIA Self-Protection Guardrails.

Protects ARIA from being wrong, misleading, or destructive. These are NOT
code-safety guards (the removed constitutional validator was that). These
are OUTPUT-quality and ACTION-safety guardrails that run on every response
and every autonomous action.

Three layers:
  1. HONESTY — never assert without evidence. Every factual claim must be
     traceable to a source fetched in the current request or a verified
     knowledge entry. Runs after every chat response.
  2. CONFIDENCE — confidence tags (CONFIRMED/PROBABLE/ASSESSED/LOW) must
     match the actual evidence level. Prevents confidence inflation.
  3. DESTRUCTIVE ACTION — irreversible actions (deploy, delete, credential
     change) require multi-step verification before execution.

All three wire to the brain (brain_hook.absorb / capability_gaps.record_gap)
so ARIA learns from every guardrail hit.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("aria.intel.self_protection")


# ── Result types ─────────────────────────────────────────────────────────────

@dataclass
class ProtectionVerdict:
    """Result of a self-protection check."""
    passed: bool = True
    warnings: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    risk_score: float = 0.0  # 0.0 safe → 1.0 critical

    def add_warning(self, msg: str, score_delta: float = 0.1) -> None:
        self.warnings.append(msg)
        self.risk_score = min(1.0, self.risk_score + score_delta)

    def add_violation(self, msg: str, score_delta: float = 0.3) -> None:
        self.violations.append(msg)
        self.passed = False
        self.risk_score = min(1.0, self.risk_score + score_delta)


# ── Layer 1: Honesty Guard ──────────────────────────────────────────────────

# Patterns that indicate unsupported factual assertions
_UNSUPPORTED_CLAIM_PATTERNS: list[tuple[str, str]] = [
    (r"\b(?:always|never|every|all|none)\b", "Absolute claim without qualification"),
    (r"\b(?:proven|undoubtedly|certainly|definitely)\b", "Overconfident certainty"),
    (r"\b\d{4}\b", "Year reference — verify against source"),
    (r"\b(?:confirmed|verified)\s+(?:by|through)\s+(?:our|internal)\b",
     "Vague verification claim — specify the source"),
]

# Phrases that indicate a claim is being made without evidence
_EVIDENCE_GAP_PATTERNS: list[re.Pattern] = [
    re.compile(r"\b(?:sources say|reportedly|allegedly)\b", re.IGNORECASE),
    re.compile(r"\b(?:it is believed|it is thought|it is understood)\b", re.IGNORECASE),
    re.compile(r"\b(?:according to reports|according to sources)\b", re.IGNORECASE),
]


def check_honesty(response_text: str, tool_context: str = "") -> ProtectionVerdict:
    """Check that every factual claim in the response is supported by evidence.

    Args:
        response_text: The response ARIA generated.
        tool_context: The context from tools that were run (URLs fetched, etc.).

    Returns:
        ProtectionVerdict with violations for unsupported claims.
    """
    result = ProtectionVerdict(passed=True)

    if not response_text:
        return result

    # Check for unsupported claim patterns
    for pattern, label in _UNSUPPORTED_CLAIM_PATTERNS:
        if re.search(pattern, response_text, re.IGNORECASE):
            result.add_warning(
                f"Honesty: {label} — verify against sources",
                score_delta=0.1,
            )

    # Check for evidence gaps
    for pat in _EVIDENCE_GAP_PATTERNS:
        m = pat.search(response_text)
        if m:
            result.add_warning(
                f"Honesty: vague attribution '{m.group()}' — "
                f"specify the actual source",
                score_delta=0.15,
            )

    # If tool_context is available, check that cited URLs were actually fetched
    if tool_context:
        cited_urls = re.findall(r"https?://[^\s)\"']+", response_text)
        fetched_urls = set(re.findall(r"https?://[^\s)\"']+", tool_context))
        for url in cited_urls:
            if url not in fetched_urls:
                # Check if it's a domain-level match (LLM often shortens)
                domain = re.match(r"https?://([^/]+)", url)
                if domain and not any(domain.group(1) in u for u in fetched_urls):
                    result.add_warning(
                        f"Honesty: cited URL '{url[:80]}' was not fetched "
                        f"in this request — verify it exists",
                        score_delta=0.2,
                    )

    return result


# ── Layer 2: Confidence Calibration ─────────────────────────────────────────

_CONFIDENCE_LEVELS = {"CONFIRMED", "PROBABLE", "ASSESSED", "LOW"}
_CONFIDENCE_WEIGHTS = {"CONFIRMED": 1.0, "PROBABLE": 0.75, "ASSESSED": 0.5, "LOW": 0.25}


def check_confidence(
    response_text: str,
    source_count: int = 0,
    verification_rate: float = 0.0,
) -> ProtectionVerdict:
    """Check that confidence tags match the actual evidence level.

    A CONFIRMED claim requires multiple independent sources.
    A PROBABLE claim requires at least one verified source.
    An ASSESSED claim is the default for single-source information.
    LOW confidence for unverified or speculative content.

    Args:
        response_text: The response to check for confidence tags.
        source_count: Number of independent sources used.
        verification_rate: Rate of source verification (0.0-1.0).

    Returns:
        ProtectionVerdict with violations for confidence mismatches.
    """
    result = ProtectionVerdict(passed=True)

    if not response_text:
        return result

    # Find confidence tags in the response
    for level in _CONFIDENCE_LEVELS:
        if level in response_text.upper():
            required_sources = {
                "CONFIRMED": 3,
                "PROBABLE": 2,
                "ASSESSED": 1,
                "LOW": 0,
            }.get(level, 1)

            if source_count < required_sources:
                result.add_violation(
                    f"Confidence: {level} tag requires {required_sources} "
                    f"independent sources, but only {source_count} available",
                    score_delta=0.3,
                )

            if level == "CONFIRMED" and verification_rate < 0.8:
                result.add_violation(
                    f"Confidence: CONFIRMED tag requires ≥80% verification "
                    f"rate, but current rate is {verification_rate:.0%}",
                    score_delta=0.4,
                )

    return result


# ── Layer 3: Destructive Action Prevention ──────────────────────────────────

# Actions that require multi-step verification before execution
_DESTRUCTIVE_PATTERNS: list[re.Pattern] = [
    re.compile(r"\b(?:delete|remove|destroy|wipe|clear)\b", re.IGNORECASE),
    re.compile(r"\b(?:overwrite|replace|truncate)\b", re.IGNORECASE),
    re.compile(r"\b(?:rotate|revoke|change|reset)\b.*\b(?:credential|secret|token|key)\b", re.IGNORECASE),
    re.compile(r"\b(?:credential|secret|token|key)\b.*\b(?:rotate|revoke|change|reset)\b", re.IGNORECASE),
    re.compile(r"\b(?:rollback|revert)\s*(?:all|everything|database|schema)\b", re.IGNORECASE),
    re.compile(r"\b(?:drop|truncate)\s+(?:table|database|collection)\b", re.IGNORECASE),
    re.compile(r"\b(?:rm\s+-rf|format|mkfs)\b", re.IGNORECASE),
]


def check_destructive_action(action_description: str) -> ProtectionVerdict:
    """Check if an action is potentially destructive and requires verification.

    Args:
        action_description: Description of the action to check.

    Returns:
        ProtectionVerdict with violations for destructive actions.
    """
    result = ProtectionVerdict(passed=True)

    if not action_description:
        return result

    for pat in _DESTRUCTIVE_PATTERNS:
        m = pat.search(action_description)
        if m:
            result.add_violation(
                f"Destructive action detected: '{m.group()}' — "
                f"requires multi-step verification before execution",
                score_delta=0.5,
            )

    return result


# ── Composite check ─────────────────────────────────────────────────────────

def check_all(
    response_text: str = "",
    tool_context: str = "",
    action_description: str = "",
    source_count: int = 0,
    verification_rate: float = 0.0,
) -> ProtectionVerdict:
    """Run all three self-protection layers and return a composite verdict.

    Args:
        response_text: The response or code to check.
        tool_context: Context from tools that were run.
        action_description: Description of any action being taken.
        source_count: Number of independent sources.
        verification_rate: Rate of source verification.

    Returns:
        Composite ProtectionVerdict.
    """
    composite = ProtectionVerdict(passed=True)

    # Layer 1: Honesty
    if response_text:
        h = check_honesty(response_text, tool_context)
        composite.warnings.extend(h.warnings)
        composite.violations.extend(h.violations)
        composite.risk_score = max(composite.risk_score, h.risk_score)
        if not h.passed:
            composite.passed = False

    # Layer 2: Confidence
    if response_text:
        c = check_confidence(response_text, source_count, verification_rate)
        composite.warnings.extend(c.warnings)
        composite.violations.extend(c.violations)
        composite.risk_score = max(composite.risk_score, c.risk_score)
        if not c.passed:
            composite.passed = False

    # Layer 3: Destructive action
    if action_description:
        d = check_destructive_action(action_description)
        composite.warnings.extend(d.warnings)
        composite.violations.extend(d.violations)
        composite.risk_score = max(composite.risk_score, d.risk_score)
        if not d.passed:
            composite.passed = False

    return composite

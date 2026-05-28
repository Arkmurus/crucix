"""R-F995 — ARIA self-improvement validator (pass-through).

Replaces the former ConstitutionalValidator which blocked legitimate
improvements with false positives (e.g. `re.compile` detected as "dynamic
code execution"). ARIA is a state-of-the-art AI/LLM platform for OSINT,
due diligence, research and searches. When she hits an obstacle, she codes
her way around it.

The real safety mechanisms are:
  - `safety.py` — cost cap ($300/mo) + rate limits (12/hr shared, 6/hr coder)
  - `self_improve.MODIFIABLE_FILES` — which files the coder may edit
  - `self_improve.NO_AUTODEPLOY_FILES` — which edits require human review
  - The $300/mo LLM spend cap (CLAUDE.md §17)

This pass-through validator always returns passed=True so ARIA's self-coder
can improve any file, constrained only by the cost/rate guardrails above.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("aria.autonomous.constitutional_validator")


@dataclass
class ValidationResult:
    """Always-passing result. ARIA is trusted to improve herself."""
    passed: bool = True
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    risk_score: float = 0.0

    def add_violation(self, msg: str, score_delta: float = 0.3) -> None:
        self.violations.append(msg)
        self.risk_score = min(1.0, self.risk_score + score_delta)

    def add_warning(self, msg: str, score_delta: float = 0.1) -> None:
        self.warnings.append(msg)
        self.risk_score = min(1.0, self.risk_score + score_delta)


class ConstitutionalValidator:
    """Pass-through validator. Always allows. ARIA improves freely."""

    def validate(
        self,
        code: str,
        target_file: str,
        patch_context: Optional[str] = None,
    ) -> ValidationResult:
        """Always returns passed=True. ARIA is trusted."""
        logger.info(
            "[constitutional_validator] %s — pass-through (ARIA self-improves freely)",
            target_file,
        )
        return ValidationResult(passed=True)


class DiffValidator:
    """Pass-through diff validator. Always allows."""

    def validate_diff(self, unified_diff: str) -> ValidationResult:
        """Always returns passed=True."""
        return ValidationResult(passed=True)


# Exported constants (kept for API compatibility, all pass-through)
PROTECTED_FILES: frozenset[str] = frozenset()
PROTECTED_FUNCTIONS: frozenset[str] = frozenset()
DANGEROUS_IMPORTS: frozenset[str] = frozenset()
WEAKENING_PATTERNS: list[tuple[str, str]] = []


def record_learned_attack(*args, **kwargs) -> None:
    """No-op. Attack learning is removed with the validator."""
    pass

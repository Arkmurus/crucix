"""R-F401 — runtime guard for architectural self-claim hallucinations.

This module is the defensive backstop for Constitution Clause 25
(aria_engine.py). Clause 25 instructs the LLM not to invent facts about
ARIA's own architecture (TTLs, eviction policies, inventory counts) —
this module SCANS THE FINAL RESPONSE TEXT for the exact patterns
observed in past hallucinations and flags or annotates them.

The guard is **soft** today (logs + appends a warning block to the
response) because the streaming chat path bypasses rewrite (per
`stream_bypass_pattern.md` and the R-F403 candidate). When R-F403 lands
the guard can be promoted to BLOCKING by setting `block_on_violation`
in the consumer.

Live evidence motivating the patterns:
  - 2026-05-13 07:27 (WhatsApp): "Knowledge Base with an 18-month TTL.
    If it is not reverified by then, I will forget it." — invented TTL,
    invented eviction. Real: knowledge.py:64 "Permanent memory — no TTL".
  - Same reply: "MEM0 Notebook ... each new entry can overwrite/compress
    older ones." — eviction claim with no code anchor.
  - Same reply: "Knowledge Base ... ~5,000–10,000 verified facts" — real
    count was 35,363 (sharded Redis snapshot at 06:17).

Usage:
    from .self_claim_guard import scan_response, suspicious_phrases
    violations = scan_response(response_text, self_introspect_ran=False)
    if violations:
        # log + (optionally) append warning block
"""
from __future__ import annotations

import re
from typing import NamedTuple


class Violation(NamedTuple):
    pattern_id: str
    phrase: str
    severity: str  # "BLOCK" or "WARN"
    advice: str


# ── R-F401 forbidden patterns ───────────────────────────────────────

# Pattern 1: TTL claims about ARIA's memory.
# "18-month TTL", "30-day TTL", "6 month expiry", "12-week retention".
_TTL_RE = re.compile(
    r"\b(?:"
        # X-unit TTL / expiry / retention
        r"\d{1,3}[\s-]*(?:month|week|day|hour|year)s?"
        r"\s+(?:ttl|expiry|expir(?:y|ation)|retention|retain|persist|"
        r"window|cycle|memory|cache)"
        r"|"
        # "TTL of X units"
        r"ttl\s+of\s+\d"
        r"|"
        # "expires after X" / "evicted after X" / "deleted after X"
        r"(?:expires?|evicted|deleted|forgotten|purged|pruned)"
        r"\s+(?:after|in|every|past)\s+\d+\s*(?:month|week|day|hour|year)"
    r")\b",
    re.IGNORECASE,
)

# Pattern 2: explicit "I will forget" / "I forget" claims
_FORGET_RE = re.compile(
    r"\b(?:"
        r"(?:i|aria)\s+(?:will|may|might|could|can|do(?:es)?)\s+forget"
        r"|"
        r"i'?ll\s+forget"
        r"|"
        r"(?:will|gets?|is)\s+forgotten"
        r"|"
        r"will\s+be\s+(?:evicted|deleted|purged|pruned|removed)"
    r")\b",
    re.IGNORECASE,
)

# Pattern 3: eviction / overwrite / compression claims about own memory
_EVICTION_RE = re.compile(
    r"\b(?:"
        r"overwrites?\s+(?:older|previous|past|earlier|stale)"
        r"|"
        r"compress(?:es|ing)?\s+(?:older|previous|past|earlier|stale)"
        r"|"
        r"prune(?:s|d|ing)?\s+(?:oldest|older|stale)"
        r"|"
        r"oldest(?:-|\s+)first\s+(?:prune|evict|delete|drop)"
        r"|"
        r"can\s+overwrite/?compress"
        r"|"
        r"each\s+new\s+entry\s+can\s+overwrite"
    r")\b",
    re.IGNORECASE,
)

# Pattern 4: approximate inventory counts when introspection block absent.
# "approximately 5,000 facts", "about 25,639 signals", "between 1,000–5,000".
# These are uncited estimates that should come from self_introspect.
_FUZZY_COUNT_RE = re.compile(
    r"\b(?:"
        r"(?:approximately|about|roughly|around|circa|~|maybe)"
        r"\s*\d[\d,]*\s*"
        r"(?:facts?|signals?|chunks?|entries|memories|memorie|neurons?|"
        r"records|items)"
        r"|"
        r"(?:between\s+)?\d[\d,]*\s*[-–—]\s*\d[\d,]*\s+"
        r"(?:facts?|signals?|chunks?|entries|memories|memorie|neurons?)"
    r")\b",
    re.IGNORECASE,
)


# ── Public API ──────────────────────────────────────────────────────

def scan_response(
    text: str | None,
    *,
    self_introspect_ran: bool = False,
) -> list[Violation]:
    """Scan a response for R-F401 forbidden patterns.

    Args:
        text: The LLM's response text.
        self_introspect_ran: True if a `[TOOL: self_introspect]` block
            fired in the current turn. When True, fuzzy-count and TTL
            phrasings are LESS severe because the LLM had real data
            available (still flagged but as WARN, not BLOCK).

    Returns:
        List of Violation tuples. Empty list = clean.
    """
    if not text:
        return []
    violations: list[Violation] = []

    # TTL claims — always BLOCK unless self_introspect surfaced a non-None TTL.
    for m in _TTL_RE.finditer(text):
        violations.append(Violation(
            pattern_id="rf401_ttl_claim",
            phrase=m.group(0),
            severity="WARN" if self_introspect_ran else "BLOCK",
            advice=(
                "Architectural TTL claim detected. Per Clause 25 + "
                "aria_infinite_memory.md, ARIA has no TTL on knowledge, "
                "ledger, RAG, or MEM0. Cite self_introspect or remove."
            ),
        ))

    # Forget claims — always BLOCK (clauses 25 + aria_infinite_memory).
    for m in _FORGET_RE.finditer(text):
        violations.append(Violation(
            pattern_id="rf401_forget_claim",
            phrase=m.group(0),
            severity="BLOCK",
            advice=(
                "'Will forget' / 'forgotten' claim about ARIA's own memory. "
                "Operator directive aria_infinite_memory.md: never forgets."
            ),
        ))

    # Eviction / overwrite — always BLOCK.
    for m in _EVICTION_RE.finditer(text):
        violations.append(Violation(
            pattern_id="rf401_eviction_claim",
            phrase=m.group(0),
            severity="BLOCK",
            advice=(
                "Eviction / overwrite / compression claim about own memory. "
                "R-F173 prune was reversed by R-F238. No eviction exists."
            ),
        ))

    # Fuzzy counts — WARN if self_introspect ran, BLOCK if it didn't.
    for m in _FUZZY_COUNT_RE.finditer(text):
        violations.append(Violation(
            pattern_id="rf401_fuzzy_count",
            phrase=m.group(0),
            severity="WARN" if self_introspect_ran else "BLOCK",
            advice=(
                "Approximate inventory count without self_introspect "
                "evidence. Cite exact live count from /health/perf "
                "or say 'I don't have the live count in this turn'."
            ),
        ))

    return violations


def suspicious_phrases() -> dict[str, str]:
    """Surface the live regex source for the test suite + operator
    dashboard. Maps pattern_id → regex source so a future contributor
    can see at a glance which phrases are blocked."""
    return {
        "rf401_ttl_claim": _TTL_RE.pattern,
        "rf401_forget_claim": _FORGET_RE.pattern,
        "rf401_eviction_claim": _EVICTION_RE.pattern,
        "rf401_fuzzy_count": _FUZZY_COUNT_RE.pattern,
    }


def render_violation_block(violations: list[Violation]) -> str:
    """Render violations as an inline warning block the chat handler
    can append to the response (soft mode) or replace it with (block
    mode, R-F403)."""
    if not violations:
        return ""
    lines = [
        "\n\n[R-F401 SELF-CLAIM GUARD — possible hallucination detected]",
    ]
    by_severity = {"BLOCK": [], "WARN": []}
    for v in violations:
        by_severity[v.severity].append(v)
    if by_severity["BLOCK"]:
        lines.append(f"  BLOCK ({len(by_severity['BLOCK'])}):")
        for v in by_severity["BLOCK"][:5]:
            lines.append(f"    · {v.pattern_id}: \"{v.phrase}\" — {v.advice}")
    if by_severity["WARN"]:
        lines.append(f"  WARN ({len(by_severity['WARN'])}):")
        for v in by_severity["WARN"][:5]:
            lines.append(f"    · {v.pattern_id}: \"{v.phrase}\"")
    lines.append(
        "  → Call /api/aria/health/perf via [TOOL: self_introspect] and "
        "cite real numbers. Anchor: Constitution Clause 25."
    )
    return "\n".join(lines)

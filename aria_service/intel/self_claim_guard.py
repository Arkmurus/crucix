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


# ── R-F407 (2026-05-13) — Redis-backed violation counters ──────────
#
# When R-F401 fires, we want the operator to SEE the violation on the
# hallucination dashboard panel (R-F407). These functions record each
# violation to a per-pattern + per-severity counter with a 24h reset,
# plus a rolling 50-entry log of recent violations with timestamp +
# phrase + trace_id for inspection.
#
# Same shape as stream_guard_observer.py counters so the dashboard
# panel can render both side-by-side.

_R_KEY_CTR_PREFIX = "aria:self_claim_guard:24h:"      # + pattern_id → int
_R_KEY_SEV_PREFIX = "aria:self_claim_guard:24h_sev:"  # + severity → int
_R_KEY_TURNS = "aria:self_claim_guard:24h_turns"
_R_KEY_RECENT = "aria:self_claim_guard:recent"        # list, capped at 50
_R_KEY_LIFETIME = "aria:self_claim_guard:lifetime"    # int


async def record_violations(
    violations: list[Violation],
    *,
    response_preview: str = "",
    trace_id: str = "",
) -> None:
    """R-F407: persist violations to Redis so the dashboard can render
    24h counts + recent log. Fire-and-forget: redis errors are
    swallowed so they never break the chat reply.

    Call this from confidence_footer post-scan. The chat handler
    already invokes scan_response() + render_violation_block() — this
    function attaches the metrics tail.
    """
    if not violations:
        return
    import json as _json
    import time as _time
    try:
        from . import redis_store as rs
    except Exception:
        return
    # Per-pattern + per-severity counters with 24h expiry.
    # R-F408 (2026-05-13): redis_store.incr() has signature
    # incr(key, amount=1) — there is no ttl kwarg. The TTL must be
    # applied via rs.expire() in a separate call.
    try:
        for v in violations:
            try:
                k_pat = _R_KEY_CTR_PREFIX + v.pattern_id
                await rs.incr(k_pat)
                await rs.expire(k_pat, 86400)
            except Exception:
                pass
            try:
                k_sev = _R_KEY_SEV_PREFIX + v.severity
                await rs.incr(k_sev)
                await rs.expire(k_sev, 86400)
            except Exception:
                pass
    except Exception:
        pass
    # Lifetime total — never expires.
    try:
        await rs.incr(_R_KEY_LIFETIME)
    except Exception:
        pass
    # Recent log — keep last 50 with timestamp + phrase + trace_id.
    try:
        entry = {
            "ts": _time.time(),
            "pattern_id": violations[0].pattern_id,
            "severity": violations[0].severity,
            "phrase": violations[0].phrase[:120],
            "count": len(violations),
            "trace_id": (trace_id or "")[:64],
            "preview": (response_preview or "")[:200],
        }
        await rs.lpush(_R_KEY_RECENT, _json.dumps(entry))
        await rs.ltrim(_R_KEY_RECENT, 0, 49)
    except Exception:
        pass


async def record_turn_observed() -> None:
    """Call once per chat turn (regardless of violation outcome) so
    the dashboard can compute a violation_rate_24h denominator.

    R-F408: redis_store.incr has no ttl kwarg — TTL set separately.
    """
    try:
        from . import redis_store as rs
        await rs.incr(_R_KEY_TURNS)
        await rs.expire(_R_KEY_TURNS, 86400)
    except Exception:
        pass


async def get_stats() -> dict:
    """R-F407: dashboard-friendly aggregation. Same shape as
    stream_guard_observer.get_stats() so the panel can render both."""
    import json as _json
    from . import redis_store as rs

    counters: dict[str, int] = {}
    severities: dict[str, int] = {"BLOCK": 0, "WARN": 0}
    recent: list[dict] = []
    turns = 0
    lifetime = 0

    pattern_ids = (
        "rf401_ttl_claim",
        "rf401_forget_claim",
        "rf401_eviction_claim",
        "rf401_fuzzy_count",
    )
    for pid in pattern_ids:
        try:
            raw = await rs.get(_R_KEY_CTR_PREFIX + pid)
            counters[pid] = int(raw) if raw else 0
        except Exception:
            counters[pid] = 0

    for sev in ("BLOCK", "WARN"):
        try:
            raw = await rs.get(_R_KEY_SEV_PREFIX + sev)
            severities[sev] = int(raw) if raw else 0
        except Exception:
            pass

    try:
        raw = await rs.get(_R_KEY_TURNS)
        turns = int(raw) if raw else 0
    except Exception:
        pass

    try:
        raw = await rs.get(_R_KEY_LIFETIME)
        lifetime = int(raw) if raw else 0
    except Exception:
        pass

    try:
        raw_list = await rs.lrange(_R_KEY_RECENT, 0, 19)
        for r in raw_list or []:
            try:
                recent.append(_json.loads(r))
            except Exception:
                continue
    except Exception:
        pass

    total_24h = sum(counters.values())
    rate = (total_24h / turns) if turns else None

    return {
        "total_violations_lifetime": lifetime,
        "turns_observed_24h": turns,
        "violations_24h_by_pattern": counters,
        "violations_24h_by_severity": severities,
        "violations_24h_total": total_24h,
        "violation_rate_24h": round(rate, 4) if rate is not None else None,
        "recent": recent,
        "mode": (
            "POST-RESPONSE SCAN — soft-mode today (logged + appended to "
            "confidence_footer). Rewrite mode is R-F403-full territory."
        ),
        "guards": "R-F401 architectural-self-claim regex scan",
    }

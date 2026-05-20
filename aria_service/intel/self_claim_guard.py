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
# R-F594 (2026-05-16): widened to allow up to 2 adjectives between the
# digit and the noun ("approximately 5,000 VERIFIED facts" was missed by
# the original regex because "verified" blocked the match).
_FUZZY_COUNT_RE = re.compile(
    r"\b(?:"
        r"(?:approximately|about|roughly|around|circa|~|maybe)"
        r"\s*\d[\d,]*\s*"
        r"(?:\w+\s+){0,2}"  # allow up to 2 adjective words
        r"(?:facts?|signals?|chunks?|entries|memories|memorie|neurons?|"
        r"records|items)"
        r"|"
        r"(?:between\s+)?\d[\d,]*\s*[-–—]\s*\d[\d,]*\s+"
        r"(?:\w+\s+){0,2}"
        r"(?:facts?|signals?|chunks?|entries|memories|memorie|neurons?)"
    r")\b",
    re.IGNORECASE,
)

# Pattern 5 (R-F594): exact-count claims about ARIA's own inventory of
# tasks / sources / countries / languages / layers without self_introspect.
# Caught 2026-05-16: ARIA emitted "34 autonomous tasks", "48/49 sources",
# "15+ countries", "7 intelligence layers" in a capability overview reply
# — real values are 78 / 431 / unknown / unknown. The existing fuzzy-count
# pattern missed these because they're stated as EXACT integers, not
# "approximately N". Pattern matches integers ≥3 followed by these
# capability nouns; small counts (≤2) are excluded to avoid trivia
# matches (e.g. "1 country", "2 sources" inside a sentence about a deal).
_CAPABILITY_COUNT_RE = re.compile(
    r"\b(?:"
        # "34 autonomous tasks", "78 scheduled tasks", "12 cron jobs"
        r"\d{1,4}\s*\+?\s*"
        r"(?:autonomous|scheduled|active|running|live)\s+"
        r"(?:tasks?|jobs?|crons?|schedulers?|engines?)"
        r"|"
        # "48/49 sources OK", "431 / 432 feeds healthy", "200 of 250 sources"
        r"\d{1,4}\s*(?:/|of)\s*\d{1,4}\s+"
        r"(?:sources?|feeds?|adapters?|registries|registers)"
        r"|"
        # Noun-before-digit ordering: "Sources 48/49 OK", "Adapters: 21/22 ..."
        r"(?:sources?|feeds?|adapters?|registries|registers)"
        r"\s*:?\s*\d{1,4}\s*/\s*\d{1,4}"
        r"|"
        # "15+ countries", "20+ jurisdictions", "12 tiers"
        r"\d{1,4}\s*\+\s*"
        r"(?:countries|jurisdictions|markets|regions|tiers|languages|"
        r"layers?|sources?|feeds?|adapters?)"
        r"|"
        # "7 intelligence layers", "10-indicator checklist"
        r"\d{1,4}[\s-]+"
        r"(?:intelligence\s+layers?|indicator\s+checklists?|"
        r"verification\s+steps?|reasoning\s+(?:steps?|chains?))"
        r"|"
        # "N autonomous capabilities" / "N defence sources"
        r"\d{2,4}\s+"
        r"(?:defence\s+sources?|intel(?:ligence)?\s+sources?|"
        r"autonomous\s+capabilities?|live\s+feeds?)"
    r")\b",
    re.IGNORECASE,
)


# Pattern 6 (R-F604): self-capability DENIAL claims.
#
# Past incident 2026-05-16 18:58 + 19:19: ARIA emitted phrases like
#   "I cannot query OFSI directly through my tooling"
#   "No UK OFSI direct access"
#   "No outbound email capability"
#   "No corporate registry direct adapters for Saudi, Panama, Bulgaria"
# while every one of those tools was wired and operational. The R-F594
# count-pattern doesn't catch these because they're qualitative
# denials, not numeric claims.
#
# The pattern matches a denial phrase + a tool keyword from the
# R-F603 inventory in proximity. The tool-keyword list is the source
# of truth for "ARIA actually has this capability".
_TOOL_KEYWORDS = (
    r"ofsi"
    r"|ofac(?:\s+sdn)?"
    r"|fcdo[\s_-]?sanctions"
    r"|(?:hm\s+treasury|uk)\s+(?:sanctions?|consolidated\s+list)"
    r"|panama\s+(?:registry|registro)"
    r"|bulgarian?\s+(?:registry|brra)"
    r"|saudi(?:\s+(?:cr|moci|registry))"
    r"|turkish?\s+mersis"
    r"|indian?\s+mca"
    r"|(?:corporate\s+)?registry\s+(?:direct\s+)?adapters?"
    r"|corporate\s+registries"
    r"|companies\s+house"
    r"|outbound\s+email"
    r"|smtp\s+bridge"
    r"|email\s+bridge"
    r"|self[_\s-]?introspect"
    r"|health\s*/\s*perf"
    r"|deep[_\s-]?research"
    r"|crawl[_\s-]?website"
    r"|dd[_\s-]?orchestrator"
    r"|due[_\s-]?diligence\s+orchestrator"
)

_SELF_DENIAL_RE = re.compile(
    r"(?:"
        # "I cannot/can't ... <tool>"
        r"\bi\s+(?:cannot|can'?t|do(?:es)?\s+not|don'?t)\s+"
        r"(?:\w+\s+){0,5}"
        r"(?:" + _TOOL_KEYWORDS + r")"
    r"|"
        # "no <tool>" / "no direct <tool> access" / "no <tool> capability"
        # Match the bare/modifier-tailed shape here; the R-F741
        # negative-result-word exemption is applied as a post-filter in
        # `scan_response()` so the regex backtracking doesn't bypass
        # it (the `ofac(?:\s+sdn)?` greedy-with-fallback variants slip
        # past a regex-level lookahead — see R-F741 comment in scan_
        # response for the post-filter detail).
        r"\bno\s+(?:direct\s+)?(?:\w+\s+){0,3}"
        r"(?:" + _TOOL_KEYWORDS + r")"
        r"(?:\s+(?:access|capability|tooling|integration|adapter|tool))?"
    r"|"
        # "<tool> is/are not available / unavailable / missing"
        r"(?:" + _TOOL_KEYWORDS + r")"
        r"\s+(?:is|are)\s+(?:not\s+available|unavailable|missing)"
    r"|"
        # "missing <tool>" / "lacking <tool>"
        r"\b(?:missing|lacking)\s+(?:\w+\s+){0,3}"
        r"(?:" + _TOOL_KEYWORDS + r")"
    r"|"
        # "we need [a] tool integration for <tool>"
        r"\bneed\s+(?:a\s+)?(?:tool\s+)?integration\s+for\s+"
        r"(?:\w+\s+){0,2}(?:" + _TOOL_KEYWORDS + r")"
    r"|"
        # "without <tool>"
        r"\bwithout\s+(?:a\s+)?(?:\w+\s+){0,2}"
        r"(?:" + _TOOL_KEYWORDS + r")"
    r")",
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

    # R-F594: exact capability counts — BLOCK without self_introspect.
    for m in _CAPABILITY_COUNT_RE.finditer(text):
        violations.append(Violation(
            pattern_id="rf594_capability_count",
            phrase=m.group(0),
            severity="WARN" if self_introspect_ran else "BLOCK",
            advice=(
                "Exact count of own tasks/sources/countries/layers stated "
                "without self_introspect. Past incident 2026-05-16: ARIA "
                "claimed '34 autonomous tasks' / '48/49 sources' — real "
                "values were 78 / 431. Call /api/aria/health/perf or say "
                "'I don't have the live count in this turn'."
            ),
        ))

    # R-F604: self-capability DENIAL claims — always BLOCK.
    # Phrases like "I cannot query OFSI" or "no outbound email capability"
    # contradict the R-F603 TOOL INVENTORY. These are never WARN: even
    # if self_introspect ran, the LLM is denying a capability it has.
    #
    # R-F741 (2026-05-20) post-filter: skip matches that are actually
    # a NEGATIVE-RESULT phrase, not a capability denial. The regex
    # itself can't reliably distinguish "no OFAC SDN match" (no list
    # hit — a finding) from "no OFAC SDN tool" (a denial) because
    # `ofac(?:\s+sdn)?` backtracks the SDN portion off and a regex-
    # level lookahead misses the result word that comes after "SDN".
    # Cheap fix: scan the next ~40 chars after each match for a
    # result-word (match/hit/result/record/finding/etc.) — if one is
    # present AND the match itself didn't end in an explicit
    # capability modifier (access/capability/adapter/etc.), it's a
    # negative finding, not a denial. Live evidence: dd_orchestrate
    # 2026-05-20 output had the rf604 guard flag "no OFAC SDN" while
    # the full phrase was "no OFAC SDN, EU consolidated, or
    # OpenSanctions match" — a no-MATCH finding, not a capability
    # claim.
    _RESULT_WORDS = re.compile(
        r"\b(?:match(?:es)?|hit(?:s)?|result(?:s)?|record(?:s)?|"
        r"entry|entries|finding(?:s)?|listing(?:s)?|return(?:s)?)\b",
        re.IGNORECASE,
    )
    _CAPABILITY_TAIL = re.compile(
        r"(?:access|capability|tooling|integration|adapter|tool)\b\s*$",
        re.IGNORECASE,
    )
    for m in _SELF_DENIAL_RE.finditer(text):
        matched = m.group(0)
        # If the match itself ends with an explicit capability modifier,
        # it's a clear denial regardless of what follows — keep it.
        if not _CAPABILITY_TAIL.search(matched):
            # Look ahead ~80 chars past the match end for a result word.
            # 80 is chosen to span typical intermediate-clause widths
            # like ", EU consolidated, or OpenSanctions match" (41
            # chars) without bleeding into the next sentence.
            tail = text[m.end():m.end() + 80]
            # Stop at the first sentence terminator so a result-word
            # from a DIFFERENT sentence doesn't suppress a denial in
            # this one.
            sentence_end = re.search(r"[.!?](?:\s|$)", tail)
            if sentence_end:
                tail = tail[:sentence_end.start()]
            if _RESULT_WORDS.search(tail):
                continue  # skip — this is a no-match finding, not a denial
        violations.append(Violation(
            pattern_id="rf604_capability_denial",
            phrase=matched,
            severity="BLOCK",
            advice=(
                "Self-capability DENIAL contradicting the R-F603 TOOL "
                "INVENTORY. The named tool IS wired and operational — "
                "describe a specific call failure if it happened, never "
                "claim the capability is missing. Past incident "
                "2026-05-16: ARIA claimed 'no UK OFSI access' while "
                "fcdo_sanctions.lookup() was actively fetching ConList.xml."
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
        "rf594_capability_count": _CAPABILITY_COUNT_RE.pattern,
        "rf604_capability_denial": _SELF_DENIAL_RE.pattern,
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
        "rf594_capability_count",
        "rf604_capability_denial",
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

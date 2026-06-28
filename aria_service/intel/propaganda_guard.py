"""Clause 13 output guard — propaganda elevation + uncited current events.

Why this exists
───────────────
Clause 13 has three sub-rules:
  (a) UNCITED CURRENT-EVENT BAN — current-event claims tagged
      [CONFIRMED] or [PROBABLE] must cite a specific source inline.
  (b) PROPAGANDA NEVER REACHES [CONFIRMED] — TIER-D-PROPAGANDA sources
      (intelslava, RVvoenkor, mod_russia, deepstateua, etc.) cannot
      be promoted past [ASSESSED — single channel].
  (c) NO TOPIC BLEED — current-event signals not directly relevant to
      the user's question must not appear in the reply.

Pre-existing enforcement was context-only: signal_correlator filters
incoming intel-ledger signals before injection. There was no OUTPUT
guard — once the LLM wrote a [CONFIRMED] tag on a current-events
claim with no inline source, the guard chain didn't catch it. Past
incident 2026-04-09 (Vision International RFQ → fabricated Lebanon
strike claim) is the canonical example of why this matters.

This module is the missing output guard. Runs after commitment_guard
in the chat pipeline.

What it catches
───────────────
  1. Confidence-tagged sentences that mention CURRENT-EVENT keywords
     (today, yesterday, this week, hours ago, breaking, just hit,
     casualty, killed, struck, captured, etc.) AND have NO inline
     citation marker (no [from URL], no [snippet #N], no named source
     in the same or adjacent sentence).
  2. Citations to known TIER-D-PROPAGANDA sources, regardless of tag.
     Downgrades [CONFIRMED]/[PROBABLE] → [ASSESSED — single channel,
     propaganda-tier source: <name>].

What it does NOT do
───────────────────
  - Topic-bleed detection (sub-rule c) — that's a context-injection
    decision, handled upstream by the regional/intel context selector
    in aria_engine. Catching topic bleed at output would require
    knowing the user's question, which this guard doesn't see.
  - General fact-checking. This is a pattern-only filter — if the
    LLM cites "Reuters reported" with no URL, that passes (named
    source attribution is acceptable per Clause 13).

Wiring: routes/aria.py post-LLM guard chain, after commitment_guard.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("aria.propaganda_guard")


# ── Current-event keyword detector ──────────────────────────────────────
# Matches words/phrases indicating a TIME-SENSITIVE claim where no
# citation = no provenance. Conservative: prefer false negatives over
# false positives so analytical claims about historical events aren't
# mangled.
_CURRENT_EVENT_PATTERNS = [
    re.compile(r"\b(today|yesterday|this\s+(?:morning|afternoon|evening|week|month))\b", re.IGNORECASE),
    re.compile(r"\b(?:in\s+the\s+last|past)\s+\d+\s+(?:hour|day|week)s?\b", re.IGNORECASE),
    re.compile(r"\b(?:hours|minutes|days)\s+ago\b", re.IGNORECASE),
    re.compile(r"\b(breaking|just\s+(?:hit|landed|broke|reported|in)|developing)\b", re.IGNORECASE),
    # Casualty / strike / military-action language
    re.compile(r"\b(killed|wounded|casualt(?:y|ies)|struck|targeted\s+by|destroyed|"
               r"captured|seized|advanced\s+(?:on|toward)|withdrew\s+from|took\s+the\s+city)\b",
               re.IGNORECASE),
    re.compile(r"\b(?:airstrike|missile\s+(?:strike|attack)|drone\s+strike|naval\s+strike|"
               r"ground\s+offensive|raid|incursion|breach)\b", re.IGNORECASE),
    # Date qualifiers within last ~30 days (rough — anything with explicit
    # day-of-week + month is plausibly recent)
    re.compile(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+"
               r"(morning|afternoon|evening|night)?\b", re.IGNORECASE),
]

# Confidence-tagged sentence — same shape as response_verifier
_TAGGED_SENTENCE_RE = re.compile(
    r"(\[(?:CONFIRMED|PROBABLE|ASSESSED)[^\]]*\])([^.!?\n]+[.!?])",
    re.IGNORECASE,
)

# Inline-citation markers that satisfy Clause 13(a).
#
# Two regexes (compiled separately so the source-name match can stay
# case-sensitive even though the prefix is case-insensitive):
#
#   _CITATION_BRACKET — explicit bracket forms ([from http://...],
#     [snippet #N], [EXTRACT N], [from ATTACHED DOCUMENT: ...]) — case-
#     insensitive on the prefix.
#
#   _CITATION_NAMED — natural-language attribution ("per Reuters", "according
#     to the Financial Times", "reported by BBC"). The source-NAME part is
#     case-sensitive (must start with uppercase) so that "per intelslava"
#     does NOT count as a citation. Past test caught the bug where re.IGNORECASE
#     on a single regex made [A-Z][a-z]+ match all-lowercase propaganda
#     channel names.
_CITATION_BRACKET = re.compile(
    r"\[from\s+http|\[snippet\s*#|\[EXTRACT\s*\d|\[from\s+ATTACHED",
    re.IGNORECASE,
)
_CITATION_NAMED = re.compile(
    r"\b(?:per|via|[Aa]ccording\s+to|[Rr]eported\s+by|[Cc]ited\s+by|"
    r"[Ss]tated\s+(?:in|by))\s+"
    r"(?:[A-Z][A-Za-z]{2,}(?:\s+[A-Z][A-Za-z]+)*|https?://)"
)

# TIER-D-PROPAGANDA source names from constitution clause 13(b). When the
# LLM cites one of these, downgrade the confidence tag.
_PROPAGANDA_SOURCES = {
    "intelslava", "rvvoenkor", "mod_russia", "tass.com", "rt.com",
    "sputniknews", "readovka", "deepstateua", "operativnozsu",
    "generalstaffzsu", "legitimniy", "presstv.ir", "cgtn.com",
    "xinhuanet.com", "global times",
}

_PROPAGANDA_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(s) for s in sorted(_PROPAGANDA_SOURCES, key=lambda s: -len(s))) + r")\b",
    re.IGNORECASE,
)


def _is_current_event(claim: str) -> bool:
    """True if the claim contains time-sensitive / military-action language."""
    return any(p.search(claim) for p in _CURRENT_EVENT_PATTERNS)


def _has_citation_in_claim(claim: str) -> bool:
    """True if the claim sentence ITSELF contains a citation marker.

    Scope is intentionally tight — same sentence only, NOT a char-
    window. Past bug 2026-04-18 evening: a 250-char lookahead was
    capturing the NEXT claim's citation as if it belonged to the
    current one. With propaganda-tier sources two sentences apart,
    that produced false negatives where the first claim looked
    cited because the second claim had 'per intelslava' in it.
    Limiting to the same sentence means each claim must carry its
    own citation — which is what Clause 13(a) actually requires.
    """
    return bool(_CITATION_BRACKET.search(claim) or _CITATION_NAMED.search(claim))


def guard(response_text: str) -> dict:
    """Run the Clause 13 output guard over a finished response.

    Returns:
      {
        "rewritten":            str,    # response with downgrades / tags
        "current_uncited":      int,    # tagged current-event claims w/o citation
        "propaganda_downgrades": int,    # tagged claims citing TIER-D source
        "tags_added":           list[str],
        "unchanged":            bool,
      }

    The rewritten text is safe to ship — every uncited current-events
    claim gets [UNVERIFIED-CURRENT] inserted after its sentence, and
    every TIER-D-PROPAGANDA citation drives the confidence tag down to
    ASSESSED with the channel name made explicit.
    """
    out = {
        "rewritten":            response_text,
        "current_uncited":      0,
        "propaganda_downgrades": 0,
        "tags_added":           [],
        "unchanged":            True,
    }
    if not response_text or len(response_text) < 30:
        return out

    rewritten = response_text
    current_uncited = 0
    propaganda_downgrades = 0
    tags_added: list[str] = []

    # ── Pass 1: TIER-D-PROPAGANDA downgrades ─────────────────────────────
    # Walk every confidence-tagged sentence; if it references a propaganda
    # source by name, rewrite the tag from CONFIRMED/PROBABLE to ASSESSED
    # with the channel made explicit (per clause 13(b)).
    def _downgrade_match(m: re.Match) -> str:
        nonlocal propaganda_downgrades
        tag, claim = m.group(1), m.group(2)
        prop_match = _PROPAGANDA_PATTERN.search(claim)
        if not prop_match:
            return m.group(0)  # unchanged
        # Only downgrade CONFIRMED / PROBABLE — ASSESSED is already the
        # ceiling per clause 13(b).
        tag_lower = tag.lower()
        if "confirmed" not in tag_lower and "probable" not in tag_lower:
            return m.group(0)
        propaganda_downgrades += 1
        channel = prop_match.group(1)
        new_tag = f"[ASSESSED — single channel, propaganda-tier source: {channel}]"
        tags_added.append(f"propaganda_downgrade:{channel}")
        return f"{new_tag}{claim}"

    rewritten = _TAGGED_SENTENCE_RE.sub(_downgrade_match, rewritten)

    # ── Pass 2: current-event claims without inline citation ────────────
    # Re-scan after pass 1 so the downgraded tags in pass 1 don't get
    # double-tagged here. We walk by index so we can check the surrounding
    # text for citation markers.
    parts: list[str] = []
    cursor = 0
    for m in _TAGGED_SENTENCE_RE.finditer(rewritten):
        tag, claim = m.group(1), m.group(2)
        # Append text before the match
        parts.append(rewritten[cursor:m.start()])

        # If this is already an ASSESSED tag (post-downgrade or original),
        # don't add UNVERIFIED-CURRENT — analyst is already cautioned.
        is_assessed_only = "assessed" in tag.lower() and "confirmed" not in tag.lower()

        # Check current-event + missing citation
        if not is_assessed_only and _is_current_event(claim):
            if not _has_citation_in_claim(claim):
                current_uncited += 1
                tags_added.append("uncited_current_event")
                parts.append(f"{tag}{claim} [UNVERIFIED-CURRENT — no source cited; clause 13(a)]")
                cursor = m.end()
                continue

        # Default: keep as-is
        parts.append(m.group(0))
        cursor = m.end()

    parts.append(rewritten[cursor:])
    rewritten = "".join(parts)

    out["rewritten"] = rewritten
    out["current_uncited"] = current_uncited
    out["propaganda_downgrades"] = propaganda_downgrades
    out["tags_added"] = tags_added
    out["unchanged"] = (rewritten == response_text)

    # Brain signal — every guard run feeds capability_gap on hits so the
    # predictor can warn when topics consistently produce uncited claims.
    try:
        import asyncio as _aio
        from . import brain_hook as _bh

        async def _emit():
            total_hits = current_uncited + propaganda_downgrades
            await _bh.absorb(
                module="propaganda_guard",
                summary=(
                    f"Clause 13 guard: {current_uncited} uncited current-events, "
                    f"{propaganda_downgrades} propaganda downgrades"
                ),
                detail=", ".join(tags_added[:5]),
                success=total_hits == 0,  # success = nothing needed correcting
                gap_type=("clause_13_uncited_current" if current_uncited >= 2 else
                          "clause_13_propaganda_cite" if propaganda_downgrades >= 1 else None),
                gap_detail=(f"{current_uncited} uncited current-events + "
                            f"{propaganda_downgrades} propaganda cites in single response"
                            if total_hits >= 2 else None),
                extra_topics=["compliance"],
            )

        try:
            loop = _aio.get_running_loop()
            loop.create_task(_emit())
        except RuntimeError:
            pass
    except Exception:
        from .engine_wiring import wire_failure as _wf
        _wf(
            module="propaganda_guard",
            detail="brain_hook.absorb failed in guard",
            gap_type="engine_failure",
            source="propaganda_guard:guard",
        )
        pass

    # R-F1001 - wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="propaganda_guard",
        summary="Guard",
        source_id="propaganda_guard:R-F1001",
    )

    return out

# R-F2119 §21a — wire failure handler for propaganda_guard
try:
    wire_failure(module="propaganda_guard", detail="module shutdown",
                gap_type="engine_failure", source="propaganda_guard:shutdown")
except Exception:
    pass

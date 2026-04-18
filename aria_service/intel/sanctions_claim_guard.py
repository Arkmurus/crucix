"""Sanctions claim guard — forces a live primary-source check when
the operator asks a compliance yes/no question.

Extracted from the F3 incident 2026-04-17 21:30-21:50: ARIA answered
"YES, F3 is sanctioned" based on a MEM0 recall of a DEFECTIVE prior
DD (the one where entity-name parsing was broken). The answer was
wrong — F3 appears on no public sanctions list. Only after the user
challenged her reasoning did ARIA run primary-source searches and
correct herself to NO.

Root cause: recall-based compliance answers are never safe. Clause 17
requires 2 independent Tier 1b/2 sources for a VERIFIED claim. A
single mem0 recall (no matter how confident) does not meet that bar.

This module:
  1. Detects sanctions yes/no question patterns in a user message.
  2. Extracts the entity name being asked about.
  3. Forces a live sanctions.fuzzy_screen() BEFORE the LLM drafts
     the answer.
  4. Returns a mandatory [SANCTIONS LIVE CHECK] context block that
     the LLM must cite as the authoritative source.

Intended to be called from the chat pipeline BEFORE LLM generation,
not after — otherwise the model could ignore the live result and go
with its prior-recall answer.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("aria.intel.sanctions_claim_guard")


# ═══════════════════════════════════════════════════════════════════════
# Intent detection
# ═══════════════════════════════════════════════════════════════════════
#
# Catches patterns like:
#   "is Acme Corp sanctioned?"
#   "is X on a sanctions list"
#   "has X been sanctioned"
#   "is X on OFAC / SDN / EU / UN / UK / OFSI"
#   "is X blocked / debarred / embargoed"
#   "is X designated"
#
# The entity capture is greedy up to the next boundary (? . \n or
# a list-name token).

_SANCTIONS_VERBS = (
    r"sanctioned|on\s+(?:a|any|the)?\s*sanctions?(?:\s+list)?|"
    r"on\s+(?:OFAC|SDN|OFSI|EU\s+consolidated|UN|UK\s+OFSI|US\s+OFAC)|"
    r"designated|blocked|debarred|embargoed|"
    r"on\s+(?:a|any|the)?\s*(?:blocked|embargo|debarment)\s+list"
)

_SANCTIONS_Q_RE = re.compile(
    r"\b(?:is|was|has)\s+(.+?)\s+(?:been\s+)?(?:" + _SANCTIONS_VERBS + r")\b",
    re.IGNORECASE,
)

# Also catches "does X appear on a sanctions list" / "any sanctions on X"
_SANCTIONS_Q_ALT_RE = re.compile(
    r"\b(?:does|any)\s+(.+?)\s+(?:appear|listed|have)\b.*?"
    r"(?:sanction|OFAC|SDN|OFSI|EU\s+consolidated|UN|debar|embargo)",
    re.IGNORECASE,
)

# Reject if the match is clearly NOT a sanctions yes/no. These are
# question shapes that include sanction keywords but aren't asking
# for a pass/fail verdict.
_REJECT_PATTERNS = (
    re.compile(r"\b(?:what|which|how\s+many|list\s+of|show\s+me|tell\s+me\s+about)\b", re.IGNORECASE),
    re.compile(r"\b(?:why|reason|mechanism|process)\b.*sanction", re.IGNORECASE),
)


def detect_sanctions_question(message: str) -> str | None:
    """If the message is asking "is X sanctioned?" yes/no, return X.
    Otherwise return None.
    """
    if not message or not message.strip():
        return None

    # Reject what/which/list-of questions — those are exploratory, not yes/no.
    for rej in _REJECT_PATTERNS:
        if rej.search(message):
            return None

    for rgx in (_SANCTIONS_Q_RE, _SANCTIONS_Q_ALT_RE):
        m = rgx.search(message)
        if not m:
            continue
        entity = (m.group(1) or "").strip()
        # Clean common trailing words
        entity = re.sub(
            r"\s+(?:currently|now|still|today|yet|already|ever|actually)\s*$",
            "",
            entity,
            flags=re.IGNORECASE,
        ).strip()
        entity = entity.strip('"\'()[]{}')
        # Reject if entity is clearly not a name (too short / just pronouns)
        if len(entity) < 3:
            continue
        if entity.lower() in ("it", "this", "that", "them", "they", "she", "he"):
            continue
        # Strip trailing punctuation
        entity = entity.rstrip(".,;:!?")
        if not entity:
            continue
        return entity
    return None


# ═══════════════════════════════════════════════════════════════════════
# Live primary-source check
# ═══════════════════════════════════════════════════════════════════════

async def live_primary_check(entity: str) -> dict[str, Any]:
    """Run sanctions.fuzzy_screen (or screen_with_aliases when available)
    against the entity name. Returns a structured result suitable for
    direct injection into the LLM context as authoritative truth.

    This is the SAME function the DD orchestrator calls in its identity
    layer. The difference is that we call it HERE, synchronously with
    the LLM prompt build, so the result is guaranteed to be present
    before the model drafts its answer.
    """
    result: dict[str, Any] = {
        "entity": entity,
        "ran_live": False,
        "verdict": "unknown",
        "matches": [],
        "top_match": None,
        "source_tool": "",
        "citation_block": "",
        "error": "",
    }
    try:
        from . import sanctions as _sanctions
        if hasattr(_sanctions, "screen_with_aliases"):
            screen = await _sanctions.screen_with_aliases(entity)
        elif hasattr(_sanctions, "fuzzy_screen"):
            screen = await _sanctions.fuzzy_screen(entity)
        else:
            result["error"] = "sanctions module missing fuzzy_screen / screen_with_aliases"
            return result
        result["ran_live"] = True
        result["source_tool"] = "sanctions.fuzzy_screen"
        # screen is expected to be a dict-like with 'matches' list
        matches = screen.get("matches") if isinstance(screen, dict) else None
        if matches is None:
            # Tolerate alternate shapes
            matches = getattr(screen, "matches", []) or []
        result["matches"] = matches
        if not matches:
            result["verdict"] = "CLEAN"
        else:
            result["verdict"] = "HIT"
            result["top_match"] = matches[0] if isinstance(matches, list) else None
    except Exception as exc:
        logger.warning("live primary sanctions check failed for %s: %s", entity, exc)
        result["error"] = str(exc)[:240]
        return result

    # Build a context block the LLM is forced to see
    if result["verdict"] == "CLEAN":
        result["citation_block"] = (
            f"[SANCTIONS LIVE CHECK — AUTHORITATIVE]\n"
            f"Entity: {entity}\n"
            f"Tool: sanctions.fuzzy_screen (live, this turn)\n"
            f"Verdict: NO MATCHES on aggregated sanctions database "
            f"(OpenSanctions, which covers OFAC SDN, EU Consolidated, "
            f"UN, UK OFSI, World Bank Debarment, and others).\n"
            f"Answer policy: if the user asks yes/no, answer NO and cite "
            f"this live check. Do NOT cite any mem0 recall or prior DD "
            f"assessment as a YES — past assessments can be defective."
        )
    elif result["verdict"] == "HIT":
        top = result["top_match"] or {}
        name_hit = (top.get("name") if isinstance(top, dict) else "") or "?"
        score = (top.get("score") if isinstance(top, dict) else "") or "?"
        result["citation_block"] = (
            f"[SANCTIONS LIVE CHECK — AUTHORITATIVE]\n"
            f"Entity: {entity}\n"
            f"Tool: sanctions.fuzzy_screen (live, this turn)\n"
            f"Verdict: MATCH(es) found — top hit {name_hit!r} (score {score}).\n"
            f"Total matches: {len(result['matches'])}.\n"
            f"Answer policy: answer YES only if the top match genuinely "
            f"refers to the same entity (not a substring coincidence). "
            f"Cite the primary list (OFAC SDN / EU / OFSI / etc.) by "
            f"following the match's source URL."
        )

    # Brain signal — every live primary check is a high-value compliance
    # signal: HITs upgrade entity tracking; CLEANs are the audit trail
    # that proves we checked.
    try:
        from . import brain_hook as _bh
        await _bh.absorb(
            module="sanctions_claim_guard",
            summary=f"Live sanctions check: {entity} → {result['verdict']} "
                    f"({len(result.get('matches', []))} matches)",
            detail=result.get("citation_block", "")[:2000],
            entity_name=entity,
            success=result["ran_live"],
            gap_type=("sanctions_hit" if result["verdict"] == "HIT" else None),
            gap_detail=(f"HIT for {entity} via {result['source_tool']}"
                        if result["verdict"] == "HIT" else None),
            confidence="CONFIRMED" if result["ran_live"] else "ASSESSED",
        )
    except Exception:
        pass

    return result


# ═══════════════════════════════════════════════════════════════════════
# Convenience wrapper for chat-pipeline integration
# ═══════════════════════════════════════════════════════════════════════

async def guard_context_block(message: str) -> str:
    """End-to-end: detect → live-check → return context block.

    Returns "" when the message is not a sanctions yes/no question.
    Returns the [SANCTIONS LIVE CHECK] block otherwise, ready to be
    prepended to the LLM context.
    """
    entity = detect_sanctions_question(message)
    if not entity:
        return ""
    result = await live_primary_check(entity)
    if result.get("citation_block"):
        return result["citation_block"]
    # Primary tool failed — emit a degradation notice so the LLM still
    # knows it was asked a yes/no and cannot rely on recall.
    return (
        f"[SANCTIONS LIVE CHECK — DEGRADED]\n"
        f"Entity: {entity}\n"
        f"Live primary-source check could not run ({result.get('error', '?')}).\n"
        f"Answer policy: do NOT answer YES or NO with confidence. State that "
        f"a live primary-list check is required and offer to run it manually "
        f"or link to the relevant primary-source URLs (OFAC SDN search, "
        f"EU Consolidated, OFSI, etc.)."
    )

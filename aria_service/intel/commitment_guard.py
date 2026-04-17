"""Commitment guard — detect and rewrite fabricated commitments (Clause 20).

Post-processes ARIA's response to catch promises of future deliverables
with deadlines that ARIA cannot enforce. Replaces them with honest
statements about what she CAN do right now.

Patterns detected:
  - "Within the next N hours, I will..."
  - "I will deliver/prepare/send/execute by..."
  - "Expect this in your inbox by..."
  - "I will now begin the work"
  - "I will have X ready by Y"
  - "Beginning the work now"

Each detected pattern is rewritten inline with a Clause 20 correction:
  ORIGINAL: "Within 24 hours, I will prepare a full KYC checklist."
  REWRITTEN: "I can prepare a KYC checklist if you ask me to do so now.
              [Clause 20: I cannot commit to future deliverables with
              deadlines — I complete work in this response or not at all.]"

Past incident 2026-04-16: ARIA promised an OEM Export Director List
within 12 hours. No code existed to produce it.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger("aria.commitment_guard")

# Patterns that indicate fabricated commitments
_COMMITMENT_PATTERNS = [
    # "Within N hours/days, I will..."
    (re.compile(
        r"[Ww]ithin (?:the next )?\d+\s*(?:hour|day|minute)s?,?\s*I (?:will|shall|can)\s+[^.!?]+[.!?]",
    ), "time-bound promise"),

    # "I will deliver/prepare/send/execute/provide by..."
    (re.compile(
        r"I (?:will|shall)\s+(?:deliver|prepare|send|execute|provide|compile|generate|produce|create|draft|build)\s+[^.!?]*?(?:by|before|within|no later than)\s+[^.!?]+[.!?]",
        re.IGNORECASE,
    ), "deliverable with deadline"),

    # "Expect this in your inbox..."
    (re.compile(
        r"[Ee]xpect\s+(?:this|it|the)\s+(?:in your|to|by)\s+[^.!?]+[.!?]",
    ), "delivery promise"),

    # "I will have X ready by Y"
    (re.compile(
        r"I (?:will|shall) have\s+[^.!?]+?\s+ready\s+[^.!?]*[.!?]",
        re.IGNORECASE,
    ), "readiness promise"),

    # "I will now begin the work" / "Beginning the work now"
    (re.compile(
        r"(?:I (?:will|shall) now begin|[Bb]eginning the work|I am now (?:starting|beginning|executing))\s*[^.!?]*[.!?]?",
    ), "aspirational action claim"),

    # "Your next step... I will deliver/prepare..."
    (re.compile(
        r"(?:Your next step|Next step)[^.!?]*I (?:will|shall)\s+[^.!?]+[.!?]",
        re.IGNORECASE,
    ), "next-step deliverable"),

    # "You will receive X within N hours/by Y"
    (re.compile(
        r"You will receive\s+[^.!?]+?(?:within|by|before|in)\s+[^.!?]+[.!?]",
        re.IGNORECASE,
    ), "delivery promise to user"),

    # Performative status footer — "ARIA Status: Live. Autonomy engine active..."
    (re.compile(
        r"(?:ARIA\s+(?:Status|is)\s*[:—]\s*[^.!?]*(?:live|active|running|operational|enabled)[^.!?]*[.!?]\s*){1,}",
        re.IGNORECASE,
    ), "performative status footer"),

    # "Deception Detection & Daily Conversation Audit protocols running"
    (re.compile(
        r"(?:Deception|Detection|Audit|Protocol|Observability)[^.!?]*(?:running|active|enabled|live|operational)[^.!?]*[.!?]",
        re.IGNORECASE,
    ), "performative protocol claim"),
]

# Separate note for status footers — different correction than deliverable promises
_CLAUSE_20_STATUS_NOTE = (
    " [Clause 20(d): Performative status lines removed — every claim "
    "in a status line must be individually verifiable. See /aria-brain "
    "dashboard for actual system status.]"
)

_CLAUSE_20_NOTE = (
    " [Clause 20: I cannot commit to future deliverables with deadlines "
    "— ask me to do this now and I will complete it in this response.]"
)


def guard_commitments(response_text: str) -> dict:
    """Scan response for fabricated commitments and rewrite them.

    Returns:
        {
            "original": str,
            "guarded": str,
            "violations_found": int,
            "violations": [{"pattern_type": str, "match": str}],
            "changed": bool,
        }
    """
    if not response_text or len(response_text) < 50:
        return {
            "original": response_text,
            "guarded": response_text,
            "violations_found": 0,
            "violations": [],
            "changed": False,
        }

    guarded = response_text
    violations = []

    for pattern, pattern_type in _COMMITMENT_PATTERNS:
        matches = pattern.findall(guarded)
        for match in matches:
            match_text = match.strip()
            if len(match_text) < 15:
                continue
            # Don't double-tag
            if "Clause 20" in guarded[guarded.find(match_text):guarded.find(match_text) + len(match_text) + 100]:
                continue

            violations.append({
                "pattern_type": pattern_type,
                "match": match_text[:200],
            })

            # Status footers get STRIPPED entirely (not rewritten)
            if pattern_type in ("performative status footer", "performative protocol claim"):
                guarded = guarded.replace(match_text, "", 1)
            else:
                # Commitment promises get Clause 20 note appended
                guarded = guarded.replace(
                    match_text,
                    match_text.rstrip(".!?") + "." + _CLAUSE_20_NOTE,
                    1,  # only first occurrence
                )

    changed = guarded != response_text
    if changed:
        logger.info(
            "[commitment_guard] %d Clause 20 violation(s) rewritten",
            len(violations),
        )

    return {
        "original": response_text,
        "guarded": guarded,
        "violations_found": len(violations),
        "violations": violations,
        "changed": changed,
    }

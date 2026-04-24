"""Single source of truth for self-infrastructure introspection detection.

Multiple layers of the chat pipeline need to recognise "user is asking about
their own deployment" so the system can refuse to fabricate component names.
Background: 2026-04-24 OpenClaw incident — Brave Answers fabricated a
fictional "OpenClaw" gateway product on a question about ARIA's WhatsApp
listener; the answer was absorbed into mem0 / RAG / knowledge / reasoning
library via pay-once-remember-forever; even after blocking the Brave route,
the same fabrication came back from local stores.

Three commits in the 2026-04-24 / 2026-04-25 fix chain each defined their
own copy of the detection regex (`_BRAVE_QA_SELF_INFRA_RE` in routes/aria.py,
`_SELF_INFRA_INTROSPECTION_RE` in aria_engine.py and intel/reasoning_router.py).
This module makes one canonical definition that all three import. Extending
the patterns now updates every layer atomically — no drift risk.

Usage:
    from .self_infra_detector import is_self_infra_query
    if is_self_infra_query(message):
        ...

The detector is intentionally narrow. It triggers on questions phrased as
"why isn't my X working" / "what's wrong with X" where X is a component
of THE OPERATOR'S OWN deployment. It does NOT fire on:
  - generic "how do WhatsApp gateways work" knowledge questions
  - non-introspective "what is" / "who is" factual queries
  - troubleshooting questions about external systems
"""
from __future__ import annotations

import re

# Set of infrastructure nouns the operator can ask about. Used twice in
# the regex below — once in the possessive form ("my/our/this/the X")
# and once as a standalone subject ("aria isn't replying"). Centralised
# here so adding a new component is a one-line change.
_INFRA_NOUNS = (
    r"listener|gateway|bridge|sweep|brain|chat|stream|chain|loop|"
    r"deploy(?:ment)?|stack|infra(?:structure)?|service|process|"
    r"backend|fly|seenode|aria|baileys|"
    r"(?:wa|whatsapp)[\s_-]?(?:listener|gateway|bridge)?"
)

# The canonical pattern. Matches phrases like:
#   "why isn't my listener working"
#   "what's wrong with the gateway"
#   "why is aria silent"
#   "what's broken in the brain"
#   "what is wrong with our infrastructure"
#
# Two-part structure required:
#   1. Question stem: (why | what's | what is) + (is|are|isn't|aren't|
#      won't|can't|doesn't|wrong with|broken in)
#   2. Self-referent component — must be either:
#      (a) a possessive (my/our/this/the) FOLLOWED BY an infra noun, OR
#      (b) a bare named component (aria/baileys/the listener/etc.)
#
# Past bug 2026-04-25: previous version accepted (my|our|this|the) ALONE
# as the self-referent, which caused "Why is the dollar falling?" to
# false-fire because "the" matched any subsequent noun. Now (the|my|
# our|this) must be IMMEDIATELY followed by an infra noun from the
# enumerated list — generic nouns no longer trigger.
SELF_INFRA_INTROSPECTION_RE: re.Pattern[str] = re.compile(
    r"(?:why|what'?s|what\s+is)\s+"
    r"(?:is|are|isn'?t|aren'?t|won'?t|can'?t|doesn'?t|"
    r"wrong\s+with|broken\s+(?:in|with))\s+"
    r"(?:"
        # Possessive + required infra noun
        r"(?:my|our|this|the)\s+(?:" + _INFRA_NOUNS + r")"
        r"|"
        # Bare named component
        r"(?:" + _INFRA_NOUNS + r")"
    r")\b",
    re.IGNORECASE,
)

# List of fabricated component tokens observed during the 2026-04-24
# incident. The retrieval-quarantine note in aria_engine.py tells the
# LLM these are FORBIDDEN to reference even though they may appear in
# poisoned memory entries until the keyword-purge admin tooling clears
# them. Kept here so future additions land in one place.
KNOWN_FABRICATED_TOKENS: tuple[str, ...] = (
    "openclaw",
    "openclaw doctor",
    "openclaw platform",
    "openclaw gateway",
    "arkmurus platform",
    "arkmurus gateway",
)


def is_self_infra_query(message: str | None) -> bool:
    """True when the message asks about the operator's own deployment.

    Cheap, deterministic, no LLM call. Safe to call on every chat turn
    in the hot path.
    """
    if not message:
        return False
    return bool(SELF_INFRA_INTROSPECTION_RE.search(message))


def contains_known_fabrication(text: str | None) -> bool:
    """True when the text contains any token from the fabricated-component
    list. Used by the brain_hook absorption gate to refuse ingest of
    content that re-references a known fabrication. Defensive backstop
    for the keyword-purge tooling.
    """
    if not text:
        return False
    lowered = text.lower()
    return any(tok in lowered for tok in KNOWN_FABRICATED_TOKENS)

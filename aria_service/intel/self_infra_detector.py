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


# ── R-F399 (2026-05-13) — capability-introspection detector ──────────
#
# Live evidence 2026-05-13 07:25/07:27: operator asked ARIA
#   "how many neurons are currently within your brain, and what is your
#    learning and research capacity every 6-hour study cycle? Are you
#    able to digest chunks of documents and online content? Is the
#    information kept infinite?"
# ARIA routed to `spawn_research_task` which returned 0 sources / 0
# facts (the literal sentence-fragment "capacity every 6-hour study
# cycle" returned crossref papers, not self-knowledge). She then
# answered from intuition, undercounting facts ~7x and inventing an
# 18-month TTL that violates aria_infinite_memory.md.
#
# This detector catches CAPABILITY-shape questions about ARIA's own
# state (how many / how much / what's your / how big is) so the
# chat router (routes/aria.py _detect_tool_intent) can dispatch them
# to /api/aria/health/perf (the R-F396 endpoint, extended with
# inventory + retention by R-F400) instead of web search.
#
# Distinct from SELF_INFRA_INTROSPECTION_RE which catches DIAGNOSTIC
# questions ("why isn't my X working"). Two separate intents:
#   - diagnostic   → answered from logs / health, NOT from search
#   - capability   → answered from /health/perf inventory + retention
#
# Past incidents this guards against:
#   - 07:25 2026-05-13: spawn_research_task on "capacity every 6-hour
#     study cycle" returned 12 crossref papers, zero relevant
#   - 07:27 2026-05-13: ARIA invented 18-month TTL (no such TTL in code)
#   - 07:27 2026-05-13: ARIA estimated 5,000-10,000 facts (real: 35,363)

# Nouns describing ARIA's own state / capacity / inventory.
_CAPABILITY_NOUNS = (
    r"brain|neurons?|memory|memories|facts?|knowledge(?:\s+base)?|"
    r"capacity|capabilities|capability|architecture|context(?:\s+window)?|"
    r"learning(?:\s+(?:rate|capacity|cycle))?|study\s+cycle|"
    r"signals?|chunks?|tokens?|"
    r"performance|performing|status|state|inventory|"
    # R-F913 (2026-05-26) — diagnostics / autonomous-coding shape. Live miss:
    # "what are your current system diagnostics and your autonomous coding?"
    # routed to brave_answer → 12 irrelevant healthcare results + a self-claim
    # guard BLOCK (ARIA denied having self_introspect). These are ARIA's own
    # operational state → must hit /health/perf, not web search.
    r"diagnostics?|system|engine|autonomous|automation|self[\s-]?coding|"
    r"coding|scheduler|scheduled\s+tasks?|tasks?|health|introspection"
)

# Verbs/operators denoting introspective questioning of ARIA's state.
SELF_CAPABILITY_INTROSPECTION_RE: re.Pattern[str] = re.compile(
    r"(?:"
        # Quantity questions: "how many facts / neurons / signals do you have"
        r"how\s+(?:many|much|big|large|long|deep)\s+"
        r"(?:[a-z]+\s+){0,3}"
        r"(?:" + _CAPABILITY_NOUNS + r")"
        r"|"
        # Possessive direct: "your brain / your memory / your capacity".
        # R-F913 — allow up to 3 filler words so "your current system
        # diagnostics" / "your autonomous coding" match (not just "your X").
        r"(?:your|aria'?s?)\s+(?:[a-z]+\s+){0,3}(?:" + _CAPABILITY_NOUNS + r")"
        r"|"
        # Capability verbs about ARIA: "do you remember/forget/learn/study/digest/ingest"
        r"(?:do|can|are)\s+you\s+(?:able\s+to\s+)?"
        r"(?:remember|forget|learn|study|digest|ingest|retain|recall|store)"
        r"|"
        # "is (your|the) information / memory / knowledge X"
        # (e.g. "is the information kept infinite", "is your memory permanent")
        r"is\s+(?:your|the)\s+"
        r"(?:" + _CAPABILITY_NOUNS + r"|information|data)"
        r"\s+(?:infinite|permanent|persistent|kept|stored|preserved|"
        r"limited|capped|finite|expired?|deleted|evicted)"
        r"|"
        # "how (are you|do you) (performing|doing|learning)"
        r"how\s+(?:are\s+you|do\s+you|do\s+i)\s+"
        r"(?:performing|doing|learning|operating|working|running)"
        r"|"
        # "tell me about your(self|brain|memory|architecture|...)"
        r"tell\s+me\s+about\s+(?:your(?:self)?|aria'?s)"
        r"|"
        # "what's your (build|status|version|stats)"
        r"what'?s\s+your\s+"
        r"(?:build|status|version|stats|state|"
        r"" + _CAPABILITY_NOUNS + r")"
    r")\b",
    re.IGNORECASE,
)


def is_capability_introspection_query(message: str | None) -> bool:
    """R-F399: True when the message asks about ARIA's own state /
    inventory / retention / learning capacity. Routes to /health/perf
    instead of web search.

    Cheap, deterministic, no LLM call. Safe in the hot path.
    """
    if not message:
        return False
    return bool(SELF_CAPABILITY_INTROSPECTION_RE.search(message))


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

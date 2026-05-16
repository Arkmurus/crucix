"""R-F614 — Dialogue-act response classifier.

Foundation for Phase 1 of ARIA dialogue (spec v2.1). Pure function,
no LLM call, no I/O. Classifies an incoming user message into one of:

  - DIALOGUE — short, conversational. Triggers the v2 conversational
    prompt branch (R-F615): short reply, no BLUF banner, no section
    headers, ≤200 words default, may end with ONE clarifying question.

  - REPORT — explicit briefing / report / DD-style write-up. Stays on
    the existing BLUF + section-header format (Constitution §3).

  - COMMAND — explicit imperative (`/screen`, "DD on X", "investigate Y",
    "deep_research X"). Treated as a tool dispatch with terse confirmation,
    not a conversational turn.

Design discipline
─────────────────
  - Heuristic-first: regex + length thresholds. Cheap, deterministic,
    no LLM cost on the hot path.
  - Conservative tie-break: COMMAND > REPORT > DIALOGUE. A message
    that triggers multiple patterns lands at the most-actionable bucket.
  - Defaults to DIALOGUE: short, unclassified messages are
    conversational by default. This matches state-of-the-art teammate
    behaviour — the user shouldn't have to phrase a greeting as "/hi".

The classifier is consumed at one chat-entry call-site in aria_engine
(R-F615 wires it). This module itself never imports aria_engine —
zero circular-import risk.
"""
from __future__ import annotations

import re
from enum import Enum


class DialogueIntent(str, Enum):
    """Three discrete buckets the classifier resolves to.

    Subclasses `str` so callers can compare with literal strings or
    pass through `.value` to logs without conversion.
    """
    DIALOGUE = "dialogue"
    REPORT = "report"
    COMMAND = "command"


# ── COMMAND patterns (slash + imperatives) ────────────────────────────
#
# Match leading `/foo`, common imperatives ("run DD on X", "screen X",
# "investigate Y"), and named tool calls ("deep_research X").
_COMMAND_RE = re.compile(
    r"^\s*/\w+"                                                # slash command
    r"|^\s*(?:run|do|start|kick|launch|fire|trigger)\s+"
        r"(?:a\s+|an\s+|the\s+)?"
        r"(?:\w+\s+){0,2}"                                      # up to 2 qualifiers
        r"(?:dd|due\s+diligence|screen|"
        r"investigation|research|crawl|sweep)\b"
    r"|^\s*(?:dd|due\s+diligence)\s+(?:on\s+|of\s+|for\s+)"
    r"|^\s*(?:screen|investigate|crawl|verify|check|sanction[-\s]?screen|"
        r"sanction\s+screen)\b"
    r"|^\s*deep[_\s-]?research\b"
    r"|^\s*extract[_\s-]?url\b",
    re.IGNORECASE,
)


# ── REPORT patterns (explicit "give me a brief" requests) ─────────────
#
# Pattern requires a USER-side verb ("give me", "write", "prepare",
# "draft") followed by a brief/report/summary noun. Matters because
# ARIA herself uses "report" / "brief" inside her own replies — those
# must not back-classify as REPORT requests when she's quoting them.
_REPORT_RE = re.compile(
    r"\b(?:give\s+me|send\s+me|share|provide|i\s+(?:need|want))\s+"
        r"(?:a\s+|an\s+|the\s+)?"
        r"(?:full\s+|complete\s+|comprehensive\s+|detailed\s+|thorough\s+)?"
        r"(?:brief|report|summary|overview|analysis|breakdown|memo|"
        r"write[-\s]?up|profile|dossier)\b"
    r"|\b(?:write|prepare|compose|draft|produce)\s+"
        r"(?:a\s+|an\s+|the\s+)?"
        r"(?:full\s+|complete\s+|comprehensive\s+|detailed\s+)?"
        r"(?:brief|report|summary|memo|write[-\s]?up|profile|dossier)\b"
    r"|\bfull\s+(?:dd|due\s+diligence|brief|report|profile)\b"
    r"|\b(?:detailed|comprehensive|thorough)\s+"
        r"(?:report|brief|analysis|profile)\b",
    re.IGNORECASE,
)


# ── DIALOGUE positive markers ─────────────────────────────────────────
#
# Strong signals for "this is a conversational turn" — greetings,
# acknowledgements, short questions, agreements. Doesn't have to
# match — DIALOGUE is the default. These just bias the classifier
# when length / pattern checks are ambiguous.
_DIALOGUE_HINTS_RE = re.compile(
    r"^(?:hi|hey|hello|good\s+(?:morning|afternoon|evening)|"
        r"thanks|thank\s+you|cheers|cool|nice|got\s+it|understood|"
        r"yes|no|sure|maybe|ok|okay|alright|right|correct|"
        r"yep|yeah|nope)\b"
    r"|^(?:what\s+do\s+you\s+think|do\s+you\s+think|"
        r"any\s+thoughts|anything\s+else|anything\s+new)\b"
    r"|^(?:aria[,\s!]+)?(?:are\s+you|how\s+are\s+you|how's\s+it)\b",
    re.IGNORECASE,
)


# Default word-count threshold above which a message leans REPORT.
# Pick conservatively — long natural-language asks ARE often dialogue
# in practice, but a 100-word prompt with no greeting is usually a
# request for a structured response.
DEFAULT_DIALOGUE_MAX_WORDS = 60


def classify_dialogue_intent(
    message: str,
    *,
    max_words_for_dialogue: int = DEFAULT_DIALOGUE_MAX_WORDS,
    has_tool_block: bool = False,
    has_attachment: bool = False,
) -> DialogueIntent:
    """Classify a chat message into DIALOGUE / REPORT / COMMAND.

    Args:
        message: raw user message text. None / empty → DIALOGUE.
        max_words_for_dialogue: word-count threshold above which the
            classifier leans REPORT in the absence of explicit DIALOGUE
            markers.
        has_tool_block: True when a `[TOOL: ...]` block is already in
            the current turn context — implies a command already ran
            and ARIA is interpreting its output. Treat as COMMAND so
            the response stays terse + tool-grounded.
        has_attachment: True when the user attached a document. The
            attachment doesn't itself force a bucket — but combined
            with very short prose ("look at this"), DIALOGUE wins.

    Tie-break (highest priority wins):
        1. has_tool_block → COMMAND
        2. Slash / imperative pattern → COMMAND
        3. Explicit "give me a brief" / "write a report" → REPORT
        4. Word count > max_words_for_dialogue AND no DIALOGUE hint → REPORT
        5. DIALOGUE (default)
    """
    if not message or not isinstance(message, str):
        return DialogueIntent.DIALOGUE
    text = message.strip()
    if not text:
        return DialogueIntent.DIALOGUE

    # Stage 1 — tool block in context = command run is in flight
    if has_tool_block:
        return DialogueIntent.COMMAND

    # Stage 2 — explicit slash or imperative command
    if _COMMAND_RE.search(text):
        return DialogueIntent.COMMAND

    # Stage 3 — explicit REPORT request (user-side verb)
    if _REPORT_RE.search(text):
        return DialogueIntent.REPORT

    # Stage 4 — length heuristic. Long messages without explicit
    # DIALOGUE markers lean REPORT.
    word_count = len(text.split())
    if word_count > max_words_for_dialogue and not _DIALOGUE_HINTS_RE.search(text):
        return DialogueIntent.REPORT

    # Default — DIALOGUE
    return DialogueIntent.DIALOGUE


# ── Public surface helpers for diagnostics ──────────────────────────


def patterns() -> dict[str, str]:
    """Expose the live regex source for tests / operator dashboard.

    Maps pattern_id → regex source. New patterns are added here so a
    future contributor can see at a glance what triggers each bucket.
    """
    return {
        "command": _COMMAND_RE.pattern,
        "report": _REPORT_RE.pattern,
        "dialogue_hints": _DIALOGUE_HINTS_RE.pattern,
    }

"""Instruction comprehension (Clause 21 — Understand Before Act).

Origin
──────
This module is a re-implementation of the ARIA_Comprehension_Module.zip
drop-in that the operator provided on 2026-04-18. The dropped-in version
made a *second* LLM call per chat turn via a hardcoded Anthropic client
to produce a structured comprehension analysis. Two issues with shipping
that verbatim:

  1. Operator's recent directive: reduce LLM reliance, especially for
     extraction-style work. A second Claude round-trip per turn doubles
     cost + latency, the opposite direction.
  2. Hardcoded `anthropic.Anthropic()` bypasses the 5-provider fallback
     chain. With Anthropic currently cooling on billing, that module
     would silent-fail every turn.

This file keeps the IDEAS — language/stakes detection, confidence
ladder, proceed-or-ask logic, challenge flag, clarification mode — but
integrates them as a pure-regex pre-pass + a prompt prefix injected
into the SAME LLM call that answers the user's question. Zero extra
round-trips. ~20ms overhead per turn (regex work only). Clarification
mode only fires on rare CRITICAL + UNCLEAR combinations and routes via
the pending_actions queue rather than blocking the reply.

Clause 21 (proposed constitutional clause)
──────────────────────────────────────────
"Before acting on any non-trivial instruction, ARIA must formulate her
understanding of what is being asked in her own words, name the
assumptions she is making to fill gaps, and fire the appropriate tool
for the request. When understanding is unclear on a high-stakes
request (compliance / legal / financial), ARIA must ask a specific
clarification question rather than guess."

Note: NOT Clause 20 — that's already taken by commitment_guard
(future-tense fabricated-promise detection).

Public API
──────────
  analyse(message, ...) -> ComprehensionAnalysis
      Pure regex + heuristics. Returns the analysis without an LLM call.

  build_prefix(analysis) -> str
      Render "UNDERSTOOD AS: ..." block to prepend to the LLM prompt.
      Empty string if the message is trivial (greetings, short acks).

  async request_clarification(analysis, user_id, chat_id)
      Route to pending_actions — the operator sees an open clarification
      in the daily briefing. Used ONLY when need_clarification is True
      (CRITICAL complexity + UNCLEAR confidence).
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("aria.comprehension")


# ── Taxonomy ───────────────────────────────────────────────────────────────

class Confidence(str, Enum):
    CLEAR = "CLEAR"            # unambiguous, proceed
    PROBABLE = "PROBABLE"      # most-likely interpretation, state + proceed
    AMBIGUOUS = "AMBIGUOUS"    # two or more plausible reads, consider asking
    INCOMPLETE = "INCOMPLETE"  # missing info to proceed
    UNCLEAR = "UNCLEAR"        # cannot form reliable interpretation


class Complexity(str, Enum):
    SIMPLE = "SIMPLE"        # single clear action
    MODERATE = "MODERATE"    # some interpretation, bounded scope
    COMPLEX = "COMPLEX"      # multi-step, significant judgment
    CRITICAL = "CRITICAL"    # compliance / legal / financial — highest care


class LanguageSignal(str, Enum):
    NATIVE_FLUENT = "NATIVE_FLUENT"
    FLUENT_NON_NATIVE = "FLUENT_NON_NATIVE"
    NON_NATIVE = "NON_NATIVE"
    TRANSLATED = "TRANSLATED"


# ── Regex patterns ─────────────────────────────────────────────────────────

# Patterns suggesting the writer is using English as a second language.
# These are SIGNALS, not errors — they warrant extra care, not judgment.
_NON_NATIVE_PATTERNS: list[re.Pattern] = [
    # Missing article before noun after common verbs (ESL pattern)
    re.compile(
        r"\b(need|want|have|make|give|send|create|build|check|review)\s+"
        r"(?!a\b|an\b|the\b|some\b|any\b|it\b|them\b|us\b|me\b|you\b)"
        r"[a-z]+",
        re.IGNORECASE,
    ),
    # Unusual preposition compositions
    re.compile(r"\b(explain me|tell me about of|make me a|give me the)\b", re.IGNORECASE),
    # Direct-translation negation patterns (common in PT/FR/ES sources)
    re.compile(r"\bno\s+(understand|know|have|problem)\b", re.IGNORECASE),
    re.compile(r"\byes no\b|\bno yes\b", re.IGNORECASE),
    # Dropped subject pronouns at sentence start
    re.compile(
        r"^(want|need|looking|trying|have|can|must)\b",
        re.IGNORECASE | re.MULTILINE,
    ),
]

# Stakes signals — domains where a wrong answer has high cost
_HIGH_STAKES_KEYWORDS: set[str] = {
    "compliance", "legal", "bribery", "sanctions", "siel", "ecju",
    "contract", "dd", "due diligence", "opinion", "assessment",
    "money laundering", "sar", "pep", "beneficial owner",
    "sanctions exposure", "sanctions check", "fcpa", "bribery act",
    "offset", "procurement", "tender", "commission", "represent",
    "mandate", "exclusive", "itar", "ear99", "dual use", "export licence",
    "export license", "end user certificate", "euc",
}

# Ambiguity signals — vague references without antecedents
_AMBIGUOUS_PATTERNS: list[re.Pattern] = [
    # "this", "that", "them", "these", "those" at sentence start with no nearby noun
    re.compile(r"^\s*(this|that|these|those|they|them)\b(?!\s+(?:company|entity|person|one|case|report|deal|email|document|url))", re.IGNORECASE),
    # "the same thing / as before / again" — needs prior context
    re.compile(r"\b(the same (?:thing|as before|one|way|deal)|as last time|again|like before)\b", re.IGNORECASE),
    # "can you help" without specifics
    re.compile(r"^\s*(can you help|help me|i need help)\s*[.?!]?\s*$", re.IGNORECASE),
]

# Urgency markers
_URGENT_PATTERNS = re.compile(
    r"\b(urgent|asap|right\s+now|immediately|eod|by\s+(?:tomorrow|tonight|today)|"
    r"deadline|overdue|critical|emergency)\b",
    re.IGNORECASE,
)

# Trivial-message patterns — skip comprehension entirely
_TRIVIAL_PATTERNS = re.compile(
    r"^\s*(hi|hello|hey|thanks|thank\s+you|ok|okay|yes|no|sure|sounds\s+good|"
    r"cheers|got\s+it|understood|noted|agreed|perfect|great|cool|nice|"
    r"👍|👎|✅|❌|🙏|:\)|:\(|:D)\s*[.?!]?\s*$",
    re.IGNORECASE,
)


# ── Data structures ────────────────────────────────────────────────────────

@dataclass
class ComprehensionAnalysis:
    """Structured view of what the user is asking. Pure derivation — no LLM."""
    original_message: str
    language_signal: LanguageSignal
    complexity: Complexity
    confidence: Confidence
    is_trivial: bool

    # What we derived about the request
    detected_stakes: list[str] = field(default_factory=list)
    ambiguity_flags: list[str] = field(default_factory=list)
    urgency_hint: str = ""

    # Proceed-or-ask verdict
    should_proceed: bool = True
    need_clarification: bool = False
    clarification_reason: str = ""


# ── Detection helpers ──────────────────────────────────────────────────────

def detect_language_signal(text: str) -> LanguageSignal:
    """Classify writer fluency from regex signals. Never used to *judge*
    English quality — only to calibrate how carefully we interpret."""
    if not text:
        return LanguageSignal.NATIVE_FLUENT

    hits = sum(1 for p in _NON_NATIVE_PATTERNS if p.search(text))
    words = text.split()

    # Very short messages are genuinely ambiguous regardless of fluency
    if len(words) < 5:
        return LanguageSignal.NON_NATIVE
    if hits >= 2:
        return LanguageSignal.NON_NATIVE
    if hits == 1:
        return LanguageSignal.FLUENT_NON_NATIVE
    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="comprehension",
        summary="Detect Language Signal",
        source_id="comprehension:R-F996",
    )

    return LanguageSignal.NATIVE_FLUENT


def detect_complexity(text: str) -> tuple[Complexity, list[str]]:
    """Rate stakes + return the specific keywords that drove the rating."""
    text_l = text.lower()
    hits = [kw for kw in _HIGH_STAKES_KEYWORDS if kw in text_l]

    word_count = len(text.split())
    if len(hits) >= 2 or "legal" in text_l or "compliance" in text_l:
        return Complexity.CRITICAL, hits
    if hits or word_count > 100:
        return Complexity.COMPLEX, hits
    if word_count > 30:
        return Complexity.MODERATE, hits
    return Complexity.SIMPLE, hits


def detect_ambiguity(text: str) -> list[str]:
    """Return human-readable ambiguity flags (for the 'UNDERSTOOD AS' prefix)."""
    flags: list[str] = []
    for i, pattern in enumerate(_AMBIGUOUS_PATTERNS):
        if pattern.search(text):
            flag_label = [
                "vague pronoun without antecedent ('this' / 'that' / 'them')",
                "reference to prior context ('same as before', 'again')",
                "generic help request without specifics",
            ][i]
            flags.append(flag_label)
    return flags


def detect_urgency(text: str) -> str:
    """Extract urgency hint from text. Empty if no explicit urgency signal."""
    m = _URGENT_PATTERNS.search(text)
    return m.group(0).lower() if m else ""


def is_trivial(text: str) -> bool:
    """True for greetings, short acks, emoji-only messages — skip full comprehension."""
    if not text or len(text.strip()) < 2:
        return True
    return bool(_TRIVIAL_PATTERNS.match(text.strip()))


# ── Decision table ─────────────────────────────────────────────────────────

# How confident must we be before proceeding without clarification?
_PROCEED_THRESHOLD: dict[Complexity, set[Confidence]] = {
    Complexity.SIMPLE: {Confidence.CLEAR, Confidence.PROBABLE, Confidence.AMBIGUOUS},
    Complexity.MODERATE: {Confidence.CLEAR, Confidence.PROBABLE},
    Complexity.COMPLEX: {Confidence.CLEAR, Confidence.PROBABLE},
    Complexity.CRITICAL: {Confidence.CLEAR},  # must be unambiguous
}

# Non-native → raise the bar one level (extra care)
_PROCEED_THRESHOLD_NON_NATIVE: dict[Complexity, set[Confidence]] = {
    Complexity.SIMPLE: {Confidence.CLEAR, Confidence.PROBABLE},
    Complexity.MODERATE: {Confidence.CLEAR},
    Complexity.COMPLEX: {Confidence.CLEAR},
    Complexity.CRITICAL: set(),  # always confirm on critical
}


def _infer_confidence(
    ambiguity_flags: list[str],
    message: str,
) -> Confidence:
    """Assign a confidence level from the ambiguity signals.

    Pure heuristic — no LLM. We deliberately err on the PROBABLE side
    for simple messages because blocking to ask "what do you mean by
    hello" would be obnoxious. UNCLEAR is reserved for genuinely
    unparseable input.
    """
    if not message or len(message.strip()) < 2:
        return Confidence.UNCLEAR
    if len(ambiguity_flags) >= 2:
        return Confidence.AMBIGUOUS
    if ambiguity_flags:
        return Confidence.PROBABLE
    # Very short question with no context → ambiguous
    if len(message.split()) < 3 and "?" in message:
        return Confidence.AMBIGUOUS
    return Confidence.CLEAR


# ── Public API ─────────────────────────────────────────────────────────────

def analyse(message: str) -> ComprehensionAnalysis:
    """Run the pure-regex comprehension pass. Returns in ~20ms."""
    if is_trivial(message):
        return ComprehensionAnalysis(
            original_message=message,
            language_signal=LanguageSignal.NATIVE_FLUENT,
            complexity=Complexity.SIMPLE,
            confidence=Confidence.CLEAR,
            is_trivial=True,
        )

    lang = detect_language_signal(message)
    complexity, stakes = detect_complexity(message)
    ambiguity = detect_ambiguity(message)
    confidence = _infer_confidence(ambiguity, message)
    urgency = detect_urgency(message)

    threshold = (
        _PROCEED_THRESHOLD_NON_NATIVE
        if lang in (LanguageSignal.NON_NATIVE, LanguageSignal.TRANSLATED)
        else _PROCEED_THRESHOLD
    )
    allowed = threshold.get(complexity, set())
    can_proceed = confidence in allowed

    # Clarification-request mode is VERY narrow. We only block the reply
    # when both conditions hold:
    #   - Complexity is CRITICAL (a wrong answer has high cost)
    #   - Confidence is UNCLEAR or AMBIGUOUS (we genuinely cannot tell)
    # Everything else proceeds with a stated assumption. This avoids the
    # "ARIA asks 5 questions before answering hello" failure mode.
    need_clarification = (
        complexity == Complexity.CRITICAL
        and confidence in (Confidence.UNCLEAR, Confidence.AMBIGUOUS)
    )
    clarification_reason = ""
    if need_clarification:
        if ambiguity:
            clarification_reason = (
                f"Request is high-stakes ({', '.join(stakes[:3])}) but has "
                f"{len(ambiguity)} ambiguity signal(s): {'; '.join(ambiguity)}."
            )
        else:
            clarification_reason = (
                f"Request is high-stakes ({', '.join(stakes[:3])}) but "
                f"interpretation is not confident enough to proceed."
            )

    result = ComprehensionAnalysis(
        original_message=message,
        language_signal=lang,
        complexity=complexity,
        confidence=confidence,
        is_trivial=False,
        detected_stakes=stakes,
        ambiguity_flags=ambiguity,
        urgency_hint=urgency,
        should_proceed=can_proceed and not need_clarification,
        need_clarification=need_clarification,
        clarification_reason=clarification_reason,
    )

    # Brain signal — fire-and-forget from this sync function. Tracks
    # how often comprehension fires CRITICAL+UNCLEAR (= clarification
    # request) so the predictor can flag domains where users routinely
    # under-specify.
    try:
        import asyncio as _aio
        from . import brain_hook as _bh

        async def _emit():
            await _bh.absorb(
                module="comprehension",
                summary=f"Comprehension: complexity={complexity.value} confidence={confidence.value} "
                        f"clarify={need_clarification} stakes={','.join(stakes[:3]) or 'none'}",
                detail=clarification_reason or message[:200],
                success=True,
                gap_type=("clarification_required" if need_clarification else None),
                gap_detail=clarification_reason if need_clarification else None,
            )

        try:
            loop = _aio.get_running_loop()
            loop.create_task(_emit())
        except RuntimeError:
            pass
    except Exception:
        pass

    return result


def build_prefix(analysis: ComprehensionAnalysis) -> str:
    """Build a "UNDERSTOOD AS: …" block to prepend to the LLM prompt.

    This is the single-round-trip trick — instead of a second LLM call
    for comprehension, we tell THE SAME LLM "here's what we think the
    user is asking, here are the assumptions, now answer". The LLM
    naturally grounds its response to the stated interpretation.

    Returns empty string for trivial messages so greetings/thanks don't
    get a heavyweight preface.
    """
    if analysis.is_trivial:
        return ""

    parts: list[str] = []
    parts.append("[COMPREHENSION PASS — Clause 21, Understand Before Act]")
    parts.append(f"Complexity: {analysis.complexity.value}")
    parts.append(f"Confidence: {analysis.confidence.value}")
    if analysis.language_signal != LanguageSignal.NATIVE_FLUENT:
        parts.append(
            f"Language signal: {analysis.language_signal.value} — "
            f"interpret charitably and confirm if uncertain"
        )
    if analysis.detected_stakes:
        parts.append(
            f"High-stakes keywords present: {', '.join(analysis.detected_stakes[:5])}"
        )
    if analysis.urgency_hint:
        parts.append(f"Urgency signal: '{analysis.urgency_hint}'")
    if analysis.ambiguity_flags:
        parts.append(
            "Ambiguity flags (address or flag explicitly in reply):\n"
            + "\n".join(f"  - {f}" for f in analysis.ambiguity_flags)
        )

    parts.append("")
    parts.append("RESPONSE CONTRACT for this turn:")
    parts.append(
        "  1. Start your reply with 'UNDERSTOOD AS: <one sentence restating "
        "what the user is asking>'. Keep it tight."
    )
    parts.append(
        "  2. If you are filling any gap with an assumption, name it "
        "explicitly: 'Assuming <X>...'."
    )
    parts.append(
        "  3. If the request is HIGH-STAKES and you are NOT 100% confident, "
        "state what you would need to confirm — do NOT fabricate verifiable "
        "facts (jurisdictions, financials, sanctions status) to fill the gap."
    )
    if analysis.need_clarification:
        parts.append(
            "  4. CRITICAL-TIER CLARIFICATION MODE: before producing the "
            "substantive answer, ask ONE specific clarification question. "
            "Reason: " + analysis.clarification_reason
        )
    parts.append("[END COMPREHENSION PASS]")
    return "\n".join(parts)


async def request_clarification(
    analysis: ComprehensionAnalysis,
    *,
    user_id: str = "",
    chat_id: str = "",
) -> dict | None:
    """Log an open clarification request in the pending_actions queue.

    Called when need_clarification is True. The reply still goes out
    (with a clarification question in it, per the prompt contract), but
    the operator also sees the event in the daily briefing so pattern
    tracking is possible.
    """
    if not analysis.need_clarification:
        return None
    try:
        from . import pending_actions as _pa
        entry = await _pa.record(
            promise=(
                f"Clarification needed on high-stakes request: "
                f"{analysis.original_message[:200]}"
            ),
            reason=analysis.clarification_reason,
            resolver_kind="operator_action",
            resolver_ref="clarify-intent",
            severity="HIGH",
            user_id=user_id,
            chat_id=chat_id,
            source="comprehension",
            operator_prompt=(
                f"ARIA asked for clarification on this message. "
                f"Review and provide a clearer restatement, or confirm that "
                f"the original reading is correct."
            ),
            metadata={
                "complexity": analysis.complexity.value,
                "confidence": analysis.confidence.value,
                "stakes": analysis.detected_stakes,
                "ambiguity_flags": analysis.ambiguity_flags,
            },
        )
        return entry
    except Exception as e:
        logger.debug("[comprehension] pending_actions record failed: %s", e)
        return None


# ── Proposed Clause 21 constitutional text ──────────────────────────────────

CLAUSE_21_TEXT = """

CLAUSE 21 — UNDERSTAND BEFORE ACT

Before acting on any non-trivial instruction, ARIA must:

a) FORMULATE: Begin every substantive reply with 'UNDERSTOOD AS: <one
   sentence restating what the user asked>'. Prove the interpretation
   explicit so the user can correct it before it propagates.

b) NAME ASSUMPTIONS: If any assumption is made to fill a gap, name it
   explicitly. 'Assuming X' is acceptable; silent assumption is not.

c) CALIBRATE BY COMPLEXITY: For COMPLEX or CRITICAL requests (compliance,
   legal, financial, sanctions, DD), ARIA must have CLEAR confidence
   before producing any verdict. If interpretation is uncertain, ask a
   specific clarification question instead of guessing.

d) CHARITABLE INTERPRETATION UNDER LANGUAGE DRIFT: When writing suggests
   English is not the sender's first language, ARIA raises the
   comprehension bar — interpret charitably, confirm her reading, and
   never assume the first reading is correct.

e) NEVER FABRICATE TO FILL A GAP: If the user's request hinges on a fact
   ARIA cannot verify, ARIA states the gap explicitly. A wrong compliance
   opinion produced quickly is worse than a clear 'I cannot verify this —
   here is what I need'.

f) NOT-APPLICABLE TO TRIVIAL TURNS: Greetings, acknowledgements, and
   one-word replies are exempt. The UNDERSTOOD AS prefix applies to
   questions, instructions, and investigative asks only.
"""

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.

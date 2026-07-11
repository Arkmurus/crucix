"""Clause 22 — Think Before Speak: pre-response scratchpad.

Context
───────
ARIA goes directly from input to output in a single forward pass. Every
compliance opinion, intelligence assessment, and procurement paper is
produced without a separate reasoning step that could catch weak
judgements before they ship. A well-reasoned "PROCEED WITH CONDITIONS"
is very different from a confident wrong one — the difference is
whether the model tested its conclusion against the strongest
counterargument before committing.

This module fixes that by requiring every substantive response to
contain a structured `<scratchpad>...</scratchpad>` block in which the
LLM reasons privately BEFORE the visible answer. The scratchpad is
stripped from the user-facing reply by `strip()` but persisted for
audit, calibration, and future fine-tuning data collection.

Single-round-trip design
────────────────────────
Unlike a separate "reasoning LLM call" approach, the scratchpad is
produced in the SAME LLM call as the answer. This keeps latency +
cost flat relative to the current pipeline. The prompt instructs:

  "Before your user-facing response, produce a <scratchpad>...</scratchpad>
   block covering: actual question, confident vs inferred, strongest
   counterargument, where you could be wrong, relevant constitutional
   clause. Then produce the user-visible response."

The extractor then:
  1. Pulls the scratchpad out of the raw response
  2. Logs it to the trace_stream + optionally RAG for future training
  3. Returns the clean user-facing response

What makes this Clause 22 and not Clause 23, 24, 25...
──────────────────────────────────────────────────────
Existing clauses:
  Clause 17  verified_intel (stored fact citations)
  Clause 18  content-quality gate
  Clause 19  search doctrine
  Clause 20  commitment_guard + tool_claim_guard
              (and 20(e) ground_truth_guard, 20(f) tool-fabrication)
  Clause 21  comprehension — understand before act

Clause 22 is the natural next step: ACT ONLY AFTER REASONING. Clauses
21 (understand) → 22 (reason) → response. Every substantive reply goes
through both.

Public API
──────────
  build_prefix(user_message: str, complexity: str = "") -> str
      The prompt instruction to prepend to the LLM message.

  strip(raw_response: str) -> tuple[str, str]
      Splits raw LLM output into (user_facing, scratchpad_content).
      Scratchpad content is empty string if the LLM didn't produce one.

  async persist(scratchpad: str, trace_id: str, user_message: str) -> None
      Store the scratchpad in Redis under the trace id for audit.
      Fire-and-forget — failure to persist does not affect the reply.

Clause 22 text is exported for merging into the living constitution.
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import logging
import re
from typing import Any

logger = logging.getLogger("aria.scratchpad")

# The delimiter tags. We use angle-bracket-XML-like markers because every
# LLM chain we use (DeepSeek, Anthropic, Gemini, Ollama) reproduces them
# verbatim without reformatting. Plain `###` or markdown markers get
# rewritten by some providers' formatting passes, which would break the
# regex strip.
_OPEN = "<scratchpad>"
_CLOSE = "</scratchpad>"

# R-F863 (2026-05-25) — tolerate MALFORMED close tags. Live incident: the LLM
# emitted `<scratchpad>…</scratchmark>` (wrong close tag) in a weekly brief; the
# strict `</scratchpad>` regex matched nothing, so the ENTIRE chain-of-thought
# (constitutional deliberation, counterarguments, clause analysis) leaked into
# the client-facing email — and the leaked copy was cached + re-served. Match
# any `</scratch…>`-shaped close so a one-character tag typo can never expose
# internal reasoning again.
_SCRATCHPAD_RE = re.compile(
    r"<scratchpad>(.*?)</scratch\w*>",
    re.DOTALL | re.IGNORECASE,
)

# Redis key pattern — one entry per trace so /trace shows the full
# reasoning lifecycle (question → scratchpad → answer → verification).
_REDIS_KEY_PREFIX = "crucix:scratchpad:"
_SCRATCHPAD_TTL_S = 30 * 86400  # 30 days — long enough to feed training


# ── Skip-logic — scratchpad is overkill for trivial messages ───────────────

_TRIVIAL_RE = re.compile(
    r"^\s*(hi|hello|hey|thanks|thank\s+you|ok|okay|yes|no|sure|sounds\s+good|"
    r"cheers|got\s+it|understood|noted|agreed|perfect|great|cool|nice|"
    r"👍|👎|✅|❌|🙏|:\)|:\(|:D)\s*[.?!]*\s*$",
    re.IGNORECASE,
)


def _is_trivial(message: str) -> bool:
    if not message or len(message.strip()) < 2:
        return True
    return bool(_TRIVIAL_RE.match(message.strip()))


# ── Public API ─────────────────────────────────────────────────────────────

def build_prefix(user_message: str, complexity: str = "") -> str:
    """Build the scratchpad-instruction prefix for the LLM prompt.

    Args:
        user_message: The user's question — used to skip scratchpad on
            trivial inputs (greetings, one-word acks).
        complexity: Optional complexity hint from comprehension pass
            (SIMPLE / MODERATE / COMPLEX / CRITICAL). CRITICAL inputs
            get a stricter scratchpad template.

    Returns the prefix string, or empty string if no scratchpad is
    warranted (trivial message).
    """
    if _is_trivial(user_message):
        return ""

    is_critical = (complexity or "").upper() == "CRITICAL"

    # The scratchpad template is intentionally structured — freeform
    # reasoning drifts into fluff. Structured reasoning forces each
    # question to be answered or explicitly marked N/A.
    if is_critical:
        scratchpad_body = (
            "1. What is the SPECIFIC question being asked? (not what it "
            "sounds like — what are they actually asking for?)\n"
            "2. What do I KNOW with high confidence vs what am I INFERRING "
            "from partial signal?\n"
            "3. What is the STRONGEST COUNTERARGUMENT to my intended "
            "conclusion? (Steelman it.)\n"
            "4. Where could I be WRONG, and how wrong? (list at least one "
            "concrete failure mode)\n"
            "5. Which CONSTITUTIONAL CLAUSE is most relevant? (Cite the "
            "number: Clause 9 / 14 / 17 / 18 / 19 / 20 / 21 / 22)\n"
            "6. If this is compliance / legal / financial, am I meeting "
            "the CLEAR-confidence threshold? If not, say so in the answer."
        )
    else:
        scratchpad_body = (
            "1. What is the actual question being asked?\n"
            "2. What do I know confidently vs what am I inferring?\n"
            "3. What's the strongest counterargument to my conclusion?\n"
            "4. Where could I be wrong?\n"
            "5. Which constitutional clause is most relevant?"
        )

    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="scratchpad",
        summary="Build Prefix",
        source_id="scratchpad:R-F996",
    )

    return (
        "\n\n[CLAUSE 22 — Think Before Speak]\n"
        "Before your user-facing response, produce a private reasoning "
        "block wrapped in exactly these tags:\n"
        f"  {_OPEN}\n"
        f"  {scratchpad_body}\n"
        f"  {_CLOSE}\n"
        "\n"
        "The scratchpad MUST appear BEFORE the user-facing response.\n"
        "The scratchpad is internal — it will be stripped from what the "
        "user sees. It exists so you test your conclusion against its "
        "strongest counterargument before committing to an answer.\n"
        "\n"
        "After the closing </scratchpad> tag, produce the USER-VISIBLE "
        "RESPONSE as normal (with BOTTOM LINE, structured body, etc.).\n"
        "\n"
        "Do not omit the scratchpad. If you are unsure, reason through "
        "the question in the scratchpad first and produce a cleaner "
        "answer for the user."
    )


def strip(raw_response: str) -> tuple[str, str]:
    """Split the raw LLM output into (user_facing, scratchpad_content).

    If no scratchpad is found, returns (raw_response, ""). Robust to
    minor formatting variations: case-insensitive tag match, extra
    whitespace around tags, multiple scratchpad blocks (first wins).

    If the LLM produced MULTIPLE scratchpad blocks (sometimes happens
    when the prompt is repeated via continuation), all are stripped
    but only the first is returned as scratchpad_content.
    """
    if not raw_response:
        return "", ""

    # Find first scratchpad
    first_match = _SCRATCHPAD_RE.search(raw_response)
    scratchpad_content = first_match.group(1).strip() if first_match else ""

    # Strip ALL scratchpad blocks from user-facing text
    user_facing = _SCRATCHPAD_RE.sub("", raw_response)
    # R-F863 — clean up an orphan OPEN tag with no recognizable close (LLM cut
    # off mid-scratchpad): strip from `<scratchpad>` to end, but only when no
    # `</scratch…>` close remains (the lookahead), so we never eat real content
    # that simply follows a properly-closed block.
    user_facing = re.sub(
        r"<scratchpad>(?:(?!</scratch)[\s\S])*$", "", user_facing,
        flags=re.IGNORECASE,
    )
    # R-F863 — orphan CLOSE at the very start (open tag missing/eaten): strip
    # everything up to and including the first `</scratch…>`. Tolerant of the
    # `</scratchmark>` class of malformed tags.
    user_facing = re.sub(
        r"^[\s\S]*?</scratch\w*>", "", user_facing, flags=re.IGNORECASE,
    )
    user_facing = user_facing.lstrip()

    return user_facing, scratchpad_content


async def persist(
    scratchpad: str,
    *,
    trace_id: str = "",
    user_message: str = "",
    user_facing_snippet: str = "",
) -> None:
    """Store the scratchpad content in Redis for audit + training-data mining.

    Fire-and-forget — non-fatal on any error. Keyed by trace_id when
    available so /trace shows reasoning alongside the response.
    """
    if not scratchpad:
        return
    try:
        from . import redis_store as rs
        import time
        key = _REDIS_KEY_PREFIX + (trace_id or f"notrace:{int(time.time() * 1000)}")
        payload = {
            "trace_id": trace_id,
            "ts": int(time.time()),
            "user_message": (user_message or "")[:500],
            "scratchpad": scratchpad[:8000],
            "user_facing_snippet": (user_facing_snippet or "")[:400],
        }
        await rs.set_json(key, payload, ex=_SCRATCHPAD_TTL_S)
    except Exception as e:
        logger.debug("[scratchpad] persist failed (non-fatal): %s", e)


# ── Clause 22 constitutional text (for merge into living constitution) ─────

CLAUSE_22_TEXT = """

CLAUSE 22 — THINK BEFORE SPEAK

For every substantive response, ARIA must:

a) PRIVATE SCRATCHPAD: Produce a <scratchpad>...</scratchpad> block
   BEFORE the user-visible response. The scratchpad is stripped from
   what the user sees — it exists so ARIA tests her conclusion against
   its strongest counterargument before committing.

b) FIVE STRUCTURED QUESTIONS: Every scratchpad answers, in order:
   (1) What is the specific question actually being asked?
   (2) What do I know with confidence vs what am I inferring?
   (3) What is the strongest counterargument to my conclusion?
   (4) Where could I be wrong, and how wrong?
   (5) Which constitutional clause is most relevant here?

c) CRITICAL-TIER EXPANSION: When the comprehension pass (Clause 21)
   marks the request as CRITICAL complexity (compliance, legal,
   financial, sanctions, DD), the scratchpad adds a sixth question:
   "Am I meeting the CLEAR-confidence threshold? If not, say so in
   the answer."

d) NOT-APPLICABLE TO TRIVIAL TURNS: Greetings, acknowledgements, and
   one-word replies skip the scratchpad. It applies to questions,
   instructions, and investigative asks — the places where reasoning
   quality matters.

e) PERSISTENCE: The scratchpad is stored against the trace_id for 30
   days. This serves three functions:
   (1) Audit — regulators or the operator can see how ARIA reasoned
   (2) Calibration — paired with outcomes, shows where reasoning
       was weak even when the output was fine (and vice versa)
   (3) Training data — (scratchpad, response) pairs feed the future
       Constitutional-AI DPO pipeline when ARIA moves from prompted
       to trained behaviour.

The goal is not to slow ARIA down. It is to ensure that when she
commits to a conclusion, that conclusion has been tested against its
strongest counterargument — not just produced in a single forward
pass from the input.
"""

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.

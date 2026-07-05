"""Guardian Layer 3 — multilingual platform-wide intent (R-F2447).

The "association cortex" of the living-organism roadmap
(docs/aria_guardian_roadmap_2026_06_27.md §3.2 L3): the SAME LLM-interpreter
idea that made Guardian understand any language (guardian/interpret.py, R-F1983),
generalised to ARIA's FULL user-facing tool vocabulary so a Portuguese
"investiga a empresa Acme", a French "vérifie si cette société est sanctionnée"
or an Arabic request routes to the right tool — not missed by the English-only
regex in routes/aria.py:_detect_tool_intent.

Design (mirrors L3.2 of the roadmap, binding):
  * FALLBACK, never a replacement: this runs ONLY after `_detect_tool_intent`
    returns None, so the fast, proven English regex stays the primary path.
  * Fail-safe: returns None on any error, low confidence, or tool="none" — so it
    can NEVER hijack an ordinary question. A normal question falls through to chat.
  * Output is a `_detect_tool_intent`-compatible dict: {"tool", "entity"/"query",
    "context", "confidence", "_reason"} — the SAME shape the dispatcher already
    consumes, so wiring is a fall-through, not a new dispatch path.
  * Constrained to the CORE user-facing tools that actually need multilingual
    routing; anything else -> "none" (fall through), never invented.

The LLM does the reasoning; we constrain it to a tiny JSON schema and validate
hard on the way out (unknown tool / empty arg / low confidence -> None).
"""
from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger("aria.intent.interpret")

# Core user-facing tools that benefit from multilingual routing. Names MUST match
# the `tool` values routes/aria.py:_detect_tool_intent already emits/dispatches.
_ENTITY_TOOLS = {"investigate", "screen", "profile", "dd_orchestrate"}
_QUERY_TOOLS = {"deep_research", "tech_explain", "contract_analysis"}
_BARE_TOOLS = {"help", "none"}
_VALID = _ENTITY_TOOLS | _QUERY_TOOLS | _BARE_TOOLS

# Default confidence floor — below this we fall through to chat (tunable via the
# caller). Kept conservative: a false tool-route is worse than a missed one here
# (the regex already caught the clear cases).
DEFAULT_MIN_CONFIDENCE = 0.6

_SYSTEM = """You are ARIA's intent router. The user wrote a message in ANY language. Decide whether it is a request to run one of ARIA's TOOLS, and if so which, and extract the main argument. Understand the language NATIVELY (Portuguese, Spanish, French, German, Italian, Arabic, etc.).

The tools:
- "investigate": look into / investigate a company or person (background, ownership, red flags). arg = the entity name.
- "screen": sanctions / compliance / watchlist check on an entity. arg = the entity name.
- "profile": build a profile / dossier on a company or person. arg = the entity name.
- "dd_orchestrate": run full due diligence / a DD report on an entity. arg = the entity name.
- "deep_research": open-ended research on a topic/question (not a single named entity). arg = the topic.
- "tech_explain": explain a technical concept. arg = the concept.
- "contract_analysis": review / analyse a contract or legal document. arg = short note.
- "help": the user asks what ARIA can do / how to use it. arg = optional topic.
- "none": ANYTHING ELSE — normal chat, a factual question, greetings, or unclear. When in ANY doubt, choose "none".

Reply with ONLY a JSON object (no prose, no code fence):
{"tool": one of ["investigate","screen","profile","dd_orchestrate","deep_research","tech_explain","contract_analysis","help","none"],
 "arg": the extracted entity/topic/note (string, "" if none),
 "confidence": 0.0 to 1.0}

Rules:
- Extract the entity/topic EXACTLY as written; never invent a name not in the message.
- A plain factual question ("what is the capital of France", "how are you") -> "none".
- If it is not clearly a tool request, -> "none".
- Convert nothing; just classify + extract."""


def _as_float(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _parse_json(raw: str):
    if not raw:
        return None
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw).strip()
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


async def interpret_tool(message: str, llm, *,
                         min_confidence: float = DEFAULT_MIN_CONFIDENCE) -> dict | None:
    """Multilingual tool-intent classification. Returns a
    `_detect_tool_intent`-compatible dict, or None to fall through to chat.

    Fail-safe: None on empty/unconfigured/error/low-confidence/none/empty-arg —
    it can never hijack an ordinary question.
    """
    msg = (message or "").strip()
    if not msg or llm is None or not getattr(llm, "is_configured", False):
        return None
    try:
        result = await llm.complete(_SYSTEM, msg[:1500], max_tokens=160, timeout=12.0)
        raw = (getattr(result, "text", "") or "").strip()
    except Exception as e:  # LLM down / cooldown -> fall through, never raise
        logger.warning("[intent.interpret] llm error: %s", e)
        return None

    data = _parse_json(raw)
    if not isinstance(data, dict):
        return None

    tool = str(data.get("tool") or "none").strip().lower()
    if tool not in _VALID or tool == "none":
        return None
    conf = _as_float(data.get("confidence"))
    if conf < min_confidence:
        return None
    arg = str(data.get("arg") or "").strip()

    out: dict = {"tool": tool, "context": msg, "confidence": conf,
                 "_reason": "llm_intent_fallback_rf2447"}
    if tool in _ENTITY_TOOLS:
        if len(arg) < 2:
            return None                 # never route an entity tool with no entity
        out["entity"] = arg[:160]
    elif tool in _QUERY_TOOLS:
        if tool != "contract_analysis" and len(arg) < 2:
            return None
        out["query"] = arg[:400]
    elif tool == "help":
        out["topic"] = arg[:80].lower()
    return out

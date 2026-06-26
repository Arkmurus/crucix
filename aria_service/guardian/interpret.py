"""Guardian multilingual intent comprehension (R-F1983).

The deterministic `_guardianIntent` in the WA listener is fast and reliable for
clear English phrasing — but it is brittle and English-only. This is the
UNDERSTANDING layer: it asks the LLM to read a message in ANY language and reason
out a structured Guardian intent, so a user whose first language isn't English
("verifica-me daqui a um minuto", "comprueba si estoy bien en 5 minutos",
"préviens-moi dans 10 minutes") is understood just as well. The LLM does the
reasoning; we constrain it to a small JSON schema and fall back to action="none"
on any doubt so it can never hijack an ordinary question.
"""
from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger("aria.guardian.interpret")

_VALID = {"arm", "clear", "panic", "circle_add", "circle_list", "status",
          "send", "send_confirm", "send_cancel", "pause", "resume", "none"}

_SYSTEM = """You are ARIA's Guardian intent parser. The user wrote a message in ANY language. Decide whether it is a personal-SAFETY / Guardian command and, if so, extract it.

Guardian lets a user:
- set a safety CHECK-IN ("check on me in N minutes" — if they don't confirm they're safe by then, ARIA alerts their trusted circle),
- say they are SAFE (all clear),
- raise a PANIC / SOS,
- manage their trusted CIRCLE of contacts,
- SEND a WhatsApp message on their behalf.

Reply with ONLY a JSON object — no prose, no code fence:
{"action": one of ["arm","clear","panic","circle_add","circle_list","status","send","send_confirm","send_cancel","pause","resume","none"],
 "minutes": number (arm only; convert hours to minutes),
 "name": contact name (circle_add only),
 "jid": phone number digits (circle_add only),
 "to": recipient name or number (send only),
 "message": the text to send (send), or a short note (panic/arm),
 "confidence": 0.0 to 1.0}

Mapping rules:
- "check on me / check in / make sure I'm safe in N min/hours" -> "arm" (set minutes).
- "I'm safe / all clear / I'm home safe / I'm fine now" -> "clear".
- "panic / SOS / I'm in danger / help me / emergency" -> "panic".
- "add <name> <phone> to my circle" -> "circle_add".
- "who's in my circle / show my circle" -> "circle_list".
- "tell/text/message <someone> saying <text>" -> "send" (set to + message).
- "send it / yes send" -> "send_confirm". "don't send / cancel that" -> "send_cancel".
- "stop / pause guardian" -> "pause". "resume guardian" -> "resume".
- Anything that is NOT a guardian/safety command (a normal question, research, chit-chat, a company/sanctions query) -> {"action":"none"}.

Understand the language NATIVELY (Portuguese, Spanish, French, German, Italian, Arabic, etc.) and read numbers written as words in that language. When unsure, choose "none". NEVER invent a phone number or name that is not present in the message."""


async def interpret(message: str, llm) -> dict:
    """Classify a message into a Guardian intent using the LLM. Always returns a
    dict with at least {action, confidence}; degrades to action="none" on any
    error so the caller can safely fall through to normal chat."""
    msg = (message or "").strip()
    if not msg or llm is None or not getattr(llm, "is_configured", False):
        return {"action": "none", "confidence": 0.0}
    try:
        result = await llm.complete(_SYSTEM, msg[:1500], max_tokens=220, timeout=12.0)
        raw = (getattr(result, "text", "") or "").strip()
    except Exception as e:
        logger.warning("[guardian.interpret] llm error: %s", e)
        return {"action": "none", "confidence": 0.0, "error": str(e)[:120]}

    data = _parse_json(raw)
    if not isinstance(data, dict):
        return {"action": "none", "confidence": 0.0}

    action = str(data.get("action") or "none").strip().lower()
    if action not in _VALID:
        action = "none"
    out: dict = {"action": action, "confidence": _as_float(data.get("confidence"))}

    if action == "arm":
        mins = _as_float(data.get("minutes"))
        if mins and mins > 0:
            out["minutes"] = max(1.0, min(mins, 24 * 60))
            out["message"] = str(data.get("message") or "")[:200]
        else:
            out["action"] = "none"
    elif action == "circle_add":
        out["name"] = str(data.get("name") or "").strip()[:60]
        out["jid"] = re.sub(r"\D", "", str(data.get("jid") or ""))
        if not out["name"] or len(out["jid"]) < 7:
            out["action"] = "none"   # never enrol without a real name + number
    elif action == "send":
        out["to"] = str(data.get("to") or "").strip()[:80]
        out["message"] = str(data.get("message") or "").strip()[:1000]
        if not out["to"] or not out["message"]:
            out["action"] = "none"
    elif action == "panic":
        out["message"] = str(data.get("message") or "")[:200]

    return out


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

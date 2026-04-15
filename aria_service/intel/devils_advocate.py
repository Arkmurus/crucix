"""ARIA — Devil's Advocate (Slice 6).

Reads a meeting transcript and returns three things the team did not say:

  - counter_arguments         — reasons the apparent consensus could be wrong
  - unasked_dd_questions      — standard DD/compliance questions never asked
  - unchallenged_assumptions  — premises everyone treated as fact

Tone rule: advisory, never accusatory. Phrase as questions and "consider"
clauses, never as "you missed" or "you're wrong". The team must want
ARIA in the next meeting; one bad interjection ends the experiment.

Hook points
-----------
  - services/aria_zoom_service.py  (post-meeting debrief, after _debrief LLM call)
  - future: mid-meeting on consensus keyword triggers ("let's decide", "we agree")

Guardrails enforced upstream (in aria_zoom_service):
  - ARIA_CHALLENGE_ENABLED env flag (default off)
  - #ariasilent in meeting title disables for that meeting
  - Redis rate-limit: max 1 challenge per 15 min per meeting
  - Output capped to 3 items per category
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import httpx

logger = logging.getLogger("aria.devils_advocate")

BRAIN_URL = os.getenv("BRAIN_SERVICE_URL", "http://localhost:3117")
INT_TOKEN = os.getenv("ARIA_INTERNAL_TOKEN", "aria-internal")

# Standard defence-broking DD/compliance questions ARIA expects to hear at
# least once per deal-relevant meeting. If none of these appear in the
# transcript, the corresponding "unasked" flag fires.
_DD_QUESTION_BANK = [
    ("end_user", ["end user", "end-user", "euc", "end user certificate"]),
    ("sanctions", ["sanction", "ofac", "ofsi", "eu consolidated", "un sanctions"]),
    ("export_licence", ["export licence", "export license", "ecju", "siel", "oiel"]),
    ("ubo", ["ubo", "ultimate beneficial", "beneficial owner", "shareholder"]),
    ("pep", ["pep", "politically exposed"]),
    ("itar", ["itar", "ear", "us origin", "us-origin"]),
    ("payment_route", ["payment", "wire", "bank", "escrow", "letter of credit"]),
    ("delivery_route", ["incoterm", "delivery", "shipping", "freight forwarder"]),
    ("price_basis", ["price basis", "fob", "cif", "ddu", "ddp", "exw"]),
    ("warranty", ["warranty", "after-sales", "spares", "support"]),
]


def _heuristic_unasked_dd(transcript_text: str) -> list[str]:
    """Cheap pre-filter — flag DD topics never mentioned in transcript."""
    t = transcript_text.lower()
    missing = []
    for label, keywords in _DD_QUESTION_BANK:
        if not any(kw in t for kw in keywords):
            missing.append(label)
    return missing


def _build_prompt(
    title: str,
    participants: list[str],
    transcript_formatted: str,
    heuristic_missing: list[str],
    deal_context: str | None = None,
) -> str:
    context_block = f"\nDEAL CONTEXT: {deal_context}\n" if deal_context else ""
    missing_hint = (
        f"\nHEURISTIC HINT: these standard DD topics never came up in the transcript — "
        f"check if any matter for this deal: {', '.join(heuristic_missing)}\n"
        if heuristic_missing else ""
    )

    return f"""You are ARIA acting as a constructive devil's advocate for the Arkmurus team.

Your job is NOT to summarise the meeting (someone else does that). Your job is to
surface what the team did NOT say but probably should have. You are a teammate,
not a critic — phrase everything as a question or a "worth considering" clause.
Never write "you missed" or "you should have". Never accuse anyone.

Tone rules:
  - Advisory, not accusatory.
  - Specific to what was actually said in the transcript.
  - Skip generic platitudes ("consider risk") — only flag concrete gaps.
  - If the team genuinely covered everything, return empty arrays. Silence is
    better than noise.

MEETING: {title}
PARTICIPANTS: {', '.join(participants)}{context_block}{missing_hint}
TRANSCRIPT:
{transcript_formatted}

Return JSON ONLY in this exact shape (max 3 items per array, can be empty):
{{
  "counter_arguments": ["...", "..."],
  "unasked_dd_questions": ["...", "..."],
  "unchallenged_assumptions": ["...", "..."],
  "confidence": "low|medium|high"
}}"""


async def analyse(
    title: str,
    participants: list[str],
    transcript: list[tuple[str, str, str]],
    deal_context: str | None = None,
) -> dict[str, Any]:
    """Run the devil's advocate analysis.

    Returns dict with counter_arguments, unasked_dd_questions,
    unchallenged_assumptions, confidence, and meta.
    Returns empty-arrays dict on any failure (silence beats noise).
    """
    if not transcript:
        return _empty("no_transcript")

    transcript_formatted = "\n".join(
        f"[{ts[11:16]}] {s}: {t}" for s, t, ts in transcript
    )[:12000]

    heuristic_missing = _heuristic_unasked_dd(transcript_formatted)

    prompt = _build_prompt(
        title, participants, transcript_formatted, heuristic_missing, deal_context
    )

    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                f"{BRAIN_URL}/api/aria/chat",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {INT_TOKEN}",
                },
                json={
                    "message": prompt,
                    "session_id": f"devils_advocate_{title[:30]}",
                },
            )
        if r.status_code != 200:
            logger.warning("devils_advocate chat returned %s", r.status_code)
            return _empty(f"http_{r.status_code}")

        full = (r.json().get("response") or r.json().get("answer") or "").strip()
        m = re.search(r"\{[\s\S]*\}", full)
        if not m:
            return _empty("no_json")

        parsed = json.loads(m.group())
    except Exception as e:
        logger.warning("devils_advocate failed: %s", e)
        return _empty(f"exception:{type(e).__name__}")

    return {
        "counter_arguments": _clean_list(parsed.get("counter_arguments")),
        "unasked_dd_questions": _clean_list(parsed.get("unasked_dd_questions")),
        "unchallenged_assumptions": _clean_list(parsed.get("unchallenged_assumptions")),
        "confidence": (parsed.get("confidence") or "medium").lower(),
        "meta": {
            "heuristic_missing_dd": heuristic_missing,
            "ok": True,
        },
    }


def _clean_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned = []
    for item in value:
        if not isinstance(item, str):
            continue
        s = item.strip()
        if 8 <= len(s) <= 280:
            cleaned.append(s)
    return cleaned[:3]


def _empty(reason: str) -> dict[str, Any]:
    return {
        "counter_arguments": [],
        "unasked_dd_questions": [],
        "unchallenged_assumptions": [],
        "confidence": "low",
        "meta": {"ok": False, "reason": reason},
    }


def render_for_telegram(da: dict[str, Any]) -> str:
    """Format devil's advocate output for the Telegram debrief block."""
    parts = []
    ca = da.get("counter_arguments") or []
    uq = da.get("unasked_dd_questions") or []
    ua = da.get("unchallenged_assumptions") or []
    if not (ca or uq or ua):
        return ""

    parts.append("\n🎯 *ARIA — Devil's Advocate*")
    if ca:
        parts.append("*Counter-arguments to consider:*")
        parts.extend(f"  ↪ {c}" for c in ca)
    if uq:
        parts.append("*Questions the team did not ask:*")
        parts.extend(f"  ❓ {q}" for q in uq)
    if ua:
        parts.append("*Assumptions left unchallenged:*")
        parts.extend(f"  ⚖ {a}" for a in ua)
    conf = da.get("confidence", "medium")
    parts.append(f"_ARIA confidence: {conf}. Push back if any are wrong — feeds the mistake ledger._")
    return "\n".join(parts) + "\n"


def render_for_zoom_chat(da: dict[str, Any]) -> str:
    """Compact version for in-meeting Zoom chat (1000 char cap)."""
    ca = da.get("counter_arguments") or []
    uq = da.get("unasked_dd_questions") or []
    ua = da.get("unchallenged_assumptions") or []
    if not (ca or uq or ua):
        return ""

    lines = ["ARIA — devil's advocate (advisory, push back if wrong):"]
    for c in ca[:2]:
        lines.append(f"• Counter: {c}")
    for q in uq[:2]:
        lines.append(f"• Unasked: {q}")
    for a in ua[:1]:
        lines.append(f"• Assumption: {a}")
    return "\n".join(lines)[:1000]

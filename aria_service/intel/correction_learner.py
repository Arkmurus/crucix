"""ARIA correction learner.

Closes the loop on the round-5 Ari-correction failure mode.

The bug we're fixing
════════════════════
When Ari corrected ARIA on the Ghana defence minister, his message contained
specific factual content:
  • Edward Omane Boamah was Mahama's appointee
  • Boamah died in a helicopter crash on 6 August 2025
  • Cassiel Ato Forson is acting Defence Minister
  • Ernest Brogya Genfi is Deputy Minister running day-to-day
  • As of March 2026, no substantive replacement named

ARIA's reply acknowledged the error and tagged the position [UNCERTAIN] but
discarded ALL of Ari's specific facts. The corrective information lived for
exactly one turn in the LLM's context window and was lost forever the next
time anyone asked the same question.

Fix architecture
════════════════
Two paired mechanisms — one writes, one reads.

1. WRITE — `extract_and_persist`
   When the user message looks like a correction containing factual content,
   spawn a background task that:
     a) Detects the correction pattern (regex)
     b) Calls a tight LLM prompt to extract structured facts from the
        correction text — specific verifiable claims only, no opinions or
        process feedback
     c) Stores each extracted fact in knowledge.py via store_fact() with
        source='user_correction:<sender>' and confidence='PROBABLE'.
        knowledge.store_fact already handles dedup + contradiction detection
        + supersession of stale facts, so this slots in cleanly.

2. READ — `recent_corrections_addendum`
   New system-prompt addendum injected by _build_calibrated_system_prompt
   that pulls all facts with source starting 'user_correction:' from the
   last N hours and surfaces them as a "RECENT USER CORRECTIONS" block.
   Tells the LLM these facts OVERRIDE training data and any other knowledge
   layer for the same subject. This is the highest-trust knowledge channel
   because it came from a verified human who actually knows the truth.

Both gates behind feature flags for emergency rollback:
  ARIA_CORRECTION_LEARN     — write side (default ON)
  ARIA_CORRECTION_RECALL    — read side (default ON)

Why background extraction (not inline)
══════════════════════════════════════
The LLM extractor is a small additional call that adds 2-5s. If we ran it
inline, the user would wait an extra 5s for every chat reply. Background
fire-and-forget means the user gets the immediate reply at full speed and
the corrective facts land in the knowledge base for the NEXT reply.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Any

logger = logging.getLogger("aria.correction_learner")

# Feature flags
def _write_enabled() -> bool:
    val = os.getenv("ARIA_CORRECTION_LEARN", "1") or "1"
    return val.strip().lower() not in ("0", "false", "no", "off")


def _read_enabled() -> bool:
    val = os.getenv("ARIA_CORRECTION_RECALL", "1") or "1"
    return val.strip().lower() not in ("0", "false", "no", "off")


# M2 — authorised senders for KB writes. Any WhatsApp / chat user used to
# be able to post a "correction" and have it persisted with PROBABLE
# confidence, which opens a prompt-injection path into the permanent
# knowledge base. Now enforced via an allow-list.
#   ARIA_CORRECTION_TRUSTED_SENDERS="antonio,ari"  (comma, case-insensitive)
#   ARIA_CORRECTION_AUTHZ_OFF=1                    (emergency escape hatch)
def _trusted_senders() -> set[str]:
    raw = os.getenv("ARIA_CORRECTION_TRUSTED_SENDERS", "") or ""
    return {s.strip().lower() for s in raw.split(",") if s.strip()}


def _sender_is_authorised(sender_name: str) -> tuple[bool, str]:
    if (os.getenv("ARIA_CORRECTION_AUTHZ_OFF", "") or "").strip().lower() in ("1", "true", "yes", "on"):
        return True, "authz_off"
    trusted = _trusted_senders()
    if not trusted:
        # No allow-list configured — refuse writes by default so a fresh
        # deploy can't be poisoned before ops sets the list. This is the
        # opposite of the previous default, and it's intentional.
        return False, "no_allow_list"
    s = (sender_name or "").strip().lower()
    if not s:
        return False, "empty_sender"
    # Exact match OR prefix match ('antonio' matches 'antonio_desktop').
    if s in trusted or any(s.startswith(t + "_") or s.startswith(t + "-") for t in trusted):
        return True, "match"
    return False, "not_in_allow_list"


# How many hours back to surface recent corrections in the prompt addendum.
# 168h = 7 days. Long enough that a Monday correction is still active on
# Friday but short enough that stale corrections don't pile up forever.
_CORRECTION_RECENCY_HOURS = 168

# Cap on how many corrections to inject per reply — too many and the prompt
# bloats, the LLM stops paying attention to specific clauses.
_MAX_CORRECTIONS_IN_PROMPT = 10


# ── Detection ───────────────────────────────────────────────────────────────
# More permissive than reasoning_library._looks_like_user_correction because
# we WANT to catch corrections that contain new facts, not just complaints.
#
# Two-signal design: a message is treated as a correction when it contains
# AT LEAST ONE marker from EITHER list. The lists overlap in some patterns
# but the union is more permissive than either alone.
#   STRONG: explicit "you got it wrong" patterns — single match is enough
#   FACTUAL: factual-correction language ("he served under", "she died in
#            YYYY", "since YYYY" + name) — also single match is enough
# This catches Antonio's two failing cases:
#   - "He is not. He served under Akufo-Addo. Mahama won the 2024 election"
#   - "You said Boamah is alive but he died in a helicopter crash on 6 August 2025"
_CORRECTION_DETECTOR_RE = re.compile(
    r"\b(?:"
    # Direct accusations
    r"that(?:\s+was|'?s)\s+(?:wrong|incorrect|the\s+wrong)"
    r"|you('?re|\s+are)\s+(?:wrong|incorrect|mistaken)"
    r"|you\s+(?:are|got|stated|claimed|said|named|presented|reported)\s+[^.!?]{0,40}\s+wrong"
    r"|you\s+said\s+[^.!?]{0,80}\bbut\b"
    r"|you\s+said\s+[^.!?]{0,80}\bhowever\b"
    r"|you\s+(?:are|were)\s+(?:relying\s+on|using|presenting)\s+(?:stale|outdated|old)"
    r"|wrong\s+answer"
    r"|(?:that|this|it|he|she|they)\s+is\s+(?:wrong|incorrect|inaccurate|outdated|not\s+(?:correct|right|true|the))"
    r"|(?:that|this|it)\s+(?:was|is)\s+not\s+(?:correct|right|the\s+\w+)"
    # Corrective phrasings
    r"|(?:the\s+)?(?:actual|real|current|correct)\s+(?:fact|truth|answer|name|figure|holder|minister|director|person)\s+(?:is|was|are)"
    r"|let\s+me\s+correct\s+you"
    r"|i\s+(?:need\s+to\s+|must\s+)?correct\s+you"
    r"|don'?t\s+present\s+outdated"
    r"|the\s+\w+\s+breakdown\s+was\s+(?:useful|good)\s+but"
    r"|you\s+named\s+\w+\s+as"
    r"|the\s+correct\s+(?:answer|name|date|figure)"
    # Factual correction signals — these often appear in corrections that
    # also contain corrective facts (officeholder transitions, deaths,
    # appointments, election outcomes, budget figures).
    r"|(?:he|she|they)\s+(?:served|worked|was|were)\s+under\s+[A-Z]"
    r"|(?:died|killed|deceased|passed\s+away)\s+(?:in|on)\s+\w+\s+\d{4}"
    r"|(?:helicopter\s+)?crash\s+(?:in|on)\s+\w+\s+\d{4}"
    r"|(?:appointed|named|elected|chosen)\s+(?:in|by)\s+(?:[A-Z]\w+|\w+\s+\d{4})"
    r"|(?:won|lost)\s+(?:the\s+)?(?:election|vote|presidency)\s+(?:in\s+)?\w+\s+\d{4}"
    r"|(?:since|as\s+of)\s+\w+\s+\d{4}"
    r"|(?:after|following)\s+the\s+\d{4}\s+election"
    r"|(?:he|she)\s+is\s+not\.\s+[A-Z]"  # "He is not. <Capitalised next sentence>"
    r")",
    re.IGNORECASE,
)


def looks_like_correction(message: str) -> bool:
    """True if the message looks like the user is correcting ARIA. More
    permissive than the reasoning_library guard because we WANT to extract
    facts from these, not just block caching."""
    if not message or len(message) < 30:
        return False
    return bool(_CORRECTION_DETECTOR_RE.search(message))


# ── LLM-driven fact extraction ──────────────────────────────────────────────

_EXTRACTION_SYSTEM_PROMPT = """You are extracting verifiable corrective facts from a user correction sent to ARIA, an intelligence agent. The user has told ARIA she got something wrong and is providing the correct information. Your job is to extract ONLY specific verifiable factual claims.

EXTRACT:
  - Specific claims about people, positions, dates, events, organizations
  - Officeholder appointments and changes (especially with dates)
  - Numerical figures and budgets
  - Verifiable historical events
  - Organizational facts (mergers, deaths, resignations, sanctions changes)

DO NOT EXTRACT:
  - Opinions or value judgments ("you should be more careful")
  - Process feedback ("don't make this mistake again")
  - Meta-commentary about ARIA herself
  - Vague generalities ("the situation is complicated")
  - Questions
  - Restatements of ARIA's wrong answer (only the corrections)

Each extracted fact must be:
  - Self-contained (readable without the surrounding correction)
  - Verifiable (a third party could check it)
  - Subject-tagged (which entity / country it's about)

Return STRICT JSON only, no prose:
{"facts": [
  {"topic": "<short title>", "subject": "<entity/country>", "content": "<full claim>", "date_referenced": "<YYYY-MM or empty>"}
]}

If no facts can be extracted (the message is pure complaint with no corrective content), return {"facts": []}."""


_FACT_EXTRACTION_TIMEOUT_S = 30.0
_FACT_EXTRACTION_MAX_TOKENS = 800


async def _extract_facts_via_llm(text: str, llm: Any) -> list[dict]:
    """Run the LLM extractor and return a list of fact dicts.
    Returns empty list on any failure — extraction is best-effort."""
    if not llm or not getattr(llm, "is_configured", False):
        return []
    if not text or len(text.strip()) < 30:
        return []
    try:
        result = await llm.complete(
            _EXTRACTION_SYSTEM_PROMPT,
            f"User correction text:\n\n{text[:4000]}",
            max_tokens=_FACT_EXTRACTION_MAX_TOKENS,
            timeout=_FACT_EXTRACTION_TIMEOUT_S,
        )
        # llm.complete returns either a string or an object with .text
        raw = result.text if hasattr(result, "text") else str(result)
    except Exception as e:
        logger.debug("correction fact extraction LLM call failed: %s", e)
        return []
    # Pull JSON out of the response
    json_match = re.search(r"\{[\s\S]*\}", raw)
    if not json_match:
        return []
    try:
        parsed = json.loads(json_match.group())
    except Exception as e:
        logger.debug("correction extraction JSON parse failed: %s", e)
        return []
    facts = parsed.get("facts") or []
    if not isinstance(facts, list):
        return []
    # Sanity-check each fact has the required keys
    out: list[dict] = []
    for f in facts:
        if not isinstance(f, dict):
            continue
        topic = (f.get("topic") or "").strip()
        content = (f.get("content") or "").strip()
        if not topic or not content or len(content) < 10:
            continue
        out.append({
            "topic": topic[:200],
            "subject": (f.get("subject") or "").strip()[:100],
            "content": content[:1000],
            "date_referenced": (f.get("date_referenced") or "").strip()[:30],
        })
    return out


# ── Persistence ─────────────────────────────────────────────────────────────

async def _persist_facts(facts: list[dict], sender_name: str) -> int:
    """Store each extracted fact in knowledge.py. Returns count actually
    stored. knowledge.store_fact handles dedup + contradiction detection +
    supersession internally so we don't have to manage any of that here."""
    if not facts:
        return 0
    from . import knowledge as _kn
    n_stored = 0
    safe_sender = re.sub(r"[^a-zA-Z0-9_-]", "_", sender_name or "user")[:40]
    source = f"user_correction:{safe_sender}"
    for f in facts:
        try:
            await _kn.store_fact(
                topic=f["topic"],
                content=f["content"],
                source=source,
                confidence="PROBABLE",  # high-trust but still verifiable by another source
            )
            n_stored += 1
        except Exception as e:
            logger.debug("store_fact failed for %r: %s", f.get("topic"), e)
    return n_stored


# ── Public write API ────────────────────────────────────────────────────────

async def extract_and_persist(message: str, sender_name: str, llm: Any) -> dict:
    """Detect correction, extract facts via LLM, store in knowledge.py.

    Returns a summary dict:
      {detected: bool, extracted: int, stored: int}

    Safe to call from a fire-and-forget background task — never raises,
    always returns a dict.
    """
    if not _write_enabled():
        return {"detected": False, "extracted": 0, "stored": 0, "skipped": "write_disabled"}
    if not looks_like_correction(message):
        return {"detected": False, "extracted": 0, "stored": 0}
    authorised, reason = _sender_is_authorised(sender_name)
    if not authorised:
        logger.warning(
            "[correction_learner] REJECTED write from unauthorised sender '%s' (%s) — "
            "set ARIA_CORRECTION_TRUSTED_SENDERS env var to allow.",
            sender_name, reason,
        )
        return {"detected": True, "extracted": 0, "stored": 0, "skipped": f"unauthorised:{reason}"}
    facts = await _extract_facts_via_llm(message, llm)
    if not facts:
        return {"detected": True, "extracted": 0, "stored": 0}
    n_stored = await _persist_facts(facts, sender_name)
    if n_stored:
        logger.info(
            "[correction_learner] stored %d fact(s) from %s correction: %s",
            n_stored, sender_name, [f["topic"][:60] for f in facts[:3]],
        )
    return {"detected": True, "extracted": len(facts), "stored": n_stored}


# ── Public read API: recent corrections addendum ────────────────────────────

async def recent_corrections_addendum(message: str = "") -> str:
    """Build a system-prompt addendum surfacing recent user corrections.

    Pulls facts from knowledge.py whose source starts with 'user_correction:'
    and were created in the last _CORRECTION_RECENCY_HOURS. If `message` is
    non-empty, prefers corrections whose subject or topic matches words in
    the message (so a Ghana question surfaces Ghana corrections first).

    Returns the addendum text or empty string if disabled / no corrections.
    """
    if not _read_enabled():
        return ""
    try:
        from . import knowledge as _kn
        db = await _kn._load()
    except Exception as e:
        logger.debug("recent_corrections_addendum: knowledge load failed: %s", e)
        return ""
    facts = db.get("facts") or []
    if not facts:
        return ""

    cutoff_ts = time.time() - (_CORRECTION_RECENCY_HOURS * 3600)
    recent: list[dict] = []
    for f in facts:
        source = f.get("source") or ""
        if not source.startswith("user_correction:"):
            continue
        # Parse createdAt to epoch — knowledge.py stores ISO format
        created_at = f.get("createdAt") or ""
        try:
            from datetime import datetime
            ts = datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp()
        except Exception:
            ts = 0
        if ts < cutoff_ts:
            continue
        recent.append(f)

    if not recent:
        return ""

    # Optional relevance ranking by message tokens
    if message:
        msg_lower = message.lower()
        msg_tokens = set(re.findall(r"\b[a-z]{4,}\b", msg_lower))

        def relevance(f: dict) -> int:
            blob = f"{f.get('topic', '')} {f.get('content', '')}".lower()
            blob_tokens = set(re.findall(r"\b[a-z]{4,}\b", blob))
            return len(msg_tokens & blob_tokens)

        recent.sort(key=lambda f: (-relevance(f), f.get("createdAt") or ""), reverse=False)
    else:
        recent.sort(key=lambda f: f.get("createdAt") or "", reverse=True)

    recent = recent[:_MAX_CORRECTIONS_IN_PROMPT]

    lines = [
        "RECENT USER CORRECTIONS — these OVERRIDE everything else you know",
        "",
        "The following facts were provided by users correcting earlier ARIA replies. They are HIGHER-TRUST than your training data, the knowledge layer, RAG retrieval, or any other source for the same subject. If your training data conflicts with one of these corrections, the correction wins. Cite them as `[PROBABLE — per user correction <date>]` when you use them.",
        "",
    ]
    for i, f in enumerate(recent, 1):
        topic = f.get("topic", "")
        content = (f.get("content") or "")[:300]
        source = f.get("source", "")
        created = (f.get("createdAt") or "")[:10]
        lines.append(f"{i}. [{topic}] {content}")
        lines.append(f"   _source: {source} · {created}_")
    return "\n".join(lines)

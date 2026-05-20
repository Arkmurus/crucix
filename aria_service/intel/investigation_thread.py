"""R-F734 (2026-05-20) — investigation-thread state across chat turns.

Agent 1 DD audit found: "Multi-turn investigations are stateless. ARIA
re-analyzes the entity from scratch each turn; no accumulated findings
card / watchlist entry / case file automatically carried forward."

R-F734 augments the session schema with an `investigation_thread`
sub-dict that carries:

  current_entity:    str   — the entity the current chat is investigating
  current_canonical: str   — canonical form (via R-F730 resolver)
  entity_type:       str   — person | company | unknown (R-F730)
  last_tool:         str   — tool fired this turn ("dd_orchestrate" etc.)
  last_run_id:       str   — last orchestrator run id (for cross-turn ref)
  facts_so_far:      list  — accumulated findings (≤20, most recent first)
  open_questions:    list  — questions the LLM flagged for follow-up
  updated_at:        float — last touch time

Read at chat-turn start, written at chat-turn end. Persisted alongside
the existing `session["messages"]` array via the same `_save_session`
call — no new persistence path.

Continuity rule: if the new user message does NOT name a new entity
AND the thread is recent (<6h), carry forward the previous entity
context as a [INVESTIGATION THREAD] context block. This is what makes
"OK, now check their banking exposure" work after a previous turn
resolved "Acme Corp".
"""
from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger("aria.intel.investigation_thread")

# Thread is considered "stale" after this — a brand-new question on a
# session left open >6h likely isn't a follow-up.
THREAD_TTL_SECONDS = 6 * 3600

# Cap on the accumulated facts list — bounds the session blob.
MAX_FACTS = 20
MAX_OPEN_QUESTIONS = 10


def _empty_thread() -> dict[str, Any]:
    return {
        "current_entity":    "",
        "current_canonical": "",
        "entity_type":       "unknown",
        "last_tool":         "",
        "last_run_id":       "",
        "facts_so_far":      [],
        "open_questions":    [],
        "updated_at":        0.0,
    }


def get_thread(session: dict | None) -> dict[str, Any]:
    """Read investigation_thread from a session dict. Returns the empty
    shape when not present / stale. Fail-soft against malformed shapes."""
    if not isinstance(session, dict):
        return _empty_thread()
    thread = session.get("investigation_thread")
    if not isinstance(thread, dict):
        return _empty_thread()
    # TTL gate: if the thread is older than the cutoff, treat as fresh
    updated_at = thread.get("updated_at", 0)
    try:
        updated_at = float(updated_at)
    except (TypeError, ValueError):
        updated_at = 0.0
    if updated_at and (time.time() - updated_at) > THREAD_TTL_SECONDS:
        logger.debug(
            "investigation_thread: stale by %.0fs — returning empty shape",
            time.time() - updated_at,
        )
        return _empty_thread()
    # Backfill any missing keys
    empty = _empty_thread()
    for k in empty:
        if k not in thread:
            thread[k] = empty[k]
    return thread


def is_likely_followup(
    new_message: str,
    thread: dict[str, Any],
) -> bool:
    """Heuristic: is the new user message a follow-up on the current
    thread entity? True when:
      - the thread has a current_entity AND
      - the new message does NOT explicitly name a different entity
        (best-effort — we look for the thread entity's canonical/query
        token in the new message OR for follow-up signal words)

    The chat path uses this to decide whether to PREPEND the prior
    thread context block or skip it (fresh entity = fresh context).
    """
    if not thread or not isinstance(thread, dict):
        return False
    entity = (thread.get("current_canonical") or thread.get("current_entity") or "").strip()
    if not entity:
        return False
    msg = (new_message or "").strip().lower()
    if not msg:
        return False

    # If the new message mentions the entity by canonical / query token,
    # it's clearly a follow-up
    if entity.lower() in msg:
        return True

    # Otherwise, look for follow-up signal words (pronouns, continuation
    # phrases, common DD next-question patterns)
    followup_patterns = (
        "they", "their", "them", "it ", "its ",
        "follow up", "what about", "also check", "also screen",
        "and what", "anything else", "more on", "drill into",
        "go deeper", "tell me more", "expand on", "what's next",
        "next step",
    )
    return any(p in msg for p in followup_patterns)


def update(
    session: dict | None,
    *,
    entity: str = "",
    canonical: str = "",
    entity_type: str = "",
    tool: str = "",
    run_id: str = "",
    new_facts: list[str] | None = None,
    new_open_questions: list[str] | None = None,
) -> dict[str, Any]:
    """Update the investigation_thread on a session dict in-place + return
    the updated thread. Caller is responsible for the surrounding
    `_save_session` call that already persists session as a whole.

    Empty kwargs are ignored (so callers can update only what changed).
    """
    if not isinstance(session, dict):
        return _empty_thread()

    thread = get_thread(session)
    now = time.time()

    if entity:
        # If the entity has changed, reset the facts list — facts about
        # Acme Corp aren't carry-forward context for a follow-up on
        # Smith Industries.
        if thread["current_entity"] and entity.strip().lower() != thread["current_entity"].strip().lower():
            thread["facts_so_far"] = []
            thread["open_questions"] = []
        thread["current_entity"] = entity[:300]

    if canonical:
        thread["current_canonical"] = canonical[:300]

    if entity_type:
        thread["entity_type"] = entity_type[:30]

    if tool:
        thread["last_tool"] = tool[:60]

    if run_id:
        thread["last_run_id"] = run_id[:120]

    if new_facts:
        existing = thread.get("facts_so_far") or []
        # Prepend new facts (most recent first), dedupe by string
        seen = set(existing)
        prepended = []
        for f in new_facts:
            fstr = str(f).strip()[:500]
            if fstr and fstr not in seen:
                prepended.append(fstr)
                seen.add(fstr)
        thread["facts_so_far"] = (prepended + existing)[:MAX_FACTS]

    if new_open_questions:
        existing_q = thread.get("open_questions") or []
        seen_q = set(existing_q)
        prepended_q = []
        for q in new_open_questions:
            qstr = str(q).strip()[:300]
            if qstr and qstr not in seen_q:
                prepended_q.append(qstr)
                seen_q.add(qstr)
        thread["open_questions"] = (prepended_q + existing_q)[:MAX_OPEN_QUESTIONS]

    thread["updated_at"] = now
    session["investigation_thread"] = thread
    return thread


def render_context_block(thread: dict[str, Any]) -> str:
    """Render the thread as a [INVESTIGATION THREAD] context block to
    prepend before the LLM call. Returns empty string when the thread
    has no current entity (so the chat path can no-op cleanly)."""
    if not thread or not isinstance(thread, dict):
        return ""
    entity = thread.get("current_canonical") or thread.get("current_entity") or ""
    if not entity:
        return ""

    lines = ["[INVESTIGATION THREAD — R-F734 cross-turn continuity]"]
    lines.append(f"Current focus: {entity}")
    etype = thread.get("entity_type") or "unknown"
    if etype and etype != "unknown":
        lines.append(f"Type: {etype}")
    last_tool = thread.get("last_tool") or ""
    if last_tool:
        lines.append(f"Last tool: {last_tool}")
    facts = thread.get("facts_so_far") or []
    if facts:
        lines.append(f"Findings so far ({len(facts)}, most recent first):")
        for f in facts[:8]:
            lines.append(f"  - {f[:200]}")
    open_q = thread.get("open_questions") or []
    if open_q:
        lines.append(f"Open questions:")
        for q in open_q[:5]:
            lines.append(f"  - {q[:200]}")
    lines.append(
        "Carry this context forward — do NOT re-fetch from scratch unless "
        "the user is clearly asking about a different entity."
    )
    return "\n".join(lines)


def clear(session: dict | None) -> None:
    """Wipe the investigation_thread (e.g. /purgecases or operator
    reset). In-place mutation."""
    if isinstance(session, dict):
        session["investigation_thread"] = _empty_thread()

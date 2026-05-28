"""
ARIA Feedback Store — capture user reactions on ARIA's WhatsApp replies
and tie them back to the original Q&A so we have ground-truth training
signal for evaluation, regression catching, and prompt evolution.

Why this exists
═══════════════
Until now ARIA had no way to know whether her answers were any good. The
self-quiz, mastery report, and self-improve loop all run in a closed loop
without any external truth signal. The team in WhatsApp obviously *knows*
when ARIA gives a wrong or hallucinated answer — they just had no way to
tell her.

A WhatsApp reaction emoji is the cheapest possible signal: one tap, no
extra UI, fits the existing chat-first workflow. This module turns those
taps into structured records that future evaluation, judge calls, and
prompt evolution work can build on.

How it works
════════════
    1. When ARIA sends a reply via the WA listener, the listener POSTs
       a snapshot to /api/aria/feedback/snapshot containing the message
       key (chat_id + msg_id) + the user's question + ARIA's answer.
       Stored in Redis with a 7-day TTL.

    2. When a user reacts to one of those messages with an emoji, the
       Baileys reaction handler POSTs the reaction here. We look up the
       snapshot, attach the sentiment + emoji + reactor name, persist
       the full feedback record, and (if negative) fire diagnose_failure
       so ARIA learns from the disagreement.

    3. /feedback WA commands let the team browse recent feedback,
       filter to the negative ones, and pick candidates for the golden
       evaluation set (the next phase).

What we DON'T do here
═════════════════════
- No automatic prompt evolution from feedback (deliberate — premature)
- No analytics dashboard (the /feedback command is enough for now)
- No LLM-as-judge (separate phase, will use this data as ground truth)
"""
from __future__ import annotations

import logging
import time
from typing import Any

from . import redis_store as rs

logger = logging.getLogger("aria.feedback")

# Snapshot key: ARIA reply Q&A pair, written by the WA listener after sending.
# Looked up by reaction handler. 7-day TTL — reactions arriving later than
# that are stored as dangling sentiment without context (still useful for
# trend tracking).
SNAPSHOT_KEY_PREFIX = "crucix:aria:feedback:snapshot:"
SNAPSHOT_TTL = 7 * 86400

# Feedback record key: the full record after a reaction lands. Persistent.
FEEDBACK_KEY_PREFIX = "crucix:aria:feedback:record:"
FEEDBACK_INDEX_KEY = "crucix:aria:feedback:index"
FEEDBACK_TTL = 90 * 86400  # 90 days — long enough for golden-set curation

# Emoji → sentiment mapping. Anything outside this map is recorded as
# "neutral" so we don't lose the signal but don't over-interpret either.
POSITIVE_EMOJI = {"👍", "❤️", "🎯", "💯", "✅", "🔥", "👌", "🙌", "⭐", "👏"}
NEGATIVE_EMOJI = {"👎", "❌", "😠", "🤬", "💩", "🚫", "⛔", "🙅"}
UNCERTAIN_EMOJI = {"🤔", "❓", "😕", "🤷", "🤨"}


def classify_emoji(emoji: str) -> str:
    """Map a reaction emoji to a sentiment bucket. Anything unknown → neutral."""
    if not emoji:
        return "neutral"
    # Strip variation selectors so "👍" and "👍\ufe0f" both match
    e = emoji.strip().replace("\ufe0f", "")
    for ref in POSITIVE_EMOJI:
        if ref.replace("\ufe0f", "") == e:
            return "positive"
    for ref in NEGATIVE_EMOJI:
        if ref.replace("\ufe0f", "") == e:
            return "negative"
    for ref in UNCERTAIN_EMOJI:
        if ref.replace("\ufe0f", "") == e:
            return "uncertain"
    # R-F996 — wire to brain
    from .engine_wiring import wire_success
    wire_success(
        module="feedback",
        summary="Classify Emoji",
        source_id="feedback:R-F996",
    )

    return "neutral"


# ── Snapshot capture ───────────────────────────────────────────────────────

def _snap_key(chat_id: str, msg_id: str) -> str:
    return f"{SNAPSHOT_KEY_PREFIX}{chat_id}:{msg_id}"


async def snapshot_reply(
    chat_id: str,
    msg_id: str,
    question: str,
    answer: str,
    *,
    user: str = "",
    group_name: str = "",
    metadata: dict | None = None,
    trace_id: str = "",
) -> dict:
    """Persist the Q→A pair so a later reaction can recover the context.

    Called by the WA listener immediately after sock.sendMessage() returns
    a key. Cheap to call — one Redis SET with TTL — and bounded by the
    7-day TTL so it can't grow without limit.

    trace_id (optional) ties this snapshot to the trace_stream record so
    a future reaction can attach itself to the trace, completing the
    feedback → trace → cost/verification join.
    """
    if not chat_id or not msg_id:
        return {"ok": False, "reason": "missing key"}
    snap = {
        "chat_id": chat_id,
        "msg_id": msg_id,
        "question": (question or "")[:4000],
        "answer": (answer or "")[:6000],
        "user": (user or "")[:120],
        "group_name": (group_name or "")[:120],
        "metadata": metadata or {},
        "trace_id": trace_id or "",
        "ts": time.time(),
    }
    try:
        await rs.set_json(_snap_key(chat_id, msg_id), snap, ex=SNAPSHOT_TTL)
        return {"ok": True}
    except Exception as e:
        logger.warning("snapshot_reply failed: %s", e)
        return {"ok": False, "reason": str(e)}


async def get_snapshot(chat_id: str, msg_id: str) -> dict | None:
    try:
        return await rs.get_json(_snap_key(chat_id, msg_id))
    except Exception:
        return None


# ── Feedback recording ─────────────────────────────────────────────────────

def _fb_key(feedback_id: str) -> str:
    return f"{FEEDBACK_KEY_PREFIX}{feedback_id}"


async def record_feedback(
    chat_id: str,
    msg_id: str,
    emoji: str,
    *,
    reactor: str = "",
    reactor_jid: str = "",
) -> dict:
    """Record a WhatsApp reaction as ARIA feedback. Looks up the snapshot
    if available, attaches sentiment, persists, and (on negative) fires
    diagnose_failure so the self-improve loop sees the disagreement.

    Returns the persisted record so the caller can confirm.
    """
    if not chat_id or not msg_id:
        return {"ok": False, "reason": "missing key"}

    sentiment = classify_emoji(emoji)
    snap = await get_snapshot(chat_id, msg_id)

    feedback_id = f"fb_{int(time.time() * 1000)}_{msg_id[-8:] if len(msg_id) >= 8 else msg_id}"
    record = {
        "id": feedback_id,
        "chat_id": chat_id,
        "msg_id": msg_id,
        "emoji": emoji or "",
        "sentiment": sentiment,
        "reactor": (reactor or "")[:120],
        "reactor_jid": (reactor_jid or "")[:200],
        "ts": time.time(),
        # Snapshot may be missing if the reaction is older than 7 days.
        # We still record the sentiment — useful for trend tracking even
        # without context.
        "has_context": snap is not None,
        "question": (snap or {}).get("question", ""),
        "answer": (snap or {}).get("answer", ""),
        "original_user": (snap or {}).get("user", ""),
        "group_name": (snap or {}).get("group_name", ""),
        "snapshot_metadata": (snap or {}).get("metadata", {}),
        "trace_id": (snap or {}).get("trace_id", ""),
    }

    try:
        await rs.set_json(_fb_key(feedback_id), record, ex=FEEDBACK_TTL)
        # Maintain a lightweight index for /feedback list queries
        index = await rs.get_json(FEEDBACK_INDEX_KEY) or []
        index.insert(0, {
            "id": feedback_id,
            "ts": record["ts"],
            "sentiment": sentiment,
            "emoji": emoji or "",
            "reactor": record["reactor"],
            "has_context": record["has_context"],
            "question_preview": (record["question"] or "")[:120],
        })
        index = index[:500]
        await rs.set_json(FEEDBACK_INDEX_KEY, index, ex=FEEDBACK_TTL)
    except Exception as e:
        logger.warning("record_feedback persist failed: %s", e)
        return {"ok": False, "reason": str(e), "record": record}

    logger.info(
        "Feedback recorded: %s sentiment=%s emoji=%s reactor=%s context=%s",
        feedback_id, sentiment, emoji, record["reactor"], record["has_context"],
    )

    # If the snapshot carried a trace_id, attach this feedback record to
    # the trace so /trace shows the user reaction inline. Fire-and-forget.
    trace_id = (snap or {}).get("trace_id") or ""
    if trace_id:
        try:
            from . import trace_stream
            import asyncio as _aio
            _aio.create_task(trace_stream.attach_feedback(trace_id, record))
        except Exception as e:
            logger.debug("attach_feedback dispatch failed: %s", e)

    # Fire self-diagnostic on negative feedback so the improvement loop
    # picks it up the same way it picks up runtime errors. Fire-and-forget;
    # don't block the WA reaction handler.
    if sentiment == "negative":
        try:
            from . import self_improve
            err_summary = (
                f"User '{record['reactor']}' rated ARIA's reply negatively "
                f"({emoji}). "
                f"Question: {(record['question'] or '(no snapshot)')[:300]}. "
                f"Answer was: {(record['answer'] or '(no snapshot)')[:400]}"
            )
            import asyncio as _aio
            _aio.create_task(self_improve.diagnose_failure(
                "user_negative_feedback",
                err_summary,
                {
                    "feedback_id": feedback_id,
                    "chat_id": chat_id,
                    "msg_id": msg_id,
                    "reactor": record["reactor"],
                    "has_context": record["has_context"],
                },
            ))
        except Exception as e:
            logger.debug("diagnose_failure dispatch failed: %s", e)

    return {"ok": True, "record": record}


async def get_feedback(feedback_id: str) -> dict | None:
    try:
        return await rs.get_json(_fb_key(feedback_id))
    except Exception:
        return None


async def list_feedback(
    limit: int = 30,
    sentiment_filter: str | None = None,
) -> list[dict]:
    """List recent feedback from the index. Index entries are summaries —
    callers wanting full records should call get_feedback(id)."""
    try:
        index = await rs.get_json(FEEDBACK_INDEX_KEY) or []
        if sentiment_filter:
            index = [e for e in index if e.get("sentiment") == sentiment_filter]
        return index[: max(1, min(limit, 500))]
    except Exception:
        return []


async def get_feedback_stats() -> dict:
    """Aggregate feedback counts for the /feedback summary command."""
    try:
        index = await rs.get_json(FEEDBACK_INDEX_KEY) or []
        by_sentiment: dict[str, int] = {}
        recent_24h = 0
        cutoff = time.time() - 86400
        for e in index:
            s = e.get("sentiment", "neutral")
            by_sentiment[s] = by_sentiment.get(s, 0) + 1
            if e.get("ts", 0) >= cutoff:
                recent_24h += 1
        total = len(index)
        positive = by_sentiment.get("positive", 0)
        negative = by_sentiment.get("negative", 0)
        # Rough quality score: positives / (positives + negatives), ignoring
        # neutral/uncertain. Returns None if there's no clear signal yet.
        decisive = positive + negative
        quality_score = round(positive / decisive, 3) if decisive > 0 else None
        return {
            "total": total,
            "by_sentiment": by_sentiment,
            "recent_24h": recent_24h,
            "quality_score": quality_score,
        }
    except Exception as e:
        return {"error": str(e)}

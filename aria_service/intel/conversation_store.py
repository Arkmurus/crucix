"""
Conversation Store — per-user conversation history for the ARIA chat UI.

Redis schema:
  crucix:aria:conversations:{userId}  — sorted set (score=timestamp, member=session_id)
  crucix:aria:conv:meta:{sessionId}   — hash: title, createdAt, updatedAt, messageCount, userId
  crucix:aria:session:{sessionId}     — existing session storage (messages array)
"""
from __future__ import annotations

import logging
import time

from . import redis_store as rs

logger = logging.getLogger("aria.conversation_store")

_CONV_KEY = "crucix:aria:conversations:{user_id}"
_META_KEY = "crucix:aria:conv:meta:{session_id}"
_SESSION_KEY = "crucix:aria:session:{session_id}"


async def create_conversation(
    user_id: str,
    session_id: str,
    first_message: str = "",
) -> dict:
    """Register a new conversation for a user. Auto-titles from first message."""
    now = time.time()
    title = (first_message.strip()[:60] + "...") if len(first_message.strip()) > 60 else first_message.strip()
    title = title or "New conversation"

    meta = {
        "title": title,
        "userId": user_id,
        "createdAt": str(now),
        "updatedAt": str(now),
        "messageCount": "1",
    }

    await rs.zadd(_CONV_KEY.format(user_id=user_id), now, session_id)
    await rs.hset(_META_KEY.format(session_id=session_id), meta)

    return {
        "session_id": session_id,
        "title": title,
        "userId": user_id,
        "createdAt": now,
        "updatedAt": now,
        "messageCount": 1,
    }


async def touch_conversation(session_id: str, user_id: str = "") -> None:
    """Update timestamp + increment message count after each turn."""
    now = time.time()
    meta_key = _META_KEY.format(session_id=session_id)
    existing = await rs.hgetall(meta_key)
    if not existing:
        # Conversation doesn't exist yet — create it
        if user_id:
            await create_conversation(user_id, session_id, "")
        return

    count = int(existing.get("messageCount", "0")) + 1
    await rs.hset(meta_key, {
        "updatedAt": str(now),
        "messageCount": str(count),
    })

    # Update score in the sorted set so it bubbles to top
    uid = existing.get("userId") or user_id
    if uid:
        await rs.zadd(_CONV_KEY.format(user_id=uid), now, session_id)


async def list_conversations(
    user_id: str,
    offset: int = 0,
    limit: int = 30,
) -> list[dict]:
    """Return conversations for a user, newest first, with metadata."""
    key = _CONV_KEY.format(user_id=user_id)
    session_ids = await rs.zrevrange(key, offset, offset + limit - 1)

    conversations = []
    for sid in session_ids:
        meta = await rs.hgetall(_META_KEY.format(session_id=sid))
        if meta:
            conversations.append({
                "session_id": sid,
                "title": meta.get("title", "Untitled"),
                "createdAt": float(meta.get("createdAt", 0)),
                "updatedAt": float(meta.get("updatedAt", 0)),
                "messageCount": int(meta.get("messageCount", 0)),
            })
        else:
            # Orphaned entry — still include it with minimal info
            conversations.append({
                "session_id": sid,
                "title": "Untitled",
                "createdAt": 0,
                "updatedAt": 0,
                "messageCount": 0,
            })

    return conversations


async def get_conversation(session_id: str, user_id: str | None = None) -> dict | None:
    """Load a conversation: metadata + full message history.

    R-F606: when ``user_id`` is passed, the conversation is only returned if the
    stored ``userId`` on the meta hash matches. This is the load-bearing
    ownership check — without it, any authenticated user can read any other
    user's chat by passing their session_id. Callers from authenticated route
    handlers MUST pass user_id; pass None only for trusted internal paths
    (e.g. backfill / admin export).
    """
    meta = await rs.hgetall(_META_KEY.format(session_id=session_id))
    if not meta:
        return None
    if user_id is not None and (meta.get("userId") or "") != user_id:
        return None

    session_data = await rs.get_json(_SESSION_KEY.format(session_id=session_id))
    messages = (session_data or {}).get("messages", []) if session_data else []

    # R-F996 — wire to brain
    from .engine_wiring import wire_success
    wire_success(
        module="conversation_store",
        summary="Get Conversation",
        source_id="conversation_store:R-F996",
    )

    return {
        "session_id": session_id,
        "title": meta.get("title", "Untitled"),
        "createdAt": float(meta.get("createdAt", 0)),
        "updatedAt": float(meta.get("updatedAt", 0)),
        "messageCount": int(meta.get("messageCount", 0)),
        "messages": messages,
    }


async def delete_conversation(user_id: str, session_id: str) -> bool:
    """Remove a conversation from the index, metadata, and session data.

    R-F606: previously deleted meta+session keys unconditionally and only
    zrem'd from the caller's index — so passing someone else's session_id
    with your own user_id silently destroyed their conversation. Now verifies
    ownership against meta.userId before touching anything; mismatched or
    missing meta → no-op, returns False.
    """
    meta = await rs.hgetall(_META_KEY.format(session_id=session_id))
    if not meta or (meta.get("userId") or "") != user_id:
        return False
    await rs.zrem(_CONV_KEY.format(user_id=user_id), session_id)
    await rs.delete(_META_KEY.format(session_id=session_id))
    await rs.delete(_SESSION_KEY.format(session_id=session_id))
    return True


async def rename_conversation(session_id: str, title: str, user_id: str | None = None) -> bool:
    """Update the title of a conversation.

    R-F606: ownership check via user_id. With user_id=None the function still
    works for back-compat with any internal admin call, but the route layer
    must pass user_id from the authenticated session.
    """
    meta_key = _META_KEY.format(session_id=session_id)
    existing = await rs.hgetall(meta_key)
    if not existing:
        return False
    if user_id is not None and (existing.get("userId") or "") != user_id:
        return False
    await rs.hset(meta_key, {"title": title.strip()[:100]})
    return True

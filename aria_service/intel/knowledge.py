"""
Knowledge Base — persistent verified facts, queries, and learnings.
Ported from lib/aria/knowledge.mjs.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from . import redis_store as rs

logger = logging.getLogger("aria.intel.knowledge")

KEY = "crucix:aria:knowledge"
MAX_FACTS = 500
MAX_QUERIES = 500
MAX_LEARNINGS = 200

_cache: dict[str, list] | None = None


async def _load() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    data = await rs.get_json(KEY)
    if data and "facts" in data:
        _cache = data
    else:
        _cache = {"facts": [], "queries": [], "learnings": [], "version": 1}
    return _cache


async def _save() -> None:
    if _cache:
        await rs.set_json(KEY, _cache)


# ── Public API ───────────────────────────────────────────────────────────────

async def init() -> None:
    await _load()
    logger.info(f"Knowledge base loaded: {len((_cache or {}).get('facts', []))} facts")


async def store_fact(topic: str, content: str, source: str = "user", confidence: str = "CONFIRMED") -> None:
    db = await _load()
    now = datetime.now(timezone.utc).isoformat()

    # Dedup by topic
    for f in db["facts"]:
        if f["topic"].lower() == topic.lower():
            f["content"] = content
            f["source"] = source
            f["confidence"] = confidence
            f["updatedAt"] = now
            f["accessCount"] = f.get("accessCount", 0) + 1
            await _save()
            return

    db["facts"].insert(0, {
        "id": str(uuid.uuid4())[:8],
        "topic": topic,
        "content": content,
        "source": source,
        "confidence": confidence,
        "createdAt": now,
        "updatedAt": now,
        "accessCount": 0,
    })
    if len(db["facts"]) > MAX_FACTS:
        db["facts"] = db["facts"][:MAX_FACTS]
    await _save()


async def record_query(query: str, summary: str, market: str = "", category: str = "") -> None:
    db = await _load()
    db["queries"].insert(0, {
        "id": str(uuid.uuid4())[:8],
        "query": query,
        "summary": summary,
        "market": market,
        "category": category,
        "createdAt": datetime.now(timezone.utc).isoformat(),
    })
    if len(db["queries"]) > MAX_QUERIES:
        db["queries"] = db["queries"][:MAX_QUERIES]
    await _save()


async def store_learning(correction: str, context: str = "") -> None:
    db = await _load()
    db["learnings"].insert(0, {
        "id": str(uuid.uuid4())[:8],
        "correction": correction,
        "context": context,
        "createdAt": datetime.now(timezone.utc).isoformat(),
    })
    if len(db["learnings"]) > MAX_LEARNINGS:
        db["learnings"] = db["learnings"][:MAX_LEARNINGS]
    await _save()


def search_knowledge(query: str) -> str:
    """Synchronous search for prompt injection. Returns formatted string."""
    if not _cache:
        return ""
    words = [w.lower() for w in query.split() if len(w) > 2]
    if not words:
        return ""

    scored: list[tuple[float, dict]] = []
    for f in _cache["facts"]:
        score = 0
        text = f"{f['topic']} {f['content']}".lower()
        for w in words:
            if w in text:
                score += 3
        score += min(f.get("accessCount", 0), 5)
        if score > 0:
            scored.append((score, f))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:15]
    if not top:
        return ""

    lines = ["\n[ARIA KNOWLEDGE BASE — verified facts]"]
    for _, f in top:
        lines.append(f"- [{f['confidence']}] {f['topic']}: {f['content'][:200]}")
    return "\n".join(lines)


def auto_extract_facts(user_query: str, aria_response: str) -> None:
    """Auto-mine [CONFIRMED] and [PROBABLE] tagged facts from ARIA responses."""
    if not _cache:
        return
    patterns = [
        (r"\[CONFIRMED\]\s*(.+?)(?:\n|$)", "CONFIRMED"),
        (r"\[PROBABLE\]\s*(.+?)(?:\n|$)", "PROBABLE"),
    ]
    import asyncio
    loop = asyncio.get_event_loop()
    for pat, conf in patterns:
        for m in re.finditer(pat, aria_response):
            text = m.group(1).strip()[:300]
            if len(text) > 20:
                # Fire-and-forget in the background
                topic = text[:60].rstrip(".")
                loop.create_task(store_fact(topic, text, "aria_auto", conf))


async def get_stats() -> dict:
    db = await _load()
    return {
        "totalFacts": len(db["facts"]),
        "totalQueries": len(db["queries"]),
        "totalLearnings": len(db["learnings"]),
    }

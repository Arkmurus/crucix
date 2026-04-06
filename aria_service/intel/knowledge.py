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
MAX_FACTS = 30000
MAX_QUERIES = 20000
MAX_LEARNINGS = 10000

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
    facts = (_cache or {}).get("facts", [])
    logger.info(f"Knowledge base loaded: {len(facts)} facts")
    # Build semantic index
    try:
        from .semantic_search import rebuild_index_from_knowledge
        rebuild_index_from_knowledge(facts)
    except Exception as e:
        logger.warning("Semantic index build failed: %s", e)


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
    # Index for semantic search
    try:
        from .semantic_search import index_fact
        index_fact(db["facts"][0]["id"], f"{topic} {content}", {"confidence": confidence})
    except Exception:
        pass


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


async def auto_extract_facts(user_query: str, aria_response: str) -> None:
    """Auto-mine [CONFIRMED] and [PROBABLE] tagged facts from ARIA responses."""
    if not _cache:
        return
    patterns = [
        (r"\[CONFIRMED\]\s*(.+?)(?:\n|$)", "CONFIRMED"),
        (r"\[PROBABLE\]\s*(.+?)(?:\n|$)", "PROBABLE"),
    ]
    import asyncio
    for pat, conf in patterns:
        for m in re.finditer(pat, aria_response):
            text = m.group(1).strip()[:300]
            if len(text) > 20:
                topic = text[:60].rstrip(".")
                asyncio.create_task(store_fact(topic, text, "aria_auto", conf))


async def consolidate_facts() -> dict:
    """Merge near-duplicate facts and prune stale ones."""
    from datetime import datetime, timezone
    db = await _load()
    facts = db.get("facts", [])
    if not facts:
        return {"merged": 0, "pruned": 0, "total_before": 0, "total_after": 0}

    total_before = len(facts)
    now = datetime.now(timezone.utc)

    # ── 1. Merge near-duplicate facts (same topic, case-insensitive) ─────
    merged = 0
    seen: dict[str, int] = {}  # topic_lower → index of best fact
    to_remove: set[int] = set()

    for i, f in enumerate(facts):
        key = f["topic"].strip().lower()
        if key in seen:
            # Keep the one with highest confidence rank / access_count
            existing_idx = seen[key]
            existing = facts[existing_idx]
            # Compare: prefer higher accessCount, then more recent update
            e_score = existing.get("accessCount", 0)
            f_score = f.get("accessCount", 0)
            if f_score > e_score:
                # Current fact is better — remove the existing one
                to_remove.add(existing_idx)
                seen[key] = i
            else:
                to_remove.add(i)
            merged += 1
        else:
            seen[key] = i

    # ── 2. Prune stale facts (>90 days old, accessCount < 2) ────────────
    pruned = 0
    ninety_days_ago = now.timestamp() - 90 * 86400
    for i, f in enumerate(facts):
        if i in to_remove:
            continue
        created = f.get("createdAt", "")
        if not created:
            continue
        try:
            created_ts = datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
        except (ValueError, TypeError):
            continue
        if created_ts < ninety_days_ago and f.get("accessCount", 0) < 2:
            to_remove.add(i)
            pruned += 1

    # ── 3. Rebuild facts list ────────────────────────────────────────────
    db["facts"] = [f for i, f in enumerate(facts) if i not in to_remove]
    await _save()

    total_after = len(db["facts"])
    logger.info("Knowledge consolidation: merged %d, pruned %d, %d → %d facts",
                merged, pruned, total_before, total_after)
    return {
        "merged": merged,
        "pruned": pruned,
        "total_before": total_before,
        "total_after": total_after,
    }


async def get_stats() -> dict:
    db = await _load()
    return {
        "totalFacts": len(db["facts"]),
        "totalQueries": len(db["queries"]),
        "totalLearnings": len(db["learnings"]),
    }

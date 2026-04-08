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
    # Semantic index build is INTENTIONALLY deferred — rebuild_index_from_knowledge
    # encodes every fact through sentence-transformers (~200-700ms per fact, sync
    # C call that doesn't yield to the event loop). For ~500 facts that's 100-350s
    # of blocking, which prevents uvicorn from binding and causes fly health checks
    # to fail. Past incident 2026-04-08.
    #
    # Spawn it as a background task so the server can bind first. Search calls
    # before the index is ready will fall through to the TF-IDF / Jaccard
    # fallback in semantic_search, which is degraded but functional.
    #
    # Can be disabled entirely with ARIA_SEMANTIC_INDEX_BUILD=0 — useful during
    # interactive testing. Even though encode() runs in a thread executor, it
    # holds the GIL in chunks, which starves the chat handler enough that
    # liveness probes time out. Past incident 2026-04-08 (round 2): user
    # couldn't get a reply for 'Aria, are you online?' because the startup
    # index build was hammering CPU continuously for 60+ seconds.
    import os as _os
    if (_os.getenv("ARIA_SEMANTIC_INDEX_BUILD", "1") or "1").lower() in ("0", "false", "no"):
        logger.info("Semantic index build SKIPPED via ARIA_SEMANTIC_INDEX_BUILD=0 — search will use TF-IDF/Jaccard fallback")
        return
    try:
        import asyncio as _aio
        async def _build_index_bg():
            await _aio.sleep(10)  # Give the server time to bind first
            try:
                from .semantic_search import rebuild_index_from_knowledge
                # Run in a thread executor so the encode loop doesn't starve
                # the event loop. encode() is sync C; the executor lets the
                # main loop keep handling requests while it works.
                loop = _aio.get_running_loop()
                count = await loop.run_in_executor(None, rebuild_index_from_knowledge, facts)
                logger.info("Semantic index built in background: %d facts indexed", count)
            except Exception as e:
                logger.warning("Background semantic index build failed: %s", e)
        _aio.create_task(_build_index_bg())
    except Exception as e:
        logger.warning("Could not schedule semantic index build: %s", e)


# ── Contradiction detection ──────────────────────────────────────────────────
# When a new fact arrives, we look for existing facts on the same topic that
# might disagree. Caught contradictions are flagged on BOTH facts so ARIA's
# context layer can surface "I previously thought X, but now I'm seeing Y" —
# the foundation of metacognitive self-correction.

_NEGATION_RE = re.compile(
    r"\b(not|no longer|never|denies|denied|withdrew|cancelled|cancelled|"
    r"reversed|stopped|halted|terminated|suspended|abandoned|dropped|"
    r"refuted|disputed|false|incorrect|wrong)\b",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"\b\d[\d,\.]*\b")
_CONFIDENCE_RANK = {"CONFIRMED": 4, "PROBABLE": 3, "ASSESSED": 2, "UNCERTAIN": 1, "SPECULATIVE": 0}

def _detect_contradictions(topic: str, content: str, existing_facts: list[dict]) -> list[dict]:
    """Find existing facts that may contradict the new statement.

    Heuristic — flags potential conflicts when:
      1. Same topic, but new content has negation that old doesn't (or vice-versa)
      2. Same topic, but the numeric values disagree (e.g. £200m vs £350m)
      3. Same topic, opposing keywords (won/lost, signed/cancelled, alive/dead)
    """
    if not existing_facts:
        return []

    new_lower = content.lower()
    new_negated = bool(_NEGATION_RE.search(new_lower))
    new_numbers = set(_NUMBER_RE.findall(new_lower))
    topic_lower = topic.strip().lower()

    OPPOSING = [
        ({"won", "awarded", "signed", "delivered"}, {"lost", "cancelled", "withdrew", "rejected", "terminated"}),
        ({"alive", "active", "in office", "serving"}, {"dead", "deceased", "removed", "dismissed", "retired"}),
        ({"increased", "rising", "growing"}, {"decreased", "falling", "declining", "cut"}),
        ({"sanctioned", "embargoed", "blocked"}, {"removed", "delisted", "exempt", "cleared"}),
    ]
    contradictions: list[dict] = []
    for f in existing_facts:
        if f.get("topic", "").strip().lower() != topic_lower:
            continue
        old_text = (f.get("content") or "").lower()
        old_negated = bool(_NEGATION_RE.search(old_text))
        conflict_reason = None

        if new_negated != old_negated:
            conflict_reason = "negation mismatch"
        else:
            old_numbers = set(_NUMBER_RE.findall(old_text))
            if new_numbers and old_numbers and not (new_numbers & old_numbers):
                conflict_reason = f"numeric mismatch (was {sorted(old_numbers)[:3]}, now {sorted(new_numbers)[:3]})"
            else:
                for set_a, set_b in OPPOSING:
                    has_a_old = any(w in old_text for w in set_a)
                    has_b_old = any(w in old_text for w in set_b)
                    has_a_new = any(w in new_lower for w in set_a)
                    has_b_new = any(w in new_lower for w in set_b)
                    if (has_a_old and has_b_new) or (has_b_old and has_a_new):
                        conflict_reason = "opposing terms"
                        break

        if conflict_reason:
            contradictions.append({
                "fact_id": f.get("id"),
                "old_content": (f.get("content") or "")[:200],
                "old_confidence": f.get("confidence"),
                "old_source": f.get("source"),
                "old_updated_at": f.get("updatedAt"),
                "reason": conflict_reason,
            })
    return contradictions


async def store_fact(topic: str, content: str, source: str = "user",
                     confidence: str = "CONFIRMED") -> dict:
    """Store a fact, detecting contradictions and merging duplicates.

    Returns a dict with action taken: ``{action: "created"|"updated"|"superseded",
    fact_id, contradictions: [...]}``
    """
    db = await _load()
    now = datetime.now(timezone.utc).isoformat()

    # ── Detect contradictions BEFORE storing ──────────────────────────────
    contradictions = _detect_contradictions(topic, content, db["facts"])

    # ── Dedup by topic ────────────────────────────────────────────────────
    for f in db["facts"]:
        if f["topic"].lower() == topic.lower():
            # Don't blindly overwrite — check confidence ranks
            old_rank = _CONFIDENCE_RANK.get(f.get("confidence", "ASSESSED"), 2)
            new_rank = _CONFIDENCE_RANK.get(confidence, 2)

            if contradictions:
                # New info contradicts old — keep both, mark the older one as superseded
                # but only if the new fact is at least as confident as the old.
                if new_rank >= old_rank:
                    f["superseded_by"] = None  # placeholder; set after we know the new id
                    f["superseded_at"] = now
                    f["history"] = (f.get("history") or [])[-9:] + [{
                        "content": f["content"],
                        "confidence": f["confidence"],
                        "source": f["source"],
                        "replaced_at": now,
                    }]
                    f["content"] = content
                    f["source"] = source
                    f["confidence"] = confidence
                    f["updatedAt"] = now
                    f["accessCount"] = f.get("accessCount", 0) + 1
                    f["contradictions_detected"] = (f.get("contradictions_detected", 0) or 0) + len(contradictions)
                    await _save()
                    return {"action": "superseded", "fact_id": f["id"], "contradictions": contradictions}
                else:
                    # New fact is weaker — keep old, log the conflict but don't overwrite
                    f["pending_conflicts"] = (f.get("pending_conflicts") or [])[-4:] + [{
                        "content": content[:200], "confidence": confidence,
                        "source": source, "noted_at": now,
                    }]
                    await _save()
                    return {"action": "conflict_logged", "fact_id": f["id"], "contradictions": contradictions}

            # No conflict — refresh in place
            f["content"] = content
            f["source"] = source
            f["confidence"] = confidence
            f["updatedAt"] = now
            f["accessCount"] = f.get("accessCount", 0) + 1
            await _save()
            return {"action": "updated", "fact_id": f["id"], "contradictions": []}

    # ── Brand-new fact ────────────────────────────────────────────────────
    new_id = str(uuid.uuid4())[:8]
    db["facts"].insert(0, {
        "id": new_id,
        "topic": topic,
        "content": content,
        "source": source,
        "confidence": confidence,
        "createdAt": now,
        "updatedAt": now,
        "accessCount": 0,
        "contradictions_detected": len(contradictions),
    })
    if len(db["facts"]) > MAX_FACTS:
        db["facts"] = db["facts"][:MAX_FACTS]
    await _save()
    # Index for semantic search — runs sync model.encode() under the hood,
    # which holds the GIL. Must be off the event loop or it will block the
    # /teach reply for hundreds of milliseconds (longer if first call cold-
    # loads the model).
    try:
        from .semantic_search import index_fact
        import asyncio as _aio
        await _aio.to_thread(
            index_fact,
            db["facts"][0]["id"],
            f"{topic} {content}",
            {"confidence": confidence},
        )
    except Exception:
        pass
    # Index into the persistent RAG store as well so retrieval can find it
    try:
        from . import rag_store
        await rag_store.ingest_fact(
            fact_id=new_id,
            topic=topic,
            content=content,
            confidence=confidence,
            source=source,
        )
    except Exception:
        pass
    return {"action": "created", "fact_id": new_id, "contradictions": contradictions}


async def get_contradictions(limit: int = 50) -> list[dict]:
    """Return facts that have detected contradictions or version history.

    This is what powers ARIA's self-aware "I used to think X, now Y" reasoning.
    """
    db = await _load()
    result = []
    for f in db.get("facts", []):
        if f.get("contradictions_detected", 0) > 0 or f.get("history") or f.get("pending_conflicts"):
            result.append({
                "id": f.get("id"),
                "topic": f.get("topic"),
                "current_content": f.get("content"),
                "current_confidence": f.get("confidence"),
                "current_source": f.get("source"),
                "updated_at": f.get("updatedAt"),
                "history": f.get("history") or [],
                "pending_conflicts": f.get("pending_conflicts") or [],
                "contradictions_count": f.get("contradictions_detected", 0),
            })
        if len(result) >= limit:
            break
    return result


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

"""Memory-tier diagnostics (M7 from the audit).

ARIA has five parallel memory systems that can drift from each other:
  1. knowledge.py      — manual /teach + auto-extract facts (JSON)
  2. mem0              — conversation summariser (notebook)
  3. neural_memory     — concept neurons + associations
  4. rag_store         — chromadb document chunks (proprietary corpus)
  5. conflict_tracker  — conflict claims derived from signals

Full reconciliation across all tiers is a larger effort — for now this
module provides VISIBILITY so ops can see drift before it becomes a
user-visible contradiction. Two public functions:

  await snapshot_tiers()
      Returns {tier: {count, ok, note}} — cheap sampling, safe to call
      from /api/aria/memory/tiers and startup log.

  await diagnose_topic(topic)
      Returns per-tier hits for a given topic, so ops can investigate
      "why does ARIA say X about Ghana in one session and Y in another".

Both functions are DIAGNOSTIC only — they never mutate state.
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import logging

logger = logging.getLogger("aria.memory_diagnostics")


async def _safe(label: str, coro):
    try:
        return {"ok": True, **(await coro)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200], "tier": label}


async def _knowledge_snapshot() -> dict:
    from . import knowledge as _kn
    db = await _kn._load()
    facts = db.get("facts", []) or []
    return {"tier": "knowledge", "count": len(facts)}


async def _mem0_snapshot() -> dict:
    try:
        from . import mem0 as _m
    except ImportError:
        return {"tier": "mem0", "count": 0, "note": "module not present"}
    # R-F1068: mem0.get_stats() doesn't exist — use is_enabled() as a probe
    return {"tier": "mem0", "enabled": _m.is_enabled() if hasattr(_m, "is_enabled") else False}


async def _neural_snapshot() -> dict:
    try:
        from . import neural_memory as _n
    except ImportError:
        return {"tier": "neural", "count": 0, "note": "module not present"}
    if hasattr(_n, "get_stats"):
        try:
            stats = await _n.get_stats()
            return {"tier": "neural", **stats}
        except Exception as e:
            return {"tier": "neural", "count": -1, "error": str(e)[:200]}
    return {"tier": "neural", "count": -1, "note": "no get_stats()"}


async def _rag_snapshot() -> dict:
    from . import rag_store as _r
    if hasattr(_r, "get_stats"):
        stats = await _r.get_stats()
        return {"tier": "rag", **stats}
    return {"tier": "rag", "count": -1, "note": "no get_stats()"}


async def snapshot_tiers() -> dict:
    """Return a lightweight count snapshot across all memory tiers."""
    out = {}
    for label, fn in (
        ("knowledge", _knowledge_snapshot),
        ("mem0", _mem0_snapshot),
        ("neural", _neural_snapshot),
        ("rag", _rag_snapshot),
    ):
        try:
            out[label] = await fn()
        except Exception as e:
            out[label] = {"tier": label, "ok": False, "error": str(e)[:200]}
    return out


async def diagnose_topic(topic: str) -> dict:
    """Query each memory tier for the given topic and return what each says.

    Operators use this when ARIA appears to contradict itself across
    sessions — e.g. 'who is the current Ghana defence minister' returning
    different names in different conversations. Walking the tiers shows
    which one is out of date.
    """
    if not topic:
        return {"error": "topic required"}
    hits: dict = {"topic": topic, "tiers": {}}

    # knowledge.py
    try:
        from . import knowledge as _kn
        # R-F4141 (C-171) — 2.28s O(corpus) scan; must not run on the loop.
        import asyncio as _aio
        ctx = await _aio.to_thread(_kn.search_knowledge, topic)
        hits["tiers"]["knowledge"] = {"ok": True, "preview": (ctx or "")[:800]}
    except Exception as e:
        hits["tiers"]["knowledge"] = {"ok": False, "error": str(e)[:200]}

    # rag_store
    try:
        from . import rag_store as _r
        ctx = await _r.get_rag_context(topic, max_chars=1500)
        hits["tiers"]["rag"] = {"ok": True, "preview": (ctx or "")[:800]}
    except Exception as e:
        hits["tiers"]["rag"] = {"ok": False, "error": str(e)[:200]}

    # mem0
    try:
        from . import mem0 as _m
        if hasattr(_m, "retrieve_for_query"):
            # R-F3489 — a tier diagnostic must see the tier. Without an explicit system
            # scope it would report ok=True with an empty preview: a false clean.
            ctx = _m.retrieve_for_query(topic, system_scope=True)
            hits["tiers"]["mem0"] = {"ok": True, "preview": (ctx or "")[:800]}
        else:
            hits["tiers"]["mem0"] = {"ok": False, "error": "no retrieve_for_query"}
    except Exception as e:
        hits["tiers"]["mem0"] = {"ok": False, "error": str(e)[:200]}

    # neural
    try:
        from . import neural_memory as _n
        if hasattr(_n, "get_neural_context"):
            ctx = await _n.get_neural_context(topic)
            hits["tiers"]["neural"] = {"ok": True, "preview": (ctx or "")[:800]}
        else:
            hits["tiers"]["neural"] = {"ok": False, "error": "no get_neural_context"}
    except Exception as e:
        hits["tiers"]["neural"] = {"ok": False, "error": str(e)[:200]}

    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="memory_diagnostics",
        summary="Diagnose Topic",
        source_id="memory_diagnostics:R-F996",
    )

    return hits

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.

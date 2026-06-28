"""provenance_chain — derivation lineage for knowledge facts with
cascade-invalidate (R-F75, 2026-05-09).

Why this module exists
──────────────────────
Per peer review: 'When Source A is ingested and produces Fact 1, which
then contributes to Knowledge Summary 2, which influences Assessment
Conclusion 3, and Source A is subsequently found to be a coordinated
disinformation outlet — there is currently no mechanism to trace and
invalidate Facts 1, Summary 2, and Conclusion 3. The provenance chain
— where each derived fact carries the lineage of its derivation — is
the knowledge base equivalent of a database foreign key with cascade
delete.'

What this module adds (without breaking what's there)
─────────────────────────────────────────────────────
Knowledge facts already carry `source` (a free-text URL or label).
This module adds an EDGE-RECORD storage layer alongside knowledge.json:

  /data/aria_provenance.json
    { "edges": [
        {"src_id": "...", "dst_id": "...", "edge_type": "derives",
         "weight": 0.8, "ts": "...", "via_module": "..."},
        ...
      ],
      "sources": {
        "<source_id>": {"url": ..., "first_seen": ..., "trust": 1.0,
                          "invalidated": false, "invalidated_reason": null,
                          "invalidated_at": null}
      }
    }

Edges form a DAG: source → fact → derived_fact → conclusion.

When a source is invalidated:
  invalidate_source(source_id, reason) walks the DAG depth-first and
  marks every transitively-derived node with `invalidated_via=<source_id>`.
  The knowledge layer can then filter invalidated facts out of search,
  RAG retrieval, and chat context — without deleting them (audit
  preservation).

Public API
──────────
    register_source(source_id, url=None, trust=1.0, **kw) -> dict
    register_derivation(src_id, dst_id, edge_type='derives',
                         weight=0.8, via_module=None) -> dict
    invalidate_source(source_id, reason) -> dict
        Marks the source + all transitively-derived nodes.
    is_invalidated(node_id) -> bool
    get_lineage(node_id, max_depth=10) -> list[dict]
        Walk backwards: what sources/facts led to this node?
    summary() -> dict
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("aria.provenance_chain")

_DATA_DIR = Path(os.getenv("ARIA_DATA_DIR", "/data"))
_PROVENANCE_FILE = _DATA_DIR / "aria_provenance.json"
_REDIS_MIRROR_KEY = "crucix:aria:provenance:snapshot"

# In-memory mirror for fast access. Dict-based for O(1) lookups.
_state: dict[str, Any] = {
    "edges":   [],   # list[dict] — DAG edges
    "sources": {},   # source_id -> source metadata
}
_state_loaded = False
_state_lock = asyncio.Lock()


async def _load() -> None:
    """Load from disk on first access. Best-effort — failures result in
    a fresh empty state (consistent with the pattern in knowledge.py /
    intel_ledger.py)."""
    global _state_loaded
    if _state_loaded:
        return
    async with _state_lock:
        if _state_loaded:
            return
        try:
            if _PROVENANCE_FILE.exists():
                with _PROVENANCE_FILE.open("r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    _state["edges"] = loaded.get("edges", []) or []
                    _state["sources"] = loaded.get("sources", {}) or {}
                logger.info(
                    "provenance: loaded %d edges, %d sources from disk",
                    len(_state["edges"]),
                    len(_state["sources"]),
                )
        except Exception as e:
            logger.warning("provenance: load failed (starting fresh): %s", e)
            _state["edges"] = []
            _state["sources"] = {}
        _state_loaded = True


async def _persist() -> None:
    """Write current state to disk + Redis mirror. Best-effort."""
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _PROVENANCE_FILE.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(_state, f, ensure_ascii=False, separators=(",", ":"))
        tmp.replace(_PROVENANCE_FILE)
    except Exception as e:
        logger.warning("provenance: disk persist failed: %s", e)
    try:
        from . import redis_store as rs
        await rs.set_json(_REDIS_MIRROR_KEY, _state, ex=90 * 24 * 3600)
    except Exception as e:
        logger.debug("provenance: redis mirror failed (non-fatal): %s", e)


async def register_source(
    source_id: str,
    *,
    url: str | None = None,
    trust: float = 1.0,
    source_type: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Register a source in the provenance graph. Idempotent — calling
    twice with the same id updates only the metadata fields."""
    await _load()
    if not source_id:
        raise ValueError("source_id required")
    existing = _state["sources"].get(source_id, {})
    record = {
        "url":                 url or existing.get("url"),
        "trust":               float(trust),
        "source_type":         source_type or existing.get("source_type"),
        "notes":               notes or existing.get("notes"),
        "first_seen":          existing.get("first_seen") or datetime.now(timezone.utc).isoformat(),
        "invalidated":         existing.get("invalidated", False),
        "invalidated_reason":  existing.get("invalidated_reason"),
        "invalidated_at":      existing.get("invalidated_at"),
    }
    _state["sources"][source_id] = record
    await _persist()
    return {"source_id": source_id, **record}


async def register_derivation(
    src_id: str,
    dst_id: str,
    *,
    edge_type: str = "derives",
    weight: float = 0.8,
    via_module: str | None = None,
) -> dict[str, Any]:
    """Record an edge: src_id contributed to dst_id."""
    await _load()
    if not src_id or not dst_id:
        raise ValueError("src_id and dst_id both required")
    if src_id == dst_id:
        raise ValueError("self-loop edge rejected")
    edge = {
        "src_id":     src_id,
        "dst_id":     dst_id,
        "edge_type":  edge_type,
        "weight":     float(weight),
        "via_module": via_module,
        "ts":         datetime.now(timezone.utc).isoformat(),
    }
    _state["edges"].append(edge)
    await _persist()
    return edge


async def invalidate_source(source_id: str, reason: str) -> dict[str, Any]:
    """Mark a source invalid + cascade through the DAG.

    Returns: { invalidated_source, cascade_count, cascade_node_ids }
    The actual knowledge facts are not deleted — they remain queryable
    with the `invalidated` flag set. Knowledge consumers should filter
    invalidated nodes out of search and chat context.
    """
    await _load()
    src = _state["sources"].get(source_id)
    if not src:
        # Auto-register so the invalidation persists
        await register_source(source_id, url=None, trust=0.0)
        src = _state["sources"][source_id]
    src["invalidated"] = True
    src["invalidated_reason"] = reason
    src["invalidated_at"] = datetime.now(timezone.utc).isoformat()

    # Build forward-edge index: src_id -> [dst_id]
    forward: dict[str, list[str]] = {}
    for e in _state["edges"]:
        forward.setdefault(e["src_id"], []).append(e["dst_id"])

    # Walk DAG forward, marking transitive descendants
    visited: set[str] = set()
    queue: list[str] = [source_id]
    cascade: list[str] = []
    while queue:
        cur = queue.pop(0)
        if cur in visited:
            continue
        visited.add(cur)
        for child in forward.get(cur, []):
            if child not in visited:
                cascade.append(child)
                queue.append(child)

    # Mark each cascaded node as invalidated_via this source. We store
    # the marker on the source-record because the dst nodes are typically
    # knowledge facts in knowledge.json — touching that file is the
    # consumer's job. Our marker is the canonical answer to
    # is_invalidated(node_id).
    src["cascade_invalidated"] = cascade
    await _persist()
    return {
        "invalidated_source": source_id,
        "reason":             reason,
        "cascade_count":      len(cascade),
        "cascade_node_ids":   cascade,
    }


async def is_invalidated(node_id: str) -> bool:
    """True if the node is itself invalidated OR has been cascade-
    invalidated by an upstream source."""
    await _load()
    src = _state["sources"].get(node_id)
    if src and src.get("invalidated"):
        return True
    # Walk every invalidated source and check its cascade list
    for sid, srec in _state["sources"].items():
        if srec.get("invalidated") and node_id in (srec.get("cascade_invalidated") or []):
            return True
    return False


async def get_lineage(node_id: str, *, max_depth: int = 10) -> list[dict[str, Any]]:
    """Walk backwards from node_id to find every source / fact that
    contributed. Returns the path edges."""
    await _load()
    # Build reverse-edge index
    reverse: dict[str, list[dict[str, Any]]] = {}
    for e in _state["edges"]:
        reverse.setdefault(e["dst_id"], []).append(e)

    # BFS upward
    path: list[dict[str, Any]] = []
    queue: list[tuple[str, int]] = [(node_id, 0)]
    visited: set[str] = set()
    while queue:
        cur, depth = queue.pop(0)
        if cur in visited or depth >= max_depth:
            continue
        visited.add(cur)
        for edge in reverse.get(cur, []):
            path.append(edge)
            queue.append((edge["src_id"], depth + 1))
    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="provenance_chain",
        summary="Get Lineage",
        source_id="provenance_chain:R-F996",
    )

    return path


async def stats() -> dict[str, Any]:
    """Health/visibility for /api/aria/provenance/stats."""
    await _load()
    return {
        "total_sources":       len(_state["sources"]),
        "total_edges":         len(_state["edges"]),
        "invalidated_sources": sum(
            1 for s in _state["sources"].values() if s.get("invalidated")
        ),
        "data_file":           str(_PROVENANCE_FILE),
    }


def summary() -> dict[str, Any]:
    return {
        "module":     "provenance_chain",
        "purpose":    "DAG of source → fact → conclusion with cascade-invalidate",
        "data_file":  str(_PROVENANCE_FILE),
    }

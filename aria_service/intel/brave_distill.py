"""R-F2318 — Brave → SearXNG distillation corpus.

Captures, for every user-facing search where Brave participated, the query and the
per-backend ranked result URLs. Brave is the TEACHER; SearXNG + the free stack are
the STUDENT. This is the training / evaluation signal ARIA uses to make SearXNG match
Brave's behaviour and eventually drop the paid dependency (§6 mirror-Claude /
reduce-dependency). We can only capture Brave's INPUT→OUTPUT behaviour (which sources
it surfaces, in what order) — its internal ranking algorithm is proprietary and not
exposed — but that behaviour is exactly what a re-ranker / query-expansion student
needs to imitate.

Storage: append-only daily JSONL shards under a volume-backed directory (files only,
§6/§7 — no paid persistence, no eviction). Best-effort: never blocks the search hot
path, never raises. The label "brave" lives ONLY in this internal corpus — it is never
shown to the user (R-F2318 branding rule: all search is presented as ARIA's).
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger("aria.brave_distill")

# Volume-backed on fly (set ARIA_BRAVE_DISTILL_DIR=/data/brave_distill); repo-relative
# in local dev. Kept off the state_store (rs) on purpose — high-volume search captures
# must not add write load to the single-writer sqlite ceiling (R-F2277/R-F2290).
_CORPUS_DIR = Path(
    os.getenv("ARIA_BRAVE_DISTILL_DIR")
    or os.path.join(os.getenv("ARIA_DATA_DIR", "data"), "brave_distill")
)
_MAX_URLS_PER_BACKEND = 20
_MAX_QUERY_LEN = 300
_ENABLED = (os.getenv("ARIA_BRAVE_DISTILL_ENABLED", "1") or "1").lower() in ("1", "true", "yes")


def _url_of(r) -> str | None:
    u = getattr(r, "url", None)
    if u is None and isinstance(r, dict):
        u = r.get("url")
    return (u or "").strip() or None


def capture(query: str, language: str, backend_names: list, raw_results: list) -> None:
    """Append one (query → per-backend ranked URLs) record. Only writes when Brave
    actually contributed results. Best-effort; swallows every error."""
    if not _ENABLED:
        return
    try:
        by_backend: dict[str, list[str]] = {}
        for i, name in enumerate(backend_names):
            if i >= len(raw_results):
                break
            batch = raw_results[i]
            if isinstance(batch, BaseException) or not batch:
                continue
            urls: list[str] = []
            for r in batch[:_MAX_URLS_PER_BACKEND]:
                u = _url_of(r)
                if u:
                    urls.append(u)
            if urls:
                by_backend[name] = urls
        if "brave" not in by_backend:
            return  # only capture the teacher signal when Brave actually contributed
        _CORPUS_DIR.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": time.time(),
            "query": (query or "")[:_MAX_QUERY_LEN],
            "language": language,
            "backends": by_backend,
        }
        # Daily shard keeps individual files bounded + easy to iterate for training.
        shard = _CORPUS_DIR / (time.strftime("%Y-%m-%d") + ".jsonl")
        with shard.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        # §21a — wire the successful teacher-signal capture to the brain (lightweight
        # record_signal metric path, fire-and-forget; not the heavy absorb tier).
        try:
            from .engine_wiring import wire_success
            wire_success(
                module="brave_distill",
                summary=f"captured Brave teacher signal ({len(by_backend)} backends)",
                source_id="brave_distill:capture",
            )
        except Exception:
            pass
    except Exception as e:  # never let capture break a search
        logger.debug("brave_distill capture failed (non-fatal): %s", e)
        try:
            from .engine_wiring import wire_failure
            wire_failure(
                module="brave_distill",
                detail=f"distillation capture failed: {e}",
                gap_type="engine_failure",
                source="brave_distill:capture",
            )
        except Exception:
            pass


def stats() -> dict:
    """Lightweight corpus summary for the ops/proprioception surface (§25)."""
    try:
        if not _CORPUS_DIR.exists():
            return {"records": 0, "shards": 0, "dir": str(_CORPUS_DIR)}
        shards = sorted(_CORPUS_DIR.glob("*.jsonl"))
        total = 0
        for s in shards:
            try:
                with s.open("r", encoding="utf-8") as f:
                    total += sum(1 for _ in f)
            except Exception:
                continue
        return {"records": total, "shards": len(shards),
                "dir": str(_CORPUS_DIR), "latest": shards[-1].name if shards else None}
    except Exception as e:
        return {"error": str(e), "dir": str(_CORPUS_DIR)}


# R-F2541: removed the R-F2318 import-time _wf_shutdown("module shutdown") block — it
# fired a FALSE engine_failure gap on every import (variant of the R-F2538 sweep that
# used an aliased wire_failure import, so the sweep regex missed it); do not re-add.

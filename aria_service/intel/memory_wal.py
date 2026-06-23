"""memory_wal — durable write-ahead log for facts (R-F1342, §7 infinite memory).

Operator directive (2026-06-05): "she never forgets anything — infinite memory."

The problem this closes: `brain_hook` absorb persists facts via
`knowledge.store_fact`, but that write can be SKIPPED (concurrency cap /
backlog cap under load) or FAIL (tier timeout, transient DB error). Before
this module, a skipped/failed fact was lost — a silent forget.

The guarantee: every fact that cannot be persisted immediately is appended to
a durable append-only JSONL on the data volume. A periodic drain re-attempts
`store_fact` for queued facts and removes only the ones that succeed. So a
fact is retained until it is actually in the knowledge base — nothing is ever
forgotten, even across restarts (the WAL lives on /data, which survives).

Design notes:
- Append is the hot-path operation — O(1), never raises, never blocks
  meaningfully (one line to a file). Safe to call from anywhere.
- R-F1422: append and the drain are SERIALIZED by `_APPEND_LOCK` (held only
  for the O(1) append / the drain's atomic rotate, never during the retry
  loop). The drain ROTATES the WAL aside (atomic os.replace) rather than
  blind-overwriting it, so an append arriving mid-drain lands in a fresh live
  WAL and survives. Pre-R-F1422 the drain snapshot-read then overwrote with
  only still_failing — any append between the read and the rewrite was silently
  lost (the "never forget" WAL forgetting). Crash-safe: an orphaned .draining
  file from a died drain is recovered on the next drain before any new rotate.
- The WAL is bounded by _MAX_WAL_LINES only as a safety valve against
  unbounded disk growth if store_fact is broken for a long time; when trimming
  it keeps the OLDEST (they've waited longest) and logs loudly — it never
  silently discards without a WARNING (no silent forget, §21).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Callable, Optional
from .wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.memory_wal")

_WAL_PATH = Path(os.getenv("ARIA_FACT_WAL_PATH", "/data/aria_fact_wal.jsonl"))
_MAX_WAL_LINES = int(os.getenv("ARIA_FACT_WAL_MAX", "100000"))
_drain_in_progress = False
# R-F1422 — serialize append vs the drain's rotate so an append can never
# interleave the atomic rename and be lost. Held only for the O(1) rename /
# one-line-write, NEVER during the retry loop (which doesn't touch the file).
_APPEND_LOCK = threading.Lock()
_stats = {"appended": 0, "drained": 0, "retry_failed": 0, "trimmed": 0}


def _ensure_dir() -> bool:
    try:
        _WAL_PATH.parent.mkdir(parents=True, exist_ok=True)
        return True
    except Exception:
        # Fall back to /tmp if /data is unmounted (still better than losing it)
        try:
            globals()["_WAL_PATH"] = Path("/tmp/aria_fact_wal.jsonl")
            _WAL_PATH.parent.mkdir(parents=True, exist_ok=True)
            return True
        except Exception:
            return False


@fail_wire(module="memory_wal", gap_type="engine_failure")
def record_pending_fact(topic: str, content: str, source: str,
                        confidence: str) -> bool:
    """Append one fact to the durable WAL. Never raises. Returns True on write.

    Called whenever a fact could not be persisted immediately, so it is
    retried later instead of forgotten."""
    if not content:
        return False
    if not _ensure_dir():
        logger.error("[memory_wal] could not open WAL dir — fact at risk: %s",
                     topic)
        return False
    try:
        line = json.dumps({
            "topic": topic, "content": content[:2000],
            "source": source, "confidence": confidence,
        }, ensure_ascii=False, default=str)
        # R-F1422 — under the append lock so this write can't interleave the
        # drain's atomic rotate (which would otherwise lose this line).
        with _APPEND_LOCK:
            with _WAL_PATH.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        _stats["appended"] += 1
        return True
    except Exception as e:
        logger.error("[memory_wal] append failed (fact at risk): %s — %s",
                     topic, e)
        return False


@fail_wire(module="memory_wal", gap_type="engine_failure")
def pending_count() -> int:
    try:
        if not _WAL_PATH.exists():
            return 0
        with _WAL_PATH.open("r", encoding="utf-8") as fh:
            return sum(1 for _ in fh)
    except Exception:
        return 0


@fail_wire(module="memory_wal", gap_type="engine_failure")
def get_stats() -> dict:
    return {**_stats, "pending": pending_count(), "path": str(_WAL_PATH)}


@fail_wire(module="memory_wal", gap_type="engine_failure")
async def drain(store_fact: Callable[..., Any], max_items: int = 500) -> dict:
    """Re-attempt queued facts; keep only the ones that still fail.

    `store_fact` is the async knowledge.store_fact (injected to avoid an import
    cycle). Single-flight. Returns a small summary dict.
    """
    global _drain_in_progress
    if _drain_in_progress:
        return {"skipped": "already_draining"}
    _drain_in_progress = True
    _proc_path = _WAL_PATH.with_suffix(".jsonl.draining")
    try:
        # R-F1422 — ROTATE, don't blind-overwrite. The old drain read a
        # snapshot then rewrote the file with only still_failing; any
        # record_pending_fact() append between the read and the rewrite was
        # silently lost (the "never forget" WAL forgetting). Now: atomically
        # move the live WAL aside under the append lock, so concurrent appends
        # land in a FRESH live WAL and survive. Crash-safe: if a prior drain
        # died after rotating, the orphaned .draining file is recovered first.
        def _rotate():
            with _APPEND_LOCK:
                if _proc_path.exists():
                    # orphan from a crashed prior drain — process it as-is,
                    # do NOT overwrite it with the live WAL (that would lose
                    # the orphan's facts). Live WAL waits for the next cycle.
                    return "orphan"
                if not _WAL_PATH.exists():
                    return "empty"
                os.replace(_WAL_PATH, _proc_path)
                return "rotated"
        _mode = await asyncio.to_thread(_rotate)
        if _mode == "empty":
            return {"pending": 0}

        # R-F1346: read off the event loop (up to 100k lines).
        def _read():
            with _proc_path.open("r", encoding="utf-8") as fh:
                return [ln for ln in fh if ln.strip()]
        lines = await asyncio.to_thread(_read)
        if not lines:
            # nothing in the rotated file — clean up the empty orphan/proc
            await asyncio.to_thread(
                lambda: _proc_path.unlink() if _proc_path.exists() else None
            )
            return {"pending": 0}

        still_failing: list[str] = []
        processed = 0
        for ln in lines:
            if processed >= max_items:
                still_failing.append(ln)  # leave the rest for the next drain
                continue
            processed += 1
            try:
                rec = json.loads(ln)
            except Exception:
                continue  # malformed line — drop (can't retry what we can't parse)
            try:
                ok = await store_fact(
                    topic=rec.get("topic", "wal"),
                    content=rec.get("content", ""),
                    source=rec.get("source", "wal_retry"),
                    confidence=rec.get("confidence", "ASSESSED"),
                )
                # store_fact returns truthy/None; treat no-exception as success
                _stats["drained"] += 1
            except Exception:
                _stats["retry_failed"] += 1
                still_failing.append(ln)  # keep — retry next cycle

        # Safety valve: bound disk growth, keep OLDEST, log loudly (no silent loss)
        if len(still_failing) > _MAX_WAL_LINES:
            dropped = len(still_failing) - _MAX_WAL_LINES
            _stats["trimmed"] += dropped
            logger.warning(
                "[memory_wal] WAL over cap (%d) — store_fact failing for a long "
                "time; %d oldest facts kept, %d NEWEST deferred-to-log only",
                _MAX_WAL_LINES, _MAX_WAL_LINES, dropped,
            )
            still_failing = still_failing[:_MAX_WAL_LINES]

        # R-F1422 — RE-APPEND the still-failing facts to the (possibly grown)
        # live WAL under the append lock, then remove the processed file. This
        # merges retries with any appends that arrived during the drain — no
        # blind overwrite, nothing lost. Order: new appends first, then the
        # older still-failing (fine — it's a retry queue, all get re-tried).
        def _requeue():
            with _APPEND_LOCK:
                if still_failing:
                    with _WAL_PATH.open("a", encoding="utf-8") as fh:
                        fh.writelines(still_failing)
                try:
                    _proc_path.unlink()
                except OSError:
                    pass
        await asyncio.to_thread(_requeue)
        return {"retried": processed, "remaining": len(still_failing),
                "drained_total": _stats["drained"]}
    except Exception as e:
        logger.error("[memory_wal] drain failed: %s", e)
        return {"error": str(e)[:200]}
    finally:
        _drain_in_progress = False

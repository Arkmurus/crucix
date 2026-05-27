"""ARIA Compliance Watch — evidentiary capture store (R-F933, slice 1).

Slice 1 of the Compliance Watch pipeline (CAPTURE → analyse → feedback → private
delivery). This is the CAPTURE layer: every WhatsApp group message the listener
forwards (brain/signal, signal_type=whatsapp_group_message) is persisted here
append-only, fully attributed (group, sender, message-time, verbatim text), and
HASH-CHAINED so the record is tamper-evident — an analyst (or, ultimately, a
regulator/court) can verify that no entry was altered or removed after capture.
Nothing is ever deleted (CLAUDE.md §7 — infinite memory, no eviction).

Design (compliance-grade, no room for mistakes):
  - append-only Redis/file list (newest at index 0 via lpush),
  - monotonic `seq` via an atomic counter (ordering survives a hash fork),
  - each record carries prev_hash + hash = sha256(prev_hash + canonical(record)),
    so verify_chain() can prove integrity end-to-end,
  - verbatim text is stored (capped) so every later finding can cite real
    evidence — the analysis/digest slices NEVER accuse without a quote.

The analysis + private-digest layers (later slices) READ from here via
get_captured(); they never re-fetch from the listener, so this store is the
single evidentiary source of truth. Best-effort + non-fatal: capture must never
break the live brain/signal path.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Optional

logger = logging.getLogger("aria.intel.compliance_watch")

_LOG_KEY = "crucix:aria:compliance_watch:log"      # lpush → newest at index 0
_SEQ_KEY = "crucix:aria:compliance_watch:seq"
_GENESIS = "0" * 64
_MAX_SCAN = 5000          # hard cap on records a single query/verify walks
_TEXT_CAP = 8000          # verbatim text cap per record


def _canonical(rec: dict) -> str:
    """Deterministic serialisation of the evidentiary fields (excludes the
    hash fields themselves) so the hash is reproducible for verification."""
    core = {k: rec.get(k) for k in
            ("seq", "group", "sender", "timestamp", "captured_at", "text", "channel")}
    return json.dumps(core, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _hash(prev_hash: str, rec: dict) -> str:
    return hashlib.sha256((prev_hash + _canonical(rec)).encode("utf-8")).hexdigest()


async def _head_hash() -> str:
    """Hash of the most-recent record, or genesis when the log is empty."""
    try:
        from . import redis_store as rs
        head = await rs.lrange(_LOG_KEY, 0, 0)
        if head:
            return (json.loads(head[0]) or {}).get("hash") or _GENESIS
    except Exception:
        pass
    return _GENESIS


async def capture_message(*, group: str, sender: str, text: str,
                          timestamp: str = "", channel: str = "whatsapp") -> dict:
    """Append one attributed, hash-chained message to the evidentiary store.

    Best-effort: never raises into the caller (the brain/signal path). Returns
    {captured, seq, hash} on success or {captured: False, error} otherwise."""
    try:
        from . import redis_store as rs
        prev = await _head_hash()
        seq = await rs.incr(_SEQ_KEY)
        rec: dict[str, Any] = {
            "seq": int(seq),
            "group": str(group or "")[:200],
            "sender": str(sender or "")[:200],
            "timestamp": str(timestamp or "")[:40],      # message time (from WA)
            "captured_at": round(time.time(), 3),         # server receipt time
            "text": str(text or "")[:_TEXT_CAP],
            "channel": str(channel or "whatsapp")[:40],
            "prev_hash": prev,
        }
        rec["hash"] = _hash(prev, rec)
        await rs.lpush(_LOG_KEY, json.dumps(rec, ensure_ascii=False))
        return {"captured": True, "seq": rec["seq"], "hash": rec["hash"]}
    except Exception as e:
        logger.warning("compliance_watch.capture_message failed (non-fatal): %s", e)
        return {"captured": False, "error": str(e)[:200]}


async def get_captured(*, since_epoch: Optional[float] = None,
                       group: Optional[str] = None, limit: int = 500) -> list[dict]:
    """Return captured messages newest-first, optionally filtered by group and/or
    server-receipt time (`captured_at` >= since_epoch). For the analysis layer."""
    out: list[dict] = []
    try:
        from . import redis_store as rs
        scan = min(_MAX_SCAN, max(1, limit * 4))
        rows = await rs.lrange(_LOG_KEY, 0, scan - 1)
        for raw in rows:
            try:
                rec = json.loads(raw)
            except Exception:
                continue
            if since_epoch is not None and float(rec.get("captured_at") or 0) < since_epoch:
                continue
            if group and rec.get("group") != group:
                continue
            out.append(rec)
            if len(out) >= limit:
                break
    except Exception as e:
        logger.warning("compliance_watch.get_captured failed: %s", e)
    return out


async def verify_chain(limit: int = 1000) -> dict:
    """Walk the recent chain newest→oldest, confirming each record's own hash
    AND its linkage to the next-older record. Returns
    {ok, checked, broken_at}. This is the tamper-evidence proof."""
    try:
        from . import redis_store as rs
        rows = await rs.lrange(_LOG_KEY, 0, max(1, min(_MAX_SCAN, limit)) - 1)
        checked = 0
        for i, raw in enumerate(rows):
            try:
                rec = json.loads(raw)
            except Exception:
                return {"ok": False, "checked": checked, "broken_at": "unparseable_record"}
            if _hash(rec.get("prev_hash", ""), rec) != rec.get("hash"):
                return {"ok": False, "checked": checked, "broken_at": rec.get("seq")}
            if i + 1 < len(rows):
                try:
                    older = json.loads(rows[i + 1])
                    if rec.get("prev_hash") != older.get("hash"):
                        return {"ok": False, "checked": checked, "broken_at": rec.get("seq")}
                except Exception:
                    return {"ok": False, "checked": checked, "broken_at": "unparseable_link"}
            checked += 1
        return {"ok": True, "checked": checked, "broken_at": None}
    except Exception as e:
        return {"ok": False, "checked": 0, "error": str(e)[:200]}


async def stats() -> dict:
    """Coverage stats for the capture store."""
    try:
        from . import redis_store as rs
        return {"total_captured": await rs.llen(_LOG_KEY)}
    except Exception:
        return {"total_captured": 0}

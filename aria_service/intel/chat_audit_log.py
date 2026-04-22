"""Chat audit trail — HMAC-signed, append-only log of ALL chat responses.

Extends the existing compliance-only audit_log.py to cover every output.
This is what makes ARIA a commercial product for regulated enterprises:
provable due diligence on every response.

Each entry records:
  - timestamp, session_id
  - user message hash (not content — privacy)
  - response hash + length
  - sources cited (URLs)
  - confidence tags used ([CONFIRMED], [PROBABLE], etc.)
  - mastery state at response time
  - verification status (grounded/ungrounded/no_tool)
  - operating mode

Redis keys:
  crucix:chat_audit:log           — append-only list (max 10,000, 90-day TTL)
  crucix:chat_audit:head_hash     — latest chain hash (tamper detection)
  crucix:chat_audit:by_session    — secondary index by session_id
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import time
from typing import Any

logger = logging.getLogger("aria.chat_audit")

_K_LOG = "crucix:chat_audit:log"
_K_HEAD = "crucix:chat_audit:head_hash"
_K_BY_SESSION = "crucix:chat_audit:by_session:{sid}"
_K_ENTRIES_24H = "crucix:chat_audit:entries_24h"
# Permanent audit trail — was 10k entries / 90d TTL before 2026-04-21.
# Compliance-grade audit logs must not self-delete; HMAC chain integrity
# also degrades if entries vanish from the tail.
_MAX_ENTRIES = 10_000_000
_TTL_DAYS = 36500  # 100 years

_SIGNING_KEY = (os.getenv("ARIA_AUDIT_SIGNING_KEY") or "dev-unsigned").encode()
_GENESIS_HASH = "0" * 64

# Confidence tag patterns
_CONFIDENCE_RE = re.compile(r"\[(CONFIRMED|PROBABLE|ASSESSED|UNCERTAIN|SPECULATIVE)"
                            r"[^\]]*\]", re.IGNORECASE)
# URL pattern
_URL_RE = re.compile(r"https?://[^\s\)\]\"'>]+")


def _extract_confidence_tags(text: str) -> list[str]:
    """Extract all confidence tags from response text."""
    return list(set(m.group(1).upper() for m in _CONFIDENCE_RE.finditer(text)))


def _extract_cited_urls(text: str) -> list[str]:
    """Extract cited URLs from response text."""
    urls = _URL_RE.findall(text)
    return list(set(urls))[:20]  # cap at 20


def _hash_text(text: str) -> str:
    """SHA-256 hash of text content."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _sign(body: dict, prev_hash: str) -> tuple[str, str]:
    """HMAC-sign an entry and compute chain hash."""
    payload = json.dumps(body, sort_keys=True, default=str)
    signature = hmac.new(_SIGNING_KEY, payload.encode(), hashlib.sha256).hexdigest()[:32]
    chain_hash = hashlib.sha256(f"{prev_hash}:{signature}".encode()).hexdigest()[:32]
    return signature, chain_hash


async def record_chat(
    *,
    session_id: str,
    user_message: str,
    response_text: str,
    mastery_overall: float = 0.0,
    mastery_weak_topics: list[str] | None = None,
    verification_status: str = "unknown",
    grounded_rate: float | None = None,
    operating_mode: str = "NORMAL",
    tool_context: dict | None = None,
) -> dict:
    """Record a chat response in the audit trail. Called after every
    aria_chat() or aria_chat_stream() response."""
    from . import redis_store as rs

    # Get previous chain hash
    prev_hash = await rs.get(_K_HEAD) or _GENESIS_HASH

    entry = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "session_id": session_id or "unknown",
        "user_message_hash": _hash_text(user_message or ""),
        "response_hash": _hash_text(response_text or ""),
        "response_length": len(response_text or ""),
        "confidence_tags": _extract_confidence_tags(response_text or ""),
        "sources_cited": _extract_cited_urls(response_text or ""),
        "sources_count": len(_extract_cited_urls(response_text or "")),
        "mastery_overall": round(mastery_overall, 3),
        "mastery_weak_topics": mastery_weak_topics or [],
        "verification_status": verification_status,
        "grounded_rate": round(grounded_rate, 3) if grounded_rate is not None else None,
        "operating_mode": operating_mode,
        "tools_used": list((tool_context or {}).get("tools_called", {}).keys())[:10],
    }

    # Sign and chain
    signature, chain_hash = _sign(entry, prev_hash)
    entry["signature"] = signature
    entry["chain_hash"] = chain_hash
    entry["prev_hash"] = prev_hash

    # Persist
    await rs.lpush(_K_LOG, json.dumps(entry, default=str))
    await rs.ltrim(_K_LOG, 0, _MAX_ENTRIES - 1)
    await rs.set(_K_HEAD, chain_hash)
    await rs.expire(_K_LOG, _TTL_DAYS * 86400)

    # Session index
    sid_key = _K_BY_SESSION.format(sid=session_id or "unknown")
    await rs.lpush(sid_key, json.dumps({"timestamp": entry["timestamp"],
                                         "response_hash": entry["response_hash"]},
                                        default=str))
    await rs.ltrim(sid_key, 0, 100)
    await rs.expire(sid_key, _TTL_DAYS * 86400)

    # Rolling 24h counter — autonomy_surface reads this as
    # `chat_turns_served`. The lifetime log retains entries forever, but
    # the dashboard needs a bounded window. 25h expire gives natural
    # decay without per-event timestamp bookkeeping.
    try:
        await rs.incr(_K_ENTRIES_24H)
        await rs.expire(_K_ENTRIES_24H, 90_000)
    except Exception as e:
        logger.debug("entries_24h counter incr failed: %s", e)

    return entry


async def get_recent(limit: int = 50) -> list[dict]:
    """Return recent audit entries."""
    from . import redis_store as rs
    raw = await rs.lrange(_K_LOG, 0, limit - 1)
    return [json.loads(r) for r in raw] if raw else []


async def get_stats() -> dict:
    """Aggregate audit stats."""
    from . import redis_store as rs
    total = await rs.llen(_K_LOG)
    head = await rs.get(_K_HEAD)
    entries_24h_raw = await rs.get(_K_ENTRIES_24H)
    try:
        entries_24h = int(entries_24h_raw) if entries_24h_raw is not None else 0
    except (TypeError, ValueError):
        entries_24h = 0
    return {
        "total_entries": total or 0,
        "entries_24h": entries_24h,
        "head_hash": head or _GENESIS_HASH,
        "ttl_days": _TTL_DAYS,
        "max_entries": _MAX_ENTRIES,
    }


async def verify_chain(sample: int = 100) -> dict:
    """Verify chain integrity on a sample of recent entries."""
    entries = await get_recent(sample)
    if not entries:
        return {"verified": True, "checked": 0, "breaks": []}
    breaks = []
    for i in range(len(entries) - 1):
        current = entries[i]
        prev = entries[i + 1]
        if current.get("prev_hash") != prev.get("chain_hash"):
            breaks.append({
                "index": i,
                "expected_prev": prev.get("chain_hash"),
                "actual_prev": current.get("prev_hash"),
            })
    return {
        "verified": len(breaks) == 0,
        "checked": len(entries),
        "breaks": breaks[:10],
    }

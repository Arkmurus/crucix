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
from .engine_wiring import wire_failure

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


def _truthy(val: str | None) -> bool:
    """Env-flag parse: 1/true/yes (case-insensitive) → True."""
    return (val or "").strip().lower() in ("1", "true", "yes")


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

    # R-F107 (2026-05-09): merge URLs cited in the response with the
    # RAG sources that were actually retrieved (passed via tool_context
    # by aria_engine). Previously sources_count counted ONLY URLs that
    # survived the LLM paraphrase — chronically 0 even when the
    # 8-layer context had 5+ retrieved passages. Now reflects real
    # retrieval provenance.
    inline_urls = _extract_cited_urls(response_text or "")
    retrieved_urls: list[str] = []
    if isinstance(tool_context, dict):
        retrieved = tool_context.get("retrieved_sources") or []
        if isinstance(retrieved, list):
            for r in retrieved:
                if isinstance(r, dict):
                    u = r.get("url") or r.get("source")
                    if u and u not in retrieved_urls:
                        retrieved_urls.append(u)
                elif isinstance(r, str) and r:
                    if r not in retrieved_urls:
                        retrieved_urls.append(r)
    # Union of inline + retrieved (dedupe)
    all_urls = list(dict.fromkeys(inline_urls + retrieved_urls))[:30]

    entry = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "session_id": session_id or "unknown",
        "user_message_hash": _hash_text(user_message or ""),
        "response_hash": _hash_text(response_text or ""),
        "response_length": len(response_text or ""),
        "confidence_tags": _extract_confidence_tags(response_text or ""),
        "sources_cited": all_urls,
        "sources_count": len(all_urls),
        "sources_inline_count":    len(inline_urls),     # in the response text
        "sources_retrieved_count": len(retrieved_urls),  # from RAG/knowledge layer
        "mastery_overall": round(mastery_overall, 3),
        "mastery_weak_topics": mastery_weak_topics or [],
        "verification_status": verification_status,
        "grounded_rate": round(grounded_rate, 3) if grounded_rate is not None else None,
        "operating_mode": operating_mode,
        "tools_used": list((tool_context or {}).get("tools_called", {}).keys())[:10],
    }

    # Sign and chain (over hash-only body — preserves compliance stance)
    signature, chain_hash = _sign(entry, prev_hash)
    entry["signature"] = signature
    entry["chain_hash"] = chain_hash
    entry["prev_hash"] = prev_hash

    # Optional raw-text capture for training-corpus collectors.
    # Default OFF — audit remains hash-only for privacy.
    # When ARIA_CHAT_TRAIN_CAPTURE_TEXT=1, append the captured user_message
    # and response to the entry AFTER signing so the HMAC body is unchanged
    # and `verify_chain` (which checks prev_hash→chain_hash linking only)
    # still passes. Collectors in `learning/training_export.py` read these
    # fields; they are absent when capture is disabled.
    #
    # R-F1563 (2026-06-14): capture-WITHOUT-redaction must no longer be the
    # default. The captured text held live PII (emails, phones, deal data,
    # secrets) at rest for ~100yr (_TTL_DAYS). Run it through a deterministic
    # LLM-free PII redaction pass BEFORE truncation/persistence so the stored
    # raw fields carry typed placeholders ([EMAIL]/[PHONE]/[CARD]/...), not
    # clear PII. Redaction defaults ON whenever capture is on; it can be
    # tuned OFF via ARIA_CHAT_TRAIN_CAPTURE_REDACT=0 for a trusted offline
    # corpus run, but the safe default leaks nothing.
    #
    # The chain hash is computed over the hash-only body (above), so adding
    # / redacting these fields does not affect chain integrity. The
    # user_message_hash / response_hash anchors still hash the ORIGINAL
    # exchange (lines ~123-124) for tamper detection of what was actually
    # said — only the at-rest *plaintext copy* is redacted.
    if _truthy(os.getenv("ARIA_CHAT_TRAIN_CAPTURE_TEXT")):
        raw_user = (user_message or "")[:4000]
        raw_resp = (response_text or "")[:20000]
        if os.getenv("ARIA_CHAT_TRAIN_CAPTURE_REDACT") is None or _truthy(
            os.getenv("ARIA_CHAT_TRAIN_CAPTURE_REDACT")
        ):
            from .pii_redaction import redact_pii
            raw_user = redact_pii(raw_user)
            raw_resp = redact_pii(raw_resp)
        entry["user_message"] = raw_user
        entry["response"] = raw_resp

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
    #
    # Subtle: the previous implementation called `expire()` on every incr.
    # That resets the TTL each time, so under continuous chat traffic the
    # key never expired and the counter became a lifetime tally instead
    # of a 24h rolling window. The dashboard's "24h" panel was reporting
    # entries-since-25h-of-inactivity. Fix: only set TTL when the counter
    # is first created (incr returned 1 from a missing key), so the
    # window genuinely rolls every 25 hours.
    try:
        new_val = await rs.incr(_K_ENTRIES_24H)
        if new_val == 1:
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
    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="chat_audit_log",
        summary="Verify Chain",
        source_id="chat_audit_log:R-F996",
    )

    return {
        "verified": len(breaks) == 0,
        "checked": len(entries),
        "breaks": breaks[:10],
    }

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.

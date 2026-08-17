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
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("aria.chat_audit")

_K_LOG = "crucix:chat_audit:log"
_K_HEAD = "crucix:chat_audit:head_hash"
_K_BY_SESSION = "crucix:chat_audit:by_session:{sid}"
_K_ENTRIES_24H = "crucix:chat_audit:entries_24h"   # legacy, retired by R-F4068
# R-F4068 (C-109) — the 24h window lives in the KEY, not in a TTL.
#
# `_K_ENTRIES_24H` was a plain counter whose 25h TTL was applied only when the
# increment returned 1. Measured live 2026-08-16 it held '758' with
# `expires_at = NULL`: no TTL, so it never expired, so the increment never
# returned 1 again, so the TTL could never be re-applied. The counter had become
# a lifetime tally that the brain page rendered as "Chat turns served (24h)"
# while the real figure was ~10 — and the defect repaired its own trigger, so it
# could never recover on its own.
#
# An hourly-bucketed hash removes the failure mode rather than patching the TTL
# rule: one atomic `hincrby` per turn (same cost as the old incr), and a read
# sums only the buckets inside the window. A lost TTL cannot corrupt it because
# no TTL is consulted — a bucket outside the window is simply never read again.
# Bounded by construction via `_HOURLY_BUCKETS_KEPT`.
_K_ENTRIES_HOURLY = "crucix:chat_audit:entries_hourly"
_HOURLY_BUCKETS_KEPT = 30   # ~25h of window + slack, pruned on write
# Permanent audit trail — was 10k entries / 90d TTL before 2026-04-21.
# Compliance-grade audit logs must not self-delete; HMAC chain integrity
# also degrades if entries vanish from the tail.
_MAX_ENTRIES = 10_000_000
_TTL_DAYS = 36500  # 100 years

_SIGNING_KEY = (os.getenv("ARIA_AUDIT_SIGNING_KEY") or "dev-unsigned").encode()
_GENESIS_HASH = "0" * 64


class ChatAuditChainUnreadable(RuntimeError):
    """R-F4100 (C-144) — the chat chain head could not be READ.

    Distinct from "the log is empty". Only one of those means genesis.
    """


async def _read_head_hash(*, strict: bool = False) -> str:
    """Read the current chain head, or genesis if the log is empty.

    R-F4100 (C-144) — A TRANSIENT READ FAILURE IS NOT GENESIS.

    THE DEFECT: `record_chat` read its head as

        prev_hash = await rs.get(_K_HEAD) or _GENESIS_HASH

    `redis_store.get` returns None on a store FAILURE (the documented
    None-on-error contract), so a blip answered "this log is empty" and the
    next entry was chained to genesis — SILENTLY FORKING the evidentiary chain
    and orphaning every prior entry.

    This is verbatim the defect R-F3711 fixed in the sibling `audit_log.py`;
    this module was never ported. Both halves were measured live on aria-intel
    2026-08-17, in the same 23-minute window:

        audit chain BROKEN: 16 link(s) failed over 1233 of 1233 entries
        state_store.get(...) timed out after 5s — ... Returning None.   (x26)

    `strict=True` raises so the WRITE path can refuse rather than fork. The
    read-only verifier stays lenient on purpose — it reports rather than
    extends, and a genesis fallback there is visible in its own output, where
    hardening it would make a store blip look like tampering.
    """
    from . import redis_store as rs
    try:
        h = await (rs.get_strict(_K_HEAD) if strict else rs.get(_K_HEAD))
    except Exception as exc:
        if strict:
            raise ChatAuditChainUnreadable(str(exc)) from exc
        return _GENESIS_HASH
    if h:
        return h
    # An ABSENT key is the genuine empty-log case and is legitimate — the very
    # first entry must be able to chain to genesis. Only a store FAILURE (the
    # branch above) is refused. Collapsing these two again is the whole defect.
    return _GENESIS_HASH

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

    # R-F4100 (C-144) — the WRITE path reads strictly. Chaining to genesis
    # because the store blipped would fork the evidentiary chain and orphan
    # every prior entry; refusing the write loses ONE record and keeps the
    # chain provable. Losing a chat-audit entry is bad. Silently invalidating
    # the whole trail is categorically worse, and it is what this used to do.
    try:
        prev_hash = await _read_head_hash(strict=True)
    except ChatAuditChainUnreadable as _cce:
        logger.error(
            "[R-F4100] chat audit head hash UNREADABLE (%s) — refusing to "
            "write session=%r rather than chain it to genesis and fork the "
            "chain", _cce, session_id,
        )
        try:  # §21a — a refused audit write must reach the brain, not just a log
            wire_failure(
                module="chat_audit_log",
                detail=(f"chat audit write refused: head hash unreadable "
                        f"({str(_cce)[:120]}) session={session_id}"),
                gap_type="data_integrity",
                source="chat_audit_log:record_chat:R-F4100",
            )
        except Exception:
            pass
        # Deliberately NOT shaped like an entry: aria_engine reads
        # `response_hash` off this dict to enqueue a reconcile, so a refusal
        # that carried one would queue work for a record that never existed.
        return {"ok": False, "recorded": False,
                "reason": "chat_audit_chain_unreadable"}

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

    # Rolling 24h window — autonomy_surface reads this as `chat_turns_served`.
    # R-F4068 (C-109): bucketed by UTC hour in a hash. See the note beside
    # `_K_ENTRIES_HOURLY` for why the previous TTL-based counter could not be
    # repaired in place — both of its historical forms (expire-every-incr and
    # expire-only-when-1) failed through the TTL, in opposite directions.
    try:
        await rs.hincrby(_K_ENTRIES_HOURLY, _hour_field(), 1)
        await _prune_hourly_buckets(rs)
        await _retire_legacy_counter(rs)
    except Exception as e:
        logger.debug("hourly audit bucket incr failed: %s", e)

    return entry


def _hour_field(dt: datetime | None = None) -> str:
    """UTC hour bucket id, e.g. `2026-08-16T17`. The window is this string."""
    d = dt or datetime.now(timezone.utc)
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H")


def _sum_recent_buckets(buckets: dict, hours: int = 24,
                        now: datetime | None = None) -> int:
    """Sum the buckets inside the window. Unparseable ids are skipped rather
    than counted — a field we cannot place in time is not evidence of a turn."""
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(hours=hours)
    total = 0
    for field, value in (buckets or {}).items():
        try:
            when = datetime.strptime(str(field), "%Y-%m-%dT%H").replace(
                tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        if when >= cutoff.replace(minute=0, second=0, microsecond=0):
            try:
                total += int(value)
            except (TypeError, ValueError):
                continue
    return total


async def _prune_hourly_buckets(rs) -> None:
    """Keep the bucket hash bounded. One field per hour would otherwise grow
    without limit — the same unbounded-growth class §28 records for the
    knowledge graph, just slower."""
    try:
        buckets = await rs.hgetall(_K_ENTRIES_HOURLY) or {}
        if len(buckets) <= _HOURLY_BUCKETS_KEPT:
            return
        for field in sorted(buckets)[: len(buckets) - _HOURLY_BUCKETS_KEPT]:
            await rs.hdel(_K_ENTRIES_HOURLY, field)
    except Exception as e:
        logger.debug("hourly bucket prune failed: %s", e)


# Retired once per process — a 758-valued orphan left in the store invites the
# next reader to "restore" it.
_legacy_counter_retired = False


async def _retire_legacy_counter(rs) -> None:
    global _legacy_counter_retired
    if _legacy_counter_retired:
        return
    _legacy_counter_retired = True
    try:
        await rs.delete(_K_ENTRIES_24H)
    except Exception as e:
        logger.debug("legacy entries_24h delete failed: %s", e)


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
    # R-F4068 (C-109) — summed from hourly buckets. The legacy
    # `_K_ENTRIES_24H` counter is deliberately NOT read: live it held 758 with
    # no TTL against a real 24h figure of ~10, and nothing about that value can
    # be salvaged because the window it was counting is unknown.
    try:
        entries_24h = _sum_recent_buckets(await rs.hgetall(_K_ENTRIES_HOURLY))
    except Exception:
        entries_24h = 0
    return {
        "total_entries": total or 0,
        "entries_24h": entries_24h,
        "head_hash": head or _GENESIS_HASH,
        "ttl_days": _TTL_DAYS,
        "max_entries": _MAX_ENTRIES,
    }


async def verify_chain(sample: int = 100) -> dict:
    """Verify chain integrity over the most recent `sample` entries.

    R-F4070 (C-111) — three faults, all the same family.

    1. An EMPTY log returned `{"verified": True, "checked": 0}`. An audit trail
       with nothing in it certified itself: the §1 "certified by an absence"
       shape, on the one surface whose whole job is to be un-fakeable.
    2. `verified` read as a whole-chain claim while the default depth is 100.
       Live 2026-08-16 the log held 1208 entries with 714 missing and a real
       break at index 409, so `?sample=100` answered `verified: true` and
       `?sample=500` answered `verified: false`. The damage began below the
       default.
    3. `wire_success` fired UNCONDITIONALLY before the return and
       `wire_failure` was imported but never called — so a detected break, the
       one event this module exists to detect, was dark (§21a).

    `verified` keeps its literal meaning (no break in the span examined).
    `complete` says whether the whole log was covered, and `verdict` is the
    field to read:

        intact        whole log checked, no breaks
        broken        a break was found
        partial_ok    no break in the span checked, but the log is longer
        unverifiable  nothing to check

    The default sample is deliberately still bounded — a dashboard poll must not
    walk the whole log — but a bounded check can no longer render as a clean
    bill of health. Missing entries need no separate detector: they break the
    prev_hash -> chain_hash linkage of their surviving neighbours, which is
    precisely the live break at 409.
    """
    from . import redis_store as rs
    from .engine_wiring import wire_success, wire_failure

    entries = await get_recent(sample)
    try:
        total_entries = int(await rs.llen(_K_LOG) or 0)
    except Exception:
        total_entries = len(entries)

    if not entries:
        result = {
            "verified": None,          # NOT True — nothing was examined
            "verdict": "unverifiable",
            "checked": 0,
            "total_entries": total_entries,
            "complete": False,
            "breaks": [],
        }
        wire_failure(
            module="chat_audit_log",
            detail=("chain verification found no entries to check — an empty "
                    "audit trail cannot be reported as verified"),
            gap_type="data_integrity",
            source="chat_audit_log:verify_chain:R-F4070",
        )
        return result

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

    complete = len(entries) >= total_entries
    if breaks:
        verdict = "broken"
    elif complete:
        verdict = "intact"
    else:
        verdict = "partial_ok"

    # R-F996 / R-F4070 — wire to brain on BOTH branches (§21a).
    if breaks:
        wire_failure(
            module="chat_audit_log",
            detail=(f"audit chain BROKEN: {len(breaks)} link(s) failed over "
                    f"{len(entries)} of {total_entries} entries; first at "
                    f"index {breaks[0]['index']}"),
            gap_type="data_integrity",
            source="chat_audit_log:verify_chain:R-F4070",
        )
    else:
        wire_success(
            module="chat_audit_log",
            summary=(f"Verify Chain: {verdict} "
                     f"({len(entries)}/{total_entries} entries)"),
            source_id="chat_audit_log:R-F996",
        )

    return {
        "verified": len(breaks) == 0,
        "verdict": verdict,
        "checked": len(entries),
        "total_entries": total_entries,
        "complete": complete,
        "breaks": breaks[:10],
    }

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.

"""ARIA Mistake Ledger — immutable record of every correction / miss / override.

Rule Zero says ARIA protects reputation. The autonomy doctrine says "no
mistakes = no REPEATED mistakes." This file is how she keeps that promise:
every time a mistake is surfaced — user correction, eval assertion fail,
user override of an ARIA recommendation, escalation she failed to trigger
— it gets written here with the root cause, the fix, and the signal that
surfaced it. Before running any similar task she reads her own ledger
first. That's the memory that turns autonomy into trust.

Not a substitute for
────────────────────
  - audit_log: that is regulator-grade, signed, for compliance decisions.
    Mistakes here are for ARIA's self-learning; they may reference an
    audit entry_hash but don't live there.
  - capability_gaps: that logs "I don't support X". This logs "I DID X
    and got it wrong" — distinct categories.
  - self_metrics: that logs performance over time. This logs the
    specific failures that drove the metric.

Categories (allow-listed to keep the ledger clean)
──────────────────────────────────────────────────
  correction        — user explicitly said "no, it's Y not X" via /correct
  eval_miss         — a regression-suite assertion failed
  user_override     — user took a different action from ARIA's recommendation
  escalation_miss   — ARIA should have escalated but didn't (alert threshold)
  false_confidence  — declared confidence but got corrected
  silent_failure    — subsystem returned empty when data existed
  hallucination     — ARIA stated a fact that proved false

Each entry carries a stable `signature` (task_type + domain + what_class)
so lookup_similar() can find past mistakes on similar work. That is the
primary read-path: before a DD run on a Polish entity, query
lookup_similar(task_type="dd", domain="PL") and surface the last 5
mistakes to the planning step.

Public API
──────────
  await record(category, task_type, domain, what, why, fix, ...) -> dict
  await lookup_similar(task_type, domain, limit=5) -> list[dict]
  await recent(limit=50, category=None) -> list[dict]
  await mark_prevented(mistake_id, prevented_by, context="") -> dict
  await stats() -> dict
  await verify_chain(start=0, count=500) -> dict
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("aria.mistake_ledger")

CATEGORIES = {
    "correction", "eval_miss", "user_override", "escalation_miss",
    "false_confidence", "silent_failure", "hallucination",
    # Core Self-Development Loop (Clauses 17/18/19) — shipped 2026-04-15
    "source_seeding_suspected",    # search_doctrine: ≥3 uniform snippets
    "insufficient_public_intel",   # search_doctrine: 3-attempt exhaustion
    "verified_contradiction",      # verified_intel: sources disagree on fact
    "source_validator_rejected",   # source_validator: candidate failed quality
    "source_auto_suspended",       # web_atlas: reliability EMA < 0.4
    "paraphrase_violation",        # chat response copied ≥200 chars verbatim
}

_KEY_LOG = "crucix:mistake_ledger:log"
_KEY_HEAD_HASH = "crucix:mistake_ledger:head_hash"
_KEY_BY_SIG = "crucix:mistake_ledger:by_sig:{sig}"
_KEY_BY_ID = "crucix:mistake_ledger:by_id:{mid}"
_KEY_PREVENTED_TOTAL = "crucix:mistake_ledger:prevented_total"
# Invalidation lives in a SEPARATE store so the immutable hash chain
# stays untouched. lookup_similar/recent filter against this set; the
# original entries remain in the chain for forensic traceability.
_KEY_INVALIDATED = "crucix:mistake_ledger:invalidated"
_KEY_INVALIDATION_REASON = "crucix:mistake_ledger:invalidation_reason:{mid}"
_GENESIS = "0" * 64


def _serialise(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _signature(task_type: str, domain: str, what_class: str) -> str:
    """Stable 16-char hash grouping similar mistakes. Used by the
    lookup_similar read-path — the predictor asks 'any past mistakes
    matching this signature?' before every new task."""
    norm = f"{(task_type or '').lower()}|{(domain or '').lower()}|{(what_class or '').lower()}"
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


async def _read_head() -> str:
    from . import redis_store as rs
    try:
        h = await rs.get(_KEY_HEAD_HASH)
        return h if h else _GENESIS
    except Exception:
        return _GENESIS


async def record(
    category: str,
    task_type: str,
    domain: str,
    what: str,
    why: str,
    fix: str,
    *,
    what_class: str = "",
    severity: str = "MEDIUM",  # LOW | MEDIUM | HIGH | CRITICAL
    source_ref: str = "",       # audit_log entry_hash, run_id, assertion name
    audit_entry_hash: str = "",
    feed_brain: bool = True,
) -> dict:
    """Record a mistake. Returns the entry with a stable mistake_id.

    what_class is a 1-3 word category like 'jurisdiction_inference',
    'sanctions_miss', 'confidence_overstatement' — used to group similar
    mistakes. If omitted, the first three words of `what` are used.
    """
    if category not in CATEGORIES:
        raise ValueError(f"mistake_ledger: unknown category '{category}'. Must be one of {CATEGORIES}")

    from . import redis_store as rs

    if not what_class:
        what_class = " ".join((what or "").split()[:3]).lower() or "unclassified"

    prev_hash = await _read_head()
    ts = datetime.now(timezone.utc).isoformat()
    seq = int(time.time() * 1000)
    sig = _signature(task_type, domain, what_class)
    # mistake_id: short, human-readable-ish, deterministic from content
    mid = hashlib.sha256(f"{seq}|{sig}|{what[:80]}".encode("utf-8")).hexdigest()[:16]

    # Immutable body — signed and hash-chained. Mutable tracking fields
    # (prevented_count etc.) are stored alongside but excluded from the hash.
    immutable = {
        "mistake_id": mid,
        "ts": ts,
        "seq": seq,
        "category": category,
        "task_type": (task_type or "").lower(),
        "domain": (domain or "unknown").lower(),
        "what_class": what_class,
        "signature": sig,
        "what": what[:2000],
        "why": why[:2000],
        "fix": fix[:2000],
        "severity": severity.upper(),
        "source_ref": source_ref,
        "audit_entry_hash": audit_entry_hash,
        "prev_hash": prev_hash,
    }
    entry_hash = hashlib.sha256(_serialise(immutable)).hexdigest()
    body = {**immutable, "entry_hash": entry_hash, "prevented_count": 0}

    try:
        await rs.lpush(_KEY_LOG, json.dumps(body, default=str))
        await rs.set(_KEY_HEAD_HASH, body["entry_hash"])
        await rs.lpush(_KEY_BY_SIG.format(sig=sig), mid)
        await rs.set_json(_KEY_BY_ID.format(mid=mid), body)
    except Exception as e:
        logger.warning("mistake_ledger: persist failed (%s): %s", mid, e)
        body["_persisted"] = False

    logger.warning(
        "[mistake] %s | %s/%s | class=%s | sev=%s | %s",
        category, task_type, domain, what_class, severity, what[:80],
    )

    if feed_brain:
        try:
            from . import brain_hook
            await brain_hook.absorb(
                module="mistake_ledger",
                summary=f"Mistake ({category}) on {task_type}/{domain}: {what[:120]} — fix: {fix[:120]}",
                entity_name=domain,
                success=False,
                confidence="CONFIRMED",  # the fact OF the mistake is confirmed
                gap_type="mistake",
                gap_detail=f"{what_class}: {what[:200]}",
            )
        except Exception as e:
            logger.debug("mistake_ledger brain_hook failed: %s", e)

    return body


async def lookup_similar(
    task_type: str,
    domain: str,
    *,
    what_class: str = "",
    limit: int = 5,
    include_invalidated: bool = False,
) -> list[dict]:
    """Return recent mistakes matching the (task_type, domain) pair,
    optionally narrowed by what_class. This is the function the predictor
    calls before every new task. Newest first.

    If what_class is omitted, returns mistakes across all classes for
    that task/domain — useful when the predictor wants the full picture.

    By default invalidated mistakes are filtered out (the predictor
    must NOT act on them — they were marked false positives manually).
    Pass include_invalidated=True for forensic queries.
    """
    from . import redis_store as rs
    results: list[dict] = []
    seen_ids: set[str] = set()
    invalidated = await _invalidated_set() if not include_invalidated else set()

    # If what_class provided, direct signature lookup. Otherwise, pull all
    # entries and filter — expensive but bounded by the soft retention cap.
    if what_class:
        sig = _signature(task_type, domain, what_class)
        ids = await rs.lrange(_KEY_BY_SIG.format(sig=sig), 0, limit * 2)
        for mid in ids:
            mid_clean = mid.strip('"') if isinstance(mid, str) else mid
            if mid_clean in seen_ids:
                continue
            seen_ids.add(mid_clean)
            if mid_clean in invalidated:
                continue
            entry = await rs.get_json(_KEY_BY_ID.format(mid=mid_clean))
            if entry:
                results.append(entry)
            if len(results) >= limit:
                break
        return results

    # Broad scan fallback
    raw = await rs.lrange(_KEY_LOG, 0, 500)
    tt = (task_type or "").lower()
    dm = (domain or "").lower()
    for r in raw:
        try:
            e = json.loads(r) if isinstance(r, str) else r
        except Exception:
            continue
        if e.get("task_type") != tt:
            continue
        if dm and e.get("domain") != dm:
            continue
        if e.get("mistake_id") in seen_ids:
            continue
        if e.get("mistake_id") in invalidated:
            continue
        seen_ids.add(e.get("mistake_id"))
        results.append(e)
        if len(results) >= limit:
            break
    return results


async def recent(
    limit: int = 50,
    category: Optional[str] = None,
    *,
    include_invalidated: bool = False,
) -> list[dict]:
    from . import redis_store as rs
    raw = await rs.lrange(_KEY_LOG, 0, max(limit * 3, 50))
    invalidated = await _invalidated_set() if not include_invalidated else set()
    out: list[dict] = []
    for r in raw:
        try:
            e = json.loads(r) if isinstance(r, str) else r
        except Exception:
            continue
        if category and e.get("category") != category:
            continue
        if not include_invalidated and e.get("mistake_id") in invalidated:
            continue
        out.append(e)
        if len(out) >= limit:
            break
    return out


# ── Soft invalidation (poisoned entries from LLM-degraded runs) ────────────
#
# The hash chain is intentionally immutable for tamper-evidence. To
# remove the predictor-corrupting effect of false-positive entries
# (e.g. the 11 from 2026-04-19 06:00 adversarial that fired with two
# providers billing-cooled), we mark them invalidated in a separate
# Redis SET. lookup_similar / recent filter them out by default; the
# chain itself is untouched and remains auditable.

async def _invalidated_set() -> set[str]:
    from . import redis_store as rs
    try:
        members = await rs.smembers(_KEY_INVALIDATED)
        return {m.decode() if isinstance(m, bytes) else m for m in (members or set())}
    except Exception:
        return set()


async def is_invalidated(mistake_id: str) -> bool:
    from . import redis_store as rs
    try:
        return bool(await rs.sismember(_KEY_INVALIDATED, mistake_id))
    except Exception:
        return False


async def invalidate(mistake_ids: list[str], reason: str = "") -> dict:
    """Mark mistakes as invalidated. They remain in the hash chain
    (forensic record) but are filtered from lookup_similar / recent
    so the predictor doesn't act on them.

    Returns counts. Idempotent — re-invalidating is a no-op.
    """
    from . import redis_store as rs
    if not mistake_ids:
        return {"requested": 0, "invalidated": 0, "skipped": 0}
    requested = len(mistake_ids)
    invalidated_count = 0
    skipped = 0
    ts = datetime.now(timezone.utc).isoformat()
    payload = {"reason": reason, "invalidated_at": ts}
    for mid in mistake_ids:
        try:
            entry = await rs.get_json(_KEY_BY_ID.format(mid=mid))
            if not entry:
                skipped += 1
                continue
            await rs.sadd(_KEY_INVALIDATED, mid)
            await rs.set_json(_KEY_INVALIDATION_REASON.format(mid=mid), payload, ex=365 * 86400)
            invalidated_count += 1
        except Exception as e:
            logger.warning("mistake_ledger.invalidate(%s) failed: %s", mid, e)
            skipped += 1
    logger.warning(
        "mistake_ledger: invalidated %d/%d entries — reason=%r",
        invalidated_count, requested, reason[:120],
    )
    return {"requested": requested, "invalidated": invalidated_count, "skipped": skipped}


async def list_invalidated() -> list[dict]:
    """Return invalidated entries with their reason. Forensic view."""
    from . import redis_store as rs
    invalidated = await _invalidated_set()
    out = []
    for mid in invalidated:
        entry = await rs.get_json(_KEY_BY_ID.format(mid=mid))
        reason = await rs.get_json(_KEY_INVALIDATION_REASON.format(mid=mid))
        if entry:
            out.append({
                "mistake_id": mid,
                "ts": entry.get("ts"),
                "category": entry.get("category"),
                "task_type": entry.get("task_type"),
                "domain": entry.get("domain"),
                "what": entry.get("what", "")[:120],
                "source_ref": entry.get("source_ref", ""),
                "invalidated_at": (reason or {}).get("invalidated_at"),
                "reason": (reason or {}).get("reason"),
            })
    return out


async def mark_prevented(mistake_id: str, prevented_by: str, context: str = "") -> dict:
    """When the predictor surfaces a past mistake and the new task avoids
    it, increment the prevented_count. This is the single most important
    metric in the whole self-awareness stack — it's proof the loop closes.

    Returns the updated mistake record. Also emits a self_metrics utility=1.0
    signal so the rollup can report 'mistakes prevented this week'.
    """
    from . import redis_store as rs
    entry = await rs.get_json(_KEY_BY_ID.format(mid=mistake_id))
    if not entry:
        return {"ok": False, "reason": "mistake_id not found"}
    entry["prevented_count"] = int(entry.get("prevented_count", 0)) + 1
    entry["last_prevented_at"] = datetime.now(timezone.utc).isoformat()
    entry["last_prevented_by"] = prevented_by
    if context:
        entry.setdefault("prevented_context", []).append(context[:500])
    await rs.set_json(_KEY_BY_ID.format(mid=mistake_id), entry)
    try:
        await rs.incr(_KEY_PREVENTED_TOTAL, 1)
    except Exception as e:
        logger.debug("mistake_ledger prevented_total incr failed: %s", e)
    try:
        from . import self_metrics
        await self_metrics.emit(
            "utility",
            entry.get("domain", "unknown"),
            "mistake_prevented",
            1.0,
            context={"mistake_id": mistake_id, "class": entry.get("what_class")},
            source_module="mistake_ledger",
        )
    except Exception as e:
        logger.debug("mark_prevented self_metrics emit failed: %s", e)
    return {"ok": True, "entry": entry}


async def stats() -> dict:
    from . import redis_store as rs
    sample = await rs.lrange(_KEY_LOG, 0, 9999)
    by_category: dict[str, int] = {c: 0 for c in CATEGORIES}
    for r in sample:
        try:
            e = json.loads(r) if isinstance(r, str) else r
        except Exception:
            continue
        cat = e.get("category")
        if cat in by_category:
            by_category[cat] += 1
    try:
        prevented_total = int(await rs.get(_KEY_PREVENTED_TOTAL) or 0)
    except Exception:
        prevented_total = 0
    return {
        "total_entries": len(sample),
        "prevented_total": prevented_total,
        "by_category": by_category,
        "head_hash": await _read_head(),
    }


async def verify_chain(start: int = 0, count: int = 500) -> dict:
    from . import redis_store as rs
    raw = await rs.lrange(_KEY_LOG, start, start + count - 1)
    entries = []
    for r in raw:
        try:
            entries.append(json.loads(r) if isinstance(r, str) else r)
        except Exception:
            continue
    entries.reverse()  # chronological
    if not entries:
        return {"verified": True, "checked": 0, "broken": []}
    broken: list[dict] = []
    expected_prev = entries[0].get("prev_hash") or _GENESIS
    mutable_fields = {"entry_hash", "prevented_count", "last_prevented_at",
                      "last_prevented_by", "prevented_context", "_persisted"}
    for i, e in enumerate(entries):
        immutable_body = {k: v for k, v in e.items() if k not in mutable_fields}
        recomputed = hashlib.sha256(_serialise(immutable_body)).hexdigest()
        if recomputed != e.get("entry_hash"):
            broken.append({"index": i, "ts": e.get("ts"),
                           "mistake_id": e.get("mistake_id"),
                           "reason": "entry_hash mismatch"})
        if e.get("prev_hash") != expected_prev:
            broken.append({"index": i, "ts": e.get("ts"),
                           "mistake_id": e.get("mistake_id"),
                           "reason": "chain break"})
        expected_prev = e.get("entry_hash", "")
    return {"verified": not broken, "checked": len(entries), "broken": broken[:20]}

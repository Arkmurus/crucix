"""Metacognitive journal — hourly structured reflection.

Every hour, ARIA reviews what she did in the last 60 minutes and writes
a structured journal entry. Three buckets:

  SUCCESSES — chat turns that passed the honesty judge, DD reports that
              didn't get quarantined, adversarial attacks she caught,
              compliance gates that fired correctly, calibration lifts

  FAILURES  — mistakes recorded in mistake_ledger, DD runs that were
              quarantined, chat turns the verifier flagged ungrounded,
              bright-lines that should have fired but didn't

  NOVELTIES — new entity types, new jurisdictions, new document formats,
              new bright-line codes, new sanctions-list matches. The
              "what have I encountered that's genuinely new" signal.

The journal persists to Redis (hourly rolling list, 168h = 1 week
retention) and feeds the training_export pipeline. It's ARIA's
equivalent of a PhD student's lab notebook — raw, structured,
time-stamped observation of her own operation.

LLM-free. Pure Python reading the structured signals ARIA already
emits. ~30-60s per run.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

logger = logging.getLogger("aria.learning.metacognitive_journal")


_REDIS_JOURNAL_KEY = "crucix:learning:metacog:journal"
_MAX_ENTRIES = 168          # ~1 week of hourly entries
_LOOKBACK_HOURS = 1


# ═══════════════════════════════════════════════════════════════════════
# Reflection collectors
# ═══════════════════════════════════════════════════════════════════════

async def _collect_successes(since: datetime) -> list[str]:
    """Things that went right in the lookback window."""
    out: list[str] = []

    # Chat turns with grounded verdict. chat_audit_log.record_chat now
    # writes `verification_status`; legacy entries used `honesty_verdict`.
    # Accept either so the journal's success collector doesn't stay at 0
    # after the field rename (same compatibility fix style_learner took
    # in c0418c5).
    try:
        from ..intel import chat_audit_log as cal
        if hasattr(cal, "get_recent"):
            for e in (await cal.get_recent(limit=100)) or []:
                if not isinstance(e, dict):
                    continue
                if _is_after(e.get("timestamp") or e.get("ts"), since):
                    verdict = (
                        e.get("verification_status")
                        or e.get("honesty_verdict")
                        or ""
                    ).lower()
                    if verdict == "grounded":
                        trace = (e.get("trace_id") or "?")[:10]
                        out.append(f"grounded chat turn ({trace})")
    except Exception as exc:
        logger.debug("success.chat_audit failed: %s", exc)

    # DD reports that ran + didn't get quarantined
    try:
        from ..intel import redis_store as rs
        from ..intel import dd_orchestrator as dd
        from ..intel import run_quarantine
        idx = await rs.get_json(getattr(dd, "REPORT_INDEX_KEY", "crucix:dd:report_index"))
        items = idx if isinstance(idx, list) else (idx.get("items") if isinstance(idx, dict) else [])
        for it in items:
            if not isinstance(it, dict):
                continue
            if not _is_after(it.get("generated_at") or it.get("run_at"), since):
                continue
            run_id = it.get("run_id") or ""
            if run_id and await run_quarantine.is_quarantined(run_id):
                continue
            out.append(f"DD completed: {it.get('entity_name', '?')} ({run_id[:12]})")
    except Exception as exc:
        logger.debug("success.dd_reports failed: %s", exc)

    # Adversarial passes
    try:
        from ..intel import adversarial_challenge as ac
        stats = await ac.stats() if hasattr(ac, "stats") else {}
        if isinstance(stats, dict):
            last = stats.get("last_run") or {}
            run_at = last.get("run_at")
            if _is_after(run_at, since):
                passed = last.get("passed", 0)
                total = last.get("total_attacks", 0)
                if passed:
                    out.append(f"adversarial audit: {passed}/{total} attacks passed")
    except Exception as exc:
        logger.debug("success.adversarial failed: %s", exc)

    # Bright-line triggers (compliance gate fired correctly — a success)
    try:
        from ..intel import regional_bright_lines as rbl
        hits = await rbl.get_hits_24h()
        # Only count hits within the last hour
        if hits.get("items"):
            recent = [i for i in hits["items"] if _is_after(i.get("at"), since)]
            for r in recent:
                out.append(f"bright-line fired: {r.get('code')}")
    except Exception as exc:
        logger.debug("success.bright_lines failed: %s", exc)

    # Calibration corrections (mastery was lifted toward truth — success for us)
    try:
        from ..intel import redis_store as rs
        last_correction = await rs.get("crucix:calibration:last_correction")
        if last_correction:
            try:
                lc_ts = datetime.fromtimestamp(float(last_correction), tz=timezone.utc)
                if lc_ts >= since:
                    out.append("calibration auto-correction applied (mastery lifted)")
            except Exception:
                pass
    except Exception:
        pass

    return out


async def _collect_failures(since: datetime) -> list[str]:
    """Mistakes / quarantines / ungrounded replies in the window."""
    out: list[str] = []

    # Mistake ledger
    try:
        from ..intel import mistake_ledger as ml
        if hasattr(ml, "recent"):
            for m in (await ml.recent(limit=50)) or []:
                if not isinstance(m, dict):
                    continue
                if _is_after(m.get("timestamp") or m.get("ts"), since):
                    out.append(
                        f"mistake recorded: {m.get('category', '?')} — "
                        f"{(m.get('summary') or '')[:120]}"
                    )
    except Exception as exc:
        logger.debug("failure.mistake_ledger failed: %s", exc)

    # Ungrounded chat turns — same field-drift accommodation as the
    # success collector above.
    try:
        from ..intel import chat_audit_log as cal
        if hasattr(cal, "get_recent"):
            for e in (await cal.get_recent(limit=100)) or []:
                if not isinstance(e, dict):
                    continue
                if not _is_after(e.get("timestamp") or e.get("ts"), since):
                    continue
                verdict = (
                    e.get("verification_status")
                    or e.get("honesty_verdict")
                    or ""
                ).lower()
                if verdict in ("ungrounded", "partially_grounded", "unverified"):
                    out.append(
                        f"chat turn {verdict}: "
                        f"{(e.get('user_message') or '')[:80]!r}"
                    )
    except Exception as exc:
        logger.debug("failure.ungrounded failed: %s", exc)

    # New quarantine entries
    try:
        from ..intel import run_quarantine as rq
        recent_q = await rq.list_quarantined()
        for q in recent_q or []:
            qat = q.get("quarantined_at") or ""
            if _is_after(qat, since):
                out.append(
                    f"DD run quarantined: {q.get('run_id')} — "
                    f"{(q.get('reason') or '')[:120]}"
                )
    except Exception as exc:
        logger.debug("failure.quarantine failed: %s", exc)

    # Local-brain invocations (each one = LLM chain degraded)
    try:
        from ..intel import redis_store as rs
        lb = await rs.get("crucix:local_brain:invocations_24h")
        if lb and int(lb) > 0:
            # Report as a failure-like signal — LLM chain stumbled
            out.append(f"local-brain served {lb} degraded responses in 24h (LLM chain partial outage)")
    except Exception:
        pass

    return out


async def _collect_novelties(since: datetime) -> list[str]:
    """Genuinely new patterns in the window — unknown jurisdictions, new
    sanctions hits, new bright-line codes, new entity shapes."""
    out: list[str] = []

    # New bright-line codes (first time the system has seen this code fire)
    try:
        from ..intel import regional_bright_lines as rbl
        hits = await rbl.get_hits_24h()
        codes = hits.get("by_code", {})
        # Heuristic: a code that fired exactly once in 24h + hasn't been
        # seen before. We don't have a historical bright-line index yet,
        # so use a simple "first-fire-today" approximation.
        for code, n in codes.items():
            if n == 1:
                out.append(f"bright-line first-fire-today: {code}")
    except Exception as exc:
        logger.debug("novelty.bright_lines failed: %s", exc)

    # New registry-lookup capability gaps (jurisdictions we don't cover)
    try:
        from ..intel import capability_gaps as cg
        if hasattr(cg, "recent_gaps"):
            for g in (await cg.recent_gaps(limit=20)) or []:
                if not isinstance(g, dict):
                    continue
                if not _is_after(g.get("timestamp") or g.get("ts"), since):
                    continue
                out.append(
                    f"capability gap: {g.get('gap_type', '?')} — {g.get('detail', '')[:120]}"
                )
    except Exception as exc:
        logger.debug("novelty.capability_gaps failed: %s", exc)

    # Removed 2026-04-27: two aspirational signal sources read from Redis
    # keys that nothing ever wrote (`crucix:learning:bridge_triggers`,
    # `crucix:sanctions_prop:seen_oems`) -- confirmed via repo-wide grep
    # of both Python and Node code. The journal "saw" zero such signals
    # since shipped, so removing the dead reads is a no-op behaviourally
    # but stops the code from implying signal sources that don't exist.
    # If document-entity bridge or sanctions propagation get wired up
    # later, re-add the reads here against the actual key names the
    # writers will use.

    return out


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def _is_after(ts_value: Any, since: datetime) -> bool:
    """Safely parse a timestamp and return True if it's >= since."""
    if not ts_value:
        return False
    try:
        if isinstance(ts_value, (int, float)):
            return datetime.fromtimestamp(float(ts_value), tz=timezone.utc) >= since
        if isinstance(ts_value, str):
            return datetime.fromisoformat(ts_value.replace("Z", "+00:00")) >= since
    except Exception:
        return False
    return False


# ═══════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════

async def run_hourly_journal() -> dict[str, Any]:
    """Write the hourly journal entry. Returns the entry for inspection."""
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=_LOOKBACK_HOURS)

    successes = await _collect_successes(since)
    failures = await _collect_failures(since)
    novelties = await _collect_novelties(since)

    entry = {
        "timestamp": now.isoformat(),
        "window_start": since.isoformat(),
        "window_hours": _LOOKBACK_HOURS,
        "successes": successes[:50],
        "failures": failures[:50],
        "novelties": novelties[:50],
        "counts": {
            "successes": len(successes),
            "failures": len(failures),
            "novelties": len(novelties),
        },
        "hash": hashlib.sha1(
            f"{now.isoformat()}|{len(successes)}|{len(failures)}|{len(novelties)}".encode()
        , usedforsecurity=False).hexdigest()[:12],
    }

    # Persist — keep last _MAX_ENTRIES
    try:
        from ..intel import redis_store as rs
        existing = await rs.get_json(_REDIS_JOURNAL_KEY) or []
        if not isinstance(existing, list):
            existing = []
        existing.append(entry)
        existing = existing[-_MAX_ENTRIES:]
        await rs.set_json(_REDIS_JOURNAL_KEY, existing)
    except Exception as exc:
        logger.warning("journal persist failed: %s", exc)

    # Brain-hook: the act of journaling is a learning signal
    try:
        from ..intel import brain_hook
        net = len(successes) - len(failures)
        await brain_hook.absorb(
            module="metacognitive_journal",
            summary=(
                f"Hourly journal: {len(successes)} successes, "
                f"{len(failures)} failures, {len(novelties)} novelties "
                f"(net +{net})"
            ),
            success=len(failures) == 0,
        )
    except Exception:
        pass

    logger.info(
        "[metacog] journal tick — successes=%d failures=%d novelties=%d",
        len(successes), len(failures), len(novelties),
    )
    return entry


async def get_recent_entries(limit: int = 24) -> list[dict[str, Any]]:
    from ..intel import redis_store as rs
    try:
        data = await rs.get_json(_REDIS_JOURNAL_KEY) or []
        return data[-limit:] if isinstance(data, list) else []
    except Exception:
        return []


async def get_weekly_summary() -> dict[str, Any]:
    """Aggregate the last 168 hourly entries into a weekly snapshot."""
    entries = await get_recent_entries(limit=_MAX_ENTRIES)
    if not entries:
        return {"hours_recorded": 0, "successes": 0, "failures": 0, "novelties": 0}
    total_s = sum(e.get("counts", {}).get("successes", 0) for e in entries)
    total_f = sum(e.get("counts", {}).get("failures", 0) for e in entries)
    total_n = sum(e.get("counts", {}).get("novelties", 0) for e in entries)
    return {
        "hours_recorded": len(entries),
        "successes": total_s,
        "failures": total_f,
        "novelties": total_n,
        "success_to_failure_ratio": round(total_s / max(total_f, 1), 2),
        "latest_entry_at": entries[-1].get("timestamp"),
    }


def summary() -> dict[str, Any]:
    return {
        "max_entries": _MAX_ENTRIES,
        "lookback_hours": _LOOKBACK_HOURS,
    }

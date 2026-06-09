"""Measure real trace volume — count admissible candidates for training.

R-F1462: before committing to 200-500 pairs/week, measure what's actually
available from each source over the last 7 and 30 days.

Sources:
  - chat_audit: grounded + well_formed chat turns
  - adversarial: passed attack responses
  - mistake_ledger: recorded mistakes with known fixes
  - DD reports: passed-verification, non-quarantined reports

Usage:
    python scripts/admin/measure_trace_volume.py

Requires:
  - Running on the Fly instance (aria-intel) with Redis access
  - Or: ARIA_REDIS_URL env var pointing to the live Redis
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make aria_service importable
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("measure_trace_volume")


async def measure_chat_audit(days: int) -> dict:
    """Count admissible chat turns from the audit log."""
    from aria_service.intel import chat_audit_log as cal
    from aria_service.learning.training_export import (
        _MIN_WORD_COUNT, _MAX_WORD_COUNT, _EXCLUDE_TAGS,
    )

    if not hasattr(cal, "get_recent"):
        return {"source": "chat_audit", "error": "get_recent not available", "admitted": 0}

    try:
        entries = await cal.get_recent(limit=2000)
    except Exception as e:
        return {"source": "chat_audit", "error": f"get_recent failed: {e}", "admitted": 0}

    if not entries:
        return {"source": "chat_audit", "admitted": 0, "reason": "no entries in Redis"}

    # Check if raw text capture is enabled
    import os as _os
    capture_enabled = (_os.getenv("ARIA_CHAT_TRAIN_CAPTURE_TEXT") or "").strip().lower() in ("1", "true", "yes")

    # Check a sample entry to see what fields exist
    sample = entries[0] if entries else {}
    available_fields = list(sample.keys()) if isinstance(sample, dict) else []

    cutoff = datetime.now(timezone.utc).timestamp() - (days * 86400)
    total = 0
    grounded = 0
    well_formed = 0
    filtered_empty = 0
    filtered_short = 0
    filtered_long = 0
    filtered_excluded = 0
    filtered_old = 0
    total_entries_in_window = 0

    for e in entries:
        if not isinstance(e, dict):
            continue
        # Check timestamp
        ts_raw = e.get("timestamp") or e.get("ts") or 0
        try:
            if isinstance(ts_raw, str):
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).timestamp()
            else:
                ts = float(ts_raw)
        except Exception:
            ts = 0
        if ts < cutoff:
            filtered_old += 1
            continue
        total_entries_in_window += 1

        # Without ARIA_CHAT_TRAIN_CAPTURE_TEXT=1, entries only have hashes
        user_msg = e.get("user_message") or ""
        aria_reply = e.get("response") or ""
        if not user_msg or not aria_reply:
            filtered_empty += 1
            continue

        wc = len(aria_reply.split())
        if wc < _MIN_WORD_COUNT:
            filtered_short += 1
            continue
        if wc > _MAX_WORD_COUNT:
            filtered_long += 1
            continue

        if any(tag.lower() in aria_reply.lower() for tag in _EXCLUDE_TAGS):
            filtered_excluded += 1
            continue

        verdict = (
            (e.get("verification_status") or e.get("honesty_verdict") or "").lower()
        )
        grounded_rate = e.get("grounded_rate", 0) or 0

        if verdict == "grounded" and grounded_rate >= 0.40:
            grounded += 1
            total += 1
        elif verdict == "well_formed":
            well_formed += 1
            total += 1

    return {
        "source": "chat_audit",
        "days": days,
        "admitted": total,
        "grounded": grounded,
        "well_formed": well_formed,
        "total_entries_in_window": total_entries_in_window,
        "capture_enabled": capture_enabled,
        "available_fields": available_fields[:15],
        "filtered_old": filtered_old,
        "filtered_empty": filtered_empty,
        "filtered_short": filtered_short,
        "filtered_long": filtered_long,
        "filtered_excluded": filtered_excluded,
        "note": (
            "Without ARIA_CHAT_TRAIN_CAPTURE_TEXT=1, entries only have hashed text. "
            "Set it to enable raw text capture for training."
            if not capture_enabled else ""
        ),
    }


async def measure_adversarial(days: int) -> dict:
    """Count passed adversarial attacks."""
    from aria_service.intel import adversarial_challenge as ac
    from aria_service.learning.training_export import _MIN_WORD_COUNT

    if not hasattr(ac, "recent_runs"):
        return {"source": "adversarial", "error": "recent_runs not available", "count": 0}

    runs = await ac.recent_runs(limit=50)
    library_by_id = {a.id: a for a in getattr(ac, "ATTACK_LIBRARY", [])}
    cutoff = datetime.now(timezone.utc).timestamp() - (days * 86400)

    total_passed = 0
    total_attacks = 0
    filtered_short = 0
    filtered_no_turns = 0

    for run in runs or []:
        if not isinstance(run, dict):
            continue
        try:
            run_ts = datetime.fromisoformat(
                (run.get("run_at") or "").replace("Z", "+00:00")
            ).timestamp()
        except Exception:
            run_ts = 0.0
        if run_ts and run_ts < cutoff:
            continue

        for result in run.get("results") or []:
            if not isinstance(result, dict) or not result.get("passed"):
                continue
            total_attacks += 1
            attack_id = result.get("attack_id") or ""
            attack = library_by_id.get(attack_id)
            turns = list(attack.turns) if attack and attack.turns else []
            if not turns:
                filtered_no_turns += 1
                continue
            responses = result.get("responses") or []
            aria_reply = (responses[-1] if responses else "") or ""
            if len(aria_reply.split()) < _MIN_WORD_COUNT:
                filtered_short += 1
                continue
            total_passed += 1

    return {
        "source": "adversarial",
        "days": days,
        "admitted": total_passed,
        "total_attacks_seen": total_attacks,
        "filtered_no_turns": filtered_no_turns,
        "filtered_short": filtered_short,
    }


async def measure_dd_reports(days: int) -> dict:
    """Count admissible DD reports."""
    from aria_service.intel import redis_store as rs
    from aria_service.intel import dd_orchestrator as dd
    from aria_service.intel import run_quarantine
    from aria_service.learning.training_export import _MIN_WORD_COUNT

    idx = await rs.get_json(getattr(dd, "REPORT_INDEX_KEY", "crucix:dd:report_index"))
    items = idx if isinstance(idx, list) else (idx.get("items") if isinstance(idx, dict) else [])
    cutoff = datetime.now(timezone.utc).timestamp() - (days * 86400)

    total = 0
    filtered_old = 0
    filtered_quarantined = 0
    filtered_short = 0
    filtered_no_body = 0

    for it in items:
        if not isinstance(it, dict):
            continue
        generated = it.get("generated_at") or it.get("run_at") or ""
        try:
            ts = datetime.fromisoformat(generated.replace("Z", "+00:00")).timestamp()
        except Exception:
            continue
        if ts < cutoff:
            filtered_old += 1
            continue
        run_id = it.get("run_id") or ""
        if run_id and await run_quarantine.is_quarantined(run_id):
            filtered_quarantined += 1
            continue
        body_key = f"crucix:dd:report:{run_id}"
        body = await rs.get_json(body_key)
        if not isinstance(body, dict):
            filtered_no_body += 1
            continue
        entity = (body.get("identity") or {}).get("entity_name") or ""
        if not entity or len(entity) < 3:
            continue
        rendered = body.get("rendered") or body.get("markdown") or ""
        if not rendered or len(rendered.split()) < _MIN_WORD_COUNT:
            filtered_short += 1
            continue
        total += 1

    return {
        "source": "dd_reports",
        "days": days,
        "admitted": total,
        "filtered_old": filtered_old,
        "filtered_quarantined": filtered_quarantined,
        "filtered_no_body": filtered_no_body,
        "filtered_short": filtered_short,
    }


async def measure_mistake_ledger(days: int) -> dict:
    """Count recorded mistakes with known fixes."""
    from aria_service.intel import redis_store as rs

    KEY = "crucix:mistake_ledger:log"
    cutoff = datetime.now(timezone.utc).timestamp() - (days * 86400)

    try:
        raw = await rs.lrange(KEY, 0, 500)
    except Exception as e:
        return {"source": "mistake_ledger", "error": str(e), "count": 0}

    total = 0
    filtered_old = 0
    for entry_bytes in raw or []:
        try:
            entry = json.loads(
                entry_bytes.decode("utf-8") if isinstance(entry_bytes, bytes) else entry_bytes
            )
        except Exception:
            continue
        if not isinstance(entry, dict):
            continue
        ts_raw = entry.get("ts") or entry.get("timestamp") or 0
        try:
            if isinstance(ts_raw, str):
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).timestamp()
            else:
                ts = float(ts_raw)
        except Exception:
            ts = 0
        if ts < cutoff:
            filtered_old += 1
            continue
        what = entry.get("what") or entry.get("what_class") or ""
        fix = entry.get("fix") or ""
        if what and fix:
            total += 1

    return {
        "source": "mistake_ledger",
        "days": days,
        "admitted": total,
        "filtered_old": filtered_old,
    }


async def main() -> None:
    print("=" * 60)
    print("TRACE VOLUME MEASUREMENT")
    print("=" * 60)

    for days_label, days in [("7 days", 7), ("30 days", 30)]:
        print(f"\n--- {days_label} ---")
        for measure_fn in [
            measure_chat_audit,
            measure_adversarial,
            measure_dd_reports,
            measure_mistake_ledger,
        ]:
            try:
                result = await measure_fn(days)
                admitted = result.get("admitted", "ERR")
                print(f"  {result['source']}: {admitted} admissible")
                # Show all non-standard fields
                for k, v in result.items():
                    if k not in ("source", "days", "admitted"):
                        print(f"    {k}: {v}")
            except Exception as e:
                import traceback
                print(f"  {measure_fn.__name__}: UNCAUGHT ERROR: {e}")
                traceback.print_exc()

    # Store key verification
    print("\n" + "=" * 60)
    print("STORE KEY VERIFICATION")
    print("=" * 60)
    print("Verifying that each source reads the ACTUAL live store keys...")
    store_keys = {
        "chat_audit": "crucix:chat_audit:log",
        "adversarial_runs": "aria:adversarial:runs",
        "mistake_ledger": "crucix:mistake_ledger:log",
        "dd_report_index": "crucix:dd:report_index",
    }
    try:
        from aria_service.intel import redis_store as rs
        for name, key in store_keys.items():
            try:
                exists = await rs.exists(key)
                print(f"  {name}: {key} -> {'EXISTS' if exists else 'NOT FOUND'}")
            except Exception as e:
                print(f"  {name}: {key} -> ERROR: {e}")
    except Exception as e:
        print(f"  redis_store not available: {e}")

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print("Run this script ON the Fly instance (aria-intel) to get real numbers.")
    print("  flyctl ssh console -a aria-intel")
    print("  cd /app")
    print("  python scripts/admin/measure_trace_volume.py")


if __name__ == "__main__":
    asyncio.run(main())

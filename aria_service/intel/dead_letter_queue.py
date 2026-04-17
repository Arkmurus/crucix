"""Dead letter queue for failed autonomous deliveries.

When an autonomous task produces a result but delivery fails (WhatsApp
down, intel_ledger write error, etc.), the result is captured here
instead of vanishing. Compliance-relevant failures escalate immediately.

Redis keys:
  crucix:dlq:queue    — list of failed deliveries (max 500, 30-day TTL)
  crucix:dlq:stats    — aggregate counts

API:
  GET  /api/aria/autonomous/dlq         — view queue
  POST /api/aria/autonomous/dlq/retry   — retry a specific entry
  POST /api/aria/autonomous/dlq/clear   — clear resolved entries
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

logger = logging.getLogger("aria.dlq")

_K_QUEUE = "crucix:dlq:queue"
_K_STATS = "crucix:dlq:stats"
_MAX_ENTRIES = 500
_TTL_DAYS = 30

# Tasks that require immediate escalation when delivery fails
COMPLIANCE_TASKS = {
    "DAILY-SANCTIONS-SCREENING",
    "DAILY-FACT-REFRESH",
    "WEEKLY-DD-WATCHLIST",
    "WEEKLY-COMP-SANCTIONS",
}


async def enqueue(
    task_id: str,
    task_name: str,
    result: str,
    channel: str,
    error: str,
    *,
    compliance_relevant: bool = False,
) -> dict:
    """Add a failed delivery to the dead letter queue."""
    from . import redis_store as rs

    entry = {
        "task_id": task_id,
        "task_name": task_name,
        "channel": channel,
        "error": str(error)[:500],
        "result_preview": str(result)[:1000],
        "result_full": str(result)[:10000],
        "compliance_relevant": compliance_relevant or task_id in COMPLIANCE_TASKS,
        "enqueued_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "retried": False,
        "resolved": False,
    }

    queue = await rs.get_json(_K_QUEUE) or []
    queue.insert(0, entry)
    queue = queue[:_MAX_ENTRIES]
    await rs.set_json(_K_QUEUE, queue, ex=_TTL_DAYS * 86400)

    # Update stats
    stats = await rs.get_json(_K_STATS) or {"total": 0, "compliance": 0, "resolved": 0}
    stats["total"] = stats.get("total", 0) + 1
    if entry["compliance_relevant"]:
        stats["compliance"] = stats.get("compliance", 0) + 1
    await rs.set_json(_K_STATS, stats, ex=_TTL_DAYS * 86400)

    logger.warning(
        "[dlq] enqueued: task=%s channel=%s error=%s compliance=%s",
        task_id, channel, error[:100], entry["compliance_relevant"],
    )

    # Compliance-relevant failures escalate via brain_hook
    if entry["compliance_relevant"]:
        try:
            from . import brain_hook as _bh
            await _bh.absorb(
                module="dead_letter_queue",
                summary=f"COMPLIANCE DELIVERY FAILURE: {task_id} → {channel} failed: {error[:200]}",
                detail=entry["result_preview"],
                success=False,
                gap_type="adversarial_critical_failure",
                gap_detail=f"Compliance task {task_id} delivery failed to {channel}",
            )
        except Exception:
            pass

    return entry


async def get_queue(include_resolved: bool = False) -> list[dict]:
    """Return the DLQ contents."""
    from . import redis_store as rs
    queue = await rs.get_json(_K_QUEUE) or []
    if not include_resolved:
        queue = [e for e in queue if not e.get("resolved")]
    return queue


async def get_stats() -> dict:
    """Return DLQ aggregate stats."""
    from . import redis_store as rs
    return await rs.get_json(_K_STATS) or {"total": 0, "compliance": 0, "resolved": 0}


async def mark_resolved(index: int) -> bool:
    """Mark a DLQ entry as resolved."""
    from . import redis_store as rs
    queue = await rs.get_json(_K_QUEUE) or []
    if 0 <= index < len(queue):
        queue[index]["resolved"] = True
        await rs.set_json(_K_QUEUE, queue, ex=_TTL_DAYS * 86400)
        return True
    return False

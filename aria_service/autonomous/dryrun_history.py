"""ARIA Layer 3 — autonomous engine dry-run visibility ledger (R-F774).

When the autonomous engine runs in dry-run mode, real delivery channels
(intel_ledger, WhatsApp, deal_pipeline) are short-circuited so nothing
externally observable happens. That's the safety floor — but it means
the operator can't *see* what the brain decided to do.

This module captures every would-be-delivery as a structured entry so
the operator can audit ARIA's autonomous decisions in real time. The
visibility loop is the bridge between "brain is independent" and
"operator stays in the loop" — both have to be true while the autonomy
ladder is climbing from L1 (research-only) toward L3 (full delivery).

Storage: Redis list, append-only, no TTL (per CLAUDE.md §7 infinite
memory). Capped at last 500 entries to keep the read endpoint fast;
older entries roll into cold storage via the existing brain_hook
absorb pipeline.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from ..intel import redis_store as rs
from ..intel.wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.autonomous.dryrun_history")

# R-F1320: wire module health to the brain
try:
    from aria_service.intel.engine_wiring import wire_success as _ws1320
    _ws1320(
        module="autonomous.dryrun_history",
        summary="Dryrun History active",
        source_id="autonomous:dryrun_history:R-F1320",
    )
except Exception:
    pass

_KEY = "crucix:autonomous:dryrun_history"
_MAX_HOT = 500  # entries kept in the hot list before the head is trimmed


@fail_wire(module="dryrun_history", gap_type="agent_cycle_failure")
async def record(
    *,
    task_id: str,
    task_name: str,
    channel: str,
    would_deliver: bool,
    response_snippet: str,
    triggered_flags: list[str] | None = None,
    reason: str = "",
) -> None:
    """Append a dry-run decision to the visibility ledger.

    Every call records what the autonomous engine WOULD have done if
    dry-run were off — the channel it would have hit, a snippet of the
    payload, the escalation flags that fired. Operator-readable via
    GET /api/aria/autonomous/dryrun/recent.
    """
    entry: dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "task_id": task_id,
        "task_name": task_name,
        "channel": channel,
        "would_deliver": bool(would_deliver),
        "reason": reason or ("dry_run_blocked" if would_deliver else "no_op"),
        "triggered_flags": list(triggered_flags or []),
        "snippet": (response_snippet or "")[:600],
    }
    try:
        await rs.lpush(_KEY, json.dumps(entry))
        await rs.ltrim(_KEY, 0, _MAX_HOT - 1)
    except Exception as e:
        # The visibility ledger MUST NOT take down the engine. A Redis
        # blip during dry-run still lets the brain run — operator just
        # loses a few audit lines.
        logger.warning("dryrun_history.record failed (non-fatal): %s", e)


@fail_wire(module="dryrun_history", gap_type="agent_cycle_failure")
async def recent(limit: int = 20) -> list[dict[str, Any]]:
    """Most-recent N dry-run entries, newest first."""
    limit = max(1, min(int(limit), _MAX_HOT))
    try:
        raw = await rs.lrange(_KEY, 0, limit - 1)
    except Exception as e:
        logger.warning("dryrun_history.recent failed: %s", e)
        return []
    out: list[dict[str, Any]] = []
    for item in raw or []:
        try:
            out.append(json.loads(item))
        except Exception:
            continue
    return out

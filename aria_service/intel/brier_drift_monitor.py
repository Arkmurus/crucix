"""R-F779 — mastery-drift monitor + RunPod fine-tune trigger scaffold.

Strategic context: ARIA's path to "Claude-tier on her domain" requires a
continuous-improvement loop where the LLM backbone gets re-trained when
its calibration drifts. The R-F94 33-attack eval harness + SFT + DPO
trainer already exist in scripts/train/* — what was missing was the
*trigger*: nobody was watching mastery scores per topic and flagging
when one cell drops.

This module ships that trigger:

  1. snapshot_mastery() — captures the current per-topic mastery_report
     to a Redis list (last 30 snapshots kept, no TTL per CLAUDE.md §7
     infinite memory).
  2. compute_drift(window_hours) — diffs the latest snapshot against
     the one closest to N hours ago. Per-topic delta + composite delta.
  3. check_thresholds_and_alert(threshold=0.10) — if any topic dropped
     by ≥ threshold (default 10 percentage points), record a CRITICAL
     pending_action so the operator sees it in the next daily briefing.
     The pending_action body documents the exact `python scripts/train/`
     command the operator should run to trigger a RunPod fine-tune.

The actual fine-tune is an operator-pending action (RunPod credentials
aren't in the repo and there's no API integration yet). Once the
operator wires `RUNPOD_API_KEY` + a `runpod_client.py` adapter, the
trigger becomes fully autonomous — this module's check_thresholds path
calls the adapter instead of just logging.

Surfaces via GET /api/aria/learning/brier-drift.
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import json
import logging
import time
from typing import Any

from . import redis_store as rs

logger = logging.getLogger("aria.brier_drift_monitor")

_SNAPSHOT_KEY = "crucix:brier_drift:snapshots"
_MAX_SNAPSHOTS = 30  # 30 daily snapshots = month-long drift window
_DEFAULT_DROP_THRESHOLD = 0.10  # 10 percentage points triggers alert


async def snapshot_mastery() -> dict[str, Any]:
    """Capture a timestamped mastery snapshot to Redis.

    Returns the snapshot for caller convenience (so the scheduled task
    that calls this can log what was captured). Errors are non-fatal —
    a Redis blip just skips this snapshot, the next one will land fine.
    """
    try:
        from . import student
        report = await student.get_mastery_report()
    except Exception as e:
        logger.warning("snapshot_mastery: get_mastery_report failed: %s", e)
        return {"error": str(e)}

    snapshot = {
        "ts": time.time(),
        "overall_mastery": report.get("overall_mastery"),
        "core_mastery": report.get("core_mastery"),
        "headline_mastery": report.get("headline_mastery"),
        "topics": {
            t: round(float(meta.get("score") or 0.0), 4)
            for t, meta in (report.get("topics") or {}).items()
        },
    }
    try:
        await rs.lpush(_SNAPSHOT_KEY, json.dumps(snapshot))
        await rs.ltrim(_SNAPSHOT_KEY, 0, _MAX_SNAPSHOTS - 1)
    except Exception as e:
        logger.warning("snapshot_mastery: redis write failed: %s", e)
    return snapshot


async def _read_snapshots() -> list[dict[str, Any]]:
    """Return all stored snapshots newest-first. Best-effort — a corrupt
    entry is skipped, not surfaced."""
    try:
        raw = await rs.lrange(_SNAPSHOT_KEY, 0, _MAX_SNAPSHOTS - 1)
    except Exception as e:
        logger.warning("_read_snapshots: redis read failed: %s", e)
        return []
    out: list[dict[str, Any]] = []
    for item in raw or []:
        try:
            out.append(json.loads(item))
        except Exception:
            continue
    return out


async def compute_drift(window_hours: int = 24) -> dict[str, Any]:
    """Diff the most-recent snapshot against the snapshot closest to
    window_hours ago. Returns per-topic and composite deltas.

    Drift is computed as `latest - baseline` so a NEGATIVE delta means
    mastery DROPPED — that's the signal we care about.
    """
    snapshots = await _read_snapshots()
    if len(snapshots) < 2:
        return {
            "ok": False,
            "reason": "need at least 2 snapshots; have %d" % len(snapshots),
            "snapshots": len(snapshots),
        }

    latest = snapshots[0]
    now = latest.get("ts") or time.time()
    cutoff = now - (max(1, int(window_hours)) * 3600)

    # Find the snapshot whose ts is closest to (but no newer than) cutoff.
    baseline = None
    for s in snapshots[1:]:
        if (s.get("ts") or 0) <= cutoff:
            baseline = s
            break
    if baseline is None:
        # Not enough history — use the OLDEST available as a best-effort.
        baseline = snapshots[-1]

    composite_delta = (
        round((latest.get("overall_mastery") or 0) - (baseline.get("overall_mastery") or 0), 4)
    )
    headline_delta = (
        round((latest.get("headline_mastery") or 0) - (baseline.get("headline_mastery") or 0), 4)
    )

    topic_deltas: dict[str, float] = {}
    latest_topics = latest.get("topics") or {}
    baseline_topics = baseline.get("topics") or {}
    for topic, latest_score in latest_topics.items():
        base_score = baseline_topics.get(topic)
        if base_score is None:
            continue
        topic_deltas[topic] = round(float(latest_score) - float(base_score), 4)

    dropped = {t: d for t, d in topic_deltas.items() if d < 0}
    worst = sorted(dropped.items(), key=lambda kv: kv[1])[:5]

    return {
        "ok": True,
        "window_hours": int(window_hours),
        "latest_ts": latest.get("ts"),
        "baseline_ts": baseline.get("ts"),
        "composite_delta": composite_delta,
        "headline_delta": headline_delta,
        "topic_deltas": topic_deltas,
        "worst_drops": worst,
    }


async def check_thresholds_and_alert(
    *,
    threshold: float = _DEFAULT_DROP_THRESHOLD,
    window_hours: int = 24,
) -> dict[str, Any]:
    """If any topic drift dropped below -threshold, record a CRITICAL
    pending_action so the operator sees it in the next briefing. The
    body documents the exact training command to run.

    The threshold is a percentage-point absolute drop, not a relative
    percentage. So threshold=0.10 fires when a cell goes from 0.80 to
    0.70 (or lower). This matches the strategic spec from R-F779.
    """
    drift = await compute_drift(window_hours=window_hours)
    if not drift.get("ok"):
        return {"alerted": False, "reason": drift.get("reason")}

    breaching = [
        (t, d) for t, d in drift.get("topic_deltas", {}).items()
        if d <= -abs(threshold)
    ]
    if not breaching:
        return {"alerted": False, "reason": "no_breach", "drift": drift}

    breaching.sort(key=lambda kv: kv[1])
    worst_topic, worst_delta = breaching[0]

    promise = (
        f"Brier-drift trigger: topic '{worst_topic}' dropped {abs(worst_delta):.3f} "
        f"in the last {window_hours}h (baseline → latest). "
        f"{len(breaching)} topic(s) breached the {threshold:+.2f} threshold. "
        "Recommended action: kick off a RunPod fine-tune against the curated "
        "golden eval set so the local_brain checkpoint regains calibration."
    )
    operator_prompt = (
        "Operator action required — RunPod adapter is not yet wired into the\n"
        "Python brain (see R-F779 module docstring). To run the fine-tune\n"
        "manually:\n\n"
        "  1. python scripts/train/prepare_sft.py "
        f"--focus-topic {worst_topic} --out /workspace/sft_corpus_R-F779.jsonl\n"
        "  2. python scripts/train/sft_train.py --corpus /workspace/sft_corpus_R-F779.jsonl "
        "--out /workspace/checkpoints/aria-llm-R-F779\n"
        "  3. python scripts/train/eval_aria_llm.py --target $RUNPOD_VLLM_URL "
        "--eval-set /workspace/datasets/defence_dd_eval.jsonl\n"
        "  4. If eval passes Phase-3 exit criteria, register the new\n"
        "     checkpoint as local_brain provider via\n"
        "     POST /api/aria/llm/provider/register.\n\n"
        "Drift detail in GET /api/aria/learning/brier-drift."
    )

    recorded = False
    try:
        from . import pending_actions as _pa
        await _pa.record(
            promise=promise,
            reason=(
                f"Mastery drift monitor (R-F779) flagged a "
                f"{abs(worst_delta):.3f} drop on '{worst_topic}' over "
                f"{window_hours}h."
            ),
            resolver_kind="operator_action",
            resolver_ref="RUNPOD_FINETUNE",
            severity="CRITICAL",
            source="brier_drift_monitor",
            operator_prompt=operator_prompt,
        )
        recorded = True
    except Exception as e:
        logger.warning("check_thresholds_and_alert: pending_actions failed: %s", e)

    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="brier_drift_monitor",
        summary="Check Thresholds And Alert",
        source_id="brier_drift_monitor:R-F996",
    )

    return {
        "alerted": recorded,
        "worst_topic": worst_topic,
        "worst_delta": worst_delta,
        "breach_count": len(breaching),
        "breaching": breaching[:10],
        "drift": drift,
    }

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.

"""R-F561 (2026-05-16) — operator-pending status panel.

Surfaces every outstanding operator-action item (env var unset, API
billing top-up needed, manual UI click pending) with live status so
the dashboard panel renders without anyone hand-grepping memory files.

Status rule per env-var entry:
  - "set"     — env var exists and is non-empty
  - "missing" — env var unset or empty
  - "stale"   — env var present but a known-bad pattern (e.g. "changeme")

For non-env actions (top-up, manual UI), status is "manual" — caller
has to confirm out of band. The panel renders the manual ones as TODO.

The catalogue is the single source of truth. To add a new pending item,
append to `_PENDING_CATALOGUE` rather than scattering it across docs.
"""
from __future__ import annotations

import os
from typing import Any

# ── Catalogue ───────────────────────────────────────────────────────────

# Each entry: dict with required keys:
#   key:         short id (snake_case)
#   kind:        "env" | "billing" | "manual"
#   env_var:     name of the env var (kind=env only)
#   title:       one-line label for the panel
#   why:         one-line justification + which gate or chain it unblocks
#   priority:    1 (P0) | 2 (P1) | 3 (P2)
#   memory_ref:  pointer to the memory file or doc with full context

_PENDING_CATALOGUE: list[dict[str, Any]] = [
    # ── Phase A gate #5 — env vars ────────────────────────────────────
    {
        "key": "acled_email",
        "kind": "env",
        "env_var": "ACLED_EMAIL",
        "title": "ACLED_EMAIL — ACLED conflict-event API login",
        "why": "Phase A gate #5: closes conflict-event coverage backlog (ACLED is free with email registration).",
        "priority": 1,
        "memory_ref": "memory/platform_buildout_north_star.md (Phase A gate #5)",
    },
    {
        "key": "acled_password",
        "kind": "env",
        "env_var": "ACLED_PASSWORD",
        "title": "ACLED_PASSWORD — paired with ACLED_EMAIL",
        "why": "Phase A gate #5: ACLED requires email + password pair, not API key.",
        "priority": 1,
        "memory_ref": "memory/platform_buildout_north_star.md (Phase A gate #5)",
    },
    {
        "key": "report_signing_key",
        "kind": "env",
        "env_var": "REPORT_SIGNING_KEY",
        "title": "REPORT_SIGNING_KEY — HMAC for signed DD reports",
        "why": "Phase A gate #5: enables HMAC-signed shareable DD reports for client delivery.",
        "priority": 1,
        "memory_ref": "memory/platform_buildout_north_star.md (Phase A gate #5)",
    },
    {
        "key": "aria_output_harvest_enabled",
        "kind": "env",
        "env_var": "ARIA_OUTPUT_HARVEST_ENABLED",
        "title": "ARIA_OUTPUT_HARVEST_ENABLED=1 — turn on output harvester after 3-7d validation",
        "why": "Phase A gate #5: gates ingestion of past responses into the training pipeline. Enable only after dry-run review.",
        "priority": 1,
        "memory_ref": "memory/platform_buildout_north_star.md (Phase A gate #5)",
    },
    # ── Billing top-ups ───────────────────────────────────────────────
    {
        "key": "anthropic_topup",
        "kind": "billing",
        "title": "Anthropic billing top-up",
        "why": "Provider in HARD cooldown — chain depth at 1 (DeepSeek only). Restores primary Claude tier.",
        "priority": 1,
        "memory_ref": "memory/next_session_pickup_2026_05_16.md (item 9)",
    },
    {
        "key": "brave_topup",
        "kind": "billing",
        "title": "Brave Search API top-up",
        "why": "Circuit OPEN — search fan-out lost top-tier backend. Free tier 2000/mo, paid extends.",
        "priority": 2,
        "memory_ref": "memory/platform_buildout_north_star.md (operator-pending Day 1)",
    },
    # ── WhatsApp channel mirror ──────────────────────────────────────
    {
        "key": "aria_mirror_groups",
        "kind": "env",
        "env_var": "ARIA_MIRROR_GROUPS",
        "title": "ARIA_MIRROR_GROUPS — WA group IDs to auto-screen",
        "why": "Activates WA counterparty auto-screening (Clause 16 deception detection on overheard messages).",
        "priority": 2,
        "memory_ref": "memory/pending_channel_mirror_activation.md",
    },
    {
        "key": "aria_counterparty_contacts",
        "kind": "env",
        "env_var": "ARIA_COUNTERPARTY_CONTACTS",
        "title": "ARIA_COUNTERPARTY_CONTACTS — phone-number watchlist",
        "why": "Pair with ARIA_MIRROR_GROUPS to scope deception scoring.",
        "priority": 2,
        "memory_ref": "memory/pending_channel_mirror_activation.md",
    },
    {
        "key": "aria_deception_threshold",
        "kind": "env",
        "env_var": "ARIA_DECEPTION_THRESHOLD",
        "title": "ARIA_DECEPTION_THRESHOLD — escalation gate (default 0.65)",
        "why": "Tunes how aggressively deception_detection flags counterparties.",
        "priority": 3,
        "memory_ref": "memory/pending_channel_mirror_activation.md",
    },
    # ── Training capture ─────────────────────────────────────────────
    {
        "key": "aria_chat_train_capture_text",
        "kind": "env",
        "env_var": "ARIA_CHAT_TRAIN_CAPTURE_TEXT",
        "title": "ARIA_CHAT_TRAIN_CAPTURE_TEXT=1 — style-learner training data",
        "why": "Captures raw chat text into training queue. OFF by default for privacy.",
        "priority": 3,
        "memory_ref": "memory/stream_bypass_pattern.md",
    },
    {
        "key": "aria_neural_sample_rate",
        "kind": "env",
        "env_var": "ARIA_NEURAL_SAMPLE_RATE",
        "title": "ARIA_NEURAL_SAMPLE_RATE=0.25 — sub-sample neural ingest",
        "why": "75% LLM cost cut on neural ingest. Code shipped in 4e8e462; env not flipped.",
        "priority": 3,
        "memory_ref": "memory/next_session_pickup_2026_05_11.md (operating env vars)",
    },
    # ── Phase A — Phase B handoff actions ────────────────────────────
    {
        "key": "phase_a_seed_latam_asia",
        "kind": "manual",
        "title": "POST /api/aria/knowledge/seed-latam-asia (R-F141)",
        "why": "Phase A gate #2: lifts heatmap weakest cell from 51% → ≥70%.",
        "priority": 1,
        "memory_ref": "memory/platform_buildout_north_star.md (Phase A gate #2)",
    },
    {
        "key": "phase_a_design_partners",
        "kind": "manual",
        "title": "4 design-partner targets identified (pipeline at data/design_partner_pipeline.json)",
        "why": "Phase A gate #7: B2B sales cycles 3-6 months; warm leads must precede Phase D. 4 targets identified — needs operator to reach out.",
        "priority": 1,
        "memory_ref": "memory/platform_buildout_north_star.md (Phase A gate #7)",
    },
    {
        "key": "phase_a_eval_seed",
        "kind": "manual",
        "title": "500-Q eval set complete (500/500 entries, 52 categories at target) — frozen per Gate #6",
        "why": "Phase A gate #6: 500-Q v1 eval set is complete and frozen. eval_golden_seed.py has 500 entries across all 52 categories at target. No further operator action needed.",
        "priority": 0,
        "memory_ref": "memory/eval_500q_v1_gap_list.md",
    },
]

# Patterns that mark a value as set-but-stale (e.g. dev placeholder).
# Picked to be long enough that legitimate values can't accidentally
# match — R-F562 verify-agent flagged short tokens "todo"/"tbd" as a
# false-positive risk, so we use only long literal placeholders here.
_STALE_PATTERNS = ("changeme", "placeholder", "your-key-here", "your_api_key_here", "replace-me")


def _classify_env(env_var: str) -> tuple[str, str | None]:
    """Return (status, masked_value_for_dashboard).

    Returns ('set' | 'missing' | 'stale', masked_or_None).
    """
    raw = os.environ.get(env_var, "")
    if not raw:
        return "missing", None
    lower = raw.strip().lower()
    if any(p in lower for p in _STALE_PATTERNS):
        return "stale", _mask(raw)
    return "set", _mask(raw)


def _mask(value: str) -> str:
    """Mask all but last 4 chars of a value, for dashboard display."""
    if not value:
        return ""
    if len(value) <= 6:
        return "***"
    return f"{'*' * (len(value) - 4)}{value[-4:]}"


def build_status() -> dict[str, Any]:
    """Return the full pending-action panel payload.

    Shape: {
        counts: {total, missing, stale, set, manual_open, billing_open},
        items: [{...}, ...],
        gate_blockers: [keys that block Phase A gate exit],
    }
    """
    items: list[dict[str, Any]] = []
    counts = {
        "total": 0,
        "missing": 0,
        "stale": 0,
        "set": 0,
        "manual_open": 0,
        "billing_open": 0,
    }
    gate_blockers: list[str] = []

    for entry in _PENDING_CATALOGUE:
        row = dict(entry)  # copy
        counts["total"] += 1
        if entry["kind"] == "env":
            status, masked = _classify_env(entry["env_var"])
            row["status"] = status
            row["value_masked"] = masked
            counts[status] = counts.get(status, 0) + 1
            if status in ("missing", "stale"):
                if entry["priority"] == 1:
                    gate_blockers.append(entry["key"])
        elif entry["kind"] == "billing":
            row["status"] = "manual"
            counts["billing_open"] += 1
            if entry["priority"] == 1:
                gate_blockers.append(entry["key"])
        elif entry["kind"] == "manual":
            row["status"] = "manual"
            counts["manual_open"] += 1
            if entry["priority"] == 1:
                gate_blockers.append(entry["key"])
        items.append(row)

    # Sort: P1 first, then by status (missing first), then key.
    def _sort_key(r: dict) -> tuple[int, int, str]:
        status_order = {"missing": 0, "stale": 1, "manual": 2, "set": 3}
        return (
            int(r.get("priority") or 9),
            status_order.get(r.get("status") or "manual", 5),
            r["key"],
        )

    items.sort(key=_sort_key)

    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="operator_pending",
        summary="Build Status",
        source_id="operator_pending:R-F996",
    )

    return {
        "counts": counts,
        "items": items,
        "gate_blockers": gate_blockers,
        "phase_a_actionable": len(gate_blockers),
    }


def list_keys() -> list[str]:
    """Cheap helper for test contracts."""
    return [e["key"] for e in _PENDING_CATALOGUE]

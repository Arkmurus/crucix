"""Autonomy Surface — what ARIA did vs. what she needs from you.

Aggregates the three views required by the operator per the autonomy
doctrine (memory: aria_autonomy_doctrine.md):

  A. AUTO-ALLOWED fired (last 24h):
     - autonomous task fires, chat turns served, corpus ingests,
       bright-line triggers, audit-log entries

  B. DRAFTS awaiting operator review:
     - pending approvals from constitution, source_validator,
       codegen, golden, ground-truth
     - recent DD reports + writer outputs produced today

  C. OPERATOR queue (needs your touch):
     - OEM contact graph coverage gap
     - env-var gated features (WA channel mirror)
     - bright-lines fired recently — what compliance called out
     - stale / contradicted knowledge facts

Each source is wrapped in try/except so a missing backend (Redis
unreachable, module not yet deployed, etc.) degrades the panel to
zero rather than crashing it.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("aria.intel.autonomy_surface")


# ═══════════════════════════════════════════════════════════════════════
# A. Auto-allowed actions (last 24h)
# ═══════════════════════════════════════════════════════════════════════

async def _auto_allowed_summary() -> dict[str, Any]:
    out: dict[str, Any] = {
        "autonomous_task_fires": 0,
        "chat_turns_served": 0,
        "corpus_ingests": 0,
        "audit_entries": 0,
        "bright_lines_triggered": 0,
        "bright_lines_by_code": {},
    }

    # ── Autonomous engine fires ──
    try:
        from . import redis_store as rs
        fires = await rs.get("crucix:autonomous:fires_24h")
        if fires is not None:
            try:
                out["autonomous_task_fires"] = int(fires)
            except (TypeError, ValueError):
                pass
    except Exception as e:
        logger.debug("autonomous_fires: %s", e)

    # ── Chat audit ──
    try:
        from . import chat_audit_log as cal
        stats = await cal.get_stats()
        out["chat_turns_served"] = int(stats.get("entries_24h", 0)) if isinstance(stats, dict) else 0
        out["audit_entries"] = int(stats.get("total_entries", 0)) if isinstance(stats, dict) else 0
    except Exception as e:
        logger.debug("chat_audit: %s", e)

    # ── Corpus ingests (best-effort) ──
    try:
        from . import redis_store as rs
        ingests = await rs.get("crucix:corpus:ingests_24h")
        if ingests is not None:
            try:
                out["corpus_ingests"] = int(ingests)
            except (TypeError, ValueError):
                pass
    except Exception as e:
        logger.debug("corpus_ingests: %s", e)

    # ── Bright-lines triggered (last 24h) ──
    try:
        from . import regional_bright_lines as rbl
        hits = await rbl.get_hits_24h()
        out["bright_lines_triggered"] = int(hits.get("total", 0))
        out["bright_lines_by_code"] = hits.get("by_code", {})
    except Exception as e:
        logger.debug("bright_lines_hits: %s", e)

    return out


# ═══════════════════════════════════════════════════════════════════════
# B. Drafts awaiting review
# ═══════════════════════════════════════════════════════════════════════

async def _drafts_awaiting() -> dict[str, Any]:
    out: dict[str, Any] = {
        "source_validator_pending": 0,
        "constitution_pending": 0,
        "codegen_pending": 0,
        "golden_pending": 0,
        "ground_truth_pending": 0,
        "dd_reports_today": 0,
        "writer_outputs_today": 0,
        "total_pending": 0,
    }

    # ── Pending-approval queues ──
    try:
        from . import redis_store as rs
        # Generic pattern: each pending queue writes count to a known key
        for key, field in (
            ("crucix:source_validator:pending_count", "source_validator_pending"),
            ("crucix:constitution:pending_count",     "constitution_pending"),
            ("crucix:codegen:pending_count",          "codegen_pending"),
            ("crucix:golden:pending_count",           "golden_pending"),
            ("crucix:ground_truth:pending_count",     "ground_truth_pending"),
        ):
            try:
                val = await rs.get(key)
                if val is not None:
                    out[field] = int(val)
            except Exception:
                continue
    except Exception as e:
        logger.debug("drafts.pending: %s", e)

    # ── DD reports produced today ──
    try:
        from . import redis_store as rs
        from . import dd_orchestrator as dd
        idx = await rs.get_json(getattr(dd, "REPORT_INDEX_KEY", "crucix:dd:report_index"))
        if isinstance(idx, dict):
            today = datetime.now(timezone.utc).date().isoformat()
            items = idx.get("items") or []
            out["dd_reports_today"] = sum(
                1 for it in items
                if isinstance(it, dict)
                and (it.get("run_at") or "").startswith(today)
            )
    except Exception as e:
        logger.debug("dd_reports: %s", e)

    # ── Writer outputs (from WriterAuditLog) ──
    try:
        from pathlib import Path
        log_path = Path(os.getenv("ARIA_WRITER_AUDIT_PATH",
                                  "/data/aria_writer_audit.jsonl"))
        if log_path.exists():
            today = datetime.now(timezone.utc).date().isoformat()
            count = 0
            for line in log_path.read_text(encoding="utf-8").splitlines()[-500:]:
                if today in line:
                    count += 1
            out["writer_outputs_today"] = count
    except Exception as e:
        logger.debug("writer_outputs: %s", e)

    out["total_pending"] = (
        out["source_validator_pending"]
        + out["constitution_pending"]
        + out["codegen_pending"]
        + out["golden_pending"]
        + out["ground_truth_pending"]
    )
    return out


# ═══════════════════════════════════════════════════════════════════════
# C. Operator queue — needs your touch
# ═══════════════════════════════════════════════════════════════════════

async def _operator_queue() -> dict[str, Any]:
    out: dict[str, Any] = {
        "oem_slots_total": 0,
        "oem_slots_filled": 0,
        "oem_slots_empty": 0,
        "oem_worst_oems": [],
        "wa_mirror_gated": False,
        "wa_mirror_missing_env": [],
        "stale_facts": 0,
        "contradicted_facts": 0,
        "bright_lines_recent": [],
    }

    # ── OEM coverage ──
    try:
        from . import oem_contact_graph as oem
        contacts = await oem.get_oem_contacts()
        total = len(contacts or [])
        filled = sum(1 for c in (contacts or []) if c.get("name"))
        out["oem_slots_total"] = total
        out["oem_slots_filled"] = filled
        out["oem_slots_empty"] = max(total - filled, 0)

        # Worst 3 OEMs by coverage
        by_oem: dict[str, dict[str, int]] = {}
        for c in (contacts or []):
            k = c.get("oem", "?")
            s = by_oem.setdefault(k, {"filled": 0, "total": 0})
            s["total"] += 1
            if c.get("name"):
                s["filled"] += 1
        worst = sorted(
            [(k, v) for k, v in by_oem.items() if v["filled"] < v["total"]],
            key=lambda kv: kv[1]["filled"],
        )[:3]
        out["oem_worst_oems"] = [
            {"oem": k, "filled": v["filled"], "total": v["total"]}
            for k, v in worst
        ]
    except Exception as e:
        logger.debug("oem coverage: %s", e)

    # ── WA mirror env-var gate ──
    # Three possible states:
    #   LIVE     — all three env vars set; mirror is running
    #   GATED    — env vars missing; operator should flip on
    #   DEFERRED — env vars missing but ARIA_MIRROR_DEFERRED=1 is set,
    #              which explicitly marks the feature as intentionally
    #              off. Doctrine is satisfied; briefing stops nagging.
    #              Operator flips deferred flag off when ready.
    missing: list[str] = []
    for env in ("ARIA_MIRROR_GROUPS", "ARIA_COUNTERPARTY_CONTACTS",
                "ARIA_DECEPTION_THRESHOLD"):
        if not os.getenv(env):
            missing.append(env)
    deferred = (os.getenv("ARIA_MIRROR_DEFERRED", "") or "").strip().lower() in ("1", "true", "yes", "on")
    out["wa_mirror_missing_env"] = missing
    if not missing:
        out["wa_mirror_status"] = "LIVE"
        out["wa_mirror_gated"] = False
    elif deferred:
        out["wa_mirror_status"] = "DEFERRED"
        out["wa_mirror_gated"] = False   # doctrine-satisfied — not a nag item
    else:
        out["wa_mirror_status"] = "GATED"
        out["wa_mirror_gated"] = True

    # ── Stale / contradicted facts ──
    try:
        from . import verified_intel as vi
        if hasattr(vi, "get_verification_summary"):
            s = await vi.get_verification_summary()
            out["stale_facts"] = int(s.get("stale", 0))
            out["contradicted_facts"] = int(s.get("contradicted", 0))
    except Exception as e:
        logger.debug("verified_intel: %s", e)

    # ── Bright-lines fired — recent list for operator visibility ──
    try:
        from . import regional_bright_lines as rbl
        hits = await rbl.get_hits_24h()
        items = hits.get("items") or []
        out["bright_lines_recent"] = items[-5:]
    except Exception as e:
        logger.debug("bright_lines_recent: %s", e)

    return out


# ═══════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════

async def get_surface() -> dict[str, Any]:
    """Return the full autonomy-surface payload for dashboard + briefing."""
    auto = await _auto_allowed_summary()
    drafts = await _drafts_awaiting()
    queue = await _operator_queue()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "auto_allowed": auto,
        "drafts_awaiting": drafts,
        "operator_queue": queue,
        "doctrine_reference": "memory/aria_autonomy_doctrine.md",
    }


async def build_operator_prompt() -> str:
    """Plain-text block suitable for inclusion in the 05:45 UTC
    WhatsApp team briefing. Surfaces only items that actually need
    operator attention — if everything is clear, returns an empty
    string so the briefing stays tight.

    Designed to read naturally: tells Antonio / the team what was
    automatic last 24h, what's waiting, and what action is needed.
    """
    s = await get_surface()
    auto = s.get("auto_allowed") or {}
    drafts = s.get("drafts_awaiting") or {}
    queue = s.get("operator_queue") or {}

    lines: list[str] = ["\n\n🧭 *AUTONOMY SURFACE*"]

    # A. What ARIA did on its own
    a_bits: list[str] = []
    if auto.get("autonomous_task_fires"):
        a_bits.append(f"{auto['autonomous_task_fires']} auto tasks")
    if auto.get("chat_turns_served"):
        a_bits.append(f"{auto['chat_turns_served']} chat turns")
    if auto.get("corpus_ingests"):
        a_bits.append(f"{auto['corpus_ingests']} ingests")
    if auto.get("bright_lines_triggered"):
        a_bits.append(f"{auto['bright_lines_triggered']} bright-lines fired")
    if a_bits:
        lines.append(f"✅ Auto last 24h: {', '.join(a_bits)}")

    # B. Drafts awaiting review — worth surfacing only if > 0
    d_bits: list[str] = []
    if drafts.get("total_pending"):
        d_bits.append(f"{drafts['total_pending']} approval(s) pending")
    if drafts.get("dd_reports_today"):
        d_bits.append(f"{drafts['dd_reports_today']} DD report(s) today")
    if drafts.get("writer_outputs_today"):
        d_bits.append(f"{drafts['writer_outputs_today']} writer output(s) today")
    if d_bits:
        lines.append(f"📋 Drafts for review: {', '.join(d_bits)}")

    # C. Operator queue — THE action prompt
    q_bits: list[str] = []
    if queue.get("oem_slots_empty"):
        total = queue.get("oem_slots_total", 0)
        empty = queue.get("oem_slots_empty", 0)
        q_bits.append(f"{empty}/{total} OEM contact slots still empty")
        worst = queue.get("oem_worst_oems") or []
        if worst:
            names = ", ".join(w["oem"] for w in worst[:3])
            q_bits.append(f"priority OEMs to fill: {names}")
    # WA mirror — only surfaced when GATED (not when DEFERRED or LIVE).
    # Doctrine: if the operator has explicitly deferred, we do NOT nag.
    if queue.get("wa_mirror_gated"):
        miss = queue.get("wa_mirror_missing_env") or []
        q_bits.append(
            f"WA counterparty mirror OFF — set {', '.join(miss[:2])} on seenode"
        )
    if queue.get("stale_facts"):
        q_bits.append(f"{queue['stale_facts']} stale fact(s) need re-verify")
    if queue.get("contradicted_facts"):
        q_bits.append(f"{queue['contradicted_facts']} contradicted fact(s) to resolve")
    # Include top-level bright-line code names so the team knows what was flagged
    bl_by_code = auto.get("bright_lines_by_code") or {}
    if bl_by_code:
        top = sorted(bl_by_code.items(), key=lambda kv: -kv[1])[:2]
        q_bits.append(
            "bright-lines flagged: " + ", ".join(f"{c} ×{n}" for c, n in top)
        )
    if q_bits:
        lines.append("⚠️ Action queue:")
        for q in q_bits:
            lines.append(f"   • {q}")

    # If nothing to show, signal a clean morning
    if len(lines) == 1:
        return ""
    lines.append("🔗 Full view: /aria-brain.html#autonomy-surface")
    return "\n".join(lines)

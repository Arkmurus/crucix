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

from . import redis_store as rs

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
    # Previously read `crucix:corpus:ingests_24h` — a key nothing ever
    # writes; the counter stayed at 0 since genesis despite
    # knowledge_spider happily ingesting (49 ingests in the last 24h on
    # 2026-04-24). The spider publishes its counters via
    # knowledge_spider.get_stats() which returns `ingests_24h`.
    try:
        from ..learning import knowledge_spider as _ks
        if hasattr(_ks, "get_stats"):
            spider_stats = await _ks.get_stats()
            if isinstance(spider_stats, dict):
                out["corpus_ingests"] = int(spider_stats.get("ingests_24h", 0) or 0)
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
        "adversarial_amendments_pending": 0,
        "codegen_pending": 0,
        "golden_pending": 0,
        "ground_truth_pending": 0,
        "dd_reports_today": 0,
        "writer_outputs_today": 0,
        "total_pending": 0,
    }

    # ── Pending-approval queues ──
    # The original design assumed each module wrote a `crucix:*:pending_count`
    # counter key. None of the source modules actually do that -- they write
    # LIST queues under `aria:<module>:pending`. The counter reads always
    # returned None, so source_validator_pending / golden_pending have been
    # stuck at 0 on every dashboard since the panel shipped. Read the real
    # list keys instead, fall back to the legacy counter keys for any module
    # that DOES write them.
    try:
        from . import redis_store as rs
        list_sources = (
            ("aria:source_validator:pending", "source_validator_pending"),
            ("aria:golden_autogen:pending",   "golden_pending"),
        )
        for key, field in list_sources:
            try:
                items = await rs.get_json(key) or []
                if isinstance(items, list):
                    out[field] = len(items)
            except Exception:
                continue
        counter_sources = (
            ("crucix:source_validator:pending_count", "source_validator_pending"),
            ("crucix:constitution:pending_count",     "constitution_pending"),
            ("crucix:codegen:pending_count",          "codegen_pending"),
            ("crucix:golden:pending_count",           "golden_pending"),
            ("crucix:ground_truth:pending_count",     "ground_truth_pending"),
        )
        for key, field in counter_sources:
            try:
                val = await rs.get(key)
                if val is not None and out.get(field, 0) == 0:
                    out[field] = int(val)
            except Exception:
                continue
    except Exception as e:
        logger.debug("drafts.pending: %s", e)

    # ── Adversarial amendments queue ──
    # Tracked under a different namespace (`aria:adversarial:amendments_queue`,
    # LIST not counter). Surface it in the same drafts panel so a
    # non-zero adversarial queue can't silently coexist with a zero
    # constitution_pending count — the exact confusion the 2026-04-23
    # session chased when the retired "12 pending" figure got compared
    # against a healthy `/constitution/pending=0`.
    try:
        from . import redis_store as rs
        amendments = await rs.get_json("aria:adversarial:amendments_queue") or []
        if isinstance(amendments, list):
            out["adversarial_amendments_pending"] = len(amendments)
    except Exception as e:
        logger.debug("drafts.adversarial_amendments: %s", e)

    # ── DD reports produced today ──
    # dd_orchestrator writes a LIST of dicts each with `generated_at`
    # (not a dict with items + run_at). Handle both shapes defensively
    # in case storage format changes.
    try:
        from . import redis_store as rs
        from . import dd_orchestrator as dd
        idx = await rs.get_json(getattr(dd, "REPORT_INDEX_KEY", "crucix:dd:report_index"))
        if isinstance(idx, list):
            items = idx
        elif isinstance(idx, dict):
            items = idx.get("items") or []
        else:
            items = []
        today = datetime.now(timezone.utc).date().isoformat()
        out["dd_reports_today"] = sum(
            1 for it in items
            if isinstance(it, dict)
            and (
                (it.get("generated_at") or "").startswith(today)
                or (it.get("run_at") or "").startswith(today)
            )
        )
    except Exception as e:
        logger.debug("dd_reports: %s", e)

    # ── Writer outputs (from WriterAuditLog) ──
    # Also counts how many of today's outputs were produced on DEGRADED
    # fallback — useful signal for the dashboard during Anthropic cooldown.
    try:
        from pathlib import Path
        import json as _json
        log_path = Path(os.getenv("ARIA_WRITER_AUDIT_PATH",
                                  "/data/aria_writer_audit.jsonl"))
        if log_path.exists():
            today = datetime.now(timezone.utc).date().isoformat()
            count = 0
            degraded_count = 0
            for line in log_path.read_text(encoding="utf-8").splitlines()[-500:]:
                if today not in line:
                    continue
                count += 1
                try:
                    entry = _json.loads(line)
                    md = entry.get("metadata") or {}
                    if md.get("degraded") is True:
                        degraded_count += 1
                except Exception:
                    continue
            out["writer_outputs_today"] = count
            out["writer_outputs_degraded_today"] = degraded_count
    except Exception as e:
        logger.debug("writer_outputs: %s", e)

    out["total_pending"] = (
        out["source_validator_pending"]
        + out["constitution_pending"]
        + out["adversarial_amendments_pending"]
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

async def _resilience_floor() -> dict[str, Any]:
    """Report ARIA's "never dies" resilience floor — provider chain
    health + local_brain availability + memory durability.

    Rationale: the operator directive is "brain never dies, memory
    never wipes, learning never stops". This surface makes each of
    those invariants directly observable.
    """
    out: dict[str, Any] = {
        "providers": [],
        "providers_active": 0,
        "providers_configured": 0,
        "providers_cooling": 0,
        "local_brain_ready": False,
        "local_brain_invocations_24h": 0,
        "memory": {
            "redis_reachable": False,
            "rag_chunks": 0,
            "mem0_facts": 0,
            "claim_ledger_entries": 0,
        },
        "resilience_count": 0,
        "verdict": "unknown",
    }

    # ── Provider chain health ──
    try:
        import os
        provider_keys = {
            "anthropic": os.getenv("ANTHROPIC_API_KEY", ""),
            "deepseek":  os.getenv("DEEPSEEK_API_KEY", ""),
            "groq":      os.getenv("GROQ_API_KEY", ""),
            "openai":    os.getenv("OPENAI_API_KEY", ""),
            "gemini":    os.getenv("GEMINI_API_KEY", ""),
            "openrouter": os.getenv("OPENROUTER_API_KEY", ""),
            "mistral":   os.getenv("MISTRAL_API_KEY", ""),
        }
        configured = [n for n, k in provider_keys.items() if k]
        out["providers_configured"] = len(configured)

        # Cooldown state — pull live from the FallbackProvider's in-process
        # stats. The previous implementation read `crucix:llm:<name>:cooldown_until`
        # from Redis, but fallback.py only ever writes cooldowns into its
        # instance dict (see fallback.py:80,86,94), so the Redis key was
        # never populated and every provider showed "active" even when one
        # was billing-cooled. Now: read get_stats() off the live provider.
        live_stats: dict = {}
        try:
            from ..main import app as _app
            llm_provider = getattr(getattr(_app, "state", None), "llm_provider", None)
            # Walk past wrappers (cost_meter, rate_limiter) to the FallbackProvider
            inner = llm_provider
            while inner is not None and not hasattr(inner, "get_stats"):
                inner = getattr(inner, "inner", None) or getattr(inner, "_inner", None) or getattr(inner, "wrapped", None)
            if inner is not None and hasattr(inner, "get_stats"):
                stats_fn = inner.get_stats
                if callable(stats_fn):
                    raw = stats_fn()
                    if isinstance(raw, dict):
                        live_stats = raw
        except Exception as _stats_err:
            logger.debug("[autonomy_surface] live LLM stats probe failed: %s", _stats_err)

        now_ts = datetime.now(timezone.utc).timestamp()
        for name in configured:
            try:
                s = live_stats.get(name) or {}
                cd = float(s.get("cooldown_until") or 0)
                cooling = cd > now_ts
                status = "cooling" if cooling else "active"
                out["providers"].append({"name": name, "status": status,
                                         "reliability": s.get("reliability"),
                                         "calls": s.get("calls", 0),
                                         "failures": s.get("failures", 0)})
                if cooling:
                    out["providers_cooling"] += 1
                else:
                    out["providers_active"] += 1
            except Exception:
                out["providers"].append({"name": name, "status": "unknown"})

    except Exception as e:
        logger.debug("provider chain probe failed: %s", e)

    # ── Local-brain readiness ──
    # local_brain is rule-based Python + local data; it's ready iff the
    # module imports cleanly and its dependent stores (knowledge cache,
    # neural_memory) are reachable. Cheap to probe.
    try:
        from . import local_brain  # noqa: F401
        out["local_brain_ready"] = True
        invocations = await rs.get("crucix:local_brain:invocations_24h")
        if invocations is not None:
            try:
                out["local_brain_invocations_24h"] = int(invocations)
            except (TypeError, ValueError):
                pass
    except Exception as e:
        logger.debug("local_brain probe failed: %s", e)
        out["local_brain_ready"] = False

    # ── Memory durability ──
    try:
        # Redis reachable
        _ping = await rs.get("crucix:aria:health_ping")
        await rs.set("crucix:aria:health_ping", "ok", ex=60)
        out["memory"]["redis_reachable"] = True
    except Exception as e:
        logger.debug("redis probe failed: %s", e)

    try:
        from . import rag_store as _rag
        if hasattr(_rag, "get_stats"):
            s = await _rag.get_stats()
            out["memory"]["rag_chunks"] = int(s.get("total_chunks", 0)) if isinstance(s, dict) else 0
    except Exception:
        pass

    try:
        from . import knowledge as _kb
        _c = getattr(_kb, "_cache", None) or {}
        facts = _c.get("facts", []) if isinstance(_c, dict) else []
        out["memory"]["mem0_facts"] = sum(
            1 for f in facts
            if isinstance(f, dict) and (f.get("source") or "").startswith("mem0:")
        )
    except Exception:
        pass

    try:
        from . import counterparty_claim_ledger  # noqa: F401
        # Exact count requires iteration; skip for speed — presence is enough.
        out["memory"]["claim_ledger_entries"] = -1  # "available, count not probed"
    except Exception:
        pass

    # ── Resilience count ──
    # How many independent fallback paths are available RIGHT NOW?
    # Active providers + local_brain (always +1 if ready).
    #
    # A cooling provider is NOT a resilience loss — the fallback chain is
    # exactly designed to route around it. So we deliberately count only
    # `providers_active` (which excludes cooling) and leave `providers_cooling`
    # as operator-visible detail, not a verdict input.
    out["resilience_count"] = out["providers_active"] + (1 if out["local_brain_ready"] else 0)

    # Load-bearing "am I broken" signal. Used by /health, meta_query, and
    # any consumer that needs a boolean "can ARIA serve the next request?".
    # Stays True when Anthropic (or any N-1 providers) is on hard cooldown
    # so long as at least one other provider is still active.
    out["chain_resilient"] = out["providers_active"] >= 1
    out["chain_fallback_engaged"] = (
        out["providers_cooling"] > 0 and out["providers_active"] >= 1
    )

    # Verdict ladder
    if out["resilience_count"] >= 3:
        out["verdict"] = "ROBUST"
    elif out["resilience_count"] == 2:
        out["verdict"] = "ADEQUATE"
    elif out["resilience_count"] == 1:
        out["verdict"] = "SINGLE_POINT"
    else:
        out["verdict"] = "CRITICAL"

    return out


async def get_surface() -> dict[str, Any]:
    """Return the full autonomy-surface payload for dashboard + briefing."""
    auto = await _auto_allowed_summary()
    drafts = await _drafts_awaiting()
    queue = await _operator_queue()
    resilience = await _resilience_floor()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "auto_allowed": auto,
        "drafts_awaiting": drafts,
        "operator_queue": queue,
        "resilience": resilience,
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
    resilience = s.get("resilience") or {}

    lines: list[str] = ["\n\n🧭 *AUTONOMY SURFACE*"]

    # Resilience — shown only when degraded (ROBUST = silent).
    # "Brain never dies" mandate: we want the operator to notice
    # BEFORE we hit the floor, so SINGLE_POINT and CRITICAL must
    # always surface.
    verdict = (resilience.get("verdict") or "").upper()
    if verdict in ("SINGLE_POINT", "CRITICAL", "ADEQUATE"):
        count = resilience.get("resilience_count", 0)
        active = resilience.get("providers_active", 0)
        cooling = resilience.get("providers_cooling", 0)
        local = resilience.get("local_brain_ready", False)
        lines.append(
            f"🛡️ Resilience: *{verdict}* — "
            f"{count} path(s): {active} provider(s) active, "
            f"{cooling} cooling, local brain {'READY' if local else 'OFF'}"
        )
        if verdict in ("SINGLE_POINT", "CRITICAL"):
            lines.append(
                "   Action: operator should check provider health / "
                "top up billing before the next outage."
            )
    lb_fires = resilience.get("local_brain_invocations_24h", 0)
    if lb_fires >= 5:
        lines.append(
            f"⚠️ Local brain fired {lb_fires}× in 24h — the LLM chain is "
            f"degraded more often than normal. Investigate providers."
        )

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

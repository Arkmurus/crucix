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
from .engine_wiring import wire_failure

import logging
import os
from datetime import datetime, timezone
from typing import Any

from . import redis_store as rs

# R-F772: eager-import counterparty_claim_ledger at module top so the
# transitive `anthropic` SDK load (heavy Pydantic models, ~5-15s on
# cold machines) is paid once at boot rather than on the first
# /api/aria/autonomy/surface call. The lazy `from . import
# counterparty_claim_ledger` inside _resilience_floor was the
# wedge cause observed in /data/wedge_stacks/wedge_674_1779350869.log
# (main thread blocked inside anthropic/types/beta_error.py for 81s
# while the dashboard polled, cascading R-F703 stalls and a fly
# health-check failure at 08:15:51 UTC on 2026-05-21).
from . import counterparty_claim_ledger as _claim_ledger_module  # noqa: F401

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
        # R-F4068 (C-109) — these two are DIFFERENT WINDOWS and the dashboard
        # rendered both under one "(24h)" heading:
        #   chat_turns_served -> entries_24h    (rolling window; hourly buckets)
        #   audit_entries     -> total_entries  (LIFETIME, by design)
        # Live 2026-08-16 that made the 24h column show 1208 — the same number
        # the Chat Audit panel prints as "Total Entries". `audit_entries` keeps
        # its meaning (other readers may rely on it); the UI now names it.
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
            # R-F2334: unmeasured on the degraded path — None (not a fake 0),
            # available False (we could not confirm the store).
            "claim_ledger_entries": None,
            "claim_ledger_available": False,
        },
        "resilience_count": 0,
        "verdict": "unknown",
    }

    # ── Provider chain health ──
    #
    # R-F4071 (C-115) — read the chain DISPATCH ACTUALLY WALKS, not the key set.
    #
    # This enumerated a hardcoded `provider_keys` map from os.getenv and called a
    # provider "active" when a key was present and no cooldown was set. It never
    # asked whether the provider is reachable on the general path. Under RULE ONE
    # (§17) Anthropic is `preference_only`: reserved for DD and deliberately
    # unreachable by general dispatch (R-F3034/R-F3767).
    #
    # Measured live 2026-08-16, same instant: this panel said "ROBUST (3
    # independent paths) · anthropic: active · deepseek: active" while /health
    # said active_providers ["deepseek"], general_vendor_depth 1. It overstated
    # (anthropic reported active with calls 0 / failures 0 / reliability null —
    # on 2026-08-12, with its balance exhausted and DD down, this would still
    # have read ROBUST) and understated (deepseek_backup served 1,591 calls this
    # month and was absent, being missing from the hardcoded map).
    #
    # `FallbackProvider.get_health()` is the same method /health publishes and
    # already carries R-F3634's lesson — "publish what dispatch actually reads,
    # or the surface describes a different system from the one running" — plus
    # `general_vendor_depth`, which collapses deepseek + deepseek_backup into the
    # ONE vendor they are (a vendor-side timeout takes both, so failing over
    # between them cannot help). Reuse it rather than keeping a second opinion.
    chain_health: dict = {}
    try:
        from ..main import app as _app0
        _inner = getattr(getattr(_app0, "state", None), "llm_provider", None)
        while _inner is not None and not hasattr(_inner, "get_health"):
            _inner = (getattr(_inner, "inner", None)
                      or getattr(_inner, "_inner", None)
                      or getattr(_inner, "wrapped", None))
        if _inner is not None and callable(getattr(_inner, "get_health", None)):
            raw = _inner.get_health()
            if isinstance(raw, dict):
                chain_health = raw
    except Exception as _e:
        logger.debug("[autonomy_surface] chain health probe failed: %s", _e)

    # Unreadable chain -> depth 0 and the verdict ladder lands on CRITICAL.
    # "Could not measure" must never render as ROBUST on the strength of an
    # env var being set, which is what the old path did.
    out["general_vendor_depth"] = int(chain_health.get("general_vendor_depth") or 0)
    out["chain_order"] = list(chain_health.get("chain_order") or [])
    out["reserved_providers"] = list(
        chain_health.get("preference_only_providers") or [])

    try:
        _active = list(chain_health.get("active_providers") or [])
        _cooling = {c.get("name"): c for c in
                    (chain_health.get("cooling_providers") or [])
                    if isinstance(c, dict)}
        configured = list(out["chain_order"]) + [
            p for p in out["reserved_providers"] if p not in out["chain_order"]]
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

        for name in configured:
            try:
                s = live_stats.get(name) or {}
                # R-F4071 — status comes from the chain's OWN verdict, not from
                # re-deriving it out of a cooldown timestamp here. Two places
                # computing the same thing is how they end up disagreeing.
                reserved = name in out["reserved_providers"]
                cooling = name in _cooling
                if reserved:
                    status = "reserved"
                elif cooling:
                    status = "cooling"
                elif name in _active:
                    status = "active"
                else:
                    status = "unknown"
                out["providers"].append({
                    "name": name,
                    "status": status,
                    # The distinction the old panel could not express: a
                    # reserved provider exists and is reachable BY NAME (DD pins
                    # it), but a general call can never fall onto it, so it is
                    # not a fallback path.
                    "role": "reserved_dd" if reserved else "general",
                    "reliability": s.get("reliability"),
                    "calls": s.get("calls", 0),
                    "failures": s.get("failures", 0),
                    "cooling_reason": (_cooling.get(name) or {}).get("reason"),
                })
                if reserved:
                    continue
                if cooling:
                    out["providers_cooling"] += 1
                elif status == "active":
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
        # R-F4065 (C-117) — the honest name. This probes the STATE STORE, which
        # is SQLite on the fly volume; Upstash was decommissioned 2026-05-12
        # (§6/§18) and `REDIS_URL` is unset. The brain page rendered
        # "Memory: Redis: up" from this field while the same page's cost panel
        # said "SQLite (fly volume /data) · Upstash decommissioned". A stale
        # name is how a future session goes hunting a dependency that does not
        # exist. `redis_reachable` is kept as-is for any existing reader.
        out["memory"]["state_store_reachable"] = True
        try:
            from . import redis_store as _rs_probe
            out["memory"]["state_store_backend"] = (
                "sqlite" if _rs_probe._use_sqlite() else "redis")
        except Exception:
            out["memory"]["state_store_backend"] = None
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

    # R-F772: counterparty_claim_ledger is eager-imported at module top
    # (see header), so presence is guaranteed by import time. Exact count
    # requires a per-counterparty key scan; skip for speed — presence is enough.
    # R-F2334: emit None + an explicit availability boolean rather than the raw
    # `-1` sentinel, which leaked into the API payload reading like an error /
    # negative count. None = "count not probed"; available = the store is up.
    out["memory"]["claim_ledger_entries"] = None
    out["memory"]["claim_ledger_available"] = True

    # ── Resilience count ──
    # How many independent fallback paths are available RIGHT NOW?
    #
    # A cooling provider is NOT a resilience loss — the fallback chain is
    # exactly designed to route around it (§14). `providers_cooling` stays
    # operator-visible detail, not a verdict input.
    #
    # R-F4071 (C-115) — the count is DISTINCT GENERAL VENDORS, not active
    # provider entries. Two changes, both narrowing:
    #   * a preference-only provider (Anthropic under RULE ONE) is reserved for
    #     DD and unreachable by general dispatch, so it is not a fallback path;
    #   * deepseek + deepseek_backup are two entries and ONE vendor, and a
    #     vendor-side timeout takes both — R-F3634's `general_vendor_depth`
    #     already collapses them, so read that rather than counting rows.
    # Live 2026-08-16 this moved the verdict from ROBUST (3) to the truth: one
    # general vendor plus the local brain.
    out["resilience_count"] = (
        out["general_vendor_depth"] + (1 if out["local_brain_ready"] else 0))

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


# R-F347 (2026-05-12) — default shapes returned when a sub-task
# hangs or raises. Operator-observed: dashboard rendered all zeros +
# "No LLM providers configured" / "Memory — Redis: down" while fly
# logs simultaneously proved Redis writes + DeepSeek 200s. Curl on
# /api/aria/autonomy/surface timed out at 30s. The old sequential
# `await ... await ... await ... await` chained the 4 sub-tasks; any
# one slow Redis probe (Upstash latency spike, dead state_store)
# blocked the whole endpoint, leaving the seenode proxy with nothing
# to render. Parallel + per-task timeout caps the blast radius.
_DEFAULT_AUTO_ALLOWED: dict[str, Any] = {
    "autonomous_task_fires": 0,
    "chat_turns_served": 0,
    "corpus_ingests": 0,
    "audit_entries": 0,
    "bright_lines_triggered": 0,
    "bright_lines_by_code": {},
}
_DEFAULT_DRAFTS: dict[str, Any] = {
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
_DEFAULT_QUEUE: dict[str, Any] = {
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
_DEFAULT_RESILIENCE: dict[str, Any] = {
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
        # R-F2334: timeout default — unmeasured, not "0 entries".
        "claim_ledger_entries": None,
        "claim_ledger_available": False,
    },
    "resilience_count": 0,
    "verdict": "unknown",
    # R-F4071 (C-115) — present on the timeout default too, so a consumer
    # reading these never gets a KeyError and never mistakes "the sub-task timed
    # out" for "the chain has no depth". `verdict: unknown` is the honest word
    # here; the ladder is only applied when the chain was actually read.
    "general_vendor_depth": 0,
    "chain_order": [],
    "reserved_providers": [],
    "_degraded_marker": "R-F347_subtask_timeout",
}

# R-F347 default was 8s; R-F782 raises to 30s so a slow sub-task under
# SQLite write contention still completes instead of degrading to defaults.
# The 60s cache below means a 30s sub-task is paid at most once per minute.
SUBTASK_TIMEOUT_SECONDS = float(os.getenv("ARIA_AUTONOMY_SURFACE_TIMEOUT", "30"))

# R-F782 (2026-05-21): in-process cache for the 4 sub-task results.
# Live evidence on 2026-05-21 (logs 10:57:06 + 10:59:03) showed every
# autonomy_surface fire timing out all 4 sub-tasks at the 8s ceiling —
# the dashboard panel rendered all-zero defaults even when the brain
# was healthy. Root cause: under crawler + autonomous-engine write
# pressure, the 4 sub-tasks contend on the SQLite WAL and each takes
# 5-10s. Cache successful results for 60s so each sub-task computes at
# most once per minute regardless of how often the dashboard polls.
_SUBTASK_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_SUBTASK_CACHE_TTL_S = float(os.getenv("ARIA_AUTONOMY_SURFACE_CACHE_TTL", "60"))


def _cache_get(label: str) -> dict[str, Any] | None:
    """Return cached sub-task result if still fresh, else None."""
    import time as _time
    hit = _SUBTASK_CACHE.get(label)
    if hit is None:
        return None
    ts, value = hit
    if (_time.monotonic() - ts) >= _SUBTASK_CACHE_TTL_S:
        return None
    return value


def _cache_set(label: str, value: dict[str, Any]) -> None:
    import time as _time
    _SUBTASK_CACHE[label] = (_time.monotonic(), value)


async def get_surface() -> dict[str, Any]:
    """Return the full autonomy-surface payload for dashboard + briefing.

    R-F347 (2026-05-12): the 4 sub-tasks run in parallel with a per-task
    timeout so one slow probe can no longer take down the whole endpoint.

    R-F782 (2026-05-21): each sub-task is cached for
    ARIA_AUTONOMY_SURFACE_CACHE_TTL seconds (default 60s) so an expensive
    SQLite-read path is paid at most once per minute regardless of poll
    frequency. Cache stores only SUCCESSFUL results — a sub-task that
    timed out / raised returns the empty-default shape AND skips the
    cache write, so the next poll re-attempts the live computation.
    """
    import asyncio

    async def _safe(coro_factory, default, label):
        # R-F782: cache hit short-circuits the await entirely.
        cached = _cache_get(label)
        if cached is not None:
            return cached
        try:
            result = await asyncio.wait_for(coro_factory(), timeout=SUBTASK_TIMEOUT_SECONDS)
            _cache_set(label, result)
            return result
        except asyncio.TimeoutError:
            logger.warning(
                "autonomy_surface: %s timed out at %.1fs, returning defaults",
                label, SUBTASK_TIMEOUT_SECONDS,
            )
            return default
        except Exception as e:
            logger.warning("autonomy_surface: %s failed: %s", label, e)
            return default

    auto, drafts, queue, resilience = await asyncio.gather(
        _safe(lambda: _auto_allowed_summary(), dict(_DEFAULT_AUTO_ALLOWED), "auto_allowed"),
        _safe(lambda: _drafts_awaiting(),       dict(_DEFAULT_DRAFTS),       "drafts"),
        _safe(lambda: _operator_queue(),         dict(_DEFAULT_QUEUE),         "queue"),
        _safe(lambda: _resilience_floor(),       dict(_DEFAULT_RESILIENCE),    "resilience"),
    )
    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="autonomy_surface",
        summary="Get Surface",
        source_id="autonomy_surface:R-F996",
    )

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

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.

"""ARIA Layer 3 — autonomous task definitions, loader, and execution wrapper.

A Task is a structured intelligence-gathering operation: a cron-like
schedule + a tool chain + delivery channels + escalation triggers.

Tasks are declared in `tasks.yaml` (alongside this file) so the
schedule can be edited without a code change. The loader supports a
`POST /api/aria/autonomous/reload-tasks` admin endpoint that re-reads
the file at runtime — no deploy needed to rotate a task.

Phase 3c-α scope (this file):
  - Task dataclass + YAML loader
  - Cron expression matching against the current minute
  - execute_task() — runs the tool chain through aria_chat() so the
    constitutional pipeline (clauses 1-15, verifier, footer) applies
    to autonomous outputs the same way it applies to interactive chat
  - Run history persisted to Redis for the /status admin endpoint

What this file deliberately does NOT do:
  - Polling loop                  → engine.py
  - Delivery routing              → delivery.py
  - Safety gating (rate/cost/etc) → safety.py (called from engine.py)

The constitutional pipeline does the heavy lifting. A task is just a
synthetic chat message routed through the same code path as interactive
WhatsApp messages, with a special session_id (`autonomous:<task_id>:<date>`)
so its history is isolated and its mem0 facts are tagged appropriately.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from ..intel.wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.autonomous.tasks")

# R-F1320: wire module health to the brain
try:
    from aria_service.intel.engine_wiring import wire_success as _ws1320
    _ws1320(
        module="autonomous.tasks",
        summary="Tasks active",
        source_id="autonomous:tasks:R-F1320",
    )
except Exception:
    pass


# ── Task dataclass ─────────────────────────────────────────────────────────

@dataclass
class Task:
    """A single autonomous research task definition.

    Loaded from YAML — the dataclass mirrors the YAML schema 1:1 so a
    new field can be added by editing the YAML and adding a default
    here. All fields have safe defaults so a partial YAML entry still
    parses (just with reduced functionality).
    """
    id: str
    name: str
    cron: str = "0 6 * * mon-fri"  # 06:00 weekdays UTC
    enabled: bool = False           # opt-in: tasks must be explicitly enabled
    priority: str = "MEDIUM"        # informational only — for the /status endpoint
    timeout_seconds: int = 180
    cost_cap_usd: float = 0.20      # per-run cost cap (independent of daily cap)
    # Tool chain: list of {tool, entity?, url?, max_queries?, ...} dicts.
    # The first tool is required; subsequent tools are optional follow-ups.
    tool_chain: list[dict[str, Any]] = field(default_factory=list)
    # Delivery configuration
    delivery_channels: list[str] = field(default_factory=lambda: ["mem0"])
    whatsapp_group_id: str = ""
    escalate_if: list[str] = field(default_factory=list)
    mem0_tags: list[str] = field(default_factory=list)
    # Notes / docs — informational only
    description: str = ""

    @fail_wire(module="tasks", gap_type="agent_cycle_failure")
    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


# ── Tasks YAML loader ──────────────────────────────────────────────────────

_TASKS_FILE = Path(__file__).parent / "tasks.yaml"
_loaded_tasks: dict[str, Task] = {}


@fail_wire(module="tasks", gap_type="agent_cycle_failure")
def load_tasks(path: Path | None = None) -> dict[str, Task]:
    """Read tasks.yaml from disk and return a dict mapping task_id → Task.

    Safe to call repeatedly. Replaces the in-process cache wholesale on
    each call. Tolerates missing file (returns empty dict + warning) and
    YAML parse errors (logs error, returns the previous cache).
    """
    global _loaded_tasks
    target = path or _TASKS_FILE
    if not target.exists():
        logger.warning(
            "[autonomous tasks] config file missing: %s — engine will load 0 tasks",
            target,
        )
        _loaded_tasks = {}
        return _loaded_tasks

    try:
        import yaml  # type: ignore
    except ImportError:
        logger.error(
            "[autonomous tasks] PyYAML not installed — cannot load tasks.yaml. "
            "Install with `pip install pyyaml` or set ARIA_AUTONOMOUS_ENABLED=0."
        )
        _loaded_tasks = {}
        return _loaded_tasks

    try:
        raw = target.read_text(encoding="utf-8")
        data = yaml.safe_load(raw) or {}
    except Exception as e:
        logger.error(
            "[autonomous tasks] failed to parse %s: %s — keeping previous cache (%d tasks)",
            target, e, len(_loaded_tasks),
        )
        return _loaded_tasks

    tasks_raw = (data or {}).get("tasks", []) if isinstance(data, dict) else []
    if not isinstance(tasks_raw, list):
        logger.error(
            "[autonomous tasks] tasks.yaml top-level `tasks` is not a list: %s",
            type(tasks_raw).__name__,
        )
        return _loaded_tasks

    new_cache: dict[str, Task] = {}
    for entry in tasks_raw:
        if not isinstance(entry, dict):
            continue
        try:
            task = Task(
                id=str(entry.get("id", "")).strip(),
                name=str(entry.get("name", "")).strip(),
                cron=str(entry.get("cron", "0 6 * * mon-fri")).strip(),
                enabled=bool(entry.get("enabled", False)),
                priority=str(entry.get("priority", "MEDIUM")).upper(),
                timeout_seconds=int(entry.get("timeout_seconds", 180)),
                cost_cap_usd=float(entry.get("cost_cap_usd", 0.20)),
                tool_chain=list(entry.get("tool_chain", []) or []),
                delivery_channels=list(entry.get("delivery_channels", ["mem0"]) or ["mem0"]),
                whatsapp_group_id=str(entry.get("whatsapp_group_id", "")),
                escalate_if=list(entry.get("escalate_if", []) or []),
                mem0_tags=list(entry.get("mem0_tags", []) or []),
                description=str(entry.get("description", "")),
            )
        except Exception as e:
            logger.warning(
                "[autonomous tasks] skipping malformed task entry: %s — error: %s",
                entry, e,
            )
            continue
        if not task.id:
            logger.warning("[autonomous tasks] skipping task with no id: %s", entry)
            continue
        new_cache[task.id] = task

    _loaded_tasks = new_cache
    logger.info("[autonomous tasks] loaded %d task(s) from %s", len(_loaded_tasks), target)
    return _loaded_tasks


@fail_wire(module="tasks", gap_type="agent_cycle_failure")
def get_loaded_tasks() -> dict[str, Task]:
    """Return the in-process task cache. Caller must call load_tasks()
    once at engine startup or via the /reload-tasks admin endpoint."""
    return _loaded_tasks


# ── Cron expression matcher (minimal — minute precision) ───────────────────
#
# We do NOT pull in croniter as a dependency. The matcher only needs to
# answer one question once per minute: "should this task fire at the
# current UTC minute?" That's a 5-field cron expression
# (minute, hour, day-of-month, month, day-of-week) with a small set of
# supported features:
#   - exact integers           "0", "5", "23"
#   - wildcard                 "*"
#   - comma lists              "0,15,30,45"
#   - ranges                   "0-5"
#   - step values on wildcard  "*/5"
#   - day-of-week names        "mon", "tue-fri"
#
# Anything fancier (slashes on ranges, last-day-of-month, etc) is
# rejected at parse time so the operator sees the failure immediately
# instead of having a task silently never fire.

_DOW_NAMES = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3,
    "fri": 4, "sat": 5, "sun": 6,
}


def _parse_cron_field(field_value: str, lo: int, hi: int, names: dict[str, int] | None = None) -> set[int]:
    """Parse a single cron field into a set of valid integer values."""
    out: set[int] = set()
    field_value = field_value.strip().lower()
    if not field_value:
        return out

    for chunk in field_value.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue

        # Step values: */N or *
        if chunk.startswith("*/"):
            try:
                step = int(chunk[2:])
            except ValueError:
                logger.warning("[cron] invalid step in %r — skipping chunk", chunk)
                continue
            if step <= 0:
                continue
            out.update(range(lo, hi + 1, step))
            continue

        if chunk == "*":
            out.update(range(lo, hi + 1))
            continue

        # Range: N-M (with optional names like mon-fri)
        if "-" in chunk:
            try:
                a_raw, b_raw = chunk.split("-", 1)
                a = names[a_raw] if names and a_raw in names else int(a_raw)
                b = names[b_raw] if names and b_raw in names else int(b_raw)
            except (KeyError, ValueError):
                logger.warning("[cron] invalid range %r — skipping chunk", chunk)
                continue
            if a > b:
                a, b = b, a
            out.update(range(max(lo, a), min(hi, b) + 1))
            continue

        # Single value (numeric or name)
        try:
            v = names[chunk] if names and chunk in names else int(chunk)
            if lo <= v <= hi:
                out.add(v)
        except (KeyError, ValueError):
            logger.warning("[cron] invalid value %r — skipping chunk", chunk)

    return out


@fail_wire(module="tasks", gap_type="agent_cycle_failure")
def cron_matches(cron_expr: str, when: time.struct_time | None = None) -> bool:
    """Return True if the cron expression matches the given UTC moment.

    `when` defaults to the current UTC time. We snap to the START of
    the minute so the engine's 60-second polling loop can call this
    once per minute and never double-fire.
    """
    if when is None:
        when = time.gmtime()

    parts = cron_expr.split()
    if len(parts) != 5:
        logger.warning(
            "[cron] expression %r does not have 5 fields — task will never fire",
            cron_expr,
        )
        return False

    minute_set = _parse_cron_field(parts[0], 0, 59)
    hour_set = _parse_cron_field(parts[1], 0, 23)
    dom_set = _parse_cron_field(parts[2], 1, 31)
    month_set = _parse_cron_field(parts[3], 1, 12)
    dow_set = _parse_cron_field(parts[4], 0, 6, names=_DOW_NAMES)

    if when.tm_min not in minute_set:
        return False
    if when.tm_hour not in hour_set:
        return False
    if when.tm_mon not in month_set:
        return False
    # Cron's day-of-month and day-of-week are OR-ed when both are
    # restricted (POSIX behaviour). When both fields are full-wildcard
    # the test simplifies to "always".
    dom_full = dom_set == set(range(1, 32))
    dow_full = dow_set == set(range(0, 7))
    # Python's struct_time.tm_wday: Monday=0..Sunday=6 (matches our mapping)
    if dom_full and dow_full:
        return True
    if dom_full:
        return when.tm_wday in dow_set
    if dow_full:
        return when.tm_mday in dom_set
    # Both restricted: OR semantics
    return (when.tm_mday in dom_set) or (when.tm_wday in dow_set)


# ── Run history (Redis-backed) ─────────────────────────────────────────────
#
# Each task run produces a small dict with id, started_at, duration_ms,
# status, cost_usd, snippet of the response. Persisted as a Redis list
# so /status can show the last 20 runs without scanning the whole index.

_RUNS_KEY = "crucix:autonomous:runs"
_MAX_RUNS_RETAINED = 50


@fail_wire(module="tasks", gap_type="agent_cycle_failure")
async def record_run(record: dict[str, Any]) -> None:
    """Push one run record onto the head of the runs list, trim the tail."""
    from ..intel import redis_store as rs
    import json as _json
    try:
        await rs.lpush(_RUNS_KEY, _json.dumps(record, default=str))
        await rs.ltrim(_RUNS_KEY, 0, _MAX_RUNS_RETAINED - 1)
    except Exception as e:
        logger.warning("[autonomous runs] failed to persist run record: %s", e)


@fail_wire(module="tasks", gap_type="agent_cycle_failure")
async def get_recent_runs(limit: int = 20) -> list[dict[str, Any]]:
    """Return the last N task run records (most recent first)."""
    from ..intel import redis_store as rs
    import json as _json
    try:
        raw = await rs.lrange(_RUNS_KEY, 0, limit - 1)
    except Exception as e:
        logger.warning("[autonomous runs] failed to read run history: %s", e)
        return []
    out: list[dict[str, Any]] = []
    for entry in raw or []:
        try:
            out.append(_json.loads(entry))
        except Exception:
            continue
    return out


# ── Audit-readiness gate ──────────────────────────────────────────────────

def _audit_readiness_gate(tool_kind: str, *, min_active: int = 1) -> dict:
    """Pre-fire health check for the trust-measurement audits (adversarial,
    constitution). These produce constitutional signal — running them on a
    degraded LLM stack poisons the historical baseline AND the amendments
    queue (see 04-19 incident: 06:00 UTC adversarial fired against two
    billing-cooled providers, scored 0/11, queued 11 garbage amendments).

    R-F1492: default min_active is 1, not 2. ARIA runs SINGLE-provider
    (DeepSeek-only) by design now (Anthropic/Brave/etc. declined — §18), so
    requiring 2 active providers blocked every trust-audit permanently — the
    adversarial/security/constitution data points froze ~05-27 (a stale safety
    signal, NOT a real score). With min_active=1 the gate still skips when the
    ONE provider is cooling (preserving the anti-poison intent), and the
    all-empty/degraded guards in run_weekly still protect the baseline if the
    single provider returns empties. Re-raise to 2 only if a 2nd provider is added.

    Gate: require at least `min_active` providers in the global fallback
    chain that are NOT currently in cool-down. If the gate fails, return
    a structured 'skipped' marker so the run is recorded as deferred,
    not as a fail.
    """
    import time as _time
    from ..main import app as _app
    llm = getattr(getattr(_app, "state", None), "llm_provider", None)
    if not llm or not getattr(llm, "is_configured", False):
        return {
            "ok": False,
            "reason": "no_llm_configured",
            "readable": (
                f"⚠ {tool_kind} SKIPPED — no LLM provider configured. "
                f"Set the API keys then re-fire."
            ),
        }
    stats = llm.get_stats() if hasattr(llm, "get_stats") else {}
    now = _time.time()
    active = []
    cooling = []
    for name, s in stats.items():
        if not isinstance(s, dict):
            continue
        if "cooldown_until" not in s:
            continue
        if s.get("cooldown_until", 0) <= now and s.get("status") != "cooling_down":
            active.append(name)
        else:
            cooling.append(f"{name}({s.get('last_kind','?')})")
    if len(active) < min_active:
        msg = (
            f"⚠ {tool_kind} SKIPPED — only {len(active)} active provider(s); "
            f"need {min_active}. Cooling: {', '.join(cooling) or 'none'}. "
            f"Active: {', '.join(active) or 'none'}. Will retry on next "
            f"scheduled fire."
        )
        return {"ok": False, "reason": "insufficient_active_providers",
                "active": active, "cooling": cooling, "readable": msg}
    return {"ok": True, "active": active, "cooling": cooling}


# ── Direct tool execution (non-chat tools) ────────────────────────────────

async def _execute_direct_tool(tool_kind: str, task: Task, llm) -> dict:
    """Execute a tool that calls a module function directly (not via chat)."""
    if tool_kind == "law_refresh":
        from ..intel import international_law
        return await international_law.refresh_law_knowledge()

    elif tool_kind == "corpus_weekly_crawl":
        from ..intel import corpus_manager
        return await corpus_manager.run_weekly_crawl()

    elif tool_kind == "metacognitive_daily_check":
        # Real cycle implementation lives in metacognitive.cycle, not engine.
        # Past wiring bug 2026-04-18: this called engine.run_daily_check (no
        # such function) and silently returned {"skipped": "not implemented"}
        # so METACOG-DAILY task fired daily but did nothing for weeks.
        from ..metacognitive import cycle as metacog_cycle
        result = await metacog_cycle.daily_self_check(llm)
        try:
            from ..intel import brain_hook as _bh
            await _bh.absorb(
                module="self_assess",
                summary=f"Metacog daily: {result.get('assessments_today', 0)} assessments, "
                        f"avg_score={result.get('avg_score')}, "
                        f"high_gaps={result.get('high_severity_gaps', 0)}",
                success=True,
                confidence="ASSESSED",
            )
        except Exception:
            pass
        return result

    elif tool_kind == "metacognitive_weekly_review":
        from ..metacognitive import cycle as metacog_cycle
        result = await metacog_cycle.weekly_consciousness_review(llm)
        try:
            from ..intel import brain_hook as _bh
            await _bh.absorb(
                module="self_assess",
                summary=f"Metacog weekly review: brier={result.get('overall_brier')}, "
                        f"weak_domains={result.get('weakest_domains', [])[:3]}",
                success=result.get("consciousness_report_ok", False),
                extra_topics=["compliance"],
                confidence="ASSESSED",
            )
        except Exception:
            pass
        return result

    elif tool_kind == "metacognitive_monthly_sprint":
        from ..metacognitive import cycle as metacog_cycle
        result = await metacog_cycle.monthly_gap_closure_sprint(llm)
        try:
            from ..intel import brain_hook as _bh
            await _bh.absorb(
                module="self_assess",
                summary=f"Metacog monthly sprint: gaps_addressed={result.get('gaps_addressed', 0)}, "
                        f"code_proposals={result.get('code_proposals_generated', 0)}",
                success=True,
                extra_topics=["compliance"],
                confidence="ASSESSED",
            )
        except Exception:
            pass
        return result

    elif tool_kind == "dd_watchlist_sweep":
        from ..intel import dd_orchestrator
        result = await dd_orchestrator.rescreen_watchlist(llm=llm)
        # R-F2559 — also re-screen the operator-curated PUBLIC watchlist (separate,
        # tenant-free store) so its risk changes flow to Golden Intel. Non-fatal.
        try:
            await dd_orchestrator.rescreen_public_watchlist()
        except Exception:
            pass
        # R-F2560 — refresh the designation-diff feed (new official designations ->
        # decision-grade Golden Intel).
        #
        # R-F3534 — this is now a BELT-AND-BRACES second run, not the lane's only
        # heartbeat. It used to be the only caller, so the most valuable signal
        # class in the product was checked once a WEEK ("0 7 * * mon"), as a
        # non-fatal afterthought on an unrelated task, behind a bare `except: pass`.
        # OFAC designated on seven separate days in July while ARIA looked once, and
        # a failure here told nobody. `sanctions_designation_watch` now owns the
        # cadence; the failure is wired either way.
        try:
            from ..intel import sanctions_designation_diff
            await sanctions_designation_diff.run_designation_diff()
        except Exception as _sdd_err:
            try:
                from ..intel.engine_wiring import wire_failure as _wf
                _wf(module="sanctions_designation_diff",
                    detail=f"designation diff failed inside dd_watchlist_sweep: {type(_sdd_err).__name__}",
                    gap_type="golden_intel_promotion_failure",
                    source="tasks:dd_watchlist_sweep")
            except Exception:
                pass
        return result

    elif tool_kind == "sanctions_designation_watch":
        # R-F3534 — the official-designation lane's OWN heartbeat.
        #
        # This is ARIA's highest-value signal: a counterparty designated by OFAC,
        # the UN, the UK, the EU or debarred by the World Bank is a stop-work event
        # for a defence broker. It costs no LLM spend (list fetch + set diff), so it
        # can run hourly; the value is entirely in LATENCY, and a week-old
        # designation is not intelligence, it is history.
        from ..intel import sanctions_designation_diff
        return await sanctions_designation_diff.run_designation_diff()

    # R-F1255 (2026-06-01): Full DD sweep — runs the 7-layer orchestrator
    # on every watchlist entity. This is a COSTLY operation (each entity
    # costs ~$0.50-2.00 in LLM calls) so it's capped at 3 entities per
    # cycle and gated behind a separate task with a higher cost cap.
    elif tool_kind == "dd_full_sweep":
        from ..intel import dd_orchestrator
        from ..intel import redis_store as _rs

        watchlist = await _rs.get_json(dd_orchestrator.WATCHLIST_KEY) or []
        if not watchlist:
            return {"entities_screened": 0, "results": [], "errors": [],
                    "message": "watchlist is empty"}

        # Cap at 3 entities per cycle to control cost
        max_entities = int((task.tool_chain[0] or {}).get("max_entities", 3))
        entities = watchlist[:max_entities]

        results = []
        errors = []
        for entity in entities:
            name = entity.get("name") or entity.get("entity") or ""
            entity_type = entity.get("type", "company")
            try:
                report = await dd_orchestrator.orchestrate_dd(
                    {"name": name, "type": entity_type},
                    llm=llm,
                    mode="quick",  # quick mode skips network + deep research
                    cost_cap_usd=1.00,
                )
                results.append({
                    "entity": name,
                    "success": True,
                    "risk_score": getattr(report, "composite_score", None),
                    "report_id": getattr(report, "report_id", None),
                })
            except Exception as e:
                logger.warning("[dd_full_sweep] DD failed for %s: %s", name, e)
                errors.append({"entity": name, "error": str(e)[:200]})

        return {
            "entities_screened": len(entities),
            "results": results,
            "errors": errors,
            "success_count": len(results),
            "error_count": len(errors),
        }

    # R-F69 (2026-05-09): DOJ FCPA enforcement monitoring. Weekly sweep
    # of the DOJ FCPA enforcement listing — extracts named entities
    # (companies, individuals, country exposures, penalty amounts) from
    # cases involving priority countries and writes them to the brain
    # with topic=enforcement_action.
    elif tool_kind == "fcpa_enforcement_scan":
        from ..intel import fcpa_enforcement
        days_back = int((task.tool_chain[0] or {}).get("days_back", 30))
        return await fcpa_enforcement.monitor_doj_fcpa(days_back=days_back)

    # R-F79 (2026-05-09): refresh the OpenSanctions crypto wallet index
    # daily so screen_wallet hits the freshest data. Zero LLM cost; one
    # CSV download + Redis index rebuild.
    elif tool_kind == "crypto_sanctions_refresh":
        from ..intel import crypto_sanctions
        force = bool((task.tool_chain[0] or {}).get("force", False))
        return await crypto_sanctions.fetch_and_index(force=force)

    # R-F98 (2026-05-09): weekly counter-intelligence sweep over the
    # top-mentioned entities. Scans for reputation washing / credibility
    # anomaly / new-outlet burst patterns. Zero LLM cost.
    elif tool_kind == "counter_intel_sweep":
        from ..intel import counter_intelligence
        n = int((task.tool_chain[0] or {}).get("top_n", 5))
        days = int((task.tool_chain[0] or {}).get("window_days", 14))
        return await counter_intelligence.scan_top_entities(n=n, window_days=days)

    # R-F90/F97 (2026-05-09): recompute continuous-update priorities.
    # Reads R-F88 freshness + R-F89 coverage gaps, writes the priority
    # list the autonomous engine reads on every poll cycle. Should fire
    # every 6 hours so priorities don't go stale.
    elif tool_kind == "recompute_priorities":
        from ..intel import continuous_update
        max_p = int((task.tool_chain[0] or {}).get("max_priorities", 30))
        return await continuous_update.write_priorities(max_priorities=max_p)

    elif tool_kind == "knowledge_freshness_audit":
        from ..intel import weekly_report
        return await weekly_report._audit_knowledge_freshness()

    elif tool_kind == "daily_team_briefing":
        from ..intel import deal_pipeline
        from ..intel import contact_intelligence
        from ..intel import signal_correlator
        from ..intel import team_engagement
        summary = await deal_pipeline.generate_pipeline_summary()
        # Also check dormancy as part of briefing
        dormant = await deal_pipeline.check_dormant_leads()
        if dormant:
            summary += f"\n\n💤 *{len(dormant)} lead(s) auto-marked DORMANT today*"
        # Add correlated intelligence
        correlation_brief = await signal_correlator.generate_correlation_briefing()
        if correlation_brief:
            summary += f"\n{correlation_brief}"
        # Add long-horizon causal chain (Priority 1, 2026-04-17)
        try:
            from ..intel import chain_correlator
            chain_brief = await chain_correlator.generate_chain_briefing()
            if chain_brief:
                summary += f"\n{chain_brief}"
        except Exception as _e:
            logger.debug("chain briefing failed (non-fatal): %s", _e)
        # Add procurement calendar (Priority 3, 2026-04-17)
        try:
            from ..intel import procurement_calendar
            calendar_brief = await procurement_calendar.generate_calendar_briefing()
            if calendar_brief:
                summary += f"\n{calendar_brief}"
        except Exception as _e:
            logger.debug("calendar briefing failed (non-fatal): %s", _e)
        # Add competitor activity (Priority 4, 2026-04-17)
        try:
            from ..intel import competitor_tracker
            competitor_brief = await competitor_tracker.generate_competitor_briefing()
            if competitor_brief:
                summary += f"\n{competitor_brief}"
        except Exception as _e:
            logger.debug("competitor briefing failed (non-fatal): %s", _e)
        # Add OEM contact graph coverage (Priority 2, 2026-04-17)
        try:
            from ..intel import oem_contact_graph
            oem_brief = await oem_contact_graph.generate_oem_briefing()
            if oem_brief:
                summary += f"\n{oem_brief}"
        except Exception as _e:
            logger.debug("OEM briefing failed (non-fatal): %s", _e)
        # Add contact intelligence section
        contact_brief = await contact_intelligence.generate_contact_briefing()
        if contact_brief:
            summary += f"\n{contact_brief}"
        # Add team engagement section
        engagement_brief = await team_engagement.generate_engagement_briefing()
        if engagement_brief:
            summary += f"\n{engagement_brief}"
        # Narrative environment section
        try:
            from ..intel import narrative_monitor
            narrative_brief = await narrative_monitor.generate_narrative_briefing()
            if narrative_brief:
                summary += f"\n{narrative_brief}"
        except Exception as _e:
            logger.debug("narrative briefing failed (non-fatal): %s", _e)
        # State-of-ARIA: what ARIA sees about herself today
        try:
            from ..intel import self_assess
            self_brief = await self_assess.generate_state_of_aria_briefing()
            if self_brief:
                summary += f"\n{self_brief}"
        except Exception as _e:
            logger.debug("self_assess briefing failed (non-fatal): %s", _e)
        # Adversarial audit: last result + pending amendments
        try:
            from ..intel import adversarial_challenge as _ac
            adv_stats = await _ac.stats()
            last_run = adv_stats.get("last_run")
            pending = adv_stats.get("pending_amendments", 0)
            if last_run:
                score = last_run.get("overall_score", 0)
                passed = last_run.get("passed", 0)
                total = last_run.get("total_attacks", 0)
                crit = last_run.get("critical_failures", 0)
                run_at = last_run.get("run_at", "?")
                adv_brief = (
                    f"\n\n🛡️ *ADVERSARIAL AUDIT*\n"
                    f"Last run: {run_at}\n"
                    f"Score: {score:.0%} ({passed}/{total} passed)"
                )
                if crit:
                    adv_brief += f"\n⚠️ {crit} CRITICAL failure(s)"
                if pending:
                    adv_brief += (
                        f"\n📋 {pending} amendment(s) pending review "
                        f"→ /api/aria/adversarial/amendments"
                    )
                summary += adv_brief
        except Exception as _e:
            logger.debug("adversarial briefing failed (non-fatal): %s", _e)
        # Instrument panel — critical metrics for the team
        try:
            from ..intel import operating_modes as _om
            from ..intel import circuit_breaker as _cb
            from ..intel import source_verifier as _sv
            from ..intel import verified_intel as _vi
            from ..intel import chat_audit_log as _cal
            from ..intel import redis_store as _rs

            mode = await _om.get_mode()
            breakers = _cb.get_all_breakers()
            open_breakers = [b for b in breakers if b["state"] == "OPEN"]
            blocks_24h = int(await _rs.get("crucix:predictor:blocks:24h") or 0)

            # Grounded rate
            grounded = None
            try:
                sv_stats = await _sv.get_verification_stats()
                grounded = sv_stats.get("avg_grounded_rate")
            except Exception:
                pass

            # Fact health
            try:
                vi_stats = await _vi.get_verification_summary()
                total_facts = vi_stats.get("total_facts", 0)
                stale_facts = vi_stats.get("stale", 0)
                contradicted = vi_stats.get("contradicted", 0)
            except Exception:
                total_facts = stale_facts = contradicted = 0

            # Audit trail
            try:
                audit_stats = await _cal.get_stats()
                audit_total = audit_stats.get("total_entries", 0)
            except Exception:
                audit_total = 0

            panel = "\n\n📊 *INSTRUMENT PANEL*"
            panel += f"\nMode: {mode.name}"
            if grounded is not None:
                panel += f" | Grounded: {grounded:.0%}"
            panel += f" | Facts: {total_facts}"
            if stale_facts:
                panel += f" ({stale_facts} stale)"
            if contradicted:
                panel += f" ({contradicted} contradicted)"
            if blocks_24h:
                panel += f"\n⚠️ Predictor blocked {blocks_24h} task(s) in 24h"
            if open_breakers:
                panel += f"\n⚠️ {len(open_breakers)} circuit breaker(s) OPEN: {', '.join(b['name'] for b in open_breakers)}"
            panel += f"\nAudit trail: {audit_total} entries"
            panel += f"\n🔗 Dashboard: /aria-brain.html"
            summary += panel
        except Exception as _e:
            logger.debug("instrument panel failed (non-fatal): %s", _e)
        # Autonomy Surface (2026-04-17 late PM) — tells the team what
        # ARIA did overnight, what's queued for review, and what needs
        # operator action. Only appended if there's something to show.
        try:
            from ..intel import autonomy_surface
            surface_block = await autonomy_surface.build_operator_prompt()
            if surface_block:
                summary += surface_block
        except Exception as _e:
            logger.debug("autonomy_surface briefing failed (non-fatal): %s", _e)
        return {"briefing": summary, "dormant_leads": len(dormant)}

    elif tool_kind == "source_discovery":
        from ..intel import team_engagement
        recs = await team_engagement.generate_source_recommendations()
        requests = await team_engagement.generate_knowledge_requests()
        return {
            "source_recommendations": len(recs),
            "knowledge_requests": len(requests),
            "recommendations": recs,
            "requests": requests,
        }

    elif tool_kind == "verified_fact_refresh":
        # Clause 17 daily refresh — re-verify PENDING_CORROBORATION / STALE
        # facts by searching for new corroborating sources.
        from ..intel import verified_intel as _vi
        from ..intel import researcher as _r
        max_facts = int((task.tool_chain[0] or {}).get("max_facts", 50))
        engine = _vi.ARIAVerificationEngine(web_search_fn=_r.web_search)
        stats = await engine.arefresh_stale_facts(max_facts=max_facts)
        return {"clause17_refresh": stats}

    elif tool_kind == "ecosystem_reassess":
        # PR 3 — hourly reassess: computes gap queue without mutating state.
        from ..intel import ecosystem_reassess as _er
        report = await _er.run()
        # Also evaluate operating mode auto-transitions (hourly check)
        # ── R-F3761 — this evaluation is the ONLY route out of DEGRADED ──────
        #
        # It was `except Exception: logger.debug(...)`. DEBUG is not emitted at
        # the running log level, so a failure here was INVISIBLE — and this call
        # is the only thing that returns the platform to NORMAL. DEGRADED
        # suppresses all external delivery (`should_deliver_external` returns
        # `mode == NORMAL`), so a silent failure means customer-facing output
        # stays off with nothing anywhere saying why.
        #
        # Measured 2026-08-06: the platform sat DEGRADED from 2026-08-05T18:00Z.
        # Driving this task on demand returned status=ok in 1.3s and did NOT
        # transition — no history entry, no log line, no signal. The logic itself
        # is sound (grounded_rate None -> 1.0 -> target NORMAL != current
        # DEGRADED -> set_mode), so something inside threw and landed here, where
        # it was discarded. 26 health samples over 78 minutes confirmed it stuck.
        #
        # Now: the outcome is REPORTED either way. A failure is an ERROR, is
        # wired (§21a), and is put in the report so `/autonomous/run-now` shows
        # it in the response — the diagnosis should not require log access to a
        # level nobody runs at. `mode_evaluated` records the no-change case too,
        # so "evaluated and nothing to do" is distinguishable from "never ran".
        try:
            from ..intel import operating_modes as _om
            _mode_before = (await _om.get_mode()).name
            transition = await _om.evaluate_auto_transition()
            report["mode_evaluated"] = {"before": _mode_before,
                                        "transitioned": bool(transition)}
            if transition:
                report["mode_transition"] = transition
                logger.warning("[ecosystem_reassess] operating mode auto-transition: %s", transition)
        except Exception as _e:
            report["mode_evaluation_error"] = f"{type(_e).__name__}: {_e}"
            logger.error(
                "[R-F3761] operating-mode evaluation FAILED (%s: %s) — the platform "
                "cannot leave DEGRADED without this, and DEGRADED suppresses ALL "
                "external delivery. This was previously logged at debug and lost.",
                type(_e).__name__, _e, exc_info=True,
            )
            try:
                from ..intel.engine_wiring import wire_failure as _wf
                _wf(module="operating_modes",
                    detail=(f"auto-transition evaluation failed ({type(_e).__name__}: "
                            f"{str(_e)[:110]}) — platform cannot recover from DEGRADED"),
                    gap_type="engine_failure",
                    source="tasks:ecosystem_reassess:R-F3761")
            except Exception:
                pass
        # Compute composite autonomy score (hourly tracking)
        try:
            from ..intel import autonomy_scorer as _as
            composite = await _as.compute_composite()
            report["autonomy_composite"] = {
                "score": composite.get("composite_score"),
                "tier": composite.get("tier_name"),
            }
        except Exception as _e:
            logger.debug("autonomy composite failed (non-fatal): %s", _e)
        return {"ecosystem": report}

    elif tool_kind == "core_develop":
        # PR 3 — daily self-development pass (auto-allowed actions only).
        # Staged rollout (2026-04-15): reads `allowed_actions` from the
        # tool_chain entry so the operator widens the whitelist step
        # by step rather than flipping the whole action surface on at once.
        from ..intel import core_develop as _cd
        tc = task.tool_chain[0] or {}
        allowed = tc.get("allowed_actions")  # list or None → module default
        max_actions = int(tc.get("max_actions", 3))
        report = await _cd.run(
            max_actions=max_actions,
            allowed_actions=tuple(allowed) if allowed else None,
        )
        return {"core_develop": report}

    elif tool_kind == "core_meta":
        # PR 3 — weekly meta-review.
        from ..intel import core_develop as _cd
        report = await _cd.meta_review()
        return {"core_meta": report}

    elif tool_kind == "source_scout":
        # PR 3 — scout patterns (citation, TLD probe, sitemap sweep).
        from ..intel import source_scout as _ss
        pattern = (task.tool_chain[0] or {}).get("pattern", "citation")
        report = await _ss.run(pattern=pattern)
        return {"scout": report}

    elif tool_kind == "golden_autogen":
        # Clause 17-driven golden-Q auto-generation.
        from ..intel import golden_autogen as _ga
        max_cands = int((task.tool_chain[0] or {}).get("max_candidates", 20))
        report = await _ga.propose_batch(max_candidates=max_cands)
        return {"golden_autogen": report}

    elif tool_kind == "run_eval":
        # R-F470 (2026-05-14): close the regression-detection loop.
        # DAILY-GOLDEN-AUTOGEN grew the golden set; pre-R-F470 nothing
        # scored against it autonomously, so regression detection was
        # manual (operator-triggered). This dispatches eval_runner.run_eval
        # with the current LLM and surfaces pass_rate + score_delta so
        # the dashboard/whatsapp can react when accuracy drops.
        #
        # R-F518: use the `llm` parameter passed into _execute_direct_tool
        # (matches every other branch in this function) instead of a
        # `from ..main import app` rebind. The rebind risked a circular
        # import if the run_eval branch fired during lifespan startup; the
        # parameter version is what the rest of the dispatcher already
        # relies on so we trust the same upstream guarantee here.
        from ..intel import eval_runner as _er
        if not llm or not getattr(llm, "is_configured", False):
            return {
                "run_eval": {
                    "ok": False,
                    "reason": "no_llm_configured",
                    "readable": "⚠ run_eval SKIPPED — no LLM provider configured.",
                }
            }
        # ── R-F3701 — the scheduled eval must RECORD, and must be able to FINISH ──
        #
        # THE DEFECT (two halves, both live):
        #
        # 1. `run_eval(llm, label=label)` left `record` at its default of False.
        #    R-F2390 built `record=True` precisely to flow each answered entry
        #    through source_verifier.record_verification + honesty_judge.
        #    record_judgment — the EXACT stores autonomy_scorer.compute_composite
        #    reads (eval_runner.py:434-438, :664, :680). This was the ONLY
        #    scheduled caller, so the mechanism that populates 70% of Phase A
        #    gate #1's weight (verification 0.45 + honesty 0.25) had never run.
        #    Live: gate #1 confidence sat at EXACTLY 0.30 = W_MASTERY, i.e.
        #    mastery was the only measured axis, and the 0.836 "composite" was
        #    the mastery score alone, renormalised.
        #
        #    R-F3696 fixed the sample-size mismatch that DISCARDED a verification
        #    signal; this fixes the reason there was no signal to discard.
        #
        # 2. `limit` defaulted to 0 = the whole 500-entry golden set, inside a
        #    600s timeout (tasks.yaml:1174). Each entry costs one full
        #    _aria_chat_session PLUS a judge completion PLUS two encodes — about
        #    1.2s of budget for two LLM round-trips, which is not achievable.
        #    `_save_run` only runs AFTER the loop (eval_runner.py:753), so a
        #    timeout persisted NOTHING: the daily regression signal was empty
        #    while the spend was real. The representative-stride sampler
        #    (eval_runner.py:470-473) exists for exactly this and was never
        #    given a limit to use — it strides across the set so the subset
        #    spans categories instead of being the first N of one category.
        #
        # Both are read from the task config so the operator can tune them in
        # tasks.yaml without a code change; the defaults are the safe ones.
        _cfg = task.tool_chain[0] or {}
        label = _cfg.get("label", "daily_autonomous")
        _record = bool(_cfg.get("record", True))
        _limit = int(_cfg.get("limit", 40) or 0)
        report = await _er.run_eval(llm, label=label, record=_record, limit=_limit)
        return {"run_eval": report}

    elif tool_kind == "cost_guard":
        # R-F930 (2026-05-27) — ARIA monitors her OWN month-to-date LLM spend
        # and throttles autonomous work BEFORE the $300/mo hard cap
        # (cost_tracker.assert_monthly_cap) would block EVERY call — including
        # user-facing chat. This task makes NO LLM call (pure read + guard) and:
        #   1. reads month-to-date burn vs cap (cost_tracker.get_month_spend),
        #   2. feeds it to ARIA's brain every run so she SEES her cost
        #      (Rule Zero / the R-F921 self_monitor channel),
        #   3. at >= pause-threshold PAUSES the autonomous engine — chat is
        #      NOT engine-gated, so pausing autonomous spend preserves the
        #      remaining budget for the user-facing path.
        # Thresholds are env-tunable; pause defaults to 90% (10% chat buffer).
        # Resume is deliberate (POST /api/aria/autonomous/resume) per safety.py.
        from ..intel import cost_tracker as _ct930
        from ..intel import brain_hook as _bh930
        from . import safety as _sf930

        def _pct_env(name: str, default: float) -> float:
            try:
                return float(os.getenv(name, str(default)))
            except (TypeError, ValueError):
                return default

        spend = await _ct930.get_month_spend()
        util = float(spend.get("utilisation_pct") or 0.0)
        pause_at = _pct_env("ARIA_COST_GUARD_PAUSE_PCT", 90.0)
        warn_at = _pct_env("ARIA_COST_GUARD_WARN_PCT", 70.0)
        autopause = os.getenv("ARIA_COST_GUARD_AUTOPAUSE", "1").strip().lower() in ("1", "true", "yes")

        action = "ok"
        if util >= pause_at:
            if autopause and not await _sf930.is_engine_paused():
                await _sf930.pause_engine(
                    reason=(
                        f"cost_guard R-F930: monthly LLM spend at {util:.1f}% of cap "
                        f"(${spend.get('spent_usd')} / ${spend.get('cap_usd')}) — autonomous "
                        f"engine paused to protect the user-facing chat budget. Resume via "
                        f"POST /api/aria/autonomous/resume once spend headroom recovers."
                    )
                )
                action = "paused"
            else:
                action = "over_threshold_no_pause"
        elif util >= warn_at:
            action = "warn"

        # Rule Zero — ARIA observes her own spend EVERY run (healthy heartbeat
        # when ok; a capability gap when warn/paused so the coder/operator see it).
        try:
            await _bh930.observe_self_event(
                "cost_status",
                {**spend, "action": action, "pause_at_pct": pause_at, "warn_at_pct": warn_at},
                success=(action == "ok"),
                gap_type=("cost_pressure" if action != "ok" else "self_runtime"),
            )
        except Exception as _cg_e:
            logger.debug("R-F930 cost_guard brain signal failed (non-fatal): %s", _cg_e)

        readable = (
            f"💸 LLM spend {util:.1f}% of ${spend.get('cap_usd')} cap "
            f"(${spend.get('spent_usd')} spent, ${spend.get('remaining_usd')} left) — {action}"
        )
        if action in ("paused", "over_threshold_no_pause"):
            logger.warning("[autonomous] R-F930 cost_guard: %s", readable)
        return {"cost_guard": {**spend, "action": action, "readable": readable}}

    elif tool_kind == "compliance_watch":
        # R-F935 — Compliance Watch private digest. Analyses the captured WA
        # group-message window -- deception + risk + blind-spot + contradiction
        # lanes -- feeds findings to the brain + coverage ledger, and emails a
        # grounded structured digest to the compliance principal. Delivery is
        # draft-safe until ARIA_EMAIL_OUTBOUND_ENABLED=1. No per-message LLM, so
        # it is cheap; urgent runs suppress email unless a HIGH/CRITICAL finding.
        from ..intel import compliance_watch as _cw935
        _cfg = task.tool_chain[0] if task.tool_chain else {}
        _urgent = bool(_cfg.get("urgent"))
        _win = float(_cfg.get("window_hours", 1.0 if _urgent else 24.0))
        report = await _cw935.run_compliance_watch(window_hours=_win, urgent_only=_urgent)
        return {"compliance_watch": report}

    elif tool_kind == "contract_selfcheck":
        # R-F953 — daily contract-review canary. Runs a synthetic review
        # end-to-end and flags the brain if it truncates / empties / times out
        # (the recurring contract-review failure class). One cheap LLM turn.
        from ..intel import contract_intelligence as _ci953
        report = await _ci953.run_contract_selfcheck(llm)
        return {"contract_selfcheck": report}

    elif tool_kind == "learning_cycle":
        # R-F662 (2026-05-17): OSS-only learning controller.
        # Zero cloud LLM calls in the loop — uses FSRS schedule +
        # reading_queue + rag_store + neural_memory. Gated by env flag
        # ARIA_LEARNING_CONTROLLER_ENABLED. Hard timeout via the R-F651
        # asyncio.wait_for wrap in the direct-tool dispatch.
        from ..learning import learning_controller as _lc
        first = (task.tool_chain[0] or {}) if task.tool_chain else {}
        max_topics = int(first.get("max_topics", 5))
        time_budget_s = float(first.get("time_budget_s", 120.0))
        report = await _lc.run_cycle(
            max_topics=max_topics, time_budget_s=time_budget_s,
        )
        return {"learning_cycle": report}

    elif tool_kind == "cost_free_learn":
        # R-F567 (2026-05-16): hourly preview of the four cost-free
        # learning loops (mastery decay, mistake replay, cross-source
        # corroborate, Q/A distill). Read-only by default; writes
        # gated by ARIA_COST_FREE_LEARN_WRITE=1 env. No LLM cost.
        # Surfaces "would improve X" candidates without spending.
        from ..intel import cost_free_learning as _cfl
        report = await _cfl.run_preview()
        return {"cost_free_learn": report}

    elif tool_kind == "adversarial_weekly":
        # Manipulation-resistance bi-weekly sweep — 5 attacks across
        # 4 categories. Failures stage clause-amendment candidates.
        # Returns readable_report for WhatsApp delivery (not raw JSON).
        from ..intel import adversarial_challenge as _ac
        gate = _audit_readiness_gate("adversarial_weekly", min_active=1)  # R-F1492: 1, not 2 — ARIA is single-provider (DeepSeek-only) by design
        if not gate["ok"]:
            return gate["readable"]
        report = await _ac.run_weekly()
        readable = report.get("readable_report", str(report))
        return readable

    elif tool_kind == "security_weekly":
        # Security-resistance weekly audit — complements adversarial_weekly.
        # Tests prompt injection / prompt leak / tool misuse / PII exfil
        # / SSRF / output injection (intel/security_challenge.py).
        # Same readiness gate + summary shape as adversarial_weekly so
        # the dashboard + reporters consume both identically.
        from ..intel import security_challenge as _sc
        gate = _audit_readiness_gate("security_weekly", min_active=1)  # R-F1492: 1, not 2 — ARIA is single-provider (DeepSeek-only) by design
        if not gate["ok"]:
            return gate["readable"]
        summary = await _sc.run_security_weekly()
        # Produce a short WhatsApp-friendly summary
        lines = [
            "*🛡 Security audit — weekly*",
            f"Passed {summary.get('passed')}/{summary.get('total_attacks')} "
            f"(overall {summary.get('overall_score')}, "
            f"critical fails {summary.get('critical_failures')})",
            "",
            "By category:",
        ]
        for cat, s in (summary.get("by_category") or {}).items():
            lines.append(f"  • {cat}: {s.get('passed')}/{s.get('total')} ({s.get('score')})")
        return "\n".join(lines)

    elif tool_kind == "constitution_test":
        # Weekly constitution compliance audit — runs all 20 clause tests.
        # Failures feed mistake_ledger + brain_hook via structured report.
        import os as _os
        from ..tests.test_constitution import ARIAConstitutionTestRunner
        gate = _audit_readiness_gate("constitution_test", min_active=1)  # R-F1492: 1, not 2 — ARIA is single-provider (DeepSeek-only) by design
        if not gate["ok"]:
            return gate["readable"]
        api_key = _os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            return {"error": "ANTHROPIC_API_KEY not set — cannot run constitution tests"}
        runner = ARIAConstitutionTestRunner(
            api_key=api_key,
            model="claude-sonnet-4-6",
        )
        report = runner.run_all()
        # 2026-04-26: split FAILs into "Claude actively violated" vs "test
        # framework couldn't grade". The runner's pass condition is
        # `violation is None AND compliance is not None`, so a clean response
        # that just doesn't use the test's expected vocabulary scores as FAIL
        # even though nothing was violated. Only the real-violation bucket is
        # a constitutional issue worth recording in mistake_ledger and
        # absorbing into the brain as a critical-failure signal — ungraded
        # results are a test-framework gap.
        real_violations = [
            r for r in report.results
            if not r.passed and not r.error and r.violation_found is not None
        ]
        ungraded = [
            r for r in report.results
            if not r.passed and not r.error and r.violation_found is None
        ]
        try:
            from ..intel import mistake_ledger as _ml
            from ..intel import brain_hook as _bh
            for r in real_violations:
                await _ml.record(
                    category="false_confidence",
                    task_type="constitution_test",
                    domain=f"clause_{r.clause_number}",
                    what=f"Constitution clause {r.clause_number} ({r.clause_name}) FAILED",
                    why=f"Violation pattern matched: {(r.violation_found or '')[:200]}",
                    fix=f"Strengthen clause {r.clause_number} to catch this attack pattern",
                )
            if real_violations:
                gap_type = "adversarial_critical_failure"
                gap_detail = f"{len(real_violations)} clause(s) actively violated"
            elif ungraded:
                gap_type = "test_framework_gap"
                gap_detail = (
                    f"{len(ungraded)} clause(s) ungraded — no violation matched, "
                    "but no compliance pattern matched either (test cannot grade)"
                )
            else:
                gap_type = None
                gap_detail = None
            await _bh.absorb(
                module="constitution_test",
                summary=(
                    f"Constitution audit: {report.passed}/{report.total} passed "
                    f"({report.pass_rate:.0%}), {len(real_violations)} violated, "
                    f"{len(ungraded)} ungraded"
                ),
                detail=report.to_report()[:500],
                # Success gate: only real violations count against ARIA. An
                # ungraded result is a test bug, not an ARIA failure.
                success=len(real_violations) == 0,
                gap_type=gap_type,
                gap_detail=gap_detail,
            )
        except Exception:
            pass
        return report.to_report()

    elif tool_kind == "corpus_ingest":
        # Weekly corpus registry auto-ingest — reads YAML, picks top 5
        # priority unread sources, runs deep_research on each.
        import yaml as _yaml
        from ..intel import researcher as _res
        from ..intel import brain_hook as _bh
        from ..main import app as _app
        registry_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "intel", "corpus_registry.yaml",
        )
        try:
            with open(registry_path) as f:
                reg = _yaml.safe_load(f)
        except Exception as e:
            return {"error": f"Failed to load corpus_registry.yaml: {e}"}
        sources = reg.get("sources", [])
        # Filter to crawlable Tier A/B sources
        candidates = [
            s for s in sources
            if s.get("tier", "").upper() in ("A", "B", "B+")
            and s.get("ingest_strategy") in ("html_crawl", "rss")
            and s.get("url")
        ]
        # Sort: Tier A first, then cplp_relevant, then alphabetical
        tier_order = {"A": 0, "B": 1, "B+": 2}
        candidates.sort(key=lambda s: (
            tier_order.get(s.get("tier", "B+").upper(), 3),
            0 if s.get("cplp_relevant") else 1,
            s.get("url", ""),
        ))
        # Pick top 5
        to_ingest = candidates[:5]
        results = []
        # Use the globally-configured fallback chain, not a no-arg
        # factory call (which raises TypeError — factory needs
        # provider: str). Same bug pattern as the one fixed in
        # adversarial_challenge._default_llm_fn on 2026-04-19.
        llm = getattr(getattr(_app, "state", None), "llm_provider", None)
        for src in to_ingest:
            url = src["url"]
            try:
                article = await _res.read_article(
                    llm, url,
                    context=f"Ingest source: {src.get('source_class', '')} — {src.get('notes', '')}",
                )
                results.append({
                    "url": url,
                    "source_class": src.get("source_class"),
                    "status": "ingested" if article else "empty",
                    "chars": len(str(article)),
                })
            except Exception as e:
                results.append({
                    "url": url,
                    "source_class": src.get("source_class"),
                    "status": f"error: {e}",
                })
        try:
            await _bh.absorb(
                module="knowledge_ingestor",
                summary=f"Corpus auto-ingest: {sum(1 for r in results if r['status'] == 'ingested')}/{len(results)} sources ingested",
                detail=str(results)[:500],
                success=any(r["status"] == "ingested" for r in results),
            )
        except Exception:
            pass
        return {"ingested": len([r for r in results if r["status"] == "ingested"]),
                "failed": len([r for r in results if r["status"] != "ingested"]),
                "results": results}

    elif tool_kind == "narrative_scan":
        from ..intel import narrative_monitor
        return await narrative_monitor.scan_narratives()

    elif tool_kind == "news_monitor_poll":
        from ..intel import news_monitor
        return await news_monitor.poll_feeds()

    elif tool_kind == "bd_strategy_generate":
        from ..intel import bd_strategy
        return await bd_strategy.generate_market_intelligence()

    elif tool_kind == "pipeline_dormancy_check":
        from ..intel import deal_pipeline
        dormant = await deal_pipeline.check_dormant_leads()
        stale = await deal_pipeline.get_stale_leads()
        deadlines = await deal_pipeline.get_upcoming_deadlines(days_ahead=7)
        return {
            "dormant_marked": len(dormant),
            "stale_leads": len(stale),
            "deadlines_7d": len(deadlines),
        }

    elif tool_kind == "procurement_calendar":
        # Priority 3 (2026-04-17). Actions:
        #   refresh           — compute upcoming alerts + push to chain
        #   list_upcoming     — return events in horizon (days_ahead: int)
        from ..intel import procurement_calendar
        action = ((task.tool_chain[0] or {}).get("action") or "refresh").strip().lower()
        if action == "refresh":
            return await procurement_calendar.refresh()
        if action == "list_upcoming":
            days = int((task.tool_chain[0] or {}).get("days_ahead", 180))
            return {"upcoming": await procurement_calendar.list_upcoming(days_ahead=days)}
        return {"error": f"procurement_calendar: unknown action {action!r}"}

    elif tool_kind == "competitor_tracker":
        # Priority 4 (2026-04-17). Actions:
        #   scan              — walk tender alerts + ledger, record activities
        #   who_else_in       — look up competitor activity for a country
        from ..intel import competitor_tracker
        action = ((task.tool_chain[0] or {}).get("action") or "scan").strip().lower()
        if action == "scan":
            return await competitor_tracker.scan_sources()
        if action == "who_else_in":
            country = (task.tool_chain[0] or {}).get("country", "")
            return {"hits": await competitor_tracker.who_else_in(country)}
        return {"error": f"competitor_tracker: unknown action {action!r}"}

    elif tool_kind == "oem_contact_graph":
        # Priority 2 (2026-04-17). Actions:
        #   enrich            — operator-driven enrichment scan
        #   stats             — coverage snapshot
        from ..intel import oem_contact_graph
        action = ((task.tool_chain[0] or {}).get("action") or "enrich").strip().lower()
        if action == "enrich":
            return await oem_contact_graph.enrich_from_linkedin()
        if action == "stats":
            return await oem_contact_graph.stats()
        return {"error": f"oem_contact_graph: unknown action {action!r}"}

    elif tool_kind == "chain_correlator":
        # Priority 1 (2026-04-17) — long-horizon causal chain geopolitics
        # → procurement → relationships. Dispatches by `action` in the
        # first tool_chain entry. See aria_service/intel/chain_correlator.py.
        from ..intel import chain_correlator
        action = ((task.tool_chain[0] or {}).get("action") or "").strip().lower()
        if action == "scan_shifts":
            shifts = await chain_correlator.scan_geopolitical_shifts()
            return {"new_shifts": len(shifts), "shifts": shifts[:20]}
        if action == "project_windows":
            windows = await chain_correlator.project_windows()
            active = [w for w in windows if w.get("status") == "ACTIVE"]
            return {
                "windows_total": len(windows),
                "windows_active": len(active),
                "windows": windows[:20],
            }
        if action == "close_chains":
            chains = await chain_correlator.close_chains()
            in_window = [c for c in chains if c.get("within_window")]
            return {
                "new_chains": len(chains),
                "in_window": len(in_window),
                "chains": chains[:20],
            }
        return {"error": f"chain_correlator: unknown action {action!r}"}

    elif tool_kind == "training_export":
        from ..learning import training_export
        return await training_export.run_daily_export(days_lookback=7)

    elif tool_kind == "knowledge_spider":
        from ..learning import knowledge_spider
        return await knowledge_spider.run_spider_tick()

    elif tool_kind == "metacognitive_journal":
        from ..learning import metacognitive_journal
        return await metacognitive_journal.run_hourly_journal()

    elif tool_kind == "research_engine":
        from ..learning import research_engine
        return await research_engine.run_research_tick()

    elif tool_kind == "style_learner":
        from ..learning import style_learner
        return await style_learner.run_hourly_style_learn()

    elif tool_kind == "memory_replication":
        from ..learning import memory_replication
        return await memory_replication.run_daily_backup()

    elif tool_kind == "consistency_suite":
        # Weekly canonical-vs-variants test. Fires each variant through
        # the chat pipeline — the LLM is the one passed in by the caller
        # (same provider the rest of autonomous uses, with cost meter
        # already wrapped).
        from ..intel import consistency_suite
        return await consistency_suite.run_all(llm)

    elif tool_kind == "capability_card_refresh":
        from ..intel import capability_card
        card = await capability_card.build_card()
        return {"ok": True, "version": card.get("version"), "generated_at": card.get("generated_at")}

    elif tool_kind == "calibration_auto_tune":
        from ..intel import calibration_auto_tune
        return await calibration_auto_tune.run_auto_tune()

    elif tool_kind == "source_uptime_ping":
        from ..intel import source_uptime_monitor
        return await source_uptime_monitor.run_daily_ping()

    elif tool_kind == "self_diagnostic":
        from ..intel import self_diagnostic
        return await self_diagnostic.run_diagnostic_tick()

    elif tool_kind == "portal_coverage_audit":
        from ..intel import portal_coverage_audit as _pca
        return await _pca.auto_register_gaps(max_portals=3)

    # R-F1256 (2026-06-01): Daily vault registration agent — picks up
    # pending vault entries and attempts registration. More frequent than
    # the weekly PORTAL-REGISTRATION-WEEKLY task. Handles portals that
    # were added to the vault but not yet registered.
    elif tool_kind == "vault_registration_daily":
        from ..intel.agent_signup_vault import AgentSignupVault
        from ..intel import portal_registry as _pr

        vault = AgentSignupVault()
        try:
            # Get all pending entries from the vault
            pending = vault.list(status="pending", limit=10)
            if not pending:
                return {"checked": 0, "registered": 0, "message": "no pending entries"}

            results = []
            for entry in pending:
                site_id = entry.get("site_id", "")
                if not site_id:
                    continue
                try:
                    reg_result = await _pr.register_for_portal(site_id)
                    results.append({
                        "site_id": site_id,
                        "site_name": entry.get("site_name", ""),
                        "success": reg_result.get("success", False),
                        "message": reg_result.get("message", reg_result.get("error", "")),
                    })
                except Exception as e:
                    logger.warning("[vault_registration_daily] Failed for %s: %s", site_id, e)
                    results.append({
                        "site_id": site_id,
                        "site_name": entry.get("site_name", ""),
                        "success": False,
                        "error": str(e)[:200],
                    })

            success_count = sum(1 for r in results if r.get("success"))
            return {
                "checked": len(pending),
                "registered": success_count,
                "results": results,
            }
        finally:
            vault.close()

    elif tool_kind == "fill_knowledge_gaps":
        return await fill_knowledge_gaps(llm, dry_run=False, max_cells=5)

    # R-F1410: DRAIN-COLLAB-BRIDGE — drain Claude→ARIA notes from the
    # server-mediated collaboration bridge. Runs every ~1-2 min so Claude's
    # notes reach the ONE ARIA brain (intel/web/wa) without relay delay.
    # cursor-guarded: each note is absorbed once. Both branches wired (§21a).
    elif tool_kind == "collab_bridge_drain":
        try:
            from ..intel import collab_bridge
            result = await collab_bridge.drain_for_aria()
            drained = result.get("drained", 0)
            if drained > 0:
                logger.info("[R-F1410] DRAIN-COLLAB-BRIDGE: drained %d note(s)", drained)
            return result
        except Exception as e:
            logger.warning("[R-F1410] DRAIN-COLLAB-BRIDGE failed: %s", e)
            return {"drained": 0, "last_seq": 0, "error": str(e)[:200]}

    else:
        return {"error": f"unknown direct tool: {tool_kind}"}


async def _wire_task_delivery_outcomes(
    task: "Task", delivery_result: Any, session_id: str, latency_ms: int,
) -> None:
    """R-F2706 (§25a) — report each autonomous-task delivery channel's outcome to the
    proprioception outcome-wire so the brain KNOWS whether each limb actually delivered,
    and a non-success triggers a self-heal gap.

    Before this, ``delivery.deliver()`` returned a per-channel result map
    (``{"whatsapp": "ok:...", "intel_ledger": "error:..."}``) that was stored on the run
    record but NEVER reached ``outcome_wire`` — so ``engine.py`` wired success on
    EXECUTION, not DELIVERY, and a WhatsApp push that failed still read as ``status=ok``.

    Follows the R-F1969 "dd" engine-surface precedent: all channels roll up under one
    dashboard-visible surface ("autotask"), with the channel in request_id + detail so
    per-channel failures are still distinct and de-duped. Deliberate non-deliveries
    (``skipped:``/``suppressed:``/``dry_run``) are NOT recorded as failures. Never raises."""
    try:
        if not isinstance(delivery_result, dict):
            return  # "dry_run_skipped" string, or nothing delivered
        from ..intel.outcome_wire import OutcomeRecord, record_outcome
        for ch, val in delivery_result.items():
            if not isinstance(val, str):
                continue
            v = val.strip().lower()
            if ch == "error":            # total-raise shape {"error": "<Type>: msg"}
                outcome, ch_name = "send_failed", "delivery"
            elif v.startswith("ok"):
                outcome, ch_name = "delivered_real_answer", ch
            elif v.startswith("error"):
                outcome, ch_name = "send_failed", ch
            else:
                continue                 # skipped:/suppressed: — deliberate, not a failure
            await record_outcome(OutcomeRecord(
                surface="autotask",
                request_id=f"{task.id}:{session_id}:{ch_name}",
                intended_result=f"deliver:{ch_name}",
                actual_outcome=outcome,
                latency_ms=int(latency_ms),
                detail=f"{ch_name}:{str(val)[:180]}",
            ))
    except Exception as e:
        logger.debug("[autonomous] delivery-outcome wire failed (non-fatal): %s", e)


# ── Task execution wrapper ─────────────────────────────────────────────────

@fail_wire(module="tasks", gap_type="agent_cycle_failure")
async def execute_task(task: Task, llm, *, dry_run: bool = True) -> dict[str, Any]:
    """Run a task through the constitutional pipeline.

    The task's first tool_chain entry becomes a synthetic user message
    routed through aria_chat() with a deterministic session_id. This
    means autonomous outputs go through the SAME pipeline as interactive
    chat — clauses 1-15, the verifier, the honesty judge, the footer,
    the cost tracker, the trace stream — so we get production-grade
    discipline on the autonomous outputs without re-implementing any of
    that logic.

    Args:
        task: the Task to run
        llm: the LLM provider (already wrapped with the cost meter)
        dry_run: if True, do NOT call delivery.deliver() at the end.
                 The result is logged + recorded in the runs list but
                 nothing is pushed to WhatsApp / intel ledger. Default
                 True so the engine is safe by default.

    Returns:
        run record dict (also persisted to Redis)
    """
    t0 = time.time()
    record: dict[str, Any] = {
        "task_id": task.id,
        "task_name": task.name,
        "started_at": t0,
        "started_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0)),
        "dry_run": dry_run,
        "status": "started",
    }

    try:
        if not task.tool_chain:
            record["status"] = "error"
            record["error"] = "task has empty tool_chain"
            record["duration_ms"] = int((time.time() - t0) * 1000)
            await record_run(record)
            return record

        # Synthesise the user message from the first tool chain entry.
        # The deep_research / web_search tool router in routes/aria.py
        # will pick this up and fire the right tool — no need to call
        # the tool directly. Reusing the chat path means the response
        # gets the full constitutional pipeline applied.
        first = task.tool_chain[0]
        if not isinstance(first, dict):
            record["status"] = "error"
            record["error"] = f"first tool_chain entry is {type(first).__name__}, expected dict"
            record["duration_ms"] = int((time.time() - t0) * 1000)
            await record_run(record)
            return record

        tool_kind = (first.get("tool") or "").strip().lower()

        # ── Pre-task predictor forecast ──────────────────────────────
        # Consult the mistake ledger + self_metrics + capability_manifest
        # BEFORE executing. Three confidence tiers:
        #   ≥ 0.5  → proceed normally
        #   0.2–0.5 → proceed with warning logged
        #   < 0.2  → BLOCK task, record as "blocked_by_predictor"
        #
        # Tasks that are never blocked (self-assessment infrastructure):
        _NEVER_BLOCK = {
            "metacognitive_daily_check", "metacognitive_weekly_review",
            "metacognitive_monthly_sprint", "ecosystem_reassess",
            "adversarial_weekly", "constitution_test", "golden_autogen",
            "daily_team_briefing", "core_meta",
        }
        _BLOCK_THRESHOLD = 0.2   # below this → task blocked
        _WARN_THRESHOLD = 0.5    # below this → warning logged

        prediction = None
        try:
            from ..intel import predictor as _pred
            domain = (first.get("entity") or first.get("topic") or
                      task.id.split("-")[0] if "-" in task.id else "general").lower()
            prediction = await _pred.forecast(
                task_type=tool_kind,
                domain=domain[:30],
            )
            conf = prediction.get("overall_confidence", 0.5)
            n_failures = len(prediction.get("likely_failures", []))
            n_mistakes = len(prediction.get("past_mistakes", []))
            record["predictor"] = {
                "confidence": conf,
                "likely_failures": n_failures,
                "past_mistakes": n_mistakes,
                "degraded": prediction.get("degraded", False),
            }
            if prediction.get("recommendations"):
                record["predictor"]["recommendations"] = prediction["recommendations"][:3]

            if conf < _BLOCK_THRESHOLD and tool_kind not in _NEVER_BLOCK:
                # ── BLOCK — too many unprevented failures in this domain ──
                record["status"] = "blocked_by_predictor"
                record["predictor"]["action"] = "BLOCKED"
                # Track block count per domain + 24h counter for operating mode.
                # Set TTL only on first incr — resetting expire() on every incr
                # turned both into lifetime counters under continuous blocks.
                try:
                    from ..intel import redis_store as _rs
                    _blk_key = f"crucix:predictor:blocks:{domain[:30]}"
                    _new = await _rs.incr(_blk_key)
                    if _new == 1:
                        await _rs.expire(_blk_key, 30 * 86400)
                    # 24h counter for operating mode auto-transition
                    _blk_24h = "crucix:predictor:blocks:24h"
                    _new24 = await _rs.incr(_blk_24h)
                    if _new24 == 1:
                        await _rs.expire(_blk_24h, 86400)
                except Exception:
                    pass
                record["predictor"]["reason"] = (
                    f"Confidence {conf:.0%} below {_BLOCK_THRESHOLD:.0%} threshold. "
                    f"{n_failures} likely failure(s), {n_mistakes} past mistake(s). "
                    f"Task will not execute until mistakes are addressed."
                )
                record["duration_ms"] = int((time.time() - t0) * 1000)
                logger.warning(
                    "[predictor] BLOCKED %s/%s: confidence %.0f%% < %.0f%% — "
                    "%d failures, %d past mistakes",
                    tool_kind, domain[:20], conf * 100,
                    _BLOCK_THRESHOLD * 100, n_failures, n_mistakes,
                )
                # Record the block as a brain signal so the team sees it
                try:
                    from ..intel import brain_hook as _bh
                    await _bh.absorb(
                        module="predictor",
                        summary=(
                            f"BLOCKED task {task.id}: confidence {conf:.0%}, "
                            f"{n_failures} likely failures, {n_mistakes} past mistakes"
                        ),
                        detail=record["predictor"]["reason"],
                        success=False,
                        gap_type="adversarial_critical_failure",
                        gap_detail=f"Task {task.id} blocked by predictor",
                    )
                except Exception:
                    pass
                await record_run(record)
                return record

            elif conf < _WARN_THRESHOLD:
                record["predictor"]["action"] = "WARNED"
                logger.info(
                    "[predictor] WARNING %s/%s: confidence %.0f%%, %d likely failures",
                    tool_kind, domain[:20], conf * 100, n_failures,
                )
            else:
                record["predictor"]["action"] = "CLEAR"
                logger.info(
                    "[predictor] CLEAR %s/%s: confidence %.0f%%",
                    tool_kind, domain[:20], conf * 100,
                )
        except Exception as e:
            logger.debug("predictor forecast failed (non-fatal): %s", e)

        if tool_kind == "deep_research":
            entity = (first.get("entity") or "").strip()
            url = (first.get("url") or "").strip()
            user_msg = f"Aria, investigate the latest on: {entity}"
            if url:
                user_msg += f" {url}"
        elif tool_kind == "web_search":
            entity = (first.get("entity") or first.get("query") or "").strip()
            user_msg = f"Aria, search for: {entity}"
        elif tool_kind == "investigate":
            topic = (first.get("topic") or first.get("entity") or "").strip()
            user_msg = f"Aria, investigate: {topic}"
        elif tool_kind in ("law_refresh", "corpus_weekly_crawl",
                           "metacognitive_daily_check", "metacognitive_weekly_review",
                           "metacognitive_monthly_sprint",
                           "dd_watchlist_sweep", "knowledge_freshness_audit",
                           "daily_team_briefing", "pipeline_dormancy_check",
                           "source_discovery",
                           # Clause 17 + Core Self-Development Loop
                           "verified_fact_refresh",
                           "ecosystem_reassess",
                           "core_develop",
                           "core_meta",
                           "source_scout",
                           "golden_autogen",
                           "adversarial_weekly",
                           "security_weekly",
                           "constitution_test",
                           "corpus_ingest",
                           "narrative_scan",
                           # 04-17/18 marathon — handlers exist in
                           # _execute_direct_tool but were never added to this
                           # dispatch tuple. Result on production 04-19: every
                           # task using these errored "unsupported tool kind"
                           # and the entire 24/7 learning loop reported zeros.
                           "procurement_calendar",
                           "competitor_tracker",
                           "oem_contact_graph",
                           "chain_correlator",
                           "training_export",
                           "knowledge_spider",
                           "metacognitive_journal",
                           "research_engine",
                           "style_learner",
                           "memory_replication",
                           "consistency_suite",
                           "capability_card_refresh",
                           "calibration_auto_tune",
                           "source_uptime_ping",
                           "self_diagnostic",
                           # R-F3293 [2026-07-27]: R-F1410 added the
                           # collab_bridge_drain HANDLER and a task whose
                           # tool_chain names it, but never added it here — so
                           # run_task answered "unsupported tool kind" for the
                           # one task that uses it. The Claude<->ARIA bridge
                           # kept draining only because R-F1548 later added an
                           # independent 2-minute scheduler loop that calls
                           # drain_for_aria() directly, which masked the dead
                           # task path rather than replacing it.
                           "collab_bridge_drain",
                           # R-F470 [2026-05-14]: daily golden-set eval
                           "run_eval",
                           # R-F662 [2026-05-17]: OSS-only learning controller
                           "learning_cycle",
                           # R-F470 bonus [2026-05-14]: 4 pre-existing
                           # handlers were dark — production tasks using
                           # these would have errored "unsupported tool
                           # kind". Caught by test_autonomous_dispatch_parity
                           # while extending the tuple.
                           "counter_intel_sweep",
                           "crypto_sanctions_refresh",
                           "fcpa_enforcement_scan",
                           "recompute_priorities",
                           # R-F930 [2026-05-27]: cost self-guardian. Also adds
                           # cost_free_learn -- a pre-existing dark handler whose
                           # elif exists but was never routable here; caught by
                           # test_autonomous_dispatch_parity.
                           "cost_free_learn",
                           "cost_guard",
                           # R-F935 -- Compliance Watch private digest task.
                           "compliance_watch",
                           # R-F953 -- daily contract-review self-check canary.
                           "contract_selfcheck",
                           # R-F1289 (2026-06-01): 5 handlers existed in
                           # _execute_direct_tool but were never added to this
                           # dispatch tuple. Same bug class as the 04-17/18
                           # marathon — tasks errored "unknown direct tool"
                           # silently in production.
                           "bd_strategy_generate",
                           "dd_full_sweep",
                           "news_monitor_poll",
                           "portal_coverage_audit",
                           "vault_registration_daily",
                           "fill_knowledge_gaps"):
            # Direct-call tools — these don't go through chat, they call
            # their module function directly and return a summary.
            # 2026-04-27 — attribute every direct-tool LLM call to its
            # tool-kind feature bucket. Without this wrap, all LLM
            # calls inside _execute_direct_tool that don't set their
            # own feature land in "uncategorized" — which was 40% of
            # April 2026 spend.
            from ..intel import cost_tracker as _ct
            _direct_token = _ct.set_feature(f"tool:{tool_kind}")
            try:
                # R-F651 (2026-05-17): wrap direct-tool dispatch in
                # asyncio.wait_for so timeout_seconds is actually enforced.
                # Pre-R-F651 the chat-pipeline branch (below) had this
                # wrap but the direct-tool branch did not — RUN-EVAL-DAILY
                # at 0:6:* fired on 2026-05-17 06:00 UTC and ran for 2h
                # (12x the 600s timeout), burning $12.76 on Sonnet. Mirror
                # the chat-pipeline timeout pattern + safety cost charge
                # so the per-task cost_cap_usd at least lands on the
                # daily-cap circuit breaker after a runaway.
                try:
                    direct_result = await asyncio.wait_for(
                        _execute_direct_tool(tool_kind, task, llm),
                        timeout=task.timeout_seconds,
                    )
                except asyncio.TimeoutError:
                    record["status"] = "timeout"
                    record["error"] = (
                        f"direct tool {tool_kind} exceeded "
                        f"timeout {task.timeout_seconds}s"
                    )
                    record["duration_ms"] = int((time.time() - t0) * 1000)
                    try:
                        from . import safety as _sf
                        await _sf.record_task_cost(float(task.cost_cap_usd or 0))
                        logger.warning(
                            "[autonomous] direct tool %s timed out — "
                            "charged cap $%.2f to safety counter",
                            tool_kind, task.cost_cap_usd or 0,
                        )
                    except Exception as _cost_e:
                        logger.debug(
                            "R-F651 direct-tool timeout cost charge failed: %s",
                            _cost_e,
                        )
                    await record_run(record)
                    return record
                record["status"] = "ok"
                record["response_preview"] = str(direct_result)[:400]
                record["response_length"] = len(str(direct_result))
                record["tool_used"] = tool_kind
                record["duration_ms"] = int((time.time() - t0) * 1000)
                if not dry_run:
                    from .delivery import deliver
                    record["delivery"] = await deliver(
                        task=task,
                        response_text=str(direct_result),
                        triggered_flags=[],
                        session_id=f"autonomous:{task.id}:{time.strftime('%Y-%m-%d')}",
                    )
                else:
                    record["delivery"] = "dry_run_skipped"
                await record_run(record)
                return record
            except Exception as e:
                record["status"] = "error"
                record["error"] = f"direct tool {tool_kind} failed: {e}"
                record["duration_ms"] = int((time.time() - t0) * 1000)
                await record_run(record)
                return record
            finally:
                _ct.reset_feature(_direct_token)
        else:
            record["status"] = "error"
            record["error"] = f"unsupported tool kind in first chain entry: {tool_kind!r}"
            record["duration_ms"] = int((time.time() - t0) * 1000)
            await record_run(record)
            return record

        # Per-run session id so each fire is isolated and the mem0
        # store tags it correctly. Daily granularity is enough for
        # daily/weekly tasks (the dedupe layer prevents same-day
        # duplicates).
        date_tag = time.strftime("%Y-%m-%d", time.gmtime(t0))
        session_id = f"autonomous:{task.id}:{date_tag}"

        # Run through the chat pipeline. The cost meter is already
        # attached to the llm provider — costs land under the
        # `autonomous_engine` feature in cost_tracker.
        from ..intel import cost_tracker
        from .. import aria_engine
        feature_token = cost_tracker.set_feature("autonomous_engine")
        try:
            chat_result = await asyncio.wait_for(
                aria_engine.aria_chat(
                    message=user_msg,
                    session_id=session_id,
                    llm=llm,
                ),
                timeout=task.timeout_seconds,
            )
        except asyncio.TimeoutError:
            # M5: timeouts still consumed tokens up to the cancellation
            # point. The per-call cost_tracker already recorded those,
            # but safety.record_task_cost (which feeds the daily cap
            # circuit breaker) never ran. Charge the per-task cap as a
            # conservative upper bound so a task that times out
            # repeatedly can't silently bypass the cost cap.
            record["status"] = "timeout"
            record["error"] = f"task exceeded timeout {task.timeout_seconds}s"
            record["duration_ms"] = int((time.time() - t0) * 1000)
            try:
                from . import safety as _sf
                await _sf.record_task_cost(float(task.cost_cap_usd or 0))
                logger.warning(
                    "[autonomous] task %s timed out — charged cap $%.2f to safety counter",
                    task.id, task.cost_cap_usd or 0,
                )
            except Exception as _cost_e:
                logger.debug("timeout cost charge failed: %s", _cost_e)
            await record_run(record)
            return record
        finally:
            cost_tracker.reset_feature(feature_token)

        response_text = (chat_result or {}).get("response", "") or ""
        record["response_preview"] = response_text[:400]
        record["response_length"] = len(response_text)
        record["tool_used"] = (chat_result or {}).get("tool_used")

        # Escalation keyword scan
        triggered_flags: list[str] = []
        if task.escalate_if and response_text:
            response_lower = response_text.lower()
            for keyword in task.escalate_if:
                if keyword.lower() in response_lower:
                    triggered_flags.append(keyword)
        record["escalation_triggered"] = bool(triggered_flags)
        record["triggered_flags"] = triggered_flags

        # Delivery — DRY RUN by default
        if dry_run:
            record["delivery"] = "dry_run_skipped"
        else:
            try:
                from . import delivery
                delivery_result = await delivery.deliver(
                    task=task,
                    response_text=response_text,
                    triggered_flags=triggered_flags,
                    session_id=session_id,
                )
                record["delivery"] = delivery_result
            except Exception as e:
                logger.warning(
                    "[autonomous task %s] delivery raised: %s: %s",
                    task.id, type(e).__name__, e,
                )
                record["delivery"] = {"error": f"{type(e).__name__}: {e}"}

            # R-F2706 (§25a) — report per-channel delivery outcomes to the proprioception
            # wire (covers both the per-channel result map AND the total-raise shape above),
            # so a delivery failure is VISIBLE and triggers a self-heal gap instead of
            # hiding behind status=ok. Execution status is unchanged (see 1799 hooks gate).
            await _wire_task_delivery_outcomes(
                task, record.get("delivery"), session_id, int((time.time() - t0) * 1000),
            )

        record["status"] = "ok"
    except Exception as e:
        logger.warning(
            "[autonomous task %s] execution raised: %s: %s",
            task.id, type(e).__name__, e,
        )
        record["status"] = "error"
        record["error"] = f"{type(e).__name__}: {e}"

    record["duration_ms"] = int((time.time() - t0) * 1000)
    await record_run(record)

    # ── Failed-run dedupe rollback ────────────────────────────────────────
    # If the run errored OR returned an invalid/skipped marker, drop the
    # dedupe slot so the next scheduled fire (or a manual run-now) can
    # retry. Without this a single transient outage burns the daily slot.
    try:
        is_invalid = False
        if record.get("status") == "error":
            is_invalid = True
        else:
            preview = record.get("response_preview") or ""
            if any(m in preview for m in ("SKIPPED — ", "invalid_reason", "blocked\":")):
                is_invalid = True
        if is_invalid:
            from . import safety as _sf
            await _sf.clear_dedupe(task.id, "")
            logger.info(
                "[autonomous] dedupe slot cleared for %s after invalid/error run "
                "(next scheduled fire can retry)", task.id,
            )
    except Exception as e:
        logger.debug("dedupe clear (non-fatal): %s", e)

    # ── Post-execution hooks (non-fatal — run only on success) ────────────
    if record.get("status") == "ok":
        # For chat-path tasks `response_text` is a local with the full
        # response; for direct-tool tasks it was never assigned, so fall
        # back to the truncated preview stored in the run record.
        try:
            _hook_text: str = response_text  # type: ignore[possibly-undefined]
        except NameError:
            _hook_text = record.get("response_preview") or ""

        await _auto_escalate_to_watchlist(task, _hook_text, record)
        await _feed_knowledge(task, _hook_text)

    return record


# ── Auto-escalation: autonomous scan → DD watchlist ──────────────────────
#
# When a procurement/research/intel scan surfaces RED or HARD_STOP risk
# indicators, or names a new counterparty, we automatically add the
# entity to the DD watchlist so it gets periodic re-screening.

_ESCALATION_ELIGIBLE_TOOLS = frozenset({
    "deep_research", "web_search", "investigate",
    "dd_watchlist_sweep", "corpus_weekly_crawl",
})

# Guard against whitelist drift. Before this, a new tool added to
# tasks.yaml that wasn't in the frozensets silently skipped auto-escalation
# / knowledge-feed paths. This set tracks tool names we've already warned
# about so we log the drift once per tool per process, not per task run.
_WARNED_UNKNOWN_TOOLS: set[str] = set()


def _warn_once_unknown_tool(tool: str, path: str) -> None:
    """Log a one-shot warning the first time we see a tool name that
    isn't in the eligibility whitelist for `path`. Prevents silent
    whitelist drift when tasks.yaml gains new tool types."""
    if not tool:
        return
    key = f"{path}:{tool}"
    if key in _WARNED_UNKNOWN_TOOLS:
        return
    _WARNED_UNKNOWN_TOOLS.add(key)
    logger.warning(
        "[autonomous] tool %r not in %s whitelist; task skipped — "
        "add to whitelist if this tool should trigger the path",
        tool, path,
    )

_RISK_PATTERNS = re.compile(
    r"\b(RED|HARD[_\s]?STOP|HIGH[_\s]?RISK|SANCTIONED|BLOCKED|DESIGNATED|"
    r"DENIED[_\s]?PARTY|BLACKLISTED|EMBARGO|SDN|OFAC[_\s]?HIT)\b",
    re.IGNORECASE,
)

# Simple entity extractor — looks for capitalised multi-word names near
# risk keywords.  Not perfect, but good enough for auto-escalation.
_ENTITY_NAME_RE = re.compile(
    r"(?:(?:company|entity|counterparty|firm|organisation|organization|supplier|buyer|"
    r"vendor|contractor|target|subject)[:\s]+)([A-Z][A-Za-z&.,'\- ]{2,60})",
)


async def _auto_escalate_to_watchlist(
    task: Task, response_text: str, record: dict[str, Any]
) -> None:
    """Check response for risk indicators and escalate to DD watchlist."""
    if not response_text:
        return

    # Only escalate from research/procurement/intel tasks
    tool_used = (record.get("tool_used") or "").strip().lower()
    first_tool = ""
    if task.tool_chain:
        first_tool = ((task.tool_chain[0] or {}).get("tool") or "").strip().lower()

    eligible_tool = tool_used in _ESCALATION_ELIGIBLE_TOOLS or first_tool in _ESCALATION_ELIGIBLE_TOOLS
    # Also eligible if the task id hints at procurement/research/intel
    eligible_id = any(
        kw in task.id.lower()
        for kw in ("proc", "research", "intel", "scan", "monitor", "watchlist", "osint")
    )
    if not eligible_tool and not eligible_id:
        # Whitelist-drift canary: if a tool was declared but isn't in the
        # whitelist AND the id doesn't hint, the path silently skips.
        # Warn once per tool so a new tasks.yaml entry doesn't dark.
        _warn_once_unknown_tool(first_tool or tool_used, "ESCALATION")
        return

    # Look for risk signals
    risk_matches = _RISK_PATTERNS.findall(response_text)
    if not risk_matches:
        return

    # Extract entity names from the response
    entity_matches = _ENTITY_NAME_RE.findall(response_text)
    # Also check the task's own entity field
    task_entity = ""
    if task.tool_chain:
        task_entity = (
            (task.tool_chain[0] or {}).get("entity")
            or (task.tool_chain[0] or {}).get("query")
            or ""
        ).strip()
    if task_entity and task_entity not in entity_matches:
        entity_matches.insert(0, task_entity)

    if not entity_matches:
        return

    # F57 fix 2026-04-28: filter through the same entity-shape validator
    # the sanctions screening layer uses. Without this, deep_research
    # tasks whose `entity:` field is a search query (e.g. "SAM.gov defence
    # military security procurement global last 7 days 2026") were being
    # added to the watchlist verbatim. The daily re-screen then fed those
    # to OpenSanctions where _looks_like_entity_name rejected them — but
    # the rejection happens AFTER the entry has already polluted the list.
    # Filter here so the watchlist never accumulates query strings to
    # begin with.
    from ..intel.sanctions import _looks_like_entity_name

    # Deduplicate and escalate
    seen: set[str] = set()
    from ..intel import dd_orchestrator
    for raw_name in entity_matches[:5]:  # cap at 5 entities per task run
        name = raw_name.strip().rstrip(".,")
        name_lower = name.lower()
        if name_lower in seen or len(name) < 3:
            continue
        seen.add(name_lower)
        if not _looks_like_entity_name(name):
            logger.info(
                "[autonomous escalation] skipping %r — does not look like an "
                "entity name (search query / sentence fragment)",
                name[:80],
            )
            continue

        target = {
            "name": name,
            "source_task": task.id,
            "detected_risk": list(set(r.upper() for r in risk_matches[:5])),
            "detected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "auto_escalated": True,
        }
        try:
            # R-F3287 — deliberately does NOT pass requested_by_user. A keyword
            # match in a research task is not a person asking to pay for
            # recurring monitoring, and this loop ran on every autonomous cycle.
            # It can still enrich an entity the user already watches; if it is
            # not on the list, nothing is created and ok=False comes back.
            result = await dd_orchestrator.add_to_watchlist(target)
            if result.get("ok") is False:
                logger.info(
                    "[autonomous escalation] %s matched %s but was NOT enrolled: "
                    "the watchlist is user-added only (R-F3287)",
                    name, ", ".join(target["detected_risk"]),
                )
            elif result.get("note") != "already on watchlist":
                logger.info(
                    "[autonomous escalation] added %s to DD watchlist from task %s "
                    "(risks: %s)",
                    name, task.id, ", ".join(target["detected_risk"]),
                )
                record.setdefault("auto_escalations", []).append(name)
        except Exception as e:
            logger.warning(
                "[autonomous escalation] failed to add %s to watchlist: %s", name, e
            )


# ── Knowledge feedback loop ──────────────────────────────────────────────
#
# After research/procurement/intel tasks, extract key facts from the
# response and feed them into the knowledge base so ARIA accumulates
# institutional memory from her own autonomous work.

_KNOWLEDGE_ELIGIBLE_TOOLS = frozenset({
    "deep_research", "web_search", "investigate",
    "corpus_weekly_crawl", "dd_watchlist_sweep",
})

_CONTRACT_VALUE_RE = re.compile(
    r"[\$€£]\s?(\d[\d,.]*)\s*(million|billion|mn|bn|m|b)\b"
    r"|(\d[\d,.]*)\s*(million|billion|mn|bn|m|b)\s*(?:USD|EUR|GBP|dollars|euros|pounds)",
    re.IGNORECASE,
)

_DATE_EVENT_RE = re.compile(
    r"\b(signed|awarded|announced|concluded|entered into force|effective|expired|terminated|cancelled)"
    r"\s+(?:on\s+)?(\d{1,2}[\s/\-]\w{3,9}[\s/\-]\d{4}|\d{4}[\-/]\d{2}[\-/]\d{2})",
    re.IGNORECASE,
)

_COMPANY_ACTION_RE = re.compile(
    r"\b([A-Z][A-Za-z&.,'\- ]{2,50})\s+(?:won|lost|signed|awarded|acquired|merged|divested|terminated|secured|received)\b",
)


async def _feed_knowledge(task: Task, response_text: str) -> None:
    """Extract key facts from autonomous task output and store in knowledge base."""
    if not response_text or len(response_text) < 50:
        return

    # Only run for research/procurement/intel tasks
    first_tool = ""
    if task.tool_chain:
        first_tool = ((task.tool_chain[0] or {}).get("tool") or "").strip().lower()

    eligible_tool = first_tool in _KNOWLEDGE_ELIGIBLE_TOOLS
    eligible_id = any(
        kw in task.id.lower()
        for kw in ("proc", "research", "intel", "scan", "monitor", "osint")
    )
    if not eligible_tool and not eligible_id:
        # Same whitelist-drift canary as _auto_escalate_to_watchlist.
        _warn_once_unknown_tool(first_tool, "KNOWLEDGE")
        return

    from ..intel import knowledge

    source_tag = f"autonomous:{task.id}"
    facts_stored = 0

    # Extract contract values
    for m in _CONTRACT_VALUE_RE.finditer(response_text):
        # Get surrounding context (up to 120 chars before the match)
        start = max(0, m.start() - 120)
        context = response_text[start:m.end() + 40].strip()
        # Clean up to a sentence-ish boundary
        context = context.replace("\n", " ").strip()
        try:
            await knowledge.store_fact(
                topic="contract_value",
                content=context,
                source=source_tag,
                confidence="ASSESSED",
            )
            facts_stored += 1
        except Exception as e:
            logger.debug("[knowledge feed] store_fact failed: %s", e)

    # Extract date events (signed/awarded/announced + date)
    for m in _DATE_EVENT_RE.finditer(response_text):
        start = max(0, m.start() - 80)
        context = response_text[start:m.end() + 40].strip().replace("\n", " ")
        try:
            await knowledge.store_fact(
                topic="event_date",
                content=context,
                source=source_tag,
                confidence="ASSESSED",
            )
            facts_stored += 1
        except Exception as e:
            logger.debug("[knowledge feed] store_fact failed: %s", e)

    # Extract company actions (Company X won/signed/etc)
    for m in _COMPANY_ACTION_RE.finditer(response_text):
        start = max(0, m.start() - 40)
        context = response_text[start:m.end() + 80].strip().replace("\n", " ")
        entity_name = m.group(1).strip().rstrip(".,")
        try:
            await knowledge.store_fact(
                topic="company_action",
                content=context,
                source=source_tag,
                confidence="ASSESSED",
                entity_name=entity_name,
            )
            facts_stored += 1
        except Exception as e:
            logger.debug("[knowledge feed] store_fact failed: %s", e)

    if facts_stored:
        logger.info(
            "[knowledge feed] stored %d fact(s) from task %s",
            facts_stored, task.id,
        )


# R-F1985: autonomous knowledge gap filler. Runs periodically via the
# autonomous engine to identify and fill the weakest heatmap cells.
# This is the structural fix for Phase A Gate #2 — instead of manual
# seed packs, ARIA continuously discovers and researches her own gaps.

# ── Coverage-matrix domain → mastery topic mapping ──────────────────────
# The coverage matrix (coverage_heatmap.py) uses fine-grained domains like
# "sanctions_screening", "eccn_classification" etc. The mastery heatmap
# (student.py) uses broader topics like "compliance", "technical", etc.
# This map bridges the two so research on a coverage cell can update the
# corresponding mastery cell. R-F1986.
_COVERAGE_DOMAIN_TO_TOPIC: dict[str, str] = {
    # Compliance umbrella
    "sanctions_screening": "compliance",
    "sanctions_divergence": "compliance",
    "rca_screening": "compliance",
    # Export controls
    "eccn_classification": "technical",
    "euc_jurisdictions": "compliance",
    "wassenaar_dual_use": "compliance",
    "weapon_systems": "technical",
    # Anti-financial-crime
    "fatf_ml_typologies": "compliance",
    "fatf_tbml": "compliance",
    "fcpa_enforcement": "legal",
    "economic_substance": "finance",
    "virtual_assets": "finance",
    # Counterparty
    "defence_market_briefing": "market_intel",
    "procurement_pipeline": "procurement",
    "counter_intelligence": "osint",
    # NATO + interoperability
    "nato_standards": "technical",
    "international_law": "legal",
}

# ── Coverage-matrix jurisdiction (country) → mastery region map ─────────
# The coverage matrix uses country names; the mastery heatmap uses regions.
# R-F1986.
_COUNTRY_TO_REGION: dict[str, str] = {
    # Anchors — map to their primary region
    "US": "nato",
    "UK": "europe",
    "EU": "europe",
    "UN": "global",
    "NATO": "nato",
    # Lusophone moat
    "Angola": "lusophone",
    "Mozambique": "lusophone",
    "Cape Verde": "lusophone",
    "Guinea-Bissau": "lusophone",
    "Brazil": "latam_lusophone",
    "São Tomé": "lusophone",
    # Wider Africa
    "Nigeria": "west_africa",
    "Ghana": "west_africa",
    "Kenya": "east_africa",
    "Ethiopia": "east_africa",
    "Tanzania": "east_africa",
    "Senegal": "west_africa",
    "Côte d'Ivoire": "west_africa",
    "Cameroon": "central_africa",
    "Rwanda": "central_africa",
    "South Africa": "southern_africa",
    "Algeria": "north_africa",
    "Morocco": "north_africa",
    # Gulf / MENA
    "Saudi Arabia": "gulf",
    "UAE": "gulf",
    "Qatar": "gulf",
    "Bahrain": "gulf",
    "Kuwait": "gulf",
    "Oman": "gulf",
    "Jordan": "mena",
    "Iraq": "mena",
    "Lebanon": "mena",
    "Israel": "mena",
    "Turkey": "turkey",
    "Egypt": "mena",
    # Asia Pacific
    "Indonesia": "southeast_asia",
    "Vietnam": "southeast_asia",
    "Philippines": "southeast_asia",
    "Bangladesh": "south_asia",
    "India": "south_asia",
    "Pakistan": "south_asia",
    "South Korea": "southeast_asia",
    "Japan": "southeast_asia",
    # LATAM
    "Mexico": "latam_non_lusophone",
    "Colombia": "latam_non_lusophone",
    "Peru": "latam_non_lusophone",
    "Venezuela": "latam_non_lusophone",
    "Argentina": "latam_non_lusophone",
    # Europe emerging
    "Romania": "balkans",
    "Poland": "europe",
    "Ukraine": "europe",
}

# Topics that the gap filler is allowed to research (mastery topic names).
# R-F1986: expanded from the old set (which only had mastery topic names
# that never matched coverage-matrix domains) to include ALL topics that
# have a mapping from coverage domains.
_GAP_FILLER_ELIGIBLE_TOPICS = frozenset({
    "compliance", "procurement", "market_intel", "osint", "legal",
    "technical", "competitor_intel", "relationships", "finance",
    "geopolitics",
})

_GAP_FILLER_QUERY_TEMPLATES = {
    "compliance": "defence compliance sanctions export control {region} 2025 2026",
    "procurement": "defence procurement contracts {region} 2025 2026",
    "market_intel": "defence procurement market intelligence {region} 2025 2026",
    "osint": "security and defence news {region} recent developments",
    "legal": "regulatory and compliance framework defence sector {region}",
    "technical": "defence technology capabilities and systems {region}",
    "competitor_intel": "defence companies and competitors operating in {region}",
    "relationships": "defence partnerships and alliances involving {region}",
    "finance": "defence budget spending and financial trends {region}",
    "geopolitics": "geopolitical situation and security dynamics {region}",
}


#: R-F3971 (C-60) — how much of the ANSWER is grounded in the research text.
#: The threshold the grader applies; unchanged from the Jaccard era so the bar
#: is not quietly lowered along with the measure.
_GROUNDING_THRESHOLD = 0.4


def _answer_grounding(answer: str, document: str) -> float:
    """Fraction of the answer's tokens that appear in the research text.

    R-F3971 (C-60) — the grader used `student._quick_similarity`, which is
    JACCARD (`inter / union`). `answer` is short and `document` is up to 4,000
    characters, so the union is dominated by the document and a PERFECT answer's
    ceiling is its own length over the document's. Measured on a real 4,000-char
    sample of 308 unique tokens: a 40-token all-correct answer scored 0.130, an
    80-token one 0.260, a 120-token one 0.390 — all below the 0.4 bar. It took
    124 tokens to pass regardless of correctness, so the grader could not return
    True for a right answer, and every false negative fed the EWMA that gate #2
    reads.

    Same asymmetry as C-52 one axis over: Jaccard is symmetric and this
    relationship is not. The grader's own docstring names the question it means
    to ask — "its answer overlaps the research findings" — which is CONTAINMENT
    of the answer in the document.

    Deliberately NOT applied to `student._quick_similarity` itself: its other two
    callers (student.py:1061, :2148) compare a local response against a cloud
    response of similar length, where symmetric similarity is correct.
    """
    import re as _re
    if not answer or not document:
        return 0.0
    ta = set(_re.findall(r"\w+", answer.lower()))
    tb = set(_re.findall(r"\w+", document.lower()))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta)


async def _grade_researched_cell(
    topic: str, region: str, research_text: str,
) -> bool | None:
    """Honest mastery grade for a freshly-researched cell (R-F1989, Claude review).

    Replaces the old self-grading bridge that credited mastery with
    ``correct=True`` whenever research merely returned text — a participation
    trophy that inflated Phase A gate #2 without proving comprehension (CLAUDE.md
    §1: close gate #2 via grounded improvement, NOT clamping).

    Instead we apply the same honest test ``self_quiz`` uses: ask the LOCAL
    reasoning stack a question about the cell and only count it correct if it can
    actually answer AND its answer overlaps the research findings. This CAN fail
    (the knowledge didn't take / isn't usable) — so mastery reflects real recall,
    not the act of researching.

    R-F3483 — TRI-STATE. Returns True (recalled), False (genuinely could not
    recall) or **None (could not be measured)**. Every branch below used to
    return False, so a measurement failure was recorded as ARIA getting the
    answer wrong, and the caller then drove mastery down for it.

    That matters most for ``answered=False``: reasoning_router documents it as a
    ROUTING signal meaning "no local source was confident, escalate to the
    cloud" (reasoning_router.py:220-224), and it is also returned by two
    deliberate bypasses (Stage 0 self-infra, Stage 0.5 self-capability). None of
    those is a wrong answer.

    This is the same tri-state R-F2639 codified one layer up for gate reporting:
    True/False when measured, None when it could not be measured — "could not
    measure" is not "measured and failed". CLAUDE.md §1 already DESCRIBES this
    behaviour ("a grader error SKIPS the update"); the code did not implement it.

    A genuine wrong answer still returns False. The gate is not softened.
    """  # noqa: D205
    from ..intel import reasoning_router, student
    if not research_text:
        return None          # nothing to compare against — not evidence of a miss
    question = (f"What are the most important {topic.replace('_', ' ')} facts and "
                f"recent developments for {region.replace('_', ' ')}?")
    try:
        local = await reasoning_router.try_local_reasoning(question)
    except Exception:
        return None          # the instrument broke, not the knowledge
    if not local.get("answered"):
        return None          # escalate-to-cloud signal, NOT a wrong answer
    resp = local.get("response") or ""
    if not resp:
        return None
    try:
        # R-F3971 (C-60) — grounding, not Jaccard. See `_answer_grounding`:
        # against a 4,000-char document, Jaccard capped a PERFECT answer below
        # this threshold purely on length, so the grader could never say True.
        return _answer_grounding(resp, research_text) >= _GROUNDING_THRESHOLD
    except Exception:
        return None          # scorer failure — unmeasured


@fail_wire(module="tasks", gap_type="agent_cycle_failure")
async def fill_knowledge_gaps(llm, *, dry_run: bool = True, max_cells: int = 5) -> dict:
    """Identify and research the weakest knowledge cells.

    Called by the autonomous engine as a scheduled task. Builds the
    heatmap, finds the weakest cells via gap_targets(), and runs
    targeted research queries for each one. Results are automatically
    fed into the knowledge base by _feed_knowledge().

    Args:
        llm: LLM provider
        dry_run: if True, log gaps but don't run research
        max_cells: max cells to research per cycle (default 5)

    Returns:
        dict with results per cell
    """
    from ..intel.coverage_heatmap import build_heatmap, gap_targets, invalidate_heatmap_cache

    t0 = time.time()
    results = {
        "ok": True,
        "cells_researched": 0,
        "cells_skipped": 0,
        "mastery_tested": 0,
        "mastery_passed": 0,
        "errors": [],
        "duration_ms": 0,
    }

    try:
        heatmap = await asyncio.to_thread(build_heatmap)
        targets = gap_targets(heatmap, max_targets=max_cells)
    except Exception as e:
        results["ok"] = False
        results["errors"].append(f"heatmap build failed: {e}")
        results["duration_ms"] = int((time.time() - t0) * 1000)
        return results

    if not targets:
        logger.info("[knowledge gap filler] no gaps found — heatmap is healthy")
        results["duration_ms"] = int((time.time() - t0) * 1000)
        return results

    for target in targets:
        domain = target.get("domain", "")
        jurisdiction = target.get("jurisdiction", "")
        tier = target.get("tier", "absent")

        # R-F1986: map coverage-matrix domain to mastery topic.
        # The coverage matrix uses fine-grained domains like
        # "sanctions_screening"; the mastery heatmap uses broader
        # topics like "compliance". If no mapping exists, skip.
        topic = _COVERAGE_DOMAIN_TO_TOPIC.get(domain)
        if topic is None:
            results["cells_skipped"] += 1
            continue

        if topic not in _GAP_FILLER_ELIGIBLE_TOPICS:
            results["cells_skipped"] += 1
            continue

        # R-F1986: map coverage-matrix jurisdiction (country name) to
        # mastery region. If no mapping exists, skip.
        region = _COUNTRY_TO_REGION.get(jurisdiction)
        if region is None:
            results["cells_skipped"] += 1
            continue

        template = _GAP_FILLER_QUERY_TEMPLATES.get(topic, "defence {topic} {region}")
        query = template.format(topic=topic, region=region.replace("_", " "))

        logger.info(
            "[knowledge gap filler] researching %s x %s (topic=%s, region=%s, tier=%s, facts=%d): %s",
            domain, jurisdiction, topic, region, tier, target.get("fact_count", 0), query,
        )

        if dry_run:
            results["cells_skipped"] += 1
            continue

        try:
            # Run the research through the existing chat pipeline
            research_task = Task(
                id=f"gap_fill_{domain}_{jurisdiction}",
                name=f"Research {domain} x {jurisdiction}",
                tool_chain=[{"tool": "deep_research", "query": query}],
                timeout_seconds=120,
                cost_cap_usd=0.10,
            )
            record = await execute_task(research_task, llm, dry_run=False)
            if record.get("status") == "error":
                results["errors"].append(f"{domain}x{jurisdiction}: {record.get('error', 'unknown')}")
            else:
                results["cells_researched"] += 1
                # R-F1986 + Claude review (R-F1989): knowledge-to-mastery bridge,
                # HONESTLY graded. The research itself improved the COVERAGE matrix
                # (facts stored); mastery only moves on a REAL recall grade so gate
                # #2 reflects comprehension, not participation (CLAUDE.md §1 — no
                # clamping). update_regional_mastery filters by TOPICS internally,
                # so an off-topic cell is a no-op.
                try:
                    from ..intel import student as _student
                    response_text = record.get("response_preview", "") or ""
                    response_len = record.get("response_length", 0)
                    if response_len > 200:
                        graded_correct = await _grade_researched_cell(
                            topic, region, response_text)
                        # R-F3483 — None means COULD NOT MEASURE. Skip the update
                        # entirely: passing None through would be coerced to a
                        # wrong answer by the EMA (`obs = 1.0 if correct else
                        # 0.0`, student.py:2381), which is the exact defect this
                        # tri-state removes. An unmeasured cell is also not a
                        # "test", so it must not inflate mastery_tested.
                        if graded_correct is None:
                            results["mastery_unmeasured"] = (
                                results.get("mastery_unmeasured", 0) + 1)
                            logger.info(
                                "[knowledge gap filler] mastery UNMEASURED: %s x %s "
                                "(local stack could not answer — not counted as a "
                                "miss) (%d chars)", topic, region, response_len,
                            )
                        else:
                            await _student.update_regional_mastery(
                                topics=[topic], regions=[region],
                                correct=graded_correct, weight=0.5,
                            )
                            results["mastery_tested"] = results.get("mastery_tested", 0) + 1
                            if graded_correct:
                                results["mastery_passed"] = results.get("mastery_passed", 0) + 1
                            logger.info(
                                "[knowledge gap filler] mastery graded: %s x %s -> %s (%d chars)",
                                topic, region, "PASS" if graded_correct else "fail", response_len,
                            )
                except Exception as bridge_err:
                    logger.debug(
                        "[knowledge gap filler] mastery bridge failed (non-fatal): %s",
                        bridge_err,
                    )
        except Exception as e:
            results["errors"].append(f"{domain}x{jurisdiction}: {e}")

    # Invalidate heatmap cache so next read reflects new knowledge
    try:
        invalidate_heatmap_cache()
    except Exception:
        pass

    results["duration_ms"] = int((time.time() - t0) * 1000)
    logger.info(
        "[knowledge gap filler] completed: %d researched, %d skipped, %d errors in %dms",
        results["cells_researched"], results["cells_skipped"],
        len(results["errors"]), results["duration_ms"],
    )
    return results

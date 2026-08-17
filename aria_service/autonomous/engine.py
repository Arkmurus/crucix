"""ARIA Layer 3 — autonomous engine bootstrap, polling loop, lifecycle.

R-F1059 — wired to brain: task fire/skip/error events reach brain via
wire_success/wire_failure so the coder can see engine behaviour.

This is the heartbeat of the autonomous research engine. The whole
module is ~150 lines because it leans entirely on the existing
aria_engine.aria_chat() pipeline for the heavy lifting:

  1. On startup (lifespan hook), schedule a single asyncio task that
     polls every 60 seconds.
  2. Each tick: load tasks.yaml (cached), iterate over enabled tasks,
     check whether each task's cron expression matches the current
     UTC minute, and fire the matched ones serially.
  3. Each fire goes through safety.can_task_run() first — if any
     guardrail blocks it, the fire is skipped silently.
  4. The actual task execution happens in tasks.execute_task() which
     routes through aria_engine.aria_chat() so the constitutional
     pipeline applies. The result goes through delivery.deliver()
     unless DRY_RUN is set.

Why a 60-second polling loop instead of APScheduler:

  - Consistent with the existing autonomous_research and self_improve
    loops in main.py — same code style, same restart semantics, same
    cost meter wiring
  - Zero new dependencies (no apscheduler / pytz / tzlocal)
  - Survives restart cleanly: tasks resume their schedule on the next
    minute boundary after the new container is up. The cron expression
    is the source of truth, not in-process state.
  - 60-second precision is fine for tasks that run daily / weekly. We
    do not need second-level scheduling.
  - Pause / resume is implemented via Redis flags (see safety.py) so
    the engine can be stopped from the admin endpoint without restart

The engine is gated behind TWO independent enable flags so a deploy
cannot accidentally turn it on:

  - ARIA_AUTONOMOUS_ENABLED env var (default OFF)
  - per-task `enabled` flag in tasks.yaml (default false on every task)

Even with both flags on, the engine runs in DRY_RUN mode by default
(no actual delivery to WhatsApp / intel ledger). Set
ARIA_AUTONOMOUS_DRY_RUN=0 to enable real delivery.
"""
from __future__ import annotations

import asyncio
import calendar
import logging
import os
import time
from typing import Any

from . import safety, tasks as tasks_mod
from ..intel.wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.autonomous.engine")


# ── Configuration ──────────────────────────────────────────────────────────

POLL_INTERVAL_SECONDS = 60  # one tick per minute
# R-F845 (2026-05-23): raised from 90s → 180s. The 90s default still
# triggered a cold-start wedge after every deploy — the L3 autonomy
# absorb storm hit the event loop before lifespan (RAG warm-up +
# sentence-transformers load + ARIA-Coder boot + knowledge_seed_task)
# had settled. Health flapped critical for 2-5 min. R-F841 made this
# more frequent because CI now redeploys aria-intel on every push.
# 180s gives the slow boot path room without delaying the eventual
# steady state (POLL_INTERVAL=60s, so we miss at most 1-2 ticks vs the
# old default).
STARTUP_DELAY_SECONDS = 180  # don't poll until the server is fully warm

_ENABLED_VAR = "ARIA_AUTONOMOUS_ENABLED"
_DRY_RUN_VAR = "ARIA_AUTONOMOUS_DRY_RUN"
_AUTONOMY_LEVEL_VAR = "ARIA_AUTONOMY_LEVEL"

# Redis override — lets /autonomous/enable flip the master switch without
# a redeploy. Past incident 2026-04-18: Antonio added ARIA_AUTONOMOUS_ENABLED
# on seenode (the Node WA listener), but the autonomous engine runs on
# fly.io (the Python backend) — wrong environment. A redis-backed runtime
# override closes that footgun: the admin endpoint flips a flag we check
# in-process, no redeploy required.
_REDIS_ENABLE_KEY = "crucix:autonomous:enabled_override"
# Sentinel values:
#   "1"      → force-enable (env var can be 0)
#   "0"      → force-disable (env var can be 1)
#   missing  → defer to env var (default path)
_RUNTIME_ENABLE_CACHE: dict[str, Any] = {"val": None, "ts": 0.0}
_RUNTIME_ENABLE_TTL_S = 5.0  # cache Redis read so is_enabled() stays cheap

# Autonomy ladder:
#   L0 = off (engine does not run)
#   L1 = research-only (tasks run but no delivery at all)
#   L2 = internal delivery (mem0 + intel_ledger, no WhatsApp)
#   L3 = full delivery (all channels including WhatsApp)
AUTONOMY_LEVELS = {0: "OFF", 1: "RESEARCH", 2: "INTERNAL", 3: "FULL"}


@fail_wire(module="engine", gap_type="agent_cycle_failure")
def get_autonomy_level() -> int:
    """Current autonomy level. Default: inferred from ENABLED + DRY_RUN."""
    explicit = os.getenv(_AUTONOMY_LEVEL_VAR, "").strip()
    if explicit.isdigit() and int(explicit) in AUTONOMY_LEVELS:
        return int(explicit)
    # Backward compat: infer from old flags
    if not is_enabled():
        return 0
    if is_dry_run():
        return 1
    return 3  # enabled + not dry_run = full


@fail_wire(module="engine", gap_type="agent_cycle_failure")
def is_enabled() -> bool:
    """Master engine kill switch. Env var default OFF, with a runtime
    override that lets /autonomous/enable flip the switch without a
    redeploy. Even when True, individual tasks must ALSO be enabled in
    tasks.yaml.

    Override precedence (purely in-process — read from the cache that
    `refresh_runtime_override()` keeps fresh):
      cache "1" → enabled (regardless of env var)
      cache "0" → disabled (regardless of env var)
      cache None → fall through to env var
    """
    override = _RUNTIME_ENABLE_CACHE.get("val")
    if override == "1":
        return True
    if override == "0":
        return False
    val = (os.getenv(_ENABLED_VAR, "0") or "0").strip().lower()
    return val in ("1", "true", "yes", "on")


@fail_wire(module="engine", gap_type="agent_cycle_failure")
async def check_engine_liveness(now: float | None = None) -> dict:
    """R-F2006 — assess whether the autonomous engine is actually live AND firing.

    The engine writes two heartbeats: `crucix:autonomous:last_tick_ts` (every
    loop iteration, before the enable/pause gates) and `crucix:autonomous:
    last_fire_ts` (when a task fires). This reads them and classifies:

      - loop not ticking          -> the loop crashed / never started (ALERT)
      - master-disabled           -> autonomy is dark (ALERT)
      - paused                    -> controlled + auto-expires (R-F2004); NOT an alert
      - alive but not firing >3h   -> tasks all blocked / cron bug (ALERT)

    Returns {healthy, problem, tick_age_s, fire_age_s, paused, enabled}. Never
    raises — the watchdog must keep running. This is the missing signal that let
    the R-F2004 187h fire=0 outage go unnoticed.
    """
    import time as _t
    n = float(now if now is not None else _t.time())
    tick_stale_s = float(os.getenv("ARIA_ENGINE_TICK_STALE_S", "600"))          # 10 min
    fire_stale_s = float(os.getenv("ARIA_ENGINE_FIRE_STALE_S", str(3 * 3600)))  # 3 h

    async def _get(key: str):
        try:
            from ..intel import redis_store as rs
            return await rs.get(key)
        except Exception:
            return None

    def _age(raw):
        try:
            return n - float(raw) if raw else None
        except (TypeError, ValueError):
            return None

    tick_age = _age(await _get("crucix:autonomous:last_tick_ts"))
    fire_age = _age(await _get("crucix:autonomous:last_fire_ts"))

    try:
        from . import safety as _safety
        paused = await _safety.is_engine_paused()
    except Exception:
        paused = False
    enabled = is_enabled()

    problem = None
    if tick_age is not None and tick_age > tick_stale_s:
        problem = (f"autonomous engine loop NOT TICKING — {int(tick_age)}s since "
                   f"last tick (loop crashed or never started)")
    elif not enabled:
        problem = ("autonomous engine is MASTER-DISABLED (is_enabled=False) — "
                   "autonomy is dark; re-enable via POST /autonomous/enable")
    elif paused:
        problem = None   # R-F2004 bounds + auto-expires pauses; not an alert
    elif fire_age is not None and fire_age > fire_stale_s:
        problem = (f"autonomous engine is alive but FIRING NOTHING — "
                   f"{int(fire_age // 3600)}h since last task fire (tasks blocked?)")

    return {
        "healthy": problem is None,
        "problem": problem,
        "tick_age_s": tick_age,
        "fire_age_s": fire_age,
        "paused": paused,
        "enabled": enabled,
    }


@fail_wire(module="engine", gap_type="agent_cycle_failure")
async def check_feed_liveness() -> list[str]:
    """R-F2959 (B1) — SYMMETRIC feed-liveness for the LEARNING feeds.

    check_engine_liveness() above watches the autonomous ENGINE. But the two
    gate-#2 mastery WRITERS — the RSS→facts research feed and the student
    reading loop — are on independent switches with NO watchdog: disabling
    ARIA_AUTONOMOUS_RESEARCH_ENABLED, or a reading loop that quietly died, threw
    only an INFO log and silently throttled regional-mastery compounding (the
    2026-07 incident). This returns a list of problem strings (empty = healthy)
    so the R-F2006 watchdog can alarm on the feeds too. Never raises.

    Fresh-not-alarm rules: a PAUSED engine (R-F2004 bounds pauses) and a
    load-SHED cycle both legitimately explain an idle feed — neither is a fault.
    An agent not yet registered (early boot) is skipped, not flagged.
    """
    problems: list[str] = []
    # (1) research feed disabled at boot — pure boot flag, no override path.
    research_on = (os.getenv("ARIA_AUTONOMOUS_RESEARCH_ENABLED", "1") or "1").strip().lower() not in ("0", "false", "no")
    if not research_on:
        problems.append(
            "research feed DISABLED (ARIA_AUTONOMOUS_RESEARCH_ENABLED=0) — the RSS→facts "
            "learning feed is dark; set the secret and restart to resume gate-#2 content flow")
    # Paused → feeds legitimately idle; don't alarm.
    try:
        from . import safety as _safety
        if await _safety.is_engine_paused():
            return problems
    except Exception:
        pass
    # Load-shed explains a skipped cycle → treat as fresh. R-F2980 (review F2): use
    # the PURE pressure() read, NOT should_shed() — should_shed() calls _note_shed()
    # which wires a fabricated "I throttled myself to protect serving" SUCCESS signal
    # + WARNING to the brain. This is a read-only liveness probe that sheds nothing,
    # so it must not pollute the self-record with a false shed-success.
    try:
        from ..intel import load_governor as _lg
        if _lg.pressure().get("shedding"):
            return problems
    except Exception:
        pass
    try:
        from ..intel.agent_registry import AgentRegistry
        reg = AgentRegistry()
        read_int = float(os.getenv("ARIA_READING_INTERVAL_S", "9000") or "9000")
        feeds = [("student_reading", 2.0 * read_int), ("regional_snapshot", 2.0 * 6 * 3600)]
        if research_on:
            # R-F2980 (review F1): 4x (not 2x) the 30-min interval. The research cycle
            # is heavy (research_and_learn + up to 8 sequential BACKGROUND-priority LLM
            # validations) BEFORE the 30-min sleep, so inter-beat gap = cycle-time + 30m.
            # A 2x (1h) bound left only 30m headroom → a slow/rate-limited provider cycle
            # (~40m) false-RED'd. Provider latency is NOT covered by the pressure-shed
            # suppression above, so widen the bound to ~2h of headroom.
            feeds.append(("research_engine", 4.0 * 1800))
        for aid, bound in feeds:
            try:
                st = await reg.get_agent_status(aid)
            except Exception:
                st = None
            if not st:
                continue  # not yet registered (early boot) — don't false-alarm
            age = st.get("heartbeat_age_s")
            if age is not None and age > bound:
                problems.append(
                    f"learning loop '{aid}' STALE: last beat {int(age)}s ago "
                    f"(bound {int(bound)}s) — it may have crashed or been disabled")
    except Exception:
        pass
    return problems


@fail_wire(module="engine", gap_type="agent_cycle_failure")
async def refresh_runtime_override() -> str | None:
    """Read the Redis override into the in-process cache. Called at
    lifespan startup (before start_engine) and once per engine tick so
    flips made via /autonomous/enable are seen within one poll cycle.

    Returns the cached value ("1", "0", or None) for logging.
    """
    # ── R-F3722 — an UNREADABLE store is not "no override" ───────────────────
    #
    # THE DEFECT (the R-F2664/R-F3716/R-F3717 class, in the master switch):
    # this read `rs.get`, which returns None on a store FAILURE as well as on a
    # genuinely absent key, and then wrote that None into the cache. Both the
    # success path and the `except` path erased a good "1".
    #
    # `is_enabled()` falls through to ARIA_AUTONOMOUS_ENABLED when the cache is
    # None, and that env var is "0" in production (§18/§1 gate #5: the durable
    # override is the ONLY thing keeping autonomy on). So one unreadable read
    # turned the whole metabolism OFF — and at BOOT that is not a one-tick
    # flicker: `main.py:3966` calls `start_engine()` exactly once, it hard-
    # refuses when `is_enabled()` is False, and nothing retries. That is the
    # R-F2004 outage class (187h dark) reachable from a slow-booting store,
    # which §11c says is the NORMAL cold-boot condition.
    #
    # Fixed by reading strictly and treating a read failure as NO NEWS: the
    # previous cached value stands. Only a SUCCESSFUL read may change the
    # switch. Absent-and-readable still clears it, so /autonomous/enable's
    # "clear the override" path is unaffected.
    # R-F3732 — the import stays INSIDE the try. R-F3722 lifted it out, which
    # quietly broke this function's never-raises contract: the original wrapped
    # EVERYTHING, so callers could rely on it not throwing. `_engine_loop`
    # (≈:849) awaits it BARE — unlike the catch-up caller at ≈:609, which
    # guards it — so a raise there propagates into the tick loop. An
    # unimportable store module is also just "no news", and belongs on the same
    # path as an unreadable one.
    try:
        from ..intel import redis_store as rs
        v = await rs.get_strict(_REDIS_ENABLE_KEY)
    except Exception as e:
        prev = _RUNTIME_ENABLE_CACHE.get("val")
        logger.warning(
            "[R-F3722] autonomy override UNREADABLE (%s) — KEEPING the previous "
            "value %r rather than falling back to %s=%s. An unreadable store is "
            "not a decision to disable autonomy.",
            e, prev, _ENABLED_VAR, os.getenv(_ENABLED_VAR, "0"),
        )
        try:  # §21a — the master switch going blind must reach the brain
            from ..intel.engine_wiring import wire_failure as _wf
            _wf(module="autonomous_engine",
                detail=(f"runtime override unreadable ({str(e)[:120]}) — retained "
                        f"cached value {prev!r}; autonomy NOT silently disabled"),
                gap_type="data_integrity",
                source="autonomous_engine:R-F3722")
        except Exception:
            pass
        return prev
    cleaned = (v or "").strip()
    _RUNTIME_ENABLE_CACHE["val"] = cleaned if cleaned in ("0", "1") else None
    _RUNTIME_ENABLE_CACHE["ts"] = time.time()
    return _RUNTIME_ENABLE_CACHE.get("val")


@fail_wire(module="engine", gap_type="agent_cycle_failure")
async def set_runtime_override(enabled: bool | None) -> dict[str, Any]:
    """Flip the master switch at runtime via Redis. Also updates the
    in-process cache immediately so the next is_enabled() call (including
    the one inside start_engine() that the admin endpoint will make)
    reflects the change without waiting for the tick refresh.

    Args:
        enabled: True to force-enable, False to force-disable, None to
            clear the override (env var regains control).
    """
    from ..intel import redis_store as rs
    if enabled is True:
        await rs.set(_REDIS_ENABLE_KEY, "1")
        _RUNTIME_ENABLE_CACHE["val"] = "1"
    elif enabled is False:
        await rs.set(_REDIS_ENABLE_KEY, "0")
        _RUNTIME_ENABLE_CACHE["val"] = "0"
    else:
        try:
            await rs.delete(_REDIS_ENABLE_KEY)
        except Exception:
            await rs.set(_REDIS_ENABLE_KEY, "")
        _RUNTIME_ENABLE_CACHE["val"] = None
    _RUNTIME_ENABLE_CACHE["ts"] = time.time()
    return {
        "override": _RUNTIME_ENABLE_CACHE["val"],
        "is_enabled_now": is_enabled(),
        "env_var_value": os.getenv(_ENABLED_VAR, ""),
    }


# R-F2184 — master-switch auto-recovery (heal a LOST flag; respect a deliberate
# disable). The R-F2004 outage class: env ARIA_AUTONOMOUS_ENABLED is lost (machine
# recreate / secret unset) and there is no Redis override, so is_enabled() defaults
# False and the whole real-time metabolism goes dark — previously only ALERTED.
_DESIRED_KEY = "crucix:autonomous:desired_enabled"
_AUTORECOVER_VAR = "ARIA_AUTONOMOUS_AUTORECOVER"   # default ON
_desired_marked = False


async def _mark_desired_enabled() -> None:
    """Durably record (once per process) that the operator WANTS autonomy enabled,
    the moment the engine is observed running enabled. Survives env-secret loss so
    the watchdog can heal a lost flag WITHOUT re-enabling a deliberate disable."""
    global _desired_marked
    if _desired_marked:
        return
    try:
        from ..intel import redis_store as rs
        await rs.set(_DESIRED_KEY, "1")
        _desired_marked = True
    except Exception:  # noqa: BLE001
        pass


@fail_wire(module="engine", gap_type="agent_cycle_failure")
async def maybe_autorecover_master_switch() -> dict:
    """Re-enable autonomy IFF it is dark because of a LOST flag, not a deliberate
    disable. Conditions (all required):
      - is_enabled() is currently False (master-disabled), AND
      - the Redis override is None — a deliberate disable sets it to "0" via
        /autonomous/disable, which is RESPECTED (never auto-overridden), AND
      - the durable desired-enabled marker is set (operator ran it enabled before).
    Returns {recovered, reason}. Never raises. Gate off with
    ARIA_AUTONOMOUS_AUTORECOVER=0."""
    try:
        if (os.getenv(_AUTORECOVER_VAR, "1") or "1").strip().lower() in ("0", "false", "no"):
            return {"recovered": False, "reason": "autorecover disabled by env"}
        if is_enabled():
            return {"recovered": False, "reason": "already enabled"}
        override = _RUNTIME_ENABLE_CACHE.get("val")
        if override == "0":
            return {"recovered": False, "reason": "deliberately disabled (override=0) — respected"}
        from ..intel import redis_store as rs
        # R-F3722 — the SAFETY NET had the same blindness as the switch it
        # guards. `rs.get` returns None on a store failure, so an unreadable
        # store produced desired="" and this returned "no desired-enabled
        # marker" — a FALSE CAUSE. Both this and refresh_runtime_override()
        # run at boot, against the same store, in the same reconnect window
        # (§11c), so the net could never catch the fall it exists for: the
        # switch read disabled and the recovery agreed, for the same reason,
        # and reported a different one.
        try:
            desired = (await rs.get_strict(_DESIRED_KEY) or "").strip()
        except Exception as e:
            logger.warning(
                "[R-F3722] desired-enabled marker UNREADABLE (%s) — cannot tell "
                "'operator never enabled it' from 'the store is down', so NOT "
                "claiming the former.", e,
            )
            try:  # §21a — a blind safety net must not be silent
                from ..intel.engine_wiring import wire_failure as _wf
                _wf(module="autonomous_engine",
                    detail=(f"autorecover could not read the desired-enabled marker "
                            f"({str(e)[:120]}) — recovery deferred, not refused"),
                    gap_type="data_integrity",
                    source="autonomous_engine:R-F3722")
            except Exception:
                pass
            return {"recovered": False, "reason": "desired marker UNREADABLE — store down, not a decision"}
        if desired != "1":
            return {"recovered": False, "reason": "no desired-enabled marker — not auto-restoring"}
        # Lost-flag recovery: override is None + desired=1 → restore to enabled.
        await set_runtime_override(True)
        logger.warning("[R-F2184] autonomous master switch was LOST (env default off, "
                       "no override) but operator intent = enabled — AUTO-RECOVERED "
                       "via Redis override. The real-time metabolism is back online.")
        try:
            from ..intel.engine_wiring import wire_failure as _wf
            _wf(module="autonomous_engine",
                detail=("master switch lost (env off, no override) — auto-recovered "
                        "to enabled (R-F2184). Investigate why the env flag dropped."),
                gap_type="agent_cycle_failure",
                source="autonomous_engine:autorecover_rf2184")
        except Exception:  # noqa: BLE001
            pass
        return {"recovered": True, "reason": "lost flag restored to enabled"}
    except Exception as e:  # noqa: BLE001
        logger.debug("[R-F2184] autorecover failed: %s", e)
        return {"recovered": False, "reason": f"error: {e}"}


@fail_wire(module="engine", gap_type="agent_cycle_failure")
def is_dry_run() -> bool:
    """Default ON. Set ARIA_AUTONOMOUS_DRY_RUN=0 to enable real delivery."""
    val = (os.getenv(_DRY_RUN_VAR, "1") or "1").strip().lower()
    return val not in ("0", "false", "no", "off")


# ── In-process state (Redis is the source of truth for everything else) ───

_engine_task: asyncio.Task | None = None
_started_at: float | None = None
_last_tick_at: float | None = None
_tick_count: int = 0
_fire_count: int = 0


@fail_wire(module="engine", gap_type="agent_cycle_failure")
def get_engine_status() -> dict[str, Any]:
    """One-shot snapshot of the engine's in-process state. Used by the
    /api/aria/autonomous/status admin endpoint together with the
    safety state and the recent run history."""
    level = get_autonomy_level()
    return {
        "enabled": is_enabled(),
        "dry_run": is_dry_run(),
        "autonomy_level": level,
        "autonomy_label": AUTONOMY_LEVELS.get(level, "UNKNOWN"),
        "running": _engine_task is not None and not _engine_task.done(),
        "started_at": _started_at,
        "last_tick_at": _last_tick_at,
        "tick_count": _tick_count,
        "fire_count": _fire_count,
        "poll_interval_seconds": POLL_INTERVAL_SECONDS,
        "startup_delay_seconds": STARTUP_DELAY_SECONDS,
    }


# ── R-F2013: startup catch-up for missed cron slots ─────────────────────────
# A once-an-hour task (cron "0 * * * *", e.g. news_monitor) only fires at :00.
# If the engine is restarting (deploy / crash) when its slot lands, that slot is
# MISSED and the task can stay stale for hours/days if restarts keep coinciding
# with it — this is exactly why news_monitor sat 189h stale (R-F2004). On startup
# we fire any task whose most-recent scheduled slot is recent AND hasn't run since.
_CATCH_UP_MAX_AGE_S = float(os.getenv("ARIA_ENGINE_CATCHUP_MAX_AGE_S", "7200"))   # 2h
_CATCH_UP_MAX_FIRES = int(os.getenv("ARIA_ENGINE_CATCHUP_MAX_FIRES", "15"))       # burst cap

# R-F2631 — strong ref to the background startup-maintenance task. asyncio only
# holds a WEAK reference to bare create_task() results, so without this the task
# can be garbage-collected mid-flight and the repair/catch-up would silently
# vanish. Module-level so it outlives _engine_loop's frame.
_startup_maintenance_task: Any = None
_TASK_LAST_FIRE_KEY = "crucix:autonomous:task_last_fire:{tid}"
_TASK_LAST_FIRE_TTL = 30 * 86400


async def _set_task_last_fire(task_id: str, epoch: float) -> None:
    """Persist a per-task last-fire timestamp so catch-up knows what already ran.
    (The shared run-history list is a rolling 50 across ALL tasks — not reliable
    per-task.) Fire-and-forget."""
    try:
        from ..intel import redis_store as rs
        key = _TASK_LAST_FIRE_KEY.format(tid=task_id)
        await rs.set(key, str(int(epoch)))
        if hasattr(rs, "expire"):
            await rs.expire(key, _TASK_LAST_FIRE_TTL)
    except Exception:
        pass


async def _get_task_last_fire(task_id: str) -> float | None:
    try:
        from ..intel import redis_store as rs
        v = await rs.get(_TASK_LAST_FIRE_KEY.format(tid=task_id))
        return float(v) if v else None
    except Exception:
        return None


def _resolve_task_entity(task) -> str:
    """R-F2635 — the dedupe entity for a task, resolved IDENTICALLY everywhere.

    This exists because the tick and catch_up disagreed. The tick passed
    `entity or task_id` (entity from the tool_chain) while catch_up passed
    `task_id` — so for the 38 of 97 entity-bearing tasks the two produced
    DIFFERENT entity_hashes and therefore different dedupe keys FOR THE SAME
    SLOT. Dedupe could not bind them, and R-F2631's justification for running
    catch_up concurrently with the tick ("both paths go through can_task_run,
    whose dedupe marker is exactly the guard against a catch-up and a tick
    firing the same task twice") was false for exactly those tasks.

    It was latent while catch_up ran BEFORE the loop; R-F2631 made it
    reachable by running them concurrently — and catch_up only writes
    _set_task_last_fire AFTER `await execute_task` (which has been observed
    running >10 min), so the `last_fire >= match_epoch` guard is wide open
    for that whole window. One resolver, one key, guarantee restored.
    """
    if task is not None and getattr(task, "tool_chain", None):
        first = task.tool_chain[0]
        if isinstance(first, dict):
            return (
                first.get("entity")
                or first.get("topic")
                or first.get("query")
                or ""
            )
    return ""


def _most_recent_cron_match_epoch(cron: str, now_epoch: float, lookback_s: float) -> float | None:
    """Epoch of the latest whole-minute <= now where `cron` matches, within
    lookback_s; None if it didn't match in the window. Cheap: at most
    lookback_s/60 cron_matches() calls (~120 for the 2h default)."""
    base = int(now_epoch // 60) * 60
    steps = int(lookback_s // 60) + 1
    for i in range(steps):
        t = base - i * 60
        try:
            if tasks_mod.cron_matches(cron, time.gmtime(t)):
                return float(t)
        except Exception:
            return None
    return None


def _wire_catchup(fired: int, skipped: dict, deferred: int) -> None:
    """R-F2020 — wire the startup catch-up OUTCOME to the brain (§21a) so a silent
    'caught up nothing' is observable from the brain, not invisible. Sync + best-
    effort (engine_wiring.wire_success is sync; never let wiring break catch-up)."""
    try:
        from ..intel.engine_wiring import wire_success
        wire_success(
            module="autonomous_engine",
            summary=f"startup catch-up: fired={fired} skipped={dict(skipped)} deferred={deferred}",
            source_id="autonomous_engine:catchup_rf2017",
        )
    except Exception:
        pass


@fail_wire(module="engine", gap_type="agent_cycle_failure")
async def catch_up_overdue_tasks(llm) -> int:
    """R-F2013 — on engine startup, fire tasks that MISSED their scheduled cron
    slot while the engine was down/restarting. Bounded + safe:
      - only tasks whose most-recent cron slot is within CATCH_UP_MAX_AGE (2h),
      - that have NOT run since that slot (per-task last-fire),
      - each fired AT MOST ONCE, capped at CATCH_UP_MAX_FIRES per startup,
      - through the SAME enabled / pause / operating-mode / safety gates as a
        normal tick (so a paused or disabled engine catches up nothing).
    Returns the number of catch-up fires."""
    # R-F2020 — refresh the runtime override FIRST. The polling loop refreshes it
    # at the top of every tick (≈line 505), but catch-up runs BEFORE the loop, so
    # without this it reads a STALE is_enabled() cache: a freshly re-enabled engine
    # could skip all catch-up (or a freshly disabled one could fire). Root-kill of
    # the stale-gate window that helped hide the R-F2004 187h fire=0 outage.
    try:
        await refresh_runtime_override()
    except Exception as e:
        logger.warning("[R-F2020 catch-up] runtime-override refresh failed (continuing): %s", e)

    if not is_enabled():
        logger.warning("[R-F2020 catch-up] SKIPPED ALL: engine master-disabled (is_enabled=False)")
        _wire_catchup(0, {"master_disabled": 1}, 0)
        return 0
    try:
        if await safety.is_engine_paused():
            logger.warning("[R-F2020 catch-up] SKIPPED ALL: engine paused")
            _wire_catchup(0, {"paused": 1}, 0)
            return 0
    except Exception as e:
        logger.warning("[R-F2020 catch-up] SKIPPED ALL: pause-check failed: %s", e)
        _wire_catchup(0, {"pause_check_error": 1}, 0)
        return 0

    loaded = tasks_mod.get_loaded_tasks() or {}
    now_epoch = time.time()
    fired = 0
    skipped: dict[str, int] = {}
    deferred: list[str] = []
    try:
        from ..intel import operating_modes as _om
        mode = await _om.get_mode()
    except Exception:
        _om, mode = None, None

    def _skip(reason: str, task_id: str) -> None:
        # R-F2020 — every GENUINELY-OVERDUE task that gets dropped is logged at
        # WARNING with its reason (was silent → a missed fire vanished without
        # trace). This is the observability that lets us pin the exact gate.
        skipped[reason] = skipped.get(reason, 0) + 1
        logger.warning("[R-F2020 catch-up] OVERDUE %s NOT caught up: %s", task_id, reason)

    for task_id, task in loaded.items():
        try:
            if not getattr(task, "enabled", False):
                continue  # disabled task — not a missed slot (not logged)
            match_epoch = _most_recent_cron_match_epoch(task.cron, now_epoch, _CATCH_UP_MAX_AGE_S)
            if match_epoch is None or (now_epoch - match_epoch) > _CATCH_UP_MAX_AGE_S:
                continue  # no recent slot — not overdue (not logged)
            last_fire = await _get_task_last_fire(task_id)
            if last_fire is not None and last_fire >= match_epoch:
                continue  # already ran since its scheduled slot — not missed (not logged)
            # ── From here the task is GENUINELY OVERDUE: every drop below is a real
            #    missed fire being skipped, so it is logged with its reason. ──
            if fired >= _CATCH_UP_MAX_FIRES:
                deferred.append(task_id)
                continue
            if _om is not None and mode is not None:
                try:
                    if not _om.should_task_run(task_id, mode):
                        _skip(f"operating_mode={mode}", task_id)
                        continue
                except Exception:
                    pass
            try:
                # R-F2635 — dedupe on the SCHEDULED SLOT this catch-up is
                # firing (match_epoch), not on a flat 23h window. Without the
                # slot, catching up one missed fire burned the task's ONLY
                # permitted fire for the next 23h regardless of its cron.
                allowed, why = await safety.can_task_run(
                    task_id, _resolve_task_entity(task) or task_id,
                    slot=int(match_epoch // 60),
                )
            except Exception as e:
                _skip(f"safety_error:{type(e).__name__}", task_id)
                continue
            if not allowed:
                _skip(f"safety_gate:{why}", task_id)
                continue
            logger.warning("[R-F2020 catch-up] firing MISSED task %s (slot %ds ago, cron=%r)",
                           task_id, int(now_epoch - match_epoch), task.cron)
            await tasks_mod.execute_task(task=task, llm=llm, dry_run=is_dry_run())
            await _set_task_last_fire(task_id, now_epoch)
            fired += 1
        except Exception as e:
            _skip(f"exec_error:{type(e).__name__}", task_id)

    if deferred:
        logger.warning("[R-F2020 catch-up] burst cap %d reached — DEFERRED %d overdue task(s) "
                       "to their next normal cron: %s", _CATCH_UP_MAX_FIRES, len(deferred), deferred[:10])
    # R-F2020 — ALWAYS emit a summary (the original logged nothing when fired==0,
    # the exact blind spot that hid the stale-engine outage). WARNING level so it
    # survives the INFO-suppressed flyctl log filter.
    logger.warning("[R-F2020 catch-up] done: fired=%d skipped=%s deferred=%d",
                   fired, skipped or {}, len(deferred))
    _wire_catchup(fired, skipped, len(deferred))
    return fired


# ── The polling loop ───────────────────────────────────────────────────────

#: R-F3824 — how often to tick while a task runs, and for how long. The interval
#: must stay well under `self_restart._BLACKOUT_THRESHOLD_S` (300s) or the tick is
#: too late to prevent a false blackout; the window must be a MULTIPLE of it so a
#: genuinely hung task still trips the detector.
_TASK_HEARTBEAT_INTERVAL_S = 60.0
_TASK_HEARTBEAT_MAX_BUSY_S = 900.0     # 3x the blackout threshold


def _wire_task_result(task_id: str, task, record) -> None:
    """R-F4106 (C-151) — report what the task ACTUALLY did.

    THE DEFECT: the tick loop discarded `execute_task`'s return value and then
    wired `wire_success("Task fired: …")` unconditionally. `execute_task`
    returns a record whose `status` is one of
    `ok | error | timeout | blocked_by_predictor | started`, and it contains
    ZERO `wire_failure` calls of its own — so a task that RAISED or TIMED OUT
    produced a brain SUCCESS signal and no failure signal at all.

    That is §21a inverted: the failure branch did not merely fail to reach the
    brain, it reached it wearing a success. §25a requires ARIA to know whether
    the intended result was produced; every task reported that it was.

    (R-F2706 fixed the neighbouring half — per-channel DELIVERY outcomes — which
    is why this looked covered. `_wire_task_delivery_outcomes` runs only on the
    success path and reports delivery, never execution status.)

    Three outcomes, three readings, and the distinctions are load-bearing:

      * `ok`                    → success.
      * `blocked_by_predictor`  → a DELIBERATE skip. §14: cooling/skipping is
                                  not broken, so it is not a failure — but it
                                  must not read as a plain success either, or
                                  "we skipped it" and "it worked" collapse.
      * anything else, INCLUDING an unreadable/missing record → failure.
        "I could not tell" must never be certified as success; that is the
        absence-reads-as-health shape §1 records three times.

    Never raises: observability must not be able to kill the tick loop.
    """
    try:
        status = (record or {}).get("status") or "unknown"
    except Exception:
        status = "unknown"
    _cron = getattr(task, "cron", "?")
    try:
        if status == "ok":
            from ..intel.engine_wiring import wire_success as _ws
            _ws(
                module="autonomous_engine",
                summary=f"Task completed: {task_id}",
                detail=f"cron={_cron} dry_run={is_dry_run()} status=ok",
                source_id=f"autonomous_engine:task:{task_id}",
            )
        elif status == "blocked_by_predictor":
            from ..intel.engine_wiring import wire_success as _ws
            _ws(
                module="autonomous_engine",
                summary=f"Task skipped by predictor: {task_id}",
                detail=f"cron={_cron} status={status} (deliberate skip, not a fault)",
                source_id=f"autonomous_engine:task_skipped:{task_id}",
            )
        else:
            from ..intel.engine_wiring import wire_failure as _wf
            _err = ""
            try:
                _err = str((record or {}).get("error") or "")[:200]
            except Exception:
                _err = ""
            _wf(
                module="autonomous_engine",
                detail=(f"Task {task_id} did not succeed: status={status} "
                        f"cron={_cron} error={_err or 'none reported'}"),
                gap_type="engine_failure",
                source="autonomous_engine:R-F4106",
            )
    except Exception:      # pragma: no cover — never break the tick loop
        pass


async def _heartbeat_during_task(
    task_id: str, tick, *, interval: float = _TASK_HEARTBEAT_INTERVAL_S,
    max_busy_s: float = _TASK_HEARTBEAT_MAX_BUSY_S,
) -> None:
    """Keep the engine heartbeat fresh while a task executes — but NOT forever.

    R-F3824. The heartbeat was ticked once per POLLING-LOOP iteration, so any task
    running longer than the 300s blackout threshold made a healthy engine look dead.
    Live 2026-08-10, twice: `heartbeat stale 301.1s` / `310.4s` with `Task-143
    (_engine_loop) done=False cancelled=False` parked at the `execute_task` await.
    One signal was standing for two very different states, "busy" and "wedged".

    THE BOUND IS THE POINT. Ticking for as long as `execute_task` is on the stack
    would remove the false blackout and the true one together: a task hung forever
    would hold the heartbeat fresh forever and R-F1146 could never fire again. That
    trades a noisy alarm for no alarm. So this stops at `max_busy_s` and lets the
    detector do its job for anything beyond a plausible task duration.

    `tick` is injected rather than imported so the call site keeps its existing
    ImportError guard, and so this is testable without the store.
    """
    waited = 0.0
    while waited < max_busy_s:
        await asyncio.sleep(interval)
        waited += interval
        try:
            tick("autonomous_engine")
        except Exception:      # observability must never kill the loop it observes
            logger.debug("[R-F3824] heartbeat tick failed during task %s", task_id,
                         exc_info=True)
    logger.warning(
        "[R-F3824] task %s has been running %.0fs — heartbeat ticking STOPS here so a "
        "genuine wedge is still detectable by the R-F1146 blackout detector",
        task_id, max_busy_s,
    )


async def _engine_loop(llm) -> None:
    """The main engine loop. Runs forever (until cancelled at shutdown).

    Tick semantics:
      - Wake every 60 seconds
      - Snap to the start of the current UTC minute
      - For each loaded task, evaluate cron expression at that minute
      - For each match: run safety guardrails, then execute_task()
      - Tasks are run SERIALLY (one at a time) so we can never
        burn the rate limit budget on parallel fires
    """
    global _started_at, _last_tick_at, _tick_count, _fire_count

    _started_at = time.time()
    logger.info(
        "[autonomous engine] starting — startup delay %ds, poll interval %ds",
        STARTUP_DELAY_SECONDS, POLL_INTERVAL_SECONDS,
    )


    # R-F1897: register in the agent registry so other agents can see us
    try:
        from ..intel.agent_registry import AgentRegistry
        _reg = AgentRegistry()
        await _reg.register(
            agent_id="autonomous_engine",
            agent_type="autonomous_scheduler",
            current_task="starting up - waiting for startup delay",
        )
    except Exception:
        pass  # registration is best-effort, never breaks the engine

    await asyncio.sleep(STARTUP_DELAY_SECONDS)

    # First load of tasks.yaml — also re-loadable via the reload-tasks
    # admin endpoint.
    # R-F1750 (2026-06-20) — offload the load: load_tasks() does a synchronous
    # yaml.safe_load of tasks.yaml, and a live wedge capture 2026-06-20
    # (/data/wedge_stacks/wedge_675) caught `_engine_loop:243 → load_tasks →
    # yaml.safe_load` blocking the MAIN event loop ~5-6s (this runs ~90s after
    # boot, exactly when post-deploy users retry), starving concurrent SSE
    # streams. Parse off-loop so the engine's own loop never freezes requests.
    try:
        await asyncio.to_thread(tasks_mod.load_tasks)
    except Exception as e:
        logger.error("[autonomous engine] initial tasks load failed: %s", e)

    # R-F1146 — start the blackout detector and tick heartbeat
    try:
        from ..intel.self_restart import start_blackout_detector, tick_heartbeat, save_checkpoint
        start_blackout_detector()
    except ImportError:
        pass

    # R-F2631 — STARTUP MAINTENANCE RUNS IN THE BACKGROUND. The tick loop
    # below must begin within seconds of the startup delay, not minutes.
    #
    # This was the reason the engine was dark, and it dwarfed the dedupe bug:
    # `repair` and `catch_up_overdue_tasks` sat on the PRE-LOOP path and were
    # awaited serially, and catch_up executes up to _CATCH_UP_MAX_FIRES (15)
    # real tasks INLINE (engine.py:506 `await tasks_mod.execute_task(...)`) —
    # one observed task ran >10 min despite timeout_seconds=180. Measured live
    # 2026-07-15: time-to-first-tick 19.2 min, and `tick_count=0` 28.7 min
    # after _started_at. Meanwhile a process crashed (exit_code=1) after 6.6
    # min. So for long stretches the polling loop DID NOT EXIST — no cron was
    # ever evaluated. Expected ~964 fires/24h from tasks.yaml's crons;
    # observed 7. Every restart reset the 19-min clock.
    #
    # Running them concurrently with the loop is SAFE: both paths go through
    # safety.can_task_run, whose dedupe marker is exactly the guard against a
    # catch-up and a tick firing the same task twice. That is what dedupe is
    # FOR — this is not a new race, it is the existing one being used as
    # designed.
    #
    # Ordering within the background task is preserved: repair BEFORE catch_up
    # (catch_up is a dedupe consumer and must not be blocked by the markers
    # being cleared).
    async def _startup_maintenance() -> None:
        # R-F2626/R-F2629 — release dedupe markers stranded WITHOUT a TTL by
        # the old non-atomic set+expire race. Those never expire, so
        # `duplicate_recent_run` blocks the task forever. Idempotent and
        # precise (NULL-TTL only), so it is safe to run on every start.
        try:
            _repair = await safety.repair_nulled_dedupe_markers()
            if _repair.get("deleted"):
                logger.info(
                    "[autonomous engine] R-F2629 dedupe repair on startup: %s",
                    _repair,
                )
        except Exception as _rep_err:  # noqa: BLE001
            logger.warning(
                "[autonomous engine] R-F2629 dedupe repair failed (non-fatal): %s",
                _rep_err,
            )

        # R-F2013 — fire any task that MISSED its scheduled slot while we were
        # down / restarting (e.g. a restart spanned a once-an-hour task's :00).
        try:
            n_caught = await catch_up_overdue_tasks(llm)
            if n_caught:
                logger.warning("[R-F2013] startup catch-up fired %d missed task(s)", n_caught)
        except Exception as e:  # noqa: BLE001
            logger.warning("[R-F2013] startup catch-up failed (non-fatal): %s", e)

    # Fire-and-forget: a failure in maintenance must never stop the loop, and
    # a SLOW maintenance must never delay it. Held in a module ref so the task
    # isn't garbage-collected mid-flight (asyncio only keeps weak refs).
    global _startup_maintenance_task
    _startup_maintenance_task = asyncio.create_task(_startup_maintenance())
    logger.info(
        "[autonomous engine] startup maintenance dispatched to background — "
        "tick loop starting now (R-F2631)",
    )

    while True:
        try:
            _last_tick_at = time.time()
            _tick_count += 1

            # R-F1146 — tick heartbeat so the blackout detector knows we're alive
            try:
                tick_heartbeat("autonomous_engine")
            except Exception:
                pass
            # R-F1897 — tick agent registry heartbeat so other agents see us alive
            try:
                from ..intel.agent_registry import AgentRegistry
                _reg_hb = AgentRegistry()
                await _reg_hb.tick_heartbeat("autonomous_engine", "running tasks")
            except Exception:
                pass
            # R-F2006 — liveness heartbeat for the engine WATCHDOG (a SEPARATE
            # loop in main.py alerts the operator if this stops). Written every
            # tick BEFORE the enabled/pause gates, so it proves the loop ITSELF
            # is alive even while paused/disabled — letting the watchdog tell
            # "loop dead/crashed" apart from "alive but not firing". This is the
            # missing signal that let the R-F2004 187h fire=0 outage go unnoticed.
            try:
                from ..intel import redis_store as _rs_hb
                await _rs_hb.set("crucix:autonomous:last_tick_ts", str(int(time.time())))
            except Exception:
                pass

            # Refresh the runtime override so /autonomous/disable takes
            # effect within one tick without restarting the service.
            await refresh_runtime_override()
            if not is_enabled():
                # R-F2184 — heal a LOST master flag (env default off + no override)
                # when the operator's durable intent is enabled; respects a
                # deliberate override=0. If recovered, fall through and run this tick.
                _rec = await maybe_autorecover_master_switch()
                if not _rec.get("recovered"):
                    # Master switch deliberately off (or no desired marker). Sleep
                    # the tick so we don't spin; the admin endpoint can flip us back
                    # on and the next tick will resume.
                    await asyncio.sleep(POLL_INTERVAL_SECONDS)
                    continue
            # R-F2184 — engine is enabled: durably record the operator's intent so a
            # future lost flag can be auto-healed (once per process).
            await _mark_desired_enabled()

            # Engine globally paused via Redis flag?
            if await safety.is_engine_paused():
                logger.debug("[autonomous engine] paused — skipping tick")
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                continue

            # R-F2185 — adaptive load governor: SELF-REGULATE. If user-facing
            # serving is under pressure (state_store write-queue backing up or
            # the event loop stalling), shed this tick so autonomy can never
            # starve chat/DD on the single-process brain. Auto-clears when the
            # brain is calm again — no operator intervention. This is the
            # self-heal that turns "autonomy degrades serving" into "autonomy
            # yields to serving". Fail-safe: a probe error reports no pressure.
            try:
                from ..intel import load_governor as _lg
                if _lg.should_shed():
                    await asyncio.sleep(POLL_INTERVAL_SECONDS)
                    continue
            except Exception:
                pass  # governor must never break the engine

            # Check operating mode — some modes restrict which tasks can run
            from ..intel import operating_modes as _om
            mode = await _om.get_mode()

            # Iterate over loaded tasks
            loaded = tasks_mod.get_loaded_tasks()
            if not loaded:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                continue

            now_utc = time.gmtime()

            # R-F97 (2026-05-09): bias task selection toward priority
            # domains computed by R-F90 continuous_update. When multiple
            # tasks match cron at the same tick (common at 06:00 UTC),
            # tasks tagged for currently-stale or coverage-gap domains
            # fire first. Best-effort; never blocks the engine.
            try:
                from ..intel import continuous_update as _cu
                _priorities = await _cu.read_priorities()
                _priority_domains = set(
                    p.get("domain", "").lower()
                    for p in (_priorities.get("priorities") or [])[:10]
                )
            except Exception:
                _priority_domains = set()

            def _task_priority_score(task_item):
                _, t = task_item
                tags = set((tag or "").lower() for tag in (t.mem0_tags or []))
                # +1 per priority-domain match in the task's mem0_tags or id
                score = 0
                t_id_lower = (t.id or "").lower()
                for d in _priority_domains:
                    if d and (d in t_id_lower or d in tags):
                        score += 1
                return -score  # negative for descending sort

            ordered_tasks = sorted(loaded.items(), key=_task_priority_score)

            # R-F1849: per-tick task concurrency cap with boot ramp-up.
            # Under autonomous load (97 tasks), the absorb storm from firing
            # all matching tasks in one tick overwhelms the event loop even
            # with individual asyncio.to_thread offloads. Cap tasks per tick
            # and ramp up over the first 10 ticks after startup.
            _boot_ticks = max(0, 10 - _tick_count)
            _per_tick_cap = max(3, 20 - _boot_ticks * 2)  # 3 -> 5 -> 7 -> ... -> 20
            _fired_this_tick = 0

            for task_id, task in ordered_tasks:
                # Cheap filters first
                if not task.enabled:
                    continue
                if not tasks_mod.cron_matches(task.cron, now_utc):
                    continue
                # Operating mode filter — EMERGENCY blocks non-essential tasks
                if not _om.should_task_run(task_id, mode):
                    logger.info("[autonomous engine] task %s blocked by operating mode %s",
                                task_id, mode.name)
                    continue

                # Determine the entity used for the dedupe hash. We use
                # the first tool_chain entry's entity field if present,
                # otherwise the task id alone.
                entity = _resolve_task_entity(task)

                # Safety guardrails (rate / cost / dedupe / pauses)
                # R-F2635 — dedupe on the cron slot we just matched
                # (`now_utc`, the same minute cron_matches evaluated), so a
                # task fires once PER SCHEDULED SLOT instead of once per 23h.
                # timegm() converts the UTC struct_time back to epoch — using
                # the matched minute (not time.time()) keeps the marker exactly
                # aligned with the slot that authorised this fire.
                allowed, reason = await safety.can_task_run(
                    task_id, entity or task_id,
                    slot=int(calendar.timegm(now_utc) // 60),
                )
                if not allowed:
                    logger.info(
                        "[autonomous engine] task %s blocked: %s",
                        task_id, reason,
                    )
                    continue

                # R-F1849: per-tick concurrency cap with boot ramp-up
                _fired_this_tick += 1
                if _fired_this_tick > _per_tick_cap:
                    logger.debug(
                        "[autonomous engine] per-tick cap %d reached "
                        "- deferring remaining tasks",
                        _per_tick_cap,
                    )
                    break

                logger.info(
                    "[autonomous engine] firing task %s (cron=%r dry_run=%s)",
                    task_id, task.cron, is_dry_run(),
                )
                _fire_count += 1
                # Persist a rolling 24h counter so /autonomy/surface can
                # report real fires. Before this, the in-process counter
                # was never synced to Redis, so the dashboard always read
                # 0 and reset on every deploy. Set TTL only on the first
                # incr from a missing key — calling expire() on every
                # incr resets the TTL, so under continuous firing the
                # counter would never decay (became a lifetime tally,
                # not a 24h window).
                try:
                    from ..intel import redis_store as rs
                    new_val = await rs.incr("crucix:autonomous:fires_24h")
                    if new_val == 1:
                        await rs.expire("crucix:autonomous:fires_24h", 90_000)
                    # R-F2006 — last-fire recency for the watchdog (distinguishes
                    # "alive but firing nothing" from healthy firing).
                    await rs.set("crucix:autonomous:last_fire_ts", str(int(time.time())))
                    # R-F2013 — per-task last-fire so startup catch-up knows what
                    # already ran (and doesn't double-fire a task that fired normally).
                    await _set_task_last_fire(task_id, time.time())
                except Exception as _e:
                    logger.debug("fires_24h counter incr failed: %s", _e)

                # Run the task. execute_task() handles its own
                # try/except + run history persistence — we just have
                # to call it and not let an exception escape into the
                # polling loop.
                try:
                    # R-F3824 — keep the heartbeat fresh WHILE the task runs.
                    #
                    # This await is engine.py:1019, the exact line the live blackout
                    # dumps name: `Task-143 (_engine_loop) done=False cancelled=False`
                    # with `heartbeat stale 301.1s`. The heartbeat is ticked once per
                    # polling-loop iteration (above), so any task outliving the 300s
                    # threshold made a healthy engine read as dead.
                    #
                    # Bounded on purpose — see `_heartbeat_during_task`: ticking for
                    # as long as this await is on the stack would disarm the wedge
                    # detector entirely.
                    _hb_task = None
                    try:
                        _hb_task = asyncio.create_task(
                            _heartbeat_during_task(task_id, tick_heartbeat),
                            name=f"engine_hb:{task_id}",
                        )
                    except Exception:      # NameError if the R-F1146 import failed
                        _hb_task = None
                    try:
                        # R-F4106 (C-151) — BIND the result. This call used to
                        # discard it, so the wiring below could only ever say
                        # "fired", never "worked".
                        _task_record = await tasks_mod.execute_task(
                            task=task,
                            llm=llm,
                            dry_run=is_dry_run(),
                        )
                    finally:
                        if _hb_task is not None:
                            _hb_task.cancel()
                    # R-F1059 / R-F4106 — wire the task's ACTUAL outcome.
                    _wire_task_result(task_id, task, _task_record)
                    # R-F1146 — save checkpoint after successful task
                    try:
                        await save_checkpoint(
                            agent_id="autonomous_engine",
                            current_task=f"Task completed: {task_id}",
                            error_context="",
                        )
                    except Exception:
                        pass
                except Exception as e:
                    logger.warning(
                        "[autonomous engine] task %s execution raised: %s: %s",
                        task_id, type(e).__name__, e,
                    )
                    # R-F1059 — wire task failure to brain
                    try:
                        from ..intel.engine_wiring import wire_failure as _wf
                        _wf(
                            module="autonomous_engine",
                            detail=f"Task {task_id} failed: {type(e).__name__}: {e}",
                            gap_type="engine_failure",
                            source="autonomous_engine",
                        )
                    except Exception:
                        pass
                    # R-F1146 — save checkpoint with error context on failure
                    try:
                        await save_checkpoint(
                            agent_id="autonomous_engine",
                            current_task=f"Task failed: {task_id}",
                            error_context=f"{type(e).__name__}: {e}",
                        )
                    except Exception:
                        pass
        except asyncio.CancelledError:
            logger.info("[autonomous engine] cancelled — exiting loop")
            raise
        except Exception as e:
            logger.warning("[autonomous engine] tick raised: %s: %s", type(e).__name__, e)

        # Sleep until the next tick
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


# ── Lifecycle: start and stop ──────────────────────────────────────────────

@fail_wire(module="engine", gap_type="agent_cycle_failure")
def start_engine(llm) -> bool:
    """Spawn the engine loop as an asyncio task. Idempotent — calling
    twice is a no-op. Returns True if the engine started (or was
    already running), False if disabled by env var.
    """
    global _engine_task
    if not is_enabled():
        logger.info(
            "[autonomous engine] not started — %s is OFF (set %s=1 to enable)",
            _ENABLED_VAR, _ENABLED_VAR,
        )
        return False
    if _engine_task is not None and not _engine_task.done():
        logger.info("[autonomous engine] already running")
        return True
    if llm is None or not getattr(llm, "is_configured", False):
        logger.warning(
            "[autonomous engine] not started — LLM provider is not configured",
        )
        # R-F1059 — wire the skip to brain
        try:
            from ..intel.engine_wiring import wire_failure as _wf
            _wf(
                module="autonomous_engine",
                detail="Engine not started: LLM not configured",
                gap_type="engine_failure",
                source="autonomous_engine",
            )
        except Exception:
            pass
        return False
    # Load tasks.yaml NOW so the "started" log line is accurate AND the
    # engine's first tick has tasks ready. Previously load_tasks() ran
    # INSIDE _engine_loop after the 90s startup delay, so start_engine
    # always logged "0 tasks loaded" -- live observation 2026-04-27 18:00:05.
    # Loading is a sync file read; failures are tolerated by load_tasks
    # (returns previous cache). _engine_loop still re-loads on its first
    # iteration so reload-tasks admin endpoint behavior is unchanged.
    try:
        tasks_mod.load_tasks()
    except Exception as e:
        logger.warning("[autonomous engine] eager tasks load failed (will retry in loop): %s", e)
    _engine_task = asyncio.create_task(_engine_loop(llm))
    _engine_task.add_done_callback(_on_engine_done)
    logger.info(
        "[autonomous engine] started (dry_run=%s, %d tasks loaded)",
        is_dry_run(), len(tasks_mod.get_loaded_tasks()),
    )
    return True


def _on_engine_done(t: asyncio.Task) -> None:
    """Done callback that surfaces unexpected loop exits."""
    if t.cancelled():
        logger.info("[autonomous engine] task ended via cancellation")
        return
    exc = t.exception()
    if exc is not None:
        logger.error(
            "[autonomous engine] task ended with exception: %s: %s",
            type(exc).__name__, exc,
        )
        # R-F1059 — wire engine crash to brain
        try:
            from ..intel.engine_wiring import wire_failure as _wf
            _wf(
                module="autonomous_engine",
                detail=f"Engine loop crashed: {type(exc).__name__}: {exc}",
                gap_type="engine_failure",
                source="autonomous_engine",
            )
        except Exception:
            pass
    else:
        logger.warning("[autonomous engine] task ended without exception (unexpected)")


@fail_wire(module="engine", gap_type="agent_cycle_failure")
async def stop_engine() -> None:
    """Cancel the engine task. Used by the lifespan shutdown hook."""
    global _engine_task
    if _engine_task is None:
        return
    if _engine_task.done():
        _engine_task = None
        return
    _engine_task.cancel()
    try:
        await _engine_task
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.warning("[autonomous engine] stop raised: %s", e)
    finally:
        _engine_task = None


# ── Manual run-now (admin endpoint) ────────────────────────────────────────

@fail_wire(module="engine", gap_type="agent_cycle_failure")
async def run_task_now(task_id: str, llm) -> dict[str, Any]:
    """Manually fire a single task immediately, regardless of cron or
    enabled flag. Used by the /api/aria/autonomous/run-now/<task_id>
    admin endpoint for one-shot validation.

    Safety guardrails STILL apply (rate / cost / engine pause) — only
    the cron schedule and the per-task enabled flag are bypassed.

    The dry_run flag still applies UNLESS the caller passes a query
    string to override it (handled in the admin endpoint, not here).
    """
    loaded = tasks_mod.get_loaded_tasks()
    task = loaded.get(task_id)
    if task is None:
        return {
            "ok": False,
            "error": f"task {task_id!r} not found in loaded tasks (have: {sorted(loaded.keys())})",
        }
    # R-F2635 — one resolver, everywhere (verify-pass-2). A verbatim third
    # copy here is the drift vector the helper exists to kill, even though it
    # is behaviourally identical today.
    entity = _resolve_task_entity(task)
    allowed, reason = await safety.can_task_run(task_id, entity or task_id)
    if not allowed:
        return {"ok": False, "blocked": reason, "task_id": task_id}
    record = await tasks_mod.execute_task(
        task=task,
        llm=llm,
        dry_run=is_dry_run(),
    )
    return {"ok": True, "task_id": task_id, "record": record}

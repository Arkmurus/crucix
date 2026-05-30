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
import logging
import os
import time
from typing import Any

from . import safety, tasks as tasks_mod

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


async def refresh_runtime_override() -> str | None:
    """Read the Redis override into the in-process cache. Called at
    lifespan startup (before start_engine) and once per engine tick so
    flips made via /autonomous/enable are seen within one poll cycle.

    Returns the cached value ("1", "0", or None) for logging.
    """
    try:
        from ..intel import redis_store as rs
        v = await rs.get(_REDIS_ENABLE_KEY)
        cleaned = (v or "").strip()
        _RUNTIME_ENABLE_CACHE["val"] = cleaned if cleaned in ("0", "1") else None
        _RUNTIME_ENABLE_CACHE["ts"] = time.time()
    except Exception as e:
        logger.debug("[autonomous engine] runtime override read failed: %s", e)
        _RUNTIME_ENABLE_CACHE["val"] = None
    return _RUNTIME_ENABLE_CACHE.get("val")


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


# ── The polling loop ───────────────────────────────────────────────────────

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
    await asyncio.sleep(STARTUP_DELAY_SECONDS)

    # First load of tasks.yaml — also re-loadable via the reload-tasks
    # admin endpoint.
    try:
        tasks_mod.load_tasks()
    except Exception as e:
        logger.error("[autonomous engine] initial tasks load failed: %s", e)

    # R-F1146 — start the blackout detector and tick heartbeat
    try:
        from ..intel.self_restart import start_blackout_detector, tick_heartbeat, save_checkpoint
        start_blackout_detector()
    except ImportError:
        pass

    while True:
        try:
            _last_tick_at = time.time()
            _tick_count += 1

            # R-F1146 — tick heartbeat so the blackout detector knows we're alive
            try:
                tick_heartbeat("autonomous_engine")
            except Exception:
                pass

            # Refresh the runtime override so /autonomous/disable takes
            # effect within one tick without restarting the service.
            await refresh_runtime_override()
            if not is_enabled():
                # Master switch flipped off while running. Sleep the
                # tick so we don't spin; the admin endpoint can flip us
                # back on and the next tick will resume.
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                continue

            # Engine globally paused via Redis flag?
            if await safety.is_engine_paused():
                logger.debug("[autonomous engine] paused — skipping tick")
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                continue

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
                entity = ""
                if task.tool_chain and isinstance(task.tool_chain[0], dict):
                    entity = (
                        task.tool_chain[0].get("entity")
                        or task.tool_chain[0].get("topic")
                        or task.tool_chain[0].get("query")
                        or ""
                    )

                # Safety guardrails (rate / cost / dedupe / pauses)
                allowed, reason = await safety.can_task_run(task_id, entity or task_id)
                if not allowed:
                    logger.info(
                        "[autonomous engine] task %s blocked: %s",
                        task_id, reason,
                    )
                    continue

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
                except Exception as _e:
                    logger.debug("fires_24h counter incr failed: %s", _e)

                # Run the task. execute_task() handles its own
                # try/except + run history persistence — we just have
                # to call it and not let an exception escape into the
                # polling loop.
                try:
                    await tasks_mod.execute_task(
                        task=task,
                        llm=llm,
                        dry_run=is_dry_run(),
                    )
                    # R-F1059 — wire task success to brain
                    try:
                        from ..intel.engine_wiring import wire_success as _ws
                        _ws(
                            module="autonomous_engine",
                            summary=f"Task fired: {task_id}",
                            detail=f"cron={task.cron} dry_run={is_dry_run()}",
                            source_id=f"autonomous_engine:task:{task_id}",
                        )
                    except Exception:
                        pass
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
    entity = ""
    if task.tool_chain and isinstance(task.tool_chain[0], dict):
        entity = (
            task.tool_chain[0].get("entity")
            or task.tool_chain[0].get("topic")
            or task.tool_chain[0].get("query")
            or ""
        )
    allowed, reason = await safety.can_task_run(task_id, entity or task_id)
    if not allowed:
        return {"ok": False, "blocked": reason, "task_id": task_id}
    record = await tasks_mod.execute_task(
        task=task,
        llm=llm,
        dry_run=is_dry_run(),
    )
    return {"ok": True, "task_id": task_id, "record": record}

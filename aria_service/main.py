"""
ARIA Service — FastAPI entrypoint. R-F1191: fully autonomous.

Runs the complete ARIA intelligence engine as a standalone Python service.
Replaces both the Node.js lib/aria/ and the Flask brain/ service.

Usage:
    python -m aria_service.main
    # or
    uvicorn aria_service.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import asyncio
import logging
import os as _os
import time
from contextlib import asynccontextmanager
from typing import Any, Optional

import json

# R-F1448: lazy import for AgentContract (used in lifespan for web_integrity proof)
# Imported at module level so it doesn't shadow anything inside lifespan.
from .intel.agent_contract import AgentContract

# R-F1608: strong references for lifespan background tasks. Without this set,
# asyncio.create_task() assigned to a local variable can be garbage-collected
# before the task completes — the task is cancelled silently. All long-cycle
# agent loops (tender_monitor, watchlist_rescreen, weekly_report, etc.) must
# be added here with a done_callback that logs any unhandled exception.
_BOOT_TIME: float = time.time()  # R-F1611 — process boot time for proprioception
_BG_TASKS: set[asyncio.Task] = set()
# R-F1610 — self-healing actuator state. _BG_RESPAWN maps a loop's task-name to
# its factory (a no-arg callable returning the loop coroutine) so the supervisor
# can RE-SPAWN it if it dies — turning self-healing from "log it" into "fix it".
# _BG_RESPAWN_COUNT bounds re-spawns so a genuinely-broken loop can't crash-loop.
_BG_RESPAWN: dict[str, "callable"] = {}
_BG_RESPAWN_COUNT: dict[str, int] = {}
_BG_MAX_RESPAWNS = 5
# R-F1769 — act+verify self-heal: loops re-spawned LAST tick, awaiting survival
# confirmation. The supervisor only wires the truthful "re-spawn VERIFIED alive"
# success once a re-spawned loop is still live a full supervisor interval later —
# not the instant it's created (which proves nothing).
_BG_RESPAWN_PENDING: set[str] = set()


def _bg_task(task: asyncio.Task, name: str = "", factory=None) -> asyncio.Task:
    """Register a background task so it survives GC, and log if it dies.

    R-F1608: strong reference in _BG_TASKS + done_callback that logs any
    unhandled exception (so a silent crash is visible, not invisible).
    R-F1610: if `factory` is given, register it so the bg supervisor can
    re-spawn this loop should it die — the self-healing actuator.
    """
    _BG_TASKS.add(task)
    _nm = name or task.get_name()
    if factory is not None and _nm:
        _BG_RESPAWN[_nm] = factory
    task.add_done_callback(lambda t: (_BG_TASKS.discard(t),
                                       (not t.cancelled()) and t.exception() and logger.error(
                                           "[R-F1608] bg task %s died: %s",
                                           name or t.get_name(), t.exception())))
    return task


# ── R-F2073 (Tier 1) — PROCESS ROLE for multi-worker scaling ────────────────
# The brain is a single-process asyncio app today (1 uvicorn worker → 1 event
# loop). To "deploy more workers whenever needed" we must be able to run N
# request-serving processes WITHOUT running N copies of the ~15 singleton
# background loops (autonomous engine, research/self-improve/coder, schedulers,
# monitors, deploy/guardian/weekly/watchlist/tender loops). N copies would mean
# N× LLM cost, N× external API calls, N× git auto-deploys, and races on the
# shared coder gap-queue. Role-split is the KEYSTONE that lets the rest become a
# config flip.
#
#   ARIA_ROLE=engine  → runs singleton loops AND serves requests
#   ARIA_ROLE=web     → serves requests ONLY (no singleton loops)
#   unset / 'all'     → BOTH (today's single-process behavior — BACKWARD-COMPAT)
#
# Per-process warmers (embedder/ocr prewarm, rag init, stall detector, bg
# supervisor, health precompute) are NOT singletons — they run on every role,
# because each process needs its own warm caches and its own heartbeat.
# R-F2174 — engine-role election (opt-in, default OFF). When multiple uvicorn
# workers run they all inherit the SAME env, so ARIA_ROLE alone can't mark just
# one as the engine. With ARIA_ENGINE_ELECTION=1 the workers atomically claim an
# engine lease in state_store at startup — exactly one wins ('engine'), the rest
# become 'web'. Default OFF + an explicit ARIA_ROLE override + fail-safe-to-'all'
# mean the current single-worker ecosystem is bit-for-bit unchanged until the
# election is deliberately enabled on a coordinated multi-worker deploy.
_resolved_role: "str | None" = None          # set by _elect_engine_role()
_ENGINE_LEASE_KEY = "crucix:aria:engine_lease"
_engine_lease_id: "str | None" = None
# R-F2219 — set once the engine election has resolved. Singleton loops that are
# STARTED BEFORE the election (expiry_sweeper, the crawler in _boot_continuation)
# await this before deciding their role, so a not-yet-elected 'web' worker cannot
# start a singleton during the startup race (_aria_role() defaults to 'all' until
# _elect_engine_role runs).
_election_complete: "asyncio.Event | None" = None


def _aria_role() -> str:
    # An explicit env override always wins (manual pinning / today's behaviour).
    _env = (_os.getenv("ARIA_ROLE") or "").strip().lower()
    if _env in ("engine", "web", "all"):
        return _env
    # Otherwise honour the elected role if the election ran; else 'all'.
    return _resolved_role or "all"


def _runs_singletons() -> bool:
    """True on the engine process, or the default all-in-one single process.
    A 'web' role process returns False and starts no singleton loops."""
    return _aria_role() in ("engine", "all", "")


def _portal_registration_enabled() -> bool:
    """Return True only when autonomous portal signup is explicitly enabled.

    R-F2389: portal registration uses a real browser agent and is valuable only
    after the MVP data plane is stable. Keep curated vault/source ingestion live,
    but make human-like portal signup opt-in so it cannot starve brain endpoints.

    R-F3198 (2026-07-26) — RETIRED, not merely defaulted off.

    Operator direction: stop overloading the brain. Autonomous portal signup
    drove a real browser agent plus third-party CAPTCHA solving on a schedule,
    and it is the single heaviest background consumer that produces no
    user-facing output. R-F2389 made it opt-in; this makes it unavailable, so
    an env var set in a future deploy cannot silently restart it.

    Retired at the GATE rather than by deleting the scheduler, because the
    surrounding boot block, its logging and the R-F1447 asyncio note are all
    still worth reading. And portal_registry.py itself STAYS: DD depends on its
    lookup helpers (company_investigator imports lookup_contracts_by_uei), so
    deleting the module to remove the signup behaviour would take a working
    feature with it.
    """
    return False


def _singleton_task(factory, name: str, *, respawn: bool = True) -> "asyncio.Task | None":
    """R-F2073 — start a SINGLETON background loop, but ONLY on a process that
    owns the singletons (engine / all-in-one). On a 'web' role process the loop
    is skipped (logged once) so N web workers never each run it. Mirrors
    _bg_task registration so the bg supervisor can still respawn it on the
    engine process. `factory` is the zero-arg coroutine function for the loop.

    R-F2668 — `respawn`: when False, the task is NOT registered with the bg
    supervisor. Use for ONE-SHOT startup tasks (e.g. the boot-time knowledge
    seed) that run once and RETURN. The supervisor only knows "not done() = live"
    (see _bg_supervisor_tick) — it cannot tell a clean one-shot completion from a
    crash, so a respawn-registered one-shot gets re-spawned on every NORMAL
    completion until it hits _BG_MAX_RESPAWNS and emits the R-F1610 'NEEDS
    OPERATOR' ERROR, which reset the gate-#3 streak on every boot. Genuine
    while-True loops keep respawn=True (the default) so real crashes still heal."""
    if not _runs_singletons():
        logger.info("[R-F2073] singleton loop '%s' SKIPPED (ARIA_ROLE=%s)", name, _aria_role())
        return None
    return _bg_task(
        asyncio.create_task(factory(), name=name),
        factory=(factory if respawn else None),
    )


def _engine_election_enabled() -> bool:
    # R-F2186 — safety bind: WEB_CONCURRENCY>1 IMPLIES election must be on. With
    # multiple uvicorn workers but election OFF, every worker keeps role 'all' and
    # runs EVERY singleton loop (N× LLM cost, N× deploys, gap-queue races) — a
    # silent footgun the DD flagged. Auto-engage election whenever workers scale so
    # exactly one worker owns the singletons, regardless of the env flag.
    if (_os.getenv("ARIA_ENGINE_ELECTION") or "0").strip().lower() in ("1", "true", "yes"):
        return True
    return _web_concurrency() > 1


# ── R-F4215 / C-195 — a malformed tuning knob must never raise ───────────────
# main.py parsed six operator env vars with a bare int()/float(). A typo in any
# of them raised ValueError where nothing catches it: boot failed, or the engine
# heartbeat died and lost the singleton lease, or the event-loop wedge watchdog
# went dark, or the reading loop that feeds gate-2 mastery stopped — and
# ARIA_MAX_BODY_BYTES is parsed at MODULE level, so a typo there made
# `import aria_service.main` itself fail and the service could not start at all.
# Same class as C-192, where exactly this raise sat above the only
# heavy_graph_ready.set() and parked ARIA's entire metabolism.
#
# The convention was never in doubt: autonomous/safety.py, intel/user_quota.py,
# intel/neural_memory.py, intel/dd_orchestrator.py and autonomous/self_coder.py
# each independently wrote this guard. main.py — the file where a raise is most
# expensive — was the outlier. A knob is for TUNING behaviour; it must never be
# able to disable it.
def _env_number(name: str, default, caster):
    """Parse an operator env var, falling back to `default` on anything invalid."""
    raw = (_os.getenv(name, "") or "").strip()
    if not raw:
        return default
    try:
        return caster(raw)
    except (TypeError, ValueError):
        # A silently-ignored misconfiguration is an unwired failure branch
        # (§21a); the operator must be told which knob was dropped. logger is
        # bound below this point in the module, so fall back to stderr if a
        # future module-level caller runs before it exists (the neural_memory.py
        # precedent) — never let the WARNING itself become the raise.
        try:
            logger.warning(
                "[R-F4215] %s=%r is not a valid number — using the default %r. "
                "Set a bare number.", name, raw, default,
            )
        except Exception:  # noqa: BLE001 — logging must never break boot
            import sys as _sys_w
            _sys_w.stderr.write(
                f"[R-F4215] {name}={raw!r} invalid - using {default!r}\n")
        return default


def _env_float(name: str, default: float) -> float:
    return _env_number(name, default, float)


def _env_int(name: str, default: int) -> int:
    return _env_number(name, default, int)


def _engine_lease_ttl_s() -> int:
    return max(10, _env_int("ARIA_ENGINE_LEASE_TTL_S", 45))


def _web_concurrency() -> int:
    """R-F2174 — uvicorn worker count. Default 1 = today's single-process
    behaviour (unchanged). Set WEB_CONCURRENCY>1 (on a coordinated deploy, with
    ARIA_ENGINE_ELECTION=1 + ARIA_TOTAL_LLM_WORKERS=N) to run N workers."""
    try:
        return max(1, int(_os.getenv("WEB_CONCURRENCY", "1")))
    except (TypeError, ValueError):
        return 1


async def _elect_engine_role() -> None:
    """R-F2174 — resolve THIS worker's role via an atomic engine-lease claim.

    No-op (leaves role 'all') unless ARIA_ENGINE_ELECTION=1 and ARIA_ROLE is not
    explicitly pinned. FAIL-SAFE: any error → 'all' (run the singletons) so a
    claim bug can never leave the engine unowned — better N engines than zero.
    Failover is automatic: the elected engine heartbeats to keep its lease; if
    it dies, uvicorn respawns the worker which re-runs this election and
    re-claims the now-expired lease."""
    global _resolved_role, _engine_lease_id
    if (_os.getenv("ARIA_ROLE") or "").strip().lower() in ("engine", "web", "all"):
        return  # explicit pin wins; nothing to elect
    if not _engine_election_enabled():
        return  # default → _aria_role() returns 'all' (unchanged)
    try:
        import uuid as _uuid
        from .intel import state_store as _ss
        _engine_lease_id = _uuid.uuid4().hex
        won = await _ss.set_if_absent(
            _ENGINE_LEASE_KEY, _engine_lease_id, ex=_engine_lease_ttl_s())
        _resolved_role = "engine" if won else "web"
        logger.info("[R-F2174] engine election: this worker is '%s' (lease=%s)",
                    _resolved_role, _engine_lease_id[:8])
    except Exception as e:
        # FAIL-SAFE: run singletons rather than risk an unowned engine.
        _resolved_role = "all"
        logger.warning(
            "[R-F2174] engine election failed (%s) — falling back to ALL "
            "(this worker WILL run singletons; safe but may N× if multi-worker)", e)


async def _engine_heartbeat_loop() -> None:
    """R-F2174 — renew the engine lease while this engine worker is alive, so a
    respawning sibling never steals it. Started only on the elected engine."""
    from .intel import state_store as _ss
    interval = max(3, _engine_lease_ttl_s() // 3)
    while True:
        try:
            await asyncio.sleep(interval)
            if _engine_lease_id is None:
                continue
            renewed = await _ss.renew_lease(
                _ENGINE_LEASE_KEY, _engine_lease_id, ex=_engine_lease_ttl_s())
            if not renewed:
                # Lost the lease (expired + taken over). Stay engine for THIS
                # process (we already started singletons); log so it's visible.
                logger.warning("[R-F2174] engine lease lost on renew — another "
                               "worker may have claimed engine; investigate if persistent")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug("[R-F2174] engine heartbeat tick failed: %s", e)


async def _bg_supervisor_loop() -> None:
    """R-F1610 — the self-healing ACTUATOR. Periodically checks every
    respawn-registered bg loop; if its task is no longer live (died/GC'd), it
    RE-SPAWNS it (bounded by _BG_MAX_RESPAWNS) and records the event to the
    brain. This is the piece that makes self_healing ACT, not just log — the
    operator's core gap: 'she detects but doesn't heal'. A loop that is merely
    sleeping (not done) is still 'live', so normal long-cycle loops are left
    alone; only genuinely-dead ones are revived."""
    await asyncio.sleep(180)  # let the initial loops settle past startup delays
    while True:
        try:
            await _bg_supervisor_tick()
        except Exception as _sup_err:
            logger.error("[R-F1610] bg_supervisor error (non-fatal): %s", _sup_err)
            try:  # R-F2256 §21a — the supervisor respawns dead loops; its own failure must not be dark
                from .intel.engine_wiring import wire_failure
                wire_failure(module="bg_supervisor", detail=f"bg_supervisor error: {str(_sup_err)[:160]}",
                             gap_type="engine_failure", source="main:_bg_supervisor_loop")
            except Exception:
                pass
        await asyncio.sleep(180)


async def _bg_supervisor_tick() -> list[str]:
    """R-F1610 — one supervisor pass: re-spawn any registered bg loop whose
    task is no longer live (died), bounded by _BG_MAX_RESPAWNS. Returns the
    names re-spawned this tick (for tests/observability). A loop that is merely
    sleeping is `not done()` → 'live' → left alone."""
    respawned: list[str] = []
    live = {t.get_name() for t in _BG_TASKS if not t.done()}
    # R-F1769 — VERIFY last tick's re-spawns: a re-spawned loop still live a full
    # supervisor interval later has genuinely recovered → wire the TRUTHFUL
    # success. One that died again is NOT verified (it gets re-spawned below, and
    # the bounded counter escalates to operator if it keeps crashing). This is
    # act+VERIFY — we never claim a heal worked until it provably survived.
    for _pn in list(_BG_RESPAWN_PENDING):
        if _pn in live:
            _BG_RESPAWN_PENDING.discard(_pn)
            try:
                from .intel import brain_hook as _bh
                _BG_TASKS.add(asyncio.create_task(_bh.absorb(
                    module="bg_supervisor",
                    summary=f"self-heal VERIFIED: re-spawned loop {_pn} survived an interval (alive)",
                    success=True, confidence="CONFIRMED",
                )))
            except Exception:
                pass
        # else: still dead — leave in pending; the re-spawn loop below re-acts.
    for _nm, _factory in list(_BG_RESPAWN.items()):
        if _nm in live:
            continue
        n = _BG_RESPAWN_COUNT.get(_nm, 0)
        if n < _BG_MAX_RESPAWNS:
            _BG_RESPAWN_COUNT[_nm] = n + 1
            logger.warning(
                "[R-F1610] bg_supervisor: loop %s is dead — re-spawning (%d/%d)",
                _nm, n + 1, _BG_MAX_RESPAWNS,
            )
            _bg_task(asyncio.create_task(_factory(), name=_nm), factory=_factory)
            respawned.append(_nm)
            _BG_RESPAWN_PENDING.add(_nm)  # R-F1769: verify survival next tick
            try:
                # R-F1769 — HONEST: re-spawn ATTEMPTED, not yet verified alive.
                from .intel import brain_hook as _bh
                _BG_TASKS.add(asyncio.create_task(_bh.absorb(
                    module="bg_supervisor",
                    summary=f"self-heal: re-spawn ATTEMPTED for dead loop {_nm} "
                            f"({n + 1}/{_BG_MAX_RESPAWNS}) — verifying survival next tick",
                    success=True, confidence="PROBABLE",
                )))
            except Exception:
                pass
        elif n == _BG_MAX_RESPAWNS:
            _BG_RESPAWN_COUNT[_nm] = n + 1  # latch so we alert once
            logger.error(
                "[R-F1610] bg_supervisor: loop %s exceeded %d respawns — "
                "NEEDS OPERATOR (a real crash, not GC)", _nm, _BG_MAX_RESPAWNS,
            )
    return respawned


async def _record_deploy_event() -> dict:
    """R-F1612 — record this boot/build event to the brain so ARIA has a
    persistent, RAG-queryable record of what she shipped (not just the live
    value). Returns the entry (for tests). Fire-and-forget safe — never raises;
    each sink (deploy-history key, brain absorb) is independently guarded."""
    import json as _j
    iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    entry = {"build_rev": ARIA_BUILD_REV, "booted_at": iso}
    try:
        from .intel import redis_store as _rs
        await _rs.lpush("crucix:aria:deploy:history", _j.dumps(entry))
        await _rs.ltrim("crucix:aria:deploy:history", 0, 49)  # keep last 50
    except Exception as _e:
        logger.debug("[R-F1612] deploy-history write skipped: %s", _e)
    try:
        from .intel import brain_hook as _bh
        await _bh.absorb(
            module="deploy",
            summary=f"ARIA booted on build_rev {ARIA_BUILD_REV} at {iso}",
            detail="deploy proprioception (R-F1612)",
            success=True, confidence="CONFIRMED",
        )
    except Exception as _e:
        logger.debug("[R-F1612] deploy-event brain absorb skipped: %s", _e)
    return entry


async def _run_boot_inits(inits) -> list:
    """R-F1421/R-F2378 — run boot init functions, isolating failures.

    Pre-R-F1421 the intel inits were bare `await x.init()` with no guard: one
    throw made the lifespan raise → uvicorn never reached `yield` → the app
    never served → TOTAL OUTAGE (the 2026-04-27 F28 class). A degraded-but-up
    ARIA that surfaces which subsystem failed beats a fully-dark one.

    R-F2378: run in parallel with a per-init timeout. Serial unbounded inits
    made one slow state-store read (e.g. competitors.get burning its full 5s)
    hold the entire pre-yield path hostage. Returned failure names preserve
    input order for callers/tests.
    """
    timeout_s = max(0.25, _env_float("ARIA_BOOT_INIT_TIMEOUT_S", 5.0))

    async def _one(name, fn):
        try:
            await asyncio.wait_for(fn(), timeout=timeout_s)
            return None
        except asyncio.TimeoutError:
            try:
                logger.error(
                    "[R-F2378] intel init '%s' timed out after %.1fs "
                    "(degrading, staying up)",
                    name, timeout_s,
                )
            except Exception:
                pass
            return name
        except Exception as e:  # noqa: BLE001 — isolate per-subsystem
            try:
                logger.error(
                    "[R-F1421] intel init '%s' FAILED at boot (degrading, "
                    "staying up): %s", name, e, exc_info=True,
                )
            except Exception:
                pass
            return name

    results = await asyncio.gather(*[_one(name, fn) for name, fn in inits])
    return [name for name in results if name]


async def _expiry_sweeper_loop() -> None:
    """R-F2154: background loop that sweeps expired state_store entries.

    The state_store.sweep_expired() function was defined but NEVER wired into
    the lifespan, so expired entries (cost records with 90-day TTL, etc.)
    accumulated forever — 154K cost records + 102K verified facts contributed
    to a 1 GB DB that made boot-time reads hang for 40+ minutes.
    Runs every 300s (5 min) to keep the DB lean.
    """
    # R-F2219: engine SINGLETON — the state_store DB is shared per-machine, so
    # one sweeper suffices; N sweepers on N workers just N× the DELETE load on
    # an already-saturation-sensitive store. Started before the election, so
    # wait for it to resolve, then exit on non-engine roles.
    if _election_complete is not None:
        await _election_complete.wait()
    if not _runs_singletons():
        logger.info("[R-F2073] expiry_sweeper SKIPPED (ARIA_ROLE=%s)", _aria_role())
        return
    while True:
        try:
            from .intel import state_store as _ss
            _deleted = await _ss.sweep_expired()
            if _deleted:
                logger.info("[R-F2154] state_store sweep: removed %d expired entries", _deleted)
        except Exception as _sw_e:
            logger.debug("[R-F2154] state_store sweep skipped: %s", _sw_e)
            try:  # R-F2256 §21a — surface sweep failures to the brain (was dark)
                from .intel.engine_wiring import wire_failure
                wire_failure(module="expiry_sweeper", detail=f"state_store sweep error: {str(_sw_e)[:160]}",
                             gap_type="engine_failure", source="main:_expiry_sweeper_loop")
            except Exception:
                pass
        await asyncio.sleep(300)


def _dd_reconcile_enabled() -> bool:
    """R-F3524 — the DD reconcile loop had NO kill switch, and that was the defect.

    THE INCIDENT (2026-07-30). aria-intel entered a SIGSEGV crash-loop (exit_code=139,
    ~70s period). `reconcile_pending_adverse_media` exists precisely to "re-launch
    adverse-media follow-ups orphaned by a restart" (R-F2941), and
    `reconcile_stale_running_dds` re-launches restart-killed DDs (R-F2300) — so every
    crash re-armed the deep-DD work that was running when the box went down. The last
    log line before a crash was a web search for an officer of a subject whose DD had
    already been killed twice.

    Whether the DD load CAUSED the segfault was not established. What was established is
    that the operator asked to pause DD relaunches and **there was no way to do it**:
    `_dd_reconcile_loop` checked only the singleton role, and the only levers were
    `ARIA_ROLE` (far too broad — it disables every singleton) or a redeploy. The
    autonomous engine has had a master switch since R-F276; this loop, which can generate
    just as much production load, had none.

    THE PROPERTY: a subsystem that can generate production load must be pausable WITHOUT
    a deploy and WITHOUT the app being healthy. Hence an env var read on EVERY iteration
    — not captured once at startup — so `flyctl secrets set` takes effect at the next
    boot even while the box is crash-looping, which is exactly when it is needed.

    Defaults to ENABLED so this is byte-equivalent to prior behaviour unless deliberately
    turned off. Turning it off is not free: orphaned `status='running'` DDs stop being
    cleared (R-F2300's 12.5h chat-hang), so it is an incident lever, not a setting.
    """
    # `_os`, not `os`: this module imports `os as _os` (main.py:16) and nothing binds a
    # bare `os`. py_compile cannot see the difference — it would have been a NameError
    # on the first iteration, disabling the very switch this adds.
    return str(_os.getenv("ARIA_DD_RECONCILE_ENABLED", "1")).strip().lower() not in (
        "0", "false", "no", "off")


async def _dd_reconcile_once() -> None:
    """R-F2568 — ONE dd-reconcile pass with §21d failure-wiring. Extracted to module
    level so the (previously DARK) failure branch is capability-testable.

    reconcile_stale_running_dds is the ONLY thing that clears orphaned status='running'
    DDs after a restart/wedge (R-F2300, the 12.5h chat-hang). Its failure mode is exactly
    the R-F2277 state_store wedge — so it goes blind precisely when it's needed. Wire the
    failure to the brain so the self-heal loop can act instead of user DDs silently hanging."""
    try:
        from .intel import dd_orchestrator as _ddo
        _rec = await _ddo.reconcile_stale_running_dds()
        # R-F2941 — same pass re-launches adverse-media follow-ups orphaned by a
        # restart, so the Grade-A adverse-media question self-heals instead of
        # hanging at status=in_progress forever.
        _am_rec = await _ddo.reconcile_pending_adverse_media()
        # ── R-F3288 — WIRE THE SUCCESS BRANCH TOO ────────────────────────────
        #
        # R-F2568 wired the failure and stopped there, so only half of §21a was
        # satisfied ("emits on BOTH the success and the failure branch"). This
        # reconcile counts exactly what it did, logs it, and returned a dict that
        # this line used to DISCARD — so a pass that re-launched five
        # restart-killed DDs was indistinguishable from one that found nothing.
        #
        # That is the blind spot that matters: this is the only thing clearing
        # orphaned 'running' DDs after a restart, so if it quietly stops resuming,
        # the first evidence is a user's DD hanging forever, which is the R-F2300
        # failure it exists to prevent.
        #
        # Signal only when there was WORK. A heartbeat on every 600s pass would
        # bury the real events in noise, which is its own kind of dark.
        try:
            _resumed = int((_rec or {}).get("resumed") or 0)
            _cleared = int((_rec or {}).get("reconciled") or 0)
            _relaunched = int((_am_rec or {}).get("relaunched") or 0)
            if _resumed or _cleared or _relaunched:
                from .intel.engine_wiring import wire_success
                wire_success(
                    module="dd_reconcile",
                    summary=(f"dd reconcile: resumed {_resumed} restart-killed DD(s), "
                             f"cleared {_cleared} orphan(s), relaunched "
                             f"{_relaunched} adverse-media follow-up(s)"),
                    source_id="main:_dd_reconcile_once:R-F3288",
                )
        except Exception:  # noqa: BLE001 — reporting must never break the loop
            pass
    except Exception as _e:  # noqa: BLE001 — best-effort, never crash the loop
        logger.debug("[R-F2300] dd reconcile error: %s", _e)
        try:  # R-F2568 §21d — surface reconcile failures to the brain (was dark)
            from .intel.engine_wiring import wire_failure
            wire_failure(module="dd_reconcile", detail=f"dd reconcile error: {str(_e)[:160]}",
                         gap_type="engine_failure", source="main:_dd_reconcile_once")
        except Exception:
            pass


async def _outcome_reconcile_once() -> None:
    """R-F2568 — ONE outcome-reconcile pass (§25 silent-drop backstop) with §21d
    per-surface failure-wiring (was DARK). A surface whose delivery backstop keeps
    failing is a real 'did-I-deliver?' blindspot — surface it as a gap so self-heal fires."""
    from .intel import outcome_wire as _ow
    for _surface in _ow.KNOWN_SURFACES:
        try:
            await _ow.reconcile_silent_drops(_surface)
        except Exception as _e2:  # per-surface best-effort
            logger.debug("[R-F2376] outcome reconcile(%s) error: %s", _surface, _e2)
            try:  # R-F2568 §21d — surface the failing backstop to the brain (was dark)
                from .intel.engine_wiring import wire_failure
                wire_failure(module="outcome_reconcile",
                             detail=f"outcome reconcile({_surface}) error: {str(_e2)[:140]}",
                             gap_type="engine_failure", source="main:_outcome_reconcile_once")
            except Exception:
                pass


async def _sanctions_refresh_once() -> dict:
    """R-F2572 — refresh the canonical sanctions store IF it is stale. Downloads the OFAC
    SDN Enhanced XML + EU consolidated CSV and loads them into the canonical store — the
    pipeline the DAILY-SANCTIONS-REFRESH tasks.yaml task tried to run via `tool: shell`,
    which the autonomous engine has no handler for (a silent no-op → 58-day-stale store on
    2026-07-12). Staleness-gated (skips a fresh store so deploys don't re-download the
    108MB feed), runs off the event loop, §21-wired. The R-F2570 drift floor protects the
    store if a feed's format ever breaks the parser (refuses to overwrite good data)."""
    try:
        from .intel.sanctions_canonical import store as _ss
        # R-F3264 — OFF THE LOOP, like the refresh it guards.
        #
        # This was called inline while the docstring above already claimed the
        # refresh "runs off the event loop". The expensive half was true —
        # `refresh_all` is wrapped below — but the cheap-LOOKING gate in front
        # of it was not, and it is a synchronous sqlite3 MAX() over the
        # `entries` table. A live R-F704 wedge stack caught exactly this frame
        # blocking the loop, with 24,953 rows to scan.
        #
        # R-F3264 also indexes `last_refreshed` so the scan becomes a lookup.
        # Both are needed: an index alone would leave synchronous sqlite on the
        # loop, which is wrong at any speed, and `to_thread` alone would move a
        # table scan onto a worker thread and call it fixed.
        newest = await asyncio.to_thread(_ss.newest_entry_refresh)
        max_age_h = float(_os.getenv("ARIA_SANCTIONS_REFRESH_MAX_AGE_H", "20"))
        if newest is not None and (time.time() - newest) < max_age_h * 3600:
            return {"refreshed": False, "reason": "fresh",
                    "age_h": round((time.time() - newest) / 3600, 1)}
        from scripts import refresh_sanctions as _rs
        res = await asyncio.to_thread(_rs.refresh_all)
        # A drifted load returns success=True with rows_loaded=0 (R-F2570), so require BOTH.
        per_ok = {s: bool(v.get("success")) and int(v.get("rows_loaded") or 0) > 0
                  for s, v in (res or {}).items()}
        all_ok = bool(per_ok) and all(per_ok.values())
        try:
            from .intel.engine_wiring import wire_success, wire_failure
            if all_ok:
                wire_success(module="sanctions_refresh",
                             summary=f"canonical sanctions store refreshed: {per_ok}",
                             source_id="main:_sanctions_refresh_once")
            else:
                wire_failure(module="sanctions_refresh",
                             detail=f"sanctions refresh incomplete/failed: {res}",
                             gap_type="source_failure", source="main:_sanctions_refresh_once")
        except Exception:
            pass
        return {"refreshed": True, "ok": all_ok, "result": res}
    except Exception as e:
        logger.warning("[R-F2572] sanctions refresh error: %s", e)
        try:
            from .intel.engine_wiring import wire_failure
            wire_failure(module="sanctions_refresh",
                         detail=f"sanctions refresh error: {str(e)[:160]}",
                         gap_type="engine_failure", source="main:_sanctions_refresh_once")
        except Exception:
            pass
        return {"refreshed": False, "reason": f"error: {e}"}


async def _news_poll_once() -> dict:
    """R-F2584 — run news_monitor.poll_feeds() IF the Golden Intel feed is stale. poll_feeds
    refreshes the signal store + runs the promotion bridge that feed BOTH the Telegram Golden
    Intel channel AND the dashboard 'Distribution Ready' column. The HOURLY-NEWS-MONITOR
    autonomous task stopped firing (2026-07-12: 27h stale, last_poll 2026-07-11T20:14) → the
    gate correctly skipped stale signals → the channel went silent. This first-class loop makes
    the poll reliable regardless of the autonomous scheduler (same fix pattern as R-F2572).
    Staleness-gated so it doesn't re-poll a fresh feed; poll_feeds is async (~250s) and yields
    on feed I/O; §21-wired."""
    try:
        from .intel import news_monitor as _nm
        st = await _nm._read_poll_state()
        last = (st or {}).get("last_poll_at")
        max_age = float(_os.getenv("ARIA_NEWS_POLL_MAX_AGE_S", "3000"))  # ~50min
        stale = True
        if last:
            try:
                import datetime as _dt
                lp = _dt.datetime.fromisoformat(str(last).replace("Z", "+00:00")).timestamp()
                stale = (time.time() - lp) > max_age
            except Exception:
                stale = True
        if not stale:
            return {"polled": False, "reason": "fresh"}
        timeout = float(_os.getenv("ARIA_NEWS_POLL_TIMEOUT_S", "330"))
        res = await asyncio.wait_for(_nm.poll_feeds(), timeout=timeout)
        try:
            from .intel.engine_wiring import wire_success
            wire_success(module="news_poll",
                         summary=f"Golden Intel news poll refreshed: {str(res)[:120]}",
                         source_id="main:_news_poll_once")
        except Exception:
            pass
        return {"polled": True, "result": res}
    except Exception as e:
        logger.warning("[R-F2584] news poll error: %s", e)
        try:
            from .intel.engine_wiring import wire_failure
            wire_failure(module="news_poll", detail=f"news poll error: {str(e)[:160]}",
                         gap_type="engine_failure", source="main:_news_poll_once")
        except Exception:
            pass
        return {"polled": False, "reason": f"error: {e}"}


async def await_llm_provider(
    app, timeout_s: float = 600.0, poll_s: float = 2.0,
) -> float:
    """R-F2901 — block until app.state.llm_provider is configured.

    Returns the seconds waited (0.0 if it was ready immediately). Never raises;
    on timeout it returns the elapsed time and lets the caller act on a still-
    unconfigured provider, so the existing capability-gap path still fires.

    Why: the autonomous-engine bootstrap and _init_llm_and_dialogue_bg (which
    ASSIGNS app.state.llm_provider) are both background tasks created by
    _bg_task with NO ordering between them. start_engine() hard-refuses when the
    provider is unconfigured and nothing retries, so losing that race left the
    autonomous loop silently dark until the next restart — observed live on the
    2026-07-23 Claude-flip restart (engine checked 12:10:48, chain assigned
    12:10:49, engine never started). Same outcome as the R-F2004 outage where a
    dropped master flag killed the metabolism for 187h, reached by a different
    route, so §1 requires the structural fix rather than a nudge.

    The default bound is generous on purpose: a cold boot loads ~223k facts and
    ~1.2M edges before the LLM init task is even scheduled (§11c).
    """
    waited = 0.0
    while waited < timeout_s:
        provider = getattr(getattr(app, "state", None), "llm_provider", None)
        if provider is not None and getattr(provider, "is_configured", False):
            return waited
        await asyncio.sleep(poll_s)
        waited += poll_s
    return waited


def _should_force_restart(
    stale_s: float, armed: bool, enabled: bool, ceiling_s: float
) -> bool:
    """R-F1417 — decide whether the off-loop wedge watchdog should force a
    process exit (so Fly cold-boots the machine and ARIA self-recovers).

    Pure + module-level so the dangerous os._exit it gates is unit-testable.
    Returns True ONLY when self-restart is enabled, the detector is armed
    (i.e. past the cold-boot settle window — never fires during boot), and
    the heartbeat has been stale past the hard ceiling (genuinely wedged,
    not a legitimate slow op).
    """
    try:
        return bool(enabled and armed and float(stale_s) > float(ceiling_s))
    except (TypeError, ValueError):
        return False


#: How stale the R-F2849 lag gauge may get before the FEED itself is suspect.
#: The monitor samples once a second from a task running ON the event loop, so
#: a gap this large means the loop stopped turning — the gauge's own silence is
#: the signal. Generous vs the 1 s interval so ordinary scheduling jitter, a GC
#: pause or a slow health call never trips it.
LOOP_MONITOR_STALE_S = 60.0


def _seed_ingested_something(result) -> bool | None:
    """R-F4262 (dossier E2) — did a knowledge seed actually ingest anything?

    True / False when the seeder REPORTS a count, and **None when it did not
    say** — which the caller must not treat as success. That is the whole
    defect: `ingest_all_sections()` catches every per-section exception,
    returns `{"sections_ingested": 0}` without raising, and the caller read
    "no exception" as success and stamped a **30-day** skip hash. A module
    whose ingest failed completely was then skipped for the next month, and DD
    Layer 4c went on stamping `source: "RAG:regional_compliance"` on report
    content attributed to a store that may never have been filled.

    Shape-agnostic on purpose. Thirteen seeders report `sections_ingested`;
    `dd_case_library` reports `cases_ingested`. Keying on one literal name
    would silently exempt the other, so any ``*_ingested`` integer counts and a
    future third shape is covered without another edit.

    Pure and module-level so the decision is testable without a boot.
    """
    if not isinstance(result, dict):
        return None
    counts = [v for k, v in result.items()
              if isinstance(k, str) and k.endswith("_ingested")
              and isinstance(v, bool) is False and isinstance(v, int)]
    if not counts:
        return None          # it did not say — NOT evidence that it worked
    return sum(counts) > 0


def _vendor_balance_degraded_reasons(vendor_balance) -> list[str]:
    """R-F4261 — turn the vendor-credit gauge into health VERDICT input.

    Same shape as `_loop_degraded_reasons` one function below, and the same
    lesson: `/health` published `llm_chain.vendor_balance` and never read it.
    Measured 2026-08-23: `deepseek.total_balance 7.61` against
    `warn_threshold_usd 10.0`, stamped `severity: "low"`, sitting beside
    `status: "operational"`, `degraded_reasons: []` and a self-diagnostic
    reporting 76 pass / 0 warn / 0 fail — in the SAME payload. General chat runs
    on a chain of depth 1, so exhaustion is a full chat + WhatsApp outage: the
    19-hour incident C-209 was written about, which happened at an overdraft of
    two cents while every headline field read green.

    R-F4229 built the gauge and got its tri-state right. What it did not do is
    give any verdict the power to say so. A number three levels deep that no
    verdict consumes is the C-96 shape exactly.

    Two reasons, both MEASURED, one per affected vendor so an operator knows
    WHICH account to top up:

    - ``llm_vendor_credit_exhausted_{vendor}`` — the vendor is refusing, measured
      from its own body. This is an outage cause, not a warning.
    - ``llm_vendor_credit_low_{vendor}`` — below the warn threshold and still
      serving. Deliberately degraded rather than silent: on a depth-1 chain the
      gap between "low" and "dark" is hours, and R-F4229's own default exists to
      give warning BEFORE zero.

    `unknown` is deliberately NOT a reason. It covers `unreadable` (could not
    ask), `unsupported` (Anthropic publishes no balance endpoint — by design,
    not a fault) and `never_observed` (not yet polled). Flagging it would make
    `degraded_reasons` permanently non-empty on Anthropic alone, and a verdict
    that always fires is one nobody reads — the same reasoning that keeps `busy`
    out of the loop reasons. It is also the R-F4229 doctrine: "I could not ask"
    must never render as a measurement, in EITHER direction.

    Pure and module-level so the decision is testable without standing up the
    app. Never raises: a health endpoint that 500s because its own gauge is odd
    is worse than one that reports nothing, and each vendor is read
    independently so one malformed entry cannot suppress a readable neighbour.
    """
    reasons: list[str] = []
    if not isinstance(vendor_balance, dict):
        return reasons
    for vendor, reading in sorted(vendor_balance.items()):
        try:
            if not isinstance(reading, dict):
                continue
            sev = str(reading.get("severity") or "").lower()
            name = str(vendor).strip().lower() or "unknown_vendor"
            if sev == "exhausted":
                reasons.append(f"llm_vendor_credit_exhausted_{name}")
            elif sev == "low":
                reasons.append(f"llm_vendor_credit_low_{name}")
        except Exception:      # pragma: no cover - defensive
            continue
    return reasons


def _loop_degraded_reasons(loop_health) -> list[str]:
    """R-F4024 (C-96) — turn the event-loop gauge into health VERDICT input.

    `/health` published `loop` and `degraded_reasons` in the same payload and
    never read the former. Live 2026-08-14: `loop.status: starved`, p95
    3264 ms, max 9726 ms, alongside `status: operational`, `degraded_reasons:
    []` and a GREEN self-diagnostic. The loop was blocking for up to 9.7 s and
    no verdict in the tree could say so — which is why C-95 ran for at least a
    day after `knowledge.py` recorded the same `starved` reading.

    Pure + module-level for the same reason as `_should_force_restart`: the
    decision is testable without standing up the app.

    Two reasons, both MEASURED:

    - `event_loop_starved` — the gauge's own band for "I/O callbacks are
      waiting behind CPU work". `busy` is deliberately NOT included: elevated
      but turning is normal under load, and a verdict that cries wolf is one
      nobody reads when it finally matters.
    - `event_loop_monitor_stale` — the samples stopped. The monitor runs ON the
      loop, so a wedge silences it and the gauge then serves its last healthy
      numbers indefinitely. Reading that as health is the "guard that goes
      blind rather than fails" shape (R-F3791): a frozen instrument certifying
      the very thing it stopped measuring.

    `unknown` with no samples is NOT degraded — the detector arms 120 s after
    boot by design, and flagging that would make every deploy flap.

    Never raises: a health endpoint that 500s because its own gauge is odd is
    worse than one that reports nothing. Each signal is read independently, so
    an unparseable age cannot suppress a readable `starved`.
    """
    reasons: list[str] = []
    if not isinstance(loop_health, dict):
        return reasons
    try:
        if str(loop_health.get("status") or "").lower() == "starved":
            reasons.append("event_loop_starved")
    except Exception:      # pragma: no cover - defensive
        pass
    try:
        age = loop_health.get("last_sample_age_s")
        samples = loop_health.get("samples") or 0
        # Only meaningful once the monitor has actually produced samples;
        # age is None on a fresh boot, which is not staleness.
        if int(samples) > 0 and age is not None:
            if float(age) > LOOP_MONITOR_STALE_S:
                reasons.append("event_loop_monitor_stale")
    except (TypeError, ValueError):
        pass
    return reasons

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse

from .config import settings
from .llm.factory import create_llm_provider
from .llm.fallback import create_fallback_chain
from .intel import redis_store as rs
from .intel import knowledge, intel_ledger, contacts, competitors, training_data, neural_memory
from .intel import self_improve
from .intel import student
from .intel import reasoning_library
from .intel import proactive
from .intel import rag_store
from .intel import ocr as ocr_module
# R-F2378: document_reader pulls PyMuPDF/OCR backends and can take multiple
# seconds, or worse under AV scanning, at cold import. Keep module import cheap;
# lifespan schedules a background prewarm so the first document request is still
# usually warm without blocking boot or test collection.
from .intel import cost_tracker
from .intel.researcher import research_and_learn, get_hypotheses, validate_hypothesis
from .routes.aria import router as aria_router, require_aria_token
# R-F3138 — vetting surface. Its own router (prefix /api/aria/vetting) rather
# than more lines in the 28k-line routes/aria.py; it reuses that module's
# _router_auth_dep object so the two cannot drift apart on auth.
from .routes.vetting import router as vetting_router
# R-F3180 — the portal router is UNAUTHENTICATED by design (applicant/referee
# links). Kept a separate module so "is this endpoint authenticated?" is
# answered by which file it lives in, not by reading a decorator list.
from .routes.vetting_portal import router as vetting_portal_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

# R-F2851 (2026-07-22): root logging is INFO, and httpx logs every outbound
# request at INFO with the FULL url — so any credential carried in a query
# string (e.g. tender_monitor's SAM_GOV_API_KEY) was printed in plaintext to the
# production log. Wrap every handler's formatter so credentials are redacted at
# the emit choke point, whatever call site or library produced them. Installed
# again in lifespan() once uvicorn has added its own handlers.
from .intel.log_redaction import install_log_redaction  # noqa: E402

install_log_redaction()

logger = logging.getLogger("aria.main")

# R-F513 (2026-05-14): auto-derive build_rev from Dockerfile ARGs that
# are passed by deploy-fly.yml at build time. Pre-R-F513 ARIA_BUILD_REV
# was a hand-edited string that drifted — 27 commits on 2026-05-14
# shipped without a bump, so /health/live reported R-F422 while
# R-F509/F510 were live. Verify-after-fix was unreliable as a result.
#
# Env vars (set in aria_service/Dockerfile via --build-arg):
#   ARIA_BUILD_GIT_SHA — git SHA at build (e.g. "a698a4f...")
#   ARIA_BUILD_R_TAG   — most recent R-number(s) (e.g. "R-F511+F512+F513")
#
# Pass these on the CLI:
#   flyctl deploy --remote-only --build-arg ARIA_BUILD_GIT_SHA=$(git rev-parse HEAD) \
#                 --build-arg ARIA_BUILD_R_TAG="R-F513"
# CI already does this in .github/workflows/deploy-fly.yml.
#
# If neither env var is present (local dev, image rebuilt without
# args), fall back to a clear sentinel that lights up obviously in
# logs and /health so we know to redeploy with the args.
_BUILD_GIT_SHA = _os.environ.get("ARIA_BUILD_GIT_SHA", "").strip()
_BUILD_R_TAG = _os.environ.get("ARIA_BUILD_R_TAG", "").strip()

# R-F1539: boot-time secret self-audit registry. Maps env-var names to
# a human-readable hint about expected format. The audit runs 3s after
# boot and warns if any value looks malformed (CLI flags leaked in, etc).
_SECRET_AUDIT: dict[str, str] = {
    "ARIA_RAG_BACKFILL_DISABLED": "expected true/false/1/0",
    "ARIA_INTERNAL_TOKEN": "expected a hex token (32+ chars)",
    "ARIA_AUDIT_SIGNING_KEY": "expected a hex key (32+ chars)",
    "REPORT_SIGNING_KEY": "expected a hex key (32+ chars)",
}


def _resolve_git_head_from_image(git_dir: str = "/app/.git") -> str:
    """R-F589 (2026-05-16) — runtime build_rev fallback.

    Pre-R-F589 the only path to build_rev was the --build-arg passed at
    docker build time. Manual `flyctl deploy` invocations (no wrapper)
    skipped the flag, so the Dockerfile ARG defaulted to "unknown" and
    /api/aria/health.build_rev reported UNKNOWN-BUILD even though the
    code was live.

    R-F589 bakes .git/HEAD + .git/refs/ into the image (via
    .dockerignore exceptions + COPY in the Dockerfile) so we can
    resolve HEAD at runtime regardless of how the deploy was invoked.
    Returns the resolved 40-char SHA, or "" if anything is missing.

    Pure-Python git ref resolution — no `git` binary needed in the
    image. Handles three HEAD shapes:
      - "ref: refs/heads/main"  → reads refs/heads/main
      - "ref: refs/heads/feature/foo" → reads refs/heads/feature/foo
      - "<40-char-sha>"         → detached HEAD; return SHA directly
    Also handles packed-refs fallback (long-lived clones with many tags).
    """
    try:
        head_path = _os.path.join(git_dir, "HEAD")
        if not _os.path.isfile(head_path):
            return ""
        with open(head_path, "r", encoding="utf-8") as f:
            head = f.read().strip()
        if not head.startswith("ref:"):
            # Detached HEAD — first 40 chars are the SHA
            return head[:40] if len(head) >= 40 else head
        ref_path = head[len("ref:"):].strip()
        ref_file = _os.path.join(git_dir, ref_path)
        if _os.path.isfile(ref_file):
            with open(ref_file, "r", encoding="utf-8") as f:
                return f.read().strip()[:40]
        # Fallback to packed-refs (long-lived clones often pack refs)
        packed = _os.path.join(git_dir, "packed-refs")
        if _os.path.isfile(packed):
            with open(packed, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("#") or line.startswith("^"):
                        continue
                    parts = line.strip().split(" ", 1)
                    if len(parts) == 2 and parts[1] == ref_path:
                        return parts[0][:40]
        return ""
    except Exception:
        return ""


if _BUILD_GIT_SHA and _BUILD_GIT_SHA != "unknown":
    _sha_short = _BUILD_GIT_SHA[:8]
    _source = "build-arg"
elif (_runtime_sha := _resolve_git_head_from_image()):
    # R-F589 fallback — operator skipped --build-arg, but .git/HEAD is
    # in the image so we still know which commit is running.
    _BUILD_GIT_SHA = _runtime_sha
    _sha_short = _runtime_sha[:8]
    _source = "git-head-runtime"
else:
    _sha_short = ""
    _source = "unknown"

# R-F920 (2026-05-26) — build-source is tracked SEPARATELY from the
# user-facing build_rev string. Per CLAUDE.md §14 (fallback transparency): a
# runtime-resolved SHA is the CORRECT commit and IS working, so the user-facing
# footer must read cleanly ("sha 3a9139f") — not "· R-F589 runtime fallback
# (build-arg missing)", which leaked an internal deploy detail into every
# WhatsApp answer (live 2026-05-26) and read as if ARIA were broken. The
# build-arg-skipped signal still reaches operators via the startup log and the
# ARIA_BUILD_SOURCE field on /api/aria/health — just not the customer footer.
ARIA_BUILD_SOURCE = _source
if _sha_short:
    if _BUILD_R_TAG:
        ARIA_BUILD_REV = f"{_BUILD_R_TAG} · sha {_sha_short}"
    else:
        ARIA_BUILD_REV = f"sha {_sha_short}"
else:
    # Build-arg AND .git/HEAD both missing — final fallback string.
    # Operator should run `scripts/fly_deploy.sh` or pass --build-arg
    # explicitly so the metadata reflects the running commit.
    ARIA_BUILD_REV = "UNKNOWN-BUILD · ARIA_BUILD_GIT_SHA not set at image build (pass --build-arg)"


async def _delayed_auto_register(auto_reg_fn, delay_s: int = 120) -> None:
    """R-F1444: fire-and-forget auto-registration after boot settles.

    Waits `delay_s` seconds for the app to finish booting (RAG store,
    neural memory, agent registry), then runs auto_register_all for
    every pending portal. Failures are logged but never crash boot.
    """
    try:
        await asyncio.sleep(delay_s)
        result = await auto_reg_fn()
        total = result.get("total", 0)
        registered = result.get("newly_registered", 0)
        captcha = result.get("captcha_deferred", 0)
        failed = result.get("failed", 0)
        skipped = result.get("skipped_open", 0)
        if registered > 0 or captcha > 0 or failed > 0:
            logger.info(
                "[R-F1444] Auto-registration complete: %d total, "
                "%d newly registered, %d captcha-deferred, %d failed, %d open/skipped",
                total, registered, captcha, failed, skipped,
            )
        else:
            logger.debug(
                "[R-F1444] Auto-registration: %d portals already processed",
                total,
            )
    except Exception as e:
        logger.warning("[R-F1444] Auto-registration failed (non-fatal): %s", e)


def _freeze_long_lived_state() -> int:
    """R-F1621 — move the boot-loaded, long-lived graphs (knowledge ~87k facts,
    neural_memory, intel_ledger) into CPython's permanent generation so the
    cyclic GC never scans them again.

    Why: the recurring 5-16s event-loop wedge (wedge_674) is the knowledge
    json.dump holding the GIL while gen2 GC repeatedly re-scans the huge
    never-deleted graph during serialisation. §7 makes these objects genuinely
    permanent (no TTL, no eviction, never deleted), so freezing them is exactly
    correct — they will never be collected anyway. This is the boot half; the
    dump half (gc.disable around the json.dump) lives in knowledge._write_to_disk_atomic.

    Idempotent and never fatal — a GC bookkeeping call that must not break boot.
    Returns the permanent-generation object count for observability/tests."""
    import gc
    try:
        gc.collect()        # promote/settle boot objects first
        gc.freeze()         # move everything currently alive to the permanent gen
        frozen = gc.get_freeze_count()
        logger.info("[R-F1621] gc.freeze() — %d long-lived objects moved out of GC scan set", frozen)
        return frozen
    except Exception as e:  # pragma: no cover — defensive; never break boot
        logger.warning("[R-F1621] gc.freeze() skipped (non-fatal): %s", e)
        return 0


# R-F1845 / R-F3467 — modules whose FIRST lazy import is heavy enough to stall the
# event loop, pre-warmed in a thread at boot so the later in-request import is a
# cache hit. Module-level (not a literal buried in lifespan) so a guard test can
# assert membership without booting the app.
#
# R-F3467 (2026-07-30): playwright added on LIVE evidence. The R-F3464 stall
# attribution — the first stall report that names the loop thread rather than a
# census of sleeping threads — caught this within minutes of deploy:
#   last_stall_loop_stack: ["<frozen importlib._bootstrap_external>:get_data:1214",
#                           ... "playwright/_impl/_locator.py:<module>:43"]
#   last_stall_threads: {"total": 23, "aiosqlite_workers": 9}
# An application frame on the loop thread means something BLOCKED it (main.py's own
# rule below). `from playwright.async_api import async_playwright` is lazy in four
# call sites (headless.py, scraper/playwright_engine.py x2, scraper/
# procurement_adapters.py), so whichever runs first pays a multi-second synchronous
# disk read + module exec ON the loop. The thread census in the same report also
# clears the standing GIL-starvation theory for this stall: 9 aiosqlite workers
# against R-F3252's 56 (peak 140), so R-F2754's leak fix is holding.
_HEAVY_PREWARM_MODULES: tuple[str, ...] = (
    "aria_service.writers.procurement_paper_writer",
    "playwright.async_api",
)


# ── R-F4213 / C-192 — the heavy-graph barrier must ALWAYS open ───────────────
# R-F4211 put SEVEN boot workloads behind one asyncio.Event: the autonomous
# engine, ARIA-Coder, the knowledge seed, the web-integrity agent, the defence
# seed, the health precompute loop and the entire boot continuation. That is
# ARIA's whole metabolism — the self-improvement loop §21c requires stay enabled
# and draining. It had no failsafe on either side: the producer's single .set()
# was the tail statement of an unguarded coroutine, and this consumer waited on
# it forever. Any escape above that .set() parked all seven PERMANENTLY while
# /health still reported `operational` and HTTP served normally — a dark
# metabolism is indistinguishable from a healthy one from the outside.
_HEAVY_WARMUP_TIMEOUT_DEFAULT_S = 1200.0
# Margin over the warmup cap. The two graph loads run concurrently under
# asyncio.gather, so the warmup's own worst case is ~one cap plus the
# freeze-out-of-GC pass; this is head-room on top of that, not a second budget.
_HEAVY_BARRIER_MARGIN_S = 300.0
_HEAVY_BARRIER_TIMEOUTS = 0
_HEAVY_BARRIER_ANNOUNCED = False


def _heavy_warmup_timeout_s() -> float:
    """ONE source of truth for the heavy-graph warmup cap.

    This used to be an inline `float(_os.getenv(...))` INSIDE the warmup and
    ABOVE its only `heavy_graph_ready.set()`. A malformed operator value —
    "20m", "1200s", a stray space — raised ValueError there, the barrier never
    opened, and ARIA's entire metabolism went dark until someone redeployed.
    A cap is a tuning knob; it must never be able to disable self-improvement.
    Malformed input degrades to the default and says so, rather than raising.
    """
    # R-F4215: one parser per file. This used to warn and fall back itself,
    # which was correct but was a SECOND mechanism alongside _env_float — the
    # forked-measure shape R-F2639 records ("there is ONE measure now").
    return max(60.0, _env_float("ARIA_HEAVY_WARMUP_TIMEOUT_S",
                                _HEAVY_WARMUP_TIMEOUT_DEFAULT_S))


def _heavy_barrier_timeout_s() -> float:
    """How long a gated workload waits before running anyway.

    DERIVED from the warmup cap, never hardcoded: an operator who lengthens the
    warmup would otherwise silently push the barrier into releasing early — the
    stale-hand-maintained-constant shape §27d exists to prevent.
    """
    return _heavy_warmup_timeout_s() + _HEAVY_BARRIER_MARGIN_S


async def _await_heavy_graph_ready(app: FastAPI) -> None:
    """Wait until boot graph hydration finishes before starting heavy agents.

    BOUNDED. If hydration has not signalled by the cap, the workload is released
    anyway and the fact is logged. Running late and contended is a degradation;
    never running at all is a silent capability loss, and §21c makes keeping this
    loop draining a P0. The wait is NOT the safety property here — the barrier
    only sequences non-critical work away from hydration (R-F4211), so releasing
    late is exactly the right failure direction.
    """
    global _HEAVY_BARRIER_TIMEOUTS, _HEAVY_BARRIER_ANNOUNCED
    ready = getattr(app.state, "heavy_graph_ready", None)
    if ready is None:
        return
    cap = _heavy_barrier_timeout_s()
    try:
        await asyncio.wait_for(ready.wait(), timeout=cap)
    except asyncio.TimeoutError:
        _HEAVY_BARRIER_TIMEOUTS += 1
        if not _HEAVY_BARRIER_ANNOUNCED:
            _HEAVY_BARRIER_ANNOUNCED = True
            # WARNING, never ERROR: this is a recoverable degradation and
            # `is_reset_type` excludes log:warning, so it must not reset the
            # Phase A gate-#3 streak (R-F2663). The error_log_handler mirror
            # carries it to the brain, so this failure branch is wired (§21a).
            logger.warning(
                "[R-F4213] heavy-graph barrier still closed after %.0fs — "
                "releasing gated boot workloads anyway (autonomous engine, "
                "coder, seeds, web-integrity, health precompute). Hydration "
                "did not signal; running degraded beats not running.", cap,
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────
    # R-F2158: bind teardown-referenced startup locals at the TOP of the
    # function (before any conditional/early-exit startup path), so the
    # shutdown section's `if _llm_health_checker is not None` can never raise
    # UnboundLocalError. The resilience init (which assigns the real value) is
    # gated behind `if _llm:` — when an LLM provider isn't configured that block
    # is skipped, leaving the name unbound. The misspelled shutdown reference
    # (`llm_health_checker`, no underscore) used to NameError on EVERY shutdown,
    # aborting teardown before the knowledge-flush (F94) + search-WAL flush
    # (R-F504) → unclean shutdown → bloated WAL (root of the R-F2116/2137/2154
    # state_store boot/timeout chain).
    _llm_health_checker = None
    # ── R-F3715 — BOUND the default thread executor ─────────────────────────
    #
    # THE DEFECT: nothing in the tree ever called `set_default_executor`, so all
    # 328 `asyncio.to_thread(...)` call sites shared CPython's default pool,
    # sized `min(32, os.cpu_count() + 4)`. On Fly `os.cpu_count()` reports the
    # HOST's cores, not the machine's share — so a 1-vCPU machine happily sized a
    # 32-worker pool and then thrashed it. Live evidence: the heartbeat's stall
    # dumps showed a bare `asyncio.runners.run` on the loop thread (the R-F704
    # discriminator for STARVATION, not blocking) with 32-33 threads alive and
    # `pool_workers` climbing 9 -> 11 -> 13 over 35 minutes.
    #
    # `redis_store.get_json/set_json` put EVERY call on this pool (R-F2108, for
    # 50k-entry blobs), so the common small-payload case is thousands of
    # dispatches a minute onto a pool competing with the loop for one core.
    #
    # Sized from the REAL budget, not the host: ARIA_THREAD_POOL_WORKERS, else
    # a conservative 8. This does not make anything slower — a pool wider than
    # the core count cannot execute more work, it just adds context switching
    # and GIL contention to the thing the loop is trying to share.
    try:
        import asyncio as _aio_boot
        from concurrent.futures import ThreadPoolExecutor as _TPE_boot
        # R-F3798 — `_os`, not `os`. main.py binds the module ONLY as `os as _os`
        # (line 16), so both calls here raised NameError, the `except` below caught
        # it, and R-F3715 has never once applied: the default executor stayed at
        # Python's `min(32, cpu_count() + 4)` — sized from the HOST's cores on fly,
        # which is the exact 32-worker thrash R-F3715 was written to prevent.
        # Runtime-proven 2026-08-09 by driving lifespan():
        #   "[R-F3715] could not bound the default executor: name 'os' is not defined"
        # It read as a benign tuning-knob warning, which is why it survived.
        _pool_workers = max(2, int(_os.getenv("ARIA_THREAD_POOL_WORKERS", "8") or 8))
        _aio_boot.get_running_loop().set_default_executor(
            _TPE_boot(max_workers=_pool_workers, thread_name_prefix="aria_default")
        )
        logger.info(
            "[R-F3715] default thread executor bounded at %d workers "
            "(os.cpu_count()=%s reports the HOST on fly, not this machine)",
            _pool_workers, _os.cpu_count(),
        )
    except Exception as _tpe_err:  # never block boot on a tuning knob
        logger.warning("[R-F3715] could not bound the default executor: %s", _tpe_err)
    # R-F2219: create the election-complete gate BEFORE any singleton loop is
    # scheduled (expiry_sweeper at ~790, crawler in _boot_continuation), and set
    # it right after _elect_engine_role() below.
    global _election_complete
    _election_complete = asyncio.Event()
    # R-F2851: re-install log redaction now that uvicorn has added its own
    # handlers (it configures logging when it starts the server, which is after
    # this module was imported — the import-time install alone would miss them).
    # Idempotent: already-wrapped handlers are skipped.
    try:
        _redacted_handlers = install_log_redaction()
        if _redacted_handlers:
            logger.info(
                "[R-F2851] log redaction installed on %d additional handler(s)",
                _redacted_handlers,
            )
    except Exception as _redact_exc:  # never let logging setup abort boot
        logger.warning("[R-F2851] log redaction re-install failed: %s", _redact_exc)

    logger.info("ARIA Service starting...")
    logger.info("ARIA Build: %s", ARIA_BUILD_REV)
    # R-F920 — operator-facing signal that the deploy skipped --build-arg and we
    # resolved the SHA from the in-image .git/HEAD. The user-facing footer stays
    # clean (§14); this WARNING tells operators to use scripts/fly_deploy.sh / CI.
    if ARIA_BUILD_SOURCE == "git-head-runtime":
        logger.warning(
            "ARIA Build SHA resolved at runtime from .git/HEAD (deploy skipped "
            "--build-arg ARIA_BUILD_GIT_SHA). SHA is correct; use scripts/fly_deploy.sh "
            "or CI so build metadata is passed at build time."
        )

    # R-F1845 — pre-warm heavy imports OFF the event loop. LIVE WEDGE 2026-06-23:
    # the first DD per process stalled the event loop 6-10s (R-F703 watchdog) and
    # "produced nothing". Main-thread wedge stack showed the cause: the
    # commercial-coherence layer (Layer 5c) lazily ran
    #   from ..writers.procurement_paper_writer import OFFSET_REGIMES
    # purely to read a constant dict — but that triggers writers/__init__, which
    # eager-imports the whole writers package incl. the anthropic SDK: a
    # multi-second synchronous module load ON the request loop. Warming it once at
    # boot, in a thread, makes the lazy import a cache hit so no DD ever blocks the
    # loop on it again. Guarded + fire-and-forget: never affects boot success.
    async def _prewarm_heavy_imports():
        import importlib
        for _mod in _HEAVY_PREWARM_MODULES:
            try:
                await asyncio.to_thread(importlib.import_module, _mod)
                logger.info("[R-F1845] pre-warmed %s off the event loop", _mod)
            except Exception as _pw_e:
                logger.warning("[R-F1845] pre-warm %s failed (non-fatal): %s", _mod, _pw_e)
        # R-F1846 — warm the sanctions-list source caches at boot. LIVE WEDGE
        # 2026-06-23: the first DD's identity layer fires 6 primary sources in
        # parallel; the four list-based ones download + SYNCHRONOUSLY PARSE large
        # XML datasets (fcdo/ofac/un/wb _parse_xml) — CPU/GIL-bound work that
        # starved the event loop so the DD's async per-layer timeouts could not
        # fire and the DD never completed. Each _load_records() caches for 6h, so
        # paying it here (off the request path) makes the first user DD a cache
        # hit. Sequential + guarded; never affects boot success.
        for _src_name in ("ofac_sdn", "fcdo_sanctions", "un_sc_sanctions", "worldbank_debarred"):
            try:
                _src = importlib.import_module(f"aria_service.intel.sources.{_src_name}")
                await _src._load_records()
                logger.info("[R-F1846] pre-warmed sanctions cache off the request path: %s", _src_name)
            except Exception as _sw_e:
                logger.warning("[R-F1846] pre-warm sanctions %s failed (non-fatal): %s", _src_name, _sw_e)
        # R-F2259 — pre-warm the cross-encoder re-ranker (when ARIA_RERANK_ENABLED=1) so the
        # FIRST live search doesn't eat the ~60s cold model load (the baked R-F2222 model is
        # loaded off the event loop here). No-op when the reranker is disabled.
        try:
            from .intel import reranker as _rr
            if _rr.is_enabled():
                await _rr.prewarm()
        except Exception as _rr_e:
            logger.warning("[R-F2259] reranker prewarm failed (non-fatal): %s", _rr_e)
    _bg_task(asyncio.create_task(_prewarm_heavy_imports(), name="heavy_import_prewarm"))

    # F28/R-F2378: use a fresh local alias for the boot-state branches below.
    # R-F2763 removed the later lifespan-local `_os` import so module-level
    # `_os` references can no longer become unbound through local shadowing.
    import os as _f28_os
    _f28_os.environ.setdefault("NODE_OPTIONS", "--no-deprecation")

    # R-F2563 — one-shot EXCLUSIVE boot VACUUM of the hot state DB, run BEFORE the
    # state-backend connect below. It MUST be here (not inside state_store.connect):
    # (a) EXCLUSIVE — no aiosqlite conn/read-pool is open yet, so VACUUM can take its
    #     lock cleanly; and (b) OFF the 20s ARIA_STATE_CONNECT_BOOT_TIMEOUT_S budget that
    #     wraps rs.connect() below — a slow compaction here just delays reclamation, it
    #     does NOT trip that cap and drop the box to the in-memory fallback.
    # The R-F2504 reclaim deleted 376k rows but never compacted the file (~1GB free pages
    # → slow WAL boots + writer pressure = the wedge class). This reclaims it once; after
    # the first compaction it self-gates (below_threshold) to a fast no-op. Existence-guarded
    # + timeout-bounded + failure-tolerant; only runs for the sqlite backend.
    if _f28_os.getenv("ARIA_STATE_BACKEND", "sqlite").strip().lower() in ("sqlite", "", "file"):
        try:
            from .intel import state_store as _ss_vac
            _vac_timeout_s = max(5.0, float(_f28_os.getenv("ARIA_STATE_VACUUM_TIMEOUT_S", "120.0")))
            _vac_res = await asyncio.wait_for(_ss_vac.maybe_boot_vacuum(), timeout=_vac_timeout_s)
            if _vac_res.get("vacuumed"):
                logger.warning("[R-F2563] boot VACUUM reclaimed ~%.0fMB in %.1fs before state connect",
                               _vac_res.get("reclaimed_mb", 0), _vac_res.get("seconds", 0))
        except asyncio.TimeoutError:
            logger.warning("[R-F2563] boot VACUUM exceeded %.0fs — skipped; runtime stays on the "
                           "existing DB (reclamation retries next boot)", _vac_timeout_s)
        except Exception as _vac_e:
            logger.warning("[R-F2563] boot VACUUM skipped (non-fatal): %s", _vac_e)

    # Connect Redis / SQLite / memory backend per ARIA_STATE_BACKEND.
    # R-F762 (2026-05-20): capture the result so /health can flag the
    # backend as RED when connect fails. Pre-R-F762 a Redis-unreachable
    # boot would silently fall back to in-process _mem_store
    # (knowledge cache grows in RAM, lost on restart) and the operator
    # only noticed by manually inspecting fly logs or seeing /health
    # report degraded for unrelated reasons. Now the state-backend
    # health rolls up into /health's top-level status and the
    # /health.state_backend block shows backend (sqlite/upstash/memory)
    # + reachable bool + a status string for observers.
    try:
        _state_connect_timeout_s = max(
            1.0,
            float(_f28_os.getenv("ARIA_STATE_CONNECT_BOOT_TIMEOUT_S", "20.0")),
        )
        _state_connect_ok = await asyncio.wait_for(
            rs.connect(settings.redis_url),
            timeout=_state_connect_timeout_s,
        )
    except asyncio.TimeoutError:
        logger.error(
            "[R-F2378] state-backend connect exceeded %.1fs — falling back to "
            "in-memory dict so lifespan reaches yield",
            _state_connect_timeout_s,
        )
        _state_connect_ok = False
    except Exception as _state_e:
        logger.error(
            "[R-F762] state-backend connect raised — falling back to "
            "in-memory dict (data lost on restart): %s",
            _state_e, exc_info=True,
        )
        _state_connect_ok = False
    app.state.state_backend = (rs._BACKEND if hasattr(rs, "_BACKEND") else "unknown")
    app.state.state_backend_reachable = bool(_state_connect_ok)
    if not _state_connect_ok:
        logger.error(
            "[R-F762] state-backend connect FAILED (backend=%s). "
            "Knowledge cache will grow in RAM and be lost on restart. "
            "Check ARIA_STATE_BACKEND env var + /data volume mount + "
            "Upstash subscription state. /health will show backend=red.",
            app.state.state_backend,
        )

    # R-F1178: install the error ledger handler so WARNING+ log entries
    # are persisted to Redis and the self_coder's _monitor_post_deploy
    # can detect regressions after auto-deploy. Pre-R-F1178 the handler
    # was only installed in tests, so the error count key had no producer
    # in production and the post-deploy monitor was a permanent no-op.
    try:
        from .intel import error_log_handler
        error_log_handler.install()
        logger.info("[R-F1178] Error ledger handler installed")
    except Exception as _elh_err:
        logger.warning("[R-F1178] Error ledger handler install failed: %s", _elh_err)

    # B1 fix 2026-04-27: install the error-ledger logging handler so
    # WARNING+ aria.* logs auto-record into self_improve's error ledger.
    # Previously self_improve.record_error was only wired to 2 sites in
    # aria_engine.py, so the autonomous self-improvement cycle reported
    # "0 errors" every cycle and had nothing to act on.
    try:
        from .intel import error_log_handler as _elh
        _elh.install()
    except Exception as e:
        logger.warning("error-ledger handler install failed (non-fatal): %s", e)

    # Initialize all intel modules — R-F1421: each isolated so one subsystem
    # failing degrades that subsystem instead of aborting the whole lifespan
    # (an unwrapped throw here = never reach `yield` = total outage, F28 class).
    # R-F2122: only the CHEAP inits stay on the critical path. The two HEAVY
    # graphs (knowledge ~223k facts, neural ~1.2M edges) are warmed in the
    # background — see _warmup_heavy_graphs below — so boot reaches `yield`
    # (and /health goes green) in seconds instead of ~10 min.
    _boot_init_failures = await _run_boot_inits([
        ("intel_ledger", intel_ledger.init),
        ("contacts", contacts.init),
        ("competitors", competitors.init),
        ("training_data", training_data.init),
    ])
    if _boot_init_failures:
        logger.error(
            "[R-F1421] %d/4 cheap intel subsystems failed to init: %s — ARIA is "
            "UP but DEGRADED; these are unavailable until fixed/restarted.",
            len(_boot_init_failures), _boot_init_failures,
        )
    try:
        app.state.boot_init_failures = _boot_init_failures
    except Exception as _e672:
        logger.debug("[R-F672] suppressed in lifespan (app.state.boot_init_failures = _boot_init_failur): %s", _e672)

    # ── R-F2122 — load the HEAVY graphs OFF the boot critical path ────────
    # 2026-06-28 incident: loading knowledge (~223k facts) + neural_memory
    # (~1.2M edges) SYNCHRONOUSLY before `yield` made boot take ~10 min —
    # far past fly's 1-min health grace — so every restart was a 10-min
    # outage that looked like a crash loop. These two are independent of the
    # cheap inits above and of each other, and every query fn degrades to
    # empty/partial while they are unloaded (knowledge.search_knowledge→"",
    # knowledge.all_facts→[], neural_memory.recall→empty — verified), so we
    # warm them in the background and serve /health immediately. Chat runs
    # with reduced context for the warmup window, never errors/hangs.
    # _freeze_long_lived_state() (R-F1621, was on the critical path) moves
    # INTO the warmup — it must run AFTER the graphs are in RAM.
    app.state.knowledge_ready = False
    app.state.neural_ready = False
    app.state.heavy_graph_ready = asyncio.Event()

    async def _warmup_heavy_graphs():
        # R-F2201 — LEAN WEB WORKERS. A 'web' role process (R-F2174 election)
        # serves requests but SKIPS the heavy in-memory graph load (knowledge
        # ~223k facts + neural ~1.2M edges) so that 1 engine + N web workers fit
        # in the per-machine RAM (the 8GB OOM constraint — each worker otherwise
        # loads its own ~GB-scale copy). Web workers still serve grounded chat
        # via the PROCESS-SHARED RAG store (chromadb on the /data volume) + the
        # LLM; the doc-lane (R-F2196) + fast-lane skip the 7-layer build entirely,
        # and the full 7-layer build degrades gracefully (each layer is
        # _safe_call-wrapped + budget-bounded) when knowledge/neural aren't
        # loaded. The ENGINE role keeps the full graphs for autonomous work.
        if _aria_role() == "web":
            app.state.knowledge_ready = False
            app.state.neural_ready = False
            logger.info(
                "[R-F2201] LEAN WEB WORKER — heavy graph warmup SKIPPED "
                "(serves via shared RAG + LLM; engine holds the full graphs). "
                "Saves the per-worker in-memory knowledge+neural footprint.")
            app.state.heavy_graph_ready.set()
            return
        # R-F2663 — this warmup is a DETACHED background task (created below); it
        # blocks nothing, so it must NOT inherit _run_boot_inits' aggressive 5s
        # pre-yield timeout. That 5s cap on a ~10-min load (R-F2122: ~223k facts +
        # ~1.2M edges) made the load ALWAYS time out → the init was CANCELLED
        # (graphs never loaded → chat stuck on reduced context indefinitely), AND
        # the umbrella line below logged ERROR on EVERY boot → mirrored to
        # record_error as log:error → RESET the Phase A gate-#3 7-day streak. At
        # ~10 deploys/day the streak could never accrue: gate #3 was structurally
        # un-closeable. Give the background load a generous cap so the normal slow
        # warmup COMPLETES cleanly (zero log), and log any residual as WARNING — a
        # recoverable DEGRADATION (app stays up, reduced context, re-warms next
        # boot), not an ERROR (§14 degraded≠broken; is_reset_type excludes
        # log:warning per error_streak.py:94, so it no longer resets gate #3).
        _warm_timeout = _heavy_warmup_timeout_s()  # R-F4213: cannot raise

        async def _warm_one(_name, _fn):
            try:
                await asyncio.wait_for(_fn(), timeout=_warm_timeout)
                return None
            except asyncio.TimeoutError:
                logger.warning(
                    "[R-F2663] heavy graph '%s' still warming after %.0fs — chat "
                    "runs with reduced context; re-warms next boot (degraded, not "
                    "an error)", _name, _warm_timeout,
                )
                return _name
            except Exception as _we:  # noqa: BLE001 — isolate per-subsystem
                logger.warning(
                    "[R-F2663] heavy graph '%s' warmup failed (degraded, staying "
                    "up): %s", _name, _we,
                )
                return _name

        _heavy_failures = [
            _n for _n in await asyncio.gather(
                _warm_one("knowledge", knowledge.init),
                _warm_one("neural_memory", neural_memory.init),
            ) if _n
        ]
        try:
            app.state.knowledge_ready = "knowledge" not in _heavy_failures
            app.state.neural_ready = "neural_memory" not in _heavy_failures
        except Exception as _e672:
            logger.debug("[R-F672] suppressed in lifespan (app.state.knowledge_ready = 'knowledge' not in _): %s", _e672)
        if _heavy_failures:
            # WARNING, never ERROR (R-F2663): a degraded warmup is recoverable and
            # must not reset the gate-#3 streak. Reserve ERROR for genuinely
            # terminal failures elsewhere.
            logger.warning(
                "[R-F2122/R-F2663] heavy graph warmup degraded: %s — chat runs with "
                "reduced context until re-warmed.", _heavy_failures,
            )
        # Freeze the now-loaded graphs out of GC (R-F1621). Runs once, after
        # warmup; a brief one-time GC pass on the serving loop is acceptable.
        try:
            _freeze_long_lived_state()
        except Exception as _fz_e:
            logger.warning("[R-F2122] freeze-after-warmup skipped (non-fatal): %s", _fz_e)
        logger.info(
            "[R-F2122] heavy graph warmup complete — knowledge_ready=%s neural_ready=%s",
            app.state.knowledge_ready, app.state.neural_ready,
        )
        app.state.heavy_graph_ready.set()

    async def _warmup_heavy_graphs_guarded():
        """R-F4213: guarantee the barrier opens, whatever the warmup does.

        Wrapping rather than reindenting the warmup is deliberate — a bare
        `finally` also runs on CancelledError, which `_warm_one`'s
        `except Exception` cannot catch (it is a BaseException), and it covers
        every future statement added above the inner .set() without anyone
        having to remember this rule. Event.set() is idempotent, so the
        warmup's own set() calls stay as the fast path.
        """
        try:
            await _warmup_heavy_graphs()
        finally:
            app.state.heavy_graph_ready.set()

    _bg_task(asyncio.create_task(_warmup_heavy_graphs_guarded(), name="heavy_graph_warmup"))

    # ---- R-F2300 - reconcile orphaned async-DD 'running' placeholders ---------
    # An async DD (R-F2250) runs in an in-process bg task; a restart (deploy /
    # R-F2277 os._exit / crash) kills it but leaves status='running' forever, so
    # the chat/report poll spins with a frozen "running · ETA …" (2026-07-02: a
    # deep DD sat 'running' 12.5h after a deploy). Sweep once shortly after boot
    # (catches restart-orphans) and every 10 min (catches a hang without restart).
    async def _dd_reconcile_loop():
        await asyncio.sleep(45)  # let the state store settle after boot
        # R-F2541: engine SINGLETON — reconciliation rewrites shared DD status +
        # brain/gap signals; N workers would N× the writes and race on shared state.
        # Started before the election, so wait for it, then exit on non-singleton roles.
        if _election_complete is not None:
            await _election_complete.wait()
        if not _runs_singletons():
            logger.info("[R-F2541] dd_reconcile SKIPPED (ARIA_ROLE=%s)", _aria_role())
            return
        _skip_logged = False
        while True:
            # R-F3524 — checked EVERY iteration, not captured at startup, so the switch
            # works on a box that is already running. Logged on the transition only: a
            # line every 10 minutes for hours is how a real signal gets ignored, and
            # silence is how an operator forgets DD self-heal is off.
            if not _dd_reconcile_enabled():
                if not _skip_logged:
                    logger.warning(
                        "[R-F3524] dd_reconcile PAUSED by ARIA_DD_RECONCILE_ENABLED=0. "
                        "Orphaned status='running' DDs will NOT be cleared and "
                        "restart-killed DDs will NOT be re-launched until this is "
                        "unset. This is an incident lever, not a setting.")
                    _skip_logged = True
                await asyncio.sleep(600)
                continue
            if _skip_logged:
                logger.info("[R-F3524] dd_reconcile RESUMED")
                _skip_logged = False
            await _dd_reconcile_once()   # R-F2568: failure-wired, capability-tested
            await asyncio.sleep(600)
    # R-F2568: register the FACTORY so the bg supervisor respawns this DD-hang self-heal
    # loop if it dies (was death-visible but not auto-respawned). Safe: on the singleton
    # box the loop never returns early, so respawn only fires on a genuine crash.
    _bg_task(asyncio.create_task(_dd_reconcile_loop(), name="dd_reconcile"),
             factory=_dd_reconcile_loop)

    # R-F2507 — start the durable brain-ingest queue drain worker (a SINGLE worker;
    # one process under WEB_CONCURRENCY=1). No-op unless ARIA_BRAIN_QUEUE_ENABLED=1,
    # so this block is byte-identical to legacy when the flag is off. The worker
    # connects the queue db then drains absorb payloads past the state_store writer
    # at bounded concurrency (see brain_hook.brain_queue_drain_loop).
    import os as _os2507
    if _os2507.environ.get("ARIA_BRAIN_QUEUE_ENABLED", "0") == "1":
        async def _brain_queue_drain():
            await asyncio.sleep(20)  # let the state store + boot settle first
            from .intel import brain_hook as _bh2507
            await _bh2507.brain_queue_drain_loop()
        # R-F2537: register the FACTORY so the bg supervisor respawns this load-bearing
        # drain worker if it dies (without it, a post-startup exit was logged but never
        # respawned — the durable ingest queue would silently stop draining). Mirrors the
        # supervised-loop pattern at _bg_task(...factory=...) elsewhere in lifespan.
        _bg_task(asyncio.create_task(_brain_queue_drain(), name="brain_queue_drain"),
                 factory=_brain_queue_drain)

    # R-F2376 (M4/§25): drive outcome_wire's silent-drop reconciler. Its
    # reconcile_silent_drops() had ZERO production callers, so the ACTIVE
    # proprioception layer (a surface that dies AFTER record_request_start but
    # BEFORE a terminal outcome) never fired. Sweep every known surface on a
    # loop so those drops surface as a delivery_failure gap for the self-heal
    # loop. NOTE: producers (record_request_start) are intentionally NOT yet
    # wired into the hot chat path — record_request_start does a shared-key
    # read-modify-write per request (one pending:<surface> dict), which would
    # add hot-key RMW contention (R-F2277). Producer instrumentation needs
    # per-request pending keys first; tracked as a follow-up gap. The scheduler
    # is live now so the mechanism drains the moment producers land.
    async def _outcome_reconcile_loop():
        await asyncio.sleep(90)  # let the state store settle after boot
        # R-F2541: engine SINGLETON — reconciles shared outcome ledger + emits
        # delivery_failure gaps; N workers would duplicate the gap writes. Wait for the
        # election (started before it), then exit on non-singleton roles.
        if _election_complete is not None:
            await _election_complete.wait()
        if not _runs_singletons():
            logger.info("[R-F2541] outcome_reconcile SKIPPED (ARIA_ROLE=%s)", _aria_role())
            return
        while True:
            await _outcome_reconcile_once()   # R-F2568: per-surface failure-wired, tested
            await asyncio.sleep(600)
    # R-F2568: register the FACTORY so the bg supervisor respawns the §25 delivery backstop.
    _bg_task(asyncio.create_task(_outcome_reconcile_loop(), name="outcome_reconcile"),
             factory=_outcome_reconcile_loop)

    # ---- R-F2154 - background expired-entry sweeper --------------------------
    # R-F2568: factory= so the sweeper (already failure-wired R-F2256) also auto-respawns.
    _bg_task(asyncio.create_task(_expiry_sweeper_loop(), name="expiry_sweeper"),
             factory=_expiry_sweeper_loop)

    # ---- R-F2572 — keep the canonical sanctions store fresh -------------------
    # The DAILY-SANCTIONS-REFRESH tasks.yaml task used `tool: shell`, which the autonomous
    # engine has NO handler for → it was a silent no-op and the store went 58 DAYS stale
    # (2026-07-12), so every clean compliance/DD screen returned INSUFFICIENT_DATA and new
    # designations were missed. This first-class loop refreshes at boot (if stale) + every
    # 6h, off the event loop, §21-wired, engine-singleton, factory-respawned.
    async def _sanctions_refresh_loop():
        await asyncio.sleep(150)  # let boot settle — don't compete with warmup or re-download eagerly
        if _election_complete is not None:
            await _election_complete.wait()
        if not _runs_singletons():
            logger.info("[R-F2572] sanctions_refresh SKIPPED (ARIA_ROLE=%s)", _aria_role())
            return
        while True:
            try:
                r = await _sanctions_refresh_once()
                if r.get("refreshed"):
                    logger.warning("[R-F2572] canonical sanctions refresh: %s", str(r)[:300])
            except Exception as e:
                logger.warning("[R-F2572] sanctions refresh loop error: %s", e)
            await asyncio.sleep(6 * 3600)   # re-check every 6h (refresh only fires when stale)
    _bg_task(asyncio.create_task(_sanctions_refresh_loop(), name="sanctions_refresh"),
             factory=_sanctions_refresh_loop)

    # ---- R-F2584 — keep the Golden Intel feed (Telegram channel + dashboard) fresh --------
    # The HOURLY-NEWS-MONITOR autonomous task stopped firing (scheduler) → the news_monitor
    # poll went 27h stale (2026-07-12) → the Golden Intel gate correctly skipped stale signals
    # → the Telegram channel + dashboard "Distribution Ready" went silent. This first-class
    # loop runs the poll hourly (staleness-gated), off the autonomous scheduler, §21-wired,
    # engine-singleton, factory-respawned. Idempotent with the autonomous task (staleness gate
    # → skips a fresh feed) so both can coexist if the scheduler is later fixed.
    async def _news_poll_loop():
        await asyncio.sleep(200)  # boot settle — poll_feeds is ~250s of feed I/O
        if _election_complete is not None:
            await _election_complete.wait()
        if not _runs_singletons():
            logger.info("[R-F2584] news_poll SKIPPED (ARIA_ROLE=%s)", _aria_role())
            return
        while True:
            try:
                r = await _news_poll_once()
                if r.get("polled"):
                    logger.warning("[R-F2584] Golden Intel news poll ran: %s", str(r)[:200])
            except Exception as e:
                logger.warning("[R-F2584] news poll loop error: %s", e)
            await asyncio.sleep(3600)   # hourly (only fires when the feed is stale)
    _bg_task(asyncio.create_task(_news_poll_loop(), name="news_poll"),
             factory=_news_poll_loop)
    # ---- R-F2277 - state_store liveness watchdog (per-process, NOT election- ---
    # gated: each process owns a connection that can wedge). Recovers a hung
    # aiosqlite thread the event-loop watchdog (R-F1417) can't see, escalating
    # reconnect → os._exit so Fly cold-boots. Fixes the 2026-07-02 3.5h outage.
    try:
        from .intel import state_store as _ss_wd
        _bg_task(asyncio.create_task(
            _ss_wd.liveness_watchdog_loop(), name="state_store_liveness_watchdog"))
    except Exception as _ss_wd_e:
        logger.error("[R-F2277] could not start state_store liveness watchdog: %s", _ss_wd_e)
    # ---- R-F2149 - yield IMMEDIATELY so the server starts serving --------
    # Everything below this point is moved into a background task. The
    # previous code had ~2500 lines of boot init between here and the yield
    # at line 3132 - any one of those awaits could hang (search index,
    # crawler, RAG backfill, LLM hydration, etc.) and block the server from
    # ever starting, causing Fly's health check to kill the machine.
    # By yielding now, the server starts serving immediately and the heavy
    # init runs in the background without blocking the event loop.
    # R-F2448 — crawler handles declared at LIFESPAN scope so shutdown (far
    # below) sees the values set inside _boot_continuation (which now uses
    # `nonlocal`). Before: the nested assignment was function-local → at
    # shutdown `_crawler_stop_event` was undefined → a swallowed NameError
    # ("crawler shutdown failed (non-fatal)") → the crawler never stopped
    # cleanly across deploys.
    _crawler_stop_event = None
    _crawler_task = None
    async def _boot_continuation():
        """Everything that was between the heavy graph warmup and the yield,
        now running in a background task so the server starts immediately."""
        nonlocal _crawler_stop_event, _crawler_task  # R-F2448: share with shutdown cleanup
        # R-F4211: reconciliation and search-index seeding exercise the shared
        # state/database tiers. Sequence this non-critical work behind graph
        # hydration; HTTP is already serving at this point.
        await _await_heavy_graph_ready(app)
        # ---- R-F1891 - recover orphaned async jobs after a restart --------
        try:
            from .routes.aria import recover_orphaned_jobs as _recover_jobs
            _n_recovered = await _recover_jobs()
            if _n_recovered:
                logger.info("[R-F1891] failed %d orphaned async job(s) interrupted by the restart", _n_recovered)
        except Exception as _rec_e:
            logger.warning("[R-F1891] orphaned-job recovery skipped (non-fatal): %s", _rec_e)

        # ---- R-F504 - search index ----------------------------------------
        try:
            from .search_index import db as _search_db
            _ok = await _search_db.connect()
            if _ok:
                from .crawler import seed_list as _seeds
                _n = await _seeds.seed_all()
                logger.info("[R-F504] search index ready (%d seed domains registered)", _n)
            else:
                logger.warning("[R-F504] search index connect() returned False")
        except Exception as _exc:
            logger.warning("[R-F504] search index init failed (non-fatal): %s", _exc)

        # ---- R-F507 - light the crawler -----------------------------------
        # R-F2219: the crawler is an engine SINGLETON — it does external N×
        # effects (crawls sites + writes the shared search index). It starts
        # before the election resolves, so wait for that, then only the
        # engine/all role runs it. N crawlers on N workers would N× external
        # load and risk hammering/banning target sites. (Missed in R-F2073.)
        if _election_complete is not None:
            await _election_complete.wait()
        if not _runs_singletons():
            logger.info("[R-F2073] crawler SKIPPED (ARIA_ROLE=%s)", _aria_role())
        elif _f28_os.getenv("ARIA_CRAWLER_DISABLED", "").lower() not in ("1", "true", "yes"):
            try:
                from .crawler import runner as _crunner
                _crawler_stop_event = asyncio.Event()
                _crawl_interval = int(_f28_os.getenv("ARIA_CRAWLER_INTERVAL_SEC", "21600"))
                _crawler_task = asyncio.create_task(
                    _crunner.crawl_loop(interval_sec=_crawl_interval, stop_event=_crawler_stop_event),
                )
                logger.info("[R-F507] crawler attached (interval=%ds)", _crawl_interval)
            except Exception as _exc:
                logger.warning("[R-F507] crawler attach failed (non-fatal): %s", _exc)
        else:
            logger.info("[R-F507] crawler DISABLED via ARIA_CRAWLER_DISABLED env")

    _bg_task(asyncio.create_task(_boot_continuation(), name="boot_continuation"))
    # ── RAG store: probe + backfill ALL in background ──────────────────
    # NEITHER the probe nor the backfill can run inline in lifespan.
    # Past incidents (2026-04-07):
    #   1. Backfill was awaited inline → uvicorn never bound → rollback
    #   2. Backfill moved to background, but get_stats() probe was still
    #      inline → chromadb auto-init triggered sentence-transformer
    #      download from HuggingFace (~30-90s) which blocked yield
    # Fix: probe runs in the same background task as the (optional)
    # backfill, after a delay long enough for the server to bind first.
    # Backfill stays opt-in via ARIA_RAG_BACKFILL_ENABLED.
    rag_backfill_task = None
    backfill_enabled = (_os.getenv("ARIA_RAG_BACKFILL_ENABLED", "") or "").lower() in ("1", "true", "yes")
    backfill_disabled = (_os.getenv("ARIA_RAG_BACKFILL_DISABLED", "") or "").lower() in ("1", "true", "yes")

    # ── R-F459 (2026-05-14) — sentence-transformer prewarm ──────────────
    # Fire the prewarm IMMEDIATELY (no sleep) as its own background task
    # so the ~32s cold model load happens DURING boot, in a worker
    # thread, off the event loop. By the time fly's healthcheck grace
    # period (30s) elapses and real HTTP traffic arrives, the model is
    # warm and the embed-load race no longer fires.
    #
    # Pre-R-F459 history:
    #   - R-F379 (queued 2026-05-12, never shipped) called for this
    #   - R-F458 (2026-05-13 22:18) attempted but used a 15s sleep
    #     before prewarming AND added a threading.Lock — combined effect
    #     was that the first request beat the prewarm to _get_embedder,
    #     held the lock for 32s, and the lock serialised other waiting
    #     threads → ThreadPoolExecutor saturated → GIL starvation →
    #     /health/live timed out → fly PR04 cascade → reverted 22:25.
    #   - R-F459 (this commit) — prewarm fires at T+0 (not T+15) AND
    #     the lock-pattern was validated locally on Python 3.14 first.
    async def _prewarm_inprocess_model():
        try:
            from .intel.semantic_search import prewarm_embedder
            await prewarm_embedder()
            logger.info("[R-F459] sentence-transformer prewarm complete")
        except Exception as exc:
            logger.warning(
                "[R-F459] sentence-transformer prewarm failed "
                "(non-fatal, lazy load will retry): %s", exc,
            )

    async def _embedder_prewarm_bg():
        # R-F1890 — load the embedding model in EXACTLY ONE place to avoid 2x
        # torch+model RAM on the memory-constrained Fly box:
        #   offload ON  → the separate WORKER process owns the model; the main
        #                 process does NOT prewarm it (lazy-loads only if a
        #                 fallback encode is ever needed).
        #   offload OFF → prewarm the in-process model (legacy behaviour).
        try:
            from .intel import encode_offload as _eo
            if _eo._ENABLED:
                await asyncio.to_thread(_eo.start)   # blocks on bounded worker warmup
                if _eo.is_enabled():
                    logger.info("[R-F1890] encode-offload pool ready — main-process model NOT prewarmed (saves RAM)")
                else:
                    logger.warning("[R-F1890] encode-offload pool unavailable — prewarming in-process model as fallback")
                    await _prewarm_inprocess_model()
            else:
                await _prewarm_inprocess_model()
        except Exception as exc:
            logger.warning("[R-F1890] encode-offload start errored — prewarming in-process model as fallback: %s", exc)
            await _prewarm_inprocess_model()
    _bg_task(asyncio.create_task(_embedder_prewarm_bg(), name="embedder_prewarm"))

    # ── R-F2086 — prewarm the knowledge search lowercase cache (_search_lc) ──
    # search_knowledge() builds a per-fact lowercased-text cache on its FIRST
    # scan. That cold build is GIL-bound and, in the 7-layer-context worker pool
    # on the chat path, still stalled the event loop ~5s post-deploy (live wedge
    # stack). Warming it ONCE at boot (off the request path) means user requests
    # always hit the warm cache — the cold scan never lands on a chat turn. One
    # call scans all facts and populates the whole cache regardless of the query.
    async def _prewarm_knowledge_search_bg():
        # R-F2122: knowledge now loads in the background warmup, so wait for it
        # to be ready (cap ~20 min) instead of a fixed 20s — otherwise we'd
        # prewarm an empty cache and the cold scan would still land on a request.
        for _ in range(600):  # 600 * 2s = 20 min cap
            if getattr(app.state, "knowledge_ready", False):
                break
            await asyncio.sleep(2)
        try:
            from .intel import knowledge as _kn
            await asyncio.to_thread(_kn.search_knowledge, "warmup")
            logger.info("[R-F2086] knowledge search cache prewarmed (cold scan off the request path)")
        except Exception as exc:
            logger.warning("[R-F2086] knowledge search prewarm failed (non-fatal, lazy build will retry): %s", exc)
    _bg_task(asyncio.create_task(_prewarm_knowledge_search_bg(), name="knowledge_search_prewarm"))

    # ── R-F2130 — populate the coder's constitutional-rules RAG at boot ──────
    # coding_constitutional was built but NEVER populated (index_constitutional_rules
    # was only ever called in tests), so the autonomous coder was grounded in code
    # STRUCTURE + past fixes but not in the playbook RULES — plausibly why its edits
    # violated conventions (the annotation campaign that shipped 31 syntax errors).
    # Sync the canonical rules off the loop, after the RAG client settles. Guarded;
    # never blocks or breaks boot.
    async def _sync_constitutional_rag_bg():
        await asyncio.sleep(30)  # let chromadb/rag init settle (rag_init_bg sleeps 15)
        try:
            from .intel import coding_rag_indexer as _crag
            res = await asyncio.to_thread(_crag.sync_constitutional_rules)
            logger.info("[R-F2130] constitutional-rules RAG sync: %s", res)
        except Exception as exc:
            logger.warning("[R-F2130] constitutional RAG sync failed (non-fatal): %s", exc)
    _bg_task(asyncio.create_task(_sync_constitutional_rag_bg(), name="constitutional_rag_sync"))

    # ── R-F1512 — seed baseline mastery for topics stuck at scaffold ───
    async def _seed_mastery_bg():
        try:
            from .intel.student import seed_baseline_mastery
            await seed_baseline_mastery()
        except Exception as exc:
            logger.debug("[R-F1512] seed_baseline_mastery skipped: %s", exc)
    _bg_task(asyncio.create_task(_seed_mastery_bg(), name="seed_mastery"))

    # ── R-F703 (2026-05-18) — event-loop stall detector ────────────────
    # The fly /health/live timeout pattern is consistent: the event loop
    # gets blocked by sync CPU work (most commonly sentence_transformers
    # encode under torch's GIL, occasionally large JSON load/save) for
    # 8-30s; during the stall /health/live can't respond → fly LB marks
    # the machine unhealthy → PR04 cascade. The 19:52:34 wedge showed
    # this exactly — autonomy_surface's 4 parallel asyncio.wait_for(...,
    # timeout=8.0) calls all expired at the same wall-clock instant,
    # which is only possible if the loop was wall-clock-stuck for ≥8s.
    #
    # Pre-this-detector we had no on-line signal of *what* was blocking
    # the loop; gate #3 (0 fly ERRORs/7d) has been blocked by recurring
    # ~4-min outage cycles with the actual blocker invisible. This
    # background task wakes every 1s; whenever the real elapsed wall-
    # clock between wakeups exceeds 5s, it logs WARNING with the
    # measured stall duration. The next wedge will be timestamped so
    # we can correlate against what was running at that instant.
    #
    # Implementation is deliberately tiny:
    #   - one asyncio.sleep(1.0) per iteration (zero CPU when idle)
    #   - monotonic-clock measurement (immune to wall-clock jumps)
    #   - logs at WARNING so it joins the error-ledger (R-F381) and
    #     surfaces on the operator dashboard's recent-errors panel.
    #   - 5s threshold chosen so spurious GC pauses (typically <1s)
    #     don't spam; an 8s autonomy_surface timeout = real wedge.
    #
    # R-F704 (2026-05-18) — stack-capture extension. Pre-R-F704 the
    # detector logged "stall happened" but not "what was running". The
    # wedge had already ended by the time the detector iteration runs
    # (it can only wake AFTER the blocking sync work returned and the
    # loop became schedulable again). So we add a sibling daemon
    # *thread* (not coroutine) that updates from a wall-clock heartbeat
    # that the loop posts; when the heartbeat goes stale the thread
    # captures live stack frames via faulthandler.dump_traceback. The
    # daemon thread is OS-scheduled — torch / numpy / sentence_transformers
    # release the GIL during their actual compute, so a 1-second sleep
    # in the daemon can wake and grab the GIL even mid-stall.
    import faulthandler as _fh
    import threading as _threading
    import time as _time

    # R-F710 (2026-05-19) — prefer fly's persistent /data volume so the
    # wedge log survives reboots. Pre-R-F710 the path resolved to
    # /app/data/wedge_stacks/ inside the container, which is wiped on
    # every fly machine restart — including every deploy. The 07:18:58
    # wedge captured immediately after R-F704's deploy would have been
    # gone the moment the next deploy landed. /data is the fly volume
    # mount (same root as aria_state.db / aria_knowledge.json / aria_rag).
    # Local dev (no /data dir) falls back to the repo-local path.
    if _os.path.isdir("/data") and _os.access("/data", _os.W_OK):
        _wedge_dir = "/data/wedge_stacks"
    else:
        _wedge_dir = _os.path.join(_os.path.dirname(__file__), "..", "data", "wedge_stacks")
    try:
        _os.makedirs(_wedge_dir, exist_ok=True)
    except Exception:
        logger.warning("R-F672: wedge dir creation failed")
    # R-F3360 — enforce the R-F1435 retention budget (<=50 files, <=200MB) HERE.
    # It existed since the 2026-06-07 /data-full incident but had a single
    # caller — save_blackout_wedge — so it only ran on the self-restart blackout
    # path. This detector is the writer that actually fills the directory: it
    # opens a NEW file every boot and never cleaned up, leaving 513 files (oldest
    # seven weeks old) against that cap of 50 when measured live on 2026-07-28.
    # Boot is the right moment to charge the budget, because boot is when a file
    # is added; it runs once, off the hot path, and is best-effort by contract.
    try:
        from .intel.self_restart import prune_wedge_dir as _prune_wedges
        _prune_wedges(_wedge_dir)
    except Exception as _pexc:
        logger.warning("[R-F3360] wedge-dir prune skipped (non-fatal): %s", _pexc)
    _wedge_log_path = _os.path.join(
        _wedge_dir, f"wedge_{_os.getpid()}_{int(_time.time())}.log"
    )
    try:
        _wedge_log_fh = open(_wedge_log_path, "a", buffering=1, encoding="utf-8")
        logger.info("[R-F704] wedge stack log → %s", _wedge_log_path)
    except Exception as _exc:
        logger.warning("[R-F704] could not open wedge log (non-fatal): %s", _exc)
        _wedge_log_fh = None

    # Shared monotonic heartbeat the async detector bumps every 1s.
    # Initialised in the future — the daemon won't begin until after
    # the 120s settle window passes (matching the async detector).
    _wedge_state = {
        "heartbeat": _time.monotonic(),
        "armed": False,
        "last_dump": 0.0,
    }
    _STALL_WARN_THRESHOLD_S = 5.0
    # R-F1417 — hard-wedge self-restart ceiling. If the event-loop heartbeat
    # stays stale this long, the loop is genuinely wedged (no legitimate stall
    # survives this — the detector only arms after the 120s cold-boot settle,
    # and real stalls are seconds). The on-loop detector + blackout detector
    # are themselves frozen in a wedge, so this OFF-LOOP daemon thread is the
    # ONLY actor that can still run — it forces a process exit so Fly cold-boots
    # the machine. Default 90s = 18x the 5s warn threshold. Env-tunable; the
    # kill-switch disables it entirely.
    _HARD_WEDGE_CEILING_S = _env_float("ARIA_WEDGE_HARD_CEILING_S", 90.0)
    _WEDGE_SELF_RESTART = (
        _os.getenv("ARIA_WEDGE_SELF_RESTART_ENABLED", "1").strip().lower()
        not in ("0", "false", "no", "off")
    )
    _WEDGE_DUMP_DEBOUNCE_S = 30.0  # don't dump more than once per 30s

    # R-F814 (2026-05-22) — local sys import for is_finalizing() guard
    # below. Module-level `import sys` isn't otherwise needed in main.py;
    # keeping the import local to the watchdog avoids polluting the rest
    # of the file.
    import sys as _sys

    def _wedge_watchdog():
        # Daemon thread. Runs forever; cleanly exits when fh closed.
        while True:
            try:
                _time.sleep(1.0)
                # R-F814 (2026-05-22) — skip during interpreter shutdown.
                # Pre-R-F814 every graceful machine restart (deploy or
                # health-check restart) produced a false-positive "event-
                # loop stalled" wedge dump:
                #   1. uvicorn catches SIGTERM, lifespan unwinds, the
                #      _event_loop_stall_detector task is cancelled.
                #   2. The asyncio loop closes; nothing updates
                #      _wedge_state["heartbeat"] anymore.
                #   3. This daemon watchdog keeps running (daemon=True),
                #      sees the stale heartbeat, dumps stacks.
                #   4. Captured stack shows the main thread at
                #      threading.py:1543 _shutdown — the actual interpreter
                #      shutdown sequence, NOT a runtime wedge.
                # Live evidence (2026-05-22 21:06:41 + 21:18:27 UTC):
                # both captured wedges showed the _shutdown frame —
                # confirmed false positives produced once per release
                # cycle (the app saw 5+ deploys in 4h on 2026-05-22).
                # `sys.is_finalizing()` is True from the moment the
                # interpreter starts shutting down. Bail cleanly rather
                # than emit operator-confusing noise.
                if _sys.is_finalizing():
                    return
                if not _wedge_state.get("armed"):
                    continue
                now = _time.monotonic()
                stale = now - _wedge_state["heartbeat"]
                if (
                    stale > _STALL_WARN_THRESHOLD_S
                    and (now - _wedge_state["last_dump"]) > _WEDGE_DUMP_DEBOUNCE_S
                    and _wedge_log_fh is not None
                ):
                    _wedge_state["last_dump"] = now
                    try:
                        _wedge_log_fh.write(
                            f"\n=== [R-F704] event-loop heartbeat stale "
                            f"by {stale:.2f}s at wall-clock "
                            f"{_time.strftime('%Y-%m-%d %H:%M:%S UTC', _time.gmtime())} "
                            f"(epoch {_time.time():.3f}) ===\n"
                        )
                        _fh.dump_traceback(file=_wedge_log_fh, all_threads=True)
                        _wedge_log_fh.write("=== end stack dump ===\n")
                        _wedge_log_fh.flush()
                    except Exception:
                        # Daemon must never crash — swallow and continue.
                        pass
                # R-F1417 — REAL recovery: if the loop is wedged past the hard
                # ceiling, nothing on the loop can recover it. This off-loop
                # daemon is the only actor left, so force a process exit → Fly
                # cold-boots the machine → ARIA self-recovers. Gated by a tested
                # pure predicate so the dangerous os._exit only fires when
                # genuinely wedged (armed + far past any legitimate stall) and
                # never when the kill-switch is off.
                if _should_force_restart(
                    stale, _wedge_state.get("armed", False),
                    _WEDGE_SELF_RESTART, _HARD_WEDGE_CEILING_S,
                ):
                    try:
                        if _wedge_log_fh is not None:
                            _wedge_log_fh.write(
                                f"\n=== [R-F1417] HARD WEDGE {stale:.1f}s > "
                                f"ceiling {_HARD_WEDGE_CEILING_S:.0f}s — forcing "
                                f"os._exit(1) so Fly cold-boots the machine ===\n"
                            )
                            _fh.dump_traceback(file=_wedge_log_fh, all_threads=True)
                            _wedge_log_fh.flush()
                    except Exception as _e672:
                        logger.debug("[R-F672] suppressed in lifespan (if _wedge_log_fh is not None:): %s", _e672)
                    try:
                        logger.critical(
                            "[R-F1417] event loop wedged %.1fs > hard ceiling "
                            "%.1fs — forcing process exit so Fly restarts the "
                            "machine (self-recovery from blackout)",
                            stale, _HARD_WEDGE_CEILING_S,
                        )
                    except Exception as _e672:
                        logger.debug("[R-F672] suppressed in lifespan (logger.critical(): %s", _e672)
                    # os._exit (not sys.exit): immediate, no atexit/cleanup that
                    # would hang on the wedged loop. Durable writers are atomic
                    # (os.replace) / WAL crash-consistent, so an exit mid-write
                    # is safe. Fly's on-failure restart cold-boots us.
                    _os._exit(1)
            except Exception:
                # Defensive: keep watchdog alive across any failure.
                continue

    _threading.Thread(
        target=_wedge_watchdog,
        daemon=True,
        name="rf704-wedge-watchdog",
    ).start()

    async def _event_loop_stall_detector():
        import asyncio as _aio
        # R-F1332: import tick_heartbeat once so the 1s loop doesn't re-import.
        try:
            from .intel.self_restart import tick_heartbeat as _tick_hb
        except ImportError:
            _tick_hb = None
        # Wait until the lifespan settle window has fully passed before
        # starting to measure. Cold-boot hydration legitimately stalls
        # the loop for tens of seconds (RAG, knowledge load, OCR
        # prewarm); we only care about post-warm stalls.
        await _aio.sleep(120)
        _wedge_state["heartbeat"] = _time.monotonic()
        _wedge_state["armed"] = True
        logger.info(
            "[R-F703] event-loop stall detector armed (threshold=%.1fs); "
            "[R-F704] watchdog will dump live stacks to %s on stall",
            _STALL_WARN_THRESHOLD_S,
            _wedge_log_path,
        )
        last = _time.monotonic()
        # R-F2177: debounce stall→gap recording (acute stalls can recur; one gap
        # per 10 min is enough for the coder to act without a gap storm).
        _last_stall_gap_at = 0.0
        _STALL_GAP_MIN_INTERVAL_S = 600.0
        while True:
            try:
                await _aio.sleep(1.0)
            except _aio.CancelledError:
                return
            now = _time.monotonic()
            elapsed = now - last
            last = now
            _wedge_state["heartbeat"] = now
            # R-F2849 — feed the queryable loop-lag gauge from the SAME measurement
            # the R-F703 detector already computes. One 1s loop, two consumers: this
            # detector LOGS discrete stalls (>5s); loop_monitor exposes a rolling
            # p50/p95/max on /health so contention is observable BEFORE it wedges.
            # (elapsed is the wake interval; the overshoot over the 1s sleep is the lag.)
            try:
                from .intel.loop_monitor import record_lag as _record_lag
                _record_lag(max(0.0, (elapsed - 1.0) * 1000.0))
            except Exception as _e672:
                logger.debug("[R-F672] suppressed in lifespan (from .intel.loop_monitor import record_lag as _r): %s", _e672)
            # R-F1332: tick the self_restart heartbeat for aria_main every 1s.
            # The stall detector already runs every 1s, so this is a free tick
            # that keeps the blackout detector happy without a separate task.
            if _tick_hb is not None:
                try:
                    _tick_hb("aria_main")
                except Exception as _e672:
                    logger.debug("[R-F672] suppressed in lifespan (_tick_hb('aria_main')): %s", _e672)
            if elapsed > _STALL_WARN_THRESHOLD_S:
                # R-F3252 — report the MEASUREMENT, not a guess at its cause.
                #
                # This said "synchronous CPU work blocked the loop" and then
                # named three culprits. All the detector actually knows is that
                # the heartbeat did not tick for `elapsed` seconds. On
                # 2026-07-27 the R-F704 stack captured during one of these
                # showed the main thread parked in a bare `asyncio.runners.run`
                # with NO application frame — nothing was blocking a coroutine.
                # The real signature was 56 live aiosqlite connection worker
                # threads (peak 140) against a design of ~6: GIL starvation, a
                # different failure with a different fix.
                #
                # An asserted cause is worse than no cause: two review cycles
                # went looking for a blocking call that was never there. The
                # stack dump is the evidence — point at it and stop guessing.
                logger.warning(
                    "[R-F703] event loop heartbeat did not tick for %.2fs "
                    "(threshold=%.1fs). CAUSE NOT ESTABLISHED by this detector "
                    "— it measures loop latency only. Both a blocking call in a "
                    "coroutine AND thread/GIL starvation with an idle loop "
                    "produce this. [R-F704] the live stack dump at %s "
                    "distinguishes them: an application frame on the main "
                    "thread means something blocked the loop; a bare "
                    "asyncio.runners.run means it was starved, so count the "
                    "worker threads instead.",
                    elapsed, _STALL_WARN_THRESHOLD_S, _wedge_log_path,
                )
                # R-F2185 — feed the adaptive load governor so autonomy SHEDS its
                # next tick when the loop is being blocked by CPU work (self-heal:
                # background work yields to serving). Pure in-memory, never raises.
                try:
                    from .intel import load_governor as _lg_stall
                    _lg_stall.record_loop_stall(elapsed)
                except Exception as _e672:
                    logger.debug("[R-F672] suppressed in lifespan (from .intel import load_governor as _lg_stall): %s", _e672)
                # R-F2177 (§21a/§21e): make the acute stall a coder-visible GAP so
                # the autonomous coder can fix the blocking call — before this it
                # was logger-warning-only (DARK), so ARIA could not see or fix her
                # own loop stalls. Debounced + fire-and-forget + fully guarded;
                # complements the continuous_profiler hotspot gap (sustained CPU)
                # by catching acute multi-second freezes that don't dominate a
                # sample window. The wedge log holds the culprit MAIN-thread frame.
                if (now - _last_stall_gap_at) >= _STALL_GAP_MIN_INTERVAL_S:
                    _last_stall_gap_at = now
                    try:
                        from .intel import capability_gaps as _cg_stall
                        asyncio.create_task(_cg_stall.record_gap(
                            gap_type="performance",
                            severity="HIGH",
                            title=f"event-loop stall {elapsed:.0f}s",
                            detail=(
                                f"event loop stalled {elapsed:.1f}s — synchronous CPU "
                                f"work on the loop thread froze all async work. Live "
                                f"thread stacks captured at {_wedge_log_path}; the "
                                f"MAIN-thread frame under uvicorn/asyncio.run is the "
                                f"culprit. Fix: offload that CPU-bound call (gzip/json/"
                                f"encode) with asyncio.to_thread or a process pool."
                            ),
                            source="event_loop_stall_detector",
                        ))
                    except Exception as _sg_e:
                        logger.debug("[R-F2177] stall gap-record failed: %s", _sg_e)
    _bg_task(asyncio.create_task(_event_loop_stall_detector(), name="stall_detector"))

    async def _rag_init_bg():
        # Wait for the server to bind and answer initial health checks
        # before we touch chromadb. The model download alone can take
        # 30-90s on a cold volume.
        await asyncio.sleep(15)
        try:
            stats = await rag_store.get_stats()
            app.state.rag_ready = True  # R-F2814 — RAG subsystem responded → reachable
            logger.info("[RAG] probe: %s", stats)
        except Exception as e:
            logger.warning("[RAG] probe failed (non-fatal): %s", e)
            return

        # R-F2856 — if RAG is DEGRADED because the R-F2855 breaker tripped (one
        # collection's HNSW segfaults on query), auto-diagnose WHICH collection is
        # corrupt, quarantine ONLY it (rename aside, never delete — §7), clear the
        # breaker, and re-init so the HEALTHY collections (aria_facts, coding_*) come
        # back UP. Runs in a thread (subprocess probes block). Fires ONLY on a tripped
        # breaker — never during normal operation. Automates the manual 2026-07-22 fix.
        if not stats.get("available"):
            try:
                _tripped = rag_store._crash_counter_read() >= rag_store._CRASH_BREAKER_THRESHOLD
            except Exception:
                _tripped = False
            if _tripped:
                logger.warning("[RAG] degraded + breaker tripped — R-F2856 self-heal starting")
                try:
                    heal = await asyncio.to_thread(
                        rag_store.diagnose_and_heal_corrupt_collections)
                except Exception as _he:
                    heal = {"healed": False, "errors": [f"self-heal raised: {_he}"]}
                logger.warning("[RAG] R-F2856 self-heal result: %s", heal)
                if heal.get("healed"):
                    app.state.rag_ready = True
                    try:
                        logger.info("[RAG] post-heal probe: %s", await rag_store.get_stats())
                    except Exception as _e672:
                        logger.debug("[R-F672] suppressed in lifespan (logger.info('[RAG] post-heal probe: %s', await r): %s", _e672)
                # §21/§25 — the limb reports its outcome (success AND failure) to the brain
                try:
                    from .intel import brain_hook as _bh
                    _parked = [q.get("name") for q in heal.get("quarantined", [])]
                    await _bh.record_signal(
                        module="rag_store",
                        success=bool(heal.get("healed")),
                        summary=(f"R-F2856 RAG self-heal: quarantined={_parked} "
                                 f"healed={heal.get('healed')} errors={heal.get('errors')}")[:300],
                    )
                except Exception as _e672:
                    logger.debug("[R-F672] suppressed in lifespan (from .intel import brain_hook as _bh): %s", _e672)
        if not backfill_enabled or backfill_disabled:
            logger.info(
                "[RAG] backfill skipped (enabled=%s disabled=%s) — "
                "set ARIA_RAG_BACKFILL_ENABLED=true to opt in",
                backfill_enabled, backfill_disabled,
            )
            return
        if not stats.get("available") or stats.get("total_chunks", 0) > 0:
            logger.info(
                "[RAG] backfill skipped (available=%s chunks=%s)",
                stats.get("available"), stats.get("total_chunks", 0),
            )
            return
        try:
            logger.info("[RAG] empty store — running one-shot backfill (background)")
            result = await rag_store.backfill_from_existing()
            logger.info("[RAG] backfill complete: %s", result)
        except Exception as e:
            logger.warning("[RAG] backfill failed (non-fatal): %s", e)

    rag_backfill_task = _bg_task(asyncio.create_task(_rag_init_bg(), name="rag_init"))

    # ---- R-F2150 - LLM provider + resilience + dialogue_state in background -----
    # These were on the critical path between boot_continuation and yield.
    # Any one of them could hang (slow import, network timeout, DB lock) and
    # block the server from ever starting. Moving them into a background task
    # means the server starts serving in <2s.
    app.state.llm_provider = None
    app.state.current_data = None
    # R-F2814 (Stage A of R-F2813 HA re-architecture) — readiness flag, read by
    # GET /health/ready. Starts False; set True once the RAG probe below confirms
    # the retrieval subsystem responds. llm readiness is read directly off
    # app.state.llm_provider (single source of truth), so no separate llm flag.
    app.state.rag_ready = False

    async def _init_llm_and_dialogue_bg():
        """Create LLM provider, resilience layer, and init dialogue_state
        in the background so the lifespan yields immediately."""
        import os as _os_bg
        _provider_key_map = {
            "anthropic": settings.anthropic_api_key,
            "openai": settings.openai_api_key,
            "gemini": settings.gemini_api_key,
            "deepseek": settings.deepseek_api_key,
            "groq": _os_bg.environ.get("GROQ_API_KEY", ""),
        }
        api_key = (
            settings.llm_api_key
            or _provider_key_map.get(settings.llm_provider.lower().strip(), "")
            or settings.deepseek_api_key
        )
        _llm = create_fallback_chain(
            primary_provider=settings.llm_provider,
            primary_key=api_key,
            primary_model=settings.llm_model,
            primary_base_url=settings.llm_base_url,
        )
        if _llm and hasattr(_llm, "hydrate_from_redis"):
            try:
                n = await _llm.hydrate_from_redis()
                if n:
                    logger.info("LLM fallback chain: rehydrated %d HARD cooldown(s) from Redis", n)
            except Exception as e:
                logger.warning("LLM cooldown hydrate failed (non-fatal): %s", e)
        if not _llm:
            _llm = create_llm_provider(
                provider=settings.llm_provider, api_key=api_key,
                model=settings.llm_model, base_url=settings.llm_base_url,
                ollama_url=settings.ollama_url, ollama_model=settings.ollama_model,
            )
        if _llm:
            try:
                from .llm.metered import MeteredProvider
                _llm = MeteredProvider(_llm)
                logger.info("LLM provider wrapped with cost meter")
            except Exception as e:
                logger.warning("MeteredProvider wrap failed (non-fatal): %s", e)
        if _llm:
            try:
                from .llm.rate_limiter import RateLimitedProvider
                _llm = RateLimitedProvider(_llm)
                logger.info("LLM provider wrapped with rate limiter (rpm=%s)",
                            _os_bg.getenv("ARIA_LLM_RPM", "50"))
            except Exception as e:
                logger.warning("RateLimitedProvider wrap failed (non-fatal): %s", e)

        try:
            from .llm.resilience import LLMHealthChecker, LLMRequestQueue, LLMResponseCache
            _llm_health_checker = LLMHealthChecker()
            await _llm_health_checker.start()
            _llm_request_queue = LLMRequestQueue(_llm)
            _llm_response_cache = LLMResponseCache(_llm_request_queue)
            app.state.llm_provider = _llm_response_cache
            logger.info("[R-F1368] LLM resilience layer active")
        except Exception as _resilience_e:
            logger.warning("[R-F1368] LLM resilience layer init failed (non-fatal): %s", _resilience_e)
            app.state.llm_provider = _llm

        if app.state.llm_provider and getattr(app.state.llm_provider, "is_configured", False):
            logger.info(f"LLM provider: {app.state.llm_provider.name}")
        else:
            logger.warning("LLM provider not configured - set LLM_PROVIDER + LLM_API_KEY")

        try:
            from .intel import dialogue_state as _ds_boot
            await _ds_boot._ensure_conn()
            logger.info("[R-F673] dialogue_state DB init")
        except Exception as _ds_e:
            logger.warning(
                "[R-F673] dialogue_state init failed at boot - open-question "
                "tracking will be degraded until DB is reachable: %s", _ds_e,
            )

    _bg_task(asyncio.create_task(_init_llm_and_dialogue_bg(), name="init_llm_and_dialogue"))

    # ── R-F248 (2026-05-11) — startup state snapshot ──────────────────────
    # Log a single "ARIA state at boot" line with the size of every
    # persistent store. This is the FIRST line operators should see if
    # any data was lost on the deploy (knowledge / RAG / mem0 / neural /
    # ledger should all match the previous boot ± natural growth).
    # If a count drops by more than ~5% across restarts, something
    # truncated or corrupted state and the operator needs to investigate
    # before traffic resumes.
    async def _log_boot_state():
        # Defer a few seconds so all stores have finished their lazy
        # init (chromadb + knowledge + ledger + neural all warm up
        # asynchronously after lifespan starts).
        # R-F2122: the heavy graphs now warm in the background, so wait for
        # them to be ready (cap ~20 min) before snapshotting — otherwise the
        # boot-state log reports misleading zeros for knowledge/neural.
        await asyncio.sleep(10)
        # R-F4170 (C-184) — REMEMBER WHETHER THE WAIT ACTUALLY FINISHED.
        # This loop gives up after 20 minutes. When it does, every counter
        # below is a lower bound on itself, and R-F251's diff at the bottom of
        # this function turned that into "STATE REGRESSION DETECTED" — an
        # ERROR, which resets Phase A gate #3. Measured live 2026-08-19:
        # neural_neurons 17742 -> 10378 (-41.5%) at exactly the 20-minute mark,
        # while the graph in that same machine read loaded=True, neurons=17743.
        # Nothing was lost. The snapshot was simply taken mid-load.
        _stores_ready = False
        for _ in range(600):  # 600 * 2s = 20 min cap
            if getattr(app.state, "knowledge_ready", False) and getattr(app.state, "neural_ready", False):
                _stores_ready = True
                break
            await asyncio.sleep(2)
        snapshot = {
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "stores_ready": _stores_ready,
        }
        try:
            from .intel import knowledge as _kb
            snapshot["knowledge_facts"] = len(_kb.all_facts())
        except Exception as e:
            snapshot["knowledge_facts"] = f"err:{str(e)[:40]}"
        try:
            from .intel import intel_ledger as _il
            sigs = await _il.recent_signals(limit=10**9)
            snapshot["ledger_signals"] = len(sigs) if isinstance(sigs, list) else "err"
        except Exception as e:
            snapshot["ledger_signals"] = f"err:{str(e)[:40]}"
        try:
            from .intel import rag_store as _rs
            rs_stats = await _rs.get_stats()
            snapshot["rag_chunks"] = rs_stats.get("documents_indexed", "err")
            snapshot["rag_facts"] = rs_stats.get("facts_indexed", "err")
        except Exception as e:
            snapshot["rag_chunks"] = f"err:{str(e)[:40]}"
        try:
            from .intel import chat_audit_log as _cal
            cal_stats = await _cal.get_stats()
            snapshot["chat_audit_total"] = cal_stats.get("total_entries", 0)
        except Exception as e:
            snapshot["chat_audit_total"] = f"err:{str(e)[:40]}"
        try:
            from .intel import neural_memory as _nm
            if hasattr(_nm, "get_stats"):
                nm_stats = await _nm.get_stats()
                # R-F4173 (C-185) — a store read FAILURE leaves the graph
                # UNLOADED, not empty, and R-F2951's `loaded` flag cannot say
                # so (init() sets it True on its except branch too). Measured
                # 2026-08-19: neural_edges 159198 -> 0 was reported as a -100%
                # regression on a graph that had lost nothing. Fold it into
                # C-184's comparability gate so NEITHER neural counter is
                # diffed against a complete baseline.
                #
                # `is False` deliberately: an older module that does not publish
                # the key returns None, and an absent fact must not be read as
                # a failure.
                if nm_stats.get("load_complete") is False:
                    _stores_ready = False
                    snapshot["stores_ready"] = False
                snapshot["neural_neurons"] = nm_stats.get("total_neurons", "n/a")
                # R-F2951 — the neural graph loads via an async incremental boot
                # warmup (~10 min), so an early-boot get_stats reads total_edges=0
                # UNTIL `loaded` flips True. R-F251's regression check would read
                # that 0 as a -100% neural_edges drop and logger.error(...) →
                # error_log_handler → record_error("log:error") → RESET the Phase A
                # gate-#3 7-day clean streak — on EVERY deploy (same class as
                # R-F2663/R-F2668). Emit a non-numeric "loading" so R-F251's
                # numeric-only diff skips it: we cannot claim a regression on a
                # counter that has not finished loading. A genuine drop-to-0 is
                # still caught — that reads loaded=True with total_edges=0.
                if nm_stats.get("loaded", True):
                    snapshot["neural_edges"] = nm_stats.get("total_edges", "n/a")
                else:
                    snapshot["neural_edges"] = "loading"
            else:
                snapshot["neural_neurons"] = "n/a"
        except Exception as e:
            snapshot["neural_neurons"] = f"err:{str(e)[:40]}"
        try:
            from .intel import state_store as _ss
            ss = await _ss.stats()
            snapshot["state_backend"] = ss.get("backend", "unknown")
            snapshot["state_keys"] = ss.get("key_count", "n/a")
        except Exception:
            snapshot["state_backend"] = "upstash-or-memory"

        logger.warning(
            "[R-F248] ARIA STATE AT BOOT — %s",
            " · ".join(f"{k}={v}" for k, v in snapshot.items()),
        )
        # Also persist the snapshot for diff-on-next-boot
        try:
            from .intel import redis_store as _rs_b
            await _rs_b.lpush("crucix:aria:boot_snapshots",
                              __import__("json").dumps(snapshot, default=str))
            await _rs_b.ltrim("crucix:aria:boot_snapshots", 0, 49)
        except Exception as _snap_e:
            # R-F672 (2026-05-17): promoted from silent pass per audit
            # — boot-snapshot persistence failure means R-F248 boot-state
            # diff loses the previous baseline, silently masking the next
            # regression. Log so the operator sees it in fly logs.
            logger.warning(
                "R-F672: boot snapshot persistence failed (next boot-diff "
                "will be against an older baseline): %s",
                _snap_e,
            )

        # R-F251 (2026-05-11) — regression detection. Diff this boot's
        # snapshot against the most recent COMPLETE previous one (R-F4170;
        # it was "index 1" until a partially-loaded snapshot proved that a
        # poisoned baseline can hide the very loss this looks for). If any
        # numeric counter dropped by >5%, that's silent state loss — log
        # a LOUD warning AND absorb to brain_hook so the operator
        # dashboard surfaces it. Per the infinite-memory rule a counter
        # NEVER drops on a healthy deploy; if it does, the operator
        # needs to know BEFORE traffic resumes.
        try:
            from .intel import redis_store as _rs_diff
            from .intel import boot_snapshot_diff as _bsd
            import json as _json_diff
            # R-F4170 — a WINDOW, not index 1. A partial snapshot persisted by
            # an earlier slow boot must not become a permanently low baseline:
            # against one, a genuine loss reads as growth, so the false alarm
            # would go on to BLIND the guard. select_baseline walks back to the
            # most recent COMPLETE snapshot instead.
            prior_raw = await _rs_diff.lrange("crucix:aria:boot_snapshots", 1, 49)
            _priors: list = []
            for _raw in (prior_raw or []):
                try:
                    _priors.append(_json_diff.loads(_raw) if isinstance(_raw, str) else _raw)
                except Exception:
                    continue
            _verdict = _bsd.diff_boot_snapshots(snapshot, _priors)
            # R-F4170 §21a — success AND failure reach the brain. Before this
            # the skip branch was a console line only, i.e. DARK: nothing could
            # tell that the data-loss detector had not run.
            _bsd.record_verdict(_verdict)
            if not _verdict["comparable"]:
                # COULD NOT MEASURE. Never an ERROR (it would reset gate #3 for
                # something nobody observed) and never silence (that would read
                # as an all-clear — the absence-as-measurement shape §1 records
                # three Phase A gates being certified by).
                logger.warning(
                    "[R-F251/R-F4170] boot-state regression check SKIPPED (%s) — "
                    "stores_ready=%s. This is NOT an all-clear: the counters were "
                    "not comparable, so no claim is made either way.",
                    _verdict["reason"], _stores_ready,
                )
            else:
                drops: list[str] = _verdict["drops"]
                if drops:
                    logger.error(
                        "[R-F251] STATE REGRESSION DETECTED — counters dropped >5%% "
                        "since previous boot: %s",
                        "; ".join(drops),
                    )
                    try:
                        from .intel import brain_hook as _bh_reg
                        await _bh_reg.absorb(
                            module="boot_diagnostic",
                            summary="R-F251: state regression detected at boot",
                            detail=(
                                "Per the infinite-memory rule, NO counter should "
                                "drop across restarts. The following counters fell "
                                f"by >5% since the previous boot: {'; '.join(drops)}. "
                                "Investigate disk volume mount, Redis fallback "
                                "behaviour, or recent code changes BEFORE traffic "
                                "resumes."
                            ),
                            success=False,
                            gap_type="boot_state_regression",
                            gap_detail="; ".join(drops),
                        )
                    except Exception as _absorb_e:
                        # R-F672 (2026-05-17): promoted from silent
                        # pass — if brain_hook fails to record the
                        # regression, we still want the failure
                        # itself logged so the operator knows the
                        # alert was generated but didn't land.
                        logger.warning(
                            "R-F672: brain_hook.absorb for boot_state "
                            "regression failed (alert may not surface "
                            "on dashboard): %s",
                            _absorb_e,
                        )
        except Exception as _diff_err:
            logger.debug("R-F251 boot-diff failed: %s", _diff_err)
    _bg_task(asyncio.create_task(_log_boot_state(), name="log_boot_state"))

    # R-F1539: boot-time secret self-audit. Validates that expected
    # environment variables have sane values — catches CLI-flag-leak
    # mistakes (e.g. ARIA_RAG_BACKFILL_DISABLED="true -a aria-intel")
    # before they cause confusing debugging sessions.
    async def _audit_secrets_bg():
        await asyncio.sleep(3)
        _suspect: list[str] = []
        for _key, _hint in _SECRET_AUDIT.items():
            _val = _os.environ.get(_key, "")  # R-F1571: was bare `os` → NameError crashed the audit task on boot
            if not _val:
                continue
            # Check for CLI flags leaked into the value
            if _val.startswith("-") or " -" in _val:
                _suspect.append(f"{_key}={_val!r} (value contains CLI flags — may have been set via `fly secrets set {_key}={_val} -a app`)")
            # Check for known malformed patterns
            if _key == "ARIA_RAG_BACKFILL_DISABLED" and _val not in ("true", "false", "1", "0", ""):
                _suspect.append(f"{_key}={_val!r} (expected true/false/1/0 — current value is inert but misleading)")
        if _suspect:
            logger.warning(
                "[R-F1539] SECRET AUDIT — %d suspect value(s) found:\n%s",
                len(_suspect), "\n".join(f"  • {s}" for s in _suspect),
            )
    _bg_task(asyncio.create_task(_audit_secrets_bg(), name="audit_secrets"))

    # ── OCR pre-warm ────────────────────────────────────────────────────
    # Load OCR backends in a background task so the first user image
    # doesn't pay the cold-start cost mid-request. Tesseract is cheap to
    # probe; EasyOCR is opt-in via ARIA_PREWARM_EASYOCR. Past incident
    # (2026-04-07): EasyOCR cold-loaded its 200MB model on the first OCR
    # call and OOM-killed the worker.
    async def _prewarm_ocr_bg():
        # Wait until sentence-transformers + chromadb have settled so we
        # don't pile model loads on top of each other and trigger an OOM
        # before traffic even arrives.
        await asyncio.sleep(20)
        try:
            status = await ocr_module.prewarm_ocr()
            logger.info("[OCR Pre-warm] %s", status)
        except Exception as e:
            logger.warning("[OCR Pre-warm] failed: %s", e)
    ocr_prewarm_task = _bg_task(asyncio.create_task(_prewarm_ocr_bg(), name="ocr_prewarm"))

    async def _prewarm_document_reader_bg():
        try:
            await asyncio.to_thread(__import__, "aria_service.intel.document_reader")
            logger.info("[R-F2378] document_reader import prewarmed in background")
        except Exception as e:
            logger.warning("[R-F2378] document_reader prewarm failed (non-fatal): %s", e)
    _bg_task(asyncio.create_task(_prewarm_document_reader_bg(), name="document_reader_prewarm"))

    # ── R-F2174: engine-role election ────────────────────────────────────
    # Resolve THIS worker's role BEFORE any singleton loop is decided below.
    # No-op (role stays 'all') unless ARIA_ENGINE_ELECTION=1. state_store is
    # connected by now (rs.connect above), and the election is fail-safe — any
    # error leaves the worker as 'all' so singletons always run somewhere.
    try:
        _election_timeout_s = _env_float("ARIA_ENGINE_ELECTION_BOOT_TIMEOUT_S", 5.0)
        await asyncio.wait_for(_elect_engine_role(), timeout=max(0.5, _election_timeout_s))
    except asyncio.TimeoutError:
        globals()["_resolved_role"] = "all"
        logger.warning(
            "[R-F2378] engine election exceeded boot budget — falling back to ALL "
            "and continuing startup"
        )
    # R-F2219: release the pre-election singleton loops now that the role is known.
    if _election_complete is not None:
        _election_complete.set()
    if _aria_role() == "engine":
        _bg_task(asyncio.create_task(_engine_heartbeat_loop(), name="engine_heartbeat"))
        logger.info("[R-F2174] engine heartbeat started (lease TTL=%ds)", _engine_lease_ttl_s())

    # ── One-shot reasoning_library cleanup ───────────────────────────────
    # Removes cached cases whose normalised question has < MIN_SALIENT_TOKENS
    # tokens — these are the entries that caused the 2026-04-08 over-cache
    # incident (every "Aria are you online?" returned the same Angola briefing
    # because it had been miscached against the single token "online").
    # Runs in a background task with a short delay so it can never block
    # uvicorn from binding to 0.0.0.0:8000.
    async def _purge_reasoning_library_bg():
        await asyncio.sleep(5)
        # R-F333 (2026-05-11): boot-time reasoning_library size diagnostic.
        # Live evidence 21:19:37 — Student Quiz fired with library_size=0,
        # meaning the chat-recorded cases weren't accumulating. Without
        # this log line we had to wait for the 3-hourly quiz to learn the
        # library was empty. Now: emit the count at boot + on every
        # consolidate cycle, AND brain_hook a gap when library is empty
        # so the dashboard surfaces it as an operator-action item.
        try:
            _bo_index = await reasoning_library._load_index()
            _bo_count = len(_bo_index or [])
            logger.info(
                "[R-F333] reasoning_library boot diagnostic: %d cases loaded from INDEX_KEY",
                _bo_count,
            )
            if _bo_count == 0:
                logger.warning(
                    "[R-F333] reasoning_library EMPTY at boot — chat-recorded "
                    "cases aren't accumulating. Check Upstash INDEX_KEY "
                    "(crucix:aria:reasoning_library:index) AND record_response "
                    "filter rejections."
                )
                try:
                    from .intel import brain_hook as _bh_rf333
                    await _bh_rf333.absorb(
                        module="reasoning_library",
                        summary="R-F333: reasoning_library empty at boot",
                        detail=(
                            "INDEX_KEY returned 0 cases on startup. Either "
                            "Upstash key was wiped, record_response is "
                            "rejecting every chat answer, or the chat path "
                            "isn't reaching record_cloud_llm_response. "
                            "Investigate: (1) GET crucix:aria:reasoning_library:index "
                            "from Upstash REST API, (2) grep fly logs for "
                            "record_response rejection reasons, (3) verify "
                            "chat handler wiring."
                        ),
                        success=False,
                        gap_type="reasoning_library_empty_at_boot",
                        gap_detail="0 cases in INDEX_KEY at startup",
                    )
                except Exception as _bh_e:
                    logger.debug("R-F333 brain_hook absorb failed: %s", _bh_e)
        except Exception as _bd_e:
            logger.warning("[R-F333] reasoning_library boot diagnostic failed: %s", _bd_e)

        try:
            result = await reasoning_library.purge_unsafe_cases()
            logger.info("[Reasoning Library] startup purge (unsafe): %s", result)
        except Exception as e:
            logger.warning("[Reasoning Library] startup purge (unsafe) failed: %s", e)
        try:
            # Second pass: remove fresh-input-tied and turn-failure responses.
            # Catches the detonator_suppliers.xlsx replay cluster (2026-04-11).
            polluted = await reasoning_library.purge_polluted_cases()
            logger.info("[Reasoning Library] startup purge (polluted): %s", polluted)
        except Exception as e:
            logger.warning("[Reasoning Library] startup purge (polluted) failed: %s", e)
    reasoning_purge_task = _bg_task(asyncio.create_task(_purge_reasoning_library_bg(), name="reasoning_purge"))

    # Start autonomous research scheduler (every 30 minutes).
    # Can be disabled entirely with ARIA_AUTONOMOUS_RESEARCH_ENABLED=0 — useful
    # during interactive testing because the research cycle's sync model.encode()
    # calls block the event loop and starve chat replies on a 2GB fly machine.
    research_task = None
    research_enabled = (_os.getenv("ARIA_AUTONOMOUS_RESEARCH_ENABLED", "1") or "1").lower() not in ("0", "false", "no")
    if not research_enabled:
        logger.info("Research scheduler DISABLED via ARIA_AUTONOMOUS_RESEARCH_ENABLED=0")
    # R-F195 (2026-05-11): start research loop even when LLM is
    # unavailable. The degraded path in researcher.research_and_learn
    # still fetches RSS + ingests into RAG; only the LLM-driven fact
    # extraction is skipped. Air-gap independence depends on this.
    if research_enabled:
        async def _research_loop():
            # 15-minute startup delay (was 5 min). Staggered far from
            # self-improve (10min) and student (20/25min) to prevent
            # thundering herd on Anthropic tier-1 rate limits.
            await asyncio.sleep(900)
            while True:
                # R-F1395: check engine pause flag before each cycle
                from .autonomous.safety import is_engine_paused as _is_paused
                if await _is_paused():
                    logger.debug("[Research] engine paused — skipping cycle")
                    await asyncio.sleep(1800)
                    continue
                # R-F2239: shed under state_store/loop pressure. Research is a heavy
                # LLM+absorb loop that contends with serving on the single-process
                # brain; when the load governor signals pressure, yield this cycle so
                # autonomy can never starve chat/DD (mirrors the engine tick,
                # engine.py:652). Fail-safe: a probe error reports no pressure.
                try:
                    from .intel import load_governor as _lg
                    if _lg.should_shed():
                        logger.debug("[Research] load-shed — deferring cycle to protect serving")
                        await asyncio.sleep(1800)
                        continue
                except Exception as _e672:
                    logger.debug("[R-F672] suppressed in lifespan (from .intel import load_governor as _lg): %s", _e672)
                # Tag as BACKGROUND priority so the rate limiter yields
                # to interactive chat when Anthropic quota is tight.
                from .llm.rate_limiter import set_priority, reset_priority, Priority
                _p = set_priority(Priority.BACKGROUND)
                # Attribute every LLM call this loop fires to the
                # "autonomous_research" feature so /cost separates it
                # from interactive chat and on-demand research_tasks.
                _t = cost_tracker.set_feature("autonomous_research")
                try:
                    await _tick_heartbeat("research_engine", "RSS feeds → fact extraction → hypothesis validation")
                    logger.info("[Research] Starting autonomous research cycle...")
                    result = await research_and_learn(getattr(app.state, "llm_provider", None))
                    await _wire_agent_success(
                        "research_engine",
                        f"Research cycle: {result.get('facts_learned', 0)} facts, "
                        f"{result.get('hypotheses_generated', 0)} hypotheses",
                    )
                    logger.info(
                        f"[Research] Complete: {result.get('facts_learned', 0)} facts, "
                        f"{result.get('hypotheses_generated', 0)} hypotheses"
                    )
                    # Auto-validate open hypotheses (every other cycle).
                    # F27 fix 2026-04-27: was reading the wrong dict key
                    # but hypotheses are stored under key "hypothesis" (see
                    # researcher._process_analysis). Empty-string lookup
                    # then triggered the substring-match-anything fallback
                    # in validate_hypothesis, so we re-validated the same
                    # hypothesis #0 three times per cycle for months.
                    # Also sort by created_at so we work the oldest-OPEN
                    # backlog first — those have had the most time for new
                    # evidence to land.
                    # F78d 2026-04-29: log was "Validated 3/175" which
                    # read like a 1.7% success rate. Reality: 175 = OPEN
                    # backlog total, 3 = per-cycle quota; flipped (verdict
                    # reached) is a separate axis. Split the three so the
                    # log doesn't mislead future log-readers.
                    # R-F32 2026-05-03: bumped quota 3→8. With 5-attempt
                    # drain cap, picks=3 gave 0.6 drained/cycle vs ~1.0
                    # generated/cycle — backlog grew +20/day (live
                    # observation 2026-05-03 09:00:56: 109 OPEN, 0/3
                    # verdicts). picks=8 gives 1.6/cycle drain, net
                    # -0.6/cycle so the backlog actually clears.
                    processed = 0
                    flipped = 0
                    # R-F205 (2026-05-11) — guard hypothesis validation against
                    # the R-F195 no-LLM degraded path. Without this, every
                    # validate_hypothesis call returns {"error": "..."} (no
                    # new_status), the `!= "OPEN"` check evaluates True (None
                    # != "OPEN"), `flipped` increments for all 8 picks, and
                    # the operator log shows phantom verdicts. Skip the whole
                    # validation pass when LLM is absent — hypotheses stay
                    # OPEN until the next cycle with a working LLM.
                    llm = getattr(app.state, "llm_provider", None)  # R-F2448: was unbound here → NameError (loop reads app.state.llm_provider elsewhere)
                    _llm_ok = bool(llm and getattr(llm, "is_configured", False))
                    if not _llm_ok:
                        logger.info(
                            "[Research] LLM unavailable — skipping hypothesis "
                            "validation pass (R-F205)"
                        )
                    try:
                        if _llm_ok:
                            hypotheses = await get_hypotheses()
                            open_hyps = [h for h in hypotheses if h.get("status") == "OPEN"]
                            open_hyps.sort(key=lambda h: h.get("created_at") or "")
                            for h in open_hyps[:8]:
                                hyp_text = h.get("hypothesis", "")
                                if not hyp_text:
                                    continue
                                vr = await validate_hypothesis(llm, hyp_text)
                                processed += 1
                                if vr.get("new_status") != "OPEN":
                                    flipped += 1
                                    logger.info("[Research] Hypothesis %s: %s → %s",
                                                hyp_text[:50],
                                                "OPEN", vr.get("new_status"))
                            if open_hyps:
                                logger.info(
                                    "[Research] Hypothesis validation: %d processed this cycle, "
                                    "%d reached a verdict, %d still OPEN in backlog",
                                    processed, flipped,
                                    max(0, len(open_hyps) - flipped),
                                )
                    except Exception as e:
                        logger.warning("[Research] Hypothesis validation failed (%d processed before error): %s",
                                       processed, e)
                except Exception as e:
                    await _wire_agent_failure("research_engine", f"Cycle failed: {e}")
                    logger.warning(f"[Research] Cycle failed: {e}")
                finally:
                    cost_tracker.reset_feature(_t)
                    reset_priority(_p)
                await asyncio.sleep(30 * 60)  # Every 30 minutes

        research_task = _singleton_task(_research_loop, "research_loop")  # R-F2073 singleton
        logger.info("Research scheduler started (every 30min)")

    # ── R-F1207/R-F1209: Register all background loops in the agent registry ─────
    # Every autonomous loop registers itself so the multi-agent awareness
    # protocol (R-F1160) can see who's running, what they're doing, and
    # detect stale/dead agents. Registration is best-effort (non-fatal).
    # R-F1209: each loop also ticks its heartbeat every iteration so the
    # registry knows the agent is alive and working.
    async def _register_agent(
        agent_id: str, agent_type: str, task: str,
        contract: Any = None,
    ) -> None:
        """Register an agent in the registry.

        R-F1448: accepts an optional AgentContract. When provided, the
        contract is registered alongside the agent and validated by
        self_healing.
        """
        try:
            from .intel.agent_registry import AgentRegistry
            _reg = AgentRegistry()
            await _reg.register(agent_id, agent_type, current_task=task, contract=contract)
        except Exception:
            logger.warning("R-F672: agent register failed for %s", agent_id)

    async def _tick_heartbeat(agent_id: str, current_task: str = "") -> None:
        """Tick an agent's heartbeat in the registry. Best-effort, non-fatal."""
        try:
            from .intel.agent_registry import AgentRegistry
            _reg = AgentRegistry()
            await _reg.tick_heartbeat(agent_id, current_task=current_task or None)
        except Exception:
            logger.warning("R-F672: agent heartbeat failed for %s", agent_id)

    async def _wire_agent_success(agent_id: str, summary: str) -> None:
        """Wire an agent's successful cycle to the brain. Best-effort."""
        try:
            from .intel.engine_wiring import wire_success
            wire_success(
                module=agent_id,
                summary=summary[:300],
                source_id=f"agent:{agent_id}",
            )
        except Exception:
            logger.warning("R-F672: agent wire_success failed for %s", agent_id)

    async def _wire_agent_failure(agent_id: str, detail: str) -> None:
        """Wire an agent's failed cycle to the brain. Best-effort."""
        try:
            from .intel.engine_wiring import wire_failure
            wire_failure(
                module=agent_id,
                detail=detail[:600],
                gap_type="agent_cycle_failure",
                source=f"agent:{agent_id}",
            )
        except Exception:
            logger.warning("R-F672: agent wire_failure failed for %s", agent_id)

    # Register research engine
    if research_enabled:
        _bg_task(asyncio.create_task(_register_agent(
            "research_engine", "autonomous_research",
            "RSS feeds → fact extraction → hypothesis validation (every 30min)",
        ), name="register_agent_research_engine"))

    # Register self-improvement engine
    # R-F2208: register UNCONDITIONALLY. The loop (below) now re-checks the LLM
    # per-cycle, so the agent must be known to the registry even if the provider
    # wasn't configured at the instant lifespan ran (resilience-layer init race).
    _bg_task(asyncio.create_task(_register_agent(
        "self_improve", "autonomous_self_improve",
        "Error-ledger analysis → bug detection → auto-fix → auto-deploy (every 2h)",
    ), name="register_agent_self_improve"))

    # Register student loops
    _bg_task(asyncio.create_task(_register_agent(
        "student_quiz", "student_brain",
        "Self-quiz on weak topics, mastery tracking (every 3h)",
    ), name="register_agent_student_quiz"))
    _bg_task(asyncio.create_task(_register_agent(
        "student_reading", "student_brain",
        "Study articles on weak topics (every 6h)",
    ), name="register_agent_student_reading"))
    _bg_task(asyncio.create_task(_register_agent(
        "library_consolidation", "student_brain",
        "Archive stale reasoning cases (daily)",
    ), name="register_agent_library_consolidation"))
    # R-F3916 — regional_snapshot was registered as an agent (R-F2957) with NO
    # contract, so test_rf1580's invariant went red on main. The invariant is worth
    # keeping: a contract is what makes an agent's expected inputs, outputs and
    # failure modes observable, and an uncontracted agent is unobservable by
    # definition. Written from what the loop ACTUALLY does (main.py
    # `_regional_snapshot_loop`), not from the one-line registration blurb.
    _regional_snapshot_contract = AgentContract(
        agent_id="regional_snapshot",
        version="1.0.0",
        directives=[
            "Record a timestamped regional-mastery snapshot (floor / mean / cells>=0.70 / per-cell) to a bounded ring, 4x/day",
            "Drive the brier (topic-mastery) snapshot, which has no other periodic caller",
            "Force-flush deferred regional mastery so a chat-only update cannot sit unwritten (R-F2963)",
            "Skip the cycle while the engine is paused, and defer under load-shed rather than contend with chat/DD",
            "First snapshot 35 min after boot, so the heatmap is warm rather than empty",
        ],
        inputs=["Regional mastery heatmap", "Topic mastery (brier)", "load_governor pressure", "engine pause flag"],
        outputs=["Regional mastery time-series ring", "Brier snapshot", "regional_snapshot heartbeat"],
        error_modes=[
            "store_unreachable - snapshot skipped, ring keeps prior history, never truncated",
            "engine_paused - cycle skipped by design, not a failure",
            "load_shed - cycle deferred one hour, not a failure",
        ],
        # C-38 (R-F3931) — EMPTY, deliberately. The first draft declared
        # dependencies=["student"], but no AgentContract with agent_id="student"
        # exists anywhere in the tree, and `ContractRegistry.validate_contract`
        # (agent_contract.py:497-505) appends a `dependency_no_contract` violation
        # for every unresolvable name and LPUSHes it on EVERY validation pass — a
        # permanent, unfixable violation accumulating forever. `dependencies` names
        # OTHER CONTRACTED AGENTS, not the modules a loop imports.
        dependencies=[],
        check_interval_s=21600,   # 6h — matches the loop's 4x/day cadence
        critical=False,
    )

    # R-F2957 — regional-mastery compounding snapshot (gate #2 observability)
    _bg_task(asyncio.create_task(_register_agent(
        "regional_snapshot", "student_brain",
        "Regional mastery time-series snapshot + brier snapshot (4×/day)",
        contract=_regional_snapshot_contract,   # R-F3916
    ), name="register_agent_regional_snapshot"))

    # Register proactive watch
    _bg_task(asyncio.create_task(_register_agent(
        "proactive_watch", "proactive_engine",
        "Daily briefing trigger + mastery prep (hourly)",
    ), name="register_agent_proactive_watch"))

    # Register weekly report
    _bg_task(asyncio.create_task(_register_agent(
        "weekly_report", "reporting_engine",
        "Weekly learning report (Monday 06-08 UTC)",
    ), name="register_agent_weekly_report"))

    # Register watchlist re-screen
    _bg_task(asyncio.create_task(_register_agent(
        "watchlist_rescreen", "dd_engine",
        "Re-screen DD watchlist entities against sanctions/PEP (daily)",
    ), name="register_agent_watchlist_rescreen"))

    # Register tender monitor
    _bg_task(asyncio.create_task(_register_agent(
        "tender_monitor", "procurement_engine",
        "Crawl defence procurement portals (every 6h)",
    ), name="register_agent_tender_monitor"))

    # R-F1282: web_crawler registration removed — the UniversalWebCrawler
    # class is only used on-demand from company_investigator (and those
    # calls were broken — they called module-level functions that don't
    # exist). No background loop was ever started. If a background crawl
    # loop is needed in future, add it properly with wiring.

    # Register Web Integrity Agent (started below)
    # R-F1448: proof contract — defines directives, inputs, outputs, error modes
    _web_integrity_contract = AgentContract(
        agent_id="web_integrity",
        version="1.0.0",
        directives=[
            "Probe all configured endpoints each cycle",
            "Report integrity results to brain via wire_success/wire_failure",
            "Never block the event loop — all probes use async httpx with timeouts",
        ],
        inputs=["Endpoint list (WEB_ENDPOINTS + _WEB_ENDPOINTS_PUBLIC)"],
        outputs=["Integrity report (passed/failed counts) to brain"],
        error_modes=[
            "endpoint_unreachable — log and continue, never crash the loop",
            "401/403 on auth-gated endpoints — EXPECTED, not a failure (R-F1439)",
            "self_probe_without_bearer_token — expected for public endpoints",
        ],
        dependencies=[],
        check_interval_s=60,
        critical=False,
    )

    # R-F1583: contract for autonomous scheduler
    _autonomous_scheduler_contract = AgentContract(
        agent_id="autonomous_scheduler",
        version="1.0.0",
        directives=[
            "Run DD trigger monitor every 5 min",
            "Scan for capability gaps every 15 min and feed to ARIACoder",
            "Run self-diagnostics every hour",
            "Run adversarial suite every 3 days",
            "Drain Claude<->ARIA collaboration bridge every 2 min",
            "Wire both success and failure to the brain",
        ],
        inputs=["Redis state store", "GapDetector", "ARIACoder", "collab_bridge"],
        outputs=["DD triggers fired", "Gaps fixed", "Diagnostics report", "Adversarial score"],
        error_modes=[
            "gap_detector_unavailable - skip cycle, log warning",
            "collab_bridge_unreachable - skip drain, log debug",
            "adversarial_suite_failure - log and continue",
        ],
        dependencies=["self_healing"],
        check_interval_s=120,
        critical=False,
    )

    # R-F1583: contract for wiring monitor
    _wiring_monitor_contract = AgentContract(
        agent_id="wiring_monitor",
        version="1.0.0",
        directives=[
            "Audit wire_success/wire_failure balance across all intel modules every hour",
            "Probe compliance screeners with malformed input to verify crash visibility",
            "Check WA connection health via capability_gaps signals",
            "Test brain signal path integrity end-to-end",
            "Check self-coding loop health - staged queue drain rate",
            "Wire both success and failure to the brain",
        ],
        inputs=["Intel module source files", "Redis state store", "capability_gaps"],
        outputs=["Wire balance report", "Compliance probe results", "Composite health status"],
        error_modes=[
            "redis_unreachable - skip persistence, still run checks",
            "module_import_failure - skip module, continue with rest",
        ],
        dependencies=[],
        check_interval_s=3600,
        critical=False,
    )

    _bg_task(asyncio.create_task(_register_agent(
        "web_integrity", "monitoring",
        "24/7 endpoint monitoring, input/output validation, error pattern detection",
        contract=_web_integrity_contract,
    ), name="register_agent_web_integrity"))

    # R-F1554: register contracts for all background agents
    _research_contract = AgentContract(
        agent_id="research_engine",
        version="1.0.0",
        directives=[
            "Extract facts from RSS feeds every 30min",
            "Validate hypotheses against existing knowledge",
            "Wire both success and failure to the brain",
        ],
        inputs=["RSS feed URLs", "LLM provider"],
        outputs=["New facts", "Validated hypotheses"],
        error_modes=["feed_unreachable", "llm_unavailable", "parse_failure"],
        dependencies=[],
        check_interval_s=1800,
        critical=False,
    )
    _self_improve_contract = AgentContract(
        agent_id="self_improve",
        version="1.0.0",
        directives=[
            "Analyse error ledger for recurring bugs",
            "Generate and stage code fixes for auto-fixable errors",
            "Auto-deploy fixes when confidence is high",
            "Wire both success and failure to the brain",
        ],
        inputs=["Error ledger", "LLM provider", "MODIFIABLE_FILES list"],
        outputs=["Staged improvements", "Auto-deployed fixes"],
        error_modes=["llm_unavailable", "no_fixable_errors", "deploy_failure"],
        dependencies=[],
        check_interval_s=7200,
        critical=False,
    )
    _student_quiz_contract = AgentContract(
        agent_id="student_quiz",
        version="1.0.0",
        directives=[
            "Self-quiz on weak topics every 3h",
            "Track mastery scores over time",
            "Wire both success and failure to the brain",
        ],
        inputs=["Reasoning library", "Mastery tracker"],
        outputs=["Quiz scores", "Mastery deltas"],
        error_modes=["empty_library", "llm_unavailable", "no_weak_topics"],
        dependencies=[],
        check_interval_s=10800,
        critical=False,
    )
    _student_reading_contract = AgentContract(
        agent_id="student_reading",
        version="1.0.0",
        directives=[
            "Study articles on weak topics every 6h",
            "Extract new facts from reading material",
            "Wire both success and failure to the brain",
        ],
        inputs=["LLM provider", "Weak topic list", "Article sources"],
        outputs=["New facts", "Mastery improvements"],
        error_modes=["llm_unavailable", "no_articles_found", "parse_failure"],
        dependencies=[],
        check_interval_s=21600,
        critical=False,
    )
    _library_consolidation_contract = AgentContract(
        agent_id="library_consolidation",
        version="1.0.0",
        directives=[
            "Archive stale reasoning cases daily",
            "Preserve cases instead of deleting",
            "Wire both success and failure to the brain",
        ],
        inputs=["Reasoning library index"],
        outputs=["Archived cases", "Remaining active cases"],
        error_modes=["redis_unreachable", "empty_index"],
        dependencies=[],
        check_interval_s=86400,
        critical=False,
    )
    _proactive_watch_contract = AgentContract(
        agent_id="proactive_watch",
        version="1.0.0",
        directives=[
            "Check daily briefing trigger every hour",
            "Flag weak topics for mastery prep",
            "Wire both success and failure to the brain",
        ],
        inputs=["Current data", "Mastery tracker"],
        outputs=["Briefing trigger", "Weak topic flags"],
        error_modes=["data_unavailable", "mastery_unreachable"],
        dependencies=[],
        check_interval_s=3600,
        critical=False,
    )
    _weekly_report_contract = AgentContract(
        agent_id="weekly_report",
        version="1.0.0",
        directives=[
            "Generate weekly learning report on Monday 06-08 UTC",
            "Aggregate new facts, mastery changes, capability gaps",
            "Wire both success and failure to the brain",
        ],
        inputs=["LLM provider", "Knowledge store", "Mastery tracker", "Capability gaps"],
        outputs=["Weekly report"],
        error_modes=["llm_unavailable", "no_data_to_report"],
        dependencies=[],
        check_interval_s=3600,
        critical=False,
    )
    _watchlist_rescreen_contract = AgentContract(
        agent_id="watchlist_rescreen",
        version="1.0.0",
        directives=[
            "Re-screen DD watchlist entities against sanctions/PEP daily",
            "Detect status changes and push alerts",
            "Wire both success and failure to the brain",
        ],
        inputs=["DD watchlist", "Sanctions lists", "PEP lists"],
        outputs=["Status changes", "Alerts"],
        error_modes=["watchlist_unreachable", "sanctions_unreachable", "no_changes"],
        dependencies=[],
        check_interval_s=86400,
        critical=False,
    )
    _tender_monitor_contract = AgentContract(
        agent_id="tender_monitor",
        version="1.0.0",
        directives=[
            "Crawl defence procurement portals every 6h",
            "Score tenders by relevance keywords and CPV codes",
            "Wire both success and failure to the brain",
        ],
        inputs=["Procurement portal URLs", "Keyword/CPV scoring rules"],
        outputs=["New tenders", "Tender scores"],
        error_modes=["portal_unreachable", "parse_failure", "no_new_tenders"],
        dependencies=[],
        check_interval_s=21600,
        critical=False,
    )
    _self_healing_contract = AgentContract(
        agent_id="self_healing",
        version="1.0.0",
        directives=[
            "Run health checks on all subsystems",
            "Detect and repair circuit breaker trips",
            "Auto-recover from known failure modes",
            "Wire both success and failure to the brain",
        ],
        inputs=["Circuit breaker registry", "Agent registry", "Contract registry"],
        outputs=["Health reports", "Auto-recovery actions"],
        error_modes=["registry_unreachable", "no_failures_to_repair"],
        dependencies=[],
        check_interval_s=3600,
        critical=True,
    )

    # R-F1561: ACTUALLY register the contracts defined above. R-F1554 created
    # the AgentContract objects but never registered them — they were dead local
    # variables (only web_integrity's contract was wired via the _register_agent
    # contract= path). Register them all on CONTRACT_REGISTRY so every background
    # agent has a binding, queryable contract (R-F1212), not just web_integrity.
    async def _register_all_contracts() -> None:
        from .intel.agent_contract import CONTRACT_REGISTRY as _CR
        for _c in (
            _research_contract, _self_improve_contract, _student_quiz_contract,
            _student_reading_contract, _library_consolidation_contract,
            _proactive_watch_contract, _weekly_report_contract,
            _watchlist_rescreen_contract, _tender_monitor_contract,
            _self_healing_contract,
            _web_integrity_contract,
            _autonomous_scheduler_contract,
            _wiring_monitor_contract,
            _regional_snapshot_contract,   # R-F3916
        ):
            try:
                await _CR.register_contract(_c)
            except Exception:
                logger.warning(
                    "R-F1561: contract registration failed for %s",
                    getattr(_c, "agent_id", "?"),
                )
    _bg_task(asyncio.create_task(_register_all_contracts(), name="register_all_contracts"))

    # Register self-healing with its binding contract
    _bg_task(asyncio.create_task(_register_agent(
        "self_healing", "infrastructure",
        "Health checks, circuit breakers, auto-recovery, ecosystem repair",
        contract=_self_healing_contract,
    ), name="register_agent_self_healing"))

    # R-F1574: register autonomous scheduler agent
    _bg_task(asyncio.create_task(_register_agent(
        "autonomous_scheduler", "scheduler",
        "DD trigger monitor, gap fixing, self-diagnostics, adversarial tests (scheduled)",
    ), name="register_agent_autonomous_scheduler"))

    # R-F1574: register wiring monitor agent
    _bg_task(asyncio.create_task(_register_agent(
        "wiring_monitor", "monitoring",
        "Wire balance audit, compliance screener probe, brain signal path integrity (hourly)",
    ), name="register_agent_wiring_monitor"))

    # Start autonomous self-improvement loop (every 2 hours)
    self_improve_task = None
    # R-F2208: always START the loop; the LLM guard moved INSIDE (per-cycle).
    # Previously a boot-time is_configured==False (LLM resilience init race) left
    # self_improve DARK for the entire process life — heartbeat 32h stale on a 2h
    # cycle while chat worked fine. The loop now self-heals when the provider
    # comes up. See R-F2207 (contract monitor) which surfaced this.
    if True:
        async def _self_improve_loop():
            await asyncio.sleep(600)  # Wait 10 min after startup (staggered from research at 15min)
            while True:
                # R-F1395: check engine pause flag before each cycle
                from .autonomous.safety import is_engine_paused as _is_paused
                if await _is_paused():
                    logger.debug("[Self-Improve] engine paused — skipping cycle")
                    await asyncio.sleep(7200)
                    continue
                # R-F2208: heartbeat EVERY iteration so the registry knows the
                # LOOP is alive even while it waits for the LLM — distinguishes
                # "loop dead" (the bug this fixes) from "loop idle, provider down".
                await _tick_heartbeat("self_improve", "Error-ledger analysis → bug detection → auto-fix")
                # R-F2239: shed under state_store/loop pressure — self_improve reads
                # the error ledger + runs LLM + absorbs (heavy). Yield the cycle when
                # the load governor signals pressure (mirrors engine.py:652). Heartbeat
                # already ticked above, so shedding never looks like a dead loop.
                try:
                    from .intel import load_governor as _lg
                    if _lg.should_shed():
                        logger.debug("[Self-Improve] load-shed — deferring cycle to protect serving")
                        await asyncio.sleep(7200)
                        continue
                except Exception as _e672:
                    logger.debug("[R-F672] suppressed in lifespan (from .intel import load_governor as _lg): %s", _e672)
                # R-F2208: re-check the provider per-cycle. It may not have been
                # configured at boot (resilience init race). Self-heal when it
                # comes up rather than staying dark for the whole process life.
                _llm_now = getattr(app.state, "llm_provider", None)
                if not (_llm_now and getattr(_llm_now, "is_configured", False)):
                    logger.info("[Self-Improve] LLM not configured yet — re-check in 30 min")
                    await asyncio.sleep(1800)
                    continue
                from .llm.rate_limiter import set_priority, reset_priority, Priority
                _p = set_priority(Priority.BACKGROUND)
                _t = cost_tracker.set_feature("self_improve")
                try:
                    logger.info("[Self-Improve] Starting autonomous improvement cycle...")
                    result = await self_improve.autonomous_improvement_cycle(_llm_now)
                    await _wire_agent_success(
                        "self_improve",
                        f"Improvement cycle: {result.get('bugs_detected', 0)} bugs, "
                        f"{result.get('auto_deployed', 0)} deployed",
                    )
                    # R-F272 (2026-05-11) — honest cycle log. Operator was
                    # alarmed by "160 errors, 0 bugs" and couldn't tell whether
                    # the 0 meant no real bugs OR that every error was in a
                    # non-MODIFIABLE_FILES path being silently skipped. The
                    # cycle now reports both populations so the operator sees
                    # the actual landscape.
                    modifiable = result.get("errors_in_modifiable_files", {}) or {}
                    external = result.get("errors_in_external_files", {}) or {}
                    mod_sum = sum(modifiable.values())
                    ext_sum = sum(external.values())
                    below_sum = result.get("errors_below_threshold", 0)
                    # R-F361 (2026-05-12): renamed "external" → "out-of-scope"
                    # in the log because every file under the prior label is
                    # in our codebase, just outside the MODIFIABLE_FILES
                    # auto-fix allowlist. Surfaced the third bucket (errors
                    # in below-threshold files) so total = sum-of-three.
                    # Underlying dict keys preserved for backward compat.
                    top_external = sorted(external.items(), key=lambda kv: -kv[1])[:3]
                    top_external_str = ", ".join(f"{p}={n}" for p, n in top_external) or "none"
                    logger.info(
                        "[Self-Improve] Cycle complete: %d errors total "
                        "(%d auto-fixable · %d out-of-scope · %d below-threshold), "
                        "%d bugs detected, %d auto-deployed. "
                        "Top out-of-scope offenders: %s",
                        result.get("errors_analysed", 0),
                        mod_sum,
                        ext_sum,
                        below_sum,
                        result.get("bugs_detected", 0),
                        result.get("auto_deployed", 0),
                        top_external_str,
                    )
                except Exception as e:
                    await _wire_agent_failure("self_improve", f"Cycle failed: {e}")
                    logger.warning("[Self-Improve] Cycle failed: %s", e)
                finally:
                    cost_tracker.reset_feature(_t)
                    reset_priority(_p)
                await asyncio.sleep(2 * 3600)  # Every 2 hours

        self_improve_task = _singleton_task(_self_improve_loop, "self_improve_loop")  # R-F2073 singleton
        logger.info("Self-improvement scheduler started (every 2h)")

    # ── ARIA STUDENT LOOPS ──────────────────────────────────────────────
    # Active learning behaviours: self-quiz, reading sessions, library
    # consolidation. These run independently of conversation traffic so
    # ARIA studies during idle time — like a real student. Each loop is
    # safe to run with or without an LLM (the student doesn't depend on
    # the cloud teacher; she just learns faster when one is available).

    quiz_task = None
    reading_task = None
    library_consolidate_task = None
    runpod_sched_task = None  # R-F1335

    async def _quiz_loop():
        # First quiz happens 20 min after startup (staggered from research
        # at 15min and self-improve at 10min to prevent rate limit storms).
        await asyncio.sleep(1200)
        while True:
            # R-F1395: check engine pause flag before each cycle
            from .autonomous.safety import is_engine_paused as _is_paused
            if await _is_paused():
                logger.debug("[Quiz] engine paused — skipping cycle")
                await asyncio.sleep(10800)
                continue
            from .llm.rate_limiter import set_priority, reset_priority, Priority
            _p = set_priority(Priority.BACKGROUND)
            _t = cost_tracker.set_feature("student_quiz")
            try:
                await _tick_heartbeat("student_quiz", "Self-quiz on weak topics")
                result = await student.self_quiz(num_questions=5)
                await _wire_agent_success(
                    "student_quiz",
                    f"Quiz: {result.get('quizzed', 0)} questions, "
                    f"score {result.get('score', 0):.2f}",
                )
                # R-F291: when quizzed==0 the previous log was diagnostically
                # blind. Surface library_size + orphan + skip counts so the
                # silent-skip root cause is visible on the next sweep.
                if result.get("quizzed", 0) == 0:
                    logger.info(
                        "[Student] Quiz complete: 0/0 passed (score 0.00) — "
                        "note=%s library_size=%d sample=%d orphans=%d healed=%d "
                        "no_question=%d no_response=%d",
                        result.get("note", "all_sample_fell_through"),
                        result.get("library_size", 0),
                        result.get("sample_size", 0),
                        result.get("orphans", 0),
                        result.get("orphans_healed", 0),
                        result.get("skipped_no_question", 0),
                        result.get("skipped_no_response", 0),
                    )
                else:
                    logger.info(
                        "[Student] Quiz complete: %d/%d passed (score %.2f)",
                        result.get("passed", 0),
                        result.get("quizzed", 0),
                        result.get("score", 0),
                    )
            except Exception as e:
                await _wire_agent_failure("student_quiz", f"Quiz failed: {e}")
                logger.warning("[Student] Quiz failed: %s", e)
            finally:
                cost_tracker.reset_feature(_t)
                reset_priority(_p)
            await asyncio.sleep(3 * 3600)  # Every 3 hours

    async def _reading_loop():
        # First reading session 25 min after startup (last in the stagger
        # sequence: self-improve 10m → research 15m → quiz 20m → reading 25m).
        await asyncio.sleep(1500)
        while True:
            # R-F1395: check engine pause flag before each cycle
            from .autonomous.safety import is_engine_paused as _is_paused
            if await _is_paused():
                logger.debug("[Reading] engine paused — skipping cycle")
                await asyncio.sleep(21600)
                continue
            from .llm.rate_limiter import set_priority, reset_priority, Priority
            _p = set_priority(Priority.BACKGROUND)
            _t = cost_tracker.set_feature("student_reading")
            try:
                await _tick_heartbeat("student_reading", "Study articles on weak topics")
                result = await student.reading_session(llm=getattr(app.state, "llm_provider", None), num_articles=4)
                await _wire_agent_success(
                    "student_reading",
                    f"Reading: {result.get('articles_read', 0)} articles on "
                    f"{result.get('weak_topics_studied', [])}",
                )
                logger.info(
                    "[Student] Reading session: %d articles studied on %s",
                    result.get("articles_read", 0),
                    result.get("weak_topics_studied", []),
                )
            except Exception as e:
                await _wire_agent_failure("student_reading", f"Reading session failed: {e}")
                logger.warning("[Student] Reading session failed: %s", e)
            finally:
                cost_tracker.reset_feature(_t)
                reset_priority(_p)
            # R-F2363 — Phase A gate #2 accelerator. The regional heatmap floor closes only
            # as the weakest topic×region cell accumulates ~17 READ-GROUNDED observations
            # (weight=0.3/alpha=0.03, credited only when the fetched text actually mentions
            # the region — student.py:1099/1113). At the old 6h cadence that's ~4+ days. A
            # shorter cadence = MORE genuine region-specific reading = legitimately faster
            # mastery — NOT gaming (alpha/weight/read-grounding are UNCHANGED). Runs at
            # Priority.BACKGROUND + cost_free, honours the R-F1395 pause flag. Env-tunable so
            # the operator can dial load; default 2.5h.
            _reading_interval_s = _env_float("ARIA_READING_INTERVAL_S", 9000.0)  # R-F2448: was bare `os` (undefined in main.py) → NameError
            await asyncio.sleep(max(600.0, _reading_interval_s))

    async def _library_consolidate_loop():
        # Daily housekeeping — prune stale low-quality cases
        await asyncio.sleep(3600)
        while True:
            # R-F1395: check engine pause flag before each cycle
            from .autonomous.safety import is_engine_paused as _is_paused
            if await _is_paused():
                logger.debug("[Library] engine paused — skipping cycle")
                await asyncio.sleep(86400)
                continue
            try:
                await _tick_heartbeat("library_consolidation", "Archive stale reasoning cases")
                result = await reasoning_library.consolidate()
                await _wire_agent_success(
                    "library_consolidation",
                    f"Consolidated: archived {result.get('archived', 0)}, "
                    f"{result.get('remaining', 0)} remaining",
                )
                # R-F242 (2026-05-13): log archived + missing distinctly.
                # Pre-R-F242 the log said "pruned N" but consolidate now
                # archives (preserves) cases instead of deleting. Surface
                # the honest counts so the daily log doesn't imply data
                # was lost.
                logger.info(
                    "[Student] Library consolidated: archived %d (preserved), "
                    "missing %d (Redis data lost), remaining %d in active index",
                    result.get("archived", 0),
                    result.get("missing", 0),
                    result.get("remaining", 0),
                )
            except Exception as e:
                await _wire_agent_failure("library_consolidation", f"Consolidate failed: {e}")
                logger.warning("[Student] Library consolidate failed: %s", e)
            await asyncio.sleep(24 * 3600)  # Daily

    async def _regional_snapshot_loop():
        # R-F2957 — Phase A gate #2 COMPOUNDING observability. Regional mastery
        # was a current-value snapshot with no history, so "is she compounding?"
        # was unanswerable. This loop records a timestamped regional-mastery
        # snapshot (floor / mean / cells≥0.70 / per-cell) to a ring, AND drives
        # the brier (topic-mastery) snapshot — which, pre-R-F2957, had NO periodic
        # caller and was itself dark. Cheap: one heatmap read + one small redis
        # write; runs 4×/day. First snapshot 35 min after boot (after the reading
        # loop's 25-min first tick so it captures a warm heatmap).
        await asyncio.sleep(2100)
        while True:
            from .autonomous.safety import is_engine_paused as _is_paused
            if await _is_paused():
                logger.debug("[RegionalSnapshot] engine paused — skipping cycle")
                await asyncio.sleep(21600)
                continue
            # Yield under serving pressure (mirrors the research loop). Snapshot is
            # cheap, but never contend with chat/DD on the single-process brain.
            try:
                from .intel import load_governor as _lg
                if _lg.should_shed():
                    logger.debug("[RegionalSnapshot] load-shed — deferring cycle")
                    await asyncio.sleep(3600)
                    continue
            except Exception as _e672:
                logger.debug("[R-F672] suppressed in lifespan (from .intel import load_governor as _lg): %s", _e672)
            try:
                await _tick_heartbeat("regional_snapshot", "Regional + topic mastery snapshot")
                # R-F2963 (C0) — backstop force-flush so a chat-only regional update
                # between reading sessions can't sit deferred indefinitely.
                try:
                    from .intel import student as _stu_flush
                    await _stu_flush.flush_regional()
                except Exception as _e672:
                    logger.debug("[R-F672] suppressed in lifespan (from .intel import student as _stu_flush): %s", _e672)
                from .intel import regional_drift_monitor as _rdm
                snap = await _rdm.snapshot_regional()
                # Un-dark the brier (topic) snapshot in the same loop — no second scheduler.
                try:
                    from .intel import brier_drift_monitor as _bdm
                    await _bdm.snapshot_mastery()
                except Exception as _be:
                    logger.debug("[RegionalSnapshot] brier snapshot failed: %s", _be)
                # R-F2960 (B2) — score-stagnation: flag below-floor cells flat over the
                # window as stalled_cell gaps (starved vs grade-failing). Non-fatal.
                try:
                    _stall = await _rdm.record_stalled_gaps(window_hours=168)
                    if _stall.get("stalled"):
                        logger.info(
                            "[RegionalSnapshot] %d stalled cells (%d starved, %d grade-failing)",
                            _stall.get("stalled"), _stall.get("starved", 0), _stall.get("grade_failing", 0))
                except Exception as _se:
                    logger.debug("[RegionalSnapshot] stall detection failed: %s", _se)
                # R-F2980 (review F8): only wire SUCCESS if the snapshot actually persisted.
                if snap.get("persisted"):
                    await _wire_agent_success(
                        "regional_snapshot",
                        f"Regional snapshot: floor={snap.get('floor')}, "
                        f"cells≥0.70={snap.get('count_ge_070')}/{snap.get('cell_count')}",
                    )
                elif snap.get("skipped") != "no_cells":
                    await _wire_agent_failure("regional_snapshot", "snapshot write did not persist")
                logger.info(
                    "[RegionalSnapshot] floor=%s mean=%s cells>=0.70=%s/%s",
                    snap.get("floor"), snap.get("mean"),
                    snap.get("count_ge_070"), snap.get("cell_count"),
                )
            except Exception as e:
                await _wire_agent_failure("regional_snapshot", f"Regional snapshot failed: {e}")
                logger.warning("[RegionalSnapshot] failed: %s", e)
            await asyncio.sleep(6 * 3600)  # 4×/day

    quiz_task = _singleton_task(_quiz_loop, "quiz_loop")  # R-F2073 singleton
    reading_task = _singleton_task(_reading_loop, "reading_loop")  # R-F2073 singleton
    library_consolidate_task = _singleton_task(_library_consolidate_loop, "library_consolidate_loop")  # R-F2073 singleton
    regional_snapshot_task = _singleton_task(_regional_snapshot_loop, "regional_snapshot_loop")  # R-F2957 singleton
    logger.info("Student loops started: self-quiz (3h), reading (6h), library consolidate (24h), regional snapshot (6h)")

    # ── R-F3580 — R-F3577 IS REVERTED HERE. It wired a reader to a DEAD PIPE. ──
    #
    # R-F3577 registered intel/brain_signal_consumer.py to poll the Redis list
    # crucix:brain:incoming_signals, on the premise that the Node web tier writes
    # to it. IT DOES NOT, and has not for a long time. A repo-wide grep for that
    # key finds ONLY: a stale COMMENT in apis/briefing.mjs (now corrected), the
    # consumer's own constant, and R-F3577's own code and test. No writer exists.
    #
    # What actually carries web-tier signals is HTTP: pushSignalsToBrain() POSTs
    # to /brain/signal/bulk (routes/aria.py:17067, R-F2505), which absorbs them
    # as cross_tier:{sig_type}. Live-verified on /api/aria/brain/stats — 120
    # signals under cross_tier:crucix_briefing_signal, last seen 0.1h ago. The
    # cross-tier path was never dark; only its retired transport was.
    #
    # The consumer module is DELETED rather than left registered: a 60s loop
    # polling a key nothing writes is pure cost, and leaving it in place is what
    # made the stale comment look corroborated in the first place.

    # ── RUNPOD SCHEDULER (R-F1335) ──────────────────────────────────────
    # ARIA runs her own GPU reasoning window: pod ON 10:00-18:00
    # Europe/London (her sovereign ARIA-LLM serves as chain primary),
    # pod OFF outside it (DeepSeek takes over via the cooldown chain).
    # Harmless no-op until RUNPOD_API_KEY + ARIA_RUNPOD_POD_ID secrets
    # are set. Loop ticks its own self_restart heartbeat.
    from .intel import runpod_scheduler as _runpod_sched
    runpod_sched_task = _singleton_task(_runpod_sched.scheduler_loop, "runpod_scheduler")  # R-F2073 singleton (starts/stops GPU pods — N schedulers would race)
    logger.info(
        "[R-F1335] RunPod scheduler started (configured=%s)",
        _runpod_sched.configured(),
    )

    # ── MEMORY WAL DRAIN (R-F1342, §7 never forget) ─────────────────────
    # Retries facts that could not be persisted immediately (concurrency
    # cap / store_fact failure) so nothing is ever forgotten. Runs every
    # 5 min; no-op when the WAL is empty. Bounded + single-flight.
    memory_wal_task = None

    async def _memory_wal_drain_loop():
        await asyncio.sleep(180)  # let boot settle
        from .intel import memory_wal as _wal
        from .intel import knowledge as _kn
        while True:
            # R-F1395: check engine pause flag before each cycle
            from .autonomous.safety import is_engine_paused as _is_paused
            if await _is_paused():
                await asyncio.sleep(300)
                continue
            try:
                pending = await asyncio.to_thread(_wal.pending_count)  # R-F1346: off-loop
                if pending:
                    res = await _wal.drain(_kn.store_fact, max_items=500)
                    logger.info("[R-F1342] memory_wal drain: %s", res)
            except Exception as e:
                logger.warning("[R-F1342] memory_wal drain error: %s", e)
                try:  # R-F2256 §21a — a failing WAL drain risks data loss; wire it (was dark)
                    from .intel.engine_wiring import wire_failure
                    wire_failure(module="memory_wal_drain", detail=f"memory_wal drain error: {str(e)[:160]}",
                                 gap_type="engine_failure", source="main:_memory_wal_drain_loop")
                except Exception as _e672:
                    logger.debug("[R-F672] suppressed in lifespan (lifespan guard): %s", _e672)
            await asyncio.sleep(300)

    memory_wal_task = _singleton_task(_memory_wal_drain_loop, "memory_wal_drain")  # R-F2073 singleton (shared WAL — one drainer avoids double-store races)
    logger.info("[R-F1342] memory WAL drain loop started (never-forget retry)")

    # R-F2072 (Tier 0-finish) — PROACTIVE health precompute. The brain is a
    # single-process asyncio app (one event loop); the heavy /api/aria/health +
    # /health/perf aggregations (~21s of Redis/stats/breaker work) must NEVER run
    # on the request path or they tie up the one loop and stall every concurrent
    # request — the "huge CPU but slow" symptom. R-F2063 added a stale-while-
    # revalidate cache, but a refresh still only fires when a request arrives (and
    # the FIRST poll after a cold boot still pays the full compute). This loop is
    # the finish: it warms BOTH caches on a fixed tick so the endpoints only ever
    # READ a precomputed value. PER-PROCESS (each worker has its own in-process
    # cache and serves /health), so it runs on every role — not a singleton.
    async def _health_precompute_loop():
        from .routes.aria import health_check_ep as _hc, health_perf_ep as _hp
        # R-F4211: the first aggregation reads brain stats, neuron metadata,
        # error history, and DD cursors. Live release 3032 proved that firing it
        # at T+10 overlapped 612k-fact hydration, timed out state reads, and then
        # amplified contention by recording a capability gap. Endpoints remain
        # available; only their proactive cache warmer waits for settled state.
        await _await_heavy_graph_ready(app)
        while True:
            for _name, _fn in (("health", _hc), ("health_perf", _hp)):
                try:
                    await _fn.refresh_now()
                except Exception as e:
                    logger.debug("[R-F2072] health precompute (%s) failed: %s", _name, e)
                    # §21a — a precompute that keeps failing means the warm cache is
                    # going stale; surface to the brain (deduped by capability_gaps).
                    try:
                        from .intel.engine_wiring import wire_failure
                        wire_failure(
                            module="health_precompute",
                            detail=f"proactive health precompute '{_name}' failed: {type(e).__name__}: {e}",
                            gap_type="engine_failure",
                            source="main:_health_precompute_loop",
                        )
                    except Exception as _e672:
                        logger.debug("[R-F672] suppressed in lifespan (from .intel.engine_wiring import wire_failure): %s", _e672)
            # R-F2417: piggyback this per-process 20s tick to force-flush any
            # coalesced mastery write (R-F2408). Mastery can be dirtied on ANY
            # worker (per-chat aria_engine updates), so the flush must be
            # per-process (this loop is _bg_task, not _singleton_task) — a
            # singleton loop would never persist a non-engine worker's cache in a
            # quiet period. No-op when ARIA_MASTERY_COALESCE_SAVE is OFF (default)
            # or when nothing is pending; bounds the deferred-write loss window to
            # one tick. Own try so a flush error never affects health precompute.
            try:
                await student.flush_mastery()
            except Exception as _mfe:
                logger.debug("[R-F2417] periodic mastery flush failed: %s", _mfe)
                # §21a — a periodic flush that keeps failing means coalesced
                # mastery writes are silently stranding; surface to the brain.
                try:
                    from .intel.engine_wiring import wire_failure
                    wire_failure(
                        module="mastery_flush",
                        detail=f"periodic mastery flush failed: {type(_mfe).__name__}: {_mfe}",
                        gap_type="engine_failure",
                        source="main:_health_precompute_loop",
                    )
                except Exception as _e672:
                    logger.debug("[R-F672] suppressed in lifespan (from .intel.engine_wiring import wire_failure): %s", _e672)
            await asyncio.sleep(20)   # < the 25s cache TTL so a request never sees a cold/expired entry

    health_precompute_task = _bg_task(asyncio.create_task(_health_precompute_loop(), name="health_precompute"), factory=_health_precompute_loop)
    logger.info("[R-F2072] health precompute loop started (endpoints read-only, never compute on request path)")

    # R-F1979 — GUARDIAN check-in reconcile loop (dead-man's switch). The safety
    # guarantee: a check-in deadline that passes WITHOUT an all-clear fires an
    # alert to the user's trusted circle. Durable (Redis) so a redeploy/restart
    # cannot drop a pending safety timer; idempotent so an alert fires at most
    # once. Delivers via the aria-wa /send hop. Runs every 60s.
    async def _guardian_reconcile_loop():
        await asyncio.sleep(90)
        from .guardian import checkin as _gci
        from .guardian.delivery import wa_send_fn as _wa_send_fn
        # R-F1981 — shared delivery hop (also used by send-as-you + panic).
        _send_fn = _wa_send_fn()

        _guard_cycle = 0
        while True:
            _guard_cycle += 1
            try:
                n = await _gci.reconcile(_send_fn)
                if n:
                    logger.warning("[R-F1979 guardian] fired %d dead-man's-switch alert(s)", n)
                # R-F2256 — §21a: make the dead-man's-switch OBSERVABLE (was DARK). Signal
                # on any alert fired, or a heartbeat every ~10 cycles (~10 min) so a
                # SILENTLY dead guardian is detectable — throttled to avoid spamming the
                # saturation-sensitive state_store every 60s.
                if n or (_guard_cycle % 10 == 1):
                    try:
                        from .intel.engine_wiring import wire_success
                        wire_success(module="guardian_reconcile",
                                     summary=f"guardian reconcile ok ({n or 0} alert(s) fired)",
                                     source_id="main:_guardian_reconcile_loop")
                    except Exception:  # noqa: BLE001 — observability must never break the switch
                        pass
            except Exception as e:
                logger.warning("[R-F1979 guardian] reconcile error: %s", e)
                # R-F2256 — §21a: a FAILING dead-man's-switch is the highest-consequence
                # dark path — wire it so the brain/self-heal sees the guardian is broken.
                try:
                    from .intel.engine_wiring import wire_failure
                    wire_failure(module="guardian_reconcile",
                                 detail=f"guardian reconcile error: {str(e)[:180]}",
                                 gap_type="engine_failure", source="main:_guardian_reconcile_loop")
                except Exception:  # noqa: BLE001
                    pass
            await asyncio.sleep(60)

    guardian_task = _singleton_task(_guardian_reconcile_loop, "guardian_reconcile")  # R-F2073 singleton
    logger.info("[R-F1979] Guardian check-in reconcile loop started (dead-man's switch)")

    # R-F2006 — ENGINE LIVENESS WATCHDOG. A SEPARATE loop (so it survives the
    # engine task dying) that alerts the operator if the autonomous engine goes
    # dark — the exact failure that let the R-F2004 187h news/sweep fire=0 outage
    # go unnoticed (a forgotten pause killed the engine and NOTHING flagged it).
    # Checks every 15 min; one HIGH operator ticket per episode (6h re-alert).
    # The brain_hook circuit breaker has its own per-episode ticket (R-F790), so
    # this watches engine firing only — no duplication.
    async def _engine_liveness_watchdog_loop():
        await asyncio.sleep(360)   # let boot + the engine's 90s startup settle
        import time as _t
        from .autonomous import engine as _eng
        _last_alert = 0.0
        _last_feed_alert = 0.0
        _RE_ALERT_S = 6 * 3600
        while True:
            try:
                status = await _eng.check_engine_liveness()
                if not status.get("healthy"):
                    now = _t.time()
                    if now - _last_alert > _RE_ALERT_S:
                        _last_alert = now
                        problem = status.get("problem") or "engine not healthy"
                        logger.error("[R-F2006 watchdog] ENGINE DARK: %s (tick_age=%ss fire_age=%ss)",
                                     problem, status.get("tick_age_s"), status.get("fire_age_s"))
                        try:
                            from .intel import pending_actions as _pa
                            await _pa.record(
                                promise="Autonomous engine must stay live (real-time ecosystem).",
                                reason=problem,
                                severity="HIGH",
                                source="autonomous",
                                resolver_kind="operator_action",
                                resolver_ref="autonomous_engine_liveness",
                                operator_prompt=(
                                    "ARIA's autonomous engine is DARK: " + problem +
                                    " — check POST /api/aria/autonomous/status; resume/enable as needed."
                                ),
                                metadata={"watchdog": "R-F2006", **{k: status.get(k) for k in
                                          ("tick_age_s", "fire_age_s", "paused", "enabled")}},
                            )
                        except Exception as _pe:
                            logger.warning("[R-F2006 watchdog] alert record failed: %s", _pe)
                        try:
                            from .intel.engine_wiring import wire_failure as _wf
                            _wf(module="autonomous_engine",
                                detail=f"liveness watchdog: {problem}",
                                gap_type="agent_cycle_failure",
                                source="autonomous_engine:watchdog_rf2006")
                        except Exception as _e672:
                            logger.debug("[R-F672] suppressed in lifespan (from .intel.engine_wiring import wire_failure as): %s", _e672)
            except Exception as e:
                logger.warning("[R-F2006 watchdog] error: %s", e)
            # R-F2178: also check EXTERNAL-LIMB heartbeats (aria-wa/web/searxng).
            # A stale limb beat → coder-visible gap so a dark limb is acted on,
            # not discovered only when a user request fails. Own try so a limb
            # check error never affects the engine check above.
            try:
                from .intel import liveness as _lv2178
                await _lv2178.probe_searxng_and_beat()   # R-F2181 — searxng can't self-beat; brain probes it
                _stale_limbs = await _lv2178.check_stale_and_gap()
                if _stale_limbs:
                    logger.warning("[R-F2178 liveness] STALE limbs: %s", ", ".join(_stale_limbs))
            except Exception as _le2178:
                logger.debug("[R-F2178 liveness] limb check failed: %s", _le2178)
            # R-F2959 (B1) — SYMMETRIC feed-liveness: alarm when the research/student
            # LEARNING feeds are disabled or stale (the engine already has R-F2006;
            # the feeds silently didn't-run). Own try + own 6h re-alert throttle so a
            # feed problem never masks the engine check above.
            try:
                _feed_problems = await _eng.check_feed_liveness()
                if _feed_problems:
                    _now_fl = _t.time()
                    logger.warning("[R-F2959 feed-liveness] %s", "; ".join(_feed_problems))
                    if _now_fl - _last_feed_alert > _RE_ALERT_S:
                        _last_feed_alert = _now_fl
                        _summary = "; ".join(_feed_problems)
                        try:
                            from .intel import pending_actions as _pa_fl
                            await _pa_fl.record(
                                promise="ARIA's learning feeds must stay live so gate-#2 regional mastery compounds.",
                                reason=_summary,
                                severity="HIGH",
                                source="student_brain",
                                resolver_kind="operator_action",
                                resolver_ref="learning_feed_liveness",
                                operator_prompt=(
                                    "A learning feed is DARK/STALE: " + _summary +
                                    " — re-enable ARIA_AUTONOMOUS_RESEARCH_ENABLED / check the "
                                    "student loops; regional mastery cannot compound while a feed is dark."
                                ),
                                metadata={"watchdog": "R-F2959", "problems": _feed_problems},
                            )
                        except Exception as _pe_fl:
                            logger.warning("[R-F2959 feed-liveness] alert record failed: %s", _pe_fl)
                        try:
                            from .intel.engine_wiring import wire_failure as _wf_fl
                            _wf_fl(module="student_brain",
                                   detail=f"feed-liveness watchdog: {_summary}"[:400],
                                   gap_type="agent_cycle_failure",
                                   source="student_brain:feed_liveness_rf2959")
                        except Exception as _e672:
                            logger.debug("[R-F672] suppressed in lifespan (from .intel.engine_wiring import wire_failure as): %s", _e672)
            except Exception as _fle:
                logger.debug("[R-F2959 feed-liveness] check failed: %s", _fle)
            await asyncio.sleep(900)   # every 15 min

    liveness_task = _singleton_task(_engine_liveness_watchdog_loop, "engine_liveness_watchdog")  # R-F2073 singleton (watches the engine — which only runs on the engine role)
    logger.info("[R-F2006] Engine liveness watchdog started (alerts if engine goes dark)")

    # R-F1766 — DEPLOY PROPRIOCEPTION loop: confirm ARIA's autonomous self-improve
    # commits ACTUALLY reached the live server (build_rev), turning a confabulated
    # "deployed" into a verified one. Every 5 min: flips committed items to
    # verified_live once is_sha_live confirms; else (past CI grace) records a
    # deploy_verification_failure so self-heal/coder retries. This is the
    # machine-verification that makes no-human-gate autonomy safe.
    async def _deploy_proprioception_loop():
        await asyncio.sleep(240)  # let boot + any in-flight deploy settle
        from .intel import self_improve as _si1766
        while True:
            from .autonomous.safety import is_engine_paused as _is_paused
            if await _is_paused():
                await asyncio.sleep(300)
                continue
            try:
                res = await _si1766.reconcile_live_deploys()
                if res.get("verified") or res.get("failed"):
                    logger.info("[R-F1766] deploy proprioception: %s", res)
            except Exception as e:
                logger.warning("[R-F1766] deploy proprioception error: %s", e)
            # R-F1773 — UNIVERSAL intent ledger: verify EVERY push (raw git push +
            # ci_deploy + self_improve) actually went live, not just self_improve items.
            try:
                from .autonomous import deploy_verifier as _dv1773
                from .intel import redis_store as _rs1773

                async def _rec_gap_1773(g):
                    from .intel import capability_gaps as _cg1773
                    detail = (
                        f"Pushed commit {g['commit_sha'][:8]} (source={g.get('source')}) "
                        f"never went live after {g.get('age_s')}s — deploy did NOT land. "
                        f"live build_rev={g.get('live_build_rev')}. A push claimed done "
                        "but the live build_rev never advanced; deploy must be retried."
                    )
                    await _cg1773.record_gap(
                        gap_type="deploy_verification_failure",
                        detail=detail[:600],
                        source="deploy_verifier:intent_ledger")
                    logger.warning("[R-F1773] deploy NOT live: %s (source=%s) → gap recorded",
                                   g["commit_sha"][:8], g.get("source"))

                res1773 = await _dv1773.reconcile_intents_via_store(
                    _rs1773, gap_recorder=_rec_gap_1773)
                if res1773.get("verified") or res1773.get("failed"):
                    logger.info("[R-F1773] intent ledger: %d verified live, %d failed",
                                res1773["verified"], res1773["failed"])
            except Exception as e:
                logger.warning("[R-F1773] intent reconcile error: %s", e)
            # R-F1920 — ORIGIN-vs-LIVE reconciler: ALERT THE OPERATOR when
            # origin/main sits ahead of the live build_rev past a threshold. The
            # intent ledger above only records a coder gap, which the coder can't
            # action for human/Claude commits — so a pushed-but-undeployed batch
            # never reached the operator (§19e). GitHub (GH_TOKEN) is the
            # authoritative origin truth — independent of the pre-push hook.
            try:
                import os as _os1920
                _gh_token = _os1920.getenv("GH_TOKEN") or _os1920.getenv("GITHUB_TOKEN")
                if _gh_token:
                    from .autonomous import deploy_verifier as _dv1920
                    from .intel import redis_store as _rs1920

                    async def _origin_alert_1920(alert):
                        from .intel import pending_actions as _pa1920
                        short = alert.get("origin_sha")
                        age_min = round(float(alert.get("age_s") or 0) / 60.0)
                        await _pa1920.record(
                            promise=(f"Deploy origin/main {short} to aria-intel — "
                                     f"live still serves {alert.get('live_sha')}"),
                            reason=(f"origin/main has been ahead of the live build_rev for "
                                    f"~{age_min} min; no auto-deploy path fired (coder dry "
                                    "and/or no [deploy] tag on the pending commits)."),
                            severity="HIGH",
                            source="deploy_reconciler",
                            operator_prompt=(
                                f"aria-intel is BEHIND origin/main by commit {short} "
                                f"(~{age_min} min). Deploy it: scripts\\deploy.ps1 -Intel "
                                "(or push a follow-up commit tagged [deploy])."),
                            metadata={"origin_sha": short,
                                      "live_build_rev": alert.get("live_build_rev")},
                        )
                        logger.warning(
                            "[R-F1920] origin/main %s ahead of live %s for ~%dmin → operator alerted",
                            short, alert.get("live_sha"), age_min)

                    res1920 = await _dv1920.reconcile_origin_via_store(
                        _rs1920, token=_gh_token, operator_notifier=_origin_alert_1920)
                    if res1920.get("behind"):
                        logger.info(
                            "[R-F1920] live behind origin: origin=%s live=%s age=%.0fs alerted=%s",
                            res1920.get("origin_sha"), res1920.get("live_sha"),
                            res1920.get("age_s"), res1920.get("alerted"))
            except Exception as e:
                logger.warning("[R-F1920] origin reconcile error: %s", e)
            await asyncio.sleep(300)

    deploy_proprio_task = _singleton_task(_deploy_proprioception_loop, "deploy_proprioception")  # R-F2073 singleton
    logger.info("[R-F1766] deploy proprioception loop started (verify changes actually land)")

    # ── ARIA PROACTIVE WATCH ────────────────────────────────────────────
    # Hourly background loop that:
    #   - Checks if a daily morning briefing should fire
    #   - Triggers mastery-driven prep on weak topics
    # The anomaly watch runs inside /ingest after every sweep so it fires
    # the moment new data arrives (not on a fixed schedule).
    proactive_task = None

    async def _proactive_loop():
        await asyncio.sleep(120)  # 2 min after startup
        while True:
            # R-F1395: check engine pause flag before each cycle
            from .autonomous.safety import is_engine_paused as _is_paused
            if await _is_paused():
                logger.debug("[Proactive] engine paused — skipping cycle")
                await asyncio.sleep(3600)
                continue
            try:
                await _tick_heartbeat("proactive_watch", "Daily briefing trigger + mastery prep")
                # Daily briefing check
                fired = await proactive.daily_briefing_check(getattr(app.state, "current_data", None))
                await _wire_agent_success(
                    "proactive_watch",
                    f"Briefing fired: {fired}, weak topics flagged",
                )
                if fired:
                    logger.info("[Proactive] Daily briefing fired")

                # Mastery prep
                weak_count = await proactive.prepare_weak_topics()
                if weak_count:
                    logger.info("[Proactive] Mastery prep: %d weak topic(s) flagged", weak_count)
            except Exception as e:
                await _wire_agent_failure("proactive_watch", f"Loop failed: {e}")
                logger.warning("[Proactive] Loop iteration failed: %s", e)
            await asyncio.sleep(3600)  # Every hour

    proactive_task = _singleton_task(_proactive_loop, "proactive_loop")  # R-F2073 singleton
    logger.info("Proactive watch started: daily briefing + mastery prep (hourly)")

    # ── WEEKLY LEARNING REPORT ──────────────────────────────────────────
    # Every Monday at ~07:00 UTC, generate a learning report aggregating
    # new facts, mastery changes, capability gaps, standards ingested,
    # reasoning library health, and correction learning activity. The
    # report is persisted in Redis and can be delivered via WhatsApp.
    async def _weekly_report_loop():
        await asyncio.sleep(300)  # 5 min after startup
        while True:
            # R-F1395: check engine pause flag before each cycle
            from .autonomous.safety import is_engine_paused as _is_paused
            if await _is_paused():
                logger.debug("[Weekly] engine paused — skipping cycle")
                await asyncio.sleep(3600)
                continue
            try:
                await _tick_heartbeat("weekly_report", "Weekly learning report generation")
                from datetime import datetime, timezone
                now = datetime.now(timezone.utc)
                await _wire_agent_success("weekly_report", "Weekly report check cycle")
                if now.weekday() == 0 and 6 <= now.hour <= 8:
                    from .intel import weekly_report
                    result = await weekly_report.generate_weekly_report(
                        llm=getattr(app.state, "llm_provider", None),
                    )
                    # weekly_report.generate_weekly_report returns nested
                    # dicts (`new_facts.total`, `capability_gaps.unresolved`,
                    # `mastery_changes.current_scores`), not flat keys.
                    # Previous logging always printed "0 new facts, 0 gaps,
                    # mastery 0%" because the keys it read didn't exist.
                    _new_facts = (result.get("new_facts") or {}).get("total", 0)
                    _gaps = (result.get("capability_gaps") or {}).get("unresolved", 0)
                    _scores = (
                        (result.get("mastery_changes") or {}).get("current_scores") or {}
                    )
                    _overall_now = (
                        sum(_scores.values()) / len(_scores) if _scores else 0
                    )
                    logger.info(
                        "[Weekly Report] Generated: %d new facts, %d gaps, mastery %.0f%%",
                        _new_facts, _gaps, _overall_now * 100,
                    )
            except Exception as e:
                await _wire_agent_failure("weekly_report", f"Loop failed: {e}")
                logger.warning("[Weekly Report] Loop iteration failed: %s", e)
            await asyncio.sleep(3600)  # Check every hour (only fires on Monday 06-08 UTC)

    weekly_report_task = _singleton_task(_weekly_report_loop, "weekly_report_loop")  # R-F2073 singleton
    logger.info("Weekly report loop started (fires Monday 06-08 UTC)")

    # ── WATCHLIST AUTO-RE-SCREEN ──────────────────────────────────────────
    # Daily background loop: re-screens every entity on the DD watchlist
    # against sanctions + PEP lists (no LLM, no deep research). Detects
    # status changes and pushes alerts to Redis for API retrieval.
    watchlist_rescreen_task = None

    async def _watchlist_rescreen_loop():
        await asyncio.sleep(600)  # 10 min after startup
        while True:
            # R-F1395: check engine pause flag before each cycle
            from .autonomous.safety import is_engine_paused as _is_paused
            if await _is_paused():
                logger.debug("[Watchlist] engine paused — skipping cycle")
                await asyncio.sleep(3600)
                continue
            try:
                await _tick_heartbeat("watchlist_rescreen", "Re-screen DD watchlist entities against sanctions/PEP")
                from .intel import dd_orchestrator
                result = await dd_orchestrator.rescreen_watchlist(
                    llm=getattr(app.state, "llm_provider", None),
                    due_only=True,
                )
                await _wire_agent_success(
                    "watchlist_rescreen",
                    f"Re-screen: {result.get('entities_screened', 0)} entities, "
                    f"{len(result.get('changes_detected', []))} changes",
                )
                logger.info(
                    "[Watchlist] Re-screen: %d entities, %d changes, %d errors, %dms",
                    result.get("entities_screened", 0),
                    len(result.get("changes_detected", [])),
                    len(result.get("errors", [])),
                    result.get("duration_ms", 0),
                )
                # If changes detected, fire-and-forget WhatsApp notification.
                # R-F2748 (finding 8) — URGENT-notify on ADVERSE changes only. A
                # removal or a score DROP is informational (still visible in the
                # UI alert list) and must not trigger urgent delivery — that was
                # the alert-fatigue source. category is set by rescreen_watchlist;
                # fall back to change_type for older alerts missing the field.
                adverse = [
                    ch for ch in (result.get("changes_detected") or [])
                    if ch.get("category") == "adverse"
                    or ch.get("change_type") in ("new_hit", "new_pep")
                ]
                if adverse:
                    try:
                        # R-F2749 (finding 10) — the old import target in this
                        # block ('.intel' had no such submodule) DID NOT EXIST, so
                        # this alert silently never sent (ImportError swallowed
                        # below) and the brain never knew. Use the real WANotifier
                        # (→ aria-wa.internal) AND record the delivery OUTCOME to
                        # the §25 proprioception ledger: a queued task is not
                        # delivery — ARIA must KNOW whether the operator got it, so
                        # a non-delivery becomes a self-heal signal.
                        import hashlib as _hl
                        import time as _wa_t
                        from .autonomous.wa_notifier import WANotifier
                        from .intel.outcome_wire import record_outcome, OutcomeRecord
                        summary_lines = []
                        for ch in adverse[:10]:
                            summary_lines.append(
                                f"  - {ch['entity']}: {ch['old_status']} -> {ch['new_status']} ({ch['change_type']})"
                            )
                        msg = (
                            f"[ARIA Watchlist Alert] {len(adverse)} adverse change(s) detected:\n"
                            + "\n".join(summary_lines)
                        )
                        # Stable request_id per alert batch → outcome_wire dedupes
                        # retries and the reconcile can spot a never-delivered batch.
                        # R-F4048 (C-107) — `usedforsecurity=False`: this is a
                        # dedup FINGERPRINT for delivery idempotency, not a
                        # security digest. Bandit B324 (High) flagged it; the
                        # same annotation is already used in capability_gaps.
                        _req_id = "watchlist:" + _hl.sha1(
                            "|".join(a.get("fingerprint") or a.get("entity", "")
                                     for a in adverse).encode("utf-8"),
                            usedforsecurity=False,
                        ).hexdigest()[:16]

                        async def _send_and_record(_msg=msg, _rid=_req_id):
                            _t0 = _wa_t.monotonic()
                            outcome = await WANotifier().notify(_msg)
                            _lat = int((_wa_t.monotonic() - _t0) * 1000)
                            if outcome == "ok":
                                await record_outcome(OutcomeRecord(
                                    surface="wa", request_id=_rid,
                                    intended_result="watchlist_alert",
                                    actual_outcome="delivered_real_answer",
                                    latency_ms=_lat, detail=""))
                            elif str(outcome).startswith("error"):
                                # attempted but the operator did NOT get it → self-heal
                                await record_outcome(OutcomeRecord(
                                    surface="wa", request_id=_rid,
                                    intended_result="watchlist_alert",
                                    actual_outcome="send_failed",
                                    latency_ms=_lat, detail=str(outcome)))
                            else:
                                # skipped:* = dry-run / not configured — a known
                                # config state, not a per-request delivery failure.
                                logger.debug("[Watchlist] WA notify skipped: %s", outcome)

                        asyncio.create_task(_send_and_record())
                    except Exception as _wa_e:
                        logger.warning(
                            "[Watchlist] WA notification wiring failed: %s", _wa_e,
                        )
            except Exception as e:
                await _wire_agent_failure("watchlist_rescreen", f"Re-screen failed: {e}")
                logger.warning("[Watchlist] Re-screen failed: %s", e)
            await asyncio.sleep(3600)  # Hourly due-check; each entity owns its cadence

    watchlist_rescreen_task = _singleton_task(_watchlist_rescreen_loop, "watchlist_rescreen_loop")  # R-F2073 singleton
    logger.info("Watchlist due-check loop started (hourly, 10 min after startup)")

    # ── BRAVE STUDENT TRAINER (R-F2339) ───────────────────────────────────
    # Closes the Brave->SearXNG learning loop: periodically re-trains the student
    # re-ranker on the brave_distill teacher corpus and evaluates student-vs-teacher
    # agreement. The model file accumulates so the free stack can (once ARIA_BRAVE_
    # STUDENT_ENABLED=1 after eval) imitate Brave's source-selection methodology.
    async def _brave_student_loop():
        await asyncio.sleep(1200)  # 20 min after startup (let the corpus warm)
        while True:
            from .autonomous.safety import is_engine_paused as _is_paused
            if await _is_paused():
                await asyncio.sleep(21600)
                continue
            try:
                await _tick_heartbeat("brave_student", "Train student re-ranker on the Brave teacher corpus")
                from .intel import brave_student as _bs
                _m = await asyncio.to_thread(_bs.train_from_corpus)
                _ev = await asyncio.to_thread(_bs.evaluate)
                logger.info(
                    "[BraveStudent] trained on %d teacher records, %d domains; "
                    "eval baseline=%s student=%s lift=%s",
                    _m.get("records_seen", 0), len(_m.get("domain_pref") or {}),
                    _ev.get("baseline_topk_overlap"), _ev.get("student_topk_overlap"),
                    _ev.get("lift"),
                )
            except Exception as e:
                logger.warning("[BraveStudent] trainer loop failed: %s", e)
            await asyncio.sleep(21600)  # every 6 hours

    brave_student_task = _singleton_task(_brave_student_loop, "brave_student_loop")  # R-F2339
    logger.info("Brave student trainer loop started (every 6h, 20 min after startup)")

    # ── TENDER MONITOR ────────────────────────────────────────────────────
    # Every 6 hours, crawl public defence procurement portals (TED, SAM.gov,
    # Contracts Finder, UNGM, AfDB) for relevant tenders. Equivalent to
    # Janes/IHS Markit tender monitoring. No LLM required — pure HTTP
    # crawl + keyword/CPV scoring.
    tender_monitor_task = None

    async def _tender_monitor_loop():
        await asyncio.sleep(900)  # 15 min after startup
        while True:
            # R-F1395: check engine pause flag before each cycle
            from .autonomous.safety import is_engine_paused as _is_paused
            if await _is_paused():
                logger.debug("[Tender] engine paused — skipping cycle")
                await asyncio.sleep(21600)
                continue
            try:
                await _tick_heartbeat("tender_monitor", "Crawl defence procurement portals")
                from .intel import tender_monitor
                result = await tender_monitor.run_monitoring_cycle()
                await _wire_agent_success(
                    "tender_monitor",
                    f"Tender cycle: {result.get('new_tenders', 0)} new tenders "
                    f"across {result.get('portals_crawled', 0)} portals",
                )
                if result.get("new_tenders", 0) > 0:
                    logger.info(
                        "[Tender Monitor] %d new tenders detected across %d portals",
                        result["new_tenders"], result["portals_crawled"],
                    )
                else:
                    logger.info("[Tender Monitor] Cycle complete — no new tenders")
            except Exception as e:
                await _wire_agent_failure("tender_monitor", f"Cycle failed: {e}")
                logger.warning("[Tender Monitor] Cycle failed: %s", e)
            await asyncio.sleep(21600)  # Every 6 hours

    tender_monitor_task = _singleton_task(_tender_monitor_loop, "tender_monitor_loop")  # R-F2073 singleton
    logger.info("Tender monitor started (every 6h)")

    # ── METACOGNITIVE ENGINE STATUS ───────────────────────────────────────
    # Phase 3 metacognitive stack: self-assessment, gap detection, Brier
    # scoring, consciousness mapping, self-improvement code generation.
    # The engine hooks into the chat pipeline (post-output self-assessment)
    # and the autonomous engine (daily/weekly/monthly cycles). No background
    # loop needed — just log readiness status at startup.
    try:
        from .metacognitive.identity import is_enabled as metacog_enabled
        if metacog_enabled():
            logger.info(
                "Metacognitive engine ENABLED — self-assessment on chat pipeline, "
                "identity+calibration injected into system prompt. "
                "Admin: /api/aria/metacognitive/status"
            )
        else:
            logger.info("Metacognitive engine DISABLED — set ARIA_METACOGNITIVE_ENABLED=1 to enable")
    except Exception as e:
        logger.warning("Metacognitive engine status check failed (non-fatal): %s", e)

    # ── ARIA LAYER 3 — AUTONOMOUS RESEARCH ENGINE ───────────────────────
    # Phase 3c-α (2026-04-09): scheduled research tasks defined in
    # aria_service/autonomous/tasks.yaml. Gated behind TWO independent
    # enable flags so a deploy cannot accidentally turn it on:
    #   1. ARIA_AUTONOMOUS_ENABLED env var (default OFF)
    #   2. per-task `enabled: true` in tasks.yaml (default false on every task)
    # Even with both flags on, the engine runs in DRY_RUN mode by default
    # (set ARIA_AUTONOMOUS_DRY_RUN=0 to enable real delivery to WhatsApp /
    # intel ledger). See aria_service/autonomous/AUTONOMOUS_ENGINE.md.
    async def _bootstrap_autonomous_engine_bg():
        await _await_heavy_graph_ready(app)
        try:
            from .autonomous import engine as autonomous_engine
            # Hydrate the in-process runtime-override cache BEFORE checking
            # is_enabled(). This lets /autonomous/enable keep the engine on
            # after a redeploy when the env var is missing — the Redis flag
            # survives restarts and gets picked up here on the next boot.
            await autonomous_engine.refresh_runtime_override()
            # R-F2184 — heal a LOST master flag at boot (env dropped + no override) so
            # the engine actually STARTS rather than silently staying dark (the R-F2004
            # outage class — a dropped ARIA_AUTONOMOUS_ENABLED killed the metabolism for
            # 187h). Respects a deliberate override=0. Singleton role only.
            if _runs_singletons():
                try:
                    _are_res = await autonomous_engine.maybe_autorecover_master_switch()
                    if _are_res.get("recovered"):
                        logger.warning("[R-F2184] boot: %s", _are_res.get("reason"))
                except Exception as _are:
                    logger.debug("[R-F2184] boot autorecover failed: %s", _are)
            if _runs_singletons() and autonomous_engine.is_enabled():  # R-F2073 — engine is a singleton (only the engine role runs it)
                # R-F2901 — WAIT for the LLM before starting. This bootstrap and
                # _init_llm_and_dialogue_bg (which sets app.state.llm_provider)
                # are BOTH background tasks with no ordering between them, and
                # start_engine() hard-refuses when the provider isn't configured
                # yet — with no retry anywhere. Losing that race left autonomy
                # silently dark until the next restart. Observed live on the
                # 2026-07-23 Claude-flip restart: the engine checked at
                # 12:10:48, the chain was assigned at 12:10:49, and the engine
                # never started (only a capability gap was recorded).
                #
                # This is the R-F2004 failure class (metabolism dark for 187h)
                # arriving by a different route, so it gets a structural fix
                # rather than a nudge: poll for the provider, bounded, then
                # start. The bound is generous because a cold boot loads ~223k
                # facts before the LLM init task gets scheduled (§11c).
                waited = await await_llm_provider(app)
                if waited:
                    logger.info(
                        "[R-F2901] autonomous engine waited %.0fs for the LLM provider",
                        waited,
                    )
                started = autonomous_engine.start_engine(getattr(app.state, "llm_provider", None))
                if started:
                    logger.info(
                        "Autonomous engine started (dry_run=%s) — see /api/aria/autonomous/status",
                        autonomous_engine.is_dry_run(),
                    )
            else:
                logger.info(
                    "Autonomous engine NOT started — set ARIA_AUTONOMOUS_ENABLED=1 "
                    "or POST /api/aria/autonomous/enable to flip at runtime"
                )
                # Log a pending-action so the operator sees this in the next
                # daily briefing. CRITICAL severity so it gets nudged now.
                try:
                    from .intel import pending_actions as _pa
                    await _pa.record(
                        promise=(
                            "Autonomous learning loop should be running 24/7 — "
                            "spider, metacog, research, style_learner, plus 65 "
                            "scheduled tasks."
                        ),
                        reason=(
                            "ARIA_AUTONOMOUS_ENABLED env var is not set on the "
                            "Python backend (fly.io app aria-intel). The engine "
                            "cannot run until the master switch is on."
                        ),
                        resolver_kind="operator_action",
                        resolver_ref="ARIA_AUTONOMOUS_ENABLED",
                        severity="CRITICAL",
                        source="lifespan_bootstrap",
                        operator_prompt=(
                            "POST /api/aria/autonomous/enable to turn on the "
                            "autonomous engine right now (survives redeploy via "
                            "Redis). For a permanent fix, also run: "
                            "fly secrets set ARIA_AUTONOMOUS_ENABLED=1 "
                            "-a aria-intel"
                        ),
                    )
                except Exception as _pa_err:
                    logger.debug(
                        "pending_actions record at bootstrap failed (non-fatal): %s",
                        _pa_err,
                    )
        except Exception as e:
            logger.warning("Autonomous engine bootstrap failed (non-fatal): %s", e)
    _bg_task(asyncio.create_task(_bootstrap_autonomous_engine_bg(), name="autonomous_bootstrap"))

    # ── Defence source seed → web_atlas (2026-04-18) ────────────────
    # Bootstrap the curated Tier-1/1b/2 defence source catalogue into
    # web_atlas if it hasn't been populated yet. Idempotent — safe to
    # run on every startup. Seeding happens in background so it doesn't
    # block the lifespan startup gate.
    try:
        from .intel import defence_source_seed
        async def _seed_bg():
            await _await_heavy_graph_ready(app)
            try:
                result = await defence_source_seed.seed_web_atlas(
                    skip_if_populated=True,
                )
                logger.info("Defence source seed: %s", result)
            except Exception as _e:
                logger.debug("Defence source seed bg failed: %s", _e)
        import asyncio as _aio
        _aio.create_task(_seed_bg())
    except Exception as e:
        logger.debug("Defence source seed dispatch failed (non-fatal): %s", e)

    # ── Knowledge seeding (background) ─────────────────────────────────
    # Seed the full knowledge corpus on startup. Runs after RAG store is
    # warm (25s delay). Idempotent — rag_store.ingest_document()
    # deduplicates by source URL. Five modules get ingested in order:
    #   1. international_law            (LOAC/IHL, ATT, sanctions, AML, …)
    #   2. global_export_control        (UK/US/EU/Wassenaar/MTCR/NSG/AG/CWC
    #                                    + national regimes TR/IL/KR/JP/BR/
    #                                    IN/RU/CN/AE)
    #   3. regional_compliance          (NATO, EU, AU/ECOWAS/SADC/EAC, GCC,
    #                                    ASEAN/Quad/AUKUS, OAS/MERCOSUR,
    #                                    CIS/CSTO/SCO, OSCE, UNROCA)
    #   4. due_diligence_playbooks      (UBO extraction + ghost scoring)
    #   5. risk_indices                 (CPI, Basel AML, FATF, WGI, EITI,
    #                                    GPI, GTI, OECD CRC)
    #   6. international_law sources    (crawl registration for refresh)
    #   7. contract_intelligence.ingest_clause_library (clause library)
    # Seed-completion marker in Redis. If the seed finished within the
    # last SEED_CACHE_TTL seconds on a previous boot, skip re-running to
    # avoid pinning CPU/memory on rolling restarts. Force re-ingest via
    # POST /api/aria/knowledge/reseed or by setting ARIA_FORCE_RESEED=1.
    _SEED_MARKER_KEY = "crucix:knowledge_seed:last_completed"
    _SEED_CACHE_TTL = 6 * 3600  # 6 hours

    async def run_knowledge_seed(force: bool = False) -> dict:
        """Idempotent knowledge-corpus seeding.

        Runs every module sequentially. Each ingest_all_sections call is
        internally deduped by rag_store via source URL, so re-running is
        cheap. Returns a summary dict. Safe to call from startup, from
        /api/aria/knowledge/reseed, or manually via fly ssh.
        """
        from .intel import redis_store as _rs
        summary: dict = {}
        if not force:
            try:
                last = await _rs.get(_SEED_MARKER_KEY)
                if last:
                    age = time.time() - float(last)
                    if age < _SEED_CACHE_TTL:
                        logger.info(
                            "[Knowledge Seed] skipping — completed %.0fs ago (within %ds cache window). "
                            "Set ARIA_FORCE_RESEED=1 or POST /api/aria/knowledge/reseed to override.",
                            age, _SEED_CACHE_TTL,
                        )
                        return {"skipped": True, "last_completed_age_s": int(age)}
            except Exception as e:
                logger.debug("seed marker read failed (non-fatal): %s", e)

        modules = [
            ("international_law",       "Law",                   "ingest_all_sections"),
            ("global_export_control",   "Global export control", "ingest_all_sections"),
            ("regional_compliance",     "Regional compliance",   "ingest_all_sections"),
            ("due_diligence_playbooks", "DD playbooks",          "ingest_all_sections"),
            ("risk_indices",            "Risk indices",          "ingest_all_sections"),
            ("dd_case_library",         "DD case library",       "ingest_all_cases"),
            ("nato_standards",          "NATO standards",        "ingest_to_knowledge"),
            ("procurement_knowledge",   "Procurement intel",     "ingest_to_knowledge"),
            ("market_competitor_knowledge", "Market & competitor",  "ingest_to_knowledge"),
            ("osint_knowledge",          "OSINT methodology",    "ingest_to_knowledge"),
            ("security_protocol",        "Security protocol",    "ingest_to_knowledge"),
            # R-F3215 — BS 7858 lived ONLY inside aria_service/vetting/ as
            # clause reference strings. ARIA enforced the standard and could
            # not discuss it: asked what it requires, she had a general
            # model's recollection of a paywalled document to fall back on.
            # This ingests OUR encoded register (clause numbers + our own
            # statement of each obligation + what we do NOT model). No BSI
            # text, here or anywhere.
            ("vetting_standard_knowledge", "BS 7858 screening", "ingest_to_knowledge"),
            ("sipri_knowledge",          "SIPRI + equipment",    "ingest_all_sections"),
            ("global_defence_knowledge", "Global defence intel", "ingest_all_sections"),
        ]

        # F50 fix 2026-04-27: chromadb dedupes upserts by ID, but the
        # sentence-transformer ENCODE still runs on every chunk every
        # time. With ~660 chunks across 13 modules, that's ~5 minutes of
        # CPU per cold boot — and it tripped the brain_hook circuit
        # breaker at 21:35:05 (p95=2800ms). Skip per-module if the
        # module's source file hash hasn't changed since the last
        # successful seed.
        import hashlib as _hashlib
        from pathlib import Path as _Path
        async def _module_hash(modname: str) -> str:
            """Return md5 of the module's .py file, or '' if not found."""
            try:
                mod_path = _Path(__file__).parent / "intel" / f"{modname}.py"
                if not mod_path.exists():
                    return ""
                h = _hashlib.md5(usedforsecurity=False)
                h.update(mod_path.read_bytes())
                return h.hexdigest()
            except Exception:
                return ""

        for modname, label, fn in modules:
            try:
                # Hash-guard: skip the whole module if its source file
                # hasn't changed since the last successful seed.
                if not force:
                    cur_hash = await _module_hash(modname)
                    if cur_hash:
                        seed_hash_key = f"crucix:knowledge_seed:hash:{modname}"
                        try:
                            stored = await _rs.get(seed_hash_key)
                            if stored and str(stored) == cur_hash:
                                summary[modname] = {"skipped": True, "reason": "hash_unchanged"}
                                logger.info(
                                    "[Knowledge Seed] %s: skipped (file unchanged since last seed)",
                                    label,
                                )
                                continue
                        except Exception as e:
                            logger.debug("seed hash read failed for %s: %s", modname, e)

                mod = __import__(f"aria_service.intel.{modname}", fromlist=[fn])
                result = await getattr(mod, fn)()
                summary[modname] = result
                logger.info(
                    "[Knowledge Seed] %s: %d/%d sections, %d chunks",
                    label,
                    result.get("sections_ingested", 0),
                    result.get("total_sections", 0),
                    result.get("total_chunks", 0),
                )
                # Stamp the hash on success so subsequent boots skip
                # this module until the file changes (e.g. via a deploy
                # that updates the law/procurement/etc. text content).
                #
                # R-F4262 (dossier E2) — "no exception" is NOT success. Every
                # seeder swallows its per-section failures and returns a count,
                # so a total failure returned {"sections_ingested": 0} and this
                # stamped a 30-DAY skip on it. The next month of boots then
                # skipped the module entirely while DD Layer 4c kept attributing
                # report content to its RAG namespace.
                #
                # `None` means the seeder did not report a count — that is "I
                # could not tell", and it must not stamp either. The cost of
                # being wrong is one re-seed per boot; the cost of the old
                # behaviour was a month of silently missing knowledge.
                _ingested = _seed_ingested_something(result)
                if _ingested is not True:
                    logger.warning(
                        "[Knowledge Seed] %s: NOT stamping the 30-day skip hash "
                        "(ingested=%s, result=%s) — it will be retried next boot",
                        label, _ingested, str(result)[:200],
                    )
                cur_hash = await _module_hash(modname) if _ingested is True else ""
                if cur_hash:
                    try:
                        await _rs.set(
                            f"crucix:knowledge_seed:hash:{modname}",
                            cur_hash,
                            ex=30 * 86400,  # 30 days
                        )
                    except Exception as e:
                        logger.debug("seed hash write failed for %s: %s", modname, e)
            except Exception as e:
                summary[modname] = {"error": str(e)}
                logger.warning("[Knowledge Seed] %s ingestion failed (non-fatal): %s", label, e)

        try:
            from .intel import international_law
            reg = await international_law.register_law_sources()
            summary["law_sources"] = reg
            logger.info("[Knowledge Seed] Law sources registered: %d", reg.get("registered", 0))
        except Exception as e:
            summary["law_sources"] = {"error": str(e)}
            logger.warning("[Knowledge Seed] Law source registration failed (non-fatal): %s", e)
        try:
            from .intel import contract_intelligence
            clause_result = await contract_intelligence.ingest_clause_library()
            summary["clause_library"] = clause_result
            logger.info(
                "[Knowledge Seed] Clause library: %d clauses, %d chunks",
                clause_result.get("clauses_ingested", 0),
                clause_result.get("total_chunks", 0),
            )
        except Exception as e:
            summary["clause_library"] = {"error": str(e)}
            logger.warning("[Knowledge Seed] Clause library ingestion failed (non-fatal): %s", e)

        # Mark seed completion. Even a partial run counts — the URL-dedup
        # layer makes the next run cheap, and the marker prevents
        # thundering-herd retries on rolling restarts.
        try:
            await _rs.set(_SEED_MARKER_KEY, str(time.time()), ex=_SEED_CACHE_TTL * 4)
        except Exception as e:
            logger.debug("seed marker write failed (non-fatal): %s", e)
        summary["completed_at"] = time.time()
        return summary

    # Expose for the /api/aria/knowledge/reseed route.
    app.state.run_knowledge_seed = run_knowledge_seed

    async def _seed_knowledge_bg():
        await _await_heavy_graph_ready(app)
        force = (_os.getenv("ARIA_FORCE_RESEED", "") or "").strip().lower() in ("1", "true", "yes", "on")
        try:
            await run_knowledge_seed(force=force)
        except Exception as e:
            logger.warning("[Knowledge Seed] unhandled error (non-fatal): %s", e)

    # R-F2668 — ONE-SHOT (runs run_knowledge_seed once and RETURNS): respawn=False so
    # its normal completion is not mistaken for a crash and re-spawned to the R-F1610
    # 'NEEDS OPERATOR' ERROR that reset the gate-#3 streak every boot. Keeps the R-F2073
    # singleton lock (web-role skip); just does not register for supervisor re-spawn.
    knowledge_seed_task = _singleton_task(_seed_knowledge_bg, "seed_knowledge", respawn=False)

    # ── R-F803 (2026-05-22): autonomous self-coder boot ───────────────────
    # ARIACoder + GapDetector. R-F996: coder is ALWAYS enabled when ARIA_INTERNAL_TOKEN is set.
    # No ARIA_CODER_ENABLED env var gate — the coder loop must stay
    # draining per CLAUDE.md §21c. The auto-deploy brake is
    # ARIA_SELF_IMPROVE_AUTO_DEPLOY (must stay 0 until R-F1450 proven).
    # See aria_service/autonomous/coder_entrypoint.py for the actual gates.
    # Returns a list[Task] (or None if any gate refused).
    aria_coder_tasks: list[asyncio.Task] = []
    if not _runs_singletons():  # R-F2073 — coder is a singleton (one gap-queue drainer; N would race + N× cost)
        logger.info("[R-F2073] ARIA-Coder SKIPPED (ARIA_ROLE=%s)", _aria_role())
    else:
        async def _start_aria_coder_bg():
            await _await_heavy_graph_ready(app)
            try:
                from .autonomous.coder_entrypoint import start_aria_coder
                _coder_tasks = await start_aria_coder(app.state)
                if _coder_tasks:
                    aria_coder_tasks.extend(_coder_tasks)
                    # R-F2543 (codex F4): the coder's own loops were held only in
                    # aria_coder_tasks (GC refs), NOT registered with the bg supervisor —
                    # so a post-startup death was neither logged nor respawned. Register
                    # each with _bg_task for death-visibility, and give the MAIN self_coder
                    # loop a respawn factory (coder.run_forever) so the supervisor revives
                    # it if it dies (mirrors the R-F2537 drain-worker supervision).
                    _coder = getattr(app.state, "aria_coder", None)
                    for _ct in _coder_tasks:
                        try:
                            _cn = _ct.get_name()
                            if _cn == "aria_coder.self_coder" and _coder is not None:
                                _bg_task(_ct, name=_cn, factory=_coder.run_forever)
                            else:
                                _bg_task(_ct, name=_cn)
                        except Exception as _reg_e:
                            logger.debug("[R-F2543] coder task register failed: %s", _reg_e)
                    logger.info(
                        "[R-F803] ARIA-Coder started with %d background tasks",
                        len(aria_coder_tasks),
                    )
            except Exception as _coder_e:
                # Never let a coder-init exception block the lifespan — the engine
                # is non-essential for chat / DD traffic.
                logger.warning(
                    "[R-F803] ARIA-Coder init failed (non-fatal): %s", _coder_e,
                )
        _bg_task(asyncio.create_task(_start_aria_coder_bg(), name="aria_coder_start"))

    # R-F1207 — start Web Integrity Agent (24/7 endpoint monitoring)
    # Monitors all 14 web endpoints every 60s, validates inputs/outputs,
    # detects error patterns, and stages fixes for recurring issues.
    # Implements all 7 binding directives from the operator:
    #   1. Verify every input   2. Verify every output   3. Monitor 24/7
    #   4. Cross-agent comms    5. Zero tolerance         6. Self-healing
    #   7. Never silent
    web_integrity_agent: Optional[Any] = None
    if not _runs_singletons():  # R-F2073 — one monitor (N would N× probe every endpoint)
        logger.info("[R-F2073] Web Integrity Agent SKIPPED (ARIA_ROLE=%s)", _aria_role())
    else:
        async def _start_web_integrity_bg():
            nonlocal web_integrity_agent
            await _await_heavy_graph_ready(app)
            try:
                from .intel.web_integrity_agent import WebIntegrityAgent, WEB_ENDPOINTS, _WEB_ENDPOINTS_PUBLIC
                from .intel import brain_hook as _bh_wia
                web_integrity_agent = WebIntegrityAgent(
                    aria_service_url=f"http://localhost:{settings.effective_port}",
                    brain_hook=_bh_wia,
                    redis_store=rs if _state_connect_ok else None,
                )
                await web_integrity_agent.start()
                logger.info(
                    "[R-F1207] Web Integrity Agent started — monitoring %d endpoints every 60s",
                    len(WEB_ENDPOINTS) + len(_WEB_ENDPOINTS_PUBLIC),
                )
            except Exception as _wia_e:
                logger.warning("[R-F1207] Web Integrity Agent start failed (non-fatal): %s", _wia_e)
        _bg_task(asyncio.create_task(_start_web_integrity_bg(), name="web_integrity_start"))

    # R-F1253 — auto-populate agent signup vault from portal registry on boot
    try:
        from .intel.agent_signup_vault import get_vault
        from .intel.portal_registry import PORTALS
        vault = get_vault()
        stats = vault.stats()
        if stats.get("total", 0) == 0 and vault.is_auto_seed_enabled():
            # R-F1482: method is import_open_portals, not import_from_portal_registry
            count = vault.import_open_portals(PORTALS, agent_id="system")
            logger.info(
                "[R-F1253] Agent signup vault auto-populated: %d portals imported",
                count,
            )
            # R-F1444: also mark free/open portals (registration_type="none") as registered
            open_count = vault.import_open_portals(PORTALS, agent_id="system")
            if open_count > 0:
                logger.info(
                    "[R-F1444] Marked %d open portals as registered (no signup needed)",
                    open_count,
                )
        elif stats.get("total", 0) > 0:
            logger.debug(
                "[R-F1253] Agent signup vault already has %d entries — skipping import",
                stats["total"],
            )
        else:
            logger.info(
                "[R-F3094] Agent signup vault is empty after an operator clear — "
                "boot-time portal import remains disabled"
            )

        # R-F1444: fire-and-forget auto-registration for pending portals
        # R-F3198 — RETIRED alongside the scheduler tick and the registration
        # loop. Left as an explicit `False and ...` rather than deleted so the
        # R-F1447 note below (module-level asyncio; a bare local import here
        # once caused a boot outage) stays where the next reader will find it.
        if False and vault.is_auto_seed_enabled():
            try:
                from .intel.portal_registry import auto_register_all as _auto_reg
                # R-F1447: use the module-level asyncio (line 14). A bare local
                # `import asyncio` here made asyncio function-local for the WHOLE
                # lifespan(), so the earlier asyncio.create_task at line ~450
                # raised UnboundLocalError -> lifespan startup failed -> the app
                # never bound :8000 -> deploy failed / OUTAGE. Same class as R-F1441.
                _bg_task(asyncio.create_task(_delayed_auto_register(_auto_reg), name="portal_auto_register"))
            except Exception as _reg_e:
                logger.warning("[R-F1444] Auto-registration launch failed (non-fatal): %s", _reg_e)

    except Exception as _vault_e:
        logger.warning("[R-F1253] Vault auto-population failed (non-fatal): %s", _vault_e)

    logger.info(f"ARIA Service ready on {settings.host}:{settings.effective_port}")

    # R-F1051 -- start self-healing infrastructure
    async def _start_self_healing_bg():
        try:
            from .intel.self_healing import start_self_healing
            await start_self_healing()
            logger.info("[R-F1051] Self-healing infrastructure started")
        except Exception as _heal_e:
            logger.warning("[R-F1051] Self-healing start failed (non-fatal): %s", _heal_e)
    _bg_task(asyncio.create_task(_start_self_healing_bg(), name="self_healing_start"))

    # R-F1146 -- start self-restart blackout detector
    try:
        from .intel.self_restart import start_blackout_detector, tick_heartbeat
        start_blackout_detector()
        tick_heartbeat("aria_main")
        logger.info("[R-F1146] Self-restart blackout detector started")
    except Exception:
        logger.warning("[R-F1146] Self-restart start failed (non-fatal)")

    # R-F1850 (DD stage 1) — REMOVED the R-F1225 PowerShell Master boot block.
    # It registered an unauthenticated POST /powershell/execute (arbitrary command
    # execution) and was dead in every environment (`router` is undefined here —
    # only `aria_router` is imported — so `add_powershell_endpoints(router, ...)`
    # raised NameError, swallowed by the except; pwsh is also absent on Linux prod).
    # `ps_master`/`app.state.ps_master` were never read anywhere and no caller hits
    # the route. Removed outright to eliminate the latent-RCE tripwire (a one-line
    # `router`→`aria_router` "fix" would have exposed it). The PowerShellMaster
    # class remains in utils/powershell_master.py for the local dev CLI; it is just
    # no longer auto-wired into the HTTP surface.

    # R-F1550: start Eagle Eye codebase guardian
    if not _runs_singletons():  # R-F2073 singleton (one codebase guardian)
        logger.info("[R-F2073] Eagle Eye SKIPPED (ARIA_ROLE=%s)", _aria_role())
    else:
        async def _start_eagle_eye_bg():
            try:
                from .intel import eagle_eye
                await eagle_eye.start()
            except Exception as _ee_err:
                logger.warning("[EagleEye] Start failed (non-fatal): %s", _ee_err)
        _bg_task(asyncio.create_task(_start_eagle_eye_bg(), name="eagle_eye_start"))

    # R-F1552: start Wiring Monitor (M1-M5 background checks every hour)
    _wiring_monitor_task = None
    if _runs_singletons():  # R-F2073 singleton
        try:
            from .intel import wiring_monitor as _wm
            _wiring_monitor_task = _bg_task(
                _wm.start_monitor(),
                name="wiring_monitor",
                factory=_wm.monitor_loop,
            )
        except Exception as _wm_err:
            logger.warning("[R-F1552] Wiring Monitor start failed (non-fatal): %s", _wm_err)
    else:
        logger.info("[R-F2073] Wiring Monitor SKIPPED (ARIA_ROLE=%s)", _aria_role())

    # R-F1574: start Autonomous Scheduler (DD monitor, gap fixing, diagnostics)
    _scheduler_task = None
    if _runs_singletons():  # R-F2073 singleton
        try:
            from .intel.autonomous_scheduler import AutonomousScheduler
            async def _autonomous_scheduler_loop():
                _scheduler = AutonomousScheduler()
                await _scheduler.start()
                try:
                    while True:
                        await asyncio.sleep(3600)
                finally:
                    await _scheduler.stop()

            _scheduler_task = _bg_task(
                asyncio.create_task(_autonomous_scheduler_loop(), name="autonomous_scheduler"),
                name="autonomous_scheduler",
                factory=_autonomous_scheduler_loop,
            )
        except Exception as _sched_err:
            logger.warning("[R-F1574] Autonomous Scheduler start failed (non-fatal): %s", _sched_err)
    else:
        logger.info("[R-F2073] Autonomous Scheduler SKIPPED (ARIA_ROLE=%s)", _aria_role())

    # R-F3202 — Portal Registration Scheduler REMOVED. R-F3198 retired the
    # behaviour; this deletes the dead launch block, which still imported
    # portal_scheduler — a module that no longer exists. Unreachable (the
    # gate returns False) but a reference to a deleted module is a trap for
    # whoever re-enables the gate next. The name stays bound: shutdown
    # still checks it.
    _portal_scheduler_task = None

    # R-F1610 — start the self-healing actuator: re-spawns any registered bg
    # loop that dies, instead of only logging it. This is what makes ARIA
    # actually self-HEAL (not just detect). It runs only AFTER all loops above
    # are created so _BG_RESPAWN is fully populated.
    try:
        _bg_task(asyncio.create_task(_bg_supervisor_loop(), name="bg_supervisor"))
        logger.info("[R-F1610] bg_supervisor started — supervising %d loops: %s",
                    len(_BG_RESPAWN), sorted(_BG_RESPAWN))
    except Exception as _sup_err:
        logger.warning("[R-F1610] bg_supervisor start failed (non-fatal): %s", _sup_err)

    # R-F1612 — deploy proprioception: record this boot/build event to the brain
    # so ARIA KNOWS what she shipped over time (persistent history queryable from
    # her RAG), not just the live value. Fire-and-forget — never blocks boot.
    try:
        _bg_task(asyncio.create_task(_record_deploy_event(), name="deploy_record"))
    except Exception as _e672:
        logger.debug("[R-F672] suppressed in lifespan (_bg_task(asyncio.create_task(_record_deploy_even): %s", _e672)

    # R-F2278 / R-F3816 — the duplicate-route audit. Runs HERE, not at import:
    # the route table is complete by now (import-time it was 754 of 770 routes,
    # missing /static, / and /download/*), and the loop and state store exist, so
    # the R-F3792 brain signal can actually land. See the note at the
    # include_router block for the measurements.
    try:
        from .route_audit import log_duplicate_routes as _log_dup_routes
        _log_dup_routes(app)
    except Exception:  # pragma: no cover - never let the audit break boot
        logger.debug("[R-F3816] route audit failed to run", exc_info=True)

    yield


    # ── Shutdown ─────────────────────────────────────────────────────────
    async def _shutdown_await(label: str, awaitable, timeout_s: float = 5.0) -> None:
        """R-F2378: teardown must not hang the lifespan after a degraded boot."""
        try:
            await asyncio.wait_for(awaitable, timeout=timeout_s)
        except asyncio.TimeoutError:
            logger.warning(
                "[R-F2378] %s shutdown exceeded %.1fs (continuing teardown)",
                label,
                timeout_s,
            )
        except Exception as e:
            logger.warning("%s shutdown failed (non-fatal): %s", label, e)

    # R-F3006 -- drain buffered cost records into the durable month rollup BEFORE
    # teardown, so a graceful restart (deploy) never loses the in-memory pending /
    # rollup-retry buffers from the spend gauge + $300 cap. Runs first (cheap,
    # timeout-guarded) so later teardown hangs can't skip it.
    try:
        from .intel import cost_tracker as _ct_shutdown
        await _shutdown_await("[R-F3006] cost flush", _ct_shutdown.flush_pending_cost())
    except Exception as _ct_e:
        logger.warning("[R-F3006] cost flush on shutdown failed (non-fatal): %s", _ct_e)

    # R-F4309 -- release the shared web_search httpx client's connection pool.
    # The search backends share ONE long-lived client (per-request construction
    # rebuilt the TLS context on the event loop). Nothing else closes it, so
    # without this the pool's sockets are only reclaimed by process exit.
    # Safe to run before the autonomous engine stops: `_get_shared_client`
    # rebuilds a closed client, so a task still in flight gets a working one
    # rather than "Cannot send a request, as the client has been closed".
    try:
        from .intel import web_search as _ws_shutdown
        await _shutdown_await("[R-F4309] shared search client", _ws_shutdown.close_shared_client())
    except Exception as _ws_e:
        logger.warning("[R-F4309] shared search client close failed (non-fatal): %s", _ws_e)

    # R-F1051 -- stop self-healing infrastructure
    try:
        from .intel.self_healing import stop_self_healing
        await _shutdown_await("[R-F1051] Self-healing", stop_self_healing())
    except Exception as _heal_import_e:
        logger.warning(
            "[R-F1051] Self-healing shutdown import failed (non-fatal): %s",
            _heal_import_e,
        )

    # R-F1146 -- stop self-restart blackout detector
    try:
        from .intel.self_restart import stop_blackout_detector
        stop_blackout_detector()
        logger.info("[R-F1146] Self-restart blackout detector stopped")
    except Exception:
        logger.warning("[R-F1146] Self-restart shutdown failed (non-fatal)")

    # R-F1207 -- stop Web Integrity Agent
    if web_integrity_agent is not None:
        await _shutdown_await("[R-F1207] Web Integrity Agent", web_integrity_agent.stop())
        logger.info("[R-F1207] Web Integrity Agent stop attempted")

    # R-F1368 -- stop LLM health checker
    # R-F2158: was `llm_health_checker` (no underscore) — a name that is NEVER
    # bound (startup assigns `_llm_health_checker`), so this raised an
    # UNHANDLED NameError on EVERY shutdown, aborting the rest of lifespan
    # teardown BEFORE the knowledge-flush (F94) + search-DB WAL flush (R-F504).
    # That made shutdowns unclean → left a bloated WAL → the very state_store
    # boot/timeout symptoms the R-F2116/2137/2154 chain kept band-aiding.
    if _llm_health_checker is not None:
        await _shutdown_await("[R-F1368] LLM health checker", _llm_health_checker.stop())
        logger.info("[R-F1368] LLM health checker stop attempted")

    # R-F1550: stop Eagle Eye codebase guardian
    try:
        from .intel import eagle_eye
        await _shutdown_await("[EagleEye]", eagle_eye.stop())
    except Exception as _ee_import_err:
        logger.warning("[EagleEye] Shutdown import failed (non-fatal): %s", _ee_import_err)

    # R-F1890: stop the encode-offload worker process
    try:
        from .intel import encode_offload as _eo
        await _shutdown_await("[R-F1890] encode-offload", asyncio.to_thread(_eo.stop))
    except Exception as _eo_err:
        logger.warning("[R-F1890] encode-offload stop failed (non-fatal): %s", _eo_err)

    # R-F1574: stop Autonomous Scheduler
    if _scheduler_task is not None:
        try:
            _scheduler_task.cancel()
            await asyncio.wait_for(_scheduler_task, timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        logger.info("[R-F1574] Autonomous Scheduler stopped")

    try:
        from .autonomous import engine as _autonomous_engine
        await _shutdown_await("Autonomous engine", _autonomous_engine.stop_engine())
    except Exception as e:
        logger.warning("Autonomous engine shutdown import failed (non-fatal): %s", e)
    # R-F803: cancel ARIA-Coder background tasks. The tasks own httpx
    # clients (SovereignLLM + FlyDeployer); cancel propagates aclose.
    for _t in aria_coder_tasks:
        _t.cancel()
    if aria_coder_tasks:
        logger.info(
            "[R-F803] cancelled %d ARIA-Coder tasks on shutdown",
            len(aria_coder_tasks),
        )
    if knowledge_seed_task:
        knowledge_seed_task.cancel()
    if research_task:
        research_task.cancel()
    if self_improve_task:
        self_improve_task.cancel()
    if quiz_task:
        quiz_task.cancel()
    if reading_task:
        reading_task.cancel()
    if library_consolidate_task:
        library_consolidate_task.cancel()
    if runpod_sched_task:  # R-F1335
        runpod_sched_task.cancel()
    if memory_wal_task:  # R-F1342
        memory_wal_task.cancel()
    if proactive_task:
        proactive_task.cancel()
    if ocr_prewarm_task:
        ocr_prewarm_task.cancel()
    if rag_backfill_task:
        rag_backfill_task.cancel()
    if tender_monitor_task:
        tender_monitor_task.cancel()
    # R-F656 (2026-05-17): full-system audit found these 3 background
    # tasks were started in lifespan but never cancelled on shutdown.
    # Without cancel, the task survives the lifespan return, fly's
    # graceful-stop window expires before SIGKILL, and the loop
    # accumulates wakeups across deploys. Net effect on fly: deploy
    # latency creeps + intermittent worker-zombie warnings in logs.
    if reasoning_purge_task:
        reasoning_purge_task.cancel()
    if weekly_report_task:
        weekly_report_task.cancel()
    if watchlist_rescreen_task:
        watchlist_rescreen_task.cancel()
    # R-F2378: cancel every supervised background task, not just the legacy
    # hand-maintained subset above. Startup tasks moved off the pre-yield path
    # (ARIA-Coder scans, document-reader prewarm, web integrity start, etc.) can
    # still be running when a smoke test or deploy shutdown exits immediately.
    # Leaving them alive makes asyncio.run wait on executor/process cleanup and
    # can hang graceful-stop. Cancel before durability flushes below.
    try:
        _current_task = asyncio.current_task()
        _pending_bg = [
            _task for _task in list(_BG_TASKS)
            if _task is not _current_task and not _task.done()
        ]
        for _task in _pending_bg:
            _task.cancel()
        if _pending_bg:
            await asyncio.wait_for(
                asyncio.gather(*_pending_bg, return_exceptions=True),
                timeout=5.0,
            )
            logger.info("[R-F2378] cancelled %d supervised background tasks", len(_pending_bg))
    except asyncio.TimeoutError:
        logger.warning("[R-F2378] supervised background task cancellation exceeded 5.0s")
    except Exception as e:
        logger.warning("[R-F2378] supervised background task cancellation failed: %s", e)
    # R-F2761: brain_hook absorption tasks are created outside the supervised
    # lifespan set. Cancel and await their explicit owner before durability
    # flushes, otherwise they can keep writing while stores are shutting down.
    try:
        from .intel import brain_hook as _brain_hook_shutdown
        _cancelled_absorbs = await _brain_hook_shutdown.shutdown_background_tasks()
        if _cancelled_absorbs:
            logger.info(
                "[R-F2761] cancelled %d brain absorption tasks",
                _cancelled_absorbs,
            )
    except Exception as e:
        logger.warning("[R-F2761] brain absorption shutdown failed: %s", e)
    # F94: flush any pending knowledge writes to disk before exit so the
    # last <FLUSH_DEBOUNCE_S of in-memory mutations aren't lost on a
    # clean shutdown / deploy.
    try:
        await _shutdown_await("knowledge", knowledge.shutdown())
    except Exception as e:
        logger.warning("knowledge shutdown setup failed (non-fatal): %s", e)
    # F110: same protection for the intel ledger — without this, the last
    # ~2s of channel/ingest signals (and any sweep-burst mid-flush) are
    # lost on every deploy.
    try:
        await _shutdown_await("intel_ledger", intel_ledger.shutdown())
    except Exception as e:
        logger.warning("intel_ledger shutdown setup failed (non-fatal): %s", e)
    # R-F2417: flush any coalesced mastery write (R-F2408) so a graceful
    # restart persists the last learning signal. When ARIA_MASTERY_COALESCE_SAVE
    # is OFF (default) this is a no-op inline save (nothing deferred); when ON it
    # writes the pending whole-cache snapshot that a quiet period left unflushed.
    # Own try/except — a durability flush must NEVER block or raise during
    # shutdown (fly's graceful-stop window is short; F28/R-F2158 class).
    try:
        await _shutdown_await("[R-F2417] mastery flush", student.flush_mastery())
    except Exception as e:
        logger.warning("[R-F2417] mastery flush setup failed (non-fatal): %s", e)
    # R-F507: stop the crawler loop cleanly so we don't leak a fetch
    # across deploys. The loop checks the stop_event before sleeping;
    # if it's mid-fetch, the task.cancel() interrupt is also caught.
    try:
        if _crawler_stop_event is not None:
            _crawler_stop_event.set()
        if _crawler_task is not None:
            _crawler_task.cancel()
    except Exception as e:
        logger.warning("crawler shutdown failed (non-fatal): %s", e)
    # R-F504: close ARIA's own search index cleanly so WAL is flushed.
    try:
        from .search_index import db as _search_db_shut
        await _shutdown_await("search_index", _search_db_shut.close())
    except Exception as e:
        logger.warning("search_index.close setup failed (non-fatal): %s", e)
    # R-F2394: close SQLite state-store connections/read-pool so local smoke
    # tests and graceful stops do not leave non-daemon aiosqlite worker threads
    # alive after the lifespan context exits.
    try:
        from .intel import state_store as _state_store_shut
        await _shutdown_await("state_store", _state_store_shut.close())
    except Exception as e:
        logger.warning("state_store.close setup failed (non-fatal): %s", e)
    logger.info("ARIA Service shutting down")


# ── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="ARIA Intelligence API",
    description="Arkmurus Research Intelligence Agent — defence procurement, compliance, and geopolitical intelligence",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS for frontend — R-F2057: env-driven origin ALLOWLIST (root fix).
# Was `allow_origins=["*"]` + `allow_credentials=True` — an invalid/permissive combo
# that makes Starlette reflect ANY Origin back AND set Allow-Credentials:true, i.e.
# every site a logged-in user visits could make credentialed cross-origin calls.
# (Live exposure is limited because the browser reaches this API SAME-ORIGIN via the
# aria-web proxy, and auth is a Bearer token not a cookie — but a wildcard with
# credentials is never correct.) Now: an explicit, env-overridable allowlist, so a
# missed origin is a one-secret change (ARIA_CORS_ORIGINS) — never a redeploy.
#   - default: the real first-party web origins.
#   - ARIA_CORS_ORIGINS="a,b,c" → exactly those origins (credentials allowed).
#   - ARIA_CORS_ORIGINS="*"     → wildcard WITHOUT credentials (spec-correct opt-out).
_cors_env = (_os.getenv("ARIA_CORS_ORIGINS", "https://imaria.io,https://aria-web.fly.dev") or "").strip()
if _cors_env == "*":
    _cors_kwargs = {"allow_origins": ["*"], "allow_credentials": False}
else:
    _cors_kwargs = {
        "allow_origins": [o.strip() for o in _cors_env.split(",") if o.strip()] or ["https://imaria.io"],
        "allow_credentials": True,
    }
app.add_middleware(
    CORSMiddleware,
    allow_methods=["*"],
    allow_headers=["*"],
    **_cors_kwargs,
)

# R-F1853 (audit, DD stage 3) — global request-body size cap. Before this, ~150
# endpoints called `await request.json()` with no upper bound, so a single multi-GB
# or deeply-nested body could OOM the single-process brain. Header-only guard (no
# buffering): reject by Content-Length before any parsing. The cap is generous
# (default 50MB) so legitimate base64 documents pass; tune via ARIA_MAX_BODY_BYTES.
# Chunked requests with no Content-Length aren't caught here — the per-endpoint
# caps (read-document, etc.) backstop those.
import os as _bodylim_os
_MAX_BODY_BYTES = _env_int("ARIA_MAX_BODY_BYTES", 50 * 1024 * 1024)


@app.middleware("http")
async def _limit_body_size(request, call_next):
    _cl = request.headers.get("content-length")
    if _cl:
        try:
            if int(_cl) > _MAX_BODY_BYTES:
                from starlette.responses import JSONResponse
                return JSONResponse(
                    status_code=413,
                    content={"detail": f"request body too large (> {_MAX_BODY_BYTES} bytes)"},
                )
        except ValueError:
            pass
    return await call_next(request)


# R-F3730 — Cure Protocol Phase 0.3 runtime usage observation.
#
# The Phase 0.2 census found 109 DEAD-CANDIDATE modules and NONE is deletable:
# the three-proof rule needs a runtime proof, and NOTHING in either tier recorded
# that a route was called. This is that proof. The 14-day window cannot be
# reconstructed retrospectively, so it only starts once this is live.
#
# Deliberately the LAST middleware added, so it wraps the smallest surface and
# cannot interfere with the body-size guard above. It does NO I/O on the request
# path — record_route() is a sync in-memory counter increment, and the durable
# write is a coalesced fire-and-forget flush at most once per interval.
@app.middleware("http")
async def _observe_route_usage(request, call_next):
    response = await call_next(request)
    try:
        # Key on the ROUTE TEMPLATE, never request.url.path — an unbounded id
        # space in the raw path would explode the key set.
        route = request.scope.get("route")
        template = getattr(route, "path", None)
        if template:
            from aria_service.intel import cure_usage
            cure_usage.record_route(template, request.method)
            cure_usage.maybe_schedule_flush()
    except Exception:
        # Observability must never be able to break a request (CLAUDE.md §21a
        # wants the signal, but not at the cost of the user's response).
        pass
    return response


# Routes
app.include_router(aria_router)
app.include_router(vetting_router)   # R-F3138
app.include_router(vetting_portal_router)   # R-F3180 (unauthenticated)

# R-F2278: fail-loud (non-fatal) audit for duplicate (method, path) registrations.
# FastAPI serves the first-registered route for a colliding path, so a second
# handler becomes silent dead code (this shipped 3 times: R-F2150 stubs, the
# /dd/layer-5c/stats feature collision, the /ingest shadow). The CI test
# (test_rf2278_no_duplicate_routes) is the hard gate; this boot log makes any
# future slip-through visible in the fly logs instead of invisible.
#
# R-F3816 — the call MOVED into `lifespan` (see `_run_route_audit`). It used to run
# here, at module-import time, which was wrong in two ways that only showed up when
# it was live-verified:
#
#   1. IT AUDITED AN INCOMPLETE TABLE. Measured 2026-08-09: 754 routes here versus
#      770 after import finishes. `/static`, `/`, and the `/download/*` handlers are
#      registered BELOW this line, so ~16 routes were never audited at all — and a
#      duplicate among them was exactly the class this guard exists to catch.
#   2. ITS BRAIN SIGNAL WAS DROPPED. R-F3792 wired both branches to the brain, and
#      /api/aria/brain/stats showed NO `route_audit` module after the deploy that
#      shipped it — while `intel_ledger`, which wires the same way, was there. The
#      signal is emitted before the state store is ready, so it goes nowhere. A
#      wiring that emits into a store that cannot accept it is DARK by §21a, which
#      is precisely what R-F3792 set out to fix.
#
# Running it from lifespan fixes both: the table is complete, and the loop and store
# exist. Nothing is lost by the delay — this is a startup audit, not a request gate.

# R-F1241: Serve static files (ARIA demo page) + public demo endpoint
import os as _static_os
_static_dir = _static_os.path.join(_static_os.path.dirname(__file__), "static")
if _static_os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def serve_demo_page():
        """Serve the ARIA demo page at the root URL."""
        index_path = _static_os.path.join(_static_dir, "index.html")
        try:
            with open(index_path, encoding="utf-8") as f:
                return HTMLResponse(content=f.read())
        except Exception as e:
            return HTMLResponse(
                content=f"<html><body><h1>ARIA Demo</h1><p>Error loading page: {e}</p></body></html>",
                status_code=500,
            )

    @app.get("/download/client", response_class=HTMLResponse, include_in_schema=False)
    async def download_aria_client():
        """Download ARIA Client - tiny ZIP (~5KB), double-click aria.bat to start."""
        import zipfile, io
        client_folder = _static_os.path.join(_static_dir, "aria_client")
        try:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for root, dirs, files in _static_os.walk(client_folder):
                    # R-F2921 — SHIP the Python client inside the bundle.
                    #
                    # This used to skip every .py so that aria.bat could fetch them at
                    # runtime with `New-Object Net.WebClient; $w.DownloadFile(...)` and
                    # then execute the result. That is the textbook downloader-dropper
                    # shape, and on 2026-07-23 Kaspersky File Anti-Virus deleted
                    # aria.bat from a checkout with verdict "Trojan" (initiator
                    # git.exe). It also blocked the file on download with its own
                    # HTTP 499 block page, so a customer on Kaspersky could not obtain
                    # the client at all.
                    #
                    # The detection was CORRECT in pattern: the downloaded script was
                    # executed with no hash, signature or integrity check of any kind,
                    # so anyone able to MITM or compromise the endpoint had arbitrary
                    # code execution on a customer's Windows machine. Excluding the
                    # file from the antivirus would have silenced an accurate detector
                    # on a real weakness.
                    #
                    # Shipping the .py files in the same ZIP removes the reason to
                    # download anything at runtime, which removes both the RCE path and
                    # the malware signature. __pycache__/.pyc stay out — build noise.
                    dirs[:] = [d for d in dirs if d != "__pycache__"]
                    for f in files:
                        if f.endswith(".pyc"):
                            continue
                        file_path = _static_os.path.join(root, f)
                        arcname = _static_os.path.relpath(file_path, client_folder)
                        zf.write(file_path, arcname)
            buf.seek(0)
            from fastapi.responses import Response
            return Response(
                content=buf.getvalue(),
                media_type="application/zip",
                headers={
                    "Content-Disposition": "attachment; filename=ARIA_Client.zip",
                },
            )
        except Exception as e:
            return HTMLResponse(
                content=f"<html><body><h1>Download Error</h1><p>{e}</p></body></html>",
                status_code=500,
            )

    @app.get("/download/aria.py", response_class=HTMLResponse, include_in_schema=False)
    async def download_aria_py():
        """Download the ARIA Python client (aria.py) — for auto-download from .bat."""
        py_path = _static_os.path.join(_static_dir, "aria_client", "aria.py")
        try:
            with open(py_path, encoding="utf-8") as f:
                content = f.read()
            from fastapi.responses import Response
            return Response(
                content=content,
                media_type="text/x-python",
                headers={
                    "Content-Disposition": "attachment; filename=aria.py",
                },
            )
        except Exception as e:
            return HTMLResponse(
                content=f"<html><body><h1>Download Error</h1><p>{e}</p></body></html>",
                status_code=500,
            )

    @app.get("/download/aria_tui.py", response_class=HTMLResponse, include_in_schema=False)
    async def download_aria_tui_py():
        """Download the ARIA TUI client (aria_tui.py) — for auto-download from .bat."""
        tui_path = _static_os.path.join(_static_dir, "aria_client", "aria_tui.py")
        try:
            with open(tui_path, encoding="utf-8") as f:
                content = f.read()
            from fastapi.responses import Response
            return Response(
                content=content,
                media_type="text/x-python",
                headers={
                    "Content-Disposition": "attachment; filename=aria_tui.py",
                },
            )
        except Exception as e:
            return HTMLResponse(
                content=f"<html><body><h1>Download Error</h1><p>{e}</p></body></html>",
                status_code=500,
            )

    @app.get("/token", response_class=HTMLResponse, include_in_schema=False,
             dependencies=[Depends(require_aria_token)])  # R-F1347: was PUBLIC — leaked the master token
    async def token_page(request: Request):
        """Show the user their API token for use with the terminal client.

        R-F1347: GATED behind require_aria_token. This page renders the full
        bearer token; without the gate it was a public master-key leak
        (anyone could GET /token and control all routes). The operator
        retrieves the token from fly secrets to bootstrap; this page is a
        convenience for already-authenticated callers only.
        """
        token = _os.getenv("ARIA_API_TOKEN", "")
        masked = token[:8] + "..." + token[-4:] if len(token) > 12 else "(not set)"
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ARIA — Get Your API Token</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    background: #0a0a0f; color: #e0e0e8;
    font-family: 'SF Mono', 'Cascadia Code', 'Fira Code', monospace;
    font-size: 14px; line-height: 1.6;
    min-height: 100vh; display: flex; align-items: center; justify-content: center;
  }}
  .card {{
    max-width: 600px; width: 90%; margin: 40px auto;
    background: #12121a; border: 1px solid #1e1e2e; border-radius: 12px;
    padding: 40px;
  }}
  h1 {{ font-size: 24px; margin-bottom: 8px; color: #6c5ce7; }}
  p {{ color: #8888a0; margin-bottom: 24px; }}
  .token-box {{
    background: #0a0a0f; border: 1px solid #1e1e2e; border-radius: 8px;
    padding: 16px; font-size: 13px; word-break: break-all;
    color: #00e676; margin-bottom: 24px;
    user-select: all;
  }}
  .token-box:hover {{ border-color: #6c5ce7; }}
  .step {{ margin-bottom: 16px; padding: 12px; background: #0a0a0f; border-radius: 8px; }}
  .step-num {{ color: #6c5ce7; font-weight: bold; }}
  code {{ background: #1e1e2e; padding: 2px 6px; border-radius: 4px; font-size: 12px; }}
  .btn {{
    display: inline-block; padding: 10px 20px; border-radius: 8px;
    background: #6c5ce7; color: #fff; text-decoration: none;
    font-family: inherit; font-size: 14px; cursor: pointer;
    border: none; margin-top: 8px;
  }}
  .btn:hover {{ background: #7c6cf7; }}
  .footer {{ margin-top: 24px; color: #555; font-size: 12px; text-align: center; }}
</style>
</head>
<body>
<div class="card">
  <h1>🔑 ARIA API Token</h1>
  <p>Use this token to connect from the terminal client.</p>

  <div class="token-box">{token or "(no token configured on this server)"}</div>

  <h2 style="font-size:16px;margin-bottom:16px;">How to use it</h2>

  <div class="step">
    <span class="step-num">1.</span>
    Download the ARIA client from <a href="/download/client" style="color:#6c5ce7;">here</a>
    and extract the ZIP.
  </div>

  <div class="step">
    <span class="step-num">2.</span>
    Double-click <code>aria.bat</code> to start.
  </div>

  <div class="step">
    <span class="step-num">3.</span>
    When prompted, paste this token:
    <br>
    <code style="display:block;margin-top:8px;padding:8px;font-size:13px;word-break:break-all;">{token}</code>
  </div>

  <div class="step">
    <span class="step-num">4.</span>
    Start asking questions!
  </div>

  <a href="/download/client" class="btn">⬇ Download ARIA Client</a>

  <div class="footer">
    Token: {masked} &middot; <a href="/" style="color:#555;">Back to ARIA</a>
  </div>
</div>
</body>
</html>"""
        return HTMLResponse(content=html)

    @app.post("/api/aria/client/chat",
              dependencies=[Depends(require_aria_token)])  # R-F1347: was unauth full-LLM spend
    async def aria_client_chat(request: Request):
        """Chat endpoint for the ARIA terminal client.

        Proxies to the real ARIA chat engine (routes.aria.chat_ep) so the
        terminal client gets full intelligence — intent detection, web
        research, tool execution, LLM reasoning, and verification.
        """
        body = await request.json()
        message = (body.get("message") or "").strip()
        user = (body.get("user") or "user").strip()

        if not message:
            raise HTTPException(status_code=400, detail="message required")

        # Build a ChatRequest and delegate to the real chat endpoint
        from .routes.aria import ChatRequest, chat_ep

        chat_req = ChatRequest(
            message=message,
            session_id=f"client_{user}",
            user_id=user,
            auto_tools=True,
        )
        result = await chat_ep(chat_req, request)
        return result

    @app.post("/api/aria/client/analyse",
              dependencies=[Depends(require_aria_token)])  # R-F1347: was unauth full-LLM spend
    async def aria_client_analyse(request: Request):
        """Analyse code endpoint for the ARIA terminal client.

        Proxies to the real ARIA chat engine with a code-analysis prompt,
        so the terminal client gets full ARIA intelligence on the code.
        """
        body = await request.json()
        code = (body.get("code") or "").strip()

        if not code:
            raise HTTPException(status_code=400, detail="code required")

        # Delegate to the real chat endpoint with a code-analysis prompt
        from .routes.aria import ChatRequest, chat_ep

        analysis_prompt = (
            f"Analyse the following code and provide:\n"
            f"1. A summary of what it does\n"
            f"2. Any bugs, issues, or anti-patterns\n"
            f"3. Specific fixes for each issue\n\n"
            f"```python\n{code}\n```"
        )
        chat_req = ChatRequest(
            message=analysis_prompt,
            session_id="client_analyse",
            user_id="client",
            auto_tools=False,
        )
        result = await chat_ep(chat_req, request)
        return {"analysis": result.get("response", ""), "fixes": result.get("response", "")}

    @app.post("/api/aria/coder/demo")
    async def aria_coder_demo_ep(request: Request):
        """Public demo endpoint — no auth required.

        Takes a description and existing code, runs ARIA's autonomous coding
        engine (no LLM), and returns the analysis + generated fix.
        """
        body = await request.json()
        description = (body.get("description") or "").strip()
        code = (body.get("code") or "").strip()

        if not description:
            raise HTTPException(status_code=400, detail="description required")

        if not code:
            code = 'def process_item(data):\n    result = data["value"] * 2\n    return result\n'

        try:
            from .intel.autonomous_coder import AutonomousCoder
            coder = AutonomousCoder()

            from .autonomous.gap_detector import Gap, GapType, GapSeverity
            gap = Gap(
                gap_id="demo_gap",
                gap_type=GapType.MODULE_BUG,
                severity=GapSeverity.MEDIUM,
                title=description[:80],
                description=description,
                module="demo_module",
            )
            plan = await coder.generate_fix_plan(gap, code)
            code_result = await coder.write_code(plan, code, "demo_module.py")

            return {
                "plan": {
                    "title": plan.get("title"),
                    "approach": plan.get("approach"),
                    "risk_level": plan.get("risk_level"),
                    "target_files": plan.get("target_files"),
                },
                "code": code_result.get("code", ""),
                "source": code_result.get("source", "unknown"),
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)[:500])


@app.get("/health/live")
def health_live_top_level():
    """R-F690 (2026-05-18) — top-level /health/live alias.

    R-F2484 (2026-07-08) — DELIBERATELY SYNC `def`, not `async def` (extends the
    R-F723 fix from the canonical /api/aria/health/live to this alias). FastAPI
    auto-runs sync routes in starlette's threadpool, which is NOT blocked when the
    asyncio event loop is wedged by sync work (the aiosqlite writer thread under
    state_store saturation). As `async def` this alias ran ON the event loop and
    timed out at 25s during a loop stall (live probe 2026-07-08); as a threadpool
    route it stays near-instant. Reads only the in-process ARIA_BUILD_REV global.

    Live fly logs 2026-05-18 10:43:28 / 10:47:15 / 10:52:53 showed
    monitoring callers hitting `/health/live` without the
    `/api/aria/` prefix and getting 404. The canonical
    `/api/aria/health/live` (R-F372) is the fly.io load-balancer
    probe path; this alias keeps Kubernetes-style monitoring tools
    (which default to `/health/live`) happy without forcing them to
    re-config.

    Same payload shape as the canonical handler — no Redis, no
    stats, in-process only."""
    try:
        _build_rev = ARIA_BUILD_REV
    except Exception:
        _build_rev = "unknown"
    return {"status": "alive", "build_rev": _build_rev}


# R-F2487 — /health self-diagnostic read cache. /health is polled frequently
# (status pages + web's cross-health probe); reading crucix:self_diagnostic:latest
# from the state_store on EVERY request meant every concurrent poll hit the
# (sometimes saturated) store, spiking p95 under load. The diagnostic is refreshed
# only every ~15min by an autonomous task, so a short TTL cache is safe: at most
# one request per _HEALTH_DIAG_TTL_S touches the store; the rest serve the last
# snapshot instantly (and keep serving it if a refresh times out).
_HEALTH_DIAG_CACHE: dict = {"data": None, "ts": 0.0}
_HEALTH_DIAG_TTL_S = 30.0


async def _read_self_diagnostic_cached():
    import os as _h_os
    import time as _h_time
    now = _h_time.monotonic()
    cached = _HEALTH_DIAG_CACHE["data"]
    if cached is not None and (now - _HEALTH_DIAG_CACHE["ts"]) < _HEALTH_DIAG_TTL_S:
        return cached
    try:
        from .intel import redis_store as rs
        latest = await asyncio.wait_for(
            rs.get_json("crucix:self_diagnostic:latest"),
            timeout=float(_h_os.getenv("ARIA_HEALTH_READ_TIMEOUT_S", "3")),
        )
        _HEALTH_DIAG_CACHE["data"] = latest
        _HEALTH_DIAG_CACHE["ts"] = now
        return latest
    except Exception:
        # Timeout/error under saturation -> serve the last snapshot (better than
        # blocking the whole /health response on a slow store read).
        return cached


@app.get("/health")
async def health():
    """Public liveness + minimal autonomy state.

    Deliberately exposes only boolean indicators — no task IDs, no run
    history, no vendor credentials. The authed /api/aria/autonomous/
    status endpoint is the rich view; this one is safe to publish on a
    status page.
    """
    llm = app.state.llm_provider
    llm_stats = {}
    llm_chain: dict = {}
    if hasattr(llm, "get_stats"):
        llm_stats = llm.get_stats()
    if hasattr(llm, "get_health"):
        # Chain-level summary — "resilient" is the load-bearing signal;
        # raw per-provider stats stay in llm_fallback_stats for operators.
        # A cooling provider is the fallback chain WORKING — status should
        # only flip to degraded when no provider can serve.
        llm_chain = llm.get_health()

    # Autonomy indicator — is the 24/7 loop actually running right
    # now? Boolean only, plus the last-tick age so an observer can
    # tell "enabled but stuck" from "enabled and ticking".
    autonomous_ind = {
        "enabled": False,
        "running": False,
        "dry_run": True,
        "autonomy_level": 0,
        "seconds_since_last_tick": None,
        # R-F4296 (C-250) — the THIRD state. Without these an observer reading a
        # large `seconds_since_last_tick` cannot tell a running task from a loop
        # that has stopped iterating, and the rollup below called both "stalled".
        "busy_with": None,
        "busy_seconds": None,
        "tasks_loaded": 0,
    }
    # R-F4296 — initialised BEFORE the try. If the engine import fails the
    # indicator stays `enabled: False`, and "we could not look" must not read as
    # a stalled loop — nor raise NameError at the rollup, which is the §9
    # local-scoping class that took prod down once already.
    autonomous_healthy = True
    try:
        from .autonomous import engine as _eng, tasks as _tsk
        status = _eng.get_engine_status()
        autonomous_ind["enabled"] = bool(status.get("enabled"))
        autonomous_ind["running"] = bool(status.get("running"))
        autonomous_ind["dry_run"] = bool(status.get("dry_run"))
        autonomous_ind["autonomy_level"] = int(status.get("autonomy_level", 0))
        last_tick = status.get("last_tick_at")
        import time
        if last_tick:
            autonomous_ind["seconds_since_last_tick"] = int(time.time() - last_tick)
        _busy_since = status.get("busy_since")
        if status.get("busy_task_id") and _busy_since:
            autonomous_ind["busy_with"] = str(status.get("busy_task_id"))
            autonomous_ind["busy_seconds"] = int(time.time() - float(_busy_since))
        # R-F4296 (C-250) — ONE measure. main.py used to carry its own copy of the
        # rule and it drifted from the heartbeat it was meant to agree with; that
        # fork IS the defect, so the verdict is read, never re-derived here.
        autonomous_healthy = bool(_eng.autonomy_is_healthy(status))
        try:
            autonomous_ind["tasks_loaded"] = len(_tsk.get_loaded_tasks())
        except Exception:
            pass
    except Exception:
        pass

    # Health rollup — service is operational only if LLM is configured AND
    # (autonomous is off OR autonomous is running healthily). A stuck autonomous
    # loop is worse than off. The verdict itself lives in
    # `engine.autonomy_is_healthy` (R-F4296) — see the assignment above.

    # Self-diagnostic rollup (2026-04-18) — safe-to-publish summary of
    # module wiring health. Detailed report at /api/aria/diagnostic/details
    # behind auth. Read the cached result (refreshed every 15min by the
    # autonomous task) so /health stays fast.
    diagnostic_ind: dict = {"overall": "UNKNOWN"}
    try:
        # R-F2152 — /health is a PUBLIC status endpoint and MUST stay fast. The
        # only awaitable in the handler is this cached diagnostic read (everything
        # else is in-memory); R-F2487 wraps it in a 30s TTL cache so concurrent
        # polls don't each hit the (sometimes saturated) state_store — the read is
        # bounded (ARIA_HEALTH_READ_TIMEOUT_S) and degrades to the last snapshot /
        # UNKNOWN instead of hanging.
        latest = await _read_self_diagnostic_cached()
        if latest:
            diagnostic_ind = {
                "overall": latest.get("overall"),
                "counts": latest.get("counts"),
                "critical_failures": latest.get("critical_failures", []),
                "generated_at": latest.get("generated_at"),
            }
    except Exception:
        pass

    # Top-level status: "operational" iff the chain can serve a request
    # (≥1 non-cooling provider) AND the autonomous loop isn't stuck. A
    # cooling primary with a live fallback is NOT degraded — the chain
    # is doing its job.
    chain_resilient = llm_chain.get("resilient") if llm_chain else bool(llm and llm.is_configured)

    # R-F762 (2026-05-20) — state-backend health surfaced. Pre-R-F762
    # a Redis-unreachable boot silently fell back to in-memory dict
    # and the operator had no signal from /health that knowledge was
    # not being persisted. Now: reachable=False rolls up into degraded
    # status so any monitor watching /health sees the regression
    # without log scraping.
    state_backend_ind = {
        "backend": getattr(app.state, "state_backend", "unknown"),
        "reachable": bool(getattr(app.state, "state_backend_reachable", True)),
    }
    state_backend_ind["status"] = "green" if state_backend_ind["reachable"] else "red"

    # R-F4107 (C-140) — reachability is point-in-time and has no memory. On
    # 2026-08-17 the store timed out on 25 distinct keys in two minutes and this
    # block still read green five minutes later, because nothing here could
    # remember. `read_timeouts` is that memory; an amber status distinguishes
    # "answering now but recently blind" from a genuinely healthy store.
    try:
        from .intel.state_store import read_timeout_report as _rtr
        _rt = _rtr()
        state_backend_ind["read_timeouts"] = _rt
        if _rt.get("degraded") and state_backend_ind["status"] == "green":
            state_backend_ind["status"] = "amber"
    except Exception as _rt_err:
        # Could not measure is NOT healthy — say so rather than certify (C-96).
        state_backend_ind["read_timeouts"] = {"unmeasurable": True,
                                              "error": str(_rt_err)[:120]}

    # R-F2849 — loop-lag gauge (sync snapshot; never touches the loop).
    try:
        from .intel.loop_monitor import snapshot as _loop_snapshot
        _loop_health = _loop_snapshot()
    except Exception:
        _loop_health = {"status": "unknown"}
    # ── R-F3704 — /health must not certify over a degraded estate ───────────
    #
    # THE DEFECT, measured live 2026-08-04 at the same moment:
    #   GET /health            -> "status": "operational", diagnostic GREEN
    #   GET /api/aria/health/perf -> "status": "degraded",
    #        degraded_reasons: [mode_degraded, ecosystem_red_nodes_1,
    #                           ecosystem_degraded_nodes_22]
    #
    # This expression only asked "can the chain serve, is the loop ticking, is
    # the store reachable" — a LIVENESS probe wearing a HEALTH probe's name. It
    # is also the one Fly's health check and any external monitor watch, so a
    # majority-degraded estate reported green forever, which is exactly the
    # "status page divorced from reality" failure R-F3667 fixed on the OTHER
    # surface and not this one.
    #
    # `operating_mode` is included because it is load-bearing, not cosmetic:
    # DEGRADED suppresses external delivery (operating_modes.py:189), so
    # "operational" while WhatsApp briefs are being dropped is a false clean.
    #
    # Deliberately kept SEPARATE from liveness: `/health/live` remains the pure
    # is-the-process-up probe, so nothing that reads it for restart decisions
    # starts flapping on a quality signal.
    _degraded_reasons: list[str] = []
    if not chain_resilient:
        _degraded_reasons.append("llm_chain_exhausted")
    if not autonomous_healthy:
        _degraded_reasons.append("autonomous_loop_stalled")
    if not state_backend_ind["reachable"]:
        _degraded_reasons.append("state_backend_unreachable")
    # R-F4107 (C-140) — a burst that blinded 25 keys must reach the verdict, not
    # sit in a sub-field nobody opens. That is the C-96 lesson: publishing a
    # number no verdict consumes is why the degradation went unnoticed.
    if (state_backend_ind.get("read_timeouts") or {}).get("degraded"):
        _degraded_reasons.append("state_backend_read_timeouts")
    try:
        from .intel import operating_modes as _om_h
        _mode_now = await _om_h.get_mode()
        if _mode_now != _om_h.Mode.NORMAL:
            _degraded_reasons.append(f"operating_mode_{_mode_now.name.lower()}")
    except Exception as _mode_err:
        # UNKNOWN is not healthy, but it is also not a measured failure — say so
        # rather than silently certifying.
        _degraded_reasons.append("operating_mode_unknown")
        _log_health = logging.getLogger("aria.main")
        _log_health.debug("[R-F3704] operating mode unreadable: %s", _mode_err)
    if diagnostic_ind and str(diagnostic_ind.get("overall", "")).upper() == "RED":
        _degraded_reasons.append("self_diagnostic_red")
    # R-F4024 (C-96) — the loop gauge is RIGHT THERE in this payload; read it.
    _degraded_reasons.extend(_loop_degraded_reasons(_loop_health))
    # R-F4261 — and so is the vendor-credit gauge. Same lesson, same payload:
    # `severity: "low"` at $7.61 on a depth-1 chain read as `operational` with
    # an empty reasons list until this line existed.
    _degraded_reasons.extend(
        _vendor_balance_degraded_reasons((llm_chain or {}).get("vendor_balance")))

    return {
        "loop": _loop_health,
        "status": "operational" if not _degraded_reasons else "degraded",
        # R-F3704 — name WHICH signal degraded, so the operator can act
        # surgically instead of guessing (same contract /health/perf uses).
        "degraded_reasons": _degraded_reasons,
        "service": "aria",
        "llm_provider": llm.name if llm else "none",
        "llm_configured": bool(llm and llm.is_configured),
        "llm_chain": llm_chain,
        "llm_fallback_stats": llm_stats,
        "autonomous": autonomous_ind,
        "diagnostic": diagnostic_ind,
        "state_backend": state_backend_ind,
    }


# ── R-F1643: Phase A gate surface — reads from live scorer/eval, not markdown ──


@app.get("/health/composite")
async def health_composite():
    """R-F1643 — Live composite autonomy score from compute_composite().

    Returns the same payload as autonomy_scorer.compute_composite() so the
    status surface reads from ground truth, not from a human-edited document.
    Editing CLAUDE.md or AGENTS.md does NOT change this endpoint's output.
    """
    try:
        from .intel.autonomy_scorer import compute_composite
        return await compute_composite()
    except Exception as e:
        from .intel.engine_wiring import wire_failure as _wf1643
        _wf1643(module="health_composite", detail=str(e), gap_type="source_failure", source="health_composite")
        return {"composite_score": None, "error": str(e), "source": "compute_composite() failed"}


@app.get("/phase/gates")
async def phase_gates():
    """R-F1643 — Live Phase A gate status from scorer/eval/heatmap.

    Every gate value reads from a live probe — NOT from CLAUDE.md or any
    human-edited document. Editing markdown does NOT change these values.

    R-F2639: this endpoint no longer MEASURES anything. It renders
    intel.phase_gates.compute_phase_gates() — the one canonical measure that
    /api/aria/phase/gates also renders. Two aggregators previously disagreed
    per-gate (the fork served the vacuous gate-#3 pass R-F2622 killed, and
    closed operator-owned gate #7 from ARIA's own chat sessions). Do NOT
    re-add measurement logic here: one measure, one verdict.
    """
    from .intel.phase_gates import compute_phase_gates

    result = await compute_phase_gates()
    # Render the canonical records into this endpoint's historical shape:
    # dict keyed by gate key, with `source` rather than `evidence`.
    gates = {}
    for key, g in result["gates"].items():
        rec = {k: v for k, v in g.items() if k not in ("id", "key", "title", "evidence")}
        rec["source"] = g["evidence"]
        gates[key] = rec
    return {
        "gates": gates,
        "summary": result["summary"],
        "sources_consulted": result["sources_consulted"],
        "note": result["note"],
    }


@app.get("/diagnostic")
async def public_diagnostic():
    """Public diagnostic summary — binary PASS/FAIL per module cluster.
    No per-check notes, no infra details. Safe to publish on status
    page. Rich details at /api/aria/diagnostic/details (auth required)."""
    try:
        from .intel import self_diagnostic as _sd
        return await _sd.run_diagnostic_summary()
    except Exception as e:
        return {"ok": False, "overall": "UNKNOWN", "error": str(e)[:200]}


@app.post("/api/aria/zoom/webhook")
async def zoom_webhook_ep(request: Request):
    """Zoom webhook receiver — NOT auth-protected (Zoom sends its own signature).

    Handles:
      - recording.completed → auto-download + process transcript
      - meeting.ended → log metadata
      - endpoint.url_validation → Zoom verification challenge
    """
    try:
        from .intel import zoom_integration as zoom
        body = await request.json()

        # Verify Zoom signature. R-F1349: when a secret is configured the
        # signature is REQUIRED — previously `if _WEBHOOK_SECRET and signature:`
        # let an attacker bypass the whole check by simply OMITTING the
        # x-zm-signature header (empty → falsy → skipped), reaching the
        # download/SSRF path unauthenticated. Now: secret set → must have a
        # valid signature, else 401.
        signature = request.headers.get("x-zm-signature", "")
        timestamp = request.headers.get("x-zm-request-timestamp", "")
        if zoom._WEBHOOK_SECRET:
            raw_body = await request.body()
            if not signature or not zoom.verify_webhook_signature(raw_body, signature, timestamp):
                from fastapi import HTTPException
                raise HTTPException(
                    status_code=401,
                    detail="Invalid or missing Zoom webhook signature",
                )

        llm = getattr(app.state, "llm_provider", None)
        result = await zoom.handle_webhook(body, llm=getattr(app.state, "llm_provider", None))
        return result
    except HTTPException:
        # R-F1349: let the 401 signature rejection propagate — the broad
        # except below otherwise swallowed it and returned 200 (auth bypass).
        raise
    except Exception as e:
        logger.warning("Zoom webhook error: %s", e)
        return {"error": str(e)}


async def _process_sweep_ingest(data: dict) -> dict:
    """Process accepted Node sweep data into ledger, neural memory, and anomaly watch."""
    app.state.current_data = data
    ledger_count = await intel_ledger.ingest_sweep_signals(data)
    comp_count = await competitors.scan_for_moves(data)

    # Grow neural network from sweep signals.
    # Live observation 2026-04-27 17:35:23-17:35:34: a single sweep with 5
    # signals + 4 news items fired 9 sequential DeepSeek calls and held the
    # ingest connection open for 12 seconds. Parallelize with concurrency
    # cap so the rate limiter (RPM-bounded) still gates spend, and so one
    # slow item doesn't head-of-line-block the rest.
    #
    # Safety: learn_from_text mutates module-level _neurons / _edges via
    # SYNC helpers between awaits. Two parallel tasks cannot corrupt the
    # store -- each task runs its sync mutation block atomically per
    # async-scheduling-window. _persist() races are benign last-writer-wins.
    #
    # Cost lever: ARIA_NEURAL_SAMPLE_RATE (0.0-1.0, default 1.0) skips
    # the LLM-supplement on a fraction of items. Regex extract_concepts
    # still runs on all items (free), so neuron/edge creation is
    # preserved -- only the LLM-driven novel-entity catch is sampled.
    # 0.25 ≈ 75% reduction in DeepSeek/Anthropic spend on neural ingest.
    import random as _random
    raw_rate = _os.getenv("ARIA_NEURAL_SAMPLE_RATE", "1.0") or "1.0"
    try:
        sample_rate = max(0.0, min(1.0, float(raw_rate)))
    except ValueError:
        sample_rate = 1.0

    neural_count = 0
    llm = getattr(app.state, "llm_provider", None)
    sem = asyncio.Semaphore(5)

    async def _learn_one(text: str, source: str) -> int:
        async with sem:
            item_llm = llm if (sample_rate >= 1.0 or _random.random() < sample_rate) else None  # nosec B311
            try:
                result = await neural_memory.learn_from_text(text, source=source, llm=item_llm)
                return result.get("neurons_activated", 0)
            except Exception as e:
                logger.warning("Neural ingest item failed (%s): %s", source, e)
                return 0

    learn_tasks: list = []
    signals = data.get("signals") or data.get("urgentSignals") or []
    for sig in signals[:20]:
        text = sig.get("text") or sig.get("content") or ""
        if text:
            learn_tasks.append(_learn_one(text, "sweep"))
    for item in (data.get("news") or [])[:10]:
        text = (item.get("title", "") + " " + item.get("summary", "")).strip()
        if text:
            learn_tasks.append(_learn_one(text, "news"))
    if learn_tasks:
        results = await asyncio.gather(*learn_tasks)
        neural_count = sum(results)

    # ── PROACTIVE: anomaly watch fires on every sweep ──────────────────
    # Looks at the fresh sweep data for spikes vs the rolling baseline
    # and pushes alerts to the proactive queue if anything stands out.
    anomaly_alerts = 0
    anomaly_failed = False
    try:
        anomaly_alerts = await proactive.anomaly_watch(data)
    except Exception as e:
        anomaly_failed = True
        logger.warning("Proactive anomaly watch failed: %s", e)

    # R-F973 (§21a): wire the success path to the brain. Pre-R-F973 this
    # returned counts to the Node tier but emitted NO brain signal, so the
    # largest cross-tier data path was invisible to ARIA's self-introspection
    # (health_perf_ep could cite knowledge_facts but not "what the sweep
    # ingested"). Use the lightweight per-module signal recorder (a §21a
    # metric, NOT absorb): it surfaces the ingest in brain_hook.get_stats() and
    # health_perf cross_tier WITHOUT re-running neural learning on a meta-summary
    # (the sweep already learns its own signals/news above via _learn_one) or
    # adding latency to the ingest connection. anomaly-watch failure flips the
    # signal to unsuccessful so a degrading sweep stays visible.
    try:
        from .intel import brain_hook as _bh_sweep
        await _bh_sweep._record_signal("ingest_sweep", success=not anomaly_failed)
    except Exception as _bh_e:
        logger.debug("ingest_sweep brain signal-record failed: %s", _bh_e)

    return {
        "ok": True,
        "ledger_signals_added": ledger_count,
        "competitor_moves_added": comp_count,
        "neurons_activated": neural_count,
        "anomaly_alerts_pushed": anomaly_alerts,
    }


async def _record_sweep_ingest_failure(reason: str, detail: str) -> None:
    try:
        from .intel import capability_gaps as _cg
        await _cg.record_gap(
            gap_type="file_parse",
            detail=f"ingest_sweep {reason}: {detail[:300]}",
            source="ingest_sweep",
        )
    except Exception as _cg_e:
        logger.debug("ingest_sweep gap-record failed: %s", _cg_e)


async def _process_sweep_ingest_background(data: dict) -> None:
    try:
        result = await _process_sweep_ingest(data)
        logger.info(
            "ingest_sweep async complete: ledger=%s competitors=%s neurons=%s anomalies=%s",
            result.get("ledger_signals_added"),
            result.get("competitor_moves_added"),
            result.get("neurons_activated"),
            result.get("anomaly_alerts_pushed"),
        )
    except Exception as e:
        logger.warning("ingest_sweep async processing failed: %s", e)
        await _record_sweep_ingest_failure("async_processing_failed", str(e))


@app.post("/api/aria/ingest", dependencies=[Depends(require_aria_token)])
async def ingest_sweep(request: Request):
    """Receive sweep data from Node.js server to update intel layers + neural network.

    Auth-protected: writes to persistent intel/neural state, so this endpoint
    must NOT be reachable without the bearer token. Mounted on `app` directly
    rather than via `aria_router` for historical reasons, so the token check
    is wired in explicitly here instead of inheriting it from the router.

    Body parse is manual rather than `data: dict` so validation failures log
    the offending payload (first 200 bytes) instead of returning an opaque
    FastAPI 422. Past symptom: a single 422 appeared in the log with no way
    to tell whether it was malformed JSON, a non-dict top-level, or a shape
    mismatch from the WhatsApp mirror (which posts WA-shaped payloads here).
    """
    try:
        raw = await request.body()
    except Exception as e:
        logger.warning("ingest: body read failed: %s", e)
        await _record_sweep_ingest_failure("body_read_failed", str(e))
        raise HTTPException(status_code=400, detail="body_read_failed")

    try:
        data = json.loads(raw) if raw else {}
    except Exception as e:
        preview = (raw[:200] if raw else b"").decode("utf-8", errors="replace")
        logger.warning("ingest: JSON parse failed (%s). Body first 200b: %r", e, preview)
        await _record_sweep_ingest_failure("invalid_json", f"{e} | {preview}")
        raise HTTPException(status_code=400, detail="invalid_json")

    if not isinstance(data, dict):
        logger.warning(
            "ingest: expected dict body, got %s. Preview: %r",
            type(data).__name__, str(data)[:200],
        )
        await _record_sweep_ingest_failure("expected_dict_body", f"got {type(data).__name__}")
        raise HTTPException(status_code=400, detail="expected_dict_body")

    if request.headers.get("x-aria-ingest-async") == "1":
        app.state.current_data = data
        _bg_task(asyncio.create_task(_process_sweep_ingest_background(data), name="sweep_ingest_async"))
        return {"ok": True, "accepted": True, "mode": "async"}

    return await _process_sweep_ingest(data)


# ── CLI entrypoint ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    _host = settings.host
    _port = settings.effective_port

    if _host == "::":
        # R-F842 (2026-05-23): manual dual-stack socket.
        #
        # R-F838 set ARIA_HOST=:: so .internal (IPv6/6PN) calls reach
        # aria-intel — but uvicorn's host="::" is IPv6-only on Linux
        # (IPV6_V6ONLY defaults ON for Python sockets). That broke
        # Fly's healthcheck + public proxy (both IPv4-only): aria-intel
        # showed "1 critical · connect: no route to host" until this
        # patch.
        #
        # Fix: create the listening socket ourselves, set
        # IPV6_V6ONLY=0, and pass the fd to uvicorn. The socket then
        # accepts BOTH IPv4 (mapped as ::ffff:x.x.x.x) and IPv6
        # connections. One process, one port, both stacks.
        import socket as _socket
        sock = _socket.socket(_socket.AF_INET6, _socket.SOCK_STREAM)
        sock.setsockopt(_socket.IPPROTO_IPV6, _socket.IPV6_V6ONLY, 0)
        sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        sock.bind(("::", _port))
        sock.listen(2048)
        sock.set_inheritable(True)
        uvicorn.run(
            "aria_service.main:app",
            fd=sock.fileno(),
            reload=False,
            workers=_web_concurrency(),  # R-F2174: default 1 = unchanged
        )
    else:
        uvicorn.run(
            "aria_service.main:app",
            host=_host,
            port=_port,
            reload=False,
            workers=_web_concurrency(),  # R-F2174: default 1 = unchanged
        )

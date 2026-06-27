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
    """R-F1421 — run each (name, async init_fn) in order, ISOLATING failures.

    Pre-R-F1421 the intel inits were bare `await x.init()` with no guard: one
    throw made the lifespan raise → uvicorn never reached `yield` → the app
    never served → TOTAL OUTAGE (the 2026-04-27 F28 class). A degraded-but-up
    ARIA that surfaces which subsystem failed beats a fully-dark one. Returns
    the list of failed subsystem names (empty = all ok). Module-level + pure
    over its inputs so the isolation is unit-testable.
    """
    failed = []
    for name, fn in inits:
        try:
            await fn()
        except Exception as e:  # noqa: BLE001 — isolate per-subsystem
            failed.append(name)
            try:
                logger.error(
                    "[R-F1421] intel init '%s' FAILED at boot (degrading, "
                    "staying up): %s", name, e, exc_info=True,
                )
            except Exception:
                pass
    return failed


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
# R-F786 (2026-05-21) — eager-import document_reader so the first
# /api/aria/document/extract call doesn't pay the import cost on the
# response loop. Wedge stack /data/wedge_stacks/wedge_677_1779379566.log
# captured `extract_document_ep` blocked on `<module>` of
# document_reader.py during a 17.91s stall — same pattern as R-F772
# closed for counterparty_claim_ledger. document_reader pulls
# PyMuPDF/fitz + OCR backends, which open shared libs and take
# multi-second on cold import. Eager-loading at boot moves that cost
# into the startup window where the event loop isn't serving traffic.
from .intel import document_reader as _document_reader_module  # noqa: F401
from .intel import cost_tracker
from .intel.researcher import research_and_learn, get_hypotheses, validate_hypothesis
from .routes.aria import router as aria_router, require_aria_token

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────
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
        for _mod in ("aria_service.writers.procurement_paper_writer",):
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
    asyncio.create_task(_prewarm_heavy_imports())

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
        _state_connect_ok = await rs.connect(settings.redis_url)
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

    # F28 fix 2026-04-27: every Lightpanda / Playwright render emits
    # `(node:NNN) [DEP0169] DeprecationWarning: url.parse() behavior is
    # not standardized` from internal Node helpers. The warning is
    # cosmetic — Playwright still works — but adds 3-4 noise lines per
    # render. Set NODE_OPTIONS=--no-deprecation BEFORE any Node child
    # is spawned to silence the lot.
    #
    # IMPORTANT: cannot reference module-level `_os` here. Python sees
    # the `import os as _os` later in this function (rag_init_bg block)
    # and treats `_os` as LOCAL for the whole function scope —
    # referencing it before that assignment raises UnboundLocalError.
    # That bug took prod down for 30s of restart-loop on commit
    # 6c26e17 → fixed in this commit by using a fresh local alias.
    import os as _f28_os
    _f28_os.environ.setdefault("NODE_OPTIONS", "--no-deprecation")

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
    _boot_init_failures = await _run_boot_inits([
        ("knowledge", knowledge.init),
        ("intel_ledger", intel_ledger.init),
        ("contacts", contacts.init),
        ("competitors", competitors.init),
        ("training_data", training_data.init),
        ("neural_memory", neural_memory.init),
    ])
    if _boot_init_failures:
        logger.error(
            "[R-F1421] %d/6 intel subsystems failed to init: %s — ARIA is UP "
            "but DEGRADED; these are unavailable until fixed/restarted.",
            len(_boot_init_failures), _boot_init_failures,
        )
    try:
        app.state.boot_init_failures = _boot_init_failures
    except Exception:
        pass

    # ── R-F1621 — freeze the now-loaded long-lived graphs out of GC ──────
    # knowledge/neural_memory/intel_ledger are warm above; freezing them here
    # (after their init) is what makes the per-flush json.dump cheap. See
    # _freeze_long_lived_state() + knowledge._write_to_disk_atomic.
    _freeze_long_lived_state()

    # ── R-F1891 — recover orphaned async jobs after a restart ────────────
    # A restart loses the in-memory chat/DD computation, so any job left at
    # 'processing' can never finish. Fail them now (awaited, fast scan) so a
    # reconnecting WhatsApp poll/callback gets a definitive failure and tells the
    # user to resend, instead of hanging the full 15-min poll window on a dead
    # job. Best-effort — never blocks boot on failure.
    try:
        from .routes.aria import recover_orphaned_jobs as _recover_jobs
        _n_recovered = await _recover_jobs()
        if _n_recovered:
            logger.info("[R-F1891] failed %d orphaned async job(s) interrupted by the restart", _n_recovered)
    except Exception as _rec_e:
        logger.warning("[R-F1891] orphaned-job recovery skipped (non-fatal): %s", _rec_e)

    # ── R-F504 (2026-05-14) — ARIA's own search index ───────────────────
    # Opens a separate SQLite file at /data/aria_search.db (configurable
    # via ARIA_SEARCH_DB_PATH) for the curated FTS5 corpus that powers
    # the independence path away from third-party search APIs. Registers
    # the seed_list domains at boot; the actual crawl runs out-of-band
    # (admin endpoint or scheduled job in a follow-up R-number).
    # Non-fatal: a failure here just means chat falls back to the
    # legacy web_search.search() path until the index is reachable.
    try:
        from .search_index import db as _search_db
        _ok = await _search_db.connect()
        if _ok:
            from .crawler import seed_list as _seeds
            _n = await _seeds.seed_all()
            logger.info(
                "[R-F504] search index ready (%d seed domains registered)", _n,
            )
        else:
            logger.warning(
                "[R-F504] search index connect() returned False — "
                "chat will fall back to web_search only",
            )
    except Exception as _exc:
        logger.warning(
            "[R-F504] search index init failed (non-fatal): %s", _exc,
        )

    # ── R-F507 (2026-05-14) — light the crawler ─────────────────────────
    # Game-changer move: the engine fills itself. Background task
    # rotates through the seed domains every CRAWL_INTERVAL_S (default
    # 6 h). Gated by ARIA_CRAWLER_DISABLED — set to "1" to keep the
    # engine dark (useful for first-deploy verification or volume cap
    # exposure investigation).
    _crawler_task = None
    _crawler_stop_event = None
    # R-F508 (2026-05-14): use _f28_os (already imported at line 78), NOT
    # _os — the inner `import os as _os` on line 168 makes _os a LOCAL
    # variable for the whole function, so referencing it BEFORE that line
    # raises UnboundLocalError at startup. R-F507 author missed the F28
    # warning at lines 72-77 and took prod down at 07:28 UTC.
    if _f28_os.getenv("ARIA_CRAWLER_DISABLED", "").lower() not in ("1", "true", "yes"):
        try:
            from .crawler import runner as _crunner
            _crawler_stop_event = asyncio.Event()
            _crawl_interval = int(
                _f28_os.getenv("ARIA_CRAWLER_INTERVAL_SEC", "21600"))  # 6h
            _crawler_task = asyncio.create_task(
                _crunner.crawl_loop(
                    interval_sec=_crawl_interval,
                    stop_event=_crawler_stop_event,
                ),
            )
            logger.info(
                "[R-F507] crawler attached (interval=%ds, set "
                "ARIA_CRAWLER_DISABLED=1 to disable)", _crawl_interval,
            )
        except Exception as _exc:
            logger.warning(
                "[R-F507] crawler attach failed (non-fatal): %s", _exc,
            )
    else:
        logger.info("[R-F507] crawler DISABLED via ARIA_CRAWLER_DISABLED env")

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
    import os as _os
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
    _HARD_WEDGE_CEILING_S = float(_os.getenv("ARIA_WEDGE_HARD_CEILING_S", "90"))
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
                    except Exception:
                        pass
                    try:
                        logger.critical(
                            "[R-F1417] event loop wedged %.1fs > hard ceiling "
                            "%.1fs — forcing process exit so Fly restarts the "
                            "machine (self-recovery from blackout)",
                            stale, _HARD_WEDGE_CEILING_S,
                        )
                    except Exception:
                        pass
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
        while True:
            try:
                await _aio.sleep(1.0)
            except _aio.CancelledError:
                return
            now = _time.monotonic()
            elapsed = now - last
            last = now
            _wedge_state["heartbeat"] = now
            # R-F1332: tick the self_restart heartbeat for aria_main every 1s.
            # The stall detector already runs every 1s, so this is a free tick
            # that keeps the blackout detector happy without a separate task.
            if _tick_hb is not None:
                try:
                    _tick_hb("aria_main")
                except Exception:
                    pass
            if elapsed > _STALL_WARN_THRESHOLD_S:
                logger.warning(
                    "[R-F703] event loop stalled for %.2fs (threshold=%.1fs) — "
                    "synchronous CPU work blocked the loop. Likely culprits: "
                    "sync sentence_transformers.encode(), large JSON load/save, "
                    "or unwrapped CPU-bound work. Correlate with concurrent "
                    "log lines around this timestamp. [R-F704] check %s for "
                    "live-stack capture from the wedge watchdog.",
                    elapsed, _STALL_WARN_THRESHOLD_S, _wedge_log_path,
                )
    _bg_task(asyncio.create_task(_event_loop_stall_detector(), name="stall_detector"))

    async def _rag_init_bg():
        # Wait for the server to bind and answer initial health checks
        # before we touch chromadb. The model download alone can take
        # 30-90s on a cold volume.
        await asyncio.sleep(15)
        try:
            stats = await rag_store.get_stats()
            logger.info("[RAG] probe: %s", stats)
        except Exception as e:
            logger.warning("[RAG] probe failed (non-fatal): %s", e)
            return
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

    # Create LLM provider with automatic fallback chain.
    # Auto-detect the right API key based on the provider name so that
    # setting ANTHROPIC_API_KEY + LLM_PROVIDER=anthropic works without
    # also needing to duplicate the key into LLM_API_KEY.
    _provider_key_map = {
        "anthropic": settings.anthropic_api_key,
        "openai": settings.openai_api_key,
        "gemini": settings.gemini_api_key,
        "deepseek": settings.deepseek_api_key,
        "groq": _os.environ.get("GROQ_API_KEY", ""),
    }
    api_key = (
        settings.llm_api_key
        or _provider_key_map.get(settings.llm_provider.lower().strip(), "")
        or settings.deepseek_api_key
    )
    llm = create_fallback_chain(
        primary_provider=settings.llm_provider,
        primary_key=api_key,
        primary_model=settings.llm_model,
        primary_base_url=settings.llm_base_url,
    )
    # F68 fix 2026-04-28: rehydrate any HARD (auth/billing) cooldowns
    # that were mirrored to Redis before the previous process exited.
    # Without this, every restart re-probes the failed backend and burns
    # ~5 calls before the in-process cooldown re-engages.
    if llm and hasattr(llm, "hydrate_from_redis"):
        try:
            n = await llm.hydrate_from_redis()
            if n:
                logger.info(
                    "LLM fallback chain: rehydrated %d HARD cooldown(s) from Redis",
                    n,
                )
        except Exception as e:
            logger.warning("LLM cooldown hydrate failed (non-fatal): %s", e)
    if not llm:
        # No fallback providers either — use single provider
        llm = create_llm_provider(
            provider=settings.llm_provider,
            api_key=api_key,
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            ollama_url=settings.ollama_url,
            ollama_model=settings.ollama_model,
        )
    # Wrap the provider with the cost-tracking decorator so every
    # llm.complete() call is metered automatically. Token counts come
    # straight from LLMResult; USD cost from cost_tracker pricing table.
    if llm:
        try:
            from .llm.metered import MeteredProvider
            llm = MeteredProvider(llm)
            logger.info("LLM provider wrapped with cost meter")
        except Exception as e:
            logger.warning("MeteredProvider wrap failed (non-fatal): %s", e)

    # Wrap with priority-aware rate limiter so background loops don't
    # starve interactive chat of Anthropic quota. Interactive requests
    # always go through; background tasks yield when near the limit.
    # ARIA_LLM_RPM env var sets the requests-per-minute cap (default 50).
    if llm:
        try:
            from .llm.rate_limiter import RateLimitedProvider
            llm = RateLimitedProvider(llm)
            logger.info("LLM provider wrapped with rate limiter (rpm=%s)",
                        _os.getenv("ARIA_LLM_RPM", "50"))
        except Exception as e:
            logger.warning("RateLimitedProvider wrap failed (non-fatal): %s", e)

    app.state.llm_provider = llm
    app.state.current_data = None  # Will be set by sweep integration

    # ── R-F1368: LLM resilience layer — health checker, request queue, cache ──
    # Start the background health probe for ARIA-LLM (sovereign 14B on RunPod).
    # Only activates when ARIA_LLM_URL is set. The health checker updates the
    # circuit_breaker registry so the fallback chain routes around a dead
    # sovereign model without waiting for a user request to discover the outage.
    llm_health_checker = None
    llm_request_queue = None
    llm_response_cache = None
    try:
        from .llm.resilience import LLMHealthChecker, LLMRequestQueue, LLMResponseCache
        # 1. Health checker — background probe
        llm_health_checker = LLMHealthChecker()
        await llm_health_checker.start()
        # 2. Request queue — semaphore-based concurrency limiter
        llm_request_queue = LLMRequestQueue(llm)
        # 3. Response cache — LRU cache for repeated queries
        llm_response_cache = LLMResponseCache(llm_request_queue)
        # Replace the LLM provider with the wrapped chain
        app.state.llm_provider = llm_response_cache
        logger.info(
            "[R-F1368] LLM resilience layer active: health_checker=%s queue=%s cache=%s",
            llm_health_checker.is_available() if hasattr(llm_health_checker, 'is_available') else False,
            llm_request_queue.get_stats() if hasattr(llm_request_queue, 'get_stats') else {},
            llm_response_cache.get_stats() if hasattr(llm_response_cache, 'get_stats') else {},
        )
    except Exception as _resilience_e:
        logger.warning("[R-F1368] LLM resilience layer init failed (non-fatal): %s", _resilience_e)

    if llm and llm.is_configured:
        logger.info(f"LLM provider: {llm.name} ✓")
    else:
        logger.warning(f"LLM provider not configured — set LLM_PROVIDER + LLM_API_KEY")

    # ── R-F673 (2026-05-17) — explicit dialogue_state DB init ──────────
    # dialogue_state.py lazily creates its aiosqlite connection + schema
    # on first call. That's fine for normal traffic, but it means a
    # boot-time misconfiguration (volume not mounted, schema mismatch)
    # only surfaces when the FIRST chat turn lands — sometimes minutes
    # after deploy. Calling _ensure_conn here forces the failure into
    # the boot log so /api/aria/health/live can't go green over a
    # broken dialogue store.
    try:
        from .intel import dialogue_state as _ds_boot
        await _ds_boot._ensure_conn()
        logger.info("[R-F673] dialogue_state DB init ✓")
    except Exception as _ds_e:
        logger.warning(
            "[R-F673] dialogue_state init failed at boot — open-question "
            "tracking will be degraded until DB is reachable: %s",
            _ds_e,
        )

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
        await asyncio.sleep(10)
        snapshot = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
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
                snapshot["neural_neurons"] = nm_stats.get("total_neurons", "n/a")
                snapshot["neural_edges"] = nm_stats.get("total_edges", "n/a")
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
        # snapshot against the PREVIOUS one (index 1 in the list). If any
        # numeric counter dropped by >5%, that's silent state loss — log
        # a LOUD warning AND absorb to brain_hook so the operator
        # dashboard surfaces it. Per the infinite-memory rule a counter
        # NEVER drops on a healthy deploy; if it does, the operator
        # needs to know BEFORE traffic resumes.
        try:
            from .intel import redis_store as _rs_diff
            import json as _json_diff
            prior_raw = await _rs_diff.lrange("crucix:aria:boot_snapshots", 1, 1)
            if prior_raw:
                try:
                    prior = _json_diff.loads(prior_raw[0]) if isinstance(prior_raw[0], str) else prior_raw[0]
                except Exception:
                    prior = None
                if isinstance(prior, dict):
                    drops: list[str] = []
                    for k in ("knowledge_facts", "ledger_signals", "rag_chunks",
                              "rag_facts", "chat_audit_total", "neural_neurons",
                              "neural_edges", "state_keys"):
                        cur_val = snapshot.get(k)
                        prv_val = prior.get(k)
                        if isinstance(cur_val, (int, float)) and isinstance(prv_val, (int, float)):
                            if prv_val > 0 and cur_val < prv_val * 0.95:
                                drop_pct = round((1 - cur_val / prv_val) * 100, 1)
                                drops.append(f"{k}: {prv_val} → {cur_val} (-{drop_pct}%)")
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
                _suspect.append(f"{_key}={_val!r} (value contains CLI flags — may have been set via `flyctl secrets set {_key}={_val} -a app`)")
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
                    result = await research_and_learn(llm)
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

        research_task = _bg_task(asyncio.create_task(_research_loop(), name="research_loop"), factory=_research_loop)
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
        asyncio.create_task(_register_agent(
            "research_engine", "autonomous_research",
            "RSS feeds → fact extraction → hypothesis validation (every 30min)",
        ))

    # Register self-improvement engine
    if llm and llm.is_configured:
        asyncio.create_task(_register_agent(
            "self_improve", "autonomous_self_improve",
            "Error-ledger analysis → bug detection → auto-fix → auto-deploy (every 2h)",
        ))

    # Register student loops
    asyncio.create_task(_register_agent(
        "student_quiz", "student_brain",
        "Self-quiz on weak topics, mastery tracking (every 3h)",
    ))
    asyncio.create_task(_register_agent(
        "student_reading", "student_brain",
        "Study articles on weak topics (every 6h)",
    ))
    asyncio.create_task(_register_agent(
        "library_consolidation", "student_brain",
        "Archive stale reasoning cases (daily)",
    ))

    # Register proactive watch
    asyncio.create_task(_register_agent(
        "proactive_watch", "proactive_engine",
        "Daily briefing trigger + mastery prep (hourly)",
    ))

    # Register weekly report
    asyncio.create_task(_register_agent(
        "weekly_report", "reporting_engine",
        "Weekly learning report (Monday 06-08 UTC)",
    ))

    # Register watchlist re-screen
    asyncio.create_task(_register_agent(
        "watchlist_rescreen", "dd_engine",
        "Re-screen DD watchlist entities against sanctions/PEP (daily)",
    ))

    # Register tender monitor
    asyncio.create_task(_register_agent(
        "tender_monitor", "procurement_engine",
        "Crawl defence procurement portals (every 6h)",
    ))

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
    asyncio.create_task(_register_agent(
        "web_integrity", "monitoring",
        "24/7 endpoint monitoring, input/output validation, error pattern detection",
        contract=_web_integrity_contract,
    ))

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
        ):
            try:
                await _CR.register_contract(_c)
            except Exception:
                logger.warning(
                    "R-F1561: contract registration failed for %s",
                    getattr(_c, "agent_id", "?"),
                )
    asyncio.create_task(_register_all_contracts())

    # Register self-healing with its binding contract
    asyncio.create_task(_register_agent(
        "self_healing", "infrastructure",
        "Health checks, circuit breakers, auto-recovery, ecosystem repair",
        contract=_self_healing_contract,
    ))

    # R-F1574: register autonomous scheduler agent
    asyncio.create_task(_register_agent(
        "autonomous_scheduler", "scheduler",
        "DD trigger monitor, gap fixing, self-diagnostics, adversarial tests (scheduled)",
    ))

    # R-F1574: register wiring monitor agent
    asyncio.create_task(_register_agent(
        "wiring_monitor", "monitoring",
        "Wire balance audit, compliance screener probe, brain signal path integrity (hourly)",
    ))

    # Start autonomous self-improvement loop (every 2 hours)
    self_improve_task = None
    if llm and llm.is_configured:
        async def _self_improve_loop():
            await asyncio.sleep(600)  # Wait 10 min after startup (staggered from research at 15min)
            while True:
                # R-F1395: check engine pause flag before each cycle
                from .autonomous.safety import is_engine_paused as _is_paused
                if await _is_paused():
                    logger.debug("[Self-Improve] engine paused — skipping cycle")
                    await asyncio.sleep(7200)
                    continue
                from .llm.rate_limiter import set_priority, reset_priority, Priority
                _p = set_priority(Priority.BACKGROUND)
                _t = cost_tracker.set_feature("self_improve")
                try:
                    await _tick_heartbeat("self_improve", "Error-ledger analysis → bug detection → auto-fix")
                    logger.info("[Self-Improve] Starting autonomous improvement cycle...")
                    result = await self_improve.autonomous_improvement_cycle(llm)
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

        self_improve_task = _bg_task(asyncio.create_task(_self_improve_loop(), name="self_improve_loop"), factory=_self_improve_loop)
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
                result = await student.reading_session(llm=llm, num_articles=4)
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
            await asyncio.sleep(6 * 3600)  # Every 6 hours

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

    quiz_task = _bg_task(asyncio.create_task(_quiz_loop(), name="quiz_loop"), factory=_quiz_loop)
    reading_task = _bg_task(asyncio.create_task(_reading_loop(), name="reading_loop"), factory=_reading_loop)
    library_consolidate_task = _bg_task(asyncio.create_task(_library_consolidate_loop(), name="library_consolidate_loop"), factory=_library_consolidate_loop)
    logger.info("Student loops started: self-quiz (3h), reading (6h), library consolidate (24h)")

    # ── RUNPOD SCHEDULER (R-F1335) ──────────────────────────────────────
    # ARIA runs her own GPU reasoning window: pod ON 10:00-18:00
    # Europe/London (her sovereign ARIA-LLM serves as chain primary),
    # pod OFF outside it (DeepSeek takes over via the cooldown chain).
    # Harmless no-op until RUNPOD_API_KEY + ARIA_RUNPOD_POD_ID secrets
    # are set. Loop ticks its own self_restart heartbeat.
    from .intel import runpod_scheduler as _runpod_sched
    runpod_sched_task = _bg_task(asyncio.create_task(_runpod_sched.scheduler_loop(), name="runpod_scheduler"))
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
            await asyncio.sleep(300)

    memory_wal_task = _bg_task(asyncio.create_task(_memory_wal_drain_loop(), name="memory_wal_drain"), factory=_memory_wal_drain_loop)
    logger.info("[R-F1342] memory WAL drain loop started (never-forget retry)")

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

        while True:
            try:
                n = await _gci.reconcile(_send_fn)
                if n:
                    logger.warning("[R-F1979 guardian] fired %d dead-man's-switch alert(s)", n)
            except Exception as e:
                logger.warning("[R-F1979 guardian] reconcile error: %s", e)
            await asyncio.sleep(60)

    guardian_task = _bg_task(asyncio.create_task(_guardian_reconcile_loop(), name="guardian_reconcile"), factory=_guardian_reconcile_loop)
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
                        except Exception:
                            pass
            except Exception as e:
                logger.warning("[R-F2006 watchdog] error: %s", e)
            await asyncio.sleep(900)   # every 15 min

    liveness_task = _bg_task(asyncio.create_task(_engine_liveness_watchdog_loop(), name="engine_liveness_watchdog"), factory=_engine_liveness_watchdog_loop)
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

    deploy_proprio_task = _bg_task(asyncio.create_task(_deploy_proprioception_loop(), name="deploy_proprioception"), factory=_deploy_proprioception_loop)
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

    proactive_task = _bg_task(asyncio.create_task(_proactive_loop(), name="proactive_loop"), factory=_proactive_loop)
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

    weekly_report_task = _bg_task(asyncio.create_task(_weekly_report_loop(), name="weekly_report_loop"), factory=_weekly_report_loop)
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
                await asyncio.sleep(86400)
                continue
            try:
                await _tick_heartbeat("watchlist_rescreen", "Re-screen DD watchlist entities against sanctions/PEP")
                from .intel import dd_orchestrator
                result = await dd_orchestrator.rescreen_watchlist(
                    llm=getattr(app.state, "llm_provider", None),
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
                # If changes detected, fire-and-forget WhatsApp notification
                if result.get("changes_detected"):
                    try:
                        from .intel import whatsapp
                        summary_lines = []
                        for ch in result["changes_detected"][:10]:
                            summary_lines.append(
                                f"  - {ch['entity']}: {ch['old_status']} -> {ch['new_status']} ({ch['change_type']})"
                            )
                        msg = (
                            f"[ARIA Watchlist Alert] {len(result['changes_detected'])} change(s) detected:\n"
                            + "\n".join(summary_lines)
                        )
                        asyncio.create_task(whatsapp.send_message(msg))
                    except Exception as _wa_e:
                        # R-F672 (2026-05-17): keep at debug not warning
                        # — most fires here ARE "WA not configured"
                        # which is operationally fine. But promote out
                        # of silent-pass so a real WA outage isn't
                        # invisible.
                        logger.debug(
                            "R-F672: watchlist WA notification skipped: %s",
                            _wa_e,
                        )
            except Exception as e:
                await _wire_agent_failure("watchlist_rescreen", f"Re-screen failed: {e}")
                logger.warning("[Watchlist] Re-screen failed: %s", e)
            await asyncio.sleep(86400)  # Every 24 hours

    watchlist_rescreen_task = _bg_task(asyncio.create_task(_watchlist_rescreen_loop(), name="watchlist_rescreen_loop"), factory=_watchlist_rescreen_loop)
    logger.info("Watchlist re-screen loop started (daily, 10 min after startup)")

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

    tender_monitor_task = _bg_task(asyncio.create_task(_tender_monitor_loop(), name="tender_monitor_loop"), factory=_tender_monitor_loop)
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
    try:
        from .autonomous import engine as autonomous_engine
        # Hydrate the in-process runtime-override cache BEFORE checking
        # is_enabled(). This lets /autonomous/enable keep the engine on
        # after a redeploy when the env var is missing — the Redis flag
        # survives restarts and gets picked up here on the next boot.
        await autonomous_engine.refresh_runtime_override()
        if autonomous_engine.is_enabled():
            started = autonomous_engine.start_engine(llm)
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
                        "flyctl secrets set ARIA_AUTONOMOUS_ENABLED=1 "
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

    # ── Defence source seed → web_atlas (2026-04-18) ────────────────
    # Bootstrap the curated Tier-1/1b/2 defence source catalogue into
    # web_atlas if it hasn't been populated yet. Idempotent — safe to
    # run on every startup. Seeding happens in background so it doesn't
    # block the lifespan startup gate.
    try:
        from .intel import defence_source_seed
        async def _seed_bg():
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
                h = _hashlib.md5()
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
                cur_hash = await _module_hash(modname)
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
        await asyncio.sleep(25)  # Wait for RAG + sentence-transformers
        force = (_os.getenv("ARIA_FORCE_RESEED", "") or "").strip().lower() in ("1", "true", "yes", "on")
        try:
            await run_knowledge_seed(force=force)
        except Exception as e:
            logger.warning("[Knowledge Seed] unhandled error (non-fatal): %s", e)

    knowledge_seed_task = _bg_task(asyncio.create_task(_seed_knowledge_bg(), name="seed_knowledge"))

    # ── R-F803 (2026-05-22): autonomous self-coder boot ───────────────────
    # ARIACoder + GapDetector. R-F996: coder is ALWAYS enabled when ARIA_INTERNAL_TOKEN is set.
    # No ARIA_CODER_ENABLED env var gate — the coder loop must stay
    # draining per CLAUDE.md §21c. The auto-deploy brake is
    # ARIA_SELF_IMPROVE_AUTO_DEPLOY (must stay 0 until R-F1450 proven).
    # See aria_service/autonomous/coder_entrypoint.py for the actual gates.
    # Returns a list[Task] (or None if any gate refused).
    aria_coder_tasks: list[asyncio.Task] = []
    try:
        from .autonomous.coder_entrypoint import start_aria_coder
        _coder_tasks = await start_aria_coder(app.state)
        if _coder_tasks:
            aria_coder_tasks = _coder_tasks
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

    # R-F1207 — start Web Integrity Agent (24/7 endpoint monitoring)
    # Monitors all 14 web endpoints every 60s, validates inputs/outputs,
    # detects error patterns, and stages fixes for recurring issues.
    # Implements all 7 binding directives from the operator:
    #   1. Verify every input   2. Verify every output   3. Monitor 24/7
    #   4. Cross-agent comms    5. Zero tolerance         6. Self-healing
    #   7. Never silent
    web_integrity_agent: Optional[Any] = None
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

    # R-F1253 — auto-populate agent signup vault from portal registry on boot
    try:
        from .intel.agent_signup_vault import get_vault
        from .intel.portal_registry import PORTALS
        vault = get_vault()
        stats = vault.stats()
        if stats.get("total", 0) == 0:
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
        else:
            logger.debug(
                "[R-F1253] Agent signup vault already has %d entries — skipping import",
                stats["total"],
            )

        # R-F1444: fire-and-forget auto-registration for pending portals
        try:
            from .intel.portal_registry import auto_register_all as _auto_reg
            # R-F1447: use the module-level asyncio (line 14). A bare local
            # `import asyncio` here made asyncio function-local for the WHOLE
            # lifespan(), so the earlier asyncio.create_task at line ~450
            # raised UnboundLocalError -> lifespan startup failed -> the app
            # never bound :8000 -> deploy failed / OUTAGE. Same class as R-F1441.
            asyncio.create_task(_delayed_auto_register(_auto_reg))
        except Exception as _reg_e:
            logger.warning("[R-F1444] Auto-registration launch failed (non-fatal): %s", _reg_e)

    except Exception as _vault_e:
        logger.warning("[R-F1253] Vault auto-population failed (non-fatal): %s", _vault_e)

    logger.info(f"ARIA Service ready on {settings.host}:{settings.effective_port}")

    # R-F1051 -- start self-healing infrastructure
    try:
        from .intel.self_healing import start_self_healing
        await start_self_healing()
        logger.info("[R-F1051] Self-healing infrastructure started")
    except Exception as _heal_e:
        logger.warning("[R-F1051] Self-healing start failed (non-fatal): %s", _heal_e)

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
    try:
        from .intel import eagle_eye
        await eagle_eye.start()
    except Exception as _ee_err:
        logger.warning("[EagleEye] Start failed (non-fatal): %s", _ee_err)

    # R-F1552: start Wiring Monitor (M1-M5 background checks every hour)
    _wiring_monitor_task = None
    try:
        from .intel import wiring_monitor as _wm
        _wiring_monitor_task = _wm.start_monitor()
    except Exception as _wm_err:
        logger.warning("[R-F1552] Wiring Monitor start failed (non-fatal): %s", _wm_err)

    # R-F1574: start Autonomous Scheduler (DD monitor, gap fixing, diagnostics)
    _scheduler_task = None
    try:
        from .intel.autonomous_scheduler import AutonomousScheduler
        _scheduler = AutonomousScheduler()
        _scheduler_task = asyncio.create_task(_scheduler.start(), name="autonomous_scheduler")
    except Exception as _sched_err:
        logger.warning("[R-F1574] Autonomous Scheduler start failed (non-fatal): %s", _sched_err)

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
    except Exception:
        pass

    yield


    # ── Shutdown ─────────────────────────────────────────────────────────
    # R-F1051 -- stop self-healing infrastructure
    try:
        from .intel.self_healing import stop_self_healing
        await stop_self_healing()
    except Exception as _heal_e:
        logger.warning("[R-F1051] Self-healing shutdown failed (non-fatal): %s", _heal_e)

    # R-F1146 -- stop self-restart blackout detector
    try:
        from .intel.self_restart import stop_blackout_detector
        stop_blackout_detector()
        logger.info("[R-F1146] Self-restart blackout detector stopped")
    except Exception:
        logger.warning("[R-F1146] Self-restart shutdown failed (non-fatal)")

    # R-F1207 -- stop Web Integrity Agent
    if web_integrity_agent is not None:
        try:
            await web_integrity_agent.stop()
            logger.info("[R-F1207] Web Integrity Agent stopped")
        except Exception as _wia_e:
            logger.warning("[R-F1207] Web Integrity Agent stop failed: %s", _wia_e)

    # R-F1368 -- stop LLM health checker
    if llm_health_checker is not None:
        try:
            await llm_health_checker.stop()
            logger.info("[R-F1368] LLM health checker stopped")
        except Exception as _hc_e:
            logger.warning("[R-F1368] LLM health checker stop failed: %s", _hc_e)

    # R-F1550: stop Eagle Eye codebase guardian
    try:
        from .intel import eagle_eye
        await eagle_eye.stop()
    except Exception as _ee_err:
        logger.warning("[EagleEye] Shutdown failed (non-fatal): %s", _ee_err)

    # R-F1890: stop the encode-offload worker process
    try:
        from .intel import encode_offload as _eo
        _eo.stop()
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
        await _autonomous_engine.stop_engine()
    except Exception as e:
        logger.warning("Autonomous engine shutdown failed (non-fatal): %s", e)
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
    # F94: flush any pending knowledge writes to disk before exit so the
    # last <FLUSH_DEBOUNCE_S of in-memory mutations aren't lost on a
    # clean shutdown / deploy.
    try:
        await knowledge.shutdown()
    except Exception as e:
        logger.warning("knowledge.shutdown failed (non-fatal): %s", e)
    # F110: same protection for the intel ledger — without this, the last
    # ~2s of channel/ingest signals (and any sweep-burst mid-flush) are
    # lost on every deploy.
    try:
        await intel_ledger.shutdown()
    except Exception as e:
        logger.warning("intel_ledger.shutdown failed (non-fatal): %s", e)
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
        await _search_db_shut.close()
    except Exception as e:
        logger.warning("search_index.close failed (non-fatal): %s", e)
    logger.info("ARIA Service shutting down")


# ── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="ARIA Intelligence API",
    description="Arkmurus Research Intelligence Agent — defence procurement, compliance, and geopolitical intelligence",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# R-F1853 (audit, DD stage 3) — global request-body size cap. Before this, ~150
# endpoints called `await request.json()` with no upper bound, so a single multi-GB
# or deeply-nested body could OOM the single-process brain. Header-only guard (no
# buffering): reject by Content-Length before any parsing. The cap is generous
# (default 50MB) so legitimate base64 documents pass; tune via ARIA_MAX_BODY_BYTES.
# Chunked requests with no Content-Length aren't caught here — the per-endpoint
# caps (read-document, etc.) backstop those.
import os as _bodylim_os
_MAX_BODY_BYTES = int(_bodylim_os.getenv("ARIA_MAX_BODY_BYTES", str(50 * 1024 * 1024)))


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


# Routes
app.include_router(aria_router)

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
                    # Skip __pycache__ and Python files
                    dirs[:] = [d for d in dirs if d != "__pycache__"]
                    for f in files:
                        # Skip aria.py - .bat downloads it on demand from /download/aria.py
                        if f.endswith(".py") or f.endswith(".pyc"):
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
async def health_live_top_level():
    """R-F690 (2026-05-18) — top-level /health/live alias.

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
        "tasks_loaded": 0,
    }
    try:
        from .autonomous import engine as _eng, tasks as _tsk
        status = _eng.get_engine_status()
        autonomous_ind["enabled"] = bool(status.get("enabled"))
        autonomous_ind["running"] = bool(status.get("running"))
        autonomous_ind["dry_run"] = bool(status.get("dry_run"))
        autonomous_ind["autonomy_level"] = int(status.get("autonomy_level", 0))
        last_tick = status.get("last_tick_at")
        if last_tick:
            import time
            autonomous_ind["seconds_since_last_tick"] = int(time.time() - last_tick)
        try:
            autonomous_ind["tasks_loaded"] = len(_tsk.get_loaded_tasks())
        except Exception:
            pass
    except Exception:
        pass

    # Health rollup — service is operational only if LLM is configured
    # AND (autonomous is off OR autonomous is running healthily). A
    # stuck autonomous loop is worse than off.
    autonomous_healthy = (
        not autonomous_ind["enabled"]  # off is fine for liveness purposes
        or (
            autonomous_ind["running"]
            and (autonomous_ind["seconds_since_last_tick"] is None
                 or autonomous_ind["seconds_since_last_tick"] < 180)
        )
    )

    # Self-diagnostic rollup (2026-04-18) — safe-to-publish summary of
    # module wiring health. Detailed report at /api/aria/diagnostic/details
    # behind auth. Read the cached result (refreshed every 15min by the
    # autonomous task) so /health stays fast.
    diagnostic_ind: dict = {"overall": "UNKNOWN"}
    try:
        from .intel import redis_store as rs
        latest = await rs.get_json("crucix:self_diagnostic:latest")
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

    return {
        "status": "operational" if (
            chain_resilient
            and autonomous_healthy
            and state_backend_ind["reachable"]
        ) else "degraded",
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

    Gates:
      #1 Composite >= 71%  — from compute_composite()
      #2 Heatmap floor >= 70% — from student.get_regional_heatmap()
      #3 0 fly ERRORs/7d — from mistake_ledger (WARNING-level, not ERROR)
      #4 Quarantined DDs closed — from dd_case_archive
      #5 Env vars set — from os.environ probe
      #6 500-Q eval frozen — from eval_runner.get_golden_set()
      #7 >=4 design-partner convos — from operator_pending (manual)
    """
    gates = {}
    sources = {}

    # Gate #1: Composite >= 71%
    try:
        from .intel.autonomy_scorer import compute_composite
        comp = await compute_composite()
        cs = comp.get("composite_score")
        gates["gate_1_composite"] = {
            "label": "Composite >= 71%",
            "value": cs,
            "pass": cs is not None and cs >= 0.71,
            "source": "compute_composite()",
            "confidence": comp.get("confidence"),
            "low_confidence": comp.get("low_confidence", True),
        }
        sources["composite"] = "compute_composite()"
    except Exception as e:
        gates["gate_1_composite"] = {"label": "Composite >= 71%", "value": None, "pass": False, "error": str(e)}
        sources["composite"] = f"error: {e}"

    # Gate #2: Heatmap floor >= 70%
    try:
        from .intel import student as _s1892
        hm_data = await _s1892.get_regional_heatmap()
        hm = (hm_data or {}).get("heatmap", {}) or {}
        all_scores = [s for regions in hm.values() for s in regions.values()]
        floor = min(all_scores) if all_scores else None
        breach = (hm_data or {}).get("floor_breach_cells", []) or []
        gates["gate_2_heatmap_floor"] = {
            "label": "Heatmap floor >= 70%",
            "value": floor,
            "pass": floor is not None and floor >= 0.70,
            "source": "student.get_regional_heatmap()",
            "floor_breach_cells": breach,
        }
        sources["heatmap"] = "student.get_regional_heatmap()"
    except Exception as e:
        gates["gate_2_heatmap_floor"] = {"label": "Heatmap floor >= 70%", "value": None, "pass": False, "error": str(e)}
        sources["heatmap"] = f"error: {e}"

    # Gate #3: 0 fly ERRORs/7d — from mistake_ledger.stats() (WARNING-level, not ERROR)
    try:
        from .intel import mistake_ledger as _ml1643
        ml_stats = await _ml1643.stats()
        # mistake_ledger tracks WARNING-level events; gate #3 counts ERROR-level
        # We report the ledger count but flag that this is WARNING not ERROR
        err_count_24h = ml_stats.get("errors_24h", ml_stats.get("total_24h", -1))
        gates["gate_3_zero_errors"] = {
            "label": "0 fly ERRORs/7d",
            "value": err_count_24h,
            "pass": err_count_24h == 0,
            "note": "mistake_ledger tracks WARNING-level; true ERROR count needs Fly log grep",
            "source": "mistake_ledger.stats()",
        }
        sources["errors"] = "mistake_ledger.stats()"
    except Exception as e:
        gates["gate_3_zero_errors"] = {"label": "0 fly ERRORs/7d", "value": None, "pass": False, "error": str(e)}
        sources["errors"] = f"error: {e}"

    # Gate #4: Quarantined DDs closed
    try:
        from .intel import dd_case_archive as _ddca1643
        archive_stats = _ddca1643.stats()
        quarantined = archive_stats.get("quarantined", archive_stats.get("open", -1))
        gates["gate_4_quarantine_closed"] = {
            "label": "Quarantined DDs closed",
            "value": quarantined,
            "pass": quarantined == 0,
            "source": "dd_case_archive.stats()",
        }
        sources["quarantine"] = "dd_case_archive.stats()"
    except Exception as e:
        gates["gate_4_quarantine_closed"] = {"label": "Quarantined DDs closed", "value": None, "pass": False, "error": str(e)}
        sources["quarantine"] = f"error: {e}"

    # Gate #5: Env vars set
    try:
        import os as _os1643
        required_vars = ["ARIA_OUTPUT_HARVEST_ENABLED", "ARIA_AUTONOMOUS_ENABLED"]
        # ACLED is deferred per operator 2026-06-07 — not checked here
        var_status = {}
        for v in required_vars:
            val = _os1643.environ.get(v, "")
            var_status[v] = {"set": bool(val), "value": val[:20] if val else None}
        all_set = all(v["set"] for v in var_status.values())
        gates["gate_5_env_vars"] = {
            "label": "Env vars set",
            "value": var_status,
            "pass": all_set,
            "note": "ACLED deferred per operator 2026-06-07 (MVP launch)",
            "source": "os.environ",
        }
        sources["env_vars"] = "os.environ"
    except Exception as e:
        gates["gate_5_env_vars"] = {"label": "Env vars set", "value": None, "pass": False, "error": str(e)}
        sources["env_vars"] = f"error: {e}"

    # Gate #6: 500-Q eval frozen
    try:
        from .intel import eval_runner as _er1643
        items = await _er1643.get_golden_set()
        count = len(items)
        gates["gate_6_eval_frozen"] = {
            "label": "500-Q eval frozen",
            "value": count,
            "target": 500,
            "pass": count >= 500,
            "source": "eval_runner.get_golden_set()",
        }
        sources["eval"] = "eval_runner.get_golden_set()"
    except Exception as e:
        gates["gate_6_eval_frozen"] = {"label": "500-Q eval frozen", "value": None, "pass": False, "error": str(e)}
        sources["eval"] = f"error: {e}"

    # Gate #7: >=4 design-partner convos
    # R-F1987: reads from DesignPartnerTracker store instead of operator_pending.
    try:
        from .intel.design_partner_tracker import get_tracker as _dpt
        _dpt_stats = _dpt().stats()
        gates["gate_7_design_partners"] = {
            "label": ">=4 design-partner convos",
            "value": _dpt_stats["total"],
            "target": 4,
            "pass": _dpt_stats["gate_pass"],
            "by_status": _dpt_stats["by_status"],
            "source": "design_partner_tracker.stats()",
        }
        sources["design_partners"] = "design_partner_tracker.stats()"
    except Exception as e:
        gates["gate_7_design_partners"] = {"label": ">=4 design-partner convos", "value": None, "pass": False, "error": str(e)}
        sources["design_partners"] = f"error: {e}"

    # Summary
    passed = sum(1 for g in gates.values() if g.get("pass"))
    total = len(gates)
    return {
        "gates": gates,
        "summary": {
            "passed": passed,
            "total": total,
            "all_pass": passed == total,
            "generated_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime()),
        },
        "sources_consulted": sources,
        "note": "R-F1643: every gate value reads from a live probe. Editing markdown does NOT change these values.",
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
        result = await zoom.handle_webhook(body, llm=llm)
        return result
    except HTTPException:
        # R-F1349: let the 401 signature rejection propagate — the broad
        # except below otherwise swallowed it and returned 200 (auth bypass).
        raise
    except Exception as e:
        logger.warning("Zoom webhook error: %s", e)
        return {"error": str(e)}


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
    async def _record_sweep_failure(reason: str, detail: str) -> None:
        # R-F973 (§21a): ingest_sweep is the largest Node→brain data path and
        # its parse-failure branches were logger.warning-only (DARK) — ARIA had
        # no coder-visible signal that sweeps were failing to ingest. Record a
        # capability gap so the failure reaches the brain on the failure branch.
        try:
            from .intel import capability_gaps as _cg
            await _cg.record_gap(
                gap_type="file_parse",
                detail=f"ingest_sweep {reason}: {detail[:300]}",
                source="ingest_sweep",
            )
        except Exception as _cg_e:
            logger.debug("ingest_sweep gap-record failed: %s", _cg_e)

    try:
        raw = await request.body()
    except Exception as e:
        logger.warning("ingest: body read failed: %s", e)
        await _record_sweep_failure("body_read_failed", str(e))
        raise HTTPException(status_code=400, detail="body_read_failed")

    try:
        data = json.loads(raw) if raw else {}
    except Exception as e:
        preview = (raw[:200] if raw else b"").decode("utf-8", errors="replace")
        logger.warning("ingest: JSON parse failed (%s). Body first 200b: %r", e, preview)
        await _record_sweep_failure("invalid_json", f"{e} | {preview}")
        raise HTTPException(status_code=400, detail="invalid_json")

    if not isinstance(data, dict):
        logger.warning(
            "ingest: expected dict body, got %s. Preview: %r",
            type(data).__name__, str(data)[:200],
        )
        await _record_sweep_failure("expected_dict_body", f"got {type(data).__name__}")
        raise HTTPException(status_code=400, detail="expected_dict_body")

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
            item_llm = llm if (sample_rate >= 1.0 or _random.random() < sample_rate) else None
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
        )
    else:
        uvicorn.run(
            "aria_service.main:app",
            host=_host,
            port=_port,
            reload=False,
        )

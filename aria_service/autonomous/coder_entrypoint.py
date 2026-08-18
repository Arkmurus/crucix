"""R-F802 — Autonomous coder entrypoint.

Called from `aria_service/main.py` after FastAPI startup completes.

The coder is **dormant by default** — it does not start unless ALL of:
  - `ARIA_AUTONOMOUS_ENABLED=1` (master kill switch, per existing engine)
  - `ARIA_CODER_ENABLED=1`     (this engine specifically)
  - `ARIA_INTERNAL_TOKEN` is set on the host
  - Redis client is constructible

The wiring is split out so R-F802 can land the modules without the
engine starting in production. R-F803 will wire `main.py` to call
`start_aria_coder()`.
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Optional
from ..intel.wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.autonomous.coder_entrypoint")

# R-F2543 (codex F1): PARTITION the R-number space so ARIA-Coder's autonomous
# allocations can NEVER collide with Claude's / local file-registry sequential range
# (~R-F25xx). The coder's Redis counter is otherwise UNSEEDED — next() = redis.incr from
# 0, so it hands out R-F1, R-F2, … colliding with ancient numbers AND the file registry
# (§2: 9 collisions in 50h is exactly this failure class). Full unification with the
# git-serialized file registry is a follow-up (blocked on the ci_deploy commit path).
R_CODER_BASE = 900000


async def _seed_coder_r_counter(counter, base: int = R_CODER_BASE) -> None:
    """Seed the coder's R-number counter to a high partition base if it is below it.

    Idempotent — the ``current() < base`` guard only ever RAISES the floor, so a reboot
    (counter already at e.g. base+5) is a no-op and never resets an in-flight sequence.
    """
    if await counter.current() < base:
        await counter.seed(base)
        logger.info("[coder_entrypoint] R-F2543: r_counter seeded to coder base %d "
                    "(no collision with file-registry R-numbers)", base)


# R-F1320: wire module health to the brain
try:
    from aria_service.intel.engine_wiring import wire_success as _ws1320
    _ws1320(
        module="autonomous.coder_entrypoint",
        summary="Coder Entrypoint active",
        source_id="autonomous:coder_entrypoint:R-F1320",
    )
except Exception:
    pass

ENABLE_VAR_MASTER = "ARIA_AUTONOMOUS_ENABLED"
ENABLE_VAR_CODER = "ARIA_CODER_ENABLED"


class _HarvestShim:
    """Adapter so ARIACoder can call ``output_harvester.capture(pair)`` over
    the module-level ``output_harvester.harvest`` coroutine.

    R-F1434: ``harvest`` is ``async def`` and MUST be awaited. It was
    previously called synchronously from a nested class, which left the
    coroutine un-awaited (RuntimeWarning) and meant the harvest never ran —
    the coder's output-harvest success-path was silently dark (violates
    CLAUDE.md §21a). Lifted to module level so it is unit-testable.
    """

    @fail_wire(module="coder_entrypoint", gap_type="agent_cycle_failure")
    async def capture(self, pair: dict) -> None:
        try:
            from ..learning.output_harvester import harvest as _harvest_fn
            await _harvest_fn(
                user_msg=pair.get("instruction", ""),
                response=pair.get("response", ""),
                meta={
                    "persona": pair.get("persona", ""),
                    "source": "autonomous_coder",
                    "r_number": pair.get("r_number"),
                },
            )
        except ImportError:
            pass
        except Exception as e:
            logger.warning("[coder_entrypoint] harvest shim failed: %s", e)

# ── Project context analysis (from ARIA_Coder_Complete.zip project_context.py) ──

_REPO_ROOT: Optional[Path] = None


def _find_repo_root() -> Optional[Path]:
    """Find the repo root by walking up from cwd looking for markers."""
    cwd = Path.cwd()
    markers = [".git", "pyproject.toml", "fly.toml"]
    current = cwd
    while current != current.parent:
        if any((current / m).exists() for m in markers):
            return current
        current = current.parent
    return None


def _repo_files(root: Path) -> list[Path]:
    """Return tracked repo files with a bounded filesystem fallback."""
    try:
        proc = subprocess.run(
            ["git", "ls-files"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=max(1.0, float(os.getenv("ARIA_PROJECT_CONTEXT_TIMEOUT_S", "10.0") or "10.0")),
            check=False,
        )
        if proc.returncode == 0 and proc.stdout:
            return [root / line.strip() for line in proc.stdout.splitlines() if line.strip()]
    except Exception as exc:
        logger.debug("[coder_entrypoint] git ls-files context scan unavailable: %s", exc)

    skipped = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache"}
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in skipped]
        base = Path(dirpath)
        files.extend(base / name for name in filenames)
    return files


def _analyse_project_context_sync(root: Path) -> dict[str, Any]:
    """Gather project-level stats for the coder startup log synchronously."""
    files = _repo_files(root)
    py_files = [f for f in files if f.suffix == ".py"]
    js_files = [f for f in files if f.suffix in {".mjs", ".js"}]
    test_files = [
        f for f in py_files
        if f.name.startswith("test_") or f.name.endswith("_test.py")
    ]

    ctx: dict[str, Any] = {
        "python_files": len(py_files),
        "js_files": len(js_files),
        "test_files": len(test_files),
    }

    total_lines = 0
    for f in py_files[:200]:
        try:
            total_lines += f.read_text(errors="replace").count("\n")
        except Exception:
            pass
    ctx["total_lines"] = total_lines

    endpoints = 0
    for f in py_files:
        try:
            rel = f.relative_to(root)
        except ValueError:
            continue
        if "routes" not in rel.parts:
            continue
        try:
            endpoints += f.read_text(errors="replace").count("@router.")
        except Exception:
            pass
    ctx["endpoints"] = endpoints

    ctx["aria_modules"] = sum(
        1 for f in py_files
        if "intel" in f.relative_to(root).parts
    )
    return ctx


async def _analyse_project_context() -> dict[str, Any]:
    """Gather project-level stats for the coder startup log off the event loop.

    Extracted from ARIA_Coder_Complete.zip project_context.py — provides
    the coder with awareness of codebase size and endpoint count at startup.
    """
    global _REPO_ROOT
    if _REPO_ROOT is None:
        _REPO_ROOT = _find_repo_root()
    if _REPO_ROOT is None:
        return {"error": "no repo root found"}
    return await asyncio.to_thread(_analyse_project_context_sync, _REPO_ROOT)


class _RedisStoreAdapter:
    """Adapt aria_service.intel.redis_store (module-level async functions)
    to the redis.asyncio client interface that gap_detector / r_counter /
    self_coder / fly_deployer call against.

    Only exposes the methods actually used by those modules. New methods
    are added on demand — keep this small.
    """

    def __init__(self, rs_module: Any) -> None:
        self._rs = rs_module

    @fail_wire(module="coder_entrypoint", gap_type="agent_cycle_failure")
    async def get(self, key: str) -> Any:
        return await self._rs.get(key)

    @fail_wire(module="coder_entrypoint", gap_type="agent_cycle_failure")
    async def set(self, key: str, value: str) -> None:
        await self._rs.set(key, value)

    @fail_wire(module="coder_entrypoint", gap_type="agent_cycle_failure")
    async def setex(self, key: str, ttl_seconds: int, value: str) -> None:
        # redis.asyncio: setex(name, time, value)
        # redis_store:   set(key, value, ex=time)
        await self._rs.set(key, value, ex=ttl_seconds)

    @fail_wire(module="coder_entrypoint", gap_type="agent_cycle_failure")
    async def incr(self, key: str) -> int:
        return await self._rs.incr(key)

    @fail_wire(module="coder_entrypoint", gap_type="agent_cycle_failure")
    async def expire(self, key: str, seconds: int) -> bool:
        return await self._rs.expire(key, seconds)

    @fail_wire(module="coder_entrypoint", gap_type="agent_cycle_failure")
    async def lrange(self, key: str, start: int, end: int) -> list:
        return await self._rs.lrange(key, start, end)

    @fail_wire(module="coder_entrypoint", gap_type="agent_cycle_failure")
    async def lpush(self, key: str, value: str) -> None:
        await self._rs.lpush(key, value)

    @fail_wire(module="coder_entrypoint", gap_type="agent_cycle_failure")
    async def ltrim(self, key: str, start: int, end: int) -> None:
        await self._rs.ltrim(key, start, end)

    @fail_wire(module="coder_entrypoint", gap_type="agent_cycle_failure")
    async def delete(self, key: str) -> bool:
        return await self._rs.delete(key)


def _coder_task_label() -> str:
    """A truthful one-line description of what the coder is doing right now.

    R-F4154 (C-176). Deliberately derived from state the coder ALREADY
    maintains rather than from a new field the loop must remember to update —
    a status that depends on being updated is a status that goes stale, which
    is the defect this fixes.

    Never raises: the heartbeat must keep ticking even if the label cannot be
    computed, because a missing heartbeat is read as a blackout (R-F1146) and
    would turn a cosmetic problem into a false recovery trigger.
    """
    try:
        from .self_coder import _CODER_PHASE
        phase = (_CODER_PHASE or {}).get("phase")
        detail = (_CODER_PHASE or {}).get("detail") or ""
        if phase:
            return f"{phase}: {detail}"[:120] if detail else str(phase)[:120]
    except Exception:
        pass
    return "idle (between cycles)"


async def _heartbeat_ticker() -> None:
    """Background task that ticks the coder's heartbeat every 30s.

    The blackout detector (R-F1146) monitors this heartbeat. If it goes
    stale beyond the threshold, a blackout is declared and recovery is
    triggered. This ticker ensures the heartbeat stays fresh even when
    the coder is between cycles (e.g. waiting for SCAN_INTERVAL_S).

    R-F1160: also registers the coder in the agent registry so other
    agents (gap_detector, research_engine, Claude Code sessions) know
    the coder is active and what it's working on.
    """
    _reg = None
    try:
        from ..intel.self_restart import tick_heartbeat
        from ..intel.agent_registry import AgentRegistry
        from ..intel.agent_contract import AgentContract

        # R-F2403: the coder declares gap_detector as a dependency, so the
        # dependency must have its own contract or self_healing reports
        # dependency_no_contract even when the detector is healthy.
        _gap_detector_contract = AgentContract(
            agent_id="gap_detector",
            version="1.0.0",
            directives=[
                "Scan production signal stores for structured capability gaps",
                "Publish latest scan results for operator visibility",
                "Deduplicate attempted and fixed gaps before the coder acts",
                "Wire both success and failure to the brain",
            ],
            inputs=[
                "error_log",
                "chat_audit",
                "capability_gaps",
                "mistake_ledger",
                "repository static analysis",
            ],
            outputs=["Gap objects", "latest gap snapshot", "attempt/fixed sentinels"],
            error_modes=[
                "signal_store_unavailable - skip source and continue",
                "malformed_signal - ignore entry and continue",
                "reproduce_test_unavailable - block autonomous fix",
            ],
            dependencies=[],
            check_interval_s=900,
            critical=False,
        )

        # R-F1898: define a binding contract for the coder
        _coder_contract = AgentContract(
            agent_id="aria_coder",
            version="1.0.0",
            directives=[
                "Scan for capability gaps every 15min via gap_detector",
                "Fix auto-fixable gaps with code changes",
                "Stage improvements via self_improve.stage_improvement",
                "Wire both success and failure to the brain",
                "Never modify protected files (constitutional_validator gate)",
            ],
            inputs=["GapDetector", "LLM provider", "CodebaseReader", "TestRunner"],
            outputs=["Fixed gaps", "Staged improvements", "R-numbers"],
            error_modes=[
                "gap_detector_unavailable - skip cycle, log warning",
                "llm_unavailable - skip fix, log warning",
                "test_failure - mark gap as failed, retry with cooldown",
                "constitutional_block - skip gap, log warning",
            ],
            dependencies=["gap_detector", "self_improve"],
            check_interval_s=900,
            critical=False,
        )

        # Register in the agent registry (non-fatal if it fails)
        _reg = AgentRegistry()
        try:
            try:
                from ..intel.agent_contract import CONTRACT_REGISTRY
                await CONTRACT_REGISTRY.register_contract(_gap_detector_contract)
            except Exception:
                logger.debug("[coder_entrypoint] gap_detector contract registration failed (non-fatal)")
            await _reg.register(
                agent_id="aria_coder",
                agent_type="autonomous_coder",
                current_task="starting up",
                contract=_coder_contract,
            )
        except Exception:
            logger.debug("[coder_entrypoint] agent registry registration failed (non-fatal)")

        while True:
            await asyncio.sleep(30)
            tick_heartbeat("aria_coder")
            # Also tick the agent registry heartbeat (non-fatal if it fails)
            if _reg is not None:
                try:
                    # R-F4154 (C-176) — pass the LIVE task, not nothing.
                    #
                    # This called `tick_heartbeat("aria_coder")` with no task, and
                    # `current_task` is written exactly once — at registration,
                    # as the literal "starting up" — and nowhere else in the tree.
                    # So the registry advertised "starting up" forever. Measured
                    # live 2026-08-18: registered_at 1787063949, last_heartbeat
                    # 1787067014 — **51 minutes of "starting up"** on a coder that
                    # was in fact healthy (brain stats: 85 cycles, 0 failures).
                    #
                    # That is not cosmetic. R-F1160 registers the coder here
                    # precisely "so other agents (gap_detector, research_engine,
                    # Claude Code sessions) know the coder is active AND WHAT IT
                    # IS WORKING ON", and gap claiming (`claim_gap`) is
                    # coordinated through this same registry. A field that can
                    # only ever hold its initial value is an absence dressed as a
                    # measurement — and it cost real time in this very review,
                    # where "starting up for 51 minutes" read as a hung loop.
                    #
                    # The registry already supported this: `tick_heartbeat`
                    # accepts `current_task`, and `update_task` exists. Neither
                    # was ever called.
                    await _reg.tick_heartbeat("aria_coder", current_task=_coder_task_label())
                except Exception:
                    pass
    except ImportError:
        pass
    except asyncio.CancelledError:
        pass


@fail_wire(module="coder_entrypoint", gap_type="agent_cycle_failure")
async def start_aria_coder(
    app_state: Any,
    aria_service_url: Optional[str] = None,
) -> Optional[list[asyncio.Task]]:
    """Start the ARIACoder as a background task.

    Returns the list of started tasks (so the caller can hold references
    and cancel cleanly on shutdown), or None if startup was refused.
    """
    # R-F996 — coder is ALWAYS enabled. No env var gate.
    # The master switch and coder switch checks are removed — ARIA
    # self-improves autonomously whenever the process is running.
    if not os.environ.get("ARIA_INTERNAL_TOKEN"):
        logger.warning(
            "[coder_entrypoint] ARIA_INTERNAL_TOKEN unset — refusing to start",
        )
        try:
            from aria_service.intel.engine_wiring import wire_failure as _wf1381
            _wf1381(
                module="autonomous.coder_entrypoint",
                detail="ARIA_INTERNAL_TOKEN unset — coder refused to start",
                gap_type="engine_failure",
                source="coder_entrypoint",
            )
        except Exception:
            pass
        return None

    # R-F808 (2026-05-22): live-deploy refused on first activation —
    # `app.state has no .redis — refusing to start`. Root cause: the
    # project's state layer is `aria_service.intel.redis_store` (a
    # module-level async wrapper), not a raw redis client on app.state.
    # Build a thin adapter that exposes the surface our coder modules
    # expect (.get / .setex / .lrange / .incr / etc.) and delegate to
    # rs.* module-level functions. Adapter is small + only adds the
    # methods actually used by gap_detector / r_counter / self_coder /
    # fly_deployer.
    redis_client = getattr(app_state, "redis", None)
    if redis_client is None:
        try:
            from ..intel import redis_store as rs
            redis_client = _RedisStoreAdapter(rs)
        except Exception as e:
            logger.warning(
                "[coder_entrypoint] redis_store import failed: %s — "
                "refusing to start", e,
            )
            try:
                from aria_service.intel.engine_wiring import wire_failure as _wf1381b
                _wf1381b(
                    module="autonomous.coder_entrypoint",
                    detail=f"redis_store import failed: {e}",
                    gap_type="engine_failure",
                    source="coder_entrypoint",
                )
            except Exception:
                pass
            return None

    # Lazy imports — keep `import aria_service.autonomous` cheap
    from .self_coder import ARIACoder

    # R-F1237: SovereignLLM (DeepSeek-backed) is the PRIMARY coding engine.
    # AutonomousCoder (AST-aware, no external LLM) is the fallback when
    # DeepSeek is unavailable or ARIA_INTERNAL_TOKEN is not set.
    # This gives ARIA real code synthesis (novel business logic) while
    # keeping the AST-based coder as a zero-cost fallback for simple edits.
    # R-F1250: url must be defined BEFORE SovereignLLM init — the old code
    # had `url = ...` after the try block, causing a NameError at boot.
    url = aria_service_url or os.environ.get(
        "ARIA_SELF_URL", "http://localhost:8000",
    )
    _llm = None
    try:
        from .sovereign_llm import SovereignLLM
        _llm = SovereignLLM(aria_service_url=url)
        logger.info(
            "[coder_entrypoint] Using SovereignLLM (DeepSeek-backed) "
            "as the primary coding engine",
        )
    except Exception as e:
        logger.warning(
            "[coder_entrypoint] SovereignLLM init failed: %s — "
            "falling back to AutonomousCoder (AST-only)", e,
        )
        try:
            from ..intel.autonomous_coder import AutonomousCoder
            _llm = AutonomousCoder()
            logger.info(
                "[coder_entrypoint] Using AutonomousCoder (AST-aware, no external LLM) "
                "as fallback coding engine",
            )
        except Exception as e2:
            logger.error(
                "[coder_entrypoint] Both SovereignLLM and AutonomousCoder "
                "failed to init: %s / %s — coder will have no LLM", e, e2,
            )

    # R-F1032: ensure MODIFIABLE_FILES is populated before the coder starts.
    # _one_cycle imports MODIFIABLE_FILES but it's dynamically populated by
    # _ensure_modifiable_files() and is EMPTY until that function starts.
    # Without this call, the coder sees an empty set and skips every cycle.
    try:
        from ..intel.self_improve import _ensure_modifiable_files
        await _ensure_modifiable_files()
    except Exception as e:
        logger.warning(
            "[coder_entrypoint] _ensure_modifiable_files failed: %s — "
            "coder may see empty MODIFIABLE_FILES", e,
        )

    brain_hook = None
    try:
        # R-F810 (2026-05-22): live-deploy 21:20:53 logged
        # `[coder_entrypoint] brain_hook not available: cannot import name
        # 'brain_hook' from 'aria_service.intel.brain_hook'`. Root cause:
        # brain_hook.py exports module-level functions (absorb,
        # absorb_silent) not a `brain_hook` symbol. self_coder.py:442 calls
        # `await self.brain_hook.absorb(...)` so it needs an object with an
        # .absorb attribute — the module itself satisfies that. Import the
        # module, not a nonexistent symbol.
        from ..intel import brain_hook as _brain_hook
        brain_hook = _brain_hook
    except ImportError as e:
        logger.info(
            "[coder_entrypoint] brain_hook not available: %s — continuing", e,
        )

    output_harvester = None
    try:
        from ..learning.output_harvester import harvest as _harvest_fn  # noqa: F401
        # The existing harvester exposes module-level functions, not a class.
        # ARIACoder.capture(...) is delegated to the module-level _HarvestShim
        # (lifted out of this function in R-F1434 so it is unit-testable).
        output_harvester = _HarvestShim()
    except ImportError as e:
        logger.info(
            "[coder_entrypoint] output_harvester not available: %s", e,
        )

    # R-F825 + R-F847: wire the WhatsApp notifier so the coder can post
    # rich operator-facing progress messages (queued → stages → done/failed).
    # Dormant unless ARIA_WA_INTERNAL_URL (or legacy SEENODE_BASE_URL)
    # + ARIA_INTERNAL_TOKEN + ARIA_CODER_WA_GROUP_ID are all set.
    # `notify()` returns "skipped:no_group_id" gracefully in dev, never raises.
    wa_notifier = None
    try:
        from .wa_notifier import WANotifier
        wa_notifier = WANotifier()
        if not wa_notifier.is_configured:
            logger.info(
                "[coder_entrypoint] WANotifier dormant — "
                "ARIA_WA_INTERNAL_URL (or SEENODE_BASE_URL) / "
                "ARIA_INTERNAL_TOKEN / ARIA_CODER_WA_GROUP_ID not all set",
            )
    except Exception as e:
        logger.warning(
            "[coder_entrypoint] WANotifier init failed (non-fatal): %s", e,
        )

    coder = ARIACoder(
        redis_client=redis_client,
        aria_service_url=url,
        whatsapp_notifier=wa_notifier,
        brain_hook=brain_hook,
        output_harvester=output_harvester,
        llm=_llm,  # R-F1237: SovereignLLM primary, AutonomousCoder fallback
    )

    # R-F824 (2026-05-23): expose the live ARIACoder instance on
    # app.state so the /api/aria/coder/request + /coder/status/{id}
    # endpoints can call `coder.operator_fix_request(...)` directly.
    # When the engine isn't started, the endpoints return 503.
    try:
        app_state.aria_coder = coder
    except Exception as e:
        logger.debug("[coder_entrypoint] could not stash coder on app_state: %s", e)

    # R-F2543 (codex F1): partition the R-number space so ARIA-Coder never collides with
    # the file-registry range (see _seed_coder_r_counter). Non-fatal.
    try:
        await _seed_coder_r_counter(coder.r_counter)
    except Exception as _seed_e:
        logger.warning("[coder_entrypoint] R-F2543 r_counter seed failed (non-fatal): %s", _seed_e)

    # R-F1146 — start blackout detector and tick heartbeat for the coder
    try:
        from ..intel.self_restart import start_blackout_detector, tick_heartbeat, save_checkpoint
        start_blackout_detector()
        tick_heartbeat("aria_coder")
        logger.info("[coder_entrypoint] Self-restart blackout detector started")
    except ImportError as _sr_e:
        logger.debug("[coder_entrypoint] Self-restart not available: %s", _sr_e)

    tasks = [
        # R-F1046 — gap_detector.run_forever REMOVED (was double-scanning).
        # self_coder._one_cycle already calls gap_detector.scan() on every
        # cycle, so the standalone loop was scanning twice — 43 gaps detected
        # twice = double rate-bucket burn + double the gap-detection log noise.
        # publish_latest is called inside _one_cycle after each fix attempt.
        asyncio.create_task(
            coder.run_forever(),
            name="aria_coder.self_coder",
        ),
        # R-F1146 — heartbeat ticker for the coder (ticks every 30s so the
        # blackout detector knows the coder is alive)
        asyncio.create_task(
            _heartbeat_ticker(),
            name="aria_coder.heartbeat_ticker",
        ),
    ]

    # R-F1080 — start continuous profiler alongside the coder
    try:
        from ..intel.continuous_profiler import start_profiler as _start_prof
        _prof_tasks = _start_prof()
        tasks.extend(_prof_tasks)
        logger.info(
            "[coder_entrypoint] Continuous profiler started (%d tasks)",
            len(_prof_tasks),
        )
    except Exception as _prof_e:
        logger.debug("[coder_entrypoint] Continuous profiler not available: %s", _prof_e)

    logger.info(
        "[coder_entrypoint] ARIA-Coder started (%d background tasks)",
        len(tasks),
    )

    # Log project context for startup visibility (from ARIA_Coder_Complete.zip)
    try:
        ctx = await _analyse_project_context()
        if ctx and "error" not in ctx:
            logger.info(
                "[coder_entrypoint] Project context: %d Python files, %d JS files, "
                "%d test files, ~%d LOC, %d endpoints, %d intel modules",
                ctx.get("python_files", 0), ctx.get("js_files", 0),
                ctx.get("test_files", 0), ctx.get("total_lines", 0),
                ctx.get("endpoints", 0), ctx.get("aria_modules", 0),
            )
        else:
            logger.debug("[coder_entrypoint] Project context unavailable: %s", ctx)
    except Exception as e:
        logger.debug("[coder_entrypoint] Project context analysis failed: %s", e)

    return tasks

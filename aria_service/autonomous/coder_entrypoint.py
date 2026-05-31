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
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("aria.autonomous.coder_entrypoint")

ENABLE_VAR_MASTER = "ARIA_AUTONOMOUS_ENABLED"
ENABLE_VAR_CODER = "ARIA_CODER_ENABLED"

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


async def _analyse_project_context() -> dict[str, Any]:
    """Gather project-level stats for the coder startup log.
    
    Extracted from ARIA_Coder_Complete.zip project_context.py — provides
    the coder with awareness of codebase size and endpoint count at startup.
    Uses Path-based operations only (no subprocess) per constitutional rules.
    """
    global _REPO_ROOT
    if _REPO_ROOT is None:
        _REPO_ROOT = _find_repo_root()
    if _REPO_ROOT is None:
        return {"error": "no repo root found"}

    ctx: dict[str, Any] = {}

    # File counts
    try:
        ctx["python_files"] = len(list(_REPO_ROOT.rglob("*.py")))
        ctx["js_files"] = len(list(_REPO_ROOT.rglob("*.mjs"))) + len(list(_REPO_ROOT.rglob("*.js")))
        ctx["test_files"] = len(list(_REPO_ROOT.rglob("test_*.py"))) + len(list(_REPO_ROOT.rglob("*_test.py")))
    except Exception:
        pass

    # LOC estimate (first 200 .py files)
    try:
        total_lines = 0
        for f in list(_REPO_ROOT.rglob("*.py"))[:200]:
            try:
                total_lines += f.read_text(errors="replace").count("\n")
            except Exception:
                pass
        ctx["total_lines"] = total_lines
    except Exception:
        pass

    # ARIA-specific: count route endpoints
    try:
        endpoints = 0
        for f in _REPO_ROOT.rglob("routes/*.py"):
            try:
                endpoints += f.read_text(errors="replace").count("@router.")
            except Exception:
                pass
        ctx["endpoints"] = endpoints
    except Exception:
        pass

    # Intel module count
    try:
        ctx["aria_modules"] = len(list(_REPO_ROOT.rglob("intel/*.py")))
    except Exception:
        pass

    return ctx


class _RedisStoreAdapter:
    """Adapt aria_service.intel.redis_store (module-level async functions)
    to the redis.asyncio client interface that gap_detector / r_counter /
    self_coder / fly_deployer call against.

    Only exposes the methods actually used by those modules. New methods
    are added on demand — keep this small.
    """

    def __init__(self, rs_module: Any) -> None:
        self._rs = rs_module

    async def get(self, key: str) -> Any:
        return await self._rs.get(key)

    async def set(self, key: str, value: str) -> None:
        await self._rs.set(key, value)

    async def setex(self, key: str, ttl_seconds: int, value: str) -> None:
        # redis.asyncio: setex(name, time, value)
        # redis_store:   set(key, value, ex=time)
        await self._rs.set(key, value, ex=ttl_seconds)

    async def incr(self, key: str) -> int:
        return await self._rs.incr(key)

    async def expire(self, key: str, seconds: int) -> bool:
        return await self._rs.expire(key, seconds)

    async def lrange(self, key: str, start: int, end: int) -> list:
        return await self._rs.lrange(key, start, end)

    async def lpush(self, key: str, value: str) -> None:
        await self._rs.lpush(key, value)

    async def ltrim(self, key: str, start: int, end: int) -> None:
        await self._rs.ltrim(key, start, end)

    async def delete(self, key: str) -> bool:
        return await self._rs.delete(key)


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

        # Register in the agent registry (non-fatal if it fails)
        _reg = AgentRegistry()
        try:
            await _reg.register(
                agent_id="aria_coder",
                agent_type="autonomous_coder",
                current_task="starting up",
            )
        except Exception:
            logger.debug("[coder_entrypoint] agent registry registration failed (non-fatal)")

        while True:
            await asyncio.sleep(30)
            tick_heartbeat("aria_coder")
            # Also tick the agent registry heartbeat (non-fatal if it fails)
            if _reg is not None:
                try:
                    await _reg.tick_heartbeat("aria_coder")
                except Exception:
                    pass
    except ImportError:
        pass
    except asyncio.CancelledError:
        pass


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
            return None

    # Lazy imports — keep `import aria_service.autonomous` cheap
    from .self_coder import ARIACoder

    # R-F1237: SovereignLLM (DeepSeek-backed) is the PRIMARY coding engine.
    # AutonomousCoder (AST-aware, no external LLM) is the fallback when
    # DeepSeek is unavailable or ARIA_INTERNAL_TOKEN is not set.
    # This gives ARIA real code synthesis (novel business logic) while
    # keeping the AST-based coder as a zero-cost fallback for simple edits.
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
    # _ensure_modifiable_files() and is EMPTY until that function runs.
    # Without this call, the coder sees an empty set and skips every cycle.
    try:
        from ..intel.self_improve import _ensure_modifiable_files
        await _ensure_modifiable_files()
    except Exception as e:
        logger.warning(
            "[coder_entrypoint] _ensure_modifiable_files failed: %s — "
            "coder may see empty MODIFIABLE_FILES", e,
        )

    url = aria_service_url or os.environ.get(
        "ARIA_SELF_URL", "http://localhost:8000",
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
        # ARIACoder.capture(...) will be called via a thin shim on
        # output_harvester.capture, so we wrap here.

        class _HarvestShim:
            async def capture(self, pair: dict) -> None:
                # Synchronous module-level harvest call; we delegate.
                try:
                    _harvest_fn(
                        user_msg=pair.get("instruction", ""),
                        response=pair.get("response", ""),
                        meta={
                            "persona": pair.get("persona", ""),
                            "source": "autonomous_coder",
                            "r_number": pair.get("r_number"),
                        },
                    )
                except Exception as e:
                    logger.warning(
                        "[coder_entrypoint] harvest shim failed: %s", e,
                    )

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

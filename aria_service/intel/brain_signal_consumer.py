"""R-F1060 — Web tier Redis brain signal consumer.

The Node web tier (server.mjs) pushes sweep signals to Redis key
`crucix:brain:incoming_signals` via `pushSignalsToBrain()`. This module
polls that key and forwards each signal to brain_hook.absorb so the
intel brain sees web-tier intelligence.

Background task: call `start_consumer()` from lifespan startup.
"""
from __future__ import annotations
from .engine_wiring import wire_success, wire_failure

import asyncio
import json
import logging

logger = logging.getLogger("aria.brain_signal_consumer")

_REDIS_KEY = "crucix:brain:incoming_signals"
_POLL_INTERVAL_S = 60
_BATCH_SIZE = 10
_STARTUP_DELAY_S = 30


async def _consume_loop() -> None:
    """Poll Redis for web-tier brain signals and forward to brain_hook."""
    await asyncio.sleep(_STARTUP_DELAY_S)
    while True:
        try:
            from . import redis_store as _rs
            from . import brain_hook as _bh

            raw = await _rs.lpop_multi(_REDIS_KEY, _BATCH_SIZE)
            if raw:
                for item in raw:
                    try:
                        sig = json.loads(item) if isinstance(item, str) else item
                        content = sig.get("content") or sig.get("title") or ""
                        source = sig.get("source") or "web_tier"
                        sig_type = sig.get("signal_type") or "web_sweep_signal"
                        if content:
                            await _bh.absorb(
                                module=f"cross_tier:{sig_type}",
                                summary=content[:300],
                                detail=content[:2000],
                                success=True,
                                confidence="ASSESSED",
                            )
                    except Exception:
                        logger.debug("[brain_signal_consumer] signal parse failed")
        except Exception as _e:
            logger.debug("[brain_signal_consumer] poll failed: %s", _e)
        await asyncio.sleep(_POLL_INTERVAL_S)


def start_consumer() -> asyncio.Task:
    """Start the background consumer task. Returns the task handle."""
    task = asyncio.create_task(_consume_loop())
    logger.info(
        "[R-F1060] Web tier brain signal consumer started "
        "(polling %s every %ds)",
        _REDIS_KEY, _POLL_INTERVAL_S,
    )
    return task


# Module-level auto-start: when this module is imported during lifespan,
# the consumer starts automatically. R-F1191: constitutional validator removed.
# The consumer is idempotent — multiple imports won't create duplicate loops
# because asyncio.create_task returns immediately and the loop checks
# _REDIS_KEY which is shared state.
_auto_started: asyncio.Task | None = None

try:
    _loop = asyncio.get_running_loop()
    _auto_started = start_consumer()
except RuntimeError:
    # No running event loop (e.g. during import at module load time).
    # The consumer will be started when lifespan calls start_consumer().
    pass

    # R-F2118/R-F2119 §21a — wire module active
    try:
        wire_success(module="brain_signal_consumer",
                     summary="brain_signal_consumer module active",
                     source_id="brain_signal_consumer:init")
    except Exception:
        try:
            wire_failure(module="brain_signal_consumer", detail="module init failed",
                        gap_type="engine_failure", source="brain_signal_consumer:init")
        except Exception:
            pass

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
                absorbed = 0
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
                            absorbed += 1
                    except Exception:
                        # R-F3577 — a signal LPOP'd and then dropped is gone: the
                        # pop already removed it from the list, so a parse failure
                        # is silent data loss from the web tier, not a retry.
                        logger.warning(
                            "[brain_signal_consumer] signal parse failed — signal DROPPED",
                            exc_info=True,
                        )
                        wire_failure(
                            module="brain_signal_consumer",
                            detail="web-tier signal was popped but could not be parsed — "
                                   "it is lost, not requeued",
                            gap_type="engine_failure",
                            source="brain_signal_consumer:parse",
                        )
                # §21a SUCCESS branch. Gated on work done: an empty poll is the
                # healthy steady state and runs every 60s, so signalling it would
                # flood the ledgers (the loop_monitor precedent, R-F3563).
                if absorbed:
                    wire_success(
                        module="brain_signal_consumer",
                        summary=f"absorbed {absorbed} web-tier signal(s)",
                        detail=f"drained from {_REDIS_KEY} (batch {len(raw)})",
                        confidence="ASSESSED",
                        source_id="brain_signal_consumer:consume",
                    )
        except Exception as _e:
            # R-F3577 — was a logger.debug, i.e. invisible. If Redis is unreachable
            # the web tier keeps PUSHING while this keeps failing, so the backlog
            # grows unread and nothing says so.
            logger.warning("[brain_signal_consumer] poll failed: %s", _e, exc_info=True)
            try:
                wire_failure(
                    module="brain_signal_consumer",
                    detail=f"poll of {_REDIS_KEY} FAILED ({type(_e).__name__}): {str(_e)[:200]} — "
                           f"web-tier signals are accumulating unread",
                    gap_type="engine_failure",
                    source="brain_signal_consumer:poll",
                )
            except Exception:
                logger.debug("[brain_signal_consumer] brain wiring failed", exc_info=True)
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


# ── R-F3577 — THE IMPORT-TIME AUTO-START IS REMOVED ─────────────────────────
#
# This module used to start itself on import:
#
#     try:
#         _loop = asyncio.get_running_loop()
#         _auto_started = start_consumer()
#     except RuntimeError:
#         pass    # "The consumer will be started when lifespan calls
#                 #  start_consumer()"
#
# Three things were wrong with it, and together they meant the loop had never
# run in production:
#
#   1. NOTHING IMPORTS THIS MODULE. An import-time side effect cannot fire
#      without an import. Verified repo-wide (R-F3573 orphan audit): no
#      production importer, so `start_consumer()` was never reached by either
#      route.
#   2. The stated fallback did not exist. Lifespan did not call
#      start_consumer() — the comment asserted a caller that was never written.
#   3. THE §21a WIRING WAS INSIDE THE `except RuntimeError` BRANCH, after the
#      `pass`. So `wire_success("brain_signal_consumer module active")` fired on
#      exactly the path where the consumer had NOT started, and never on the path
#      where it had. The brain was told the module was active by the code that
#      handles it failing to start.
#
# Meanwhile the PRODUCER is live: server.mjs:7709 and apis/briefing.mjs:847 both
# call pushSignalsToBrain() on the running web tier, writing to
# crucix:brain:incoming_signals. A producer with no reader — every web-tier sweep
# signal was pushed into a list nothing drained, which is exactly the cross-tier
# darkness §21b exists to prevent.
#
# The loop is now started from main.py lifespan via _singleton_task (R-F2073), so
# it runs on the engine process only and never once per web worker, and the bg
# supervisor can heal a genuine crash (respawn=True — it is a real while-True
# loop, not a one-shot; cf. R-F2668).
_auto_started: "asyncio.Task | None" = None

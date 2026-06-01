"""R-F1155 — Background tier processor for brain_hook.

Moves the 3 expensive learning tiers (mastery, knowledge, neural) into
a background task so absorb() returns immediately. Imported by brain_hook.py.

Also provides auto_record_gap_from_text() for R-F1150 structural enforcement.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Optional

logger = logging.getLogger("aria.brain_hook_bg")


async def absorb_tiers_bg(
    *,
    module: str,
    summary: str,
    text_for_neural: str,
    source: str,
    topics: list[str],
    success: bool,
    weight: float,
    confidence: str,
    entity_name: str,
    gap_type: Optional[str],
    gap_detail: Optional[str],
    sector: str,
    user_id: str,
    result: dict,
    _get_absorb_concurrency_sem,
    _ABSORB_CONCURRENCY: int,
    _ABSORB_SEM_ACQUIRE_TIMEOUT_S: float,
    _run_tier,
    _record_signal,
    _record_latency,
    _maybe_trip_breaker,
    _start_ms: float,
) -> None:
    """Run the 3 expensive learning tiers in a background task.

    Called by brain_hook.absorb() via asyncio.create_task. Runs mastery,
    knowledge, and neural tiers under the concurrency semaphore. The caller
    has already returned by the time this runs, so latency here never
    blocks chat or crawler processing.
    """
    sem = _get_absorb_concurrency_sem()
    acquired = False
    if sem is not None:
        try:
            await asyncio.wait_for(
                sem.acquire(), timeout=_ABSORB_SEM_ACQUIRE_TIMEOUT_S,
            )
            acquired = True
        except asyncio.TimeoutError:
            logger.debug(
                "brain_hook bg: concurrency cap hit (%d in flight) — "
                "skipping expensive tiers for module=%s",
                _ABSORB_CONCURRENCY, module,
            )
            result["errors"].append(
                "absorb: concurrency cap (>{:.1f}s wait)".format(
                    _ABSORB_SEM_ACQUIRE_TIMEOUT_S
                )
            )

    try:
        if sem is None or acquired:
            from . import student
            ok, err = await _run_tier(
                student.update_mastery(topics, correct=success, weight=weight),
                "mastery",
            )
            result["mastery_ok"] = ok
            if err:
                result["errors"].append(err)

            # R-F1252: yield event loop between tiers so SQLite worker
            # thread can drain its queue and other coroutines can run.
            # Without this, 3 sequential aiosqlite awaits can stall the
            # event loop for 3-4s under load (47% thread pool + 12%
            # aiosqlite in profiler).
            await asyncio.sleep(0)

            if summary:
                from . import knowledge
                topic_key = f"{module}:{entity_name}" if entity_name else module
                ok, err = await _run_tier(
                    knowledge.store_fact(
                        topic=topic_key,
                        content=summary[:2000],
                        source=source,
                        confidence=confidence,
                    ),
                    "knowledge",
                )
                result["knowledge_ok"] = ok
                if err:
                    result["errors"].append(err)

            await asyncio.sleep(0)

            if text_for_neural and len(text_for_neural) > 50:
                from . import neural_memory
                ok, err = await _run_tier(
                    neural_memory.learn_from_text(
                        text=text_for_neural[:5000],
                        source=source,
                        confidence=confidence,
                    ),
                    "neural",
                )
                result["neural_ok"] = ok
                if err:
                    result["errors"].append(err)
    finally:
        if acquired and sem is not None:
            sem.release()

    if gap_type:
        try:
            from . import capability_gaps
            await capability_gaps.record_gap(
                gap_type=gap_type,
                detail=gap_detail or f"{module} reported gap: {gap_type}",
                source=f"brain_hook:{module}",
                user_id=user_id,
                sector=sector,
            )
            result["gap_ok"] = True
        except Exception as e:
            result["errors"].append(f"gap: {e}")

    if result["errors"]:
        logger.warning("brain_hook(%s): %d errors — %s",
                        module, len(result["errors"]), "; ".join(result["errors"]))
    else:
        logger.info("brain_hook(%s): absorbed [mastery=%s knowledge=%s neural=%s]",
                     module, result["mastery_ok"], result["knowledge_ok"], result["neural_ok"])

    _core_ok = result["mastery_ok"] or result["knowledge_ok"]
    await _record_signal(module, success=_core_ok, sector=sector)

    _elapsed_ms = (time.time() * 1000) - _start_ms
    _record_latency(_elapsed_ms)
    _maybe_trip_breaker(reason=f"absorb({module})")
    result["latency_ms"] = round(_elapsed_ms, 1)


# ── R-F1150: Auto-gap from chat patterns ──────────────────────────────────────

_AUTO_GAP_PATTERNS: list[tuple[str, str, str]] = [
    (r"should be fixed",       "module_bug",        "Chat identified code that should be fixed"),
    (r"needs? to (be|handle)", "missing_capability", "Chat identified a missing capability"),
    (r"we should add",         "opportunity",        "Chat suggested adding a feature"),
    (r"this is a bug",         "module_bug",         "Chat identified a bug"),
    (r"missing (feature|capability|support)", "missing_capability", "Chat identified a missing feature"),
    (r"workaround|hack",       "performance",        "Chat identified a workaround that should be a proper fix"),
    (r"todo|FIXME|HACK|XXX",   "performance",        "Code contains a TODO/FIXME marker"),
]


async def auto_record_gap_from_text(text: str, source: str = "chat_response") -> Optional[str]:
    """R-F1150: scan text for improvement patterns and auto-record a gap.

    Called by the chat post-response pipeline. If the text contains phrases
    like "should be fixed" or "we should add", records a capability gap so
    the coder picks it up autonomously.

    Returns the gap_id if a gap was recorded, None otherwise.
    """
    lowered = text.lower()
    for pattern, gap_type, description in _AUTO_GAP_PATTERNS:
        if re.search(pattern, lowered):
            try:
                from . import capability_gaps
                await capability_gaps.record_gap(
                    gap_type=gap_type,
                    detail=f"{description} | source: {source} | text: {text[:300]}",
                    source=f"auto_gap:{source}",
                )
                logger.info(
                    "[R-F1150] auto-recorded %s gap from %s (matched: %s)",
                    gap_type, source, pattern,
                )
                return f"auto_{gap_type}_{hash(text[:100])}"
            except Exception as e:
                logger.debug("[R-F1150] auto-record gap failed: %s", e)
                return None
    return None

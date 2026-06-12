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

    R-F1342 (§7 infinite memory — never forget): whenever the durable fact is
    NOT persisted this cycle — because the concurrency cap skipped the tiers,
    or the knowledge tier itself timed out/failed — the fact is appended to the
    durable write-ahead log (memory_wal) and retried later, so it is never
    lost. The R-F799 fail-fast-under-contention behaviour is unchanged; the WAL
    is the safety net beneath it.
    """
    _fact_persisted = False
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
                "skipping expensive tiers for module=%s (fact -> WAL)",
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
                _fact_persisted = bool(ok)
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

    # ── R-F1342 (§7 never forget): if the durable fact did NOT land this
    # cycle (cap-skip or store_fact failure), queue it to the WAL for retry.
    # The R-F799 fast-skip is preserved; this just guarantees no fact is lost.
    if summary and not _fact_persisted:
        try:
            from . import memory_wal
            topic_key = f"{module}:{entity_name}" if entity_name else module
            memory_wal.record_pending_fact(
                topic=topic_key, content=summary[:2000],
                source=source, confidence=confidence,
            )
            result["fact_walled"] = True
        except Exception as e:
            result["errors"].append(f"wal: {e}")

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
                     module, result.get("mastery_ok"), result.get("knowledge_ok"),
                     result.get("neural_ok"))

    # R-F1342: .get() so durable_only mode (mastery/neural never set) can't
    # KeyError. The durable knowledge tier is what defines a successful absorb.
    _core_ok = bool(result.get("mastery_ok") or result.get("knowledge_ok"))
    # R-F1529: if the only error was a concurrency cap skip, record as skipped
    # (not success, not failure). This prevents modules like agent_registry from
    # appearing as 32% success when they were actually 100% successful — the
    # "failures" were just brain signal delivery being rate-limited.
    _all_concurrency_skips = bool(
        result.get("errors")
        and all("concurrency cap" in str(e) for e in result["errors"])
    )
    if _all_concurrency_skips:
        await _record_signal(module, success=False, sector=sector, skipped=True)
    else:
        await _record_signal(module, success=_core_ok, sector=sector)

    _elapsed_ms = (time.time() * 1000) - _start_ms
    _record_latency(_elapsed_ms)
    # R-F1505: pass module name for per-module trip threshold
    _maybe_trip_breaker(reason=f"absorb({module})", module=module)
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

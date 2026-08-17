"""R-F1155 — Background tier processor for brain_hook.

Moves the 3 expensive learning tiers (mastery, knowledge, neural) into
a background task so absorb() returns immediately. Imported by brain_hook.py.

Also provides auto_record_gap_from_text() for R-F1150 structural enforcement.
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import asyncio
import logging
import re
import time
from typing import Optional
from .wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.brain_hook_bg")


@fail_wire(module="brain_hook_bg", gap_type="engine_failure")
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
    _get_neural_concurrency_sem=None,
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

                # R-F1995: also store the DETAIL text as a separate RAG fact
                # so the full analysis is searchable via semantic retrieval.
                # The summary is a short status line (e.g. "DD report: Modirum
                # Gespi (company, FI) - risk=AMBER-LIGHT") — useful for exact
                # topic match but too short for meaningful semantic search.
                # The detail field contains the full research output, DD report
                # text, or analysis — make it discoverable via RAG.
                if text_for_neural and len(text_for_neural) > 200:
                    detail_topic = f"{topic_key}:detail"
                    try:
                        await _run_tier(
                            knowledge.store_fact(
                                topic=detail_topic,
                                content=text_for_neural[:5000],
                                source=source,
                                confidence=confidence,
                                skip_semantic_index=True,  # already indexed via summary
                            ),
                            "knowledge_detail",
                        )
                    except Exception:
                        pass

            # R-F1665 (wedge cure 2): the NEURAL tier was here, inside the main
            # absorb semaphore. It now runs LAST, on its own bounded lane, AFTER
            # the breaker latency is recorded on the durable core — so a slow
            # GIL-bound encode no longer occupies a main slot (which built the
            # 22-44s absorb-p95 queue) and no longer inflates the breaker p95.
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
        # R-F4102 (C-146) — `neural=` USED TO BE REPORTED HERE AND WAS A LIE.
        # This line runs BEFORE the neural lane (R-F1665 moved that lane last,
        # see below), and the caller seeds the dict with "neural_ok": False
        # (brain_hook.py:832) — so this printed the seed, not an outcome. Live
        # 2026-08-17: 131 of 131 absorbs read `neural=False`, including ones
        # whose encode succeeded. The neural outcome is now logged by the
        # neural lane itself, where it is actually known. Do not add it back
        # here: nothing at this point in the function can know it yet.
        logger.info("brain_hook(%s): absorbed [mastery=%s knowledge=%s] (durable core)",
                     module, result.get("mastery_ok"), result.get("knowledge_ok"))

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

    # ── R-F1665 (wedge cure 2): NEURAL enrichment LAST, on its OWN bounded lane.
    # Runs AFTER _record_latency/_maybe_trip_breaker, so the breaker measures the
    # DURABLE-CORE latency (mastery+knowledge — fast SQLite/disk), NOT the slow
    # GIL-bound neural encode that was driving the absorb-p95 wedge. Neural is
    # best-effort enrichment: bounded by its own small semaphore (default 2, so
    # concurrent encodes can't oversubscribe the GIL) and the same per-tier
    # timeout via _run_tier. A neural cap-skip/failure never affects the durable
    # fact (already persisted or WAL'd above) nor the module success metric.
    if (sem is None or acquired) and text_for_neural and len(text_for_neural) > 50:
        nsem = _get_neural_concurrency_sem() if _get_neural_concurrency_sem else None
        n_acquired = False
        if nsem is not None:
            try:
                await asyncio.wait_for(
                    nsem.acquire(), timeout=_ABSORB_SEM_ACQUIRE_TIMEOUT_S,
                )
                n_acquired = True
            except asyncio.TimeoutError:
                logger.debug(
                    "brain_hook bg: neural lane cap hit — skipping neural for "
                    "module=%s (durable fact already persisted)", module,
                )
                result["errors"].append(
                    "neural: concurrency cap (>{:.1f}s wait)".format(
                        _ABSORB_SEM_ACQUIRE_TIMEOUT_S
                    )
                )
        if nsem is None or n_acquired:
            try:
                # R-F1763: defer neural encode when a user is actively
                # chatting. neural_memory.learn_from_text() runs expensive
                # GIL-bound sentence-transformer encodes that starve the
                # event loop — same pattern as R-F1754's interactive backoff
                # in eagle_eye._index_codebase. When _interactive_active()
                # is True, skip the neural tier this cycle (the durable
                # knowledge fact was already persisted above, so no data
                # loss). The neural tier will re-trigger on the next
                # non-interactive absorb.
                from .brain_hook import _interactive_active
                if _interactive_active():
                    logger.debug(
                        "brain_hook bg: interactive active — skipping "
                        "neural encode for module=%s (deferred)",
                        module,
                    )
                    result["neural_ok"] = False
                    result["errors"].append(
                        "neural: deferred (interactive active)"
                    )
                else:
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
                    # R-F4102 (C-146) — the neural tier's ONE observable
                    # signal, emitted where the outcome is actually known.
                    # `result["neural_ok"]` keeps its exact True/False domain
                    # (several suites pin it); this is reporting only.
                    logger.info(
                        "brain_hook(%s): neural %s", module,
                        "encoded" if ok else f"FAILED ({err or 'unknown'})",
                    )
            finally:
                if n_acquired and nsem is not None:
                    nsem.release()


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


@fail_wire(module="brain_hook_bg", gap_type="engine_failure")
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

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.

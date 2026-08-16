"""learning_controller — R-F662 the conductor (Phase B, autonomous cron).

R-F662 (2026-05-17). Slice 5 of the OSS-only learning-framework
buildout. **Phase B** (explicit operator override granted 2026-05-17).

What this is
════════════
The conductor named in the 2026-05-17 design analysis:

    "She has the machinery — neural memory, RAG, hypothesis loop,
     mastery scoring, verification gate, propaganda filter, sanctions
     guardrail. What's missing is the conductor: a learning controller
     that picks topics, sequences them, holds bookmarks, judges when
     to stop, and refuses to ingest garbage."

R-F662 ships the controller proper. It picks topics, sequences them,
judges stop-criteria, and drives all of this WITHOUT calling any cloud
LLM. The whole point of the OSS-only learning framework is that this
loop runs autonomously without burning Anthropic or DeepSeek tokens.

What it uses (all local)
════════════════════════
  - student.get_due_topics()        — R-F664 FSRS-scheduled due queue
  - reading_queue.pop_pending()     — R-F661 failed-quiz backlog
  - rag_store.search()              — local sentence-transformer + chroma
  - neural_memory.get_cluster()     — local NetworkX graph
  - topic_completion()              — R-F660 measurement
  - student.update_mastery()        — feeds back into FSRS via R-F664

What it does NOT use
════════════════════
  - No anthropic.Client
  - No DeepSeek API
  - No openai / groq / etc.
  - No llm.complete() inside the cycle

The static guard in tests/test_rf662_*.py asserts these absences.

Cycle algorithm
═══════════════
A single call to `run_cycle(max_topics, time_budget_s)`:

  1. Collect candidate topics:
       a. FSRS-due topics (oldest-due first)
       b. Reading-queue pending items (oldest-first)
     Dedup by topic name, cap at `max_topics`.

  2. For each candidate topic, within the wall-clock budget:
       a. rag_store.search(topic, top_k=20) — local retrieval
       b. Compute LOCAL AGREEMENT: average top-pair similarity score
          across the returned chunks (chromadb already returns a
          similarity score per chunk). High agreement = topic is
          internally consistent in the corpus.
       c. update_mastery(topic, correct=agreement_score >= 0.6,
                          weight=0.4)
          — light weight because this is a passive read, not an
          active answer.
       d. topic_completion(topic) — check whether the topic now meets
          the gate criteria. If yes, mark any reading-queue entries
          for that topic as 'done'.

  3. Return the cycle report.

Cost discipline (R-F650/F651 lesson)
════════════════════════════════════
R-F650 disabled the daily cron RUN-EVAL-DAILY after one firing burned
$12.76 on Sonnet — neither cost_cap_usd nor timeout_seconds enforced.
R-F651 added the asyncio.wait_for wrap for direct-tool dispatch.

This controller's cron entry sits behind:
  - cost_cap_usd: 0.00     (zero — no LLM calls expected)
  - timeout_seconds: 120   (enforced via the R-F651 direct-tool
                            wait_for wrap)
  - ARIA_LEARNING_CONTROLLER_ENABLED env flag default 0 — operator
    must explicitly opt in before the cron fires.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

logger = logging.getLogger("aria.learning.controller")

# R-F4052 (C-108) — §21a wiring for this module's WORK, not its import.
#
# The old R-F1319 block fired `wire_success(module="learning.learning_controller")`
# at import time: it proved the file was imported, under a name
# brain_hook._MODULE_TOPICS does not contain, so it was invisible to
# `never_seen` (C-104). Real outcomes are reported by `_wire_cycle_outcome`
# below, under the REGISTERED name, on both branches.


def _wire_cycle_outcome(out: dict) -> None:
    """Report the cycle's real outcome to the brain. Never raises.

    R-F4056 (C-120) — an ADMINISTRATIVE outcome is not a quality outcome. The
    controller being switched off returns `ok=False`, and reporting that as
    `wire_failure` would (a) claim a working engine is broken, (b) drive
    `success_rate` to 0.0 on the health surface, and (c) hand the autonomous
    coder a "fix" for a flag the operator set on purpose. That is exactly the
    defect R-F3703 records against the coder scoreboard, where 4,007
    `coder_disabled` refusals were counted as failed attempts and permanently
    shut an evidence gate.

    It is reported as a throttled success-side note rather than dropped: the
    module must still show it is alive and reachable, because saying nothing is
    how C-104's modules became indistinguishable from dead.
    """
    try:
        from aria_service.intel.engine_wiring import (
            wire_failure, wire_success_throttled,
        )
        if not out.get("ok") and out.get("admin_skip"):
            wire_success_throttled(
                "learning_controller",
                f"learning cycle skipped: {out.get('error', 'disabled')}",
                source_id="learning_controller:run_cycle",
            )
        elif out.get("ok"):
            wire_success_throttled(
                "learning_controller",
                f"learning cycle: {out.get('studied_n', 0)} studied of "
                f"{out.get('candidates_n', 0)} candidates "
                f"({out.get('duration_ms', 0)}ms)",
                source_id="learning_controller:run_cycle",
            )
        else:
            wire_failure(
                module="learning_controller",
                detail=(
                    f"learning cycle failed: "
                    f"{'; '.join(str(e) for e in (out.get('errors') or []))[:300]}"
                    or str(out.get("error", "unknown"))
                ),
                gap_type="engine_failure",
                source="learning_controller:run_cycle",
            )
    except Exception as _exc:   # pragma: no cover - telemetry never blocks
        # Swallowed deliberately: brain wiring must never break this
        # module's work. Logged rather than `pass`ed so the wiring
        # failure is not itself dark (bandit B110) — wiring the wiring
        # failure would be circular.
        logger.debug("[R-F4052] learning_controller outcome wiring failed: %s", _exc)


# Operator-facing kill switch. Cron handler reads this and no-ops when
# it's not '1'/'true'/'yes'. Defaults OFF so the cron can be enabled in
# tasks.yaml without immediately firing — flip the env to turn it on.
_ENABLE_ENV = "ARIA_LEARNING_CONTROLLER_ENABLED"


def is_enabled() -> bool:
    val = (os.environ.get(_ENABLE_ENV) or "").strip().lower()
    return val in ("1", "true", "yes", "on")


def _agreement_score(chunks: list[dict]) -> float:
    """Local agreement = mean similarity score across the top retrieved
    chunks. The chroma backend already returns a `score` field (cosine
    similarity, 0..1). High mean → corpus tells a coherent story about
    the topic. Low mean → either the topic is unfamiliar OR sources
    contradict.

    Returns a value in [0.0, 1.0]. 0.0 when there are no usable chunks
    (treat as 'nothing learned')."""
    if not chunks:
        return 0.0
    scores = [
        float(c.get("score", 0.0)) for c in chunks
        if isinstance(c, dict) and c.get("score") is not None
    ]
    if not scores:
        return 0.0
    return sum(scores) / len(scores)


async def study_topic(topic: str) -> dict:
    """Run one local-only study pass on a single topic. No LLM calls.

    Returns:
        {topic, chunks_retrieved, agreement, mastery_updated, complete,
         duration_ms, error?, debounced?}
    """
    t0 = time.time()
    result: dict[str, Any] = {"topic": topic}
    if not topic or not topic.strip():
        result["error"] = "empty_topic"
        result["duration_ms"] = int((time.time() - t0) * 1000)
        return result

    # R-F663: debounce — skip topics studied within the last 5 minutes
    # so a tight cron interval doesn't redo the same RAG retrieval over
    # and over. Time window short enough that operator-visible turnaround
    # stays low; long enough to absorb hourly cron firings.
    try:
        from . import bookmarks
        since = await bookmarks.since_last_studied(topic)
        if since is not None and since < 300.0:
            result["debounced"] = True
            result["seconds_since_last_study"] = round(since, 1)
            result["duration_ms"] = int((time.time() - t0) * 1000)
            return result
    except Exception as e:
        logger.debug("R-F663: bookmark debounce check failed: %s", e)

    # 1. RAG retrieval — pure local sentence-transformer + chroma
    chunks: list[dict] = []
    try:
        from ..intel import rag_store
        chunks = await rag_store.search(topic, top_k=20, min_similarity=0.4)
    except Exception as e:
        logger.debug("R-F662 study_topic %s: rag_store.search failed: %s", topic, e)

    result["chunks_retrieved"] = len(chunks)
    agreement = _agreement_score(chunks)
    result["agreement"] = round(agreement, 3)

    # 2. Mastery update — local EWMA + FSRS only (R-F664)
    try:
        from ..intel import student
        passed = agreement >= 0.6
        await student.update_mastery([topic], correct=passed, weight=0.4)
        result["mastery_updated"] = True
        result["passed"] = passed
    except Exception as e:
        logger.debug("R-F662 study_topic %s: update_mastery failed: %s", topic, e)
        result["mastery_updated"] = False

    # 3. Completion check — purely local counters from R-F660
    try:
        from ..intel import topic_completion
        completion = await topic_completion.topic_completion(topic)
        result["complete"] = completion.get("complete", False)
        result["blockers"] = completion.get("blockers", [])
    except Exception as e:
        logger.debug("R-F662 study_topic %s: completion check failed: %s", topic, e)
        result["complete"] = False

    # R-F663: record bookmark for this study pass
    try:
        from . import bookmarks
        await bookmarks.record_bookmark(
            topic,
            chunks_count=len(chunks),
            agreement=agreement,
            notes=f"complete={result.get('complete', False)}",
        )
    except Exception as e:
        logger.debug("R-F663: bookmark record failed: %s", e)

    result["duration_ms"] = int((time.time() - t0) * 1000)
    return result


async def _collect_candidate_topics(max_topics: int) -> list[dict]:
    """Merge FSRS-due topics with reading-queue items, dedup by topic
    name. Returns oldest-first list capped at `max_topics`.

    Each entry: {topic, source, queue_id?, fsrs_due_at?}."""
    # R-F4057 (C-121) — collect the two sources SEPARATELY, then merge with a
    # reservation. Previously FSRS filled every slot first and the queue loop's
    # `if len(candidates) >= max_topics: break` fired immediately, so a mature
    # FSRS deck starved the reading queue forever. Measured live 2026-08-16:
    # 94 pending, 0 processed — a queue that had never drained one item.
    fsrs_list: list[dict] = []
    queue_list: list[dict] = []
    seen: set[str] = set()

    # FSRS-due first (these are scheduled by the proven SOTA model)
    try:
        from ..intel import student
        for entry in await student.get_due_topics(limit=max_topics):
            t = (entry.get("topic") or "").strip()
            if not t or t in seen:
                continue
            seen.add(t)
            fsrs_list.append({
                "topic": t,
                "source": "fsrs_due",
                "fsrs_due_at": entry.get("due_at"),
                "retention_now": entry.get("retention_now"),
            })
    except Exception as e:
        logger.debug("R-F662: get_due_topics failed: %s", e)

    # Reading queue second — additions, not duplicates
    try:
        from . import reading_queue
        for item in await reading_queue.pop_pending(limit=max_topics):
            t = (item.get("topic") or "").strip()
            if not t or t in seen:
                continue
            seen.add(t)
            queue_list.append({
                "topic": t,
                "source": "reading_queue",
                "queue_id": item.get("id"),
                "reason": item.get("reason"),
            })
    except Exception as e:
        logger.debug("R-F662: reading_queue.pop_pending failed: %s", e)

    # R-F4057 (C-121) — merge with a BOUNDED reservation for the queue.
    #
    # The rule: no candidate source may be starved indefinitely by another. The
    # queue only needs to make progress, not to win — FSRS stays the majority
    # because it is the proven scheduler and its due topics are time-sensitive.
    # Reordering the queue ahead of FSRS would just trade one starvation for
    # the other.
    #
    # The reservation costs nothing when there is nothing to drain: unused
    # slots fall straight back to FSRS below.
    # The reservation is held at the TAIL, not the head: R-F662's contract is
    # FSRS-FIRST ordering (pinned by test_rf662_collect_merges_fsrs_and_queue),
    # and starvation is about INCLUSION, not priority. Taking the slot from the
    # end fixes the starvation without demoting the proven scheduler.
    reserved = 1 if (max_topics >= 3 and queue_list) else 0
    fsrs_take = max(0, max_topics - reserved)

    candidates: list[dict] = fsrs_list[:fsrs_take]
    for c in queue_list:
        if len(candidates) >= max_topics:
            break
        candidates.append(c)
    # Give the held-back slot straight back to FSRS if the queue did not use it.
    for c in fsrs_list[fsrs_take:]:
        if len(candidates) >= max_topics:
            break
        candidates.append(c)

    return candidates[:max_topics]


async def run_cycle(
    *, max_topics: int = 5, time_budget_s: float = 120.0,
) -> dict:
    """One learning cycle. Zero cloud LLM calls.

    Args:
        max_topics:     hard cap on topics studied in this cycle.
        time_budget_s:  wall-clock budget — the cycle returns early
                        with whatever it has if exceeded.

    Returns:
        {ok, started_at, duration_ms, candidates_n, studied_n,
         results[study_topic results], completed_topics[], errors}
    """
    started_at = time.time()
    out: dict[str, Any] = {
        "ok": True,
        "started_at": started_at,
        "candidates_n": 0,
        "studied_n": 0,
        "results": [],
        "completed_topics": [],
        "errors": [],
    }

    if not is_enabled():
        out["ok"] = False
        # R-F4056 (C-120) — mark this as an ADMINISTRATIVE skip, not a failure.
        # `ok` stays False so run_cycle's contract and every existing caller are
        # unchanged; only the brain wiring reads this flag.
        out["admin_skip"] = True
        out["error"] = (
            f"controller disabled — set {_ENABLE_ENV}=1 to activate"
        )
        out["duration_ms"] = int((time.time() - started_at) * 1000)
        _wire_cycle_outcome(out)
        return out

    try:
        candidates = await _collect_candidate_topics(max_topics)
    except Exception as e:
        out["ok"] = False
        out["errors"].append(f"collect failed: {e}")
        out["duration_ms"] = int((time.time() - started_at) * 1000)
        _wire_cycle_outcome(out)
        return out

    out["candidates_n"] = len(candidates)
    if not candidates:
        out["duration_ms"] = int((time.time() - started_at) * 1000)
        _wire_cycle_outcome(out)
        return out

    # Study loop — bail on time budget
    for cand in candidates:
        if (time.time() - started_at) >= time_budget_s:
            out["errors"].append("time_budget_exhausted")
            break
        try:
            res = await study_topic(cand["topic"])
        except Exception as e:
            res = {"topic": cand["topic"], "error": str(e)[:200]}
            out["errors"].append(f"{cand['topic']}: {e}")
        res["source"] = cand.get("source")
        if cand.get("queue_id") is not None:
            res["queue_id"] = cand["queue_id"]
        out["results"].append(res)
        out["studied_n"] += 1

        # If complete, mark the corresponding reading-queue entry done.
        if res.get("complete") and cand.get("queue_id") is not None:
            try:
                from . import reading_queue
                await reading_queue.mark_processed(
                    int(cand["queue_id"]), status="done",
                    notes="completed by R-F662 cycle",
                )
                out["completed_topics"].append(cand["topic"])
            except Exception as e:
                logger.debug(
                    "R-F662: mark_processed failed for queue_id=%s: %s",
                    cand.get("queue_id"), e,
                )

    out["duration_ms"] = int((time.time() - started_at) * 1000)
    _wire_cycle_outcome(out)
    return out

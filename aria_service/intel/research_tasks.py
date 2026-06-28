"""
ARIA Research Tasks — background long-running research operations.

Why this exists
═══════════════
Some user requests can't be answered in a single LLM call:
  - "Research each of these 10 suppliers"
  - "Investigate Wearwiser, then compare with Acme"
  - "Crawl their site, then check sanctions, then build a profile"

Running them inline blocks the WhatsApp chat for 2-4 minutes (or hits a
timeout and dies). Worse: the same expensive work gets re-done if a
second user asks the same question 5 minutes later.

The fix: a background task system. The user request is acknowledged
immediately with a task_id. The actual research runs in an asyncio
background task. Results are persisted to the RAG store + the proactive
alert queue so the team is notified the moment the task completes.

How it works
════════════
    1. Caller spawns a task via spawn_research_task() with type + params
    2. Task is recorded in Redis with status="queued"
    3. Background runner executes the task
       - Status moves: queued → running → complete (or failed)
       - Each step writes progress to Redis
       - Results are saved + auto-pushed to RAG store
    4. On completion, a proactive alert is queued with the result
       so the WhatsApp listener surfaces it on the next 90s poll
    5. Failures fire the self-diagnostic so ARIA learns from them

Task types supported
════════════════════
  - "investigate"           — ARIA's deep_researcher.investigate()
  - "crawl"                 — ARIA's deep_researcher.crawl_website()
  - "research_each"         — multi-entity batch (the IFV use case)
  - "compare_entities"      — head-to-head comparison
  - "profile"               — build_profile() on a single entity
  - "compliance_screen"     — fuzzy_screen + investigate combo
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from . import redis_store as rs
from .wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.research_tasks")

TASKS_KEY_PREFIX = "crucix:aria:research_task:"
TASKS_INDEX_KEY = "crucix:aria:research_tasks_index"
TASK_TTL_SECONDS = 7 * 86400  # 7 days

# In-memory task registry — holds the actual asyncio.Task references
# so the GC can't collect them mid-execution
_running_tasks: dict[str, asyncio.Task] = {}
_max_concurrent = 3   # don't run more than 3 long ops in parallel


# ── Status helpers ──────────────────────────────────────────────────────────

async def _save_task(task: dict) -> None:
    task_id = task.get("id")
    if not task_id:
        return
    try:
        await rs.set_json(f"{TASKS_KEY_PREFIX}{task_id}", task, ex=TASK_TTL_SECONDS)
        # Maintain a lightweight index of recent tasks
        index = await rs.get_json(TASKS_INDEX_KEY) or []
        # Remove any older entry for this id, prepend the fresh one
        index = [e for e in index if e.get("id") != task_id]
        index.insert(0, {
            "id": task_id,
            "type": task.get("type"),
            "status": task.get("status"),
            "created_at": task.get("created_at"),
            "title": task.get("title"),
            "requested_by": task.get("requested_by"),
            "updated_at": task.get("updated_at"),
        })
        index = index[:200]
        await rs.set_json(TASKS_INDEX_KEY, index, ex=TASK_TTL_SECONDS)
    except Exception as e:
        logger.warning("Failed to save task %s: %s", task_id, e)


async def _update_task(task_id: str, **updates) -> dict | None:
    task = await get_task(task_id)
    if not task:
        return None
    task.update(updates)
    task["updated_at"] = time.time()
    await _save_task(task)
    return task


@fail_wire(module="research_tasks", gap_type="source_failure")
async def get_task(task_id: str) -> dict | None:
    if not task_id:
        return None
    try:
        return await rs.get_json(f"{TASKS_KEY_PREFIX}{task_id}")
    except Exception as e:
        logger.debug("get_task failed: %s", e)


        return None


@fail_wire(module="research_tasks", gap_type="source_failure")
async def list_tasks(limit: int = 30, status_filter: str | None = None) -> list[dict]:
    try:
        index = await rs.get_json(TASKS_INDEX_KEY) or []
        if status_filter:
            index = [t for t in index if t.get("status") == status_filter]
        return index[:limit]
    except Exception:
        return []


# ── Spawning + running ──────────────────────────────────────────────────────

def _eta_for_type(task_type: str, params: dict) -> int:
    """Rough seconds estimate so the user knows what to expect."""
    if task_type == "investigate":   return 120
    if task_type == "crawl":         return 90
    if task_type == "research_each":
        n = len(params.get("entities", []) or [])
        return max(60, 60 * n)
    if task_type == "compare_entities":
        n = len(params.get("entities", []) or [])
        return max(120, 60 * n)
    if task_type == "profile":            return 60
    if task_type == "compliance_screen":  return 45
    return 90


@fail_wire(module="research_tasks", gap_type="source_failure")
async def spawn_research_task(
    task_type: str,
    params: dict,
    *,
    title: str = "",
    requested_by: str = "system",
    chat_id: str = "",
    llm: Any = None,
) -> dict:
    """Create a research task, schedule it to run in the background, return
    the task record immediately so the caller can acknowledge the user.

    The actual work happens in _run_task() — fire-and-forget.
    """
    task_id = f"rt_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
    now = time.time()

    # Concurrency limit — fail fast if we're already overloaded
    active = sum(1 for t in _running_tasks.values() if not t.done())
    if active >= _max_concurrent:
        return {
            "id": task_id,
            "status": "rejected",
            "reason": f"max_concurrent_reached ({active}/{_max_concurrent})",
            "retry_after_s": 60,
        }

    task = {
        "id": task_id,
        "type": task_type,
        "params": params or {},
        "title": (title or task_type)[:200],
        "requested_by": requested_by[:120],
        "chat_id": chat_id[:120],
        "status": "queued",
        "progress": "queued",
        "created_at": now,
        "updated_at": now,
        "eta_seconds": _eta_for_type(task_type, params or {}),
        "result": None,
        "error": None,
    }
    await _save_task(task)

    # Schedule the actual work — fire-and-forget but keep a strong ref.
    # Wrap with a 10-minute hard timeout so tasks can't hang forever
    # (incident 2026-04-10: rt_1775813834565 stuck in "running" indefinitely).
    _TASK_HARD_TIMEOUT = 600  # 10 minutes
    async def _run_with_timeout():
        try:
            await asyncio.wait_for(_run_task(task_id, llm), timeout=_TASK_HARD_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning("Research task %s hit hard timeout (%ds)", task_id, _TASK_HARD_TIMEOUT)
            await _update_task(
                task_id, status="failed",
                progress="timeout", error=f"Hard timeout after {_TASK_HARD_TIMEOUT}s",
                failed_at=time.time(),
            )
            await _push_failure_alert(task_id, task, TimeoutError(f"Task exceeded {_TASK_HARD_TIMEOUT}s"))

    job = asyncio.create_task(_run_with_timeout(), name=f"research_task:{task_id}")
    _running_tasks[task_id] = job
    job.add_done_callback(lambda t: _running_tasks.pop(task_id, None))

    logger.info("Spawned research task %s (type=%s, eta=%ds)", task_id, task_type, task["eta_seconds"])
    return task


# ── Task executor ──────────────────────────────────────────────────────────

async def _run_task(task_id: str, llm: Any) -> None:
    """Execute a research task end-to-end. Called via asyncio.create_task."""
    task = await get_task(task_id)
    if not task:
        logger.warning("Task %s missing from Redis at run start", task_id)
        return

    task_type = task["type"]
    params = task.get("params") or {}

    started = time.time()
    await _update_task(task_id, status="running", progress="started", started_at=started)

    # Attribute every LLM call this task fires to "research_task" so /cost
    # shows long-running background research separately from interactive chat.
    # Use the contextvar set/reset pattern (instead of `with`) so we don't
    # have to reindent the entire body of the existing try block.
    from . import cost_tracker
    _cost_token = cost_tracker.set_feature("research_task")
    try:
        if task_type == "investigate":
            result = await _do_investigate(task_id, params, llm)
        elif task_type == "crawl":
            result = await _do_crawl(task_id, params, llm)
        elif task_type == "research_each":
            result = await _do_research_each(task_id, params, llm)
        elif task_type == "compare_entities":
            result = await _do_compare_entities(task_id, params, llm)
        elif task_type == "profile":
            result = await _do_profile(task_id, params, llm)
        elif task_type == "compliance_screen":
            result = await _do_compliance_screen(task_id, params, llm)
        else:
            raise ValueError(f"unknown task type: {task_type}")

        duration = int((time.time() - started) * 1000)
        await _update_task(
            task_id,
            status="complete",
            progress="complete",
            result=result,
            duration_ms=duration,
            completed_at=time.time(),
        )

        # Push to proactive alert queue so the WhatsApp listener
        # forwards the result to the group
        await _push_completion_alert(task_id, task, result)

        logger.info("Research task %s complete in %dms", task_id, duration)
        # R-F1059 — wire task completion to brain
        try:
            from .engine_wiring import wire_success as _ws, wire_failure
            _ws(
                module="research_tasks",
                summary=f"Research task complete: {task_id} ({task_type})",
                detail=f"duration={duration}ms",
                source_id=f"research_tasks:{task_id}",
            )
        except Exception:
            pass

    except Exception as e:
        logger.warning("Research task %s failed: %s", task_id, e)
        await _update_task(
            task_id,
            status="failed",
            progress="failed",
            error=str(e)[:500],
            failed_at=time.time(),
        )
        # Push a failure alert so the team knows the background work died
        # rather than silently disappearing
        await _push_failure_alert(task_id, task, e)
        # Fire self-diagnostic so ARIA learns from the failure
        try:
            from . import self_improve
            await self_improve.diagnose_failure(
                "research_task_failure",
                str(e),
                {"task_id": task_id, "task_type": task_type},
            )
        except Exception:
            pass
    finally:
        # Always reset the contextvar so we don't leak the
        # "research_task" attribution into the runner's other coroutines
        cost_tracker.reset_feature(_cost_token)


# ── Task implementations ───────────────────────────────────────────────────

async def _progress(task_id: str, msg: str) -> None:
    await _update_task(task_id, progress=msg)
    logger.info("[task %s] %s", task_id, msg)


async def _do_investigate(task_id: str, params: dict, llm: Any) -> dict:
    from .deep_researcher import investigate
    topic = params.get("topic") or params.get("query") or ""
    depth = params.get("depth", "thorough")
    if not topic:
        raise ValueError("investigate task needs a topic")
    await _progress(task_id, f"investigating: {topic[:80]}")
    return await investigate(llm, topic, depth=depth)


async def _do_crawl(task_id: str, params: dict, llm: Any) -> dict:
    from .deep_researcher import crawl_website
    url = params.get("url", "")
    max_pages = int(params.get("max_pages", 15))
    context = params.get("context", "")
    if not url:
        raise ValueError("crawl task needs a url")
    await _progress(task_id, f"crawling: {url[:80]}")
    return await crawl_website(llm, url, max_pages=max_pages, context=context)


async def _do_research_each(task_id: str, params: dict, llm: Any) -> dict:
    """Multi-entity batch: research each entity in parallel batches.

    The Turkish IFV use case: 5-10 supplier names, each gets a full
    investigate cycle, results aggregated into a single comparative brief.
    """
    from .deep_researcher import investigate
    entities = params.get("entities") or []
    context = params.get("context", "")
    if not entities:
        raise ValueError("research_each task needs entities list")

    await _progress(task_id, f"researching {len(entities)} entities in parallel batches")

    # Run in batches of 2 to avoid overwhelming the LLM
    batch_size = 2
    results: list[dict] = []
    for i in range(0, len(entities), batch_size):
        batch = entities[i : i + batch_size]
        await _progress(task_id, f"batch {i // batch_size + 1}/{(len(entities) + batch_size - 1) // batch_size}: {batch}")
        batch_coros = [
            investigate(llm, f"{e}{' ' + context if context else ''}", depth="quick")
            for e in batch
        ]
        batch_results = await asyncio.gather(*batch_coros, return_exceptions=True)
        for entity, r in zip(batch, batch_results):
            if isinstance(r, Exception):
                results.append({"entity": entity, "error": str(r)})
            else:
                results.append({"entity": entity, **r})

    # Synthesise the comparative brief — use the LLM one final time to
    # turn the per-entity findings into a recommendation
    synth = None
    if llm and getattr(llm, "is_configured", False):
        try:
            await _progress(task_id, "synthesising comparative brief")
            findings_block = "\n\n".join(
                f"## {r.get('entity')}\n"
                f"Articles read: {r.get('articles_read', 0)}, facts: {r.get('facts_learned', 0)}\n"
                f"Synthesis: {(r.get('synthesis') or {}).get('strategic_implications', 'N/A')[:600]}\n"
                f"Top findings: {((r.get('synthesis') or {}).get('key_findings') or [])[:5]}"
                for r in results if "error" not in r
            )
            synth_prompt = f"""You are ARIA producing a comparative supplier brief for Arkmurus.

CONTEXT FROM USER: {context or 'No additional context'}

PER-ENTITY RESEARCH:
{findings_block[:6000]}

Produce a structured brief that:
1. Compares the entities head-to-head (strengths / weaknesses / track record)
2. Flags compliance / sanctions / embargo concerns for each
3. Identifies the BEST pick for the Arkmurus use case + WHY
4. Suggests next actions (further investigation, contact, partnership)
5. Marks every claim with [CONFIRMED] / [PROBABLE] / [ASSESSED] / [UNCERTAIN]

Be specific. Cite the entity names, not generic statements."""
            r = await llm.complete(
                "You are ARIA — defence procurement intelligence analyst producing a comparative supplier brief.",
                synth_prompt,
                max_tokens=3000,
                timeout=120.0,
            )
            synth = r.text
        except Exception as e:
            logger.warning("research_each synthesis failed: %s", e)
            synth = f"(Synthesis step failed: {e}. Per-entity findings above are still valid.)"

    return {
        "type": "research_each",
        "entities_researched": len(results),
        "per_entity": results,
        "synthesis": synth,
        "context": context,
    }


async def _do_compare_entities(task_id: str, params: dict, llm: Any) -> dict:
    """Head-to-head comparison — like research_each but with explicit comparison."""
    params2 = dict(params)
    if "entities" not in params2 and "names" in params2:
        params2["entities"] = params2["names"]
    return await _do_research_each(task_id, params2, llm)


async def _do_profile(task_id: str, params: dict, llm: Any) -> dict:
    from .deep_researcher import build_profile
    entity = params.get("entity") or params.get("name") or ""
    profile_type = params.get("type", "auto")
    if not entity:
        raise ValueError("profile task needs an entity")
    await _progress(task_id, f"profiling: {entity[:80]}")
    return await build_profile(llm, entity, profile_type)


async def _do_compliance_screen(task_id: str, params: dict, llm: Any) -> dict:
    from . import sanctions
    from .deep_researcher import build_profile
    entity = params.get("entity", "")
    if not entity:
        raise ValueError("compliance_screen task needs an entity")
    await _progress(task_id, f"sanctions screen: {entity}")
    screen = await sanctions.fuzzy_screen(entity)
    await _progress(task_id, f"profile build: {entity}")
    profile = await build_profile(llm, entity, "company") if llm and llm.is_configured else None
    return {
        "type": "compliance_screen",
        "entity": entity,
        "sanctions_screen": screen,
        "profile": profile,
    }


# ── Completion alert ───────────────────────────────────────────────────────

async def _push_completion_alert(task_id: str, task: dict, result: dict) -> None:
    """When a task completes, push the result to the proactive alert queue
    so the WhatsApp listener forwards it to the group."""
    try:
        from . import proactive
        title = task.get("title") or task.get("type") or "research task"
        # Build a compact summary depending on task type
        body_parts = [f"*Task {task_id}* — _{title}_"]
        if task["type"] == "research_each":
            n = result.get("entities_researched", 0)
            body_parts.append(f"Researched {n} entities.")
            synth = result.get("synthesis")
            if synth:
                body_parts.append("")
                body_parts.append(synth[:2500])
        elif task["type"] in ("investigate", "crawl"):
            facts = result.get("facts_learned", 0) or 0
            pages = result.get("pages_crawled", 0) or result.get("articles_read", 0) or 0
            body_parts.append(f"Read {pages} sources, learned {facts} facts.")
            synth = result.get("synthesis")
            if isinstance(synth, dict):
                impl = synth.get("strategic_implications") or ""
                actions = synth.get("recommended_actions") or []
                if impl: body_parts.append(f"\n*Implications*: {impl[:600]}")
                if actions:
                    body_parts.append("\n*Recommended actions*:")
                    for a in actions[:5]:
                        body_parts.append(f"  • {a}")
        elif task["type"] == "compliance_screen":
            screen = result.get("sanctions_screen", {})
            blocked = screen.get("blocked", False)
            body_parts.append(f"Compliance: {'⛔ BLOCKED' if blocked else '✅ clear'}")
            top_match = (screen.get("matches") or [{}])[0]
            if top_match.get("name"):
                body_parts.append(f"Top match: {top_match.get('name')} ({top_match.get('score', 0)})")
        elif task["type"] == "profile":
            body_parts.append(f"Profile built: {result.get('summary', 'see /task ' + task_id)[:600]}")

        await proactive.push_alert({
            "type": "research_complete",
            "title": f"✅ Research complete — {title}",
            "severity": "info",
            "body": "\n".join(body_parts)[:3500],
            "metadata": {"task_id": task_id, "task_type": task["type"]},
        })
    except Exception as e:
        logger.warning("Push completion alert failed: %s", e)


async def _push_failure_alert(task_id: str, task: dict, err: Exception) -> None:
    """When a task fails, surface it to the alerts group so the team can
    intervene rather than waiting indefinitely for a result that never lands."""
    try:
        from . import proactive
        title = task.get("title") or task.get("type") or "research task"
        body = (
            f"*Task {task_id}* — _{title}_\n"
            f"Type: {task.get('type')}\n"
            f"Error: {str(err)[:600]}\n\n"
            f"_Use */task {task_id}* for full details._"
        )
        await proactive.push_alert({
            "type": "research_failed",
            "title": f"❌ Research failed — {title}",
            "severity": "warning",
            "body": body,
            "metadata": {"task_id": task_id, "task_type": task.get("type")},
        })
    except Exception as e:
        logger.warning("Push failure alert failed: %s", e)


# ── Cancel + cleanup ──────────────────────────────────────────────────────

@fail_wire(module="research_tasks", gap_type="source_failure")
async def cancel_task(task_id: str) -> dict:
    """Cancel a running research task. Marks it as failed + cancelled."""
    job = _running_tasks.get(task_id)
    if job and not job.done():
        job.cancel()
        await _update_task(
            task_id, status="failed",
            progress="cancelled", error="Cancelled by user/admin",
            failed_at=time.time(),
        )
        _running_tasks.pop(task_id, None)
        return {"ok": True, "task_id": task_id, "status": "cancelled"}

    # Task not in memory — might be a stale "running" record from a
    # prior process (e.g. pre-deploy). Force-update Redis status.
    task = await get_task(task_id)
    if task and task.get("status") == "running":
        await _update_task(
            task_id, status="failed",
            progress="cancelled_stale",
            error="Marked as failed — task was stale (process likely restarted)",
            failed_at=time.time(),
        )
        return {"ok": True, "task_id": task_id, "status": "cancelled_stale"}

    return {"ok": False, "error": f"Task {task_id} not found or not running"}


@fail_wire(module="research_tasks", gap_type="source_failure")
async def cleanup_stale_tasks(max_age_seconds: int = 900) -> dict:
    """Find tasks stuck in 'running' status beyond max_age and mark them failed.

    This handles the case where a deploy/restart killed the asyncio task
    but Redis still shows status=running. Default: 15 minutes.
    """
    index = await rs.get_json(TASKS_INDEX_KEY) or []
    now = time.time()
    cleaned = 0
    for entry in index:
        if entry.get("status") != "running":
            continue
        task = await get_task(entry.get("id", ""))
        if not task:
            continue
        started = task.get("started_at") or task.get("created_at", 0)
        if now - started > max_age_seconds:
            await _update_task(
                task["id"], status="failed",
                progress="stale_cleanup",
                error=f"Task was running for {int(now - started)}s — marked stale by cleanup",
                failed_at=now,
            )
            cleaned += 1
    return {"cleaned": cleaned, "max_age_seconds": max_age_seconds}


# ── Stats ───────────────────────────────────────────────────────────────────

@fail_wire(module="research_tasks", gap_type="source_failure")
async def get_stats() -> dict:
    """Return overview of the research task system."""
    try:
        index = await rs.get_json(TASKS_INDEX_KEY) or []
        by_status: dict[str, int] = {}
        by_type: dict[str, int] = {}
        for t in index:
            by_status[t.get("status", "unknown")] = by_status.get(t.get("status", "unknown"), 0) + 1
            by_type[t.get("type", "unknown")] = by_type.get(t.get("type", "unknown"), 0) + 1
        return {
            "total_tasks": len(index),
            "by_status": by_status,
            "by_type": by_type,
            "currently_running": sum(1 for t in _running_tasks.values() if not t.done()),
            "max_concurrent": _max_concurrent,
        }
    except Exception as e:
        return {"error": str(e)}

# R-F2119 §21a — wire failure handler for research_tasks
try:
    wire_failure(module="research_tasks", detail="module shutdown",
                gap_type="engine_failure", source="research_tasks:shutdown")
except Exception:
    pass

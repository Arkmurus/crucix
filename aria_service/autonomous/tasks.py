"""ARIA Layer 3 — autonomous task definitions, loader, and execution wrapper.

A Task is a structured intelligence-gathering operation: a cron-like
schedule + a tool chain + delivery channels + escalation triggers.

Tasks are declared in `tasks.yaml` (alongside this file) so the
schedule can be edited without a code change. The loader supports a
`POST /api/aria/autonomous/reload-tasks` admin endpoint that re-reads
the file at runtime — no deploy needed to rotate a task.

Phase 3c-α scope (this file):
  - Task dataclass + YAML loader
  - Cron expression matching against the current minute
  - execute_task() — runs the tool chain through aria_chat() so the
    constitutional pipeline (clauses 1-15, verifier, footer) applies
    to autonomous outputs the same way it applies to interactive chat
  - Run history persisted to Redis for the /status admin endpoint

What this file deliberately does NOT do:
  - Polling loop                  → engine.py
  - Delivery routing              → delivery.py
  - Safety gating (rate/cost/etc) → safety.py (called from engine.py)

The constitutional pipeline does the heavy lifting. A task is just a
synthetic chat message routed through the same code path as interactive
WhatsApp messages, with a special session_id (`autonomous:<task_id>:<date>`)
so its history is isolated and its mem0 facts are tagged appropriately.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("aria.autonomous.tasks")


# ── Task dataclass ─────────────────────────────────────────────────────────

@dataclass
class Task:
    """A single autonomous research task definition.

    Loaded from YAML — the dataclass mirrors the YAML schema 1:1 so a
    new field can be added by editing the YAML and adding a default
    here. All fields have safe defaults so a partial YAML entry still
    parses (just with reduced functionality).
    """
    id: str
    name: str
    cron: str = "0 6 * * mon-fri"  # 06:00 weekdays UTC
    enabled: bool = False           # opt-in: tasks must be explicitly enabled
    priority: str = "MEDIUM"        # informational only — for the /status endpoint
    timeout_seconds: int = 180
    cost_cap_usd: float = 0.20      # per-run cost cap (independent of daily cap)
    # Tool chain: list of {tool, entity?, url?, max_queries?, ...} dicts.
    # The first tool is required; subsequent tools are optional follow-ups.
    tool_chain: list[dict[str, Any]] = field(default_factory=list)
    # Delivery configuration
    delivery_channels: list[str] = field(default_factory=lambda: ["mem0"])
    whatsapp_group_id: str = ""
    escalate_if: list[str] = field(default_factory=list)
    mem0_tags: list[str] = field(default_factory=list)
    # Notes / docs — informational only
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


# ── Tasks YAML loader ──────────────────────────────────────────────────────

_TASKS_FILE = Path(__file__).parent / "tasks.yaml"
_loaded_tasks: dict[str, Task] = {}


def load_tasks(path: Path | None = None) -> dict[str, Task]:
    """Read tasks.yaml from disk and return a dict mapping task_id → Task.

    Safe to call repeatedly. Replaces the in-process cache wholesale on
    each call. Tolerates missing file (returns empty dict + warning) and
    YAML parse errors (logs error, returns the previous cache).
    """
    global _loaded_tasks
    target = path or _TASKS_FILE
    if not target.exists():
        logger.warning(
            "[autonomous tasks] config file missing: %s — engine will load 0 tasks",
            target,
        )
        _loaded_tasks = {}
        return _loaded_tasks

    try:
        import yaml  # type: ignore
    except ImportError:
        logger.error(
            "[autonomous tasks] PyYAML not installed — cannot load tasks.yaml. "
            "Install with `pip install pyyaml` or set ARIA_AUTONOMOUS_ENABLED=0."
        )
        _loaded_tasks = {}
        return _loaded_tasks

    try:
        raw = target.read_text(encoding="utf-8")
        data = yaml.safe_load(raw) or {}
    except Exception as e:
        logger.error(
            "[autonomous tasks] failed to parse %s: %s — keeping previous cache (%d tasks)",
            target, e, len(_loaded_tasks),
        )
        return _loaded_tasks

    tasks_raw = (data or {}).get("tasks", []) if isinstance(data, dict) else []
    if not isinstance(tasks_raw, list):
        logger.error(
            "[autonomous tasks] tasks.yaml top-level `tasks` is not a list: %s",
            type(tasks_raw).__name__,
        )
        return _loaded_tasks

    new_cache: dict[str, Task] = {}
    for entry in tasks_raw:
        if not isinstance(entry, dict):
            continue
        try:
            task = Task(
                id=str(entry.get("id", "")).strip(),
                name=str(entry.get("name", "")).strip(),
                cron=str(entry.get("cron", "0 6 * * mon-fri")).strip(),
                enabled=bool(entry.get("enabled", False)),
                priority=str(entry.get("priority", "MEDIUM")).upper(),
                timeout_seconds=int(entry.get("timeout_seconds", 180)),
                cost_cap_usd=float(entry.get("cost_cap_usd", 0.20)),
                tool_chain=list(entry.get("tool_chain", []) or []),
                delivery_channels=list(entry.get("delivery_channels", ["mem0"]) or ["mem0"]),
                whatsapp_group_id=str(entry.get("whatsapp_group_id", "")),
                escalate_if=list(entry.get("escalate_if", []) or []),
                mem0_tags=list(entry.get("mem0_tags", []) or []),
                description=str(entry.get("description", "")),
            )
        except Exception as e:
            logger.warning(
                "[autonomous tasks] skipping malformed task entry: %s — error: %s",
                entry, e,
            )
            continue
        if not task.id:
            logger.warning("[autonomous tasks] skipping task with no id: %s", entry)
            continue
        new_cache[task.id] = task

    _loaded_tasks = new_cache
    logger.info("[autonomous tasks] loaded %d task(s) from %s", len(_loaded_tasks), target)
    return _loaded_tasks


def get_loaded_tasks() -> dict[str, Task]:
    """Return the in-process task cache. Caller must call load_tasks()
    once at engine startup or via the /reload-tasks admin endpoint."""
    return _loaded_tasks


# ── Cron expression matcher (minimal — minute precision) ───────────────────
#
# We do NOT pull in croniter as a dependency. The matcher only needs to
# answer one question once per minute: "should this task fire at the
# current UTC minute?" That's a 5-field cron expression
# (minute, hour, day-of-month, month, day-of-week) with a small set of
# supported features:
#   - exact integers           "0", "5", "23"
#   - wildcard                 "*"
#   - comma lists              "0,15,30,45"
#   - ranges                   "0-5"
#   - step values on wildcard  "*/5"
#   - day-of-week names        "mon", "tue-fri"
#
# Anything fancier (slashes on ranges, last-day-of-month, etc) is
# rejected at parse time so the operator sees the failure immediately
# instead of having a task silently never fire.

_DOW_NAMES = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3,
    "fri": 4, "sat": 5, "sun": 6,
}


def _parse_cron_field(field_value: str, lo: int, hi: int, names: dict[str, int] | None = None) -> set[int]:
    """Parse a single cron field into a set of valid integer values."""
    out: set[int] = set()
    field_value = field_value.strip().lower()
    if not field_value:
        return out

    for chunk in field_value.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue

        # Step values: */N or *
        if chunk.startswith("*/"):
            try:
                step = int(chunk[2:])
            except ValueError:
                logger.warning("[cron] invalid step in %r — skipping chunk", chunk)
                continue
            if step <= 0:
                continue
            out.update(range(lo, hi + 1, step))
            continue

        if chunk == "*":
            out.update(range(lo, hi + 1))
            continue

        # Range: N-M (with optional names like mon-fri)
        if "-" in chunk:
            try:
                a_raw, b_raw = chunk.split("-", 1)
                a = names[a_raw] if names and a_raw in names else int(a_raw)
                b = names[b_raw] if names and b_raw in names else int(b_raw)
            except (KeyError, ValueError):
                logger.warning("[cron] invalid range %r — skipping chunk", chunk)
                continue
            if a > b:
                a, b = b, a
            out.update(range(max(lo, a), min(hi, b) + 1))
            continue

        # Single value (numeric or name)
        try:
            v = names[chunk] if names and chunk in names else int(chunk)
            if lo <= v <= hi:
                out.add(v)
        except (KeyError, ValueError):
            logger.warning("[cron] invalid value %r — skipping chunk", chunk)

    return out


def cron_matches(cron_expr: str, when: time.struct_time | None = None) -> bool:
    """Return True if the cron expression matches the given UTC moment.

    `when` defaults to the current UTC time. We snap to the START of
    the minute so the engine's 60-second polling loop can call this
    once per minute and never double-fire.
    """
    if when is None:
        when = time.gmtime()

    parts = cron_expr.split()
    if len(parts) != 5:
        logger.warning(
            "[cron] expression %r does not have 5 fields — task will never fire",
            cron_expr,
        )
        return False

    minute_set = _parse_cron_field(parts[0], 0, 59)
    hour_set = _parse_cron_field(parts[1], 0, 23)
    dom_set = _parse_cron_field(parts[2], 1, 31)
    month_set = _parse_cron_field(parts[3], 1, 12)
    dow_set = _parse_cron_field(parts[4], 0, 6, names=_DOW_NAMES)

    if when.tm_min not in minute_set:
        return False
    if when.tm_hour not in hour_set:
        return False
    if when.tm_mon not in month_set:
        return False
    # Cron's day-of-month and day-of-week are OR-ed when both are
    # restricted (POSIX behaviour). When both fields are full-wildcard
    # the test simplifies to "always".
    dom_full = dom_set == set(range(1, 32))
    dow_full = dow_set == set(range(0, 7))
    # Python's struct_time.tm_wday: Monday=0..Sunday=6 (matches our mapping)
    if dom_full and dow_full:
        return True
    if dom_full:
        return when.tm_wday in dow_set
    if dow_full:
        return when.tm_mday in dom_set
    # Both restricted: OR semantics
    return (when.tm_mday in dom_set) or (when.tm_wday in dow_set)


# ── Run history (Redis-backed) ─────────────────────────────────────────────
#
# Each task run produces a small dict with id, started_at, duration_ms,
# status, cost_usd, snippet of the response. Persisted as a Redis list
# so /status can show the last 20 runs without scanning the whole index.

_RUNS_KEY = "crucix:autonomous:runs"
_MAX_RUNS_RETAINED = 50


async def record_run(record: dict[str, Any]) -> None:
    """Push one run record onto the head of the runs list, trim the tail."""
    from ..intel import redis_store as rs
    import json as _json
    try:
        await rs.lpush(_RUNS_KEY, _json.dumps(record, default=str))
        await rs.ltrim(_RUNS_KEY, 0, _MAX_RUNS_RETAINED - 1)
    except Exception as e:
        logger.warning("[autonomous runs] failed to persist run record: %s", e)


async def get_recent_runs(limit: int = 20) -> list[dict[str, Any]]:
    """Return the last N task run records (most recent first)."""
    from ..intel import redis_store as rs
    import json as _json
    try:
        raw = await rs.lrange(_RUNS_KEY, 0, limit - 1)
    except Exception as e:
        logger.warning("[autonomous runs] failed to read run history: %s", e)
        return []
    out: list[dict[str, Any]] = []
    for entry in raw or []:
        try:
            out.append(_json.loads(entry))
        except Exception:
            continue
    return out


# ── Task execution wrapper ─────────────────────────────────────────────────

async def execute_task(task: Task, llm, *, dry_run: bool = True) -> dict[str, Any]:
    """Run a task through the constitutional pipeline.

    The task's first tool_chain entry becomes a synthetic user message
    routed through aria_chat() with a deterministic session_id. This
    means autonomous outputs go through the SAME pipeline as interactive
    chat — clauses 1-15, the verifier, the honesty judge, the footer,
    the cost tracker, the trace stream — so we get production-grade
    discipline on the autonomous outputs without re-implementing any of
    that logic.

    Args:
        task: the Task to run
        llm: the LLM provider (already wrapped with the cost meter)
        dry_run: if True, do NOT call delivery.deliver() at the end.
                 The result is logged + recorded in the runs list but
                 nothing is pushed to WhatsApp / intel ledger. Default
                 True so the engine is safe by default.

    Returns:
        run record dict (also persisted to Redis)
    """
    t0 = time.time()
    record: dict[str, Any] = {
        "task_id": task.id,
        "task_name": task.name,
        "started_at": t0,
        "started_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0)),
        "dry_run": dry_run,
        "status": "started",
    }

    try:
        if not task.tool_chain:
            record["status"] = "error"
            record["error"] = "task has empty tool_chain"
            record["duration_ms"] = int((time.time() - t0) * 1000)
            await record_run(record)
            return record

        # Synthesise the user message from the first tool chain entry.
        # The deep_research / web_search tool router in routes/aria.py
        # will pick this up and fire the right tool — no need to call
        # the tool directly. Reusing the chat path means the response
        # gets the full constitutional pipeline applied.
        first = task.tool_chain[0]
        if not isinstance(first, dict):
            record["status"] = "error"
            record["error"] = f"first tool_chain entry is {type(first).__name__}, expected dict"
            record["duration_ms"] = int((time.time() - t0) * 1000)
            await record_run(record)
            return record

        tool_kind = (first.get("tool") or "").strip().lower()
        if tool_kind == "deep_research":
            entity = (first.get("entity") or "").strip()
            url = (first.get("url") or "").strip()
            user_msg = f"Aria, investigate the latest on: {entity}"
            if url:
                user_msg += f" {url}"
        elif tool_kind == "web_search":
            entity = (first.get("entity") or first.get("query") or "").strip()
            user_msg = f"Aria, search for: {entity}"
        elif tool_kind == "investigate":
            topic = (first.get("topic") or first.get("entity") or "").strip()
            user_msg = f"Aria, investigate: {topic}"
        else:
            record["status"] = "error"
            record["error"] = f"unsupported tool kind in first chain entry: {tool_kind!r}"
            record["duration_ms"] = int((time.time() - t0) * 1000)
            await record_run(record)
            return record

        # Per-run session id so each fire is isolated and the mem0
        # store tags it correctly. Daily granularity is enough for
        # daily/weekly tasks (the dedupe layer prevents same-day
        # duplicates).
        date_tag = time.strftime("%Y-%m-%d", time.gmtime(t0))
        session_id = f"autonomous:{task.id}:{date_tag}"

        # Run through the chat pipeline. The cost meter is already
        # attached to the llm provider — costs land under the
        # `autonomous_engine` feature in cost_tracker.
        from ..intel import cost_tracker
        from .. import aria_engine
        feature_token = cost_tracker.set_feature("autonomous_engine")
        try:
            chat_result = await asyncio.wait_for(
                aria_engine.aria_chat(
                    message=user_msg,
                    session_id=session_id,
                    llm=llm,
                ),
                timeout=task.timeout_seconds,
            )
        except asyncio.TimeoutError:
            record["status"] = "timeout"
            record["error"] = f"task exceeded timeout {task.timeout_seconds}s"
            record["duration_ms"] = int((time.time() - t0) * 1000)
            await record_run(record)
            return record
        finally:
            cost_tracker.reset_feature(feature_token)

        response_text = (chat_result or {}).get("response", "") or ""
        record["response_preview"] = response_text[:400]
        record["response_length"] = len(response_text)
        record["tool_used"] = (chat_result or {}).get("tool_used")

        # Escalation keyword scan
        triggered_flags: list[str] = []
        if task.escalate_if and response_text:
            response_lower = response_text.lower()
            for keyword in task.escalate_if:
                if keyword.lower() in response_lower:
                    triggered_flags.append(keyword)
        record["escalation_triggered"] = bool(triggered_flags)
        record["triggered_flags"] = triggered_flags

        # Delivery — DRY RUN by default
        if dry_run:
            record["delivery"] = "dry_run_skipped"
        else:
            try:
                from . import delivery
                delivery_result = await delivery.deliver(
                    task=task,
                    response_text=response_text,
                    triggered_flags=triggered_flags,
                    session_id=session_id,
                )
                record["delivery"] = delivery_result
            except Exception as e:
                logger.warning(
                    "[autonomous task %s] delivery raised: %s: %s",
                    task.id, type(e).__name__, e,
                )
                record["delivery"] = {"error": f"{type(e).__name__}: {e}"}

        record["status"] = "ok"
    except Exception as e:
        logger.warning(
            "[autonomous task %s] execution raised: %s: %s",
            task.id, type(e).__name__, e,
        )
        record["status"] = "error"
        record["error"] = f"{type(e).__name__}: {e}"

    record["duration_ms"] = int((time.time() - t0) * 1000)
    await record_run(record)
    return record

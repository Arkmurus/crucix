"""ARIA Core Self-Development Loop — drains the reassess queue.

Fires daily at 03:00 UTC (DAILY-CORE-DEVELOP). Reads the priority queue
written by ecosystem_reassess.run() and executes the top N items WITHIN
THE AUTO-ALLOWED BUCKET of aria_autonomy_doctrine.md:
  - Knowledge ingests (new sources, embeddings, RAG chunks)
  - Prompt refinements
  - New registry/sanctions adapter additions
  - Eval suite expansion
  - Capability manifest re-derivation

Hard lines (never auto):
  - Client-facing output
  - External send / post
  - Money spend beyond configured caps
  - Code changes flagged client-impact-possible

Every action is HMAC-audited via audit_log.record(self_improve_*).
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import json as _json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

from . import redis_store as rs

logger = logging.getLogger("aria.intel.core_develop")

_MAX_ACTIONS_PER_RUN = 3  # Hard cap — keeps the loop slow and auditable
_META_HISTORY_KEY = "aria:core:meta:history"

# Allowed-action whitelist for STAGED ROLLOUT. Per the operational audit
# 2026-04-15: do not flip the full action surface on at once. Start with
# source-gap scouts only (read-mostly, lowest blast radius), observe for
# 5 days, then widen. Passed as `allowed_actions` in the tool_chain entry;
# defaults to the conservative starter set.
_DEFAULT_ALLOWED_ACTIONS = ("source_gap",)

# Map item kinds → action family tags (the whitelist key)
_ACTION_FAMILY = {
    "critical_gap":   "source_gap",
    "high_gap":       "source_gap",
    "medium_gap":     "source_gap",
    "source_stale":   "source_refresh",
    "mastery_drift":  "reading_session",
    "mistake_pattern": "prompt_evolution",
    "capability_gap": "eng_ticket",
    "learning_suggestion": "source_gap",  # piggy-back on source_gap family
}


async def run(
    max_actions: int = _MAX_ACTIONS_PER_RUN,
    allowed_actions: tuple[str, ...] | list[str] | None = None,
) -> dict:
    """Drain the top N items from the reassess queue and act on each one.

    `allowed_actions` is the staged-rollout gate. Only items whose action
    family is in the whitelist will be acted on; others are SKIPPED with
    reason=blocked_by_rollout so the operator can see what's queued but
    not yet authorised. Starter set is ("source_gap",) — source-scout
    targeted runs only.
    """
    from . import ecosystem_reassess
    allowed = set(allowed_actions or _DEFAULT_ALLOWED_ACTIONS)
    queue = await ecosystem_reassess.get_queue(limit=max_actions * 5)

    # Also pull learning suggestions extracted from ARIA's own responses
    try:
        raw_suggestions = await rs.lrange("aria:core:learning_suggestions", 0, 4)
        for entry in (raw_suggestions or []):
            try:
                item = _json.loads(entry)
                if item.get("status") == "pending":
                    queue.append({
                        "kind": "learning_suggestion",
                        "key": f"ls:{item.get('queued_at', '')}",
                        "urgency": 35,  # Medium-low — let real gaps take priority
                        "payload": {"detail": item["text"], "source": item.get("source", "self")},
                    })
            except Exception:
                continue
    except Exception:
        pass

    results: list[dict] = []
    acted = 0
    skipped_by_rollout = 0
    for item in queue:
        if acted >= max_actions:
            break
        family = _ACTION_FAMILY.get(item.get("kind", ""), "unknown")
        if family not in allowed:
            skipped_by_rollout += 1
            results.append({
                "key": item.get("key"), "acted": False,
                "reason": "blocked_by_rollout",
                "action_family": family,
                "allowed_families": sorted(allowed),
            })
            continue
        try:
            action_result = await _act_on(item)
            results.append(action_result)
            if action_result.get("acted"):
                acted += 1
        except Exception as e:
            logger.warning("core_develop action failed for %s: %s", item.get("key"), e)
            results.append({"key": item.get("key"), "acted": False, "error": str(e)})

    now = datetime.now(timezone.utc).isoformat()
    summary = {
        "at": now, "acted": acted,
        "scanned": len(queue[:max_actions * 5]),
        "skipped_by_rollout": skipped_by_rollout,
        "allowed_families": sorted(allowed),
        "results": results,
    }
    # Preserve a short history for the weekly meta-review.
    history = await rs.get_json(_META_HISTORY_KEY) or []
    history.append(summary)
    if len(history) > 60:
        history = history[-60:]
    await rs.set_json(_META_HISTORY_KEY, history, ex=90 * 86400)

    # Brain signal — the self-development cycle is a core mastery signal:
    # if she acts, the predictor and self_assess briefing should see it.
    try:
        from . import brain_hook as _bh
        await _bh.absorb(
            module="core_develop",
            summary=f"Self-develop cycle: {acted}/{len(queue[:max_actions * 3])} actions taken",
            detail=f"Kinds: {dict((i['kind'], 1) for i in queue[:max_actions * 3])}",
            success=acted > 0,
        )
    except Exception:
        pass
    # Self_metrics: record utility signal so daily briefing reflects
    # autonomy throughput.
    try:
        from . import self_metrics as _sm
        await _sm.emit(
            axis="utility",
            domain="self_development",
            signal=f"core_develop_actions_{acted}",
            value=min(1.0, acted / max(max_actions, 1)),
            source_module="core_develop",
        )
    except Exception:
        pass
    # R-F1001 - wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="core_develop",
        summary="Run",
        source_id="core_develop:R-F1001",
    )

    return summary


async def _act_on(item: dict) -> dict:
    """Dispatch on item.kind to the right auto-allowed action."""
    kind = item.get("kind", "")
    payload = item.get("payload", {})
    key = item.get("key", "")

    if kind in ("critical_gap", "high_gap", "medium_gap"):
        # Source gap — trigger a scout run for that (region × topic)
        return await _act_source_gap(payload, key)

    if kind == "source_stale":
        # Re-scout / re-fetch — the scout module handles the detail.
        return await _act_source_stale(payload, key)

    if kind == "mastery_drift":
        # Topic mastery dropping — queue a reading session via proactive.
        return await _act_mastery_drift(payload, key)

    if kind == "mistake_pattern":
        # Recurring correction — stage a prompt-refinement candidate.
        return await _act_mistake_pattern(payload, key)

    if kind == "capability_gap":
        # Missing file parser, missing API — log an engineering ticket.
        return await _act_capability_gap(payload, key)

    if kind == "learning_suggestion":
        return await _act_learning_suggestion(payload, key)

    return {"key": key, "acted": False, "reason": f"no handler for kind={kind}"}


async def _act_source_gap(payload: dict, key: str) -> dict:
    """Trigger a source-scout run targeting the missing (region × topic) cell."""
    try:
        from . import source_scout
        region = payload.get("region", "global")
        topic = payload.get("topic", "")
        scout_result = await source_scout.run(
            pattern="targeted", region=region, topic=topic, max_finds=3,
        )
        return {
            "key": key, "acted": True, "action": "source_scout_targeted",
            "region": region, "topic": topic, "scout": scout_result,
        }
    except Exception as e:
        return {"key": key, "acted": False, "error": str(e)}


async def _act_source_stale(payload: dict, key: str) -> dict:
    """Re-fetch a stale source to update last_fetch + reliability."""
    try:
        from . import source_scout
        family = payload.get("family", "")
        scout_result = await source_scout.refresh_family(family)
        return {"key": key, "acted": True, "action": "source_refresh",
                "family": family, "result": scout_result}
    except Exception as e:
        return {"key": key, "acted": False, "error": str(e)}


async def _act_mastery_drift(payload: dict, key: str) -> dict:
    """When a topic's mastery drops, enqueue a reading session."""
    try:
        from . import proactive
        topic = payload.get("topic", "")
        if hasattr(proactive, "queue_reading_session"):
            await proactive.queue_reading_session(topic=topic)
            return {"key": key, "acted": True, "action": "reading_session_queued",
                    "topic": topic}
        return {"key": key, "acted": False, "reason": "proactive has no queue_reading_session"}
    except Exception as e:
        return {"key": key, "acted": False, "error": str(e)}


async def _act_mistake_pattern(payload: dict, key: str) -> dict:
    """Recurring correction pattern — stage a candidate prompt addendum
    via self_improve (goes through schema validator + audit log)."""
    try:
        topic = payload.get("topic", "")
        count = payload.get("count", 0)
        # Read the current v3_prompts.py
        from . import self_improve
        read = await self_improve.read_own_code("aria_service/intel/v3_prompts.py")
        if read.get("error"):
            return {"key": key, "acted": False, "error": read["error"]}
        addition = (
            f"\n# Auto-staged {datetime.now(timezone.utc).isoformat()} — "
            f"recurring correction on topic '{topic}' (×{count})\n"
            f"# Added by core_develop. Human review before deploy.\n"
        )
        new_content = read["content"] + addition
        staged = await self_improve.stage_improvement(
            file_path="aria_service/intel/v3_prompts.py",
            new_content=new_content,
            change_type="prompt_evolution",
            description=f"Recurring correction on '{topic}' — draft addendum",
            reasoning=f"{count} corrections in the recent mistake ledger",
        )
        return {"key": key, "acted": staged.get("staged", False),
                "action": "prompt_evolution_staged", "topic": topic,
                "staging": staged}
    except Exception as e:
        return {"key": key, "acted": False, "error": str(e)}


async def _act_capability_gap(payload: dict, key: str) -> dict:
    """Missing file parser / API / endpoint — log a detailed ticket for
    the human engineering queue. No autonomous code-write on core files.

    Routes through tickets.raise_ticket() which writes to GitHub Issues.
    The previous lpush to the
    `aria:engineering:tickets` Redis list went nowhere -- nothing reads
    that key, and Clause 22 mandates real ticket IDs from raise_ticket().
    Capability-gap tickets had been silently lost since core_develop
    shipped."""
    try:
        from . import tickets as _tk
        title = f"capability gap: {(payload.get('detail') or payload.get('what') or 'unspecified')[:80]}"
        symptom = (payload.get("detail") or payload.get("what") or str(payload))[:1000]
        context = (
            f"Detected by core_develop reassessment.\n"
            f"Payload key: {key}\n"
            f"Full payload (truncated): {str(payload)[:500]}"
        )
        result = await _tk.raise_ticket(
            title=title,
            symptom=symptom,
            context=context,
            severity="MEDIUM",
            category="capability_gap",
            source="core_develop",
        )
        return {"key": key, "acted": result.get("ok", False),
                "action": "eng_ticket_logged",
                "ticket_id": result.get("ticket_id"),
                "ticket": result}
    except Exception as e:
        return {"key": key, "acted": False, "error": str(e)}


async def _act_learning_suggestion(payload: dict, key: str) -> dict:
    """Learning suggestion from ARIA's own response — feed into source scout
    as a broad discovery hint, or log as an engineering ticket if it looks
    like a capability request."""
    detail = payload.get("detail", "")
    if not detail:
        return {"key": key, "acted": False, "reason": "empty detail"}
    try:
        # Attempt a targeted source scout — the suggestion often names a
        # data source or region that the scout can try to find.
        from . import source_scout
        scout_result = await source_scout.run(
            pattern="targeted", region="global", topic=detail[:80], max_finds=2,
        )
        return {
            "key": key, "acted": True, "action": "learning_suggestion_scouted",
            "detail": detail, "scout": scout_result,
        }
    except Exception as e:
        # Fall back to a real ticket via tickets.raise_ticket() — the
        # previous Redis lpush to aria:engineering:tickets had no reader,
        # so learning suggestions that the source scout couldn't satisfy
        # were silently dropped on the floor.
        try:
            from . import tickets as _tk
            result = await _tk.raise_ticket(
                title=f"learning suggestion: {detail[:80]}",
                symptom=detail[:1000],
                context=(
                    f"Source scout failed to satisfy this suggestion.\n"
                    f"Originally surfaced by: {payload.get('source', 'self')}\n"
                    f"Scout error: {str(e)[:500]}"
                ),
                severity="LOW",
                category="capability_gap",
                source="core_develop:learning_suggestion",
            )
            return {"key": key, "acted": result.get("ok", False),
                    "action": "learning_suggestion_ticketed",
                    "detail": detail,
                    "ticket_id": result.get("ticket_id")}
        except Exception:
            return {"key": key, "acted": False, "error": str(e)}


# ── Learning suggestion extraction from ARIA responses ─────────────────

_SUGGESTION_PATTERNS = [
    # "we should/could/must [verb]" patterns
    re.compile(r"\b(?:we\s+)?(?:should|could|must|need\s+to|recommend)\s+(?:also\s+)?(\w+(?:\s+\w+){2,15})", re.I),
    # "Action: [text]" or "Action item: [text]"
    re.compile(r"\bAction(?:\s+item)?:\s*(.{10,120})", re.I),
    # "add [X] to [autonomous/sweep/monitor/atlas]"
    re.compile(r"\badd\s+(.{5,80}?)\s+to\s+(?:the\s+)?(?:autonomous|sweep|monitor|atlas|knowledge|watchlist|registry)", re.I),
    # "integrate [X]"
    re.compile(r"\bintegrate\s+(.{5,80}?)(?:\.|$|\n)", re.I),
    # "create a [task/module/endpoint]"
    re.compile(r"\bcreate\s+(?:a\s+)?(?:new\s+)?(?:scheduled\s+)?(?:task|module|endpoint|monitor)\s+(?:that|to|for)\s+(.{5,100})", re.I),
    # "Next Step: [text]"
    re.compile(r"\bNext\s+Step:\s*(.{10,150})", re.I),
]

# Filter: don't queue suggestions that are just general advice
_SUGGESTION_STOPWORDS = [
    "contact", "reach out", "speak to", "email", "call",  # human actions
    "commission", "hire", "subscribe", "purchase", "buy",  # spend money
    "within 48 hours", "within 24 hours",  # time-bound promises (not actionable by code)
]

_LEARNING_QUEUE_KEY = "aria:core:learning_suggestions"


async def extract_learning_suggestions(response_text: str, session_id: str = "") -> list[dict]:
    """Scan an ARIA response for self-improvement suggestions and queue them.

    Returns list of extracted suggestions with their queue status.
    """
    if not response_text or len(response_text) < 100:
        return []

    suggestions: list[dict] = []
    seen: set[str] = set()

    for pattern in _SUGGESTION_PATTERNS:
        for match in pattern.finditer(response_text):
            text = match.group(1).strip().rstrip(".")
            if len(text) < 10 or len(text) > 200:
                continue
            # Dedup
            key = text.lower()[:50]
            if key in seen:
                continue
            seen.add(key)
            # Filter human-only actions
            if any(stop in text.lower() for stop in _SUGGESTION_STOPWORDS):
                continue
            suggestions.append({
                "text": text,
                "source": f"self_suggestion:{session_id}" if session_id else "self_suggestion",
                "pattern": pattern.pattern[:40],
            })

    if not suggestions:
        return []

    # Queue as items in the core_develop pipeline
    queued: list[dict] = []

    for s in suggestions[:5]:  # Cap at 5 per response
        item = {
            "kind": "learning_suggestion",
            "text": s["text"],
            "source": s["source"],
            "queued_at": time.time(),
            "status": "pending",
        }
        try:
            await rs.lpush(_LEARNING_QUEUE_KEY, _json.dumps(item))
            await rs.ltrim(_LEARNING_QUEUE_KEY, 0, 99)  # Keep max 100
            queued.append(item)
        except Exception:
            pass

    if queued:
        logger.info("[core_develop] Extracted %d learning suggestions from response", len(queued))

    return queued


# ── Weekly meta-review ───────────────────────────────────────────────────

async def meta_review() -> dict:
    """Weekly Sunday 04:00 UTC — diff the capability manifest week-over-week,
    summarise what ARIA changed in the last 7 days, seed next week's queue."""
    history = await rs.get_json(_META_HISTORY_KEY) or []
    # Last 7 days of runs
    recent = history[-7:] if history else []
    total_acted = sum(r.get("acted", 0) for r in recent)
    actions_by_kind: dict[str, int] = {}
    for r in recent:
        for res in r.get("results", []):
            a = res.get("action", "")
            if a:
                actions_by_kind[a] = actions_by_kind.get(a, 0) + 1

    # Atlas stats
    atlas_stats = {}
    try:
        from . import web_atlas
        atlas_stats = await web_atlas.stats()
    except Exception:
        pass

    # Source-registry health — NEW this commit. Suspends failing sources
    # and produces a weekly top/degraded/failing breakdown. Auto-allowed
    # per doctrine ("never silently trust a failing source").
    registry_health: dict[str, Any] = {}
    suspension_result: dict[str, Any] = {}
    try:
        from . import source_validator
        suspension_result = await source_validator.suspend_failing_sources(
            threshold=0.40,
        )
        registry_health = await source_validator.registry_health_report()
    except Exception as e:
        logger.debug("registry health pass failed: %s", e)

    # Coverage-domain gap report — domain-level (not just cell-level)
    coverage_by_domain = []
    try:
        from . import source_validator
        coverage_by_domain = await source_validator.coverage_gaps_by_domain()
    except Exception as e:
        logger.debug("coverage-by-domain pass failed: %s", e)

    # Capability manifest diff — best-effort; module may not exist.
    manifest_diff = {}
    try:
        from . import capability_manifest
        if hasattr(capability_manifest, "weekly_diff"):
            manifest_diff = await capability_manifest.weekly_diff()
    except Exception:
        pass

    summary = {
        "week_ending": datetime.now(timezone.utc).isoformat(),
        "runs_in_window": len(recent),
        "total_actions_taken": total_acted,
        "actions_by_kind": actions_by_kind,
        "atlas": atlas_stats,
        "capability_diff": manifest_diff,
        "registry_health": registry_health,
        "auto_suspended_this_pass": suspension_result,
        "coverage_gaps_by_domain": coverage_by_domain,
    }

    # Brain signal for the weekly meta review — this is what lets the
    # daily 05:45 briefing say "last week: X actions, Y suspensions,
    # Z new sources added, W coverage gaps remaining".
    try:
        from . import brain_hook as _bh
        await _bh.absorb(
            module="core_develop",
            summary=(f"Weekly meta: {total_acted} actions, "
                     f"{suspension_result.get('suspended', 0)} suspended, "
                     f"{len(coverage_by_domain)} coverage gaps"),
            detail=(f"Healthy sources: {registry_health.get('healthy_count', 0)}; "
                    f"degraded: {registry_health.get('degraded_count', 0)}; "
                    f"failing: {registry_health.get('failing_count', 0)}"),
            success=True,
        )
    except Exception:
        pass

    # Snapshot the Web Atlas to its YAML mirror so self_improve can
    # touch it through the whitelisted path if needed next week.
    try:
        from . import web_atlas
        await web_atlas.snapshot_to_yaml()
    except Exception as e:
        logger.debug("atlas snapshot skipped: %s", e)

    return summary

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.

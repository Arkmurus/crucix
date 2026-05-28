"""Log-only output-guard pass for the streaming chat path.

The five output guards (officeholder / commitment / tool_claim /
propaganda / ground_truth) run only inside /chat at
routes/aria.py:5141-5432. They rewrite `response_text` to enforce
Clauses 13/17/20 + tool-claim fabrication + officeholder demotion.

Because /chat/stream is the default WhatsApp path, these have been
skipped on production traffic for ~6 months. A full rewrite-over-SSE
fix (option b in memory/stream_bypass_pattern.md) needs client-side
work. This module ships option (a): run all five guards against the
final streamed response_text in OBSERVATION mode — record every
violation to Redis, don't rewrite the already-shown reply.

Gives a 24h baseline of real stream-side violation counts so the
rewrite-UX work can be scoped against real numbers, not guesses.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

logger = logging.getLogger("aria.stream_guard_observer")

_K_LOG = "crucix:stream_guard_violations:log"
_K_CTR_PREFIX = "crucix:stream_guard_violations:counter_24h:"
_MAX_LOG_ENTRIES = 10_000
_CTR_TTL_SECONDS = 25 * 3600


async def observe(
    *,
    session_id: str,
    user_message: str,
    response_text: str,
    tool_context: str | None = None,
) -> dict[str, Any]:
    """Run all five guards read-only. Returns a summary; does NOT
    modify `response_text`. Safe to call from a background task."""
    from . import redis_store as rs

    if not response_text:
        return {"violations": 0, "by_guard": {}, "skipped": "empty_response"}

    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    by_guard: dict[str, int] = {}
    details: list[dict[str, Any]] = []

    # Coarse tool_used inference for tool_claim_guard. The streaming
    # path does not thread the real `tool_used` into aria_chat_stream;
    # we detect presence of the tool-context marker that /chat/stream
    # inserts into `message_for_llm` before the LLM call.
    tool_used = "unknown" if (user_message and "[I have already run" in user_message) else None

    # 1. officeholder_guard — sync, returns (rewritten, demotions_list)
    try:
        from . import officeholder_guard
        _rew, demotions = officeholder_guard.review_response(response_text, None)
        if demotions:
            by_guard["officeholder"] = len(demotions)
            details.append({
                "guard": "officeholder",
                "count": len(demotions),
            })
    except Exception as e:
        logger.debug("[stream_observe] officeholder_guard failed: %s", e)

    # 2. commitment_guard — sync, Clause 20
    try:
        from . import commitment_guard as _cg
        r = _cg.guard_commitments(response_text)
        if r.get("changed"):
            n = int(r.get("violations_found", 0) or 0)
            if n > 0:
                by_guard["commitment"] = n
                details.append({
                    "guard": "commitment",
                    "clause": "20",
                    "count": n,
                    "types": [v.get("pattern_type") for v in (r.get("violations") or [])[:3]],
                })
    except Exception as e:
        logger.debug("[stream_observe] commitment_guard failed: %s", e)

    # 3. tool_claim_guard — async
    try:
        from . import tool_claim_guard as _tcg
        r = await _tcg.guard(
            response_text,
            tool_used=tool_used,
            user_message=user_message,
            user_id="",
            chat_id="",
        )
        if r.get("changed"):
            n = int(r.get("violations_found", 0) or 0)
            if n > 0:
                by_guard["tool_claim"] = n
                details.append({
                    "guard": "tool_claim",
                    "count": n,
                    "types": [v.get("pattern_type") for v in (r.get("violations") or [])[:3]],
                })
    except Exception as e:
        logger.debug("[stream_observe] tool_claim_guard failed: %s", e)

    # 4. propaganda_guard — sync, Clause 13
    try:
        from . import propaganda_guard as _pg
        r = _pg.guard(response_text)
        if not r.get("unchanged"):
            n_uncited = int(r.get("current_uncited", 0) or 0)
            n_down = int(r.get("propaganda_downgrades", 0) or 0)
            total = n_uncited + n_down
            if total > 0:
                by_guard["propaganda"] = total
                details.append({
                    "guard": "propaganda",
                    "clause": "13",
                    "current_uncited": n_uncited,
                    "propaganda_downgrades": n_down,
                })
    except Exception as e:
        logger.debug("[stream_observe] propaganda_guard failed: %s", e)

    # 5. ground_truth_guard — async, Clause 17
    try:
        from . import ground_truth_guard as _gtg
        r = await _gtg.verify(
            response_text,
            tool_context=tool_context or "",
            user_message=user_message,
        )
        if r.get("changed"):
            n = int(r.get("violations_found", 0) or 0)
            if n > 0:
                by_guard["ground_truth"] = n
                details.append({
                    "guard": "ground_truth",
                    "clause": "17",
                    "count": n,
                    "slots": [v.get("slot") for v in (r.get("violations") or [])[:3]],
                })
    except Exception as e:
        logger.debug("[stream_observe] ground_truth_guard failed: %s", e)

    total = sum(by_guard.values())
    summary = {
        "timestamp": timestamp,
        "session_id": session_id or "",
        "response_length": len(response_text or ""),
        "total_violations": total,
        "by_guard": by_guard,
        "details": details,
    }

    # Always increment the "turns_observed" counter so we can compute a
    # violation RATE, not just an absolute count. 0-violation turns are
    # the useful denominator. Set TTL only on first incr — resetting
    # expire() per incr turned this into a lifetime counter, breaking
    # the "rolling window" semantics get_stats() advertises.
    try:
        _to_new = await rs.incr(_K_CTR_PREFIX + "turns_observed")
        if _to_new == 1:
            await rs.expire(_K_CTR_PREFIX + "turns_observed", _CTR_TTL_SECONDS)
    except Exception as e:
        logger.debug("[stream_observe] denominator counter failed: %s", e)

    if total == 0:
        return summary

    # Persist violation entry + rolling per-guard counters
    try:
        await rs.lpush(_K_LOG, json.dumps(summary, default=str))
        await rs.ltrim(_K_LOG, 0, _MAX_LOG_ENTRIES - 1)
        for guard_name, count in by_guard.items():
            key = _K_CTR_PREFIX + guard_name
            _new = await rs.incr(key, amount=count)
            if _new == count:  # first write into a fresh key
                await rs.expire(key, _CTR_TTL_SECONDS)
    except Exception as e:
        logger.debug("[stream_observe] persist failed: %s", e)

    logger.warning(
        "[stream_guard_observer] session=%s violations=%d by_guard=%s",
        session_id or "?", total, by_guard,
    )
    return summary


async def get_stats() -> dict[str, Any]:
    """Aggregate stats for the /stream-guards/stats endpoint + dashboard."""
    from . import redis_store as rs

    total_entries = 0
    counters_24h: dict[str, int] = {}
    turns_observed_24h = 0
    recent: list[dict] = []

    try:
        total_entries = await rs.llen(_K_LOG)
    except Exception:
        pass

    for guard in ("officeholder", "commitment", "tool_claim",
                  "propaganda", "ground_truth"):
        try:
            raw = await rs.get(_K_CTR_PREFIX + guard)
            counters_24h[guard] = int(raw) if raw else 0
        except Exception:
            counters_24h[guard] = 0

    try:
        raw = await rs.get(_K_CTR_PREFIX + "turns_observed")
        turns_observed_24h = int(raw) if raw else 0
    except Exception:
        pass

    try:
        raw_list = await rs.lrange(_K_LOG, 0, 19)
        for r in raw_list or []:
            try:
                recent.append(json.loads(r))
            except Exception:
                continue
    except Exception:
        pass

    total_24h = sum(counters_24h.values())
    rate = (total_24h / turns_observed_24h) if turns_observed_24h else None

    # R-F996 — wire to brain
    from .engine_wiring import wire_success
    wire_success(
        module="stream_guard_observer",
        summary="Get Stats",
        source_id="stream_guard_observer:R-F996",
    )

    return {
        "total_violations_logged_lifetime": total_entries,
        "turns_observed_24h": turns_observed_24h,
        "violations_24h_by_guard": counters_24h,
        "violations_24h_total": total_24h,
        "violation_rate_24h": round(rate, 4) if rate is not None else None,
        "recent": recent,
        "mode": "OBSERVE_ONLY — no rewrites applied to streamed response",
    }

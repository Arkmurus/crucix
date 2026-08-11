"""search_engine_health — ARIA measures whether her own search sources are lying.

R-F3865 (2026-08-11). §25 proprioception applied to search.

WHY THIS EXISTS. For 52 days SearXNG served query-independent junk and every
consumer saw `ok: True`. It was caught by a human running a bake-off by hand —
token-overlap scoring across 18 engines — and that instinct should not live in a
chat session. The engine list rots continuously: R-F1659's "datacenter-tolerant"
set was blocked two months later, R-F3849's replacement was blocked the same week,
and `yep` answered 20/20 and then began returning 403 within the HOUR. Any design
whose correctness depends on a hand-maintained engine list is already failing; the
only question is whether anyone has noticed yet.

So the list stops being maintained by hand and starts maintaining itself: score
every engine on live traffic, quarantine the ones that stop answering the question,
tell the brain, and let them back in when they recover.

WHAT IS MEASURED, and what deliberately is NOT. The signal is the R-F3844
discriminator — did this engine's contribution bear ANY lexical relation to the
query — accumulated per engine. It is not a quality score, and it must never become
one: R-F3844's docstring warns that a search gate which editorialises will
eventually suppress real intelligence, which is worse than the noise it removes.
"Answered a different question" is objective and cheap; "answered badly" is a
judgement this has no business making.

THREE PROPERTIES THAT KEEP IT HONEST:

  1. A MINIMUM SAMPLE. One unrelated result set proves nothing — a genuinely
     obscure query legitimately returns nothing related, and quarantining on that
     would punish an engine for the caller's query. Nothing is judged below
     `_MIN_SAMPLE` observations.

  2. QUARANTINE EXPIRES. Every block is a TTL'd hypothesis, never a death
     sentence. Blocked IPs get unblocked, rate limits reset, and `yep` may well be
     fine under ordinary load — a permanent ban would make this module the next
     stale hand-maintained list, which is the thing it exists to replace.

  3. IT FAILS OPEN. If the store cannot be read, `is_quarantined` returns False.
     A health system that blinds ARIA's search when its own bookkeeping breaks is
     worse than no health system, and "could not measure" is never "measured and
     failed" (§22).

The quarantine is advisory to the SEARCH path only. It never touches DD, which
runs Brave-only and Anthropic-pinned (R-F3847/R-F3034) and does not consult this.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from . import redis_store as rs
from .wire import fail_wire

logger = logging.getLogger("aria.search_engine_health")

#: Rolling per-engine counters. No TTL on the stats themselves — §7, ARIA does not
#: forget; the WINDOW is enforced by decay, not by eviction.
_KEY_PREFIX = "crucix:aria:search:engine_health:"
#: Quarantine markers DO expire — see property 2 above.
_QUARANTINE_PREFIX = "crucix:aria:search:engine_quarantine:"

#: Below this many observations an engine is not judged at all (property 1).
_MIN_SAMPLE = 12
#: Fraction of observations that must be query-independent before quarantine.
#: 0.8 is deliberately high: bing measured 0/10 related on niche queries and 9/10
#: on popular ones, so a mixed-traffic engine sits far below this and is left alone.
_QUARANTINE_RATIO = 0.8
#: How long a quarantined engine stays out before it is re-admitted and re-measured.
_QUARANTINE_TTL_S = 3600
#: Decay: counters are halved once they exceed this, so an engine that recovers is
#: not held hostage by ancient failures and one that rots is caught quickly.
_DECAY_AT = 200


def _key(engine: str) -> str:
    return f"{_KEY_PREFIX}{(engine or 'unknown').strip().lower()}"


def _qkey(engine: str) -> str:
    return f"{_QUARANTINE_PREFIX}{(engine or 'unknown').strip().lower()}"


@fail_wire(module="search_engine_health", gap_type="engine_failure")
async def record_observation(engine: str, *, query_independent: bool) -> dict[str, Any]:
    """Record one engine's outcome for one query. Never raises, never blocks search."""
    engine = (engine or "unknown").strip().lower()
    try:
        raw = await rs.get_json(_key(engine))
    except Exception:
        raw = None
    stats = raw if isinstance(raw, dict) else {}
    total = int(stats.get("total") or 0) + 1
    bad = int(stats.get("independent") or 0) + (1 if query_independent else 0)

    # Halve rather than reset, so the recent past still counts (property 2's spirit:
    # a recovering engine must be able to climb back out).
    if total > _DECAY_AT:
        total, bad = total // 2, bad // 2

    stats = {
        "engine": engine,
        "total": total,
        "independent": bad,
        "ratio": round(bad / total, 3) if total else 0.0,
        "last_seen": time.time(),
    }
    try:
        await rs.set_json(_key(engine), stats)
    except Exception:
        logger.debug("[R-F3865] could not persist health for %s", engine, exc_info=True)

    if _should_quarantine(stats):
        await _quarantine(engine, stats)
    return stats


def _should_quarantine(stats: dict) -> bool:
    """Pure predicate — the whole judgement, in one testable place."""
    total = int(stats.get("total") or 0)
    if total < _MIN_SAMPLE:
        return False                      # property 1: not enough evidence
    return (int(stats.get("independent") or 0) / total) >= _QUARANTINE_RATIO


async def _quarantine(engine: str, stats: dict) -> None:
    """Mark an engine untrusted for a bounded period, and TELL THE BRAIN."""
    try:
        already = await rs.get(_qkey(engine))
    except Exception:
        already = None
    try:
        await rs.set(_qkey(engine), str(time.time()), ex=_QUARANTINE_TTL_S)
    except Exception:
        logger.debug("[R-F3865] could not persist quarantine for %s", engine, exc_info=True)
        return
    if already:
        return                            # already out; do not re-alert every query
    logger.warning(
        "[R-F3865] search engine %r QUARANTINED for %ds — %d/%d result sets bore no "
        "relation to their query", engine, _QUARANTINE_TTL_S,
        stats.get("independent"), stats.get("total"),
    )
    # §21a — a source that stopped answering is exactly what ran unnoticed for 52
    # days. This is the signal that makes the engine list self-maintaining.
    try:
        from .engine_wiring import wire_failure
        wire_failure(
            module="search_engine_health",
            detail=(f"engine {engine!r} quarantined for {_QUARANTINE_TTL_S}s: "
                    f"{stats.get('independent')}/{stats.get('total')} result sets were "
                    f"query-independent (ratio {stats.get('ratio')})"),
            gap_type="search_backend_failure",
            source="search_engine_health:_quarantine",
        )
    except Exception:      # pragma: no cover - observability never blocks search
        pass


@fail_wire(module="search_engine_health", gap_type="engine_failure")
async def is_quarantined(engine: str) -> bool:
    """True while this engine is serving out a quarantine.

    FAILS OPEN (property 3): an unreadable store returns False. Blinding search
    because the health bookkeeping broke would be a worse outcome than the noise
    this module exists to catch.
    """
    try:
        return bool(await rs.get(_qkey(engine)))
    except Exception:
        return False


@fail_wire(module="search_engine_health", gap_type="engine_failure")
async def health_report(engines: list[str] | None = None) -> dict[str, Any]:
    """Queryable proprioception surface (§25.3): what does ARIA believe about each
    of her search sources right now?"""
    names = engines or ["yep", "bing", "wikipedia", "wikidata", "brave", "searxng"]
    out: dict[str, Any] = {}
    degraded: list[str] = []
    for name in names:
        try:
            stats = await rs.get_json(_key(name))
        except Exception:
            stats = None
        try:
            q = bool(await rs.get(_qkey(name)))
        except Exception:
            q = False
        if not isinstance(stats, dict) and not q:
            continue
        entry = dict(stats or {})
        entry["quarantined"] = q
        entry["judged"] = int(entry.get("total") or 0) >= _MIN_SAMPLE
        out[name] = entry
        if q:
            degraded.append(name)
    return {
        "engines": out,
        "quarantined": sorted(degraded),
        "min_sample": _MIN_SAMPLE,
        "quarantine_ratio": _QUARANTINE_RATIO,
        "quarantine_ttl_s": _QUARANTINE_TTL_S,
    }

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

────────────────────────────────────────────────────────────────────────────────
R-F3873 (2026-08-11) — THIS MODULE WAS BLIND TO THE EXACT ROT IT EXISTS TO CATCH.

Measured live, aria-intel and aria-searxng in the same second:

    SearXNG  → unresponsive_engines: [["google cse", "Suspended: too many requests"],
                                      ["yep",        "Suspended: access denied"]]
    /api/aria/search/health → yep: {total: 81, ratio: 0.025, quarantined: false}

`yep` was reported as the HEALTHIEST engine on the board while it was access-denied
and serving nothing. The cause is structural: `record_observation` is driven by the
engines that appear in RESULT ROWS. An engine that is 403'd, CAPTCHA'd or timed out
contributes no rows, so it accrues no observations — its `total` freezes at its last
good value and its ratio stays excellent forever. **A source that stopped answering
is indistinguishable from one that was never asked**, which is the absence-reads-as-
health shape of the §1 gates, the §17 cost probe, and C-23's infobox zero.

That inverts the module's purpose, because §27d makes this surface BINDING: "if a
search source looks dead, do not edit the engine list from intuition, read
engine_relevance". The surface future sessions are told to trust could not show a
dead engine.

SearXNG publishes `unresponsive_engines` — with a reason — on EVERY response, and a
repo-wide grep found no consumer at all. So this now measures TWO axes:

    LYING     (query_independent) → quarantine, because the junk must be filtered.
    BLOCKED   (unresponsive)      → report + escalate, and deliberately NOT
                                    quarantine.

They need OPPOSITE responses and must never be merged. Quarantining a blocked engine
is theatre — it already returns nothing — and actively harmful, because it would keep
the engine out for an hour after the block lifts. Nor can a block be "fixed" in code:
§27 records that a datacenter IP cannot be un-blocked by tuning, and that residential
proxies are not an option we will take. The correct response to a block is to SEE it.

`last_event` is a single last-writer-wins key rather than a comparison of two
timestamps, so "is it serving?" never depends on clock resolution.
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

#: R-F3873 — the BLOCKED axis. One key per engine, last-writer-wins, holding which
#: kind of event happened MOST RECENTLY. Deliberately not two timestamps compared
#: after the fact: that would make "is it serving?" depend on clock resolution.
_STATE_PREFIX = "crucix:aria:search:engine_state:"
#: Self-populating engine roster. This module exists because a hand-maintained
#: engine list rots; its own report must not depend on one.
_REGISTRY_KEY = "crucix:aria:search:engine_registry"
#: One brain signal per engine per hour — a blocked engine appears on EVERY query
#: and an alert that fires hundreds of times is an alert that gets muted.
_BLOCK_ALERT_PREFIX = "crucix:aria:search:engine_block_alerted:"
_BLOCK_ALERT_TTL_S = 3600


def _key(engine: str) -> str:
    return f"{_KEY_PREFIX}{(engine or 'unknown').strip().lower()}"


def _qkey(engine: str) -> str:
    return f"{_QUARANTINE_PREFIX}{(engine or 'unknown').strip().lower()}"


def _skey(engine: str) -> str:
    return f"{_STATE_PREFIX}{(engine or 'unknown').strip().lower()}"


def _norm(engine: str) -> str:
    return (engine or "unknown").strip().lower()


#: Engines this process has already written to the roster. Purely an I/O saver:
#: registration is idempotent, so a cold cache costs one redundant write, never a
#: wrong answer. Without it every observation of every engine on every query would
#: add a store read to a hot path for a fact that changes about twice a month.
_registered: set[str] = set()


async def _register(engine: str) -> None:
    """Add an engine to the self-populating roster. Best-effort by design: a name
    missing from the roster costs visibility, never correctness."""
    if engine in _registered:
        return
    try:
        raw = await rs.get_json(_REGISTRY_KEY)
        names = set(raw) if isinstance(raw, list) else set()
        if engine not in names:
            names.add(engine)
            await rs.set_json(_REGISTRY_KEY, sorted(names))
        _registered.add(engine)
    except Exception:
        logger.debug("[R-F3873] could not register engine %s", engine, exc_info=True)


async def _set_last_event(engine: str, event: str, reason: str = "") -> None:
    """Record what this engine did MOST RECENTLY: served, or refused."""
    try:
        prior = await rs.get_json(_skey(engine))
    except Exception:
        prior = None
    prior = prior if isinstance(prior, dict) else {}
    blocks = int(prior.get("unresponsive_count") or 0)
    if event == "unresponsive":
        blocks += 1
        if blocks > _DECAY_AT:
            blocks //= 2
    state = {
        "engine": engine,
        "last_event": event,
        "at": time.time(),
        "unresponsive_count": blocks,
        "last_unresponsive_reason": (reason or prior.get("last_unresponsive_reason") or ""),
    }
    try:
        await rs.set_json(_skey(engine), state)
    except Exception:
        logger.debug("[R-F3873] could not persist state for %s", engine, exc_info=True)


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

    # R-F3873 — this engine demonstrably ANSWERED, which is the only thing that
    # clears a blocked reading. Recorded on the same last-writer-wins key the
    # unresponsive path writes, so recovery needs no clock comparison.
    await _register(engine)
    await _set_last_event(engine, "served")

    if _should_quarantine(stats):
        await _quarantine(engine, stats)
    return stats


@fail_wire(module="search_engine_health", gap_type="engine_failure")
async def record_unresponsive(engine: str, reason: str = "") -> None:
    """R-F3873 — SearXNG told us this engine refused to serve. Write it down.

    This is the BLOCKED axis, and it is deliberately kept apart from the lying axis:

      * It does NOT touch the relevance counters. Folding a block into `total`
        would corrupt the quarantine ratio with events that say nothing about
        whether an engine answers the right question.
      * It does NOT quarantine. A blocked engine already returns nothing, so a
        quarantine adds no protection, and it would hold the engine out for an
        hour after the block lifts — turning a provider's transient refusal into a
        self-inflicted outage. §27: no code change fixes an IP block. The correct
        response is to SEE it.

    Never raises into the search path (property 4).
    """
    engine = _norm(engine)
    await _register(engine)
    try:
        prior = await rs.get_json(_skey(engine))
    except Exception:
        prior = None
    was_serving = not (isinstance(prior, dict)
                       and prior.get("last_event") == "unresponsive")
    await _set_last_event(engine, "unresponsive", reason)
    if was_serving:
        await _alert_blocked(engine, reason)


async def _alert_blocked(engine: str, reason: str) -> None:
    """Announce a source going dark — ONCE per hour, on the transition.

    §21a: this is the signal that did not exist. `yep` went from answering 20/20 to
    access-denied within the hour and nothing but a human bake-off noticed.
    """
    marker = f"{_BLOCK_ALERT_PREFIX}{engine}"
    try:
        if await rs.get(marker):
            return
        await rs.set(marker, str(time.time()), ex=_BLOCK_ALERT_TTL_S)
    except Exception:
        return                       # cannot dedupe -> stay silent, never flood
    logger.warning("[R-F3873] search engine %r is UNRESPONSIVE: %s", engine, reason)
    try:
        from .engine_wiring import wire_failure
        wire_failure(
            module="search_engine_health",
            detail=(f"search engine {engine!r} is refusing to serve: {reason!r}. This "
                    f"is a BLOCK, not a relevance problem — no engine-list tuning or "
                    f"retry fixes it (§27), and residential proxies are not an option "
                    f"we will take. Breadth is reduced until the provider relents; "
                    f"verify the remaining backends still cover DD's queries."),
            gap_type="search_backend_failure",
            source="search_engine_health:record_unresponsive",
        )
    except Exception:      # pragma: no cover - observability never blocks search
        pass


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
    if engines:
        names = list(engines)
    else:
        # R-F3873 — the roster maintains itself. A module whose reason for existing
        # is that hand-maintained engine lists rot must not report through one; the
        # defaults are a floor for a cold store, not the source of truth.
        names = ["yep", "bing", "wikipedia", "wikidata", "brave", "searxng"]
        try:
            raw = await rs.get_json(_REGISTRY_KEY)
            if isinstance(raw, list):
                names = sorted(set(names) | {str(n) for n in raw})
        except Exception:
            pass
    out: dict[str, Any] = {}
    degraded: list[str] = []
    blocked: list[str] = []
    for name in names:
        try:
            stats = await rs.get_json(_key(name))
        except Exception:
            stats = None
        try:
            q = bool(await rs.get(_qkey(name)))
        except Exception:
            q = False
        try:
            state = await rs.get_json(_skey(name))
        except Exception:
            state = None
        state = state if isinstance(state, dict) else None
        if not isinstance(stats, dict) and not q and not state:
            continue                      # §22 — never seen is unknown, not healthy
        entry = dict(stats or {})
        entry["quarantined"] = q
        entry["judged"] = int(entry.get("total") or 0) >= _MIN_SAMPLE
        # R-F3873 — the BLOCKED axis, reported alongside but never merged into the
        # relevance one. `serving` reads the last-writer-wins event, so an engine
        # with a spotless relevance history that has stopped answering can no
        # longer present as the healthiest source on the board.
        if state is None:
            entry["serving"] = None       # could not measure != measured and passed
        else:
            entry["serving"] = state.get("last_event") == "served"
            entry["unresponsive_count"] = int(state.get("unresponsive_count") or 0)
            entry["last_event_at"] = state.get("at")
            if state.get("last_unresponsive_reason"):
                entry["last_unresponsive_reason"] = state["last_unresponsive_reason"]
        out[name] = entry
        if q:
            degraded.append(name)
        if entry.get("serving") is False:
            blocked.append(name)
    return {
        "engines": out,
        "quarantined": sorted(degraded),
        # Engines the PROVIDER says are refusing us. Distinct from `quarantined`,
        # which is ARIA's own judgement that a source stopped answering the question.
        "blocked": sorted(blocked),
        "min_sample": _MIN_SAMPLE,
        "quarantine_ratio": _QUARANTINE_RATIO,
        "quarantine_ttl_s": _QUARANTINE_TTL_S,
    }

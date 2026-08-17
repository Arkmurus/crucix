"""R-F730 (2026-05-20) — entity resolution pre-flight for chat path.

Agent 1 DD audit found: `_detect_tool_intent` extracts a bare entity
string from chat messages and passes it straight to tool dispatch.
Live evidence: "Artur Group Angola" → web_search → returns unrelated
"Arturo's Restaurant" hits. There is no disambiguation, no canonical-
name resolution, no alias check before tools fire.

R-F730 adds a fast resolver-preflight step that wraps three existing
capabilities:

  1. `person_resolver.resolve()` — name component parsing + alias
     enumeration (transliteration, short-form variants) when the
     query looks like a person.
  2. `knowledge.search_knowledge()` — surfaces prior verified facts
     keyed on the entity name so multi-turn chats don't cold-start
     when ARIA already knows the entity.
  3. `intel_ledger` prior signals — recent observations on the
     entity from the autonomous research loop.

Returns a dict the chat path uses to:
  - prepend a `[ENTITY HINTS]` context block to the LLM prompt
  - inform tool dispatch (e.g. company name + jurisdiction → screen
    fires with the canonical form, not the raw user paste)
  - record canonical resolution for downstream investigation-thread
    continuity (R-F734).

Fail-soft: any sub-call exception logs at debug and returns whatever
has been collected. The chat path treats an empty resolver result as
no-op (current behaviour).
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import asyncio
import logging
import re
from typing import Any

logger = logging.getLogger("aria.intel.entity_resolver")

# Heuristics — when the query looks like a person vs an organisation.
# Conservative: requires either a title prefix OR multiple capitalised
# word tokens with no organisation suffix.
_TITLE_RE = re.compile(
    r"^(?:mr|mrs|ms|dr|prof|sir|dame|gen|col|maj|capt|lt|hon|rev|fr)\b\.?\s+",
    re.IGNORECASE,
)
_ORG_SUFFIX_RE = re.compile(
    r"\b(?:ltd|llc|inc|gmbh|sarl|sa|s\.a\.|plc|spa|kft|ag|co\.|corp|"
    r"corporation|holdings?|group|industries|company|partners?|"
    r"capital|ventures?|associates|llp|pty|bv|nv)\b\.?",
    re.IGNORECASE,
)
_PERSON_NAME_TOKENS_RE = re.compile(r"^[A-Z][a-z'’\-]{1,}(?:\s+[A-Z][a-z'’\-]{1,}){1,3}$")


def _classify_entity_type(query: str) -> str:
    """Return 'person', 'company', or 'unknown'. Used only to pick the
    right sub-resolver — wrong classification just means we run the
    wrong (cheap) lookup and surface nothing."""
    q = (query or "").strip()
    if not q:
        return "unknown"
    if _TITLE_RE.search(q):
        return "person"
    if _ORG_SUFFIX_RE.search(q):
        return "company"
    if _PERSON_NAME_TOKENS_RE.match(q):
        return "person"
    return "unknown"


async def _resolve_person(query: str, nationality_iso2: str | None) -> dict[str, Any]:
    """Run person_resolver.resolve in a worker thread (it's sync)."""
    try:
        from . import person_resolver
        result = await asyncio.to_thread(
            person_resolver.resolve, query, nationality_iso2=nationality_iso2,
        )
        # NameResolution is a dataclass per the module — turn it into a dict
        # safely, regardless of exact shape.
        if hasattr(result, "__dict__"):
            return {
                "canonical": getattr(result, "canonical_name", query) or query,
                "aliases": list(getattr(result, "variants", []) or [])[:20],
            }
        if isinstance(result, dict):
            return {
                "canonical": result.get("canonical_name") or query,
                "aliases": list(result.get("variants") or [])[:20],
            }
    except Exception as e:
        logger.debug("person resolver failed for %r: %s", query, e)
    return {"canonical": query, "aliases": []}


def _fetch_prior_facts_sync(query: str, limit: int) -> list[dict[str, Any]]:
    """Sync body of the facts fetch — runs in a worker thread.

    R-F4135 (C-169) — this called `knowledge.search_knowledge(query, limit=limit)`,
    and `search_knowledge(query: str) -> str` takes NO `limit`. Every call since
    R-F730 raised `TypeError` into the `except` below, whose only response was a
    `logger.debug`. So this returned `[]` unconditionally and nothing said so.

    The cost was not merely an empty list. `prior_facts` is worth 0.5 of the
    resolver's confidence score — the largest single component — so confidence
    was structurally capped at 0.5, and `render_context_block` never emitted a
    "Prior facts" section into the `[ENTITY HINTS]` block that feeds BOTH chat
    paths (§13).

    Two failures, and the second is why it survived: §3b (a call written against
    a signature nobody checked) and §21a (the failure was DARK — a permanently
    broken capability was indistinguishable from an entity with no history).

    `search_fact_records` is the function this code always wanted: it accepts
    `limit` and returns records. The records MUST be normalised to `summary`,
    because `render_context_block` reads `f["summary"]` while raw facts carry
    `topic`/`content` — swapping the call without the mapping would return facts
    and still render nothing.
    """
    try:
        from . import knowledge
        recs = knowledge.search_fact_records(query, limit=limit)
        out: list[dict[str, Any]] = []
        for r in recs or []:
            if not isinstance(r, dict):
                continue
            summary = (r.get("content") or r.get("topic") or "").strip()
            if not summary:
                continue
            out.append({
                "summary": summary[:400],
                "topic": r.get("topic") or "",
                "source": r.get("source") or "",
                "confidence": r.get("confidence"),
            })
            if len(out) >= limit:
                break
        return out
    except Exception as e:
        # §21a — a wire, not a debug log. The debug log is what let this stay
        # broken: silence and "no history" looked identical for the whole life
        # of the feature.
        try:
            from .engine_wiring import wire_failure
            wire_failure(
                module="entity_resolver",
                gap_type="engine_failure",
                detail=f"prior-facts lookup failed for {query[:80]!r}: {e}",
                source="entity_resolver:R-F4135",
            )
        except Exception:
            logger.debug("[R-F4135] prior-facts failure wire failed", exc_info=True)
        logger.debug("knowledge.search_fact_records failed for %r: %s", query, e)
    return []


async def _fetch_prior_facts(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Pull recent knowledge-store facts mentioning the entity. Wedge
    guard: the ranking scan is a sync iteration over the whole knowledge
    cache, so we dispatch to a worker thread to keep the event loop
    responsive per the R-F727 wedge findings.

    R-F4135 (C-169) — the corpus is **567,000 facts**, not the "~55k in prod"
    this said; and the count matters more than it used to, because restoring
    this lookup (dead since R-F730, see `_fetch_prior_facts_sync`) adds a REAL
    O(corpus) scan per resolve where the TypeError previously made it free. At
    the measured 0.27-0.88s per scan that is a genuine new cost on the chat
    path, taken deliberately: a resolver that cannot see ARIA's own verified
    facts is the more expensive failure. R-F4136's `knowledge.ranking_stats()`
    will show it under this module's name — if `entity_resolver` turns out to
    dominate that reading, C-166's fix is the one that pays for it.
    """
    return await asyncio.to_thread(_fetch_prior_facts_sync, query, limit)


def _scan_signals_sync(signals: list, ql: str, limit: int) -> list[dict[str, Any]]:
    hits = []
    for s in reversed(signals[-2000:]):  # newest first, capped scan
        if not isinstance(s, dict):
            continue
        text = " ".join(
            str(s.get(k) or "") for k in
            ("summary", "detail", "entity", "source", "topic")
        ).lower()
        if ql in text:
            hits.append(s)
            if len(hits) >= limit:
                break
    return hits


async def _fetch_prior_signals(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Pull recent intel_ledger signals mentioning the entity by simple
    substring match. Bounded scan (capped at 2000 newest). Per R-F727
    wedge findings, the substring scan runs in to_thread."""
    try:
        from . import intel_ledger
        ql = (query or "").strip().lower()
        if not ql or len(ql) < 3:
            return []
        # Prefer a search helper if one exists; fall back to
        # all_signals + filter.
        if hasattr(intel_ledger, "search_signals"):
            res = intel_ledger.search_signals(query)
            if asyncio.iscoroutine(res):
                res = await res
            if isinstance(res, list):
                return res[:limit]
        if hasattr(intel_ledger, "all_signals"):
            res = intel_ledger.all_signals()
            if asyncio.iscoroutine(res):
                res = await res
            if isinstance(res, list):
                return await asyncio.to_thread(_scan_signals_sync, res, ql, limit)
    except Exception as e:
        logger.debug("intel_ledger search failed for %r: %s", query, e)
    return []


async def resolve(
    query: str,
    *,
    persona: str | None = None,
    nationality_iso2: str | None = None,
    fetch_history: bool = True,
) -> dict[str, Any]:
    """Run entity resolution pre-flight. Returns a dict with shape:

        {
          "query":      str,
          "entity_type": "person" | "company" | "unknown",
          "canonical":   str,
          "aliases":     list[str],
          "prior_facts": list[dict],
          "prior_signals": list[dict],
          "confidence":  float,    # 0.0–1.0
        }

    Empty `query` returns an entity_type=unknown stub. All sub-calls
    are fail-soft. Total wall-clock budget: ~50–250ms typical.
    """
    out: dict[str, Any] = {
        "query":         (query or "").strip()[:300],
        "entity_type":   "unknown",
        "canonical":     (query or "").strip()[:300],
        "aliases":       [],
        "prior_facts":   [],
        "prior_signals": [],
        "confidence":    0.0,
    }
    if not out["query"] or len(out["query"]) < 2:
        return out

    out["entity_type"] = _classify_entity_type(out["query"])

    coros = []
    if out["entity_type"] == "person":
        coros.append(("person", _resolve_person(out["query"], nationality_iso2)))

    if fetch_history:
        coros.append(("facts", _fetch_prior_facts(out["query"])))
        coros.append(("signals", _fetch_prior_signals(out["query"])))

    if not coros:
        return out

    results = await asyncio.gather(
        *[c for _, c in coros], return_exceptions=True,
    )
    for (kind, _), res in zip(coros, results):
        if isinstance(res, Exception):
            logger.debug("entity_resolver %s sub-call raised: %s", kind, res)
            continue
        if kind == "person" and isinstance(res, dict):
            out["canonical"] = res.get("canonical") or out["canonical"]
            out["aliases"] = res.get("aliases") or []
        elif kind == "facts" and isinstance(res, list):
            out["prior_facts"] = res
        elif kind == "signals" and isinstance(res, list):
            out["prior_signals"] = res

    # Confidence is a coarse signal: high when we have prior context,
    # mid when we resolved a name shape, low otherwise.
    score = 0.0
    if out["prior_facts"]:
        score += 0.5
    if out["prior_signals"]:
        score += 0.3
    if out["aliases"]:
        score += 0.2
    out["confidence"] = round(min(score, 1.0), 2)

    return out


def render_context_block(resolved: dict[str, Any]) -> str:
    """Format a resolved entity dict into a [ENTITY HINTS] context
    block. Returns empty string when there's nothing useful to
    show (so the chat path can no-op cleanly)."""
    if not resolved or not isinstance(resolved, dict):
        return ""
    query = resolved.get("query") or ""
    canonical = resolved.get("canonical") or query
    aliases = resolved.get("aliases") or []
    facts = resolved.get("prior_facts") or []
    signals = resolved.get("prior_signals") or []

    have_signal = bool(facts or signals or (aliases and canonical != query))
    if not have_signal:
        return ""

    lines = ["[ENTITY HINTS — R-F730 pre-flight resolution]"]
    lines.append(f"Query: {query}")
    if canonical and canonical != query:
        lines.append(f"Canonical: {canonical}")
    if aliases:
        lines.append(f"Aliases ({len(aliases)}): " + ", ".join(aliases[:8]))
    if facts:
        lines.append(f"Prior facts ({len(facts)}):")
        for f in facts[:5]:
            summary = (f.get("summary") or "")[:200] if isinstance(f, dict) else str(f)[:200]
            if summary:
                lines.append(f"  - {summary}")
    if signals:
        lines.append(f"Prior signals ({len(signals)}):")
        for s in signals[:5]:
            if not isinstance(s, dict):
                continue
            summary = (s.get("summary") or s.get("detail") or "")[:200]
            if summary:
                lines.append(f"  - {summary}")
    lines.append("Use these as starting context. Verify before citing as fact.")
    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="entity_resolver",
        summary="Render Context Block",
        source_id="entity_resolver:R-F996",
    )

    return "\n".join(lines)

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.

"""Golden Intel corroboration engine — R-F2638.

Phase B+ under an explicit operator §1 override ("I understand Phase A gate is open.
Override anyway", 2026-07-15). Scope: docs/golden_intel_corroboration_scope_2026_07_15.md.

WHY THIS EXISTS
---------------
`evidence_count >= 2` is what earns the "corroborated" label (news_monitor.py:638/665/
713) — but those three sites are READERS ONLY. NO writer anywhere produced a value
above 1: the RSS path defaults to 1 (news_monitor.py:691) and the bridge adapters
HARDCODE 1 (golden_intel_bridge.py:559/731/790). Live 2026-07-15: evidence_count
{1: 20}, corroboration {'single-source': 20}.

So corroboration was IMPOSSIBLE. The Mining Queue's only exits are corroboration or a
tier upgrade, so it was a roach motel: "8 candidates awaiting corroboration" was a
promise the system could not keep, and Distribution Ready = 0 was STRUCTURAL (proved
by R-F2630: the poll went fresh, reasons=[], and the column stayed 0).

The pipeline was HONEST but INERT — it correctly refused to publish uncorroborated
news, and could never earn the right to publish. A system that always says
"insufficient evidence" is trivially never-false-clean and delivers ZERO value.
Corroboration is the USP made PRODUCTIVE rather than merely defensive.

THE DESIGN — WIRE WHAT EXISTS
-----------------------------
verified_intel.SourceIndependenceChecker (:747) ALREADY models the hard part:
    "Reuters and AFP often cover the same press conference. Al Arabiya and Al Jazeera
     both report the same presidency statement. Sources in the same family citing each
     other are not independent."
and `get_independent_count(sources) -> int` (:781) IS the evidence_count producer.
This module does NOT reimplement independence — it delegates.

The only NEW logic is fail-closed clustering.

★ THE TRAP (why this is not trivial)
------------------------------------
The live data contained BOTH failure modes:
  1. "US says it launched new wave of strikes against Iran" appeared 3x FROM ONE SOURCE
     (Middle East Eye). Counting them => evidence_count 3 from ONE org => FALSE
     corroboration.
  2. Entity-only clustering merged "US strikes Iran" with "US resumes Iran ports
     blockade" — DIFFERENT events sharing the entity "Iran".
A naive build reports evidence_count=10 and publishes confident lies — converting an
honest "no" into a false "yes". That is STRICTLY WORSE than the inert state and a
direct USP kill. Hence: cluster on entity AND time-window AND title similarity, and
fail closed on ANY uncertainty (missing entities, unparseable dates, thin titles).

§6: no new third-party, NO embeddings — deterministic only. That also avoids the
R-F703 GIL/import-lock stall class (the 44.5s freeze was torch imported for embeddings).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import urlparse

from .verified_intel import SourceIndependenceChecker, SourceRecord, SourceTier

from .engine_wiring import wire_failure, wire_success

logger = logging.getLogger("aria.corroboration")

# Two stories corroborate only if published within this window. Conflict reporting
# clusters within hours; a month apart is a different event (fail closed on time).
_WINDOW_S = 48 * 3600

# Title similarity floor (token Jaccard). Deterministic — no model, no embeddings.
# Deliberately strict: a MISS costs us a corroboration (honest "no"); a FALSE MERGE
# costs us the USP (a confident lie). Asymmetric risk => bias to miss.
_TITLE_SIM_MIN = 0.34

# Titles below this many meaningful tokens cannot be compared reliably => fail closed.
_MIN_TOKENS = 3

_STOP = {
    "the", "a", "an", "and", "or", "of", "in", "on", "at", "to", "for", "as", "by",
    "with", "from", "is", "are", "was", "were", "be", "been", "it", "its", "that",
    "this", "after", "over", "new", "says", "said", "amid", "into", "up", "out",
}

_TIER_MAP = {
    "tier_1a": SourceTier.TIER_1A,
    "tier_1b": SourceTier.TIER_1B,
    "tier_2": SourceTier.TIER_2,
    "tier_3": SourceTier.TIER_3,
}


def _tokens(title: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", str(title or "").lower())
    return {w for w in words if w not in _STOP and len(w) > 2}


def _similar(a: str, b: str) -> bool:
    """Deterministic token-Jaccard similarity, fail-closed on thin titles."""
    ta, tb = _tokens(a), _tokens(b)
    if len(ta) < _MIN_TOKENS or len(tb) < _MIN_TOKENS:
        return False  # cannot judge => do NOT merge
    inter = len(ta & tb)
    union = len(ta | tb)
    return bool(union) and (inter / union) >= _TITLE_SIM_MIN


def _epoch(value: Any) -> float | None:
    """Parse a timestamp to epoch seconds, or None (=> fail closed).

    R-F2638: RSS emits RFC 2822 ('Wed, 15 Jul 2026 10:27:52 +0000'), NOT ISO 8601.
    An ISO-only parser returned None for EVERY live signal, so nothing was ever
    clusterable and this engine shipped as a silent no-op (7/7 fixture tests green,
    0/20 real signals corroborated). Caught only by running it against the real feed —
    the fixtures used a date format production never emits. Both formats now parse.
    """
    if not value:
        return None
    text = str(value).strip()
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        pass
    try:
        from email.utils import parsedate_to_datetime  # stdlib — no new dep (§6)
        dt = parsedate_to_datetime(text)
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None  # unparseable => fail closed, never guess


def _entity_key(signal: dict) -> tuple:
    ents = signal.get("entities") or {}
    if not isinstance(ents, dict):
        return ()
    vals: list[str] = []
    for field in ("countries", "oems", "products"):
        v = ents.get(field)
        if isinstance(v, list):
            vals.extend(str(x).strip().lower() for x in v if str(x).strip())
    return tuple(sorted(set(vals)))


def _independence_group(signal: dict) -> str:
    """One vote per ORG. Derived from the domain, falling back to the source name.

    This is what kills the live "3x Middle East Eye" false corroboration: three
    articles, one independence_group, one vote.
    """
    url = str(signal.get("url") or "")
    host = urlparse(url).netloc.lower() if url else ""
    host = host[4:] if host.startswith("www.") else host
    if host:
        parts = [p for p in host.split(".") if p]
        # registrable-ish core: drop the TLD (and a ccTLD second level like .co.uk)
        if len(parts) >= 3 and parts[-2] in {"co", "com", "org", "gov", "net", "ac"}:
            return parts[-3]
        if len(parts) >= 2:
            return parts[-2]
        return host
    return str(signal.get("source") or "unknown").strip().lower()


def _to_source_record(signal: dict) -> SourceRecord:
    tier = _TIER_MAP.get(str(signal.get("source_tier") or "").lower(), SourceTier.TIER_2)
    return SourceRecord(
        url=str(signal.get("url") or ""),
        tier=tier,
        score=float(signal.get("score") or 0),
        domain=_independence_group(signal),
        retrieved_at=str(signal.get("published") or ""),
        excerpt="",
        title=str(signal.get("title") or ""),
        language=str(signal.get("language") or "en"),
        independence_group=_independence_group(signal),
    )


def _clusterable(signal: dict) -> bool:
    """Fail-closed gate: only signals with the data needed to judge may cluster."""
    return bool(_entity_key(signal)) and _epoch(signal.get("published")) is not None \
        and len(_tokens(signal.get("title"))) >= _MIN_TOKENS


def corroborate(signals: Iterable[dict]) -> list[dict]:
    """Compute HONEST evidence_count / corroboration across a batch of signals.

    Clusters members that share >=1 entity AND fall inside the time window AND exceed
    the title-similarity floor, then counts INDEPENDENT sources via the existing
    SourceIndependenceChecker. Anything uncertain stays single-source.

    Returns the same dicts, mutated with evidence_count + corroboration.
    """
    items = [s for s in (signals or []) if isinstance(s, dict)]
    if not items:
        return []

    checker = SourceIndependenceChecker()

    # Default everything to the honest floor first: 1 source, uncorroborated.
    for s in items:
        s["evidence_count"] = 1
        s["corroboration"] = "single-source"

    eligible = [s for s in items if _clusterable(s)]

    # Greedy clustering. O(n^2) on a <=100-signal batch — deterministic and cheap;
    # no embeddings (§6, and it keeps the event loop free — cf. R-F703).
    used: set[int] = set()
    for i, a in enumerate(eligible):
        if i in used:
            continue
        cluster = [a]
        used.add(i)
        ka, ta = _entity_key(a), _epoch(a.get("published"))
        for j in range(i + 1, len(eligible)):
            if j in used:
                continue
            b = eligible[j]
            if not (set(ka) & set(_entity_key(b))):
                continue                                   # no shared entity
            tb = _epoch(b.get("published"))
            if ta is None or tb is None or abs(ta - tb) > _WINDOW_S:
                continue                                   # outside the window
            if not _similar(a.get("title"), b.get("title")):
                continue                                   # different event
            cluster.append(b)
            used.add(j)

        if len(cluster) < 2:
            continue

        records = [_to_source_record(s) for s in cluster]
        try:
            n = int(checker.get_independent_count(records))
        except Exception as e:
            # R-F3387 — the SEMANTICS here were already right (fail closed, never
            # invent corroboration). The gap was that the failure was DARK: a
            # logger.warning is not a brain sink (§21a), so a permanently broken
            # independence checker looked identical to "nothing needed
            # corroborating". Signal it; behaviour is unchanged.
            wire_failure(
                module="corroboration",
                detail=f"independence count failed, failing closed: {type(e).__name__}: {e}"[:400],
                gap_type="engine_failure",
                source="corroboration:corroborate",
            )
            logger.warning("[corroboration] independence count failed: %s — failing closed", e)
            continue                                       # never invent corroboration

        # One vote per independence_group: 3x Middle East Eye is ONE source.
        n = max(1, min(n, len({r.independence_group for r in records})))
        if n < 2:
            continue

        for s in cluster:
            s["evidence_count"] = n
            s["corroboration"] = "corroborated"
        logger.info(
            "[corroboration] %d signals -> %d INDEPENDENT source(s): %s",
            len(cluster), n, str(cluster[0].get("title"))[:70],
        )

    # R-F3387 — §21a success branch: a completed corroboration pass is telemetry
    # the brain needs, so "the corroborator ran and found nothing" is
    # distinguishable from "the corroborator never ran".
    wire_success(
        module="corroboration",
        summary=f"corroboration pass over {len(items)} signal(s)",
        source_id="corroboration:corroborate",
    )
    return items

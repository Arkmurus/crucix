"""Entity-lookup-shaped intelligence sources for the DD orchestrator.

Every adapter in this package exports an async `lookup(name, ...)`
function that returns a standardised dict:

    {
        "source": str,           # canonical source name, e.g. "sec_edgar"
        "ok": bool,              # True iff the fetch succeeded (even 0 hits)
        "query": dict,           # what was actually queried, echoed back
        "hits": list[dict],      # matches (shape depends on the source)
        "hit_count": int,        # len(hits), duplicated for cheap filtering
        "query_time_ms": int,
        "fetched_at": str,       # ISO-8601 UTC
        "auth": str,             # "none" | "anonymous" | "api_key" | "oauth"
        "error": str | None,
        "citation_url": str,     # canonical URL the operator can verify
    }

The shape is intentionally thin — callers (dd_orchestrator) map each
hit into a `Finding` object with the appropriate severity, because the
severity interpretation varies by source (a World Bank debarment is a
hard stop; a SEC 8-K is INFO unless keywords escalate it).

Why these 6 sources specifically
────────────────────────────────
Past incident 2026-04-18: ARIA's DD orchestrator only queried 20 company
registries + OpenSanctions. The Node side had `apis/sources/` with 52
adapters including OFAC / FCDO / UN / SEC / ACLED / World Bank debarred
— but those were briefing-shaped (today's global activity), not
entity-lookup-shaped, and they ran on a different service. Python DD
never called them. This package closes the gap for the 6 highest-DD-value
primary sources. Others can follow the same pattern.

All adapters here are self-contained — they each own their own HTTP
client, cache, and retry logic so one failing source cannot drag the
others down. The dd_orchestrator runs them in parallel via asyncio.gather
with per-source timeouts.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("aria.sources")

__all__ = [
    "sec_edgar",
    "ofac_sdn",
    "fcdo_sanctions",
    "un_sc_sanctions",
    "worldbank_debarred",
    "acled",
]

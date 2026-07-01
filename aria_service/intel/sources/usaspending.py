"""USASpending — free US federal award/contract data (no key, datacenter-friendly).

R-F2273 — the procurement leg. For a defence-procurement DD the decision-critical question
is: does this entity actually hold US federal contracts, how much, and from which agencies?
USASpending answers it directly. API-first (POST to api.usaspending.gov) — it returns HTTP
200 to a datacenter IP (verified: real contracts for Lockheed Martin — $48B DoE, $35B DoD).
§6-clean: free, no key. Returns None (honest "no federal contracts found") rather than an
error when the entity has no US awards — most non-US entities won't, and that is itself a
signal, not a failure.
"""
from __future__ import annotations

import logging

import httpx

from ..engine_wiring import wire_success, wire_failure
from ..circuit_breaker import get_breaker

logger = logging.getLogger("aria.sources.usaspending")

_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
_TIMEOUT = 20.0


def is_available() -> bool:
    """Free + open — always available (no key)."""
    return True


async def lookup(name: str, max_awards: int = 8) -> dict | None:
    """Return a US-federal-contract summary for ``name`` (or None if no awards / on failure).

    Shape: {recipient, award_count, total_value_usd, top_agencies, awards[], source_url,
    adapter}. Best-effort; never raises.
    """
    cb = get_breaker("procurement:usaspending", failure_threshold=5, cooldown_seconds=300)
    if cb.is_open():
        return None
    q = (name or "").strip()
    if len(q) < 3:
        return None
    body = {
        "filters": {
            "recipient_search_text": [q],
            "award_type_codes": ["A", "B", "C", "D"],  # prime contracts (not grants/loans)
        },
        "fields": ["Award ID", "Recipient Name", "Award Amount", "Awarding Agency",
                   "Period of Performance Start Date"],
        "limit": max(1, min(max_awards, 25)),
        "sort": "Award Amount",
        "order": "desc",
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(_URL, json=body, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            cb.record_failure(reason=f"http_{r.status_code}")
            return None
        results = ((r.json() or {}).get("results") or [])
        cb.record_success()
        if not results:
            return None  # no US federal contracts — honest signal, not an error
        awards: list = []
        total = 0.0
        agencies: dict = {}
        for a in results:
            try:
                amtf = float(a.get("Award Amount") or 0)
            except (TypeError, ValueError):
                amtf = 0.0
            total += amtf
            ag = (a.get("Awarding Agency") or "").strip()
            if ag:
                agencies[ag] = agencies.get(ag, 0) + 1
            awards.append({
                "award_id": a.get("Award ID", "") or "",
                "recipient": a.get("Recipient Name", "") or "",
                "amount_usd": amtf,
                "agency": ag,
                "start_date": a.get("Period of Performance Start Date", "") or "",
            })
        top_agencies = sorted(agencies, key=lambda k: agencies[k], reverse=True)[:4]
        summary = {
            "recipient": awards[0]["recipient"] if awards else q,
            "award_count": len(awards),
            "total_value_usd": round(total, 2),
            "top_agencies": top_agencies,
            "awards": awards,
            "source_url": "https://www.usaspending.gov/search",
            "adapter": "usaspending",
        }
        try:
            wire_success(
                module="usaspending",
                summary=(f"USASpending: {len(awards)} federal awards "
                         f"${round(total):,.0f} for '{q}' via {', '.join(top_agencies) or 'n/a'}"),
                source_id="usaspending:lookup",
            )
        except Exception:
            pass
        return summary
    except Exception as ex:  # noqa: BLE001 — best-effort; never breaks the DD
        cb.record_failure(reason="timeout")
        logger.warning("[usaspending] lookup failed for %s: %s", q, ex)
        try:
            wire_failure(module="usaspending", detail=f"usaspending lookup failed: {ex}",
                         gap_type="procurement_lookup", source="usaspending:lookup")
        except Exception:
            pass
        return None

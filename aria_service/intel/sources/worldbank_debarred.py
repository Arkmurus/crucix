"""World Bank debarred/ineligible firms — direct primary source.

What "debarred" means
─────────────────────
The World Bank maintains a public list of firms and individuals
ineligible to be awarded a Bank-financed contract, following a finding
of fraud, corruption, collusion, coercion, or obstruction. Debarment
is frequently cross-recognised by other MDBs (AfDB, AsDB, EBRD, IDB)
under the Mutual Enforcement Agreement 2010 — so a World Bank
debarment often effectively bars the firm from the whole MDB ecosystem.

Why this matters for DD
───────────────────────
A defence contractor debarred by the World Bank cannot be proposed to
buyers who finance procurement through MDB loans (a significant share
of African + MENA + LatAm defence-adjacent procurement). Missing this
signal has material commercial consequences. This is exactly the kind
of check commercial DD tools charge a lot for; it's free and authoritative.

Data source
───────────
The WB publishes the list at:
  https://projects.worldbank.org/en/projects-operations/procurement/debarred-firms
  https://apigwext.worldbank.org/dvsvc/v1.0/json/APPLICATION/ADOBE_EXPRNCE_MGR/FIRM360/FIRM360

Two endpoints — the gwext.worldbank.org one returns JSON directly but
sometimes rate-limits hard. We try it first, then fall back to parsing
the HTML page (which embeds the same data in a JSON blob).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

from . import _common

logger = logging.getLogger("aria.sources.worldbank_debarred")

_SOURCE = "worldbank_debarred"
_AUTH = "anonymous"

_FEED_JSON = "https://apigwext.worldbank.org/dvsvc/v1.0/json/APPLICATION/ADOBE_EXPRNCE_MGR/FIRM360/FIRM360"
_HUMAN_URL = "https://projects.worldbank.org/en/projects-operations/procurement/debarred-firms"

_CACHE: dict[str, Any] = {"fetched_at": 0.0, "records": []}
_CACHE_TTL_S = 24 * 3600  # 24h — WB refreshes weekly at most
_CACHE_LOCK = asyncio.Lock()


async def _load_records() -> list[dict]:
    async with _CACHE_LOCK:
        now = time.time()
        if _CACHE["records"] and (now - _CACHE["fetched_at"] < _CACHE_TTL_S):
            return _CACHE["records"]

        # Try the JSON feed first
        data = await _common.http_get_json(_FEED_JSON, timeout=25.0)
        rows: list[dict] = []
        if isinstance(data, dict):
            # Response shape varies; try common keys
            rows = (
                data.get("response", {}).get("ZPROCSUPP", {}).get("ZPROCSUPPDETAILS")
                or data.get("response", {}).get("items")
                or data.get("records")
                or data.get("items")
                or []
            )
        elif isinstance(data, list):
            rows = data

        records: list[dict] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            name = (
                r.get("SUPP_NAME")
                or r.get("firm_name")
                or r.get("name")
                or r.get("supplierName")
                or ""
            )
            if not name:
                continue
            country = (
                r.get("COUNTRY_NAME")
                or r.get("country")
                or r.get("supplierCountryName")
                or ""
            )
            grounds = (
                r.get("GROUNDS")
                or r.get("grounds")
                or r.get("debarmentGround")
                or ""
            )
            ineligibility_from = (
                r.get("INELGBLTY_FROM_DATE")
                or r.get("ineligibilityFrom")
                or r.get("from_date")
                or ""
            )
            ineligibility_to = (
                r.get("INELGBLTY_TO_DATE")
                or r.get("ineligibilityTo")
                or r.get("to_date")
                or ""
            )
            address = r.get("SUPP_ADDR") or r.get("address") or ""

            records.append({
                "name": name.strip(),
                "aliases": [],
                "list_type": "WB_DEBARRED",
                "country": country,
                "grounds": grounds,
                "ineligibility_from": ineligibility_from,
                "ineligibility_to": ineligibility_to,
                "address": address,
                "citation_url": _HUMAN_URL,
            })

        if not records:
            logger.warning(
                "[worldbank_debarred] JSON feed returned 0 parseable records; "
                "keeping stale cache (%d recs)", len(_CACHE["records"]),
            )
            return _CACHE["records"]

        _CACHE["records"] = records
        _CACHE["fetched_at"] = now
        logger.info("[worldbank_debarred] cache refreshed (%d records)", len(records))
        return records


async def lookup(
    name: str,
    *,
    threshold: float = 0.72,
    max_hits: int = 10,
) -> dict:
    """Entity-scoped World Bank debarred-firms lookup.

    Severity for the orchestrator: any active (current-date within
    ineligibility window) hit is a RED finding — not an auto hard-stop
    like a sanctions hit, but it blocks MDB-financed deals and requires
    enhanced DD. Expired debarments are INFO (historical record).
    """
    started = time.time()
    query = {"name": name}
    result = _common.empty_result(
        _SOURCE, query, auth=_AUTH, citation_url=_HUMAN_URL,
    )

    if not name or len(name.strip()) < 2:
        return _common.error_result(
            _SOURCE, query, "name too short for screen",
            auth=_AUTH, started_at=started,
        )

    try:
        records = await _load_records()
        if not records:
            return _common.error_result(
                _SOURCE, query, "WB debarred list unavailable",
                auth=_AUTH, started_at=started,
            )
        hits = _common.fuzzy_filter(
            records, name, name_key="name",
            threshold=threshold, max_hits=max_hits,
        )
        # Annotate each hit with active/expired status vs. today
        from datetime import date
        today = date.today().isoformat()
        for h in hits:
            to_date = h.get("ineligibility_to", "")
            if to_date and to_date < today:
                h["status"] = "expired"
                h["severity_hint"] = "INFO — historical debarment (expired)"
            else:
                h["status"] = "active"
                h["severity_hint"] = "RED — active World Bank debarment"
        result["hits"] = hits
        return _common.finalise(result, started)
    except Exception as e:
        logger.warning("[worldbank_debarred] lookup failed for %r: %s", name, e)
        return _common.error_result(
            _SOURCE, query, f"{type(e).__name__}: {e}",
            auth=_AUTH, started_at=started,
        )


async def is_available() -> bool:
    if _CACHE["records"]:
        return True
    data = await _common.http_get_json(_FEED_JSON, timeout=10.0)
    return bool(data)

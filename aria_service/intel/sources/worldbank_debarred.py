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
of African + MENA + LatAm defence-adjacent procurement).

Status — 2026-04-18 operational note
────────────────────────────────────
The WB has moved the `apigwext.worldbank.org/dvsvc/...` JSON endpoint
behind an Azure API Management subscription key (returns HTTP 401
"missing subscription key" without it). Free developer-portal signup
is still possible but requires operator action.

Until a WORLDBANK_SUBSCRIPTION_KEY is set, this module degrades honestly:
  - `lookup()` returns an error_result with the capability_gap reason
  - `is_available()` returns False
  - `dd_orchestrator` flags the gap in the report's data_gaps list

OpenSanctions already aggregates WB debarments (dataset `wb_debarred`),
so the DD pipeline doesn't lose the signal — it just loses the
primary-source citation until someone registers for a WB API key.

Fallback mode — if the env var is set, we try the authenticated endpoint.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from . import _common

logger = logging.getLogger("aria.sources.worldbank_debarred")

_SOURCE = "worldbank_debarred"
_AUTH = "api_key"  # Azure APIM subscription key required by WB as of 2024

_FEED_JSON = "https://apigwext.worldbank.org/dvsvc/v1.0/json/APPLICATION/ADOBE_EXPRNCE_MGR/FIRM360/FIRM360"
_HUMAN_URL = "https://projects.worldbank.org/en/projects-operations/procurement/debarred-firms"
_API_KEY_ENV = "WORLDBANK_SUBSCRIPTION_KEY"

_CACHE: dict[str, Any] = {"fetched_at": 0.0, "records": []}
_CACHE_TTL_S = 24 * 3600  # 24h — WB refreshes weekly at most
_CACHE_LOCK = asyncio.Lock()


def _subscription_key() -> str:
    return (os.getenv(_API_KEY_ENV) or "").strip()


async def _load_records() -> list[dict]:
    async with _CACHE_LOCK:
        now = time.time()
        if _CACHE["records"] and (now - _CACHE["fetched_at"] < _CACHE_TTL_S):
            return _CACHE["records"]

        sub_key = _subscription_key()
        if not sub_key:
            # Honest degrade — no key means no fetch. OpenSanctions
            # provides the coverage in aggregated form via its
            # dataset "wb_debarred" so the DD signal is not lost.
            return _CACHE["records"]  # stale cache (possibly empty) — no HTTP call

        # Try the JSON feed with the subscription key
        headers = {
            "Ocp-Apim-Subscription-Key": sub_key,
            "Accept": "application/json",
        }
        data = await _common.http_get_json(_FEED_JSON, timeout=25.0, headers=headers)
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

    if not _subscription_key():
        # Honest "capability-gap" signal so the orchestrator knows to
        # lean on the OpenSanctions wb_debarred dataset for coverage.
        return _common.error_result(
            _SOURCE, query,
            "WORLDBANK_SUBSCRIPTION_KEY not configured — "
            "register free at worldbank.org (developer portal). "
            "Signal still available via OpenSanctions dataset wb_debarred.",
            auth=_AUTH, started_at=started,
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
                _SOURCE, query, "WB debarred list unavailable (auth key may be invalid)",
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
    """Returns True only if an API subscription key is configured AND
    the feed returns data. Without a key, this source is intentionally
    marked unavailable so dd_orchestrator knows to lean on the
    OpenSanctions wb_debarred dataset for the same coverage."""
    if not _subscription_key():
        return False
    if _CACHE["records"]:
        return True
    headers = {
        "Ocp-Apim-Subscription-Key": _subscription_key(),
        "Accept": "application/json",
    }
    data = await _common.http_get_json(_FEED_JSON, timeout=10.0, headers=headers)
    return bool(data)

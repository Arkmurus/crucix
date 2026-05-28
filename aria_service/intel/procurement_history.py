"""R-F439 — unified procurement-history query for an entity.

tender_monitor.py scans NEW tenders per portal on a schedule (what's
publishing this week). R-F439 answers a different question: "give
me every contract this ENTITY ever won/bid anywhere". That's a
supplier-history lookup, not a feed scan.

V1 backends (free, no key):
  - USAspending.gov — every US federal award since FY2008,
    queryable by recipient name.
  - UK Contracts Finder OCDS — UK central + LA awards, queryable
    by supplier name via the OCDS package search.

Follow-up R-numbers can plug in:
  - EU TED v3 (supplier query is multi-step; defer)
  - UN Global Marketplace (HTML / no JSON API)
  - SAM.gov (key required)
  - World Bank Project DB (already in sweep but not as supplier query)

Cost: 2 HTTP requests per DD when wired in. Each backend has its
own timeout + graceful failure path; the orchestrator treats empty/
error as "no history found" not "clean".
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)


USASPENDING_AWARD_SEARCH = (
    "https://api.usaspending.gov/api/v2/search/spending_by_award/"
)
UK_CF_OCDS_SEARCH = (
    "https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search"
)


# ── USAspending ─────────────────────────────────────────────────────────────


async def query_usaspending(
    entity_name: str,
    *,
    timeout: float = 20.0,
    max_records: int = 50,
) -> dict:
    """Query USAspending for federal awards to a recipient by name.

    Returns:
      {
        "ok":        bool,
        "source":    "usaspending",
        "query":     entity_name,
        "records":   [{"title", "buyer", "value_usd", "date_signed",
                       "url", "award_id"}, ...],
        "error":     None | str,
      }
    """
    out: dict[str, Any] = {
        "ok": False,
        "source": "usaspending",
        "query": entity_name,
        "records": [],
        "error": None,
    }
    if not entity_name or len(entity_name) < 3:
        out["error"] = "entity_name too short for USAspending search"
        return out
    payload = {
        "filters": {
            "recipient_search_text": [entity_name],
            "award_type_codes": ["A", "B", "C", "D"],  # contracts only
        },
        "fields": [
            "Award ID", "Recipient Name", "Award Amount",
            "Awarding Agency", "Description", "Period of Performance Start Date",
            "Period of Performance Current End Date",
        ],
        "page": 1,
        "limit": min(max_records, 100),
        "sort": "Period of Performance Start Date",
        "order": "desc",
    }
    try:
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True,
        ) as client:
            resp = await client.post(
                USASPENDING_AWARD_SEARCH,
                json=payload,
                headers={
                    "User-Agent": "ARIA-DD/1.0 (compliance research)",
                    "Accept": "application/json",
                },
            )
            if resp.status_code != 200:
                out["error"] = f"USAspending HTTP {resp.status_code}"
                return out
            try:
                data = resp.json()
            except Exception as je:
                out["error"] = f"USAspending non-JSON: {str(je)[:120]}"
                return out
    except httpx.HTTPError as e:
        out["error"] = f"USAspending request error: {str(e)[:200]}"
        return out
    results = (data or {}).get("results") or []
    if not isinstance(results, list):
        out["error"] = "USAspending: results not a list"
        return out
    records: list[dict] = []
    for r in results[:max_records]:
        if not isinstance(r, dict):
            continue
        award_id = r.get("Award ID") or r.get("generated_internal_id") or ""
        records.append({
            "title":       (r.get("Description") or "")[:240],
            "buyer":       (r.get("Awarding Agency") or "")[:160],
            "value_usd":   float(r.get("Award Amount") or 0.0),
            "date_signed": r.get("Period of Performance Start Date") or "",
            "date_end":    r.get("Period of Performance Current End Date") or "",
            "url":         f"https://www.usaspending.gov/award/{award_id}" if award_id else "",
            "award_id":    str(award_id)[:120],
            "recipient":   (r.get("Recipient Name") or "")[:160],
        })
    out["ok"] = True
    out["records"] = records
    # R-F996 — wire to brain
    from .engine_wiring import wire_success
    wire_success(
        module="procurement_history",
        summary="Query Usaspending",
        source_id="procurement_history:R-F996",
    )

    return out


# ── UK Contracts Finder OCDS ────────────────────────────────────────────────


async def query_uk_contracts_finder(
    entity_name: str,
    *,
    timeout: float = 20.0,
    max_records: int = 50,
) -> dict:
    """Query UK Contracts Finder for awards to a supplier by name.

    Uses the OCDS Search endpoint which accepts a `supplierTextSearch`
    parameter and returns OCDS-style award packages.
    """
    out: dict[str, Any] = {
        "ok": False,
        "source": "uk_contracts_finder",
        "query": entity_name,
        "records": [],
        "error": None,
    }
    if not entity_name or len(entity_name) < 3:
        out["error"] = "entity_name too short"
        return out
    payload = {
        "searchCriteria": {
            "supplierTextSearch": entity_name,
            "noticeStatuses": ["Awarded"],
        },
        "size": min(max_records, 100),
    }
    try:
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True,
        ) as client:
            resp = await client.post(
                UK_CF_OCDS_SEARCH,
                json=payload,
                headers={
                    "User-Agent": "ARIA-DD/1.0 (compliance research)",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )
            if resp.status_code != 200:
                out["error"] = f"UK CF HTTP {resp.status_code}"
                return out
            try:
                data = resp.json()
            except Exception as je:
                out["error"] = f"UK CF non-JSON: {str(je)[:120]}"
                return out
    except httpx.HTTPError as e:
        out["error"] = f"UK CF request error: {str(e)[:200]}"
        return out
    # OCDS package shape: results[].releases[]
    results = (data or {}).get("results") or []
    if not isinstance(results, list):
        out["error"] = "UK CF: results not a list"
        return out
    records: list[dict] = []
    for pkg in results[:max_records]:
        if not isinstance(pkg, dict):
            continue
        releases = pkg.get("releases") or []
        if not releases:
            continue
        rel = releases[0]
        tender = (rel.get("tender") or {})
        awards = rel.get("awards") or []
        award = awards[0] if awards else {}
        suppliers = award.get("suppliers") or []
        supplier_name = suppliers[0].get("name") if suppliers else ""
        value = (award.get("value") or {})
        records.append({
            "title":       (tender.get("title") or "")[:240],
            "buyer":       ((rel.get("buyer") or {}).get("name") or "")[:160],
            "value_usd":   _to_usd(value.get("amount"), value.get("currency")),
            "date_signed": award.get("date") or rel.get("date") or "",
            "url":         f"https://www.contractsfinder.service.gov.uk/Notice/{rel.get('ocid', '')}",
            "award_id":    rel.get("ocid", "")[:120],
            "recipient":   (supplier_name or "")[:160],
        })
    out["ok"] = True
    out["records"] = records
    return out


def _to_usd(amount: Any, currency: str | None) -> float:
    """Naive currency conversion. v1 only converts GBP and EUR via
    static rough rates — we DO NOT inflate this to a real FX service
    because the value is for ranking/dedup, not financial reporting.
    Numbers carry the source currency code in the original payload
    if downstream needs it."""
    try:
        amt = float(amount or 0)
    except (TypeError, ValueError):
        return 0.0
    if not currency:
        return amt
    cur = (currency or "").upper()
    if cur == "USD":
        return amt
    if cur == "GBP":
        return amt * 1.27  # rough 2026 baseline
    if cur == "EUR":
        return amt * 1.09
    return amt  # unknown currency — pass through


# ── Aggregator ─────────────────────────────────────────────────────────────


async def query_entity_history(
    entity_name: str,
    *,
    jurisdiction_iso2: str | None = None,
    per_backend_timeout: float = 20.0,
    max_records_per_backend: int = 50,
) -> dict:
    """Run every R-F439 backend in parallel for `entity_name`.

    Returns:
      {
        "ok":               bool (True iff at least one backend ok),
        "query":            entity_name,
        "jurisdiction":     jurisdiction_iso2,
        "backends":         {"usaspending": {...}, "uk_contracts_finder": {...}},
        "consolidated":     [<every record from every backend, sorted desc by date>],
        "total_records":    int,
        "by_buyer":         {buyer_name: count},
        "errors":           [str, ...],
      }
    """
    import asyncio
    backends_calls = [
        ("usaspending", query_usaspending(
            entity_name,
            timeout=per_backend_timeout,
            max_records=max_records_per_backend,
        )),
        ("uk_contracts_finder", query_uk_contracts_finder(
            entity_name,
            timeout=per_backend_timeout,
            max_records=max_records_per_backend,
        )),
    ]
    results = await asyncio.gather(
        *(b[1] for b in backends_calls),
        return_exceptions=True,
    )
    backends: dict[str, dict] = {}
    consolidated: list[dict] = []
    errors: list[str] = []
    by_buyer: dict[str, int] = {}
    any_ok = False
    for (label, _), res in zip(backends_calls, results):
        if isinstance(res, BaseException):
            backends[label] = {
                "ok": False,
                "source": label,
                "error": f"unhandled exception: {type(res).__name__}: {str(res)[:120]}",
                "records": [],
            }
            errors.append(f"{label}: {type(res).__name__}")
            continue
        backends[label] = res
        if res.get("ok"):
            any_ok = True
        else:
            err = res.get("error") or "unknown error"
            errors.append(f"{label}: {err}")
        for rec in (res.get("records") or []):
            rec_with_source = dict(rec)
            rec_with_source["source"] = label
            consolidated.append(rec_with_source)
            buyer = rec.get("buyer") or "(unknown)"
            by_buyer[buyer] = by_buyer.get(buyer, 0) + 1
    # Sort consolidated by date_signed desc; missing dates last.
    # Sort key MUST invert the empty-date flag because reverse=True
    # inverts the whole comparison: with (ds == "", ds) and reverse=True
    # the empty-date record's (True, "") would land FIRST, not LAST
    # (verifier-caught hotfix). Using (ds != "", ds) so non-empty
    # records compare True (higher) and reverse=True keeps them first.
    def _sort_key(r):
        ds = r.get("date_signed") or ""
        return (ds != "", ds)
    consolidated.sort(key=_sort_key, reverse=True)
    return {
        "ok":            any_ok,
        "query":         entity_name,
        "jurisdiction":  jurisdiction_iso2,
        "backends":      backends,
        "consolidated":  consolidated[:200],  # final cap
        "total_records": len(consolidated),
        "by_buyer":      by_buyer,
        "errors":        errors,
        "queried_at":    datetime.now(timezone.utc).isoformat(),
    }

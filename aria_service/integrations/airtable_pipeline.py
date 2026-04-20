"""Airtable Pipeline writer — pushes BD opportunities into the existing
Pipeline table in the ARKMURUS base.

Why a separate module from airtable_sync
────────────────────────────────────────
airtable_sync handles the pending_actions → Task Register mirror. This
module targets a different table (Pipeline, tblHoRfsLrJr6BTZK) with a
different schema (Opportunity Name, Our Role, Sector, Stage, Deal Value,
etc.). The PAT + base ID are shared; the field mapping isn't.

Field map (Airtable Pipeline schema discovered 2026-04-20)
──────────────────────────────────────────────────────────
  Opportunity Name   singleLineText (primary)
  Our Role           singleSelect
  Sector             singleSelect
  RFQ | LOI          singleSelect
  Category           singleSelect
  Stage              singleSelect
  Next Action        multilineText
  Date Added         date
  Notes              multilineText
  Deal Value         currency

We only write the fields we have confidence on; Airtable leaves the rest
blank for operator enrichment. No upsert here — every new alert gets a
new row (the Pipeline isn't action_id-keyed; rows are distinct
opportunities, not a ledger of updates).

Killswitch
──────────
  AIRTABLE_SYNC_ENABLED=0   — same as airtable_sync
  AIRTABLE_PIPELINE_TABLE   — override the table name (default "Pipeline")

Public API
──────────
  async create_opportunity(name, notes, **fields) -> dict
  async list_open(limit=25) -> list[dict]   # read-back for dashboards
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx

logger = logging.getLogger("aria.integrations.airtable_pipeline")

_DEFAULT_BASE = "appq2TB9F6NRxAB8f"
_DEFAULT_TABLE = "Pipeline"
_API_ROOT = "https://api.airtable.com/v0"
_HTTP_TIMEOUT = 12.0


def _is_enabled() -> tuple[bool, str]:
    if (os.getenv("AIRTABLE_SYNC_ENABLED", "1") or "1").strip() == "0":
        return False, "killswitch"
    if not (os.getenv("AIRTABLE_PAT") or "").strip():
        return False, "no_pat"
    return True, ""


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {os.environ['AIRTABLE_PAT']}",
        "Content-Type": "application/json",
    }


def _table_url() -> str:
    base = os.getenv("AIRTABLE_BASE_ID", _DEFAULT_BASE)
    table = os.getenv("AIRTABLE_PIPELINE_TABLE", _DEFAULT_TABLE)
    return f"{_API_ROOT}/{base}/{quote(table, safe='')}"


async def create_opportunity(
    name: str,
    *,
    notes: str = "",
    sector: str = "",
    our_role: str = "",
    category: str = "",
    stage: str = "",
    next_action: str = "",
    deal_value: float | None = None,
    date_added: str | None = None,
) -> dict[str, Any]:
    """Create a new row in the Pipeline table. Returns
    {"ok": bool, "reason": str, "record_id": str?}.

    Defaults chosen for BD-alert converted leads:
      - stage / our_role / sector / category left BLANK so the
        operator picks from the existing singleSelect options (the
        PAT scope we have cannot CREATE new options, only SELECT
        existing ones; sending an option name not on the list
        causes a 422 INVALID_MULTIPLE_CHOICE_OPTIONS). Live incident
        2026-04-20: sent stage="IDENTIFIED" to a schema without that
        option and Airtable rejected the whole row.
      - date_added = today UTC
      - Caller can still pass exact option strings if known good.
    """
    ok, why = _is_enabled()
    if not ok:
        return {"ok": False, "reason": f"disabled:{why}"}
    name = (name or "").strip()
    if not name:
        return {"ok": False, "reason": "no_name"}

    fields: dict[str, Any] = {"Opportunity Name": name[:300]}
    if notes:
        fields["Notes"] = notes[:8000]
    if our_role:
        fields["Our Role"] = our_role
    if sector:
        fields["Sector"] = sector
    if category:
        fields["Category"] = category
    if stage:
        fields["Stage"] = stage
    if next_action:
        fields["Next Action"] = next_action[:2000]
    if deal_value is not None:
        fields["Deal Value"] = float(deal_value)
    if date_added:
        fields["Date Added"] = date_added
    else:
        fields["Date Added"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    payload = {"records": [{"fields": fields}]}
    url = _table_url()
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(url, headers=_headers(), json=payload)
    except Exception as e:
        logger.warning("airtable_pipeline network error for %r: %s", name, e)
        return {"ok": False, "reason": f"net:{type(e).__name__}"}

    if resp.status_code == 404:
        logger.warning(
            "airtable_pipeline 404 — table %r in base %r not found",
            os.getenv("AIRTABLE_PIPELINE_TABLE", _DEFAULT_TABLE),
            os.getenv("AIRTABLE_BASE_ID", _DEFAULT_BASE),
        )
        return {"ok": False, "reason": "table_not_found", "http_status": 404}
    if resp.status_code >= 400:
        # 422 on unknown singleSelect options is common; body tells us
        # which field mismatched.
        body = resp.text[:500]
        logger.warning("airtable_pipeline HTTP %d for %r: %s",
                       resp.status_code, name, body)
        return {"ok": False, "reason": f"http_{resp.status_code}",
                "http_status": resp.status_code, "body": body}
    data = resp.json()
    records = data.get("records") or []
    record_id = records[0].get("id") if records else ""
    return {"ok": True, "reason": "created", "http_status": resp.status_code,
            "record_id": record_id}


async def list_open(limit: int = 25) -> list[dict[str, Any]]:
    """Read-back helper — returns the N most recently-created Pipeline
    rows. Useful for dashboard panels that want to show the current
    lead pipeline alongside the ARIA chat UI."""
    ok, why = _is_enabled()
    if not ok:
        return []
    url = f"{_table_url()}?maxRecords={int(limit)}&sort%5B0%5D%5Bfield%5D=Date%20Added&sort%5B0%5D%5Bdirection%5D=desc"
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(url, headers=_headers())
    except Exception as e:
        logger.debug("airtable_pipeline list_open network error: %s", e)
        return []
    if resp.status_code >= 400:
        return []
    data = resp.json()
    rows = data.get("records") or []
    return [
        {
            "record_id": r.get("id"),
            "created_time": r.get("createdTime"),
            **(r.get("fields") or {}),
        }
        for r in rows
    ]

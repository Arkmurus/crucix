"""Airtable one-way sync — pending_actions -> ARKMURUS / Task Register.

Hook wiring
───────────
Calls `sync_record()` fire-and-forget from pending_actions on every
record / mark_satisfied / mark_cancelled. Upsert by Action ID so retries
are idempotent and the same action_id never creates a duplicate row.

Configuration
─────────────
  AIRTABLE_PAT        — Personal Access Token. Required; integration
                        is a silent no-op if missing (keeps local dev
                        from hard-failing).
  AIRTABLE_BASE_ID    — default "appq2TB9F6NRxAB8f" (ARKMURUS).
  AIRTABLE_TASK_TABLE — default "Task Register". Can be flipped to
                        "Housekeeping" or any other existing table.
  AIRTABLE_SYNC_ENABLED — "0" disables the sync entirely (killswitch
                        for when Airtable is rate-limited / unreachable).

Table schema expected (see schema setup note at bottom of file):
  Action ID         : singleLineText (primary)
  Promise           : multilineText
  Severity          : singleSelect (LOW/MEDIUM/HIGH/CRITICAL)
  Resolver Kind     : singleSelect
  Resolver Ref      : singleLineText
  Reason            : multilineText
  Status            : singleSelect (open/satisfied/cancelled)
  Source            : singleLineText
  Operator Prompt   : multilineText
  Recorded          : dateTime (ISO)
  Satisfied At      : dateTime (ISO, may be empty)

If the table doesn't exist yet, every call logs a warning and returns.
Once the table is created (either manually in the Airtable UI or via the
meta API with a PAT that has schema.bases:write scope), sync starts
working automatically — no code change or redeploy needed.
"""
from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import quote

import httpx

logger = logging.getLogger("aria.integrations.airtable")

_DEFAULT_BASE = "appq2TB9F6NRxAB8f"
_DEFAULT_TABLE = "Task Register"
_API_ROOT = "https://api.airtable.com/v0"
_HTTP_TIMEOUT = 12.0


def _is_enabled() -> tuple[bool, str]:
    """Returns (enabled, reason). Reason is empty if enabled."""
    if os.getenv("AIRTABLE_SYNC_ENABLED", "1").strip() == "0":
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
    table = os.getenv("AIRTABLE_TASK_TABLE", _DEFAULT_TABLE)
    return f"{_API_ROOT}/{base}/{quote(table, safe='')}"


def _entry_to_fields(entry: dict) -> dict[str, Any]:
    """Map a pending_actions entry onto the Task Register schema.
    Any field absent from the entry is just omitted (Airtable leaves
    the cell unchanged)."""
    out: dict[str, Any] = {}
    if entry.get("action_id"):
        out["Action ID"] = entry["action_id"]
    if entry.get("promise"):
        out["Promise"] = entry["promise"]
    if entry.get("severity"):
        out["Severity"] = entry["severity"]
    if entry.get("resolver_kind"):
        out["Resolver Kind"] = entry["resolver_kind"]
    if entry.get("resolver_ref"):
        out["Resolver Ref"] = entry["resolver_ref"]
    if entry.get("reason"):
        out["Reason"] = entry["reason"]
    if entry.get("status"):
        out["Status"] = entry["status"]
    if entry.get("source"):
        out["Source"] = entry["source"]
    if entry.get("operator_prompt"):
        out["Operator Prompt"] = entry["operator_prompt"]
    if entry.get("ts"):
        out["Recorded"] = entry["ts"]
    if entry.get("satisfied_at"):
        out["Satisfied At"] = entry["satisfied_at"]
    return out


async def sync_record(entry: dict) -> dict[str, Any]:
    """Upsert a pending_actions entry into the Task Register table, keyed
    on Action ID. Returns {"ok": bool, "reason": str, "http_status": int?}.

    Never raises — callers use this fire-and-forget from hot paths. If
    Airtable is down or the table doesn't exist yet, logs at WARNING
    and returns {"ok": False, "reason": <why>}.
    """
    ok, why = _is_enabled()
    if not ok:
        return {"ok": False, "reason": f"disabled:{why}"}
    action_id = (entry or {}).get("action_id")
    if not action_id:
        return {"ok": False, "reason": "no_action_id"}
    fields = _entry_to_fields(entry)
    if not fields:
        return {"ok": False, "reason": "empty_fields"}

    url = _table_url()
    # Airtable's upsert contract: PATCH with performUpsert.fieldsToMergeOn
    # matches on the specified key column and creates-if-missing.
    payload = {
        "performUpsert": {"fieldsToMergeOn": ["Action ID"]},
        "records": [{"fields": fields}],
    }
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.patch(url, headers=_headers(), json=payload)
    except Exception as e:
        logger.warning("airtable sync network error for %s: %s", action_id, e)
        return {"ok": False, "reason": f"net:{type(e).__name__}"}
    if resp.status_code == 404:
        logger.warning(
            "airtable sync 404 for %s — table %r in base %r not found. "
            "Create it (see module docstring) or set AIRTABLE_TASK_TABLE "
            "to an existing table name.",
            action_id,
            os.getenv("AIRTABLE_TASK_TABLE", _DEFAULT_TABLE),
            os.getenv("AIRTABLE_BASE_ID", _DEFAULT_BASE),
        )
        return {"ok": False, "reason": "table_not_found", "http_status": 404}
    if resp.status_code >= 400:
        logger.warning(
            "airtable sync %d for %s: %s",
            resp.status_code, action_id, resp.text[:200],
        )
        return {"ok": False, "reason": f"http_{resp.status_code}",
                "http_status": resp.status_code, "body": resp.text[:200]}
    return {"ok": True, "reason": "upserted", "http_status": resp.status_code}


async def sync_status_change(action_id: str, new_status: str,
                             satisfied_at: str | None = None,
                             satisfied_note: str = "") -> dict[str, Any]:
    """Thin wrapper for mark_satisfied/mark_cancelled paths — keeps the
    fields update small (no need to re-send the whole entry)."""
    entry = {"action_id": action_id, "status": new_status}
    if satisfied_at:
        entry["satisfied_at"] = satisfied_at
    if satisfied_note:
        entry["operator_prompt"] = satisfied_note  # re-use the free-text field
    return await sync_record(entry)

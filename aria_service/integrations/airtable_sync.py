"""Airtable one-way sync — pending_actions -> ARKMURUS / Task Register.

Wiring
──────
pending_actions.record() / _mark_status() call sync_record(entry)
fire-and-forget. The upsert key is the Action ID, so retries and status
transitions update the same row rather than duplicating it.

Configuration (all via env vars / fly secrets)
──────────────────────────────────────────────
  AIRTABLE_PAT          Personal Access Token. Required. The sync is a
                        silent no-op if missing so local dev doesn't
                        hard-fail. Scopes needed: data.records:read,
                        data.records:write, schema.bases:read (plus
                        schema.bases:write only if ever auto-creating
                        tables — the runtime path doesn't need it).
  AIRTABLE_BASE_ID      Default "appq2TB9F6NRxAB8f" (ARKMURUS base).
  AIRTABLE_TASK_TABLE   Default "Task Register". Can be repointed at
                        any existing table without code changes.
  AIRTABLE_FIELD_MAP    "full"   (default) — 11-field Task Register
                                 schema, PATCH-upsert on Action ID.
                        "compact" — 2-field What/Notes shape, used
                                 when pointing at an existing 3-column
                                 table like Housekeeping. POSTs a new
                                 row per call (no upsert; the primary
                                 key is packed inside the Notes blob).
  AIRTABLE_SYNC_ENABLED "0" disables the whole sync (killswitch for
                        when Airtable is rate-limiting or unreachable).

Task Register schema (full mode)
────────────────────────────────
  Action ID         singleLineText   (primary, upsert key)
  Promise           multilineText
  Severity          singleSelect     LOW / MEDIUM / HIGH / CRITICAL
  Resolver Kind     singleSelect     autonomous_task / operator_action /
                                     background_job / pure_promise
  Resolver Ref      singleLineText
  Reason            multilineText
  Status            singleSelect     open / satisfied / cancelled
  Source            singleLineText
  Operator Prompt   multilineText
  Recorded          dateTime (ISO UTC)
  Satisfied At      dateTime (ISO UTC, may be empty)

If the target table doesn't exist, sync_record returns
{"ok": False, "reason": "table_not_found"} and logs a WARNING — callers
never see an exception. The fix is either (a) create the table, or
(b) set AIRTABLE_TASK_TABLE to an existing one + AIRTABLE_FIELD_MAP=compact.
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

# Field-map modes — some target tables don't have the full 11-field schema.
# "full"    : the 11-field Task Register schema (default)
# "compact" : two-field What/Notes schema — used when we're pointing at
#             Housekeeping (What / Notes / Attachments) because the PAT
#             lacked schema.bases:write scope at the time of rollout and
#             we couldn't create the dedicated Task Register table.
_MODE_FULL = "full"
_MODE_COMPACT = "compact"


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


def _field_map_mode() -> str:
    """Which schema are we targeting? AIRTABLE_FIELD_MAP=compact flips
    to the 2-field Housekeeping shape (What + Notes)."""
    m = (os.getenv("AIRTABLE_FIELD_MAP", "") or "").strip().lower()
    if m in (_MODE_COMPACT, "housekeeping"):
        return _MODE_COMPACT
    return _MODE_FULL


def _compact_notes_block(entry: dict) -> str:
    """Pack the 9 secondary fields (everything except the primary promise
    string) into a single human-readable block for the Housekeeping.Notes
    field. Field order is stable so Airtable's diff view is readable."""
    lines: list[str] = []
    kv = [
        ("Action ID",       entry.get("action_id")),
        ("Severity",        entry.get("severity")),
        ("Status",          entry.get("status")),
        ("Resolver Kind",   entry.get("resolver_kind")),
        ("Resolver Ref",    entry.get("resolver_ref")),
        ("Source",          entry.get("source")),
        ("Recorded",        entry.get("ts")),
        ("Satisfied At",    entry.get("satisfied_at")),
    ]
    for k, v in kv:
        if v:
            lines.append(f"{k}: {v}")
    if entry.get("reason"):
        lines.append("")
        lines.append(f"Reason: {entry['reason']}")
    if entry.get("operator_prompt"):
        lines.append("")
        lines.append(f"Operator Prompt: {entry['operator_prompt']}")
    return "\n".join(lines)


def _entry_to_fields(entry: dict) -> dict[str, Any]:
    """Map a pending_actions entry onto the target table schema.

    Full mode (AIRTABLE_FIELD_MAP=full, default): 11 fields, one per
    pending_actions key. Requires the Task Register table to exist.

    Compact mode (AIRTABLE_FIELD_MAP=compact): 2 fields — Promise →
    What, and everything else → Notes as a formatted multi-line block.
    Used when the target is Housekeeping or any existing 3-column table.
    Any field absent from the entry is just omitted (Airtable leaves
    the cell unchanged)."""
    if _field_map_mode() == _MODE_COMPACT:
        out: dict[str, Any] = {}
        if entry.get("promise"):
            out["What"] = entry["promise"]
        notes = _compact_notes_block(entry)
        if notes:
            out["Notes"] = notes
        return out

    # full-mode 11-field map
    out = {}
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
    mode = _field_map_mode()
    if mode == _MODE_COMPACT:
        # Compact mode has no dedicated "Action ID" column to upsert on —
        # Action ID is packed inside the Notes blob. So we POST a fresh
        # row each call. Status changes become separate rows rather than
        # overwrites; operator sees the progression. Acceptable because
        # compact mode is a temporary fallback until the Task Register
        # table exists with the full schema.
        payload: dict[str, Any] = {"records": [{"fields": fields}]}
        method = "POST"
    else:
        # Full mode — upsert by Action ID so retries are idempotent.
        payload = {
            "performUpsert": {"fieldsToMergeOn": ["Action ID"]},
            "records": [{"fields": fields}],
        }
        method = "PATCH"
    # Airtable enforces 5 requests/second per base. Under bursty autonomous
    # task churn (70+ tasks firing pending_actions.record) we can hit 429
    # and silently drop writes. Two retries with exponential backoff cover
    # the common case; persistent 429 surfaces as a WARN so operator can
    # tune task cadence.
    import asyncio as _aio
    resp = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                if method == "POST":
                    resp = await client.post(url, headers=_headers(), json=payload)
                else:
                    resp = await client.patch(url, headers=_headers(), json=payload)
        except Exception as e:
            # F70 fix 2026-04-28: httpx.ReadTimeout (and several other
            # transport errors) have an empty `str(e)`, so the prior
            # log line printed "airtable sync network error for X:" with
            # nothing after the colon. Always include the exception type
            # name so the operator can tell ReadTimeout from ConnectError
            # at a glance even when the message is blank.
            err_text = str(e) or "(no message)"
            logger.warning(
                "airtable sync network error for %s: %s: %s",
                action_id, type(e).__name__, err_text,
            )
            return {"ok": False, "reason": f"net:{type(e).__name__}"}
        if resp.status_code != 429:
            break
        # 429 → back off and retry; respect Retry-After header if present
        retry_after = resp.headers.get("Retry-After")
        wait_s = float(retry_after) if (retry_after or "").replace(".", "", 1).isdigit() else 0.5 * (2 ** attempt)
        logger.debug("airtable 429 for %s — retry %d/2 in %.1fs", action_id, attempt + 1, wait_s)
        await _aio.sleep(wait_s)
    if resp is not None and resp.status_code == 429:
        logger.warning(
            "airtable sync RATE-LIMITED after 3 attempts for %s — "
            "consider reducing autonomous task concurrency or enabling "
            "AIRTABLE_SYNC_ENABLED=0 as a temporary killswitch",
            action_id,
        )
        return {"ok": False, "reason": "rate_limited", "http_status": 429}
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
    reason = "created" if mode == _MODE_COMPACT else "upserted"
    return {"ok": True, "reason": reason, "http_status": resp.status_code}


async def health_check() -> dict[str, Any]:
    """Read one record from both Task Register + Pipeline to confirm the
    base is reachable and the table names resolve correctly.

    Returns a compact dict suitable for a health endpoint:
        {
          ok: bool,                  # overall (both tables OK)
          enabled: bool,
          base_id: str,
          tables: {
            task_register: {ok, http_status, reason, records_seen, table},
            pipeline:      {ok, http_status, reason, records_seen, table},
          },
        }

    Never raises — every error becomes a dict entry with reason.
    Used by /api/aria/airtable/health + /diagnostic/details so ops can
    see WHY Airtable is silent without digging through DEBUG logs.
    """
    out: dict[str, Any] = {
        "ok": False,
        "enabled": False,
        "base_id": os.getenv("AIRTABLE_BASE_ID", _DEFAULT_BASE),
        "tables": {},
    }
    enabled, why = _is_enabled()
    out["enabled"] = enabled
    if not enabled:
        out["reason"] = why
        return out

    import httpx

    async def _probe_table(table_name: str) -> dict[str, Any]:
        url = f"{_API_ROOT}/{out['base_id']}/{quote(table_name, safe='')}?maxRecords=1"
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as c:
                r = await c.get(url, headers=_headers())
        except Exception as e:
            return {
                "ok": False, "http_status": None,
                "reason": f"net:{type(e).__name__}",
                "records_seen": 0, "table": table_name,
            }
        if r.status_code == 404:
            return {
                "ok": False, "http_status": 404,
                "reason": "table_not_found", "records_seen": 0,
                "table": table_name,
            }
        if r.status_code == 401 or r.status_code == 403:
            return {
                "ok": False, "http_status": r.status_code,
                "reason": "auth_failed", "records_seen": 0,
                "table": table_name,
            }
        if r.status_code != 200:
            return {
                "ok": False, "http_status": r.status_code,
                "reason": f"http_{r.status_code}",
                "body": (r.text or "")[:200],
                "records_seen": 0, "table": table_name,
            }
        try:
            data = r.json()
            recs = data.get("records") or []
        except Exception:
            recs = []
        return {
            "ok": True, "http_status": 200, "reason": None,
            "records_seen": len(recs), "table": table_name,
        }

    task_table = os.getenv("AIRTABLE_TASK_TABLE", _DEFAULT_TABLE)
    pipeline_table = os.getenv("AIRTABLE_PIPELINE_TABLE", "Pipeline")
    out["tables"]["task_register"] = await _probe_table(task_table)
    out["tables"]["pipeline"] = await _probe_table(pipeline_table)
    out["ok"] = bool(
        out["tables"]["task_register"]["ok"]
        and out["tables"]["pipeline"]["ok"]
    )
    return out


async def sync_status_change(action_id: str, new_status: str,
                             satisfied_at: str | None = None,
                             satisfied_note: str = "") -> dict[str, Any]:
    """Minimal status-change variant for ad-hoc / operator use. Production
    callers should prefer `sync_record(full_entry)` — it carries the
    `promise` field that compact mode needs for the What column, and full
    mode doesn't care either way. This wrapper exists for cases where the
    caller genuinely only has the id + new status (e.g. scripts)."""
    entry: dict[str, Any] = {"action_id": action_id, "status": new_status}
    if satisfied_at:
        entry["satisfied_at"] = satisfied_at
    if satisfied_note:
        entry["operator_prompt"] = satisfied_note  # re-use the free-text field
    return await sync_record(entry)

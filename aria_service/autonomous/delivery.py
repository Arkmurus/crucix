"""ARIA Layer 3 — autonomous task result delivery routing.

After execute_task() runs the constitutional pipeline on a synthetic
task message, this module decides where the result goes:

  - mem0          → already done by aria_chat() itself (background
                     summariser fires automatically). Listed in
                     delivery_channels for completeness but no extra
                     work happens here.
  - intel_ledger  → push the brief as a "brain_lead" signal so the
                     rolling 30-day ledger surfaces it on next chat
                     turns and on /admin/brain.
  - whatsapp      → POST the formatted brief to the seenode bridge's
                     /api/wa-listener/send route (which is now auth-
                     gated by the recent _waRequireAuth fix). Skipped
                     if no whatsapp_group_id is configured.

Every delivery action is wrapped in try/except so a failure in one
channel never blocks the others. The function returns a dict mapping
channel name → outcome ("ok", "skipped:reason", "error:message") so
the engine's run record shows exactly what landed where.

Phase 3c-α scope:
  - mem0:        no-op (already handled by aria_chat)
  - intel_ledger: real implementation
  - whatsapp:    real implementation, with DRY_RUN env var override
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

logger = logging.getLogger("aria.autonomous.delivery")


# Env vars
SEENODE_BASE_URL = os.getenv("SEENODE_BASE_URL", "").strip().rstrip("/")
ARIA_INTERNAL_TOKEN = os.getenv("ARIA_INTERNAL_TOKEN", "").strip()
DRY_RUN_DEFAULT = (os.getenv("ARIA_AUTONOMOUS_DRY_RUN", "1") or "1").strip().lower() not in ("0", "false", "no")


def _format_brief_header(task, triggered_flags: list[str]) -> str:
    """Build the ARIA Intelligence Brief header that prepends every
    autonomous result going to WhatsApp / intel ledger."""
    timestamp = time.strftime("%d %b %Y %H:%M UTC", time.gmtime())
    lines = [
        "*ARIA INTELLIGENCE BRIEF*",
        f"_{task.name}_",
        f"Task: `{task.id}`  ·  {timestamp}",
    ]
    if triggered_flags:
        flags = ", ".join(f"`{f}`" for f in triggered_flags[:6])
        lines.append(f"🚨 *ESCALATION* — triggered: {flags}")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


async def _deliver_intel_ledger(task, response_text: str, triggered_flags: list[str]) -> str:
    """Push the brief as a brain_lead signal into the rolling intel
    ledger so future chat turns can cite it as a source."""
    try:
        from ..intel import intel_ledger
    except Exception as e:
        return f"error:intel_ledger import failed: {e}"

    snippet = response_text[:600] if response_text else ""
    if not snippet:
        return "skipped:empty_response"

    signal_payload: dict[str, Any] = {
        "type": "brain_lead",
        "title": f"Autonomous: {task.name}",
        "summary": snippet,
        "source": f"autonomous:{task.id}",
        "tags": list(task.mem0_tags or []),
        "escalation": bool(triggered_flags),
        "triggered_flags": triggered_flags,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    # The intel_ledger module exposes add_signal() (canonical) but the
    # function name has shifted across refactors. Probe for whichever
    # spelling exists at runtime.
    for fn_name in ("add_signal", "store_signal", "ingest_signal", "push_signal"):
        if hasattr(intel_ledger, fn_name):
            try:
                fn = getattr(intel_ledger, fn_name)
                result = fn(signal_payload)
                # Some are async, some are sync — handle both
                import inspect as _inspect
                if _inspect.iscoroutine(result):
                    result = await result
                return "ok"
            except Exception as e:
                logger.warning(
                    "[delivery intel_ledger] %s raised: %s: %s",
                    fn_name, type(e).__name__, e,
                )
                return f"error:{fn_name}: {type(e).__name__}: {e}"
    return "error:no_compatible_add_signal_function"


async def _deliver_whatsapp(task, response_text: str, triggered_flags: list[str]) -> str:
    """POST the formatted brief to the seenode WhatsApp listener.

    Hard fails (returns "skipped:...") on any of:
      - DRY_RUN env var is set
      - SEENODE_BASE_URL not configured
      - ARIA_INTERNAL_TOKEN not configured (would 401 anyway)
      - task has no whatsapp_group_id
    """
    if DRY_RUN_DEFAULT:
        return "skipped:dry_run"
    if not SEENODE_BASE_URL:
        return "skipped:no_seenode_base_url"
    if not ARIA_INTERNAL_TOKEN:
        return "skipped:no_internal_token"
    if not task.whatsapp_group_id:
        return "skipped:no_whatsapp_group_id"

    header = _format_brief_header(task, triggered_flags)
    body = (response_text or "").strip()
    if not body:
        return "skipped:empty_response"
    full_message = f"{header}\n\n{body}"

    payload = {
        "group_id": task.whatsapp_group_id,
        "message": full_message[:4000],  # WhatsApp has a ~4096 char limit per message
    }

    url = f"{SEENODE_BASE_URL}/api/wa-listener/send"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {ARIA_INTERNAL_TOKEN}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if resp.status_code == 200:
                return "ok"
            return f"error:http_{resp.status_code}:{resp.text[:200]}"
    except Exception as e:
        return f"error:{type(e).__name__}:{str(e)[:200]}"


# ── Public entry point ─────────────────────────────────────────────────────

async def deliver(
    *,
    task,
    response_text: str,
    triggered_flags: list[str],
    session_id: str,
) -> dict[str, str]:
    """Route a task result through every channel listed in
    task.delivery_channels. Returns a dict mapping channel → outcome.

    Each channel is independent — a failure in one never blocks the
    others. The full result is returned to execute_task() which
    persists it in the run history.
    """
    out: dict[str, str] = {}
    channels = task.delivery_channels or []

    for channel in channels:
        ch = (channel or "").strip().lower()
        if not ch:
            continue
        try:
            if ch == "mem0":
                # No-op: aria_engine.aria_chat() already fired the mem0
                # background summariser when it produced response_text.
                # The session_id `autonomous:<task>:<date>` ensures the
                # mem0 fact is tagged with the autonomous source.
                out["mem0"] = "ok:auto_via_aria_chat"
            elif ch == "intel_ledger":
                out["intel_ledger"] = await _deliver_intel_ledger(
                    task, response_text, triggered_flags,
                )
            elif ch == "whatsapp":
                out["whatsapp"] = await _deliver_whatsapp(
                    task, response_text, triggered_flags,
                )
            else:
                out[ch] = "error:unknown_channel"
        except Exception as e:
            out[ch] = f"error:{type(e).__name__}:{str(e)[:200]}"

    return out

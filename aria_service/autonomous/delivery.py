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


def _is_dry_run() -> bool:
    """R-F774: read ARIA_AUTONOMOUS_DRY_RUN fresh on every call so a
    runtime flip via fly secrets / `flyctl secrets set` is honored
    without needing a redeploy. Default ON — set to 0/false/no to
    enable real delivery."""
    return (os.getenv("ARIA_AUTONOMOUS_DRY_RUN", "1") or "1").strip().lower() not in ("0", "false", "no")


# Kept for one release as a fallback for any code that still reads the
# module-level constant. Deprecated in favour of _is_dry_run().
DRY_RUN_DEFAULT = _is_dry_run()


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
    if _is_dry_run():
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
    # R-F774: when dry-run is active, route every delivery channel (and
    # the pipeline auto-lead writer below) through the visibility ledger
    # instead of touching real storage. Closes the bug where
    # _deliver_intel_ledger unconditionally wrote signals regardless of
    # DRY_RUN, contradicting the engine spec docs (engine.py:39-41).
    # Operator audits via GET /api/aria/autonomous/dryrun/recent.
    dry_run = _is_dry_run()

    # Check operating mode — suppress external delivery if degraded
    from ..intel import operating_modes as _om
    mode = await _om.get_mode()
    suppress_external = not _om.should_deliver_external(mode)

    out: dict[str, str] = {}
    channels = task.delivery_channels or []

    if dry_run:
        # Record every would-be delivery and short-circuit. Pipeline
        # auto-create is also captured (it's a real write on the live path).
        from . import dryrun_history
        for channel in channels:
            ch = (channel or "").strip().lower()
            if not ch:
                continue
            await dryrun_history.record(
                task_id=task.id,
                task_name=task.name,
                channel=ch,
                would_deliver=True,
                response_snippet=response_text or "",
                triggered_flags=triggered_flags or [],
                reason="dry_run_active",
            )
            out[ch] = "skipped:dry_run"
        await dryrun_history.record(
            task_id=task.id,
            task_name=task.name,
            channel="pipeline",
            would_deliver=True,
            response_snippet=response_text or "",
            triggered_flags=triggered_flags or [],
            reason="dry_run_active",
        )
        out["pipeline"] = "skipped:dry_run"
        return out

    for channel in channels:
        ch = (channel or "").strip().lower()
        if not ch:
            continue
        # In DEGRADED/SUPERVISED/EMERGENCY, suppress WhatsApp delivery
        if ch == "whatsapp" and suppress_external:
            out[ch] = f"suppressed:operating_mode={mode.name}"
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
            error_msg = f"error:{type(e).__name__}:{str(e)[:200]}"
            out[ch] = error_msg
            # Dead letter queue — capture failed delivery for retry
            try:
                from ..intel import dead_letter_queue as _dlq
                await _dlq.enqueue(
                    task_id=task.id,
                    task_name=task.name,
                    result=response_text,
                    channel=ch,
                    error=error_msg,
                )
            except Exception:
                pass  # DLQ itself failing must not cascade

    # ── Auto-create pipeline leads from task outputs ──────────────────────
    # Every autonomous task result is scanned for procurement signals.
    # If found, a lead is auto-created in the deal pipeline.
    try:
        from ..intel import deal_pipeline
        lead = await deal_pipeline.auto_create_from_signal(
            task_id=task.id,
            task_name=task.name,
            response_text=response_text or "",
            triggered_flags=triggered_flags,
        )
        if lead:
            out["pipeline"] = f"ok:lead_created:{lead.id}"
        else:
            out["pipeline"] = "skipped:no_procurement_signal"
    except Exception as e:
        out["pipeline"] = f"error:{type(e).__name__}:{str(e)[:100]}"

    return out

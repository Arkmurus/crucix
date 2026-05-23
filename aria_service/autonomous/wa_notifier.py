"""R-F825 — WhatsApp notifier for ARIA-Coder progress messages.

Operator vision (2026-05-23): "She is now smarter and needs to be
able to reason ... able to update or code herself if something is
missing ... we need her to be spot on the way she communicates the
changes and the updates."

This module is the thin WhatsApp surface ARIACoder uses to send
operator-facing progress messages: when a fix is queued, what stage
it's at, when it ships, when it fails. Separate from
`autonomous/delivery.py` (which is for cron-task outputs).

Configuration
─────────────
  SEENODE_BASE_URL          — URL of the seenode WA bridge
  ARIA_INTERNAL_TOKEN       — auth for the bridge
  ARIA_CODER_WA_GROUP_ID    — default group for coder progress messages
  ARIA_AUTONOMOUS_DRY_RUN   — respected; "1" → log only, no send

Fail-safe by design — every notify() call is wrapped in try/except.
A notifier failure never blocks the pipeline; we'd rather ship a
fix without notification than block on notification.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger("aria.autonomous.wa_notifier")

DEFAULT_TIMEOUT_S = 15.0
WA_MESSAGE_CHAR_LIMIT = 4000  # WhatsApp ~4096 per message; leave headroom


class WANotifier:
    """Thin client wrapping the seenode WA listener endpoint.

    Build once at coder boot; held on the ARIACoder instance as `self.wa`.
    `notify(text)` sends to the default group; `notify(text, group_id=...)`
    overrides per call.
    """

    def __init__(
        self,
        *,
        seenode_base_url: Optional[str] = None,
        internal_token: Optional[str] = None,
        default_group_id: Optional[str] = None,
        dry_run: Optional[bool] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.base_url = (
            seenode_base_url
            or os.environ.get("SEENODE_BASE_URL", "")
        ).rstrip("/")
        self.token = internal_token or os.environ.get("ARIA_INTERNAL_TOKEN", "")
        self.default_group_id = (
            default_group_id
            or os.environ.get("ARIA_CODER_WA_GROUP_ID", "")
        )
        if dry_run is None:
            dry_run = os.environ.get(
                "ARIA_AUTONOMOUS_DRY_RUN", "0",
            ).strip() == "1"
        self.dry_run = dry_run
        self._client = http_client
        self._owns_client = http_client is None

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url and self.token and self.default_group_id)

    async def notify(
        self, text: str, group_id: Optional[str] = None,
    ) -> str:
        """Send `text` to the WhatsApp group. Returns outcome string:
          "ok" | "skipped:<reason>" | "error:<reason>"
        Never raises.
        """
        if not text or not text.strip():
            return "skipped:empty"
        if self.dry_run:
            logger.info("[wa_notifier DRY-RUN] %s", text[:200])
            return "skipped:dry_run"
        if not self.base_url:
            return "skipped:no_seenode_base_url"
        if not self.token:
            return "skipped:no_internal_token"
        gid = group_id or self.default_group_id
        if not gid:
            return "skipped:no_group_id"

        payload = {
            "group_id": gid,
            "message": text[:WA_MESSAGE_CHAR_LIMIT],
        }
        url = f"{self.base_url}/api/wa-listener/send"

        # Build a transient client if none was injected
        if self._client is None:
            try:
                async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT_S) as c:
                    return await self._post(c, url, payload)
            except Exception as e:
                logger.warning("[wa_notifier] transient client error: %s", e)
                return f"error:{type(e).__name__}:{str(e)[:200]}"

        try:
            return await self._post(self._client, url, payload)
        except Exception as e:
            logger.warning("[wa_notifier] notify error: %s", e)
            return f"error:{type(e).__name__}:{str(e)[:200]}"

    async def _post(
        self,
        client: httpx.AsyncClient,
        url: str,
        payload: dict,
    ) -> str:
        resp = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=DEFAULT_TIMEOUT_S,
        )
        if resp.status_code == 200:
            return "ok"
        return f"error:http_{resp.status_code}:{resp.text[:200]}"

    # ── Operator-facing message builders ─────────────────────────────────────
    #
    # Standard message templates so every stage looks consistent in WhatsApp.
    # These are static helpers — callers compose then pass to .notify().

    @staticmethod
    def msg_request_queued(*, fix_id: str, description: str) -> str:
        desc = description[:200] + ("..." if len(description) > 200 else "")
        return (
            f"🚀 *ARIA-Coder request queued*\n\n"
            f"_{desc}_\n\n"
            f"`fix_id: {fix_id}`\n"
            f"Live status: `/api/aria/coder/status/{fix_id}`\n\n"
            f"I'll update you as I work through plan → code → test → ship."
        )

    @staticmethod
    def msg_stage_progress(
        *, fix_id: str, stage: str, message: str,
    ) -> str:
        """Friendly stage banner. Stages with operator-recognisable emojis."""
        EMOJI = {
            "starting":          "🚀",
            "context":           "🔎",
            "planning":          "🧠",
            "writing_code":      "✏️",
            "writing_tests":     "🧪",
            "testing":           "🧪",
            "claude_review":     "🛡️",
            "staging":           "📦",
            "deploying":         "🚢",
            "opening_ticket":    "🎫",
            "done":              "✅",
            "failed":            "❌",
            "awaiting_approval": "⏸",
        }
        em = EMOJI.get(stage, "•")
        return f"{em} *R-F{fix_id}* — `{stage}`\n{message}"

    @staticmethod
    def msg_shipped(
        *,
        r_number: int,
        title: str,
        operator_summary: str,
        files_modified: list[str],
        auto_deployed: bool,
        issue_url: Optional[str] = None,
        elapsed_s: int = 0,
    ) -> str:
        deploy_state = (
            "auto-deployed to main" if auto_deployed
            else "staged for your approval at `/api/aria/self/staged`"
        )
        files_block = ", ".join(f"`{f}`" for f in files_modified[:5])
        if len(files_modified) > 5:
            files_block += f" (+{len(files_modified) - 5} more)"
        audit_block = ""
        if issue_url:
            audit_block = f"\n🎫 Audit: {issue_url}"
        return (
            f"✅ *R-F{r_number} shipped* in {elapsed_s}s\n\n"
            f"*What changed:* {title}\n\n"
            f"_{operator_summary}_\n\n"
            f"*Files:* {files_block}\n"
            f"*State:* {deploy_state}"
            f"{audit_block}"
        )

    @staticmethod
    def msg_failed(*, fix_id: str, reason: str) -> str:
        return (
            f"❌ *ARIA-Coder fix failed* (`{fix_id}`)\n\n"
            f"_{reason[:500]}_\n\n"
            f"You can retry by re-sending the request, or inspect logs "
            f"at `/api/aria/coder/status/{fix_id}`."
        )

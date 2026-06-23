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
import re
from typing import Optional

import httpx
from ..intel.wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.autonomous.wa_notifier")

DEFAULT_TIMEOUT_S = 15.0
WA_MESSAGE_CHAR_LIMIT = 4000  # WhatsApp ~4096 per message; leave headroom


# ── R-F826 (2026-05-23) — PII scrub before WhatsApp send ────────────────────
#
# Audit finding #26 (P2): WANotifier sent raw gap.description and
# plan.approach[:300] to WhatsApp without redaction. A gap of the form
# "Smith@example.com requested DD on XXX LLC (passport AB1234567)" would
# land in the group verbatim — operator-tier PII exposure.
#
# Conservative pattern set: catch the obvious leak shapes (emails,
# phones, passport/ID numbers, long digit runs) WITHOUT shredding
# legitimate operator content (company names, country names, R-numbers,
# git SHAs). Per [[output_harvester]] / earlier work, an aggressive
# `\b[A-Z][a-z]+ [A-Z][a-z]+\b` proper-name regex destroys defence-DD
# content and is rejected here.

# Order matters — labeled-ID patterns run FIRST so e.g. "SSN: 123456789"
# matches the ID_NUMBER pattern before the (greedier) PHONE pattern can
# munch the digits. IBAN before NUM for the same reason.
_PII_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    # Passport / national ID labels — operator usage pattern (MUST be first
    # so the label disambiguates from a phone match)
    (
        re.compile(
            r"\b(?:passport|national\s+id|nin|nric|ssn|tax\s+id)\s*"
            r"[#:.]?\s*[A-Z0-9-]{6,}\b",
            re.IGNORECASE,
        ),
        "[ID_NUMBER]",
    ),
    # Emails — common shape, low false-positive
    (re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-z]{2,}\b"), "[EMAIL]"),
    # IBAN-like long alphanumeric runs (banking detail) — before NUM
    (re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{12,30}\b"), "[IBAN]"),
    # International phone numbers (>= 10 digits, optional + / spaces / dashes).
    # Requires AT LEAST ONE separator char so pure all-digit runs don't
    # match (they go to [NUM] instead). This protects long account
    # numbers + ISO dates with no spaces.
    (
        re.compile(
            r"(?<![A-Za-z0-9])"                # no alphanumeric prefix
            r"\+?\d{1,3}[-.\s]"                 # country code + REQUIRED sep
            r"\(?\d{2,4}\)?[-.\s]?"             # area code
            r"\d{3,4}[-.\s]?\d{3,4}"            # subscriber
            r"(?![A-Za-z0-9])"
        ),
        "[PHONE]",
    ),
    # US-style (NNN) NNN-NNNN catch
    (
        re.compile(
            r"(?<![A-Za-z0-9])"
            r"\(\d{3}\)\s?\d{3}[-.\s]\d{4}"
            r"(?![A-Za-z0-9])"
        ),
        "[PHONE]",
    ),
    # Standalone 12+ digit runs (account numbers, long IDs) — safe because
    # R-numbers are R-Fxxx, commit SHAs are hex letters+digits, composite
    # scores are 0.xxx, dates are YYYY-MM-DD with dashes.
    (re.compile(r"(?<!\d)\d{12,}(?!\d)"), "[NUM]"),
)


@fail_wire(module="wa_notifier", gap_type="agent_cycle_failure")
def scrub_pii(text: str) -> str:
    """Apply the conservative R-F826 PII pattern set. Returns the same
    string if no patterns matched (idempotent + cheap)."""
    if not text:
        return text
    for pattern, replacement in _PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


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
        # R-F839 (2026-05-23): prefer ARIA_WA_INTERNAL_URL (the new,
        # explicit WA-send target — http://aria-wa.internal:5070). Fall
        # back to SEENODE_BASE_URL during rollback. The `seenode_base_url`
        # constructor arg kept for backward-compat with existing callers.
        self.base_url = (
            seenode_base_url
            or os.environ.get("ARIA_WA_INTERNAL_URL", "")
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

    @fail_wire(module="wa_notifier", gap_type="agent_cycle_failure")
    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url and self.token and self.default_group_id)

    @fail_wire(module="wa_notifier", gap_type="agent_cycle_failure")
    async def notify(
        self, text: str, group_id: Optional[str] = None,
        scrub: bool = True,
    ) -> str:
        """Send `text` to the WhatsApp group. Returns outcome string:
          "ok" | "skipped:<reason>" | "error:<reason>"
        Never raises.

        R-F826 (2026-05-23): PII scrub applied by default. Set
        `scrub=False` for callers that explicitly format their own
        sanitized message (e.g. structured stage banners that contain
        no PII by construction).

        R-F1227: wires success/failure to the brain on every notify.
        """
        if not text or not text.strip():
            return "skipped:empty"
        if scrub:
            text = scrub_pii(text)
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

        outcome = ""

        # Build a transient client if none was injected
        if self._client is None:
            try:
                async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT_S) as c:
                    outcome = await self._post(c, url, payload)
            except Exception as e:
                logger.warning("[wa_notifier] transient client error: %s", e)
                outcome = f"error:{type(e).__name__}:{str(e)[:200]}"
        else:
            try:
                outcome = await self._post(self._client, url, payload)
            except Exception as e:
                logger.warning("[wa_notifier] notify error: %s", e)
                outcome = f"error:{type(e).__name__}:{str(e)[:200]}"

        # R-F1227: wire to brain
        try:
            from ..intel.engine_wiring import wire_success, wire_failure
            if outcome == "ok":
                wire_success(
                    module="wa_notifier",
                    summary=f"WA notify sent: {text[:80]}",
                    source_id="wa_notifier:notify",
                )
            else:
                wire_failure(
                    module="wa_notifier",
                    detail=f"WA notify failed: {outcome} — {text[:80]}",
                    gap_type="wa_notification_failure",
                    source="wa_notifier:notify",
                )
        except Exception:
            pass

        return outcome

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

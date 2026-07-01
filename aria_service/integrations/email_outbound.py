"""R-F597 — Outbound email bridge (draft-only by default).

Past observation 2026-05-16: ARIA's self-assessment listed "no outbound
email capability" as gap #3. Verified — `aria_service/learning/
memory_replication.py:407` is the only SMTP caller in the codebase and
it ships state backups to the operator, not arbitrary outbound email.
This module closes that gap.

Autonomy posture (`aria_autonomy_doctrine.md`):
  - "Never auto-sends to a client" — HARD LINE.
  - Internal briefings to the operator address are auto-allowed.

Implementation honours both rules:
  - `compose_draft()` is always available. Returns the rendered message
    for human review; never sends.
  - `send_email()` requires BOTH `ARIA_EMAIL_OUTBOUND_ENABLED=1` AND
    `internal=True`. If `internal=False` (i.e. recipient is not on the
    operator allow-list), the function refuses to send and returns the
    draft instead, regardless of env.
  - The operator allow-list is configured via
    `ARIA_EMAIL_OPERATOR_ALLOWLIST` (comma-separated). When unset,
    falls back to `ARIA_OPERATOR_EMAIL` / `ARIA_SMTP_USER`.

SMTP config reuses the existing env vars from memory_replication.py:
  ARIA_SMTP_HOST | ARIA_EMAIL_HOST                   (default outlook.office365.com)
  ARIA_SMTP_PORT | ARIA_EMAIL_PORT                   (default 465 SSL)
  ARIA_SMTP_USER | ARIA_EMAIL_USER | EMAIL_USER      (login + From)
  ARIA_SMTP_PASS | ARIA_EMAIL_PASS | EMAIL_PASS      (password)

The module logs every draft to a rolling JSONL file at
`./runs/aria_email_outbound.log.jsonl` so the operator has a history of
what ARIA composed but didn't auto-send.
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import time
from typing import Any

logger = logging.getLogger("aria.integrations.email_outbound")

# Module-level constants — small, immutable, no config drift.
_DEFAULT_HOST = "outlook.office365.com"
_DEFAULT_PORT = 465
_LOG_PATH = pathlib.Path("./runs/aria_email_outbound.log.jsonl")
_DRAFT_LOG_MAX_BYTES = 10 * 1024 * 1024  # 10 MB rolling cap


# ── Configuration helpers ──────────────────────────────────────────

def _smtp_host() -> str:
    return (os.getenv("ARIA_SMTP_HOST")
            or os.getenv("ARIA_EMAIL_HOST")
            or _DEFAULT_HOST).strip()


def _smtp_port() -> int:
    return int(os.getenv("ARIA_SMTP_PORT")
               or os.getenv("ARIA_EMAIL_PORT")
               or _DEFAULT_PORT)


def _smtp_user() -> str:
    return (os.getenv("ARIA_SMTP_USER")
            or os.getenv("ARIA_EMAIL_USER")
            or os.getenv("EMAIL_USER")
            or "").strip()


def _smtp_pass() -> str:
    return (os.getenv("ARIA_SMTP_PASS")
            or os.getenv("ARIA_EMAIL_PASS")
            or os.getenv("EMAIL_PASS")
            or "").strip()


def _outbound_enabled() -> bool:
    """The big red switch. Default OFF — even internal sends require
    explicit operator opt-in via env."""
    raw = (os.getenv("ARIA_EMAIL_OUTBOUND_ENABLED") or "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _operator_allowlist() -> set[str]:
    """Recipients that count as 'internal' for auto-send decisions.

    Sourced from `ARIA_EMAIL_OPERATOR_ALLOWLIST` (CSV), falling back to
    `ARIA_OPERATOR_EMAIL` and `ARIA_SMTP_USER`. Lower-cased on read so
    case differences ('Antonio@…' vs 'antonio@…') don't slip past.
    """
    raw = os.getenv("ARIA_EMAIL_OPERATOR_ALLOWLIST") or ""
    addrs = {a.strip().lower() for a in raw.split(",") if a.strip()}
    for key in ("ARIA_OPERATOR_EMAIL", "ARIA_SMTP_USER", "ARIA_EMAIL_USER"):
        val = (os.getenv(key) or "").strip().lower()
        if val:
            addrs.add(val)
    return addrs


def is_internal_recipient(to: str) -> bool:
    """True if `to` is on the operator allow-list — i.e. auto-send safe."""
    if not to:
        return False
    return to.strip().lower() in _operator_allowlist()


# ── Drafting ───────────────────────────────────────────────────────

def compose_draft(
    *,
    to: str,
    subject: str,
    body: str,
    cc: list[str] | None = None,
    attachments: list[dict] | None = None,
    sender_note: str = "",
) -> dict[str, Any]:
    """Return a structured draft. Never sends.

    Use this when the recipient is external/client-facing (Clause 23 +
    autonomy doctrine: ARIA never auto-sends to a client).

    Returns dict shaped:
        {
          "draft": True,
          "from": <smtp_user>,
          "to": <to>,
          "cc": [<addresses>],
          "subject": <subject>,
          "body": <body>,
          "attachments_meta": [{"filename": ..., "size_bytes": ...}],
          "sender_note": <reviewer guidance>,
          "composed_ts": <unix>,
          "smtp_ready": <bool>,
        }
    """
    cc = cc or []
    attachments_meta = []
    for att in attachments or []:
        size = len(att.get("content", b"") or b"")
        attachments_meta.append({
            "filename": att.get("filename", ""),
            "size_bytes": size,
            "maintype": att.get("maintype", "application"),
            "subtype": att.get("subtype", "octet-stream"),
        })

    draft = {
        "draft": True,
        "from": _smtp_user(),
        "to": (to or "").strip(),
        "cc": [c.strip() for c in cc if c.strip()],
        "subject": (subject or "").strip(),
        "body": body or "",
        "attachments_meta": attachments_meta,
        "sender_note": sender_note,
        "composed_ts": time.time(),
        "smtp_ready": bool(_smtp_host() and _smtp_user() and _smtp_pass()),
    }
    _append_log({"kind": "draft", **{k: v for k, v in draft.items() if k != "body"}})
    return draft


# ── Sending (gated) ────────────────────────────────────────────────

def send_email(
    *,
    to: str,
    subject: str,
    body: str,
    cc: list[str] | None = None,
    attachments: list[dict] | None = None,
    internal: bool = False,
    sender_note: str = "",
) -> dict[str, Any]:
    """Send an email or return a draft.

    Gating rules (in priority order):
      1. If `internal=False` → ALWAYS draft-only (autonomy doctrine
         hard line: never auto-send to a client). Returns compose_draft().
      2. If `internal=True` and recipient NOT on operator allow-list →
         refuse + return draft with sender_note explaining the refusal.
      3. If `ARIA_EMAIL_OUTBOUND_ENABLED!=1` → draft-only even for
         internal recipients. Lets the operator stage the bridge
         before turning on auto-send.
      4. If SMTP credentials missing → draft-only with smtp_ready=False.
      5. All gates pass → real SMTP send via existing memory_replication
         transport pattern (SMTP_SSL on port 465, STARTTLS otherwise).

    Returns the same dict shape as compose_draft() with an extra "sent"
    boolean and "delivery_error" string when applicable.
    """
    if not internal:
        d = compose_draft(
            to=to, subject=subject, body=body, cc=cc,
            attachments=attachments,
            sender_note=(sender_note + " | Auto-send refused: external "
                         "recipient (autonomy doctrine hard line)."),
        )
        d["sent"] = False
        d["refusal_reason"] = "external_recipient_autonomy_doctrine"
        return d

    if not is_internal_recipient(to):
        d = compose_draft(
            to=to, subject=subject, body=body, cc=cc,
            attachments=attachments,
            sender_note=(sender_note + f" | Auto-send refused: '{to}' "
                         "not on operator allow-list "
                         "(ARIA_EMAIL_OPERATOR_ALLOWLIST)."),
        )
        d["sent"] = False
        d["refusal_reason"] = "recipient_not_on_allowlist"
        return d

    if not _outbound_enabled():
        d = compose_draft(
            to=to, subject=subject, body=body, cc=cc,
            attachments=attachments,
            sender_note=(sender_note + " | Auto-send refused: "
                         "ARIA_EMAIL_OUTBOUND_ENABLED!=1."),
        )
        d["sent"] = False
        d["refusal_reason"] = "outbound_disabled"
        return d

    host, port = _smtp_host(), _smtp_port()
    user, pwd = _smtp_user(), _smtp_pass()
    if not (host and user and pwd):
        d = compose_draft(
            to=to, subject=subject, body=body, cc=cc,
            attachments=attachments,
            sender_note=(sender_note + " | Auto-send refused: SMTP "
                         "credentials missing (ARIA_SMTP_HOST/USER/PASS)."),
        )
        d["sent"] = False
        d["refusal_reason"] = "smtp_credentials_missing"
        return d

    try:
        import smtplib
        import ssl
        from email.message import EmailMessage

        msg = EmailMessage()
        msg["From"] = user
        msg["To"] = to
        if cc:
            msg["Cc"] = ", ".join(c for c in cc if c)
        msg["Subject"] = subject or "(no subject)"
        msg.set_content(body or "")
        for att in attachments or []:
            content = att.get("content")
            if content is None:
                continue
            msg.add_attachment(
                content,
                maintype=att.get("maintype", "application"),
                subtype=att.get("subtype", "octet-stream"),
                filename=att.get("filename", "attachment.bin"),
            )

        ctx = ssl.create_default_context()
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=45) as s:
                s.login(user, pwd)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=45) as s:
                s.starttls(context=ctx)
                s.login(user, pwd)
                s.send_message(msg)

        out = {
            "draft": False,
            "from": user,
            "to": to,
            "cc": cc or [],
            "subject": subject,
            "attachments_meta": [
                {"filename": a.get("filename", ""),
                 "size_bytes": len(a.get("content", b"") or b"")}
                for a in (attachments or [])
            ],
            "sent": True,
            "composed_ts": time.time(),
            "smtp_ready": True,
        }
        _append_log({"kind": "send", **{k: v for k, v in out.items()}})
        logger.info("R-F597 outbound send → %s (subject=%r)", to, subject[:80])
        # R-F1059 — wire send success to brain
        try:
            from ..intel.engine_wiring import wire_success as _ws
            _ws(
                module="email_outbound",
                summary=f"Email sent to {to}: {subject[:80]}",
                source_id=f"email_outbound:{to}",
            )
        except Exception:
            pass
        return out
    except Exception as exc:
        msg = f"SMTP send failed: {str(exc)[:200]}"
        logger.warning("R-F597 %s", msg)
        # R-F1059 — wire send failure to brain
        try:
            from ..intel.engine_wiring import wire_failure as _wf
            _wf(
                module="email_outbound",
                detail=f"Email to {to} failed: {msg}",
                gap_type="integration_failure",
                source="email_outbound",
            )
        except Exception:
            pass
        d = compose_draft(
            to=to, subject=subject, body=body, cc=cc,
            attachments=attachments,
            sender_note=(sender_note + f" | {msg}"),
        )
        d["sent"] = False
        d["delivery_error"] = msg
        d["refusal_reason"] = "smtp_send_failed"
        return d


# ── Logging ────────────────────────────────────────────────────────

def _append_log(entry: dict[str, Any]) -> None:
    """Append a JSONL entry to the draft/send log. Cheap rotation: when
    the file grows past _DRAFT_LOG_MAX_BYTES, rename to .1 and start fresh.
    """
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            if _LOG_PATH.exists() and _LOG_PATH.stat().st_size > _DRAFT_LOG_MAX_BYTES:
                rotated = _LOG_PATH.with_suffix(_LOG_PATH.suffix + ".1")
                if rotated.exists():
                    rotated.unlink()
                _LOG_PATH.rename(rotated)
        except Exception:
            pass
        line = json.dumps({"ts": time.time(), **entry}, default=str)
        with _LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as exc:
        logger.debug("email_outbound log write failed: %s", exc)

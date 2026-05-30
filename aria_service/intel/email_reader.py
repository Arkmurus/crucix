"""R-F1064 — ARIA Email Reader and Intelligence Extractor.

ARIA can read emails from configured mailboxes, extract intelligence,
and feed findings into her brain. Uses IMAP for reading, SMTP for sending.

Capabilities:
  - Read emails from configured IMAP inboxes
  - Parse and extract: sender, subject, body, attachments, links
  - Classify: intelligence, compliance, business development, spam
  - Extract entities: company names, people, contract numbers, URLs
  - Feed intelligence to brain_hook for learning
  - Draft and send replies via SMTP

Gate: ARIA_EMAIL_READER_ENABLED=1 to enable (default ON).
"""
from __future__ import annotations

import asyncio
import email
import email.utils
import hashlib
import html
import json
import logging
import os
import quopri
import re
import time
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Optional

logger = logging.getLogger("aria.email_reader")

_ENABLED = os.getenv("ARIA_EMAIL_READER_ENABLED", "1") == "1"

# IMAP settings
_IMAP_HOST = os.getenv("ARIA_EMAIL_IMAP_HOST", "")
_IMAP_PORT = int(os.getenv("ARIA_EMAIL_IMAP_PORT", "993"))
_IMAP_USER = os.getenv("ARIA_EMAIL_IMAP_USER", "")
_IMAP_PASS = os.getenv("ARIA_EMAIL_IMAP_PASS", "")
_IMAP_FOLDER = os.getenv("ARIA_EMAIL_IMAP_FOLDER", "INBOX")

# SMTP settings (for sending)
_SMTP_HOST = os.getenv("ARIA_EMAIL_SMTP_HOST", "")
_SMTP_PORT = int(os.getenv("ARIA_EMAIL_SMTP_PORT", "587"))
_SMTP_USER = os.getenv("ARIA_EMAIL_SMTP_USER", "")
_SMTP_PASS = os.getenv("ARIA_EMAIL_SMTP_PASS", "")

# Processing
_POLL_INTERVAL_S = int(os.getenv("ARIA_EMAIL_POLL_INTERVAL", "300"))  # 5 min
_MAX_EMAILS_PER_POLL = int(os.getenv("ARIA_EMAIL_MAX_PER_POLL", "20"))
_SEEN_KEY = "crucix:email_reader:seen_uids"

# Classification patterns
_INTEL_PATTERNS = [
    r"\b(?:contract|award|RFP|RFQ|tender|bid|solicitation)\b",
    r"\b(?:sanctions|OFAC|OFSI|EU|UN)\s+(?:list|update|designation)\b",
    r"\b(?:defence|defense|military|security)\s+(?:contract|deal|export)\b",
    r"\b(?:intelligence|briefing|assessment|analysis)\b",
    r"\b(?:partnership|teaming|MOU|MOA|JV|joint venture)\b",
    r"\b(?:compliance|regulatory|ITAR|EAR|export control)\b",
    r"\b(?:acquisition|merger|acquisition|takeover|invest)\b",
]

_ENTITY_PATTERNS = [
    (r"\b[A-Z][A-Za-z0-9\s&]+(?:Inc|Corp|LLC|Ltd|GmbH|SA|PLC|Pty)\b", "company"),
    (r"\b[A-Z][a-z]+ [A-Z][a-z]+(?: [A-Z][a-z]+)?\b", "person"),
    (r"\b(?:RFP|RFQ|FA|HQ|W56|N000|FA8|SPE[0-9A-Z-]+)\b", "contract_number"),
    (r"https?://[^\s<>\"']+", "url"),
    (r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", "email"),
]


async def read_emails(max_emails: int = _MAX_EMAILS_PER_POLL) -> list[dict]:
    """Read unseen emails from the configured IMAP mailbox.

    Args:
        max_emails: Maximum number of emails to process.

    Returns:
        List of parsed email dicts with keys: uid, subject, from_addr,
        date, body_text, body_html, attachments, classification, entities.
    """
    if not _ENABLED:
        logger.info("[email_reader] disabled — set ARIA_EMAIL_READER_ENABLED=1")
        return []

    if not _IMAP_HOST or not _IMAP_USER:
        logger.warning(
            "[email_reader] IMAP not configured — set ARIA_EMAIL_IMAP_HOST/USER/PASS "
            "on aria-intel to enable email verification for autonomous registration"
        )
        try:
            from .capability_gaps import record_gap
            record_gap(
                gap_id="email_reader_imap_not_configured",
                title="ARIA_EMAIL_IMAP_HOST/USER/PASS not set",
                description=(
                    "Email reader cannot connect to IMAP. "
                    "Set ARIA_EMAIL_IMAP_HOST, ARIA_EMAIL_IMAP_USER, and "
                    "ARIA_EMAIL_IMAP_PASS on aria-intel to enable "
                    "autonomous email verification for portal registration."
                ),
                severity="medium",
            )
        except Exception:
            pass
        return []

    try:
        import imaplib

        seen = await _get_seen_uids()

        # Connect to IMAP
        loop = asyncio.get_running_loop()
        mail = await loop.run_in_executor(None, _connect_imap)
        if not mail:
            return []

        try:
            # Search for unseen messages
            status, messages = await loop.run_in_executor(
                None, lambda: mail.search(None, "UNSEEN"),
            )
            if status != "OK" or not messages[0]:
                logger.debug("[email_reader] no unseen messages")
                return []

            uids = messages[0].split()
            new_uids = [u for u in uids if u.decode() not in seen]

            if not new_uids:
                logger.debug("[email_reader] no new unseen messages")
                return []

            # Process newest first, up to max_emails
            new_uids = new_uids[-max_emails:]
            emails_parsed = []

            for uid_bytes in new_uids:
                uid = uid_bytes.decode()
                try:
                    status, msg_data = await loop.run_in_executor(
                        None, lambda: mail.fetch(uid_bytes, "(RFC822)"),
                    )
                    if status != "OK":
                        continue

                    raw_email = msg_data[0][1]
                    parsed = _parse_email(raw_email, uid)
                    if parsed:
                        parsed["classification"] = _classify_email(parsed)
                        parsed["entities"] = _extract_entities(parsed)
                        emails_parsed.append(parsed)

                    # Mark as seen
                    await _mark_seen(uid)

                except Exception as e:
                    logger.debug("[email_reader] failed to process uid %s: %s", uid, e)

            return emails_parsed

        finally:
            try:
                await loop.run_in_executor(None, mail.logout)
            except Exception:
                pass

    except Exception as e:
        logger.warning("[email_reader] read failed: %s", e)
        return []


def _connect_imap():
    """Connect to IMAP server. Runs in thread pool."""
    import imaplib
    try:
        mail = imaplib.IMAP4_SSL(_IMAP_HOST, _IMAP_PORT)
        mail.login(_IMAP_USER, _IMAP_PASS)
        mail.select(_IMAP_FOLDER)
        return mail
    except Exception as e:
        logger.warning("[email_reader] IMAP connect failed: %s", e)
        return None


def _parse_email(raw_bytes: bytes, uid: str) -> Optional[dict]:
    """Parse raw email bytes into a structured dict."""
    try:
        msg = email.message_from_bytes(raw_bytes)

        # Basic headers
        subject = _decode_header(msg.get("Subject", ""))
        from_addr = _decode_header(msg.get("From", ""))
        to_addr = _decode_header(msg.get("To", ""))
        date_str = msg.get("Date", "")
        message_id = msg.get("Message-ID", "")

        # Parse date
        parsed_date = None
        if date_str:
            try:
                parsed_date = email.utils.parsedate_to_datetime(date_str)
            except Exception:
                parsed_date = datetime.now(timezone.utc)

        # Extract body
        body_text = ""
        body_html = ""
        attachments = []

        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition", ""))

                if "attachment" in content_disposition:
                    filename = part.get_filename()
                    if filename:
                        attachments.append({
                            "filename": _decode_header(filename),
                            "content_type": content_type,
                            "size": len(part.get_payload(decode=True) or b""),
                        })
                elif content_type == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        try:
                            body_text += payload.decode(charset, errors="replace")
                        except (LookupError, UnicodeDecodeError):
                            body_text += payload.decode("utf-8", errors="replace")
                elif content_type == "text/html":
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        try:
                            body_html += payload.decode(charset, errors="replace")
                        except (LookupError, UnicodeDecodeError):
                            body_html += payload.decode("utf-8", errors="replace")
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                try:
                    body_text = payload.decode(charset, errors="replace")
                except (LookupError, UnicodeDecodeError):
                    body_text = payload.decode("utf-8", errors="replace")

        # Clean HTML to text if no plain text version
        if not body_text and body_html:
            body_text = html.strip_tags(body_html)
            # Also decode HTML entities
            body_text = html.unescape(body_text)

        return {
            "uid": uid,
            "message_id": message_id,
            "subject": subject[:500],
            "from_addr": from_addr[:200],
            "to_addr": to_addr[:200],
            "date": parsed_date.isoformat() if parsed_date else "",
            "body_text": body_text[:10000],
            "body_html": body_html[:5000] if body_html else "",
            "attachments": attachments,
            "classification": "unclassified",
            "entities": [],
        }
    except Exception as e:
        logger.debug("[email_reader] parse failed for uid %s: %s", uid, e)
        return None


def _decode_header(header_value: str) -> str:
    """Decode email header with encoded words."""
    if not header_value:
        return ""
    decoded_parts = []
    for part, charset in email.header.decode_header(header_value):
        if isinstance(part, bytes):
            try:
                decoded_parts.append(part.decode(charset or "utf-8", errors="replace"))
            except (LookupError, UnicodeDecodeError):
                decoded_parts.append(part.decode("utf-8", errors="replace"))
        else:
            decoded_parts.append(str(part))
    return " ".join(decoded_parts)


def _classify_email(parsed: dict) -> str:
    """Classify email by content analysis."""
    text = f"{parsed.get('subject', '')} {parsed.get('body_text', '')}".lower()

    # Check for intelligence value
    intel_score = 0
    for pattern in _INTEL_PATTERNS:
        if re.search(pattern, text):
            intel_score += 1

    if intel_score >= 3:
        return "high_value_intelligence"
    elif intel_score >= 1:
        return "intelligence"
    elif any(w in text for w in ["newsletter", "subscription", "weekly digest"]):
        return "newsletter"
    elif any(w in text for w in ["unsubscribe", "marketing", "promotion"]):
        return "marketing"
    elif any(w in text for w in ["meeting", "calendar", "invite"]):
        return "calendar"
    else:
        return "general"


def _extract_entities(parsed: dict) -> list[dict]:
    """Extract entities from email content."""
    text = f"{parsed.get('subject', '')} {parsed.get('body_text', '')}"
    entities = []

    for pattern, etype in _ENTITY_PATTERNS:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            value = match.group().strip()
            if len(value) > 2 and value not in [e["value"] for e in entities]:
                entities.append({
                    "type": etype,
                    "value": value[:200],
                })

    return entities


async def _get_seen_uids() -> set[str]:
    """Get set of already-processed UIDs."""
    try:
        from . import redis_store as _rs
        data = await _rs.get(_SEEN_KEY)
        if data:
            return set(json.loads(data))
    except Exception:
        pass
    return set()


async def _mark_seen(uid: str) -> None:
    """Mark a UID as processed."""
    try:
        from . import redis_store as _rs
        seen = await _get_seen_uids()
        seen.add(uid)
        # Keep only last 10000 UIDs
        if len(seen) > 10000:
            seen = set(list(seen)[-10000:])
        await _rs.set(_SEEN_KEY, json.dumps(list(seen)))
    except Exception as e:
        logger.debug("[email_reader] mark_seen failed: %s", e)


async def feed_to_brain(emails: list[dict]) -> int:
    """Feed extracted intelligence to ARIA's brain.

    Args:
        emails: List of parsed email dicts.

    Returns:
        Number of emails fed to brain.
    """
    fed = 0
    for email_data in emails:
        if email_data.get("classification") in ("high_value_intelligence", "intelligence"):
            try:
                from . import brain_hook as _bh

                summary = (
                    f"Email: {email_data['subject'][:200]} "
                    f"from {email_data['from_addr'][:100]}"
                )
                detail = (
                    f"From: {email_data['from_addr']}\n"
                    f"Subject: {email_data['subject']}\n"
                    f"Date: {email_data['date']}\n"
                    f"Classification: {email_data['classification']}\n"
                    f"Entities: {json.dumps(email_data.get('entities', [])[:10])}\n\n"
                    f"Body:\n{email_data['body_text'][:2000]}"
                )

                await _bh.absorb(
                    module="email_reader",
                    summary=summary,
                    detail=detail,
                    success=True,
                    confidence="ASSESSED",
                )
                fed += 1
            except Exception as e:
                logger.debug("[email_reader] brain feed failed: %s", e)

    return fed


async def poll_loop() -> dict:
    """Main polling loop — read emails, classify, feed to brain.

    Returns:
        Dict with counts of processed, classified, and fed emails.
    """
    emails = await read_emails()
    if not emails:
        return {"processed": 0, "classified": 0, "fed_to_brain": 0}

    classifications = {}
    for e in emails:
        cls = e.get("classification", "unknown")
        classifications[cls] = classifications.get(cls, 0) + 1

    fed = await feed_to_brain(emails)

    logger.info(
        "[email_reader] Processed %d emails: %s. Fed %d to brain.",
        len(emails), classifications, fed,
    )

    # Wire to brain
    try:
        from .engine_wiring import wire_success as _ws
        _ws(
            module="email_reader",
            summary=f"Email poll: {len(emails)} processed, {fed} fed to brain",
            detail=json.dumps(classifications),
            source_id="email_reader:poll",
        )
    except Exception:
        pass

    return {
        "processed": len(emails),
        "classified": classifications,
        "fed_to_brain": fed,
    }


async def send_email(
    to_addr: str,
    subject: str,
    body: str,
    cc: Optional[list[str]] = None,
) -> dict:
    """Send an email via SMTP.

    Args:
        to_addr: Recipient email address.
        subject: Email subject.
        body: Email body (plain text).
        cc: Optional CC recipients.

    Returns:
        Dict with success status.
    """
    if not _SMTP_HOST or not _SMTP_USER:
        return {"success": False, "error": "SMTP not configured"}

    try:
        import smtplib
        import ssl

        msg = MIMEMultipart()
        msg["From"] = _SMTP_USER
        msg["To"] = to_addr
        if cc:
            msg["Cc"] = ", ".join(cc)
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        loop = asyncio.get_running_loop()

        def _send():
            ctx = ssl.create_default_context()
            with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT) as server:
                server.starttls(context=ctx)
                server.login(_SMTP_USER, _SMTP_PASS)
                server.send_message(msg)

        await loop.run_in_executor(None, _send)

        logger.info("[email_reader] Sent email to %s: %s", to_addr, subject[:80])

        try:
            from .engine_wiring import wire_success as _ws
            _ws(
                module="email_reader",
                summary=f"Email sent to {to_addr}: {subject[:80]}",
                source_id=f"email_reader:send:{to_addr}",
            )
        except Exception:
            pass

        return {"success": True, "to": to_addr, "subject": subject}

    except Exception as e:
        logger.warning("[email_reader] send failed: %s", e)
        return {"success": False, "error": str(e)[:200]}


# ── Background loop ────────────────────────────────────────────────────


async def _background_poll_loop() -> None:
    """Background loop that polls email and feeds intelligence to brain."""
    # Initial delay to let the system boot
    await asyncio.sleep(60)
    while True:
        try:
            await poll_loop()
        except Exception as e:
            logger.debug("[email_reader] background poll failed: %s", e)
        await asyncio.sleep(_POLL_INTERVAL_S)


def start_background_polling() -> asyncio.Task:
    """Start the background email polling loop."""
    task = asyncio.create_task(_background_poll_loop())
    logger.info(
        "[email_reader] Background polling started (interval=%ds, host=%s)",
        _POLL_INTERVAL_S, _IMAP_HOST or "not configured",
    )
    return task


# ── Wire to brain ──────────────────────────────────────────────────────

try:
    from .engine_wiring import wire_success as _ws
    _ws(
        module="email_reader",
        summary="Email Reader Engine active",
        detail=f"IMAP: {_IMAP_HOST or 'not configured'}. "
               f"Poll interval: {_POLL_INTERVAL_S}s. "
               f"Gate: ARIA_EMAIL_READER_ENABLED=1",
        source_id="email_reader:R-F1064",
    )
except Exception:
    pass

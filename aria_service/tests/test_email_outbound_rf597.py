"""R-F597 — Outbound email bridge.

Closes the gap surfaced in ARIA's 2026-05-16 self-assessment ("no
outbound email capability"). Module enforces the autonomy doctrine:
client/external comms are ALWAYS draft-only; internal sends require
both ARIA_EMAIL_OUTBOUND_ENABLED=1 and the recipient on the operator
allow-list.
"""
from __future__ import annotations


def test_rf597_compose_draft_renders_minimal_fields(monkeypatch):
    monkeypatch.setenv("ARIA_SMTP_HOST", "mail.livemail.co.uk")
    monkeypatch.setenv("ARIA_SMTP_USER", "aria@arkmurus.com")
    monkeypatch.setenv("ARIA_SMTP_PASS", "stub-password")

    from aria_service.integrations import email_outbound as eo
    d = eo.compose_draft(
        to="client@example.com",
        subject="Hello",
        body="Body text",
    )
    assert d["draft"] is True
    assert d["to"] == "client@example.com"
    assert d["subject"] == "Hello"
    assert d["from"] == "aria@arkmurus.com"
    assert d["smtp_ready"] is True


def test_rf597_send_to_external_recipient_is_draft_only(monkeypatch):
    """Autonomy hard line: client recipient must NEVER auto-send."""
    monkeypatch.setenv("ARIA_SMTP_HOST", "mail.livemail.co.uk")
    monkeypatch.setenv("ARIA_SMTP_USER", "aria@arkmurus.com")
    monkeypatch.setenv("ARIA_SMTP_PASS", "stub-password")
    monkeypatch.setenv("ARIA_EMAIL_OUTBOUND_ENABLED", "1")  # even with switch ON

    from aria_service.integrations import email_outbound as eo
    d = eo.send_email(
        to="client@example.com",
        subject="DD Report",
        body="…",
        internal=False,
    )
    assert d["sent"] is False
    assert d.get("refusal_reason") == "external_recipient_autonomy_doctrine"


def test_rf597_send_internal_not_on_allowlist_is_draft(monkeypatch):
    """internal=True but recipient not on allow-list → still draft."""
    monkeypatch.setenv("ARIA_SMTP_HOST", "mail.livemail.co.uk")
    monkeypatch.setenv("ARIA_SMTP_USER", "aria@arkmurus.com")
    monkeypatch.setenv("ARIA_SMTP_PASS", "stub-password")
    monkeypatch.setenv("ARIA_EMAIL_OPERATOR_ALLOWLIST", "antonio@arkmurus.com")
    monkeypatch.setenv("ARIA_EMAIL_OUTBOUND_ENABLED", "1")

    from aria_service.integrations import email_outbound as eo
    d = eo.send_email(
        to="random@example.com",
        subject="x",
        body="x",
        internal=True,
    )
    assert d["sent"] is False
    assert d.get("refusal_reason") == "recipient_not_on_allowlist"


def test_rf597_send_internal_with_kill_switch_off_is_draft(monkeypatch):
    """internal=True + on allow-list but global kill switch OFF → draft."""
    monkeypatch.setenv("ARIA_SMTP_HOST", "mail.livemail.co.uk")
    monkeypatch.setenv("ARIA_SMTP_USER", "aria@arkmurus.com")
    monkeypatch.setenv("ARIA_SMTP_PASS", "stub-password")
    monkeypatch.setenv("ARIA_EMAIL_OPERATOR_ALLOWLIST", "antonio@arkmurus.com")
    monkeypatch.delenv("ARIA_EMAIL_OUTBOUND_ENABLED", raising=False)

    from aria_service.integrations import email_outbound as eo
    d = eo.send_email(
        to="antonio@arkmurus.com",
        subject="Briefing",
        body="x",
        internal=True,
    )
    assert d["sent"] is False
    assert d.get("refusal_reason") == "outbound_disabled"


def test_rf597_send_when_creds_missing_is_draft(monkeypatch):
    """All gates pass but SMTP creds missing → draft with refusal."""
    monkeypatch.setenv("ARIA_EMAIL_OPERATOR_ALLOWLIST", "antonio@arkmurus.com")
    monkeypatch.setenv("ARIA_EMAIL_OUTBOUND_ENABLED", "1")
    for k in ("ARIA_SMTP_HOST", "ARIA_EMAIL_HOST",
              "ARIA_SMTP_USER", "ARIA_EMAIL_USER", "EMAIL_USER",
              "ARIA_SMTP_PASS", "ARIA_EMAIL_PASS", "EMAIL_PASS"):
        monkeypatch.delenv(k, raising=False)

    from aria_service.integrations import email_outbound as eo
    d = eo.send_email(
        to="antonio@arkmurus.com",
        subject="x",
        body="x",
        internal=True,
    )
    assert d["sent"] is False
    assert d.get("refusal_reason") == "smtp_credentials_missing"


def test_rf597_send_all_gates_pass_invokes_smtp(monkeypatch):
    """Happy path: all gates pass → smtplib called. We patch SMTP_SSL
    so no real network call happens."""
    monkeypatch.setenv("ARIA_SMTP_HOST", "mail.livemail.co.uk")
    monkeypatch.setenv("ARIA_SMTP_USER", "aria@arkmurus.com")
    monkeypatch.setenv("ARIA_SMTP_PASS", "stub-password")
    monkeypatch.setenv("ARIA_SMTP_PORT", "465")
    monkeypatch.setenv("ARIA_EMAIL_OPERATOR_ALLOWLIST", "antonio@arkmurus.com")
    monkeypatch.setenv("ARIA_EMAIL_OUTBOUND_ENABLED", "1")

    sent_records = []

    class _StubSMTPSSL:
        def __init__(self, host, port, context=None, timeout=None):
            self.host = host
            self.port = port

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def login(self, user, pwd):
            sent_records.append(("login", user, pwd[:3]))

        def send_message(self, msg):
            sent_records.append(("send", msg["From"], msg["To"], msg["Subject"]))

    import smtplib
    monkeypatch.setattr(smtplib, "SMTP_SSL", _StubSMTPSSL)

    from aria_service.integrations import email_outbound as eo
    d = eo.send_email(
        to="antonio@arkmurus.com",
        subject="Daily briefing",
        body="Hello operator.",
        internal=True,
    )
    assert d["sent"] is True, f"Send refused: {d.get('refusal_reason')!r}"
    assert any(r[0] == "send" for r in sent_records), "SMTP send_message not called"


def test_rf597_is_internal_recipient_uses_allowlist(monkeypatch):
    monkeypatch.setenv("ARIA_EMAIL_OPERATOR_ALLOWLIST",
                       "antonio@arkmurus.com, ops@arkmurus.com")
    from aria_service.integrations import email_outbound as eo
    assert eo.is_internal_recipient("antonio@arkmurus.com") is True
    assert eo.is_internal_recipient("OPS@arkmurus.com") is True  # case-insensitive
    assert eo.is_internal_recipient("client@external.com") is False
    assert eo.is_internal_recipient("") is False

"""R-F454 — zoom webhook signature verification fails CLOSED when secret unset.

Pre-R-F454 `verify_webhook_signature` returned True when
`_WEBHOOK_SECRET` was empty (default deploy state), so any incoming
POST claiming to be a Zoom webhook was treated as authenticated. The
audit flagged this as a real security hole — if a deploy forgot to
set `ZOOM_WEBHOOK_SECRET`, ARIA would log forged webhook events as
legitimate meetings, transcript ingests, etc.

R-F454 reverses the default to fail-CLOSED with a warning log.
"""
from __future__ import annotations


def test_rf454_fail_closed_when_secret_empty(monkeypatch, caplog):
    """When the webhook secret is unset, verify_webhook_signature MUST
    return False — not True. Pin the security default."""
    from aria_service.intel import zoom_integration

    monkeypatch.setattr(zoom_integration, "_WEBHOOK_SECRET", "")

    import logging
    caplog.set_level(logging.WARNING, logger="aria.intel.zoom")
    result = zoom_integration.verify_webhook_signature(
        request_body=b'{"event":"webinar.ended"}',
        signature="v0=abcdef",
        timestamp="1234567890",
    )
    assert result is False, (
        "R-F454 REGRESSION: webhook accepted ANY signature when secret unset. "
        "Pre-R-F454 this returned True. Security hole reopened."
    )


def test_rf454_warning_logged_on_empty_secret(monkeypatch, caplog):
    """The empty-secret rejection path must emit a warning so the
    operator notices the misconfiguration in logs."""
    from aria_service.intel import zoom_integration

    monkeypatch.setattr(zoom_integration, "_WEBHOOK_SECRET", "")

    import logging
    caplog.set_level(logging.WARNING)
    zoom_integration.verify_webhook_signature(
        request_body=b"{}", signature="sig", timestamp="ts",
    )
    # The warning identifies R-F454 + the env var name
    log_text = " ".join(r.getMessage() for r in caplog.records)
    assert "R-F454" in log_text or "ZOOM_WEBHOOK_SECRET" in log_text, (
        f"R-F454: warning log missing. caplog: {log_text!r}"
    )


def test_rf454_valid_signature_with_secret_set(monkeypatch):
    """Sanity: when the secret IS set and the signature is correct,
    verification still passes — the fail-closed change doesn't break
    legitimate signed webhooks."""
    import hashlib
    import hmac
    from aria_service.intel import zoom_integration

    secret = "test-secret-r-f454"
    monkeypatch.setattr(zoom_integration, "_WEBHOOK_SECRET", secret)

    body = b'{"event":"meeting.ended"}'
    timestamp = "9999999999"
    message = f"v0:{timestamp}:{body.decode('utf-8')}"
    expected = hmac.new(
        secret.encode(), message.encode(), hashlib.sha256
    ).hexdigest()

    assert zoom_integration.verify_webhook_signature(
        request_body=body, signature=f"v0={expected}", timestamp=timestamp,
    ) is True


def test_rf454_invalid_signature_with_secret_set(monkeypatch):
    """Sanity: a wrong signature with a correctly-set secret still
    fails verification — the fail-closed change doesn't degrade real
    signature checking."""
    from aria_service.intel import zoom_integration

    monkeypatch.setattr(zoom_integration, "_WEBHOOK_SECRET", "secret")
    assert zoom_integration.verify_webhook_signature(
        request_body=b"{}", signature="v0=wrongsig", timestamp="123",
    ) is False

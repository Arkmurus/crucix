"""R-F1109 — Capability tests for the autonomous registration pipeline.

Tests the core helpers in portal_registry.py that power the registration
flow: form data building, CSRF token extraction, success detection,
field error extraction, confirmation link extraction, and identity assertion.

These are unit tests for the helper functions. An end-to-end test against
a real portal requires IMAP credentials and Playwright (deployed only).
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from aria_service.intel.portal_registry import (
    PortalDef,
    assert_real_identity,
    _build_form_data,
    _extract_csrf_token,
    _extract_hidden_field,
    _is_registration_successful,
    _extract_field_errors,
    _extract_confirmation_link,
)


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_portal() -> PortalDef:
    return PortalDef(
        id="test_portal",
        name="Test Portal",
        url="https://test.example.com",
        description="A test portal",
        registration_type="email_form",
        signup_fields=[
            ("user[email]", "email", "email"),
            ("user[name]", "text", "name"),
            ("user[org]", "text", "org"),
            ("user[password]", "password", "password"),
            ("user[website]", "text", "website"),
            ("accept_terms", "checkbox", "literal:1"),
        ],
        success_indicator="Account created",
    )


# ── Tests for assert_real_identity ──────────────────────────────────────────

class TestAssertRealIdentity:
    """R-F1106: Real-identity assertion must reject non-arkmurus identities."""

    def test_valid_identity(self):
        valid, reason = assert_real_identity("aria@arkmurus.com", "Arkmurus Group Ltd")
        assert valid is True
        assert "Valid" in reason

    def test_rejects_wrong_domain(self):
        valid, reason = assert_real_identity("aria@gmail.com", "Arkmurus Group Ltd")
        assert valid is False
        assert "arkmurus.com" in reason

    def test_rejects_non_arkmurus_name(self):
        valid, reason = assert_real_identity("aria@arkmurus.com", "Fake Corp Ltd")
        assert valid is False
        assert "Arkmurus" in reason

    def test_rejects_empty_email(self):
        valid, reason = assert_real_identity("", "Arkmurus Group Ltd")
        assert valid is False
        assert "No email" in reason

    def test_rejects_empty_name(self):
        valid, reason = assert_real_identity("aria@arkmurus.com", "")
        assert valid is False
        assert "No name" in reason


# ── Tests for _build_form_data ──────────────────────────────────────────────

class TestBuildFormData:
    """R-F1108: signup_fields schema must map correctly to form values."""

    def test_builds_form_data_from_schema(self):
        fields = [
            ("user[email]", "email", "email"),
            ("user[name]", "text", "name"),
            ("user[org]", "text", "org"),
            ("user[password]", "password", "password"),
            ("user[website]", "text", "website"),
            ("accept_terms", "checkbox", "literal:1"),
        ]
        reg_data = {
            "email": "aria@arkmurus.com",
            "name": "ARIA Research",
            "password": "s3cret!",
        }
        result = _build_form_data(fields, reg_data)
        assert result["user[email]"] == "aria@arkmurus.com"
        assert result["user[name]"] == "ARIA Research"
        assert result["user[org]"] == "Arkmurus Group Ltd"
        assert result["user[password]"] == "s3cret!"
        assert result["user[website]"] == "https://arkmurus.com"
        assert result["accept_terms"] == "1"

    def test_handles_empty_fields(self):
        result = _build_form_data([], {"email": "a@b.com"})
        assert result == {}

    def test_skips_unknown_value_source(self):
        fields = [("field_x", "text", "unknown_source")]
        result = _build_form_data(fields, {"email": "a@b.com"})
        assert result == {}

    def test_literal_values_passed_through(self):
        fields = [("category", "radio", "literal:153")]
        result = _build_form_data(fields, {})
        assert result["category"] == "153"


# ── Tests for _extract_csrf_token ───────────────────────────────────────────

class TestExtractCsrfToken:
    """Must extract CSRF tokens from various framework formats."""

    def test_django_csrf_token(self):
        html = '<input type="hidden" name="csrf_token" value="abc123">'
        assert _extract_csrf_token(html) == "abc123"

    def test_django_csrfmiddlewaretoken(self):
        html = '<input type="hidden" name="csrfmiddlewaretoken" value="xyz789">'
        assert _extract_csrf_token(html) == "xyz789"

    def test_rails_authenticity_token(self):
        html = '<input type="hidden" name="authenticity_token" value="tok_123">'
        assert _extract_csrf_token(html) == "tok_123"

    def test_laravel_token(self):
        html = '<input type="hidden" name="_token" value="laravel_tok">'
        assert _extract_csrf_token(html) == "laravel_tok"

    def test_no_token_returns_none(self):
        assert _extract_csrf_token("<html><body>no form</body></html>") is None


# ── Tests for _extract_hidden_field ─────────────────────────────────────────

class TestExtractHiddenField:
    """Must extract named hidden fields from HTML."""

    def test_extracts_by_name(self):
        html = '<input type="hidden" name="form_build_id" value="build_123">'
        assert _extract_hidden_field(html, "form_build_id") == "build_123"

    def test_returns_none_if_not_found(self):
        assert _extract_hidden_field("<html></html>", "form_id") is None

    def test_extracts_drupal_fields(self):
        html = (
            '<input type="hidden" name="form_id" value="user_register_form">'
            '<input type="hidden" name="honeypot_time" value="abc123">'
            '<input type="hidden" name="pp_version" value="2.0">'
        )
        assert _extract_hidden_field(html, "form_id") == "user_register_form"
        assert _extract_hidden_field(html, "honeypot_time") == "abc123"
        assert _extract_hidden_field(html, "pp_version") == "2.0"


# ── Tests for _is_registration_successful ───────────────────────────────────

class TestIsRegistrationSuccessful:
    """Must correctly detect successful vs failed registration responses."""

    def test_redirect_is_success(self):
        resp = MagicMock(status_code=302, text="")
        assert _is_registration_successful(resp, PortalDef(
            id="t", name="T", url="https://t.com", description="test",
            registration_type="email_form",
        )) is True

    def test_success_indicator_in_body(self):
        resp = MagicMock(status_code=200, text="Account created successfully!")
        portal = PortalDef(
            id="t", name="T", url="https://t.com", description="test",
            registration_type="email_form",
            success_indicator="Account created",
        )
        assert _is_registration_successful(resp, portal) is True

    def test_common_success_patterns(self):
        for pattern in [
            "Thank you for registering",
            "Welcome to our platform",
            "Please check your email to verify",
            "A confirmation email has been sent",
        ]:
            resp = MagicMock(status_code=200, text=pattern)
            portal = PortalDef(
                id="t", name="T", url="https://t.com", description="test",
                registration_type="email_form",
            )
            assert _is_registration_successful(resp, portal) is True, f"Failed for: {pattern}"

    def test_error_page_is_not_success(self):
        resp = MagicMock(status_code=200, text="Error: email already exists")
        portal = PortalDef(
            id="t", name="T", url="https://t.com", description="test",
            registration_type="email_form",
            success_indicator="Account created",
        )
        assert _is_registration_successful(resp, portal) is False

    def test_http_error_is_not_success(self):
        resp = MagicMock(status_code=403, text="Forbidden")
        portal = PortalDef(
            id="t", name="T", url="https://t.com", description="test",
            registration_type="email_form",
        )
        assert _is_registration_successful(resp, portal) is False


# ── Tests for _extract_field_errors ─────────────────────────────────────────

class TestExtractFieldErrors:
    """Must extract error messages from various HTML formats."""

    def test_drupal_error_messages(self):
        html = '<div class="messages messages--error">Email field is required.</div>'
        errors = _extract_field_errors(html)
        assert "Email field is required." in errors

    def test_rails_field_errors(self):
        html = '<div class="field-error">Password is too short</div>'
        errors = _extract_field_errors(html)
        assert "Password is too short" in errors

    def test_no_errors_returns_empty(self):
        assert _extract_field_errors("<html><body>OK</body></html>") == []

    def test_multiple_errors(self):
        html = (
            '<div class="messages messages--error">Email is invalid</div>'
            '<div class="field-error">Password is required</div>'
        )
        errors = _extract_field_errors(html)
        assert len(errors) == 2
        assert "Email is invalid" in errors
        assert "Password is required" in errors


# ── Tests for _extract_confirmation_link ────────────────────────────────────

class TestExtractConfirmationLink:
    """Must extract confirmation/verification links from email text."""

    def test_confirm_link(self):
        text = "Click here to confirm: https://example.com/confirm?token=abc"
        assert _extract_confirmation_link(text) == "https://example.com/confirm?token=abc"

    def test_verify_link(self):
        text = "Verify your email: https://example.com/verify/user/123"
        assert _extract_confirmation_link(text) == "https://example.com/verify/user/123"

    def test_activate_link(self):
        text = "Activate account: https://example.com/activate/abc123"
        assert _extract_confirmation_link(text) == "https://example.com/activate/abc123"

    def test_no_link_returns_none(self):
        assert _extract_confirmation_link("Welcome to our service!") is None

    def test_multiple_links_picks_first(self):
        text = (
            "Confirm: https://example.com/confirm/1 "
            "Also: https://example.com/verify/2"
        )
        link = _extract_confirmation_link(text)
        assert link is not None
        assert "confirm" in link or "verify" in link

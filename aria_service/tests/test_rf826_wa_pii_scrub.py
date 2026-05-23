"""R-F826 — Tests for PII scrub before WhatsApp send.

Audit finding #26: WANotifier was sending raw gap descriptions to
WhatsApp without redaction. This test pins:
  - emails / phones / passport-IDs / IBANs / long digit runs get scrubbed
  - operator content (company names, country names, R-numbers, commit
    SHAs, composite scores, dates) survives unchanged (no false positives)
  - .notify(scrub=False) bypasses the scrub for callers that already
    sanitized their content
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest


def _run(coro):
    return asyncio.run(coro)


class TestScrubPiiHits:
    """Patterns that MUST be scrubbed."""

    def test_email_scrubbed(self) -> None:
        from aria_service.autonomous.wa_notifier import scrub_pii
        out = scrub_pii(
            "Operator requested DD via john.smith@example.com — see thread."
        )
        assert "john.smith@example.com" not in out
        assert "[EMAIL]" in out

    def test_phone_uk_format_scrubbed(self) -> None:
        from aria_service.autonomous.wa_notifier import scrub_pii
        out = scrub_pii("Contact: +44 20 7946 0958 for follow-up.")
        assert "0958" not in out
        assert "[PHONE]" in out

    def test_phone_us_format_scrubbed(self) -> None:
        from aria_service.autonomous.wa_notifier import scrub_pii
        out = scrub_pii("Call (555) 123-4567 for confirmation.")
        assert "1234567" not in out and "4567" not in out
        assert "[PHONE]" in out

    def test_passport_id_scrubbed(self) -> None:
        from aria_service.autonomous.wa_notifier import scrub_pii
        out = scrub_pii("Subject passport: AB1234567")
        assert "AB1234567" not in out
        assert "[ID_NUMBER]" in out

    def test_national_id_scrubbed(self) -> None:
        from aria_service.autonomous.wa_notifier import scrub_pii
        out = scrub_pii("National ID #N12345678 on file")
        assert "N12345678" not in out
        assert "[ID_NUMBER]" in out

    def test_ssn_scrubbed(self) -> None:
        from aria_service.autonomous.wa_notifier import scrub_pii
        out = scrub_pii("SSN: 123456789")
        assert "[ID_NUMBER]" in out

    def test_iban_scrubbed(self) -> None:
        from aria_service.autonomous.wa_notifier import scrub_pii
        # GB IBAN format
        out = scrub_pii("Wire to GB29NWBK60161331926819 by Friday.")
        assert "GB29NWBK60161331926819" not in out
        assert "[IBAN]" in out

    def test_long_digit_run_scrubbed(self) -> None:
        from aria_service.autonomous.wa_notifier import scrub_pii
        # 12+ digits = account-number-shaped
        out = scrub_pii("Account 4532015112830366 needs review.")
        assert "4532015112830366" not in out
        assert "[NUM]" in out

    def test_multiple_pii_in_one_string_all_scrubbed(self) -> None:
        """Compound PII (the auditor's example): all patterns fire."""
        from aria_service.autonomous.wa_notifier import scrub_pii
        out = scrub_pii(
            "smith@example.com requested DD on XXX LLC "
            "(passport AB1234567, phone +44 20 7946 0958)"
        )
        assert "[EMAIL]" in out
        assert "[ID_NUMBER]" in out
        assert "[PHONE]" in out
        # Company name + country survived
        assert "XXX LLC" in out


class TestScrubPiiMisses:
    """Operator content that MUST survive unchanged (no false positives).

    The aggressive `\\b[A-Z][a-z]+ [A-Z][a-z]+\\b` proper-name regex
    from output_harvester would shred all of this. We deliberately
    DON'T use that pattern here."""

    def test_company_names_survive(self) -> None:
        from aria_service.autonomous.wa_notifier import scrub_pii
        text = "British Airways and Saudi Aramco need DD review."
        assert scrub_pii(text) == text

    def test_country_names_survive(self) -> None:
        from aria_service.autonomous.wa_notifier import scrub_pii
        text = "United Kingdom, United Arab Emirates, Saudi Arabia"
        assert scrub_pii(text) == text

    def test_r_numbers_survive(self) -> None:
        from aria_service.autonomous.wa_notifier import scrub_pii
        text = "R-F826 ships PII scrub; R-F827 wires cost cap."
        assert scrub_pii(text) == text

    def test_commit_sha_survives(self) -> None:
        from aria_service.autonomous.wa_notifier import scrub_pii
        text = "Deployed at commit 268df601 by ARIA-Coder."
        assert scrub_pii(text) == text

    def test_composite_score_survives(self) -> None:
        from aria_service.autonomous.wa_notifier import scrub_pii
        text = "Composite score: 0.723 (was 0.668 before R-F748)."
        assert scrub_pii(text) == text

    def test_iso_date_survives(self) -> None:
        from aria_service.autonomous.wa_notifier import scrub_pii
        text = "Audit window: 2026-05-22 to 2026-05-23."
        assert scrub_pii(text) == text

    def test_url_survives(self) -> None:
        from aria_service.autonomous.wa_notifier import scrub_pii
        text = "See https://aria-intel.fly.dev/api/aria/self/staged for review."
        assert scrub_pii(text) == text

    def test_file_paths_survive(self) -> None:
        from aria_service.autonomous.wa_notifier import scrub_pii
        text = "Modified aria_service/intel/knowledge.py at line 248."
        assert scrub_pii(text) == text

    def test_empty_input_returns_empty(self) -> None:
        from aria_service.autonomous.wa_notifier import scrub_pii
        assert scrub_pii("") == ""

    def test_none_safe(self) -> None:
        from aria_service.autonomous.wa_notifier import scrub_pii
        # `not text` short-circuits None too
        assert scrub_pii(None) is None


class TestNotifyAppliesScrubByDefault:
    """End-to-end: WANotifier.notify() scrubs PII before posting."""

    def _make_notifier(self, captured):
        from aria_service.autonomous.wa_notifier import WANotifier

        async def fake_post(url, headers, json, timeout):
            captured["json"] = json
            resp = MagicMock()
            resp.status_code = 200
            return resp

        http = MagicMock()
        http.post = fake_post

        return WANotifier(
            seenode_base_url="https://intel.example.com",
            internal_token="t", default_group_id="g@g.us",
            dry_run=False, http_client=http,
        )

    def test_email_scrubbed_in_outgoing_payload(self) -> None:
        captured = {}
        n = self._make_notifier(captured)
        _run(n.notify("Operator email: smith@example.com on file."))
        assert "smith@example.com" not in captured["json"]["message"]
        assert "[EMAIL]" in captured["json"]["message"]

    def test_scrub_false_bypasses_redaction(self) -> None:
        """CAPABILITY: callers that built a pre-sanitized message can
        skip the scrub by passing scrub=False."""
        captured = {}
        n = self._make_notifier(captured)
        # This caller knows the email is intentional (e.g., a docs
        # snippet) — scrub=False preserves it.
        _run(n.notify(
            "noreply@anthropic.com is the official commit signing email.",
            scrub=False,
        ))
        assert "noreply@anthropic.com" in captured["json"]["message"]

    def test_scrub_default_is_true(self) -> None:
        """Default behaviour MUST be safe — operator who forgets to
        sanitize doesn't leak."""
        captured = {}
        n = self._make_notifier(captured)
        _run(n.notify("Email: bob@example.com"))
        # Default scrub=True → email replaced
        assert "[EMAIL]" in captured["json"]["message"]

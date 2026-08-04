"""R-F1563 — PII redaction of at-rest captured chat text.

Verified audit finding: when ARIA_CHAT_TRAIN_CAPTURE_TEXT is truthy,
chat_audit_log.record_chat stored the raw user_message (<=4000) and
response (<=20000) UNREDACTED with a ~100yr TTL — live PII at rest in
clear. The secret is currently SET on aria-intel.

These tests:
  1. Unit-test the deterministic redact_pii helper (covered PII classes +
     no-false-positive survivors).
  2. CAPABILITY: drive the REAL capture path (record_chat with
     ARIA_CHAT_TRAIN_CAPTURE_TEXT=1 and a message containing an email +
     phone + token) and assert the PERSISTED entry contains placeholders
     and NOT the raw email/phone/token.
  3. Confirm redaction is the DEFAULT when capture is on, and that the
     hash-chain still verifies after redacted capture.
"""
# allowlist-secret-file — R-F3683: this suite must contain credential-SHAPED strings to be worth anything. Every value here is SYNTHETIC; never paste a live credential into a file that has opted out.
from __future__ import annotations

import asyncio

import pytest


def _run(coro):
    return asyncio.run(coro)


# ── Helper unit tests ────────────────────────────────────────────────────
class TestRedactPiiHits:
    def test_email(self) -> None:
        from aria_service.intel.pii_redaction import redact_pii
        out = redact_pii("Reach me at john.smith@example.com today.")
        assert "john.smith@example.com" not in out
        assert "[EMAIL]" in out

    def test_phone_intl(self) -> None:
        from aria_service.intel.pii_redaction import redact_pii
        out = redact_pii("Call +44 20 7946 0958 to confirm.")
        assert "0958" not in out
        assert "[PHONE]" in out

    def test_phone_us(self) -> None:
        from aria_service.intel.pii_redaction import redact_pii
        out = redact_pii("Ring (555) 123-4567 now.")
        assert "4567" not in out
        assert "[PHONE]" in out

    def test_credit_card(self) -> None:
        from aria_service.intel.pii_redaction import redact_pii
        out = redact_pii("Card 4532 0151 1283 0366 on file.")
        assert "4532" not in out
        assert "[CARD]" in out

    def test_iban(self) -> None:
        from aria_service.intel.pii_redaction import redact_pii
        out = redact_pii("Wire to GB29NWBK60161331926819 by Friday.")
        assert "GB29NWBK60161331926819" not in out
        assert "[IBAN]" in out

    def test_national_id_labelled(self) -> None:
        from aria_service.intel.pii_redaction import redact_pii
        out = redact_pii("Subject passport: AB1234567")
        assert "AB1234567" not in out
        assert "[ID_NUMBER]" in out

    def test_ssn_labelled(self) -> None:
        from aria_service.intel.pii_redaction import redact_pii
        out = redact_pii("SSN: 123456789")
        assert "[ID_NUMBER]" in out

    def test_long_digit_run(self) -> None:
        # 12-digit run (below the 13-digit CARD floor) → [NUM]. Either way
        # the security goal — no raw long digit run at rest — is met.
        from aria_service.intel.pii_redaction import redact_pii
        out = redact_pii("Internal ref 100200300400 attached.")
        assert "100200300400" not in out
        assert "[NUM]" in out

    def test_long_digit_run_card_range(self) -> None:
        from aria_service.intel.pii_redaction import redact_pii
        out = redact_pii("Internal ref 100200300400500 attached.")
        # 15 digits falls in the CARD range; still fully redacted.
        assert "100200300400500" not in out
        assert "[CARD]" in out

    def test_openai_key(self) -> None:
        from aria_service.intel.pii_redaction import redact_pii
        out = redact_pii("Use sk-abcdefghijklmnop1234567890 for the call.")
        assert "sk-abcdefghijklmnop1234567890" not in out
        assert "[SECRET]" in out

    def test_anthropic_key(self) -> None:
        from aria_service.intel.pii_redaction import redact_pii
        out = redact_pii("key is sk-ant-api03-AbC123_def-456 ok")
        assert "sk-ant-api03-AbC123_def-456" not in out
        assert "[SECRET]" in out

    def test_github_pat(self) -> None:
        from aria_service.intel.pii_redaction import redact_pii
        out = redact_pii("token ghp_abcdefghijklmnopqrstuvwxyz0123 set")
        assert "ghp_abcdefghijklmnopqrstuvwxyz0123" not in out
        assert "[SECRET]" in out

    def test_bearer_token(self) -> None:
        from aria_service.intel.pii_redaction import redact_pii
        out = redact_pii("Authorization: Bearer eyJhbGciOi.JIUzI1NiIs-abc123")
        assert "eyJhbGciOi.JIUzI1NiIs-abc123" not in out
        assert "[SECRET]" in out

    def test_labelled_secret_assignment(self) -> None:
        from aria_service.intel.pii_redaction import redact_pii
        out = redact_pii("api_key=AbCdEf123456XyZ in config")
        assert "AbCdEf123456XyZ" not in out
        assert "[SECRET]" in out

    def test_compound_all_fire(self) -> None:
        from aria_service.intel.pii_redaction import redact_pii
        out = redact_pii(
            "smith@example.com requested DD on XXX LLC "
            "(passport AB1234567, phone +44 20 7946 0958)"
        )
        assert "[EMAIL]" in out
        assert "[ID_NUMBER]" in out
        assert "[PHONE]" in out
        assert "XXX LLC" in out  # company name survives


class TestRedactPiiMisses:
    """Legitimate content must survive (no false positives)."""

    def test_company_names(self) -> None:
        from aria_service.intel.pii_redaction import redact_pii
        t = "British Airways and Saudi Aramco need DD review."
        assert redact_pii(t) == t

    def test_r_numbers(self) -> None:
        from aria_service.intel.pii_redaction import redact_pii
        t = "R-F1563 ships PII redaction; R-F826 was the WA scrub."
        assert redact_pii(t) == t

    def test_commit_sha(self) -> None:
        from aria_service.intel.pii_redaction import redact_pii
        t = "Deployed at commit 268df601 by ARIA-Coder."
        assert redact_pii(t) == t

    def test_iso_date(self) -> None:
        from aria_service.intel.pii_redaction import redact_pii
        t = "Audit window: 2026-05-22 to 2026-05-23."
        assert redact_pii(t) == t

    def test_composite_score(self) -> None:
        from aria_service.intel.pii_redaction import redact_pii
        t = "Composite score: 0.723 (was 0.668)."
        assert redact_pii(t) == t

    def test_empty(self) -> None:
        from aria_service.intel.pii_redaction import redact_pii
        assert redact_pii("") == ""


# ── CAPABILITY: drive the real record_chat capture path ──────────────────
_RAW_EMAIL = "alice.deal@example.com"
_RAW_PHONE = "+1 415 555 0182"
_RAW_TOKEN = "sk-ant-api03-SECRETvalue123_abcDEF-456"
_RAW_USER = (
    f"Run DD on Acme Holdings. Contact {_RAW_EMAIL}, "
    f"mobile {_RAW_PHONE}. My API key is {_RAW_TOKEN}."
)
_RAW_RESP = (
    f"Acknowledged. I will email {_RAW_EMAIL} and call {_RAW_PHONE}. "
    "Findings: Acme Holdings has no sanctions hits. [CONFIRMED]"
)


def _persisted_entries():
    """Read back what record_chat actually persisted to the audit log."""
    from aria_service.intel import chat_audit_log as cal
    return _run(cal.get_recent(5))


class TestRecordChatRedactsCapturedText:
    def test_capture_on_redacts_persisted_plaintext(self, monkeypatch) -> None:
        """The operator-hit path: capture flag ON. The PERSISTED entry must
        carry placeholders, NOT the raw email/phone/token."""
        from aria_service.intel import chat_audit_log as cal

        monkeypatch.setenv("ARIA_CHAT_TRAIN_CAPTURE_TEXT", "1")
        monkeypatch.delenv("ARIA_CHAT_TRAIN_CAPTURE_REDACT", raising=False)

        entry = _run(cal.record_chat(
            session_id="rf1563-cap",
            user_message=_RAW_USER,
            response_text=_RAW_RESP,
            verification_status="grounded",
        ))

        # Returned entry has the captured fields, redacted.
        assert "user_message" in entry and "response" in entry
        for field in (entry["user_message"], entry["response"]):
            assert _RAW_EMAIL not in field
            assert "555 0182" not in field and "5550182" not in field
            assert _RAW_TOKEN not in field
        assert "[EMAIL]" in entry["user_message"]
        assert "[PHONE]" in entry["user_message"]
        assert "[SECRET]" in entry["user_message"]
        # Non-PII context survives.
        assert "Acme Holdings" in entry["user_message"]

        # And it's the SAME in what was actually written to the store.
        recent = _persisted_entries()
        stored = next(e for e in recent if e.get("session_id") == "rf1563-cap")
        assert _RAW_EMAIL not in stored["user_message"]
        assert _RAW_EMAIL not in stored["response"]
        assert _RAW_TOKEN not in stored["user_message"]
        assert "[EMAIL]" in stored["response"]

    def test_redaction_is_default_when_capture_on(self, monkeypatch) -> None:
        """Capture-without-redaction must NOT be the default: with only
        ARIA_CHAT_TRAIN_CAPTURE_TEXT set, redaction still happens."""
        from aria_service.intel import chat_audit_log as cal

        monkeypatch.setenv("ARIA_CHAT_TRAIN_CAPTURE_TEXT", "true")
        monkeypatch.delenv("ARIA_CHAT_TRAIN_CAPTURE_REDACT", raising=False)

        entry = _run(cal.record_chat(
            session_id="rf1563-default",
            user_message=f"Email {_RAW_EMAIL}",
            response_text="ok",
        ))
        assert _RAW_EMAIL not in entry["user_message"]
        assert "[EMAIL]" in entry["user_message"]

    def test_capture_off_stores_no_raw_text(self, monkeypatch) -> None:
        """Capture flag OFF → no raw fields persisted at all (privacy floor)."""
        from aria_service.intel import chat_audit_log as cal

        monkeypatch.delenv("ARIA_CHAT_TRAIN_CAPTURE_TEXT", raising=False)
        entry = _run(cal.record_chat(
            session_id="rf1563-off",
            user_message=f"Email {_RAW_EMAIL}",
            response_text="ok",
        ))
        assert "user_message" not in entry
        assert "response" not in entry

    def test_chain_still_verifies_after_redacted_capture(self, monkeypatch) -> None:
        """Redaction touches only the post-sign raw copy — chain integrity
        (prev_hash→chain_hash linking) must still pass."""
        from aria_service.intel import chat_audit_log as cal

        monkeypatch.setenv("ARIA_CHAT_TRAIN_CAPTURE_TEXT", "1")
        _run(cal.record_chat(
            session_id="rf1563-chain-a",
            user_message=f"first {_RAW_EMAIL}",
            response_text="r1",
        ))
        _run(cal.record_chat(
            session_id="rf1563-chain-b",
            user_message=f"second {_RAW_PHONE}",
            response_text="r2",
        ))
        result = _run(cal.verify_chain(sample=10))
        assert result["verified"] is True, result.get("breaks")

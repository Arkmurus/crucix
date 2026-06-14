"""R-F1563 — Deterministic, LLM-free PII redaction for at-rest text.

Audit finding (verified 2026-06-14): when ``ARIA_CHAT_TRAIN_CAPTURE_TEXT``
is truthy, ``chat_audit_log.record_chat`` stored the raw user message
(<=4000 chars) and raw response (<=20000 chars) UNREDACTED with a ~100yr
TTL. The secret is currently SET on aria-intel, so live chat content
(names, emails, phones, deal data, secrets) is at rest in clear.

This module provides ``redact_pii(text) -> text``: a cheap, deterministic
regex pass that replaces obvious PII / secret shapes with typed
placeholders (``[EMAIL]``, ``[PHONE]``, ``[CARD]``, ``[IBAN]``,
``[ID_NUMBER]``, ``[NUM]``, ``[SECRET]``). No LLM, no network — safe to
run on every persisted capture.

Pattern philosophy (inherited from the proven R-F826 ``wa_notifier``
set): catch the obvious leak shapes WITHOUT shredding legitimate content
(company names, country names, R-numbers, git SHAs, ISO dates, composite
scores, URLs, file paths). The aggressive ``\\b[A-Z][a-z]+ [A-Z][a-z]+\\b``
proper-name regex is deliberately NOT used — it destroys due-diligence
content with false positives.

Order matters: more specific / higher-entropy patterns run first so a
labelled ID or a secret token is not partially eaten by the greedier
phone / digit-run patterns.
"""
from __future__ import annotations

import re

# Order matters — see module docstring.
_PII_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    # ── Secrets / API keys / tokens ────────────────────────────────────
    # Provider-prefixed keys (OpenAI sk-, Anthropic sk-ant-, GitHub ghp_/
    # gho_/github_pat_, Slack xox*, AWS AKIA, generic Bearer). Run FIRST
    # so the high-entropy tail isn't munched by the NUM/phone patterns.
    (
        re.compile(
            r"\b(?:sk-ant-[A-Za-z0-9_-]{6,}"
            r"|sk-[A-Za-z0-9]{16,}"
            r"|gh[pousr]_[A-Za-z0-9]{16,}"
            r"|github_pat_[A-Za-z0-9_]{20,}"
            r"|xox[baprs]-[A-Za-z0-9-]{8,}"
            r"|AKIA[0-9A-Z]{12,}"
            r")\b"
        ),
        "[SECRET]",
    ),
    # Bearer / token assignments:  "Bearer <token>", "api_key=<token>",
    # "token: <token>" — catch the value, keep the label readable.
    (
        re.compile(
            r"\bBearer\s+[A-Za-z0-9._\-]{12,}\b",
            re.IGNORECASE,
        ),
        "Bearer [SECRET]",
    ),
    (
        re.compile(
            r"\b(api[_-]?key|secret|token|password|passwd|pwd)\b"
            r"(\s*[:=]\s*)"
            r"[\"']?[A-Za-z0-9._\-/+]{8,}[\"']?",
            re.IGNORECASE,
        ),
        r"\1\2[SECRET]",
    ),
    # ── Labelled passport / national-ID numbers ────────────────────────
    # MUST precede PHONE/NUM so the label disambiguates the digits.
    (
        re.compile(
            r"\b(?:passport|national\s+id|nin|nric|ssn|tax\s+id)\s*"
            r"[#:.]?\s*[A-Z0-9-]{6,}\b",
            re.IGNORECASE,
        ),
        "[ID_NUMBER]",
    ),
    # ── Emails ─────────────────────────────────────────────────────────
    (re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-z]{2,}\b"), "[EMAIL]"),
    # ── IBAN-like banking runs — before CARD/NUM ───────────────────────
    (re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{12,30}\b"), "[IBAN]"),
    # ── Credit-card-like 13-19 digit groups (with optional separators) ─
    # Run before the generic NUM so grouped card numbers map to [CARD].
    (
        re.compile(
            r"(?<![A-Za-z0-9])"
            r"(?:\d[ -]?){13,19}"
            r"(?![A-Za-z0-9])"
        ),
        "[CARD]",
    ),
    # ── International phone numbers (need a separator so all-digit runs
    # and ISO dates don't match) ───────────────────────────────────────
    (
        re.compile(
            r"(?<![A-Za-z0-9])"
            r"\+?\d{1,3}[-.\s]"
            r"\(?\d{2,4}\)?[-.\s]?"
            r"\d{3,4}[-.\s]?\d{3,4}"
            r"(?![A-Za-z0-9])"
        ),
        "[PHONE]",
    ),
    # US-style (NNN) NNN-NNNN
    (
        re.compile(
            r"(?<![A-Za-z0-9])"
            r"\(\d{3}\)\s?\d{3}[-.\s]\d{4}"
            r"(?![A-Za-z0-9])"
        ),
        "[PHONE]",
    ),
    # ── Standalone 12+ digit runs (account numbers, long IDs) ──────────
    (re.compile(r"(?<!\d)\d{12,}(?!\d)"), "[NUM]"),
)


def redact_pii(text: str) -> str:
    """Replace obvious PII / secret shapes with typed placeholders.

    Deterministic, cheap, idempotent-ish (re-running on already-redacted
    text leaves placeholders untouched). Returns the input unchanged when
    it is falsy or contains no matches.

    Covers: provider API keys / Bearer tokens / labelled secrets
    (``[SECRET]``), labelled passport/national-ID (``[ID_NUMBER]``),
    emails (``[EMAIL]``), IBANs (``[IBAN]``), credit-card-shaped runs
    (``[CARD]``), international + US phones (``[PHONE]``), and long bare
    digit runs (``[NUM]``).
    """
    if not text:
        return text
    for pattern, replacement in _PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text

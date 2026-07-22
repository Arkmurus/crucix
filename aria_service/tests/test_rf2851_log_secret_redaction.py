"""R-F2851 — credentials must never reach a log handler in plaintext.

Capability test (CLAUDE.md §3c): the broken path is *httpx logging an outbound
request whose query string carries a credential*. These tests drive the real
``httpx`` client (via ``MockTransport``, which still runs ``_send_single_request``
and therefore the real ``logger.info("HTTP Request: ...")`` call) and assert on
the bytes a wrapped handler actually emits.

Negative controls are deliberate: a guard never observed to FAIL is not a guard.
``test_unwrapped_handler_leaks_the_key`` pins the pre-fix behaviour, so if the
redaction is ever silently removed the suite proves the leak returns rather than
going quietly green.
"""

from __future__ import annotations

import io
import logging

import httpx
import pytest

from aria_service.intel.log_redaction import (
    REDACTED,
    RedactingFormatter,
    install_log_redaction,
    redact_secrets,
)

SECRET = "sk-live-abc123def456ghi789"


def _make_logger(name: str, *, redacted: bool):
    """Isolated logger + in-memory handler, mirroring the root INFO setup."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(name)s | %(message)s"))
    if redacted:
        handler.setFormatter(RedactingFormatter(handler.formatter))
    lg = logging.getLogger(name)
    lg.handlers = [handler]
    lg.setLevel(logging.INFO)
    lg.propagate = False
    return lg, stream


def _drive_httpx(logger_name: str) -> None:
    """Make a real httpx request carrying a credential in the query string.

    This is the exact shape of ``tender_monitor._fetch_sam_gov``:
    ``client.get(url, params={"api_key": ...})``.
    """
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text="ok"))
    with httpx.Client(transport=transport) as client:
        client.get(
            "https://api.sam.gov/opportunities/v2/search",
            params={"api_key": SECRET, "limit": 1},
        )


# ── The capability test: the real leak, through the real client ──────────────

def test_httpx_request_log_does_not_leak_credential(monkeypatch):
    """The user-visible symptom: a prod log line containing the API key."""
    lg, stream = _make_logger("httpx", redacted=True)
    try:
        _drive_httpx("httpx")
        emitted = stream.getvalue()
        # The request was logged at all (guard against a vacuous pass where
        # httpx simply did not emit and the assertion below is trivially true).
        assert "HTTP Request" in emitted, f"httpx did not log; test is vacuous: {emitted!r}"
        assert "api.sam.gov" in emitted, "url should still be present and useful"
        assert SECRET not in emitted, f"CREDENTIAL LEAKED: {emitted!r}"
        assert REDACTED in emitted
    finally:
        lg.handlers = []


def test_unwrapped_handler_leaks_the_key():
    """Negative control — proves the guard is what is doing the work.

    Without the redacting formatter the identical path prints the key. If this
    test ever fails, the leak vector changed and the positive test above may be
    passing for the wrong reason.
    """
    lg, stream = _make_logger("httpx", redacted=False)
    try:
        _drive_httpx("httpx")
        assert SECRET in stream.getvalue(), "pre-fix leak no longer reproduces"
    finally:
        lg.handlers = []


# ── install_log_redaction wires the real handlers ───────────────────────────

def test_install_wraps_root_handlers_and_is_idempotent():
    root = logging.getLogger()
    original = list(root.handlers)
    stream = io.StringIO()
    probe = logging.StreamHandler(stream)
    probe.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(probe)
    try:
        first = install_log_redaction()
        assert first >= 1, "install did not wrap the newly added root handler"
        assert isinstance(probe.formatter, RedactingFormatter)
        # Second call must not double-wrap.
        install_log_redaction()
        assert isinstance(probe.formatter.inner, logging.Formatter)
        assert not isinstance(probe.formatter.inner, RedactingFormatter)
    finally:
        root.removeHandler(probe)
        root.handlers = original


def test_child_logger_records_are_redacted_at_the_root_handler():
    """A filter on the *root logger* would NOT see this record.

    ``callHandlers`` applies only each handler's filters to propagated records,
    which is why the fix wraps formatters on handlers rather than adding a
    logger-level filter. This test pins that distinction.
    """
    root = logging.getLogger()
    original = list(root.handlers)
    stream = io.StringIO()
    probe = logging.StreamHandler(stream)
    probe.setFormatter(RedactingFormatter(logging.Formatter("%(message)s")))
    root.handlers = [probe]
    child = logging.getLogger("aria.some.deep.child")
    child.setLevel(logging.INFO)
    child.propagate = True
    child.handlers = []
    try:
        child.info("calling https://api.example.com/v1?api_key=%s&page=2", SECRET)
        emitted = stream.getvalue()
        assert SECRET not in emitted, f"CREDENTIAL LEAKED via propagation: {emitted!r}"
        assert "page=2" in emitted
    finally:
        root.handlers = original


def test_traceback_urls_are_redacted():
    """``exc_text`` is rendered inside format(), after filters would have run."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(RedactingFormatter(logging.Formatter("%(message)s")))
    lg = logging.getLogger("aria.test.rf2851.exc")
    lg.handlers = [handler]
    lg.setLevel(logging.INFO)
    lg.propagate = False
    try:
        try:
            raise RuntimeError(f"failed fetching https://api.sam.gov/x?api_key={SECRET}")
        except RuntimeError:
            lg.exception("fetch failed")
        emitted = stream.getvalue()
        assert SECRET not in emitted, f"CREDENTIAL LEAKED in traceback: {emitted!r}"
    finally:
        lg.handlers = []


# ── redact_secrets unit contract, incl. over-redaction controls ─────────────

@pytest.mark.parametrize(
    "raw",
    [
        f"GET https://api.sam.gov/v2/search?api_key={SECRET}&limit=1",
        f"GET https://x.test/a?token={SECRET}",
        f"GET https://x.test/a?apikey={SECRET}",
        f"GET https://x.test/a?password={SECRET}",
        f"GET https://x.test/a?client_secret={SECRET}",
        f"GET https://s3.eu.amazonaws.com/b/k?X-Amz-Signature={SECRET}",
        f"GET https://s3.eu.amazonaws.com/b/k?X-Amz-Security-Token={SECRET}",
        f"headers={{'authorization': '{SECRET}'}}",
        f"Authorization: Bearer {SECRET}",
    ],
)
def test_redacts_every_credential_shape(raw):
    out = redact_secrets(raw)
    assert SECRET not in out, f"not redacted: {raw!r} -> {out!r}"
    assert REDACTED in out


@pytest.mark.parametrize(
    "raw",
    [
        # Params that merely END in a sensitive word must survive — over-redaction
        # would blind operators to ordinary query logging.
        "GET https://x.test/a?sortkey=name&monkey=3",
        "GET https://x.test/a?keyword=defence",
        "GET https://x.test/search?q=api_key+rotation+policy",
        "plain message with no url at all",
    ],
)
def test_does_not_over_redact(raw):
    assert redact_secrets(raw) == raw, f"over-redacted: {raw!r}"


def test_redaction_is_idempotent():
    once = redact_secrets(f"https://x.test/a?api_key={SECRET}")
    assert redact_secrets(once) == once


def test_multiple_params_all_redacted():
    out = redact_secrets(
        f"https://x.test/a?api_key={SECRET}&limit=5&token={SECRET}&page=2"
    )
    assert SECRET not in out
    assert "limit=5" in out and "page=2" in out
    assert out.count(REDACTED) == 2

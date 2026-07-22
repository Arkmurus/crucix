"""Redact credential-bearing values from every log record before it is emitted.

R-F2851 (2026-07-22). Codex's live-log review found a third-party API key sitting
in plaintext in the production intel log. The mechanism was not a stray
``logger.info(url)`` in our code — it was structural:

* ``main.py`` calls ``logging.basicConfig(level=logging.INFO)``, so the root
  logger emits INFO.
* ``httpx`` logs every request at INFO as
  ``HTTP Request: GET <full-url> "HTTP/1.1 200 OK"``.
* Any call site that puts a credential in the query string therefore prints it.
  ``tender_monitor.py`` does exactly that: ``client.get(url, params={"api_key":
  SAM_GOV_API_KEY, ...})``, and ``SAM_GOV_API_KEY`` is Deployed on aria-intel.

Per CLAUDE.md §1 the fix must eliminate the failure class, not the one call
site: *any* credential reaching *any* logger must be redacted. Rewriting the one
SAM.gov call to send a header would fix one leak and leave the next one to be
discovered by the next auditor.

Design notes (the two things that make this actually work):

1. **We wrap the handlers' formatters, not the loggers' filters.** A
   ``logging.Filter`` attached to the *root logger* does NOT see records that
   propagate up from child loggers — ``Logger.handle`` applies the originating
   logger's filters, then ``callHandlers`` walks the hierarchy applying only each
   *handler's* filters. A root-logger filter would have been a guard that never
   fires on the very records we care about (``httpx``'s).
2. **Formatting, not the raw message, is the choke point.** ``record.exc_text``
   is rendered inside ``Formatter.format``, after filters run, so a URL inside a
   traceback would bypass a filter entirely. Wrapping ``format()`` catches the
   message, the args and the traceback in one place.
"""

from __future__ import annotations

import logging
import re

__all__ = [
    "REDACTED",
    "SENSITIVE_QUERY_PARAMS",
    "redact_secrets",
    "install_log_redaction",
]

REDACTED = "***REDACTED***"

# Query-string parameter names that carry a credential. Matched case-insensitively
# and only when they are a *whole* parameter name (preceded by "?" or "&"), so
# innocuous names that merely end in one of these words — "sortkey=", "monkey=" —
# are left alone.
SENSITIVE_QUERY_PARAMS = (
    "api_key",
    "api-key",
    "apikey",
    "key",
    "token",
    "access_token",
    "auth",
    "auth_token",
    "password",
    "passwd",
    "pwd",
    "secret",
    "client_secret",
    "sig",
    "signature",
    "credential",
    "session",
    # AWS SigV4 presigned-URL parameters. Codex also observed a full signed AWS
    # URL in the log; these are the parts that make such a URL replayable.
    "x-amz-security-token",
    "x-amz-signature",
    "x-amz-credential",
    # ACLED authenticates with email+key, so the email is a credential half here
    # (and is PII regardless).
    "email",
)

_QUERY_PARAM_RE = re.compile(
    r"(?i)([?&](?:" + "|".join(re.escape(p) for p in SENSITIVE_QUERY_PARAMS) + r")=)"
    # Value runs until the next parameter separator or whitespace/quote/bracket
    # that would end the URL inside a log line.
    r"([^&\s\"'<>\]\)]+)"
)

# "Authorization: Bearer <token>" / "authorization=Bearer <token>" in headers that
# get repr()'d into a log line.
_BEARER_RE = re.compile(r"(?i)(bearer\s+)([A-Za-z0-9\-._~+/]{8,}={0,2})")

# Header/dict reprs such as {'authorization': 'abc123'} or x-api-key: abc123.
_HEADER_RE = re.compile(
    r"(?i)(['\"]?(?:authorization|x-api-key|x-auth-token|proxy-authorization)['\"]?\s*[:=]\s*['\"]?)"
    r"([^'\",}\s]{8,})"
)


def redact_secrets(text: str) -> str:
    """Return ``text`` with credential-bearing substrings replaced by ``REDACTED``.

    Idempotent: running it over already-redacted text is a no-op, which matters
    because the same record can pass through more than one wrapped handler.
    """
    if not text:
        return text
    out = _QUERY_PARAM_RE.sub(lambda m: m.group(1) + REDACTED, text)
    out = _BEARER_RE.sub(lambda m: m.group(1) + REDACTED, out)
    out = _HEADER_RE.sub(lambda m: m.group(1) + REDACTED, out)
    return out


class RedactingFormatter(logging.Formatter):
    """Delegates to the handler's real formatter, then redacts the result.

    Wrapping preserves whatever formatter was already configured (uvicorn's
    colourised access formatter, our ``basicConfig`` format string) instead of
    flattening every handler to a single style.
    """

    def __init__(self, inner: logging.Formatter) -> None:  # noqa: D107 - see class docstring
        super().__init__()
        self.inner = inner

    def format(self, record: logging.LogRecord) -> str:
        return redact_secrets(self.inner.format(record))

    # Delegate the rest so callers that introspect the formatter still work.
    def formatTime(self, record, datefmt=None):  # noqa: N802 - stdlib casing
        return self.inner.formatTime(record, datefmt)

    def formatException(self, ei):  # noqa: N802 - stdlib casing
        return redact_secrets(self.inner.formatException(ei))

    def formatStack(self, stack_info):  # noqa: N802 - stdlib casing
        return redact_secrets(self.inner.formatStack(stack_info))

    def __getattr__(self, item):
        # Some libraries introspect their own formatter (uvicorn's
        # ColourizedFormatter exposes ``use_colors``). Only reached when normal
        # attribute lookup fails, so it cannot shadow our own methods.
        return getattr(self.__dict__["inner"], item)


def _wrap_handler(handler: logging.Handler) -> bool:
    """Wrap one handler's formatter. Returns True if it was newly wrapped."""
    existing = handler.formatter
    if isinstance(existing, RedactingFormatter):
        return False  # already installed — keep install() idempotent
    handler.setFormatter(RedactingFormatter(existing or logging.Formatter()))
    return True


def install_log_redaction() -> int:
    """Install redaction on every currently-configured log handler.

    Returns the number of handlers newly wrapped.

    Call this early (import time) *and* again once the server is up: uvicorn
    installs its own handlers when it starts, which can be after our modules are
    imported, so a single early call would miss them.
    """
    wrapped = 0
    seen: set[int] = set()

    candidates = [logging.getLogger()]
    manager_dict = getattr(logging.Logger, "manager").loggerDict
    for name in list(manager_dict.keys()):
        obj = manager_dict.get(name)
        if isinstance(obj, logging.Logger) and obj.handlers:
            candidates.append(obj)

    for lg in candidates:
        for handler in list(lg.handlers):
            if id(handler) in seen:
                continue
            seen.add(id(handler))
            try:
                if _wrap_handler(handler):
                    wrapped += 1
            except Exception:  # pragma: no cover - never let logging setup kill boot
                continue
    return wrapped

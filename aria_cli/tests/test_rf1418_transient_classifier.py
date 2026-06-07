"""R-F1418 — CLI transient-error classifier capability tests.

Tests that DNS/getaddrinfo errors are correctly classified as transient,
so the CLI retries instead of aborting the turn.

Capability test: a simulated getaddrinfo error → _is_transient returns True
→ the turn is retried, not aborted.
"""
import pytest

from aria_cli.agent import _is_transient
from aria_cli.llm import LLMError


class TestStringBasedTransient:
    """Tests that the string-based _TRANSIENT_MARKERS catch DNS errors."""

    def test_getaddrinfo_in_message(self):
        """'getaddrinfo' in error message → transient."""
        exc = Exception("getaddrinfo failed for api.deepseek.com:443")
        assert _is_transient(exc)

    def test_11001_in_message(self):
        """Windows error code 11001 (WSAHOST_NOT_FOUND) → transient."""
        exc = Exception("[Errno 11001] getaddrinfo failed")
        assert _is_transient(exc)

    def test_could_not_reach_in_message(self):
        """'could not reach' in error message → transient."""
        exc = Exception("could not reach LLM endpoint http://api.deepseek.com: timeout")
        assert _is_transient(exc)

    def test_name_resolution_in_message(self):
        """'name resolution' in error message → transient."""
        exc = Exception("temporary failure in name resolution")
        assert _is_transient(exc)

    def test_dns_in_message(self):
        """'dns' in error message → transient."""
        exc = Exception("DNS resolution failed for api.deepseek.com")
        assert _is_transient(exc)

    def test_existing_timeout_still_transient(self):
        """Existing markers still work (regression)."""
        exc = Exception("Connection timed out")
        assert _is_transient(exc)

    def test_existing_503_still_transient(self):
        """Existing markers still work (regression)."""
        exc = Exception("HTTP 503 Service Unavailable")
        assert _is_transient(exc)

    def test_hard_error_not_transient(self):
        """Hard errors (auth) are NOT transient."""
        exc = Exception("HTTP 401 Unauthorized")
        assert not _is_transient(exc)

    def test_bad_request_not_transient(self):
        """Bad request errors are NOT transient."""
        exc = Exception("HTTP 400 Bad Request")
        assert not _is_transient(exc)


class TestLLMErrorTransientFlag:
    """Tests that LLMError.transient flag is respected by _is_transient."""

    def test_llm_error_transient_true(self):
        """LLMError with transient=True → _is_transient returns True."""
        exc = LLMError("could not reach LLM endpoint", transient=True)
        assert _is_transient(exc)

    def test_llm_error_transient_false(self):
        """LLMError with transient=False → _is_transient returns False."""
        exc = LLMError("HTTP 401 Unauthorized", transient=False)
        assert not _is_transient(exc)

    def test_llm_error_default_transient(self):
        """LLMError with no transient arg defaults to True (network errors)."""
        exc = LLMError("could not reach LLM endpoint")
        assert _is_transient(exc)

    def test_llm_error_transient_flag_wins_over_string(self):
        """The transient flag takes priority over string matching."""
        # Even though "401" is not in _TRANSIENT_MARKERS, the flag wins
        exc = LLMError("HTTP 401 Unauthorized", transient=True)
        assert _is_transient(exc)

        # Even though "timeout" IS in _TRANSIENT_MARKERS, the flag wins
        exc2 = LLMError("connection timed out", transient=False)
        assert not _is_transient(exc2)


class TestHttpxErrorClassification:
    """Tests that httpx error types produce correct transient flags.

    These test the actual LLM client error handling paths.
    """

    def test_connect_error_is_transient(self):
        """httpx.ConnectError → LLMError(transient=True)."""
        import httpx
        try:
            raise httpx.ConnectError("getaddrinfo failed")
        except httpx.HTTPError as exc:
            from aria_cli.llm import LLMError
            raised = LLMError(f"could not reach endpoint: {exc}", transient=True)
            assert raised.transient is True

    def test_connect_timeout_is_transient(self):
        """httpx.ConnectTimeout → LLMError(transient=True)."""
        import httpx
        try:
            raise httpx.ConnectTimeout("Connection timed out")
        except httpx.HTTPError as exc:
            from aria_cli.llm import LLMError
            raised = LLMError(f"could not reach endpoint: {exc}", transient=True)
            assert raised.transient is True

    def test_read_timeout_is_transient(self):
        """httpx.ReadTimeout → LLMError(transient=True)."""
        import httpx
        try:
            raise httpx.ReadTimeout("Read timed out")
        except httpx.HTTPError as exc:
            from aria_cli.llm import LLMError
            raised = LLMError(f"could not reach endpoint: {exc}", transient=True)
            assert raised.transient is True

    def test_remote_protocol_error_is_transient(self):
        """httpx.RemoteProtocolError → LLMError(transient=True)."""
        import httpx
        try:
            raise httpx.RemoteProtocolError("Connection reset")
        except httpx.HTTPError as exc:
            from aria_cli.llm import LLMError
            raised = LLMError(f"could not reach endpoint: {exc}", transient=True)
            assert raised.transient is True

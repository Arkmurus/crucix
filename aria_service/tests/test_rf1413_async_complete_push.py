"""R-F1413 — async-complete-and-push capability tests.

Tests that the brain fires a callback URL when an async chat job completes,
so deep queries deliver even after the WA listener's poll loop times out.

Capability test: a completed async chat job triggers a POST to the callback URL
with the result. The callback delivers the answer to the intended recipient.
"""
from unittest.mock import AsyncMock, patch

import pytest

from aria_service.routes.aria import _fire_and_forget_callback, _is_callback_allowed

# Allowlisted URLs for testing
_ALLOWED_URL = "http://aria-wa.internal:5070/api/wa-listener/send"
_ALLOWED_CALLBACK_URL = "http://aria-wa.internal:5070/api/wa-listener/callback"


# ── SSRF guard tests ────────────────────────────────────────────────────────

class TestSsrfGuard:
    """Tests that the SSRF allowlist rejects arbitrary callback URLs."""

    def test_allowlist_allows_internal_wa(self):
        """Internal WA listener URLs are allowed."""
        assert _is_callback_allowed("http://aria-wa.internal:5070/api/wa-listener/send")
        assert _is_callback_allowed("http://aria-wa.internal:5070/api/wa-listener/callback")

    def test_allowlist_allows_localhost(self):
        """Localhost URLs are allowed (dev mode)."""
        assert _is_callback_allowed("http://localhost:5070/api/wa-listener/send")
        assert _is_callback_allowed("http://localhost:5070/api/wa-listener/callback")

    def test_allowlist_rejects_arbitrary_url(self):
        """Arbitrary external URLs are rejected."""
        assert not _is_callback_allowed("http://evil.com/callback")
        assert not _is_callback_allowed("http://callback.example/send")
        assert not _is_callback_allowed("http://192.168.1.1:8000/hook")

    def test_allowlist_rejects_wrong_port(self):
        """Same host but wrong port is rejected."""
        assert not _is_callback_allowed("http://aria-wa.internal:9999/api/wa-listener/send")

    def test_allowlist_rejects_wrong_path(self):
        """Same host but wrong path is rejected."""
        assert not _is_callback_allowed("http://aria-wa.internal:5070/evil")


@pytest.mark.asyncio
async def test_callback_skipped_for_non_allowlisted_url():
    """_fire_and_forget_callback skips non-allowlisted URLs without calling them."""
    mock_post = AsyncMock()
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.post = mock_post

    with patch("httpx.AsyncClient", return_value=mock_client):
        await _fire_and_forget_callback(
            callback_url="http://evil.com/callback",
            job_id="test-job-evil",
            result_data={"response": "should not be sent"},
            error_msg=None,
            session_id="test-session",
        )
        # POST should NOT have been called
        mock_post.assert_not_awaited()


# ── Callback delivery tests ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_callback_posts_result_to_url():
    """_fire_and_forget_callback POSTs the result to the callback URL."""
    mock_post = AsyncMock()
    mock_post.return_value.status_code = 200
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.post = mock_post

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = {"response": "The answer is 42.", "sources": []}
        await _fire_and_forget_callback(
            callback_url=_ALLOWED_URL,
            job_id="test-job-123",
            result_data=result,
            error_msg=None,
            session_id="test-session",
        )

        mock_post.assert_awaited_once()
        call_url, call_kwargs = mock_post.call_args
        assert call_url[0] == _ALLOWED_URL
        body = call_kwargs.get("json", {})
        assert body["job_id"] == "test-job-123"
        assert body["status"] == "done"
        assert body["session_id"] == "test-session"
        assert body["result"] == result
        assert body["message"] == "The answer is 42."


@pytest.mark.asyncio
async def test_callback_posts_failure():
    """_fire_and_forget_callback POSTs failure status when job errors."""
    mock_post = AsyncMock()
    mock_post.return_value.status_code = 200
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.post = mock_post

    with patch("httpx.AsyncClient", return_value=mock_client):
        await _fire_and_forget_callback(
            callback_url=_ALLOWED_URL,
            job_id="test-job-fail",
            result_data=None,
            error_msg="LLM timeout after 60s",
            session_id="test-session",
        )

        mock_post.assert_awaited_once()
        call_url, call_kwargs = mock_post.call_args
        body = call_kwargs.get("json", {})
        assert body["status"] == "failed"
        assert body["error"] == "LLM timeout after 60s"
        assert "message" not in body


@pytest.mark.asyncio
async def test_callback_retries_on_failure():
    """_fire_and_forget_callback retries 3x on transient failure."""
    mock_post = AsyncMock(side_effect=Exception("Connection refused"))
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.post = mock_post

    with patch("httpx.AsyncClient", return_value=mock_client):
        await _fire_and_forget_callback(
            callback_url=_ALLOWED_URL,
            job_id="test-job-retry",
            result_data={"response": "ok"},
            error_msg=None,
            session_id="test-session",
        )
        # Should have been called 3 times (initial + 2 retries)
        assert mock_post.await_count == 4


@pytest.mark.asyncio
async def test_callback_succeeds_on_second_attempt():
    """_fire_and_forget_callback stops retrying after first success."""
    call_count = 0

    async def _succeed_on_second(url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("First attempt failed")
        return AsyncMock(status_code=200)

    mock_post = AsyncMock(side_effect=_succeed_on_second)
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.post = mock_post

    with patch("httpx.AsyncClient", return_value=mock_client):
        await _fire_and_forget_callback(
            callback_url=_ALLOWED_URL,
            job_id="test-job-succeed2",
            result_data={"response": "ok"},
            error_msg=None,
            session_id="test-session",
        )
        # Should have been called exactly 2 times (first failed, second succeeded)
        assert mock_post.await_count == 2


@pytest.mark.asyncio
async def test_callback_extracts_answer_from_result():
    """The callback extracts the answer text from result.response or result.answer."""
    mock_post = AsyncMock()
    mock_post.return_value.status_code = 200
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.post = mock_post

    with patch("httpx.AsyncClient", return_value=mock_client):
        # Test with 'response' key
        result = {"response": "Answer from response key", "sources": []}
        await _fire_and_forget_callback(
            callback_url=_ALLOWED_URL,
            job_id="test-job-resp",
            result_data=result,
            error_msg=None,
            session_id="test-session",
        )
        call_url, call_kwargs = mock_post.call_args
        assert call_kwargs["json"]["message"] == "Answer from response key"

        # Test with 'answer' key
        mock_post.reset_mock()
        result2 = {"answer": "Answer from answer key"}
        await _fire_and_forget_callback(
            callback_url=_ALLOWED_URL,
            job_id="test-job-ans",
            result_data=result2,
            error_msg=None,
            session_id="test-session",
        )
        call_url, call_kwargs = mock_post.call_args
        assert call_kwargs["json"]["message"] == "Answer from answer key"


# ── Model tests ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chat_request_model_has_callback_url():
    """ChatRequest model includes the callback_url field."""
    from aria_service.routes.aria import ChatRequest

    req = ChatRequest(message="test", callback_url=_ALLOWED_URL)
    assert req.callback_url == _ALLOWED_URL

    req2 = ChatRequest(message="test")
    assert req2.callback_url == ""  # default is empty string


@pytest.mark.asyncio
async def test_async_chat_serializes_callback_url():
    """ChatRequest serializes callback_url correctly."""
    from aria_service.routes.aria import ChatRequest

    req = ChatRequest(
        message="test query",
        session_id="test-session",
        async_mode=True,
        callback_url=_ALLOWED_URL,
    )
    data = req.model_dump()
    assert data["async_mode"] is True
    assert data["callback_url"] == _ALLOWED_URL
    assert data["message"] == "test query"

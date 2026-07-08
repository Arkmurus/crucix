"""R-F2478 — the 2captcha API key must travel in the POST BODY, never the URL
query string. httpx logs request URLs (and exceptions embed them), so `key` in
the query string leaked the live 2captcha API key into aria-intel logs.
"""
from unittest.mock import AsyncMock, patch

import pytest

from aria_service.intel import captcha_solver as cs


@pytest.mark.asyncio
async def test_rf2478_2captcha_key_in_body_not_url():
    calls = []

    class _Resp:
        def json(self):
            return {"status": 1, "request": "SOLVED_TOKEN"}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kwargs):
            calls.append((str(url), kwargs))
            return _Resp()

    with patch.object(cs.httpx, "AsyncClient", lambda *a, **k: _Client()), \
         patch.object(cs.asyncio, "sleep", new=AsyncMock(return_value=None)):
        provider = cs.TwoCaptchaProvider("SECRET_KEY_123")
        token = await provider._solve("recaptcha", {"method": "userrecaptcha"})

    assert token == "SOLVED_TOKEN"
    assert calls, "no HTTP calls captured"
    for url, kwargs in calls:
        # The key must never be in the URL or in `params` (which httpx renders
        # into the logged request URL) — only in the POST body (`data`).
        assert "SECRET_KEY_123" not in url, f"key leaked into URL: {url}"
        assert "SECRET_KEY_123" not in str(kwargs.get("params", "")), \
            f"key in params -> becomes logged URL query: {kwargs}"
        assert "SECRET_KEY_123" in str(kwargs.get("data", "")), \
            f"key must be in POST body (data): {kwargs}"

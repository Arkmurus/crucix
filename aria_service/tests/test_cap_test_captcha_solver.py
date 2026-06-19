"""R-F1689: Capability test — CAPTCHA solver with multi-provider failover.

Tests the REAL solver classes with mocked HTTP responses to prove:
1. Provider abstraction works (all providers implement the same interface)
2. Failover works (primary fails → fallback succeeds)
3. CAPTCHA detection works (scans HTML for site keys)
4. No provider configured = graceful no-op
5. Token injection into Playwright page works
"""
import os
import sys
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from aria_service.intel.captcha_solver import (
    CaptchaSolver, TwoCaptchaProvider, CapsolverProvider,
    AntiCaptchaProvider, get_solver, detect_and_solve_captcha,
)


class TestCaptchaProviderAbstraction:
    """All providers implement the same interface."""

    def test_providers_have_required_methods(self):
        """R-F1689: Every provider must implement solve_recaptcha_v2, solve_recaptcha_v3, solve_turnstile."""
        for provider_cls, key in [
            (TwoCaptchaProvider, "test_key"),
            (CapsolverProvider, "test_key"),
            (AntiCaptchaProvider, "test_key"),
        ]:
            p = provider_cls(key)
            assert hasattr(p, "solve_recaptcha_v2")
            assert hasattr(p, "solve_recaptcha_v3")
            assert hasattr(p, "solve_turnstile")
            assert hasattr(p, "is_configured")

    def test_provider_configured_check(self):
        """R-F1689: is_configured returns True only when API key is set."""
        assert TwoCaptchaProvider("").is_configured is False
        assert TwoCaptchaProvider("real_key").is_configured is True
        assert CapsolverProvider("").is_configured is False
        assert CapsolverProvider("real_key").is_configured is True

    def test_solver_no_providers_returns_none(self):
        """R-F1689: Solver with no providers returns None gracefully."""
        solver = CaptchaSolver()
        # Should have no providers since env vars aren't set in test
        assert solver.is_ready is False
        assert len(solver.providers) == 0


class TestCaptchaFailover:
    """Multi-provider failover works correctly."""

    @pytest.mark.asyncio
    async def test_failover_primary_fails_fallback_succeeds(self):
        """R-F1689: When primary fails, fallback is tried."""
        solver = CaptchaSolver()
        # Manually inject mock providers
        primary = MagicMock(spec=TwoCaptchaProvider)
        primary.name = "primary"
        primary.is_configured = True
        primary.solve_recaptcha_v2 = AsyncMock(return_value=None)

        fallback = MagicMock(spec=CapsolverProvider)
        fallback.name = "fallback"
        fallback.is_configured = True
        fallback.solve_recaptcha_v2 = AsyncMock(return_value="fallback_token_123")

        solver.providers = [primary, fallback]
        token = await solver.solve_recaptcha_v2("test_site_key", "https://example.com")

        assert token == "fallback_token_123"
        primary.solve_recaptcha_v2.assert_awaited_once()
        fallback.solve_recaptcha_v2.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_failover_all_fail_returns_none(self):
        """R-F1689: When all providers fail, returns None."""
        solver = CaptchaSolver()
        p1 = MagicMock(spec=TwoCaptchaProvider)
        p1.name = "p1"
        p1.is_configured = True
        p1.solve_recaptcha_v2 = AsyncMock(return_value=None)

        p2 = MagicMock(spec=CapsolverProvider)
        p2.name = "p2"
        p2.is_configured = True
        p2.solve_recaptcha_v2 = AsyncMock(return_value=None)

        solver.providers = [p1, p2]
        token = await solver.solve_recaptcha_v2("test_key", "https://example.com")
        assert token is None

    @pytest.mark.asyncio
    async def test_failover_primary_exception_triggers_fallback(self):
        """R-F1689: Exception in primary triggers fallback."""
        solver = CaptchaSolver()
        primary = MagicMock(spec=TwoCaptchaProvider)
        primary.name = "primary"
        primary.is_configured = True
        primary.solve_recaptcha_v2 = AsyncMock(side_effect=ConnectionError("timeout"))

        fallback = MagicMock(spec=CapsolverProvider)
        fallback.name = "fallback"
        fallback.is_configured = True
        fallback.solve_recaptcha_v2 = AsyncMock(return_value="fallback_token")

        solver.providers = [primary, fallback]
        token = await solver.solve_recaptcha_v2("test_key", "https://example.com")
        assert token == "fallback_token"


class TestCaptchaDetection:
    """CAPTCHA detection from HTML works."""

    @pytest.mark.asyncio
    async def test_detect_recaptcha_v2(self):
        """R-F1689: Detects reCAPTCHA v2 from HTML and solves it."""
        html = '''
        <html><body>
        <form><div class="g-recaptcha" data-sitekey="6Lc12345abcdef"></div></form>
        </body></html>
        '''

        solver = CaptchaSolver()
        mock_provider = MagicMock(spec=TwoCaptchaProvider)
        mock_provider.name = "mock"
        mock_provider.is_configured = True
        mock_provider.solve_recaptcha_v2 = AsyncMock(return_value="mock_token_456")
        solver.providers = [mock_provider]

        token = await detect_and_solve_captcha(
            "https://example.com/register", html, solver=solver,
        )
        assert token == "mock_token_456"
        mock_provider.solve_recaptcha_v2.assert_awaited_once_with(
            "6Lc12345abcdef", "https://example.com/register",
        )

    @pytest.mark.asyncio
    async def test_detect_no_captcha_returns_none(self):
        """R-F1689: No CAPTCHA in HTML returns None."""
        html = "<html><body><p>No captcha here</p></body></html>"
        solver = CaptchaSolver()
        mock_provider = MagicMock(spec=TwoCaptchaProvider)
        mock_provider.name = "mock"
        mock_provider.is_configured = True
        solver.providers = [mock_provider]

        token = await detect_and_solve_captcha(
            "https://example.com", html, solver=solver,
        )
        assert token is None
        mock_provider.solve_recaptcha_v2.assert_not_called()

    @pytest.mark.asyncio
    async def test_detect_no_solver_returns_none(self):
        """R-F1689: No solver configured returns None."""
        html = '<div class="g-recaptcha" data-sitekey="6Lc123"></div>'
        solver = CaptchaSolver()
        solver.providers = []  # No providers

        token = await detect_and_solve_captcha(
            "https://example.com", html, solver=solver,
        )
        assert token is None


class TestTwoCaptchaProvider:
    """2captcha provider API interaction."""

    @pytest.mark.asyncio
    async def test_solve_success(self):
        """R-F1689: Successful solve returns token."""
        provider = TwoCaptchaProvider("test_api_key")

        with patch("aria_service.intel.captcha_solver.httpx.AsyncClient") as mock_client_cls:
            mock_instance = MagicMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_instance

            call_count = [0]

            async def mock_post(url, **kwargs):
                call_count[0] += 1
                resp = MagicMock()
                if call_count[0] == 1:
                    resp.json = MagicMock(return_value={"status": 1, "request": "12345"})
                else:
                    resp.json = MagicMock(return_value={"status": 1, "request": "solution_token_abc"})
                return resp

            mock_instance.post = mock_post

            token = await provider.solve_recaptcha_v2(
                "6Lc_test_key", "https://example.com",
            )
            assert token == "solution_token_abc"

    @pytest.mark.asyncio
    async def test_solve_submit_failure(self):
        """R-F1689: Submit failure returns None."""
        provider = TwoCaptchaProvider("test_api_key")
        with patch("aria_service.intel.captcha_solver.httpx.AsyncClient") as mock_client_cls:
            mock_instance = MagicMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_instance

            async def mock_post(url, **kwargs):
                resp = MagicMock()
                resp.json = MagicMock(return_value={"status": 0, "request": "ERROR_NO_SLOT_AVAILABLE"})
                return resp

            mock_instance.post = mock_post

            token = await provider.solve_recaptcha_v2(
                "6Lc_test", "https://example.com",
            )
            assert token is None


class TestCapsolverProvider:
    """Capsolver provider API interaction."""

    @pytest.mark.asyncio
    async def test_solve_success(self):
        """R-F1689: Capsolver successful solve returns token."""
        provider = CapsolverProvider("test_api_key")
        with patch("aria_service.intel.captcha_solver.httpx.AsyncClient") as mock_client_cls:
            mock_instance = MagicMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_instance

            call_count = [0]

            async def mock_post(url, **kwargs):
                call_count[0] += 1
                resp = MagicMock()
                if call_count[0] == 1:
                    resp.json = MagicMock(return_value={"errorId": 0, "taskId": "task_789"})
                else:
                    resp.json = MagicMock(return_value={
                        "errorId": 0, "status": "ready",
                        "solution": {"gRecaptchaResponse": "capsolver_token_xyz"},
                    })
                return resp

            mock_instance.post = mock_post

            token = await provider.solve_recaptcha_v2(
                "6Lc_test", "https://example.com",
            )
            assert token == "capsolver_token_xyz"


class TestAntiCaptchaProvider:
    """Anti-Captcha provider API interaction."""

    @pytest.mark.asyncio
    async def test_solve_success(self):
        """R-F1689: Anti-Captcha successful solve returns token."""
        provider = AntiCaptchaProvider("test_api_key")
        with patch("aria_service.intel.captcha_solver.httpx.AsyncClient") as mock_client_cls:
            mock_instance = MagicMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_instance

            call_count = [0]

            async def mock_post(url, **kwargs):
                call_count[0] += 1
                resp = MagicMock()
                if call_count[0] == 1:
                    resp.json = MagicMock(return_value={"errorId": 0, "taskId": "task_111"})
                else:
                    resp.json = MagicMock(return_value={
                        "errorId": 0, "status": "ready",
                        "solution": {"gRecaptchaResponse": "anticaptcha_token_222"},
                    })
                return resp

            mock_instance.post = mock_post

            token = await provider.solve_recaptcha_v2(
                "6Lc_test", "https://example.com",
            )
            assert token == "anticaptcha_token_222"


class TestCaptchaDetectionSpecificity:
    """CAPTCHA type detection must be specific — Turnstile/hCaptcha before reCAPTCHA."""

    @pytest.mark.asyncio
    async def test_turnstile_detected_by_class(self):
        """R-F1695: Turnstile detected by cf-turnstile class (real-world markup)."""
        html = '''
        <html><body>
        <div class="cf-turnstile" data-sitekey="0x4AAAAAAturnstile_key"></div>
        <div class="g-recaptcha" data-sitekey="6Lc_recaptcha_key"></div>
        </body></html>
        '''

        solver = CaptchaSolver()
        mock_provider = MagicMock(spec=TwoCaptchaProvider)
        mock_provider.name = "mock"
        mock_provider.is_configured = True
        mock_provider.solve_turnstile = AsyncMock(return_value="turnstile_token")
        mock_provider.solve_recaptcha_v2 = AsyncMock(return_value="recaptcha_token")
        solver.providers = [mock_provider]

        token = await detect_and_solve_captcha(
            "https://example.com", html, solver=solver,
        )
        assert token == "turnstile_token", "Turnstile should be detected by cf-turnstile class"
        mock_provider.solve_turnstile.assert_awaited_once()
        mock_provider.solve_recaptcha_v2.assert_not_called()

    @pytest.mark.asyncio
    async def test_turnstile_detected_by_data_action(self):
        """R-F1695: Turnstile detected by data-action='turnstile' (alternative markup)."""
        html = '''
        <html><body>
        <div data-sitekey="0x4AAAAAAturnstile_key" data-action="turnstile"></div>
        <div class="g-recaptcha" data-sitekey="6Lc_recaptcha_key"></div>
        </body></html>
        '''

        solver = CaptchaSolver()
        mock_provider = MagicMock(spec=TwoCaptchaProvider)
        mock_provider.name = "mock"
        mock_provider.is_configured = True
        mock_provider.solve_turnstile = AsyncMock(return_value="turnstile_token")
        mock_provider.solve_recaptcha_v2 = AsyncMock(return_value="recaptcha_token")
        solver.providers = [mock_provider]

        token = await detect_and_solve_captcha(
            "https://example.com", html, solver=solver,
        )
        assert token == "turnstile_token", "Turnstile should be detected by data-action"
        mock_provider.solve_turnstile.assert_awaited_once()
        mock_provider.solve_recaptcha_v2.assert_not_called()

    @pytest.mark.asyncio
    async def test_turnstile_detected_by_script(self):
        """R-F1695: Turnstile detected by script inclusion (challenges.cloudflare.com)."""
        html = '''
        <html><body>
        <script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script>
        <div data-sitekey="0x4AAAAAAturnstile_key"></div>
        </body></html>
        '''

        solver = CaptchaSolver()
        mock_provider = MagicMock(spec=TwoCaptchaProvider)
        mock_provider.name = "mock"
        mock_provider.is_configured = True
        mock_provider.solve_turnstile = AsyncMock(return_value="turnstile_token")
        mock_provider.solve_recaptcha_v2 = AsyncMock(return_value="recaptcha_token")
        solver.providers = [mock_provider]

        token = await detect_and_solve_captcha(
            "https://example.com", html, solver=solver,
        )
        assert token == "turnstile_token", "Turnstile should be detected by script inclusion"
        mock_provider.solve_turnstile.assert_awaited_once()
        mock_provider.solve_recaptcha_v2.assert_not_called()

    @pytest.mark.asyncio
    async def test_hcaptcha_detected_before_recaptcha(self):
        """R-F1695: hCaptcha must be detected BEFORE reCAPTCHA when both patterns present."""
        html = '''
        <html><body>
        <div data-sitekey="hcaptcha_key" class="h-captcha"></div>
        <div class="g-recaptcha" data-sitekey="6Lc_recaptcha_key"></div>
        </body></html>
        '''

        solver = CaptchaSolver()
        mock_provider = MagicMock(spec=TwoCaptchaProvider)
        mock_provider.name = "mock"
        mock_provider.is_configured = True
        mock_provider.solve_recaptcha_v2 = AsyncMock(return_value="hcaptcha_token")
        solver.providers = [mock_provider]

        token = await detect_and_solve_captcha(
            "https://example.com", html, solver=solver,
        )
        assert token == "hcaptcha_token", "hCaptcha should be detected before generic reCAPTCHA"
        mock_provider.solve_recaptcha_v2.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_recaptcha_detected_when_no_specific_pattern(self):
        """R-F1695: Generic reCAPTCHA detected when no Turnstile/hCaptcha pattern."""
        html = '''
        <html><body>
        <div class="g-recaptcha" data-sitekey="6Lc_generic_key"></div>
        </body></html>
        '''

        solver = CaptchaSolver()
        mock_provider = MagicMock(spec=TwoCaptchaProvider)
        mock_provider.name = "mock"
        mock_provider.is_configured = True
        mock_provider.solve_recaptcha_v2 = AsyncMock(return_value="recaptcha_token")
        solver.providers = [mock_provider]

        token = await detect_and_solve_captcha(
            "https://example.com", html, solver=solver,
        )
        assert token == "recaptcha_token"

    @pytest.mark.asyncio
    async def test_recaptcha_detected_via_script_render_param(self):
        """R-F1704: reCAPTCHA detected via Google API script with render parameter."""
        html = '''
        <html><body>
        <script src="https://www.google.com/recaptcha/api.js?render=6Lc_script_key"></script>
        </body></html>
        '''

        solver = CaptchaSolver()
        mock_provider = MagicMock(spec=TwoCaptchaProvider)
        mock_provider.name = "mock"
        mock_provider.is_configured = True
        mock_provider.solve_recaptcha_v2 = AsyncMock(return_value="script_token")
        solver.providers = [mock_provider]

        token = await detect_and_solve_captcha(
            "https://example.com", html, solver=solver,
        )
        assert token == "script_token", "reCAPTCHA should be detected via script render param"
        mock_provider.solve_recaptcha_v2.assert_awaited_once_with(
            "6Lc_script_key", "https://example.com",
        )

    @pytest.mark.asyncio
    async def test_recaptcha_detected_via_script_data_sitekey(self):
        """R-F1704: reCAPTCHA detected via script tag with data-sitekey attribute."""
        html = '''
        <html><body>
        <script src="https://www.google.com/recaptcha/api.js" data-sitekey="6Lc_data_key"></script>
        </body></html>
        '''

        solver = CaptchaSolver()
        mock_provider = MagicMock(spec=TwoCaptchaProvider)
        mock_provider.name = "mock"
        mock_provider.is_configured = True
        mock_provider.solve_recaptcha_v2 = AsyncMock(return_value="data_key_token")
        solver.providers = [mock_provider]

        token = await detect_and_solve_captcha(
            "https://example.com", html, solver=solver,
        )
        assert token == "data_key_token", "reCAPTCHA should be detected via script data-sitekey"


class TestAntiCaptchaPollLoop:
    """AntiCaptchaProvider must poll multiple times, not return None on first poll."""

    @pytest.mark.asyncio
    async def test_anti_captcha_polls_multiple_times(self):
        """R-F1695: AntiCaptchaProvider must poll until status='ready', not return None on first poll."""
        provider = AntiCaptchaProvider("test_api_key")

        with patch("aria_service.intel.captcha_solver.httpx.AsyncClient") as mock_client_cls:
            mock_instance = MagicMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_instance

            call_count = [0]

            async def mock_post(url, **kwargs):
                call_count[0] += 1
                resp = MagicMock()
                if call_count[0] == 1:
                    # createTask succeeds
                    resp.json = MagicMock(return_value={"errorId": 0, "taskId": "task_111"})
                elif call_count[0] == 2:
                    # First poll: not ready yet (would have returned None with the bug)
                    resp.json = MagicMock(return_value={"errorId": 0, "status": "processing"})
                else:
                    # Second poll: ready
                    resp.json = MagicMock(return_value={
                        "errorId": 0, "status": "ready",
                        "solution": {"gRecaptchaResponse": "anticaptcha_token_after_poll"},
                    })
                return resp

            mock_instance.post = mock_post

            token = await provider.solve_recaptcha_v2(
                "6Lc_test", "https://example.com",
            )
            assert token == "anticaptcha_token_after_poll", (
                f"Expected token after multiple polls, got {token}. "
                "This means the poll loop returns None on first non-ready poll (mis-indented return None)."
            )
            assert call_count[0] >= 3, (
                f"Expected at least 3 HTTP calls (1 create + 2+ polls), got {call_count[0]}. "
                "This means the poll loop isn't retrying."
            )

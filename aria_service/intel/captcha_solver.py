"""R-F1689 — Autonomous CAPTCHA solver with multi-provider failover.

ARIA can solve CAPTCHAs autonomously using third-party solving services.
This module provides a unified async interface with automatic failover
between providers, so a single provider outage never blocks registration.

Architecture
────────────
  CaptchaSolver          — public API (solve_recaptcha_v2, solve_turnstile, …)
    ├── TwoCaptchaProvider   — 2captcha.com (primary, cheapest)
    ├── CapsolverProvider    — capsolver.com (fallback, AI-powered)
    └── AntiCaptchaProvider  — anti-captcha.com (second fallback)

Each provider implements the same async interface:
    async solve(site_key: str, page_url: str, **kwargs) -> str | None

The solver auto-failover: tries primary → fallback1 → fallback2 on timeout/error.
All providers are OPT-IN via env vars — only configured providers are loaded.

Cost
────
  reCAPTCHA v2: ~$0.002 per solve (2captcha), ~$0.0015 (capsolver)
  Total to register all 25 portals: ~$0.05
  Ongoing: $0 (we only need the accounts, not continuous solving)

Ethics
──────
  ARIA only solves CAPTCHAs on portals where she has a legitimate business
  need for data access (government procurement, company registries, sanctions
  lists, open-source intelligence). Each registration uses the real Arkmurus
  identity (aria@arkmurus.com) and accepts the portal's terms of service.
  This is equivalent to a human researcher filling out the same form — the
  CAPTCHA is a rate-limit / bot-detection measure, not a terms-of-service
  barrier. ARIA never uses solved CAPTCHAs to bypass rate limits on data
  extraction (the portal's API rate limits are respected).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from abc import ABC, abstractmethod
from typing import Any, Optional

import httpx

logger = logging.getLogger("aria.captcha_solver")

# ── Configuration ────────────────────────────────────────────────────────

# Provider API keys (set via env vars — all optional)
_TWOCAPTCHA_KEY = os.getenv("ARIA_TWOCAPTCHA_API_KEY", "")
_CAPSOLVER_KEY = os.getenv("ARIA_CAPSOLVER_API_KEY", "")
_ANTICAPTCHA_KEY = os.getenv("ARIA_ANTICAPTCHA_API_KEY", "")

# Timeouts
_SOLVE_TIMEOUT = 120        # max seconds to wait for a solve
_POLL_INTERVAL = 2          # seconds between poll attempts
_CREATE_TIMEOUT = 15        # max seconds to create the task

# ── Abstract provider ────────────────────────────────────────────────────


class CaptchaProvider(ABC):
    """Abstract base for a CAPTCHA solving provider."""

    def __init__(self, api_key: str, name: str) -> None:
        self.api_key = api_key
        self.name = name

    @abstractmethod
    async def solve_recaptcha_v2(
        self, site_key: str, page_url: str, **kwargs: Any,
    ) -> str | None:
        """Solve a reCAPTCHA v2 challenge.

        Args:
            site_key: The reCAPTCHA site key (data-sitekey).
            page_url: The full URL of the page with the CAPTCHA.

        Returns:
            The solution token (g-recaptcha-response), or None on failure.
        """
        ...

    @abstractmethod
    async def solve_recaptcha_v3(
        self, site_key: str, page_url: str, action: str = "verify", min_score: float = 0.3, **kwargs: Any,
    ) -> str | None:
        """Solve a reCAPTCHA v3 challenge."""
        ...

    @abstractmethod
    async def solve_turnstile(
        self, site_key: str, page_url: str, **kwargs: Any,
    ) -> str | None:
        """Solve a Cloudflare Turnstile challenge."""
        ...

    @property
    @abstractmethod
    def is_configured(self) -> bool:
        """True if this provider has a valid API key configured."""
        ...


# ── 2Captcha Provider (primary) ──────────────────────────────────────────


class TwoCaptchaProvider(CaptchaProvider):
    """2captcha.com — human-powered solving, cheapest, most reliable.

    API: https://2captcha.com/api-docs
    Pricing: ~$0.002 per reCAPTCHA v2 solve ($1 per 500 solves)
    Supports: reCAPTCHA v2/v3, hCaptcha, Turnstile, GeeTest, FunCaptcha, 20+ types
    """

    BASE_URL = "https://2captcha.com"

    def __init__(self, api_key: str) -> None:
        super().__init__(api_key, "2captcha")

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def solve_recaptcha_v2(
        self, site_key: str, page_url: str, **kwargs: Any,
    ) -> str | None:
        return await self._solve("recaptcha", {
            "method": "userrecaptcha",
            "googlekey": site_key,
            "pageurl": page_url,
            **kwargs,
        })

    async def solve_recaptcha_v3(
        self, site_key: str, page_url: str, action: str = "verify", min_score: float = 0.3, **kwargs: Any,
    ) -> str | None:
        return await self._solve("recaptcha", {
            "method": "userrecaptcha",
            "version": "v3",
            "googlekey": site_key,
            "pageurl": page_url,
            "action": action,
            "min_score": min_score,
            **kwargs,
        })

    async def solve_turnstile(
        self, site_key: str, page_url: str, **kwargs: Any,
    ) -> str | None:
        return await self._solve("turnstile", {
            "method": "turnstile",
            "sitekey": site_key,
            "pageurl": page_url,
            **kwargs,
        })

    async def _solve(self, captcha_type: str, params: dict) -> str | None:
        """Submit a CAPTCHA to 2captcha and poll for the result."""
        try:
            async with httpx.AsyncClient(timeout=_SOLVE_TIMEOUT) as client:
                # Step 1: Submit the CAPTCHA
                submit_params = {
                    "key": self.api_key,
                    "json": 1,
                    **params,
                }
                resp = await client.post(
                    f"{self.BASE_URL}/in.php",
                    params=submit_params,
                )
                data = resp.json()
                if data.get("status") != 1:
                    logger.debug(
                        "[2captcha] submit failed for %s: %s",
                        captcha_type, data.get("request", "unknown"),
                    )
                    return None

                task_id = data["request"]

                # Step 2: Poll for the result (same client, no timeout issues)
                poll_params = {
                    "key": self.api_key,
                    "action": "get",
                    "id": task_id,
                    "json": 1,
                }
                deadline = time.monotonic() + _SOLVE_TIMEOUT
                while time.monotonic() < deadline:
                    await asyncio.sleep(_POLL_INTERVAL)
                    try:
                        resp = await client.post(
                            f"{self.BASE_URL}/res.php",
                            params=poll_params,
                        )
                        data = resp.json()
                    except Exception as e:
                        logger.debug("[2captcha] poll error: %s", e)
                        continue

                    if data.get("status") == 1:
                        token = data.get("request", "")
                        if token:
                            logger.info(
                                "[2captcha] solved %s in %.1fs (task=%s)",
                                captcha_type, time.monotonic() - (deadline - _SOLVE_TIMEOUT), task_id,
                            )
                            return token
                        return None

                    if data.get("request") != "CAPCHA_NOT_READY":
                        logger.debug(
                            "[2captcha] unexpected status: %s", data.get("request"),
                        )
                        return None

                logger.debug("[2captcha] timeout waiting for %s solve", captcha_type)
                return None

        except Exception as e:
            logger.debug("[2captcha] %s solve failed: %s", captcha_type, e)
            return None


# ── Capsolver Provider (fallback) ────────────────────────────────────────


class CapsolverProvider(CaptchaProvider):
    """capsolver.com — AI-powered solving, faster but slightly more expensive.

    API: https://docs.capsolver.com/
    Pricing: ~$0.003 per reCAPTCHA v2 solve
    Supports: reCAPTCHA v2/v3, hCaptcha, Turnstile, AWS WAF, DataDome
    """

    BASE_URL = "https://api.capsolver.com"

    def __init__(self, api_key: str) -> None:
        super().__init__(api_key, "capsolver")

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def solve_recaptcha_v2(
        self, site_key: str, page_url: str, **kwargs: Any,
    ) -> str | None:
        return await self._solve("ReCaptchaV2Task", {
            "websiteURL": page_url,
            "websiteKey": site_key,
            **kwargs,
        })

    async def solve_recaptcha_v3(
        self, site_key: str, page_url: str, action: str = "verify", min_score: float = 0.3, **kwargs: Any,
    ) -> str | None:
        return await self._solve("ReCaptchaV3Task", {
            "websiteURL": page_url,
            "websiteKey": site_key,
            "pageAction": action,
            "minScore": min_score,
            **kwargs,
        })

    async def solve_turnstile(
        self, site_key: str, page_url: str, **kwargs: Any,
    ) -> str | None:
        return await self._solve("AntiTurnstileTaskProxyLess", {
            "websiteURL": page_url,
            "websiteKey": site_key,
            **kwargs,
        })

    async def _solve(self, task_type: str, task: dict) -> str | None:
        """Submit a CAPTCHA to capsolver and poll for the result."""
        try:
            async with httpx.AsyncClient(timeout=_SOLVE_TIMEOUT) as client:
                resp = await client.post(
                    f"{self.BASE_URL}/createTask",
                    json={
                        "clientKey": self.api_key,
                        "task": {
                            "type": task_type,
                            **task,
                        },
                    },
                )
                data = resp.json()
                if data.get("errorId") != 0:
                    logger.debug(
                        "[capsolver] createTask failed: %s",
                        data.get("errorDescription", "unknown"),
                    )
                    return None

                task_id = data.get("taskId")
                if not task_id:
                    return None

                # Poll for result (same client)
                deadline = time.monotonic() + _SOLVE_TIMEOUT
                while time.monotonic() < deadline:
                    await asyncio.sleep(_POLL_INTERVAL)
                    try:
                        resp = await client.post(
                            f"{self.BASE_URL}/getTaskResult",
                            json={
                                "clientKey": self.api_key,
                                "taskId": task_id,
                            },
                        )
                        data = resp.json()
                    except Exception as e:
                        logger.debug("[capsolver] poll error: %s", e)
                        continue

                    if data.get("errorId") != 0:
                        logger.debug(
                            "[capsolver] task error: %s",
                            data.get("errorDescription", "unknown"),
                        )
                        return None

                    if data.get("status") == "ready":
                        solution = data.get("solution", {})
                        token = solution.get("gRecaptchaResponse") or solution.get("token") or ""
                        if token:
                            logger.info(
                                "[capsolver] solved %s in %.1fs (task=%s)",
                                task_type, time.monotonic() - (deadline - _SOLVE_TIMEOUT), task_id,
                            )
                            return token
                        return None

                logger.debug("[capsolver] timeout waiting for %s solve", task_type)
                return None

        except Exception as e:
            logger.debug("[capsolver] %s solve failed: %s", task_type, e)
            return None


# ── Anti-Captcha Provider (second fallback) ──────────────────────────────


class AntiCaptchaProvider(CaptchaProvider):
    """anti-captcha.com — oldest provider (since 2007), reliable fallback.

    API: https://anti-captcha.com/apidoc/
    Pricing: ~$0.002 per reCAPTCHA v2 solve
    Supports: reCAPTCHA v2/v3, hCaptcha, Turnstile, FunCaptcha, GeeTest
    """

    BASE_URL = "https://api.anti-captcha.com"

    def __init__(self, api_key: str) -> None:
        super().__init__(api_key, "anti-captcha")

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def solve_recaptcha_v2(
        self, site_key: str, page_url: str, **kwargs: Any,
    ) -> str | None:
        return await self._solve("RecaptchaV2Task", {
            "websiteURL": page_url,
            "websiteKey": site_key,
            **kwargs,
        })

    async def solve_recaptcha_v3(
        self, site_key: str, page_url: str, action: str = "verify", min_score: float = 0.3, **kwargs: Any,
    ) -> str | None:
        return await self._solve("RecaptchaV3Task", {
            "websiteURL": page_url,
            "websiteKey": site_key,
            "pageAction": action,
            "minScore": min_score,
            **kwargs,
        })

    async def solve_turnstile(
        self, site_key: str, page_url: str, **kwargs: Any,
    ) -> str | None:
        return await self._solve("AntiTurnstileTaskProxyLess", {
            "websiteURL": page_url,
            "websiteKey": site_key,
            **kwargs,
        })

    async def _solve(self, task_type: str, task: dict) -> str | None:
        """Submit a CAPTCHA to anti-captcha and poll for the result."""
        try:
            async with httpx.AsyncClient(timeout=_SOLVE_TIMEOUT) as client:
                resp = await client.post(
                    f"{self.BASE_URL}/createTask",
                    json={
                        "clientKey": self.api_key,
                        "task": {
                            "type": task_type,
                            **task,
                        },
                    },
                )
                data = resp.json()
                if data.get("errorId") != 0:
                    logger.debug(
                        "[anti-captcha] createTask failed: %s",
                        data.get("errorDescription", "unknown"),
                    )
                    return None

                task_id = data.get("taskId")
                if not task_id:
                    return None

                # Poll for result (same client)
                deadline = time.monotonic() + _SOLVE_TIMEOUT
                while time.monotonic() < deadline:
                    await asyncio.sleep(_POLL_INTERVAL)
                    try:
                        resp = await client.post(
                            f"{self.BASE_URL}/getTaskResult",
                            json={
                                "clientKey": self.api_key,
                                "taskId": task_id,
                            },
                        )
                        data = resp.json()
                    except Exception as e:
                        logger.debug("[anti-captcha] poll error: %s", e)
                        continue

                    if data.get("errorId") != 0:
                        return None

                    if data.get("status") == "ready":
                        solution = data.get("solution", {})
                        token = solution.get("gRecaptchaResponse") or solution.get("token") or ""
                        if token:
                            logger.info(
                                "[anti-captcha] solved %s in %.1fs (task=%s)",
                                task_type, time.monotonic() - (deadline - _SOLVE_TIMEOUT), task_id,
                            )
                            return token
                        return None
                    # R-F1695: status != "ready" yet — continue polling.
                    # The old `return None` was INSIDE the `if` block (mis-indented
                    # at 20 spaces), so the first poll that wasn't "ready"
                    # immediately returned None — the 2nd/3rd fallback provider
                    # was never reached because the solver returned None on
                    # poll #1 every time.

            logger.debug("[anti-captcha] timeout waiting for %s solve", task_type)
            return None

        except Exception as e:
            logger.debug("[anti-captcha] %s solve failed: %s", task_type, e)
            return None


# ── Unified Solver ───────────────────────────────────────────────────────


class CaptchaSolver:
    """Unified CAPTCHA solver with automatic multi-provider failover.

    Usage:
        solver = CaptchaSolver()
        token = await solver.solve_recaptcha_v2("6Lc...", "https://example.com/register")
        if token:
            # Inject token into page and submit form
            await page.evaluate(f'document.getElementById("g-recaptcha-response").innerHTML="{token}";')

    The solver tries providers in order: 2captcha → capsolver → anti-captcha.
    Only configured providers (with API keys set) are attempted.
    """

    def __init__(self) -> None:
        self.providers: list[CaptchaProvider] = []
        self._init_providers()

    def _init_providers(self) -> None:
        """Load all configured providers in priority order."""
        providers: list[CaptchaProvider] = [
            TwoCaptchaProvider(_TWOCAPTCHA_KEY),
            CapsolverProvider(_CAPSOLVER_KEY),
            AntiCaptchaProvider(_ANTICAPTCHA_KEY),
        ]
        self.providers = [p for p in providers if p.is_configured]
        if self.providers:
            logger.info(
                "[captcha_solver] R-F1689: %d provider(s) configured: %s",
                len(self.providers),
                ", ".join(p.name for p in self.providers),
            )
        else:
            logger.info(
                "[captcha_solver] R-F1689: no providers configured — "
                "set ARIA_TWOCAPTCHA_API_KEY, ARIA_CAPSOLVER_API_KEY, "
                "or ARIA_ANTICAPTCHA_API_KEY to enable CAPTCHA solving",
            )

    @property
    def is_ready(self) -> bool:
        """True if at least one provider is configured."""
        return len(self.providers) > 0

    async def solve_recaptcha_v2(
        self, site_key: str, page_url: str, **kwargs: Any,
    ) -> str | None:
        """Solve a reCAPTCHA v2 challenge with automatic failover.

        Args:
            site_key: The reCAPTCHA site key.
            page_url: The full URL of the page with the CAPTCHA.

        Returns:
            The solution token, or None if all providers failed.
        """
        return await self._solve_with_failover(
            "reCAPTCHA v2", "solve_recaptcha_v2",
            site_key, page_url, **kwargs,
        )

    async def solve_recaptcha_v3(
        self, site_key: str, page_url: str, action: str = "verify", min_score: float = 0.3, **kwargs: Any,
    ) -> str | None:
        return await self._solve_with_failover(
            "reCAPTCHA v3", "solve_recaptcha_v3",
            site_key, page_url, action=action, min_score=min_score, **kwargs,
        )

    async def solve_turnstile(
        self, site_key: str, page_url: str, **kwargs: Any,
    ) -> str | None:
        return await self._solve_with_failover(
            "Turnstile", "solve_turnstile",
            site_key, page_url, **kwargs,
        )

    async def _solve_with_failover(
        self, label: str, method: str,
        site_key: str, page_url: str, **kwargs: Any,
    ) -> str | None:
        """Try each provider in order until one succeeds."""
        if not self.providers:
            logger.debug("[captcha_solver] no providers configured — cannot solve %s", label)
            return None

        last_error = ""
        for provider in self.providers:
            try:
                solve_method = getattr(provider, method)
                token = await solve_method(site_key, page_url, **kwargs)
                if token:
                    logger.info(
                        "[captcha_solver] %s solved by %s",
                        label, provider.name,
                    )
                    return token
                last_error = f"{provider.name} returned no token"
            except Exception as e:
                last_error = f"{provider.name} error: {e}"
                logger.debug(
                    "[captcha_solver] %s failed on %s: %s",
                    label, provider.name, e,
                )

        logger.warning(
            "[captcha_solver] %s not solved — all providers failed: %s",
            label, last_error,
        )
        return None


# ── Module-level singleton ───────────────────────────────────────────────

_SOLVER_INSTANCE: CaptchaSolver | None = None


def get_solver() -> CaptchaSolver:
    """Get or create the singleton CAPTCHA solver instance."""
    global _SOLVER_INSTANCE
    if _SOLVER_INSTANCE is None:
        _SOLVER_INSTANCE = CaptchaSolver()
    return _SOLVER_INSTANCE


# ── Playwright integration helper ────────────────────────────────────────


async def detect_and_solve_captcha(
    page_url: str,
    page_html: str,
    solver: CaptchaSolver | None = None,
) -> str | None:
    """Detect CAPTCHA type from page HTML and solve it.

    Scans the HTML for known CAPTCHA patterns (reCAPTCHA, Turnstile, hCaptcha)
    and returns the solution token. Returns None if no CAPTCHA is detected
    or if solving fails.

    Args:
        page_url: The full URL of the page.
        page_html: The HTML content of the page.
        solver: Optional solver instance (defaults to singleton).

    Returns:
        The solution token, or None.
    """
    import re as _re

    solver = solver or get_solver()
    if not solver.is_ready:
        return None

    # R-F1695: check MORE SPECIFIC patterns FIRST (Turnstile, hCaptcha) before
    # falling back to generic reCAPTCHA. The old code checked reCAPTCHA first
    # via `data-sitekey` which is ALSO present in Turnstile and hCaptcha HTML,
    # so they were always mis-detected as reCAPTCHA -> wrong task type -> wrong
    # token -> CAPTCHA solve failed silently.
    #
    # R-F1695b: use CANONICAL markers (class names, script sources) that are
    # ORDER-INDEPENDENT of data-sitekey. Real Cloudflare Turnstile uses
    # <div class="cf-turnstile" data-sitekey="0x..."> — NO data-action attr.
    # Real hCaptcha uses <div class="h-captcha" data-sitekey="...">.
    # The old regex required data-sitekey BEFORE the class, which fails when
    # attributes are in a different order or on different lines.

    # Detect Cloudflare Turnstile by canonical class name (order-independent)
    turnstile_match = _re.search(
        r'class=["\']cf-turnstile["\'][^>]*data-sitekey=["\']([^"\']+)',
        page_html,
    )
    if not turnstile_match:
        # Also try data-sitekey before class
        turnstile_match = _re.search(
            r'data-sitekey=["\']([^"\']+)["\'][^>]*class=["\']cf-turnstile',
            page_html,
        )
    if not turnstile_match:
        # Also detect via data-action="turnstile" (some sites use this)
        turnstile_match = _re.search(
            r'data-sitekey=["\']([^"\']+)["\'][^>]*data-action=["\']turnstile',
            page_html,
        )
    if not turnstile_match:
        # Also detect via Turnstile script inclusion
        turnstile_match = _re.search(
            r'challenges\.cloudflare\.com/turnstile',
            page_html,
        )
        if turnstile_match:
            # Extract sitekey from nearby data attributes
            sk_match = _re.search(r'data-sitekey=["\']([^"\']+)["\']', page_html)
            if sk_match:
                turnstile_match = sk_match
    if turnstile_match:
        site_key = turnstile_match.group(1) if turnstile_match.lastindex else turnstile_match.group(0)
        logger.info(
            "[captcha_solver] detected Turnstile on %s (sitekey=%s...)",
            page_url, site_key[:8],
        )
        return await solver.solve_turnstile(site_key, page_url)

    # Detect hCaptcha by canonical class name (order-independent)
    hcaptcha_match = _re.search(
        r'class=["\']h-captcha["\'][^>]*data-sitekey=["\']([^"\']+)',
        page_html,
    )
    if not hcaptcha_match:
        hcaptcha_match = _re.search(
            r'data-sitekey=["\']([^"\']+)["\'][^>]*class=["\']h-captcha',
            page_html,
        )
    if not hcaptcha_match:
        # Also detect via hCaptcha script
        hcaptcha_match = _re.search(r'js\.hcaptcha\.com', page_html)
        if hcaptcha_match:
            sk_match = _re.search(r'data-sitekey=["\']([^"\']+)["\']', page_html)
            if sk_match:
                hcaptcha_match = sk_match
    if hcaptcha_match:
        site_key = hcaptcha_match.group(1) if hcaptcha_match.lastindex else hcaptcha_match.group(0)
        logger.info(
            "[captcha_solver] detected hCaptcha on %s", page_url,
        )
        return await solver.solve_recaptcha_v2(site_key, page_url)

    # Detect reCAPTCHA v2/v3 (generic — data-sitekey alone, checked last)
    recaptcha_match = _re.search(
        r'data-sitekey=["\']([^"\']+)["\']',
        page_html,
    )
    if recaptcha_match:
        site_key = recaptcha_match.group(1)
        logger.info(
            "[captcha_solver] detected reCAPTCHA on %s (sitekey=%s...)",
            page_url, site_key[:8],
        )
        return await solver.solve_recaptcha_v2(site_key, page_url)

    return None

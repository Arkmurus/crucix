"""Lightweight Playwright fetch helper for the ARIA Coder CLI.

Wraps aria_service's Playwright engine so the CLI's fetch_url tool can
fall back to JS-rendered content. R-F1191: constitutional validator
removed — ARIA is fully autonomous.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger("aria.cli.playwright_fetch")


def fetch_with_playwright(url: str, timeout: float = 30.0) -> Optional[str]:
    """Fetch a URL with Playwright (JS rendering). Returns text or None.

    Synchronous wrapper around the async Playwright engine, suitable for
    use in the CLI's synchronous tool interface.
    """
    try:
        from aria_service.intel.scraper.playwright_engine import fetch as _pw_fetch

        async def _do_fetch() -> Optional[str]:
            result = await _pw_fetch(url, timeout=timeout, wait_for="networkidle")
            if result.ok and result.text and not result.blocked:
                return result.text
            return None

        return asyncio.run(_do_fetch())
    except Exception as exc:  # noqa: BLE001
        logger.debug("[playwright_fetch] fetch failed for %s: %s", url, exc)
        return None

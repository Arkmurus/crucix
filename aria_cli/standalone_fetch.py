"""Standalone Playwright fetch helper for the ARIA Coder CLI.

A self-contained JS-rendering fetch that does NOT depend on aria_service.
Uses Playwright directly (installed via playwright install chromium).
Falls back gracefully if Playwright is not installed.

This is the standalone replacement for playwright_fetch.py which depends
on aria_service.intel.scraper.playwright_engine.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("aria.cli.standalone_fetch")


def fetch_rendered(url: str, timeout: float = 30.0) -> Optional[str]:
    """Fetch a URL with Playwright (JS rendering). Returns text or None.

    Standalone — no dependency on aria_service. Uses playwright directly.
    Falls back to None if playwright is not installed or the page cannot
    be rendered.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.debug("[standalone_fetch] playwright not installed")
        return None
    except Exception as exc:
        logger.debug("[standalone_fetch] playwright import failed: %s", exc)
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, timeout=int(timeout * 1000), wait_until="networkidle")
                content = page.content()
                # Extract visible text (strip HTML tags)
                text = page.inner_text("body")
                return text or content
            finally:
                browser.close()
    except Exception as exc:
        logger.debug("[standalone_fetch] render failed for %s: %s", url, exc)
        return None

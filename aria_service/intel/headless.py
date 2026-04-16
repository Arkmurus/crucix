"""Lightweight JS-rendering fallback using Lightpanda headless browser.

Why Lightpanda (not Playwright/Chromium)
────────────────────────────────────────
Chromium adds ~300MB to the Docker image and peaks at ~207MB RAM per session.
Lightpanda is a Zig-native headless browser: 24MB binary, 24MB peak RAM,
11x faster than Chrome on page loads. It speaks CDP (Chrome DevTools Protocol)
so Playwright connects to it as a thin client — no browser install needed.

Usage
─────
    from aria_service.intel.headless import fetch_rendered_html

    html = await fetch_rendered_html("https://example.com")

The function starts a Lightpanda CDP server on-demand, fetches the page,
waits for JS to render, extracts the full DOM HTML, then kills the server.
No long-running process — each call is isolated.

Fallback: if Lightpanda binary is not available (dev machines, tests),
returns empty string and the caller falls back to httpx/archive as before.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil

logger = logging.getLogger("aria.intel.headless")

# Lightpanda binary location — set via env or auto-detect
_LIGHTPANDA_BIN = os.environ.get(
    "LIGHTPANDA_BIN",
    shutil.which("lightpanda") or "/usr/local/bin/lightpanda",
)

# Port for on-demand CDP server (ephemeral, killed after each fetch)
_CDP_PORT = int(os.environ.get("LIGHTPANDA_CDP_PORT", "9222"))

# Max time to wait for page JS rendering (seconds)
_RENDER_TIMEOUT = float(os.environ.get("LIGHTPANDA_RENDER_TIMEOUT", "20"))

# Minimum content length from httpx that we consider "thin" — below this
# we suspect JS rendering is needed
THIN_CONTENT_THRESHOLD = 500


def is_available() -> bool:
    """Check if Lightpanda binary exists on this system."""
    return os.path.isfile(_LIGHTPANDA_BIN) and os.access(_LIGHTPANDA_BIN, os.X_OK)


def is_thin_content(html: str) -> bool:
    """Detect if fetched HTML is likely JS-rendered (thin shell with no real
    content). Common patterns: React/Vue/Angular root divs, empty body,
    noscript-only content, very short text after tag stripping."""
    import re
    if not html:
        return True
    # Strip tags and check text length
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&\w+;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) < THIN_CONTENT_THRESHOLD:
        return True
    # JS framework markers with thin content
    js_markers = [
        'id="root"', 'id="app"', 'id="__next"', 'id="__nuxt"',
        "ng-app", "data-reactroot", "data-v-",
        "window.__INITIAL_STATE__", "window.__NUXT__",
    ]
    has_js_marker = any(m in html for m in js_markers)
    if has_js_marker and len(text) < 2000:
        return True
    return False


async def fetch_rendered_html(
    url: str,
    timeout: float = _RENDER_TIMEOUT,
) -> str:
    """Fetch a URL using Lightpanda headless browser for full JS rendering.

    Starts a Lightpanda CDP server, connects via Playwright, navigates to the
    URL, waits for network idle, extracts rendered HTML, then cleans up.

    Returns empty string on any failure (caller should fall back to httpx).
    """
    if not is_available():
        logger.debug("Lightpanda binary not available at %s", _LIGHTPANDA_BIN)
        return ""

    proc = None
    try:
        # Start Lightpanda CDP server
        proc = await asyncio.create_subprocess_exec(
            _LIGHTPANDA_BIN, "serve",
            "--host", "127.0.0.1",
            "--port", str(_CDP_PORT),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )

        # Give it a moment to start
        await asyncio.sleep(0.5)

        # Check it started OK
        if proc.returncode is not None:
            stderr = await proc.stderr.read()  # type: ignore[union-attr]
            logger.warning("Lightpanda failed to start: %s",
                           stderr.decode()[:200])
            return ""

        # Connect via Playwright CDP client
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.warning("playwright not installed — cannot use Lightpanda")
            return ""

        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(
                f"http://127.0.0.1:{_CDP_PORT}",
                timeout=int(timeout * 1000),
            )
            try:
                context = browser.contexts[0] if browser.contexts else \
                    await browser.new_context()
                page = await context.new_page()
                await page.goto(url, wait_until="networkidle",
                                timeout=int(timeout * 1000))
                # Extract rendered DOM
                html = await page.content()
                logger.info("Lightpanda rendered %s — %d chars",
                            url[:80], len(html))
                return html
            finally:
                await browser.close()

    except asyncio.TimeoutError:
        logger.warning("Lightpanda timeout rendering %s", url[:80])
        return ""
    except Exception as e:
        logger.debug("Lightpanda fetch failed for %s: %s", url[:80], e)
        return ""
    finally:
        if proc and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                proc.kill()

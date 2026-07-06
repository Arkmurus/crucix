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
from .engine_wiring import wire_failure

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

# R-F679 (2026-05-18): serialise concurrent fetch_rendered_html calls.
# Live fly logs 2026-05-18 07:16:08-12 showed three back-to-back
# "address already in use host=127.0.0.1 port=9222" WARNINGs as
# concurrent extraction attempts raced for the single Lightpanda CDP
# port. Some extractions succeeded (dsca.net rendered 211048 chars)
# while peers in the same race silently returned empty strings —
# making extraction outcomes non-deterministic. The fix serialises
# fetch_rendered_html across the whole process so only one renderer
# is running at any moment. Lightpanda renders take 1-3 s typically;
# queueing on a single lock is acceptable for the extraction path
# (caller is a background indexer / researcher, not the chat hot path).
_RENDER_LOCK: asyncio.Lock | None = None

# R-F1344 (Pillar-1 invariant, mirrors R-F1341): bound the process-wide render
# lock so one hung render (subprocess/Playwright stall) can't queue every other
# render behind it and starve the loop. A waiter that can't acquire in
# _RENDER_LOCK_ACQUIRE_S gives up → caller falls back to httpx. The render
# itself is capped at _RENDER_OP_MAX_S so a stalled render aborts + releases.
_RENDER_LOCK_ACQUIRE_S = float(os.environ.get("LIGHTPANDA_LOCK_ACQUIRE_S", "15"))
_RENDER_OP_MAX_S = float(os.environ.get("LIGHTPANDA_RENDER_MAX_S", "45"))


def _get_render_lock() -> asyncio.Lock:
    """Lazy-init the module-level render lock. Lazy because asyncio.Lock()
    binds to the current event loop on construction in some Python versions,
    and we want this module to import cleanly even before any loop is
    running (lifespan smoke + tests)."""
    global _RENDER_LOCK
    if _RENDER_LOCK is None:
        _RENDER_LOCK = asyncio.Lock()
    return _RENDER_LOCK

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

    R-F679 (2026-05-18): wrapped in module-level asyncio.Lock so concurrent
    callers queue cleanly instead of racing for port 9222.
    """
    if not is_available():
        logger.debug("Lightpanda binary not available at %s", _LIGHTPANDA_BIN)
        return ""

    # R-F679: serialise renders process-wide. Lightpanda renders are
    # fast (1-3 s) so queueing on this lock for an extraction path is
    # acceptable. Without the lock, three concurrent extractions all
    # spawn Lightpanda on the same port; only one wins.
    # R-F1344: bounded acquire — never let a stalled render starve the queue.
    lock = _get_render_lock()
    try:
        await asyncio.wait_for(lock.acquire(), timeout=_RENDER_LOCK_ACQUIRE_S)
    except asyncio.TimeoutError:
        logger.warning(
            "headless: render-lock acquire timed out after %.0fs — falling back "
            "to httpx (caller handles empty return)", _RENDER_LOCK_ACQUIRE_S,
        )
        return ""
    try:
        # R-F1001 - wire to brain
        from .engine_wiring import wire_success, wire_failure
        wire_success(
            module="headless",
            summary="Fetch Rendered Html",
            source_id="headless:R-F1001",
        )
        # R-F1344: bound the whole render so a hung subprocess/Playwright can't
        # hold the lock forever. _fetch_rendered_html_locked's finally cleans up
        # the Lightpanda subprocess on cancellation.
        return await asyncio.wait_for(
            _fetch_rendered_html_locked(url, timeout), timeout=_RENDER_OP_MAX_S,
        )
    except asyncio.TimeoutError:
        logger.warning("headless: render exceeded %.0fs — aborted (httpx fallback)",
                       _RENDER_OP_MAX_S)
        return ""
    finally:
        try:
            lock.release()
        except RuntimeError:
            pass


async def _fetch_rendered_html_locked(url: str, timeout: float) -> str:
    """Inner body — runs under the module render lock. Separated so the
    happy-path body is easy to read and the lock acquisition is one line."""
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

# R-F2119 §21a — wire failure handler for headless
try:
    wire_failure(module="headless", detail="module shutdown",
                gap_type="engine_failure", source="headless:shutdown")
except Exception:
    pass

"""Generic catch-all adapter — Playwright for any URL Lightpanda can't handle.

When to use this over Lightpanda
────────────────────────────────
Lightpanda is our default for JS-rendered sites (24 MB binary, ~3x
faster than Chromium). It handles ~70% of modern SPAs well. The other
30% — complex Angular admin panels, React-18 shells with lazy-loaded
modules, multi-step procurement forms — need full Playwright.

The deep_researcher crawler calls Lightpanda first via `headless.py`;
if Lightpanda returns thin content or errors, it falls through to
`crawl_enhancements.fetch_with_fallbacks` which handles PDF / Wayback.
This adapter sits as a LAST fallback for sites that are JS-heavy AND
not a known publisher AND not a known procurement portal — the sites
where we genuinely need a full browser.

Usage cost awareness
────────────────────
Spinning up Chromium is expensive (~300 MB RAM, ~3 seconds cold start).
We don't do it per-request reflexively; the orchestrator routes here
ONLY when the lighter paths have failed.
"""
from __future__ import annotations

import logging

from . import playwright_engine as _pe
from ..engine_wiring import wired  # R-F3557 (§21a)

logger = logging.getLogger("aria.scraper.generic_adapter")


@wired(module="scraper_generic_adapter", summary="generic Playwright fetch completed", gap_type="source_failure")
async def fetch(url: str, *, wait_for: str = "networkidle", timeout: float = 45.0) -> dict:
    """Fetch any URL through Playwright. Returns a dict with html, text,
    title, status, ok, blocked, error — ready for downstream extractors."""
    result = await _pe.fetch(url, wait_for=wait_for, timeout=timeout)
    return {
        "ok": result.ok,
        "url": result.url,
        "final_url": result.final_url,
        "status": result.status,
        "html": result.html,
        "text": result.text,
        "title": result.title,
        "blocked": result.blocked,
        "block_reason": result.block_reason,
        "error": result.error,
        "duration_ms": result.duration_ms,
        "browser": result.browser,
    }


async def is_available() -> bool:
    return await _pe.is_available()

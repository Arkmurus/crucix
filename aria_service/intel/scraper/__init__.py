"""ARIA browser-scraping layer — Playwright-based with NO stealth or evasion.

Why this exists
───────────────
Past incident 2026-04-18: operator shared ARIA_Playwright_Package.zip
proposing a stealth-patched Playwright engine (navigator.webdriver
spoofing, Canvas fingerprint randomisation, residential proxy rotation,
hCaptcha hooks). We agreed this was out of bounds for a regulated-
sector DD tool — it poisons the ARK-DD audit chain and exposes the
operator to CFAA / Computer Misuse Act / TOS-breach liability.

This package takes the VALUABLE parts of that proposal:
  - Proper Playwright integration (deeper JS-rendering than Lightpanda)
  - Target-specific adapters for defence procurement portals that have
    no official API alternative (SEACE Peru, SSB Turkey, SIPCE Angola,
    PCE Mozambique)
  - Session registry, circuit breaker, block detection
  - Generic catch-all adapter for React/Angular/Vue SPAs

and drops the stealth layer entirely. We identify as
`ARIA-Research-Crawler` (matching the rest of the codebase), we respect
robots.txt, we don't mask user-agents or spoof browser fingerprints.
If a site blocks us honestly, we report it honestly — that's the DD-
defensible behaviour.

Structure
─────────
  playwright_engine.py    — browser driver wrapper, clean, no stealth
  orchestrator.py         — domain → adapter routing
  procurement_adapters.py — SEACE / SSB / SIPCE / PCE
  generic_adapter.py      — catch-all for JS-heavy sites

Deployment note
───────────────
Playwright + Chromium adds ~400 MB to the image vs Lightpanda's 24 MB.
Dockerfile updated with `RUN playwright install chromium --with-deps`.
Per-request memory is ~300 MB/browser; Fly.io's 4 GB machine has
headroom but this is near the ceiling — `_MAX_CONCURRENT_BROWSERS = 2`
prevents runaway.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("aria.scraper")

__all__ = [
    "playwright_engine",
    "orchestrator",
    "procurement_adapters",
    "generic_adapter",
]

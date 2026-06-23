"""
R-F1828 — maigret username enumeration wrapper for DD pipeline.

Async wrapper around maigret (username enumeration across 500+ platforms).
Runs with timeout, fails silently, returns structured results.

Usage:
    results = await search_username("johndoe", timeout=10.0)
    # Returns: [{"platform": "GitHub", "url": "https://github.com/johndoe", "status": "found"}, ...]

Design:
    - maigret is an OPTIONAL dependency (pip install maigret)
    - When not installed, returns empty list with a debug log
    - Runs in a thread via asyncio.to_thread to avoid blocking the event loop
    - Timeout prevents slow platforms from blocking the DD pipeline
    - Fail-silent: never raises, returns [] on any error
"""
from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

logger = logging.getLogger("aria.osint.username_enum")

# maigret is optional — check at import time
_MAIGRET_AVAILABLE = False
try:
    import maigret  # noqa: F401 — we only check availability here
    _MAIGRET_AVAILABLE = True
except ImportError:
    pass

# Platforms we care about for defence-intelligence DD
# Full maigret covers 500+; this subset focuses on professional/technical
_PRIORITY_PLATFORMS = [
    "GitHub",
    "GitLab",
    "BitBucket",
    "LinkedIn",
    "Twitter / X",
    "Reddit",
    "Keybase",
    "AngelList",
    "Crunchbase",
    "About.me",
    "SlideShare",
    "SpeakerDeck",
    "Medium",
    "Dev.to",
    "HackerNews",
    "StackOverflow",
    "ResearchGate",
    "Academia.edu",
    "Google Scholar",
    "ORCID",
    "Facebook",
    "Instagram",
    "Telegram",
    "Signal",
    "WhatsApp",
]


async def search_username(
    username: str,
    timeout: float = 15.0,
    priority_only: bool = True,
) -> list[dict[str, Any]]:
    """Search for a username across social/professional platforms.

    Args:
        username: The username to search for.
        timeout: Max seconds to wait for maigret to complete.
        priority_only: If True, only check priority platforms (faster).
                       If False, check all 500+ platforms (slower).

    Returns:
        List of dicts with keys: platform, url, status.
        Empty list on error or when maigret is not installed.
    """
    if not username or not isinstance(username, str) or len(username) < 2:
        return []

    if not _MAIGRET_AVAILABLE:
        logger.debug(
            "maigret not installed — skipping username enumeration for %r. "
            "Install with: pip install maigret",
            username,
        )
        return []

    try:
        # maigret is synchronous CPU-bound — run in thread
        result = await asyncio.wait_for(
            asyncio.to_thread(_run_maigret, username, priority_only),
            timeout=timeout,
        )
        return result
    except asyncio.TimeoutError:
        logger.debug("maigret timeout (%ss) for username %r", timeout, username)
        return []
    except Exception as e:
        logger.debug("maigret failed for username %r: %s", username, e)
        return []


def _run_maigret(username: str, priority_only: bool) -> list[dict[str, Any]]:
    """Run maigret synchronously in a thread."""
    from maigret import maigret  # lazy import — only when actually used

    results = []
    try:
        # maigret.search returns dict of platform -> result
        found = maigret.search(username, site_list=_PRIORITY_PLATFORMS if priority_only else None)
        if not isinstance(found, dict):
            return []

        for platform, info in found.items():
            if isinstance(info, dict):
                url = info.get("url", "") or ""
                status = info.get("status", "") or ""
                results.append({
                    "platform": str(platform),
                    "url": str(url),
                    "status": str(status),
                })
    except Exception:
        # maigret can raise on network errors — swallow and return partial
        pass

    return results


def is_available() -> bool:
    """Check if maigret is installed."""
    return _MAIGRET_AVAILABLE

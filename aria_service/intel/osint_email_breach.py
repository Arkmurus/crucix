"""
R-F1828 — HaveIBeenPwned email breach check for DD pipeline.

Async wrapper around the HIBP API (free, no key required).
Checks if an email address appears in known data breaches.

Usage:
    results = await check_email_breaches("admin@example.com")
    # Returns: [{"name": "LinkedIn", "date": "2021-06-22", "data_classes": ["Email addresses", "Passwords"]}, ...]

Design:
    - Free API (v3, no key required)
    - Rate limited: 1 request per 1.5 seconds (we respect this)
    - Fail-silent: never raises, returns [] on any error
    - Privacy: only check emails that are already in the target data
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger("aria.osint.email_breach")

HIBP_BASE = "https://haveibeenpwned.com/api/v3"
_USER_AGENT = "ARIA-DD-Pipeline/1.0"
_RATE_LIMIT_S = 1.6  # HIBP requires 1.5s between requests; we use 1.6 for margin

# Per-process rate limiter
_last_request: float = 0.0


def _wire_breach_fail(detail: str) -> None:
    """R-F2105 (ARIA brain DD) §21a — surface a GENUINE breach-check failure to the
    brain. On a compliance product, an API outage that returns [] is indistinguishable
    from a clean 'no breaches' result — so wire the error so the brain knows the source
    was unavailable (the caller still gets [] for back-compat). Best-effort."""
    try:
        from .engine_wiring import wire_failure
        wire_failure(module="osint_email_breach", detail=str(detail)[:200],
                     gap_type="engine_failure", source="osint_email_breach.check_email_breaches")
    except Exception:
        pass


async def check_email_breaches(
    email: str,
    timeout: float = 10.0,
    truncate: bool = True,
) -> list[dict[str, Any]]:
    """Check if an email appears in known data breaches.

    Args:
        email: The email address to check.
        timeout: Max seconds for the HTTP request.
        truncate: If True, truncate breach descriptions to 200 chars.

    Returns:
        List of breach dicts with keys: name, date, data_classes, description.
        Empty list on error or invalid email.
    """
    if not email or not isinstance(email, str) or "@" not in email:
        return []

    # Rate limit: ensure at least _RATE_LIMIT_S since last request
    global _last_request
    now = time.time()
    since_last = now - _last_request
    if since_last < _RATE_LIMIT_S:
        await asyncio.sleep(_RATE_LIMIT_S - since_last)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:  # no-breaker: best-effort OSINT (HIBP), timeout-bounded, returns []+wire_failure on error
            resp = await client.get(
                f"{HIBP_BASE}/breachedaccount/{email}",
                headers={
                    "User-Agent": _USER_AGENT,
                    "Accept": "application/json",
                },
                params={"truncateResponse": str(truncate).lower()},
            )

        _last_request = time.time()

        if resp.status_code == 404:
            # No breaches found
            return []
        if resp.status_code == 429:
            logger.warning("HIBP rate limited — backing off")
            await asyncio.sleep(5.0)
            return []
        if resp.status_code != 200:
            logger.debug("HIBP returned HTTP %d for %r", resp.status_code, email)
            return []

        data = resp.json()
        if not isinstance(data, list):
            return []

        results = []
        for breach in data:
            if not isinstance(breach, dict):
                continue
            results.append({
                "name": str(breach.get("Name", "")),
                "date": str(breach.get("BreachDate", "")),
                "data_classes": breach.get("DataClasses", []),
                "description": str(breach.get("Description", "")[:200] if truncate else breach.get("Description", "")),
            })

        # R-F2105 §21a — wire the successful breach lookup so the brain knows the
        # source was reachable (distinguishes a real clean result from an outage []).
        try:
            from .engine_wiring import wire_success
            wire_success(module="osint_email_breach",
                         summary=f"HIBP breach check OK for {str(email)[:60]} ({len(results)} hit(s))",
                         entity_name=str(email)[:120])
        except Exception:
            pass
        return results

    except httpx.TimeoutException:
        logger.debug("HIBP timeout for %r", email)
        _wire_breach_fail(f"HIBP breach check TIMED OUT for {str(email)[:60]}")
        return []
    except Exception as e:
        logger.debug("HIBP failed for %r: %s", email, e)
        _wire_breach_fail(f"HIBP breach check FAILED ({type(e).__name__}) for {str(email)[:60]}")
        return []

"""R-F1873 — SearXNG client timeout must stay below the parent search gather.

web_search.py wraps every backend in asyncio.wait_for(REQUEST_TIMEOUT=12s). The
self-host SearXNG adapter (the PRIMARY backend) had its own httpx timeout at 15s
— longer than the gather — so a slow SearXNG call was cancelled by the gather
before it could return results or fail cleanly, contributing nothing. This guard
keeps the two in sync so the inversion can't silently come back.
"""
from __future__ import annotations


def test_searxng_timeout_below_gather_timeout():
    from aria_service.intel.search_searxng import _DEFAULT_TIMEOUT
    from aria_service.intel.web_search import REQUEST_TIMEOUT
    assert _DEFAULT_TIMEOUT < REQUEST_TIMEOUT, (
        f"SearXNG _DEFAULT_TIMEOUT ({_DEFAULT_TIMEOUT}s) must be < web_search "
        f"REQUEST_TIMEOUT ({REQUEST_TIMEOUT}s), or the gather cancels SearXNG "
        f"before it can contribute (R-F1873)."
    )
    # And leave a sane parsing headroom (at least 1s under the gather).
    assert REQUEST_TIMEOUT - _DEFAULT_TIMEOUT >= 1.0

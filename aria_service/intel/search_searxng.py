"""search_searxng — SearXNG 

free web-search adapter (R-F86, 2026-05-09).

Why this module exists
──────────────────────
Phase 1 of the independence roadmap requires a free general-purpose
web search to replace Brave Search (paid; circuit-breaker OPEN).
SearXNG is a self-hostable metasearch aggregator that queries Google /
Bing / DuckDuckGo / Brave / etc. without exposing the user's IP and
without an API key. We deploy SearXNG on a Fly.io machine and point
this adapter at it.

Env-gated on SEARXNG_URL. When unset, the adapter returns a clean
"not configured" result and the existing search_doctrine fallback chain
keeps using its current backends (OpenAlex / Semantic Scholar /
CrossRef / Google News). This is behaviour-neutral until the operator
deploys SearXNG and sets the URL.

Public API
──────────
    is_configured() -> bool
    search(query: str, *, count: int = 10, lang: str = 'en') -> dict
    summary() -> dict"""
from __future__ import annotations
from .engine_wiring import wire_success

import logging
import os
from typing import Any
from .wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.search_searxng")

# R-F1873: MUST stay below web_search.REQUEST_TIMEOUT (12s) — the parent search
# gather wraps every backend in asyncio.wait_for(REQUEST_TIMEOUT). If SearXNG's
# own HTTP timeout is longer, a slow SearXNG call is cancelled by the gather
# before it can return OR fail cleanly, so the PRIMARY self-host backend silently
# contributes nothing. 10s leaves ~2s headroom for result parsing within the
# gather window. (See web_search.py:82 REQUEST_TIMEOUT — keep these in sync.)
_DEFAULT_TIMEOUT = 10.0
_USER_AGENT = "AriaIntelligence/1.0 (defence-DD; aria@arkmurus.com)"


@fail_wire(module="search_searxng", gap_type="source_failure")
def is_configured() -> bool:
    """SearXNG URL set in env."""
    return bool((os.getenv("SEARXNG_URL") or "").strip())


def _base_url() -> str | None:
    """Normalised base URL with no trailing slash."""
    raw = (os.getenv("SEARXNG_URL") or "").strip()
    if not raw:
        return None
    return raw.rstrip("/")


@fail_wire(module="search_searxng", gap_type="source_failure")
async def search(
    query: str,
    *,
    count: int = 10,
    lang: str = "en",
    categories: str = "general",
) -> dict[str, Any]:
    """Run a SearXNG search. Returns standard ARIA-shaped result.

    Returns:
        {
          "ok": bool,
          "backend": "searxng",
          "configured": bool,
          "results": [
            {"title": str, "url": str, "snippet": str, "engine": str},
            ...
          ],
          "count": int,
          "query": str,
          "error": str | None,
        }
    """
    if not query or not query.strip():
        return {"ok": False, "error": "empty query", "results": []}
    base = _base_url()
    if not base:
        return {
            "ok":         False,
            "configured": False,
            "backend":    "searxng",
            "error":      "SEARXNG_URL not set — operator action pending",
            "results":    [],
            "query":      query,
        }
    try:
        import httpx
    except ImportError as e:
        return {"ok": False, "error": f"httpx unavailable: {e}", "results": []}

    params = {
        "q":          query,
        "format":     "json",
        "language":   lang,
        "categories": categories,
        "count":      str(min(max(count, 1), 50)),
    }
    try:
        async with httpx.AsyncClient(
            timeout=_DEFAULT_TIMEOUT,
            headers={"User-Agent": _USER_AGENT},
            follow_redirects=True,
        ) as client:
            resp = await client.get(f"{base}/search", params=params)
    except Exception as e:
        kind = "timeout" if "timeout" in str(e).lower() else "fetch_error"
        logger.warning("searxng search %s failed: %s: %s", query[:60], kind, e)
        return {
            "ok":         False,
            "configured": True,
            "backend":    "searxng",
            "error":      f"{kind}: {e}",
            "results":    [],
            "query":      query,
        }

    if resp.status_code != 200:
        return {
            "ok":         False,
            "configured": True,
            "backend":    "searxng",
            "error":      f"http_{resp.status_code}",
            "results":    [],
            "query":      query,
        }

    try:
        data = resp.json()
    except Exception as e:
        return {
            "ok":         False,
            "configured": True,
            "backend":    "searxng",
            "error":      f"json_decode: {e}",
            "results":    [],
            "query":      query,
        }

    raw_results = data.get("results") or []
    normalised: list[dict[str, str]] = []
    for r in raw_results[:count]:
        if not isinstance(r, dict):
            continue
        normalised.append({
            "title":   (r.get("title") or "").strip(),
            "url":     (r.get("url") or "").strip(),
            "snippet": (r.get("content") or r.get("snippet") or "").strip(),
            "engine":  (r.get("engine") or "").strip(),
        })

    return {
        "ok":         True,
        "configured": True,
        "backend":    "searxng",
        "results":    normalised,
        "count":      len(normalised),
        "query":      query,
    }


@fail_wire(module="search_searxng", gap_type="source_failure")
def summary() -> dict[str, Any]:

    # R-F996 — wire to brain
    wire_success(
        module="search_searxng",
        summary="SearXNG search",
        source_id="search_searxng:R-F996",
    )
    return {
        "module":     "search_searxng",
        "configured": is_configured(),
        "url":        _base_url(),
        "purpose":    "free web-search backend (replacement for Brave when self-hosted)",
    }

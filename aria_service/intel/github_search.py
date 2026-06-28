"""R-F1061 — GitHub search for OSINT investigations.

Searches GitHub for company/entity-related repositories, code, and
organizational information. Uses the public GitHub API (no token required
for basic search, but GH_TOKEN env var enables higher rate limits).

Gate: ARIA_GITHUB_SEARCH_ENABLED=1 to enable (default ON).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Optional

import httpx
from .wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.github_search")

_ENABLED = os.getenv("ARIA_GITHUB_SEARCH_ENABLED", "1") == "1"
_TIMEOUT_S = 15.0
_API_BASE = "https://api.github.com"

# Rate limit: 60 req/hr unauthenticated, 5000 req/hr with token
_GH_TOKEN = os.getenv("GH_TOKEN", "") or os.getenv("ARIA_GITHUB_TOKEN", "")


def _headers() -> dict[str, str]:
    h = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "ARIA-OSINT/1.0 (Arkmurus Research)",
    }
    if _GH_TOKEN:
        h["Authorization"] = f"Bearer {_GH_TOKEN}"
    return h


@fail_wire(module="github_search", gap_type="api_missing")
async def search_code(query: str, max_results: int = 5) -> list[dict[str, Any]]:
    """Search GitHub code for a query.

    Args:
        query: GitHub code search query.
        max_results: Max results to return (default 5).

    Returns:
        List of {repo, path, url, language, score}.
    """
    if not _ENABLED:
        return []

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:  # no-breaker: GitHub search is best-effort OSINT; breaker would block code discovery
            resp = await client.get(
                f"{_API_BASE}/search/code",
                headers=_headers(),
                params={"q": query, "per_page": min(max_results, 20), "sort": "indexed"},
            )
            if resp.status_code == 403:
                logger.warning("[github_search] rate limited — try setting GH_TOKEN")
                return []
            if resp.status_code != 200:
                logger.debug("[github_search] code search returned %d", resp.status_code)
                return []

            data = resp.json()
            items = data.get("items", [])
            results = []
            for item in items[:max_results]:
                repo = item.get("repository", {})
                results.append({
                    "repo": repo.get("full_name", ""),
                    "path": item.get("path", ""),
                    "url": item.get("html_url", ""),
                    "language": item.get("language", ""),
                    "score": item.get("score", 0),
                })
            return results
    except Exception as e:
        logger.debug("[github_search] code search failed: %s", e)
        return []


@fail_wire(module="github_search", gap_type="api_missing")
async def search_repositories(
    query: str,
    max_results: int = 5,
    sort: str = "stars",
) -> list[dict[str, Any]]:
    """Search GitHub repositories.

    Args:
        query: GitHub repository search query.
        max_results: Max results to return.
        sort: Sort by "stars", "updated", or "best match".

    Returns:
        List of {name, url, description, stars, language, topics}.
    """
    if not _ENABLED:
        return []

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.get(
                f"{_API_BASE}/search/repositories",
                headers=_headers(),
                params={
                    "q": query,
                    "per_page": min(max_results, 20),
                    "sort": sort,
                },
            )
            if resp.status_code == 403:
                logger.warning("[github_search] rate limited — try setting GH_TOKEN")
                return []
            if resp.status_code != 200:
                return []

            data = resp.json()
            items = data.get("items", [])
            results = []
            for item in items[:max_results]:
                results.append({
                    "name": item.get("full_name", ""),
                    "url": item.get("html_url", ""),
                    "description": (item.get("description") or "")[:300],
                    "stars": item.get("stargazers_count", 0),
                    "language": item.get("language", ""),
                    "topics": item.get("topics", []),
                    "updated_at": item.get("updated_at", ""),
                })
            return results
    except Exception as e:
        logger.debug("[github_search] repo search failed: %s", e)
        return []


@fail_wire(module="github_search", gap_type="api_missing")
async def search_organization(org_name: str) -> Optional[dict[str, Any]]:
    """Get GitHub organization profile.

    Args:
        org_name: GitHub organization name.

    Returns:
        Dict with org details, or None.
    """
    if not _ENABLED:
        return None

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.get(
                f"{_API_BASE}/orgs/{org_name}",
                headers=_headers(),
            )
            if resp.status_code != 200:
                return None

            data = resp.json()
            return {
                "name": data.get("name", "") or data.get("login", ""),
                "url": data.get("html_url", ""),
                "description": (data.get("description") or "")[:300],
                "location": data.get("location", ""),
                "email": data.get("email", ""),
                "blog": data.get("blog", ""),
                "twitter": data.get("twitter_username", ""),
                "public_repos": data.get("public_repos", 0),
                "public_gists": data.get("public_gists", 0),
                "followers": data.get("followers", 0),
                "following": data.get("following", 0),
                "created_at": data.get("created_at", ""),
                "updated_at": data.get("updated_at", ""),
                "type": data.get("type", ""),
            }
    except Exception as e:
        logger.debug("[github_search] org lookup failed: %s", e)
        return None


@fail_wire(module="github_search", gap_type="api_missing")
async def search_company(company_name: str) -> dict[str, Any]:
    """Comprehensive GitHub search for a company.

    Searches repositories, code, and organization in parallel.

    Returns:
        Dict with repos, code_results, organization.
    """
    repos_task = search_repositories(company_name, max_results=5)
    code_task = search_code(company_name, max_results=5)
    org_task = search_organization(company_name)

    repos, code_results, org = await asyncio.gather(
        repos_task, code_task, org_task, return_exceptions=True,
    )

    return {
        "repositories": repos if isinstance(repos, list) else [],
        "code_results": code_results if isinstance(code_results, list) else [],
        "organization": org if isinstance(org, dict) else None,
    }


# ── Wire to brain ──────────────────────────────────────────────────────

try:
    from .engine_wiring import wire_success as _ws, wire_failure
    _ws(
        module="github_search",
        summary="GitHub Search Engine active",
        detail="Searches repos, code, orgs. Gate: ARIA_GITHUB_SEARCH_ENABLED=1",
        source_id="github_search:R-F1061",
    )
except Exception:
    pass

# R-F2119 §21a — wire failure handler for github_search
try:
    wire_failure(module="github_search", detail="module shutdown",
                gap_type="engine_failure", source="github_search:shutdown")
except Exception:
    pass

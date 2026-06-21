"""R-F1765 — self-verifying-deploy primitive.

The confabulation this kills: autonomous self-improvement claims a change is
"deployed" the moment it git-commits LOCALLY (self_improve.deploy_improvement)
— it never confirms the live server actually advanced to that commit. A commit
is NOT a deploy. This module is the atomic "did the change ACTUALLY land live?"
check: poll the app's /health/live, compare the live `build_rev` to the expected
commit SHA. If the live build_rev never advances to the SHA → the deploy did NOT
land, and the caller must report FAILURE, not a confabulated success.

Reuses the EXACT compare semantics already battle-tested in
scripts/live_health_check.py, scripts/deploy.{sh,ps1}, machines_deployer._verify_live
and aria_cli ci_deploy: the expected SHORT sha (first 8 hex) appears as a substring
of the live build_rev string (e.g. "R-F1765 · sha 1a939896" contains "1a939896").

Pure/testable by design: the network fetch is injectable (`fetcher=`), so the
verify logic is unit-tested without hitting fly.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Awaitable, Callable, Optional

logger = logging.getLogger("aria.deploy_verifier")

# App → public health endpoint. build_rev only meaningful for the Python brain.
_APP_HEALTH = {
    "aria-intel": "https://aria-intel.fly.dev/health/live",
}

# 8+ hex run = a git short-SHA inside the build_rev string.
_SHA_RE = re.compile(r"\b([0-9a-f]{8,40})\b", re.IGNORECASE)

# Default poll cadence mirrors the deploy scripts (36 × 5s ≈ 3 min) — long enough
# for a fly rolling restart, short enough to fail honestly if it never lands.
DEFAULT_ATTEMPTS = 36
DEFAULT_INTERVAL_S = 5.0

# A fetcher returns the live build_rev string (or None on unreachable).
Fetcher = Callable[[str], Awaitable[Optional[str]]]


def _short(sha: str) -> str:
    return (sha or "").strip().lower()[:8]


def build_rev_matches(build_rev: Optional[str], expected_sha: str) -> bool:
    """True iff the expected commit's short-SHA appears in the live build_rev.

    Substring match (not equality) so a build_rev like "R-F1765 · sha 1a939896"
    matches expected "1a939896c0ffee…". Empty/None never matches.
    """
    if not build_rev or not expected_sha:
        return False
    return _short(expected_sha) in build_rev.strip().lower()


def extract_live_sha(build_rev: Optional[str]) -> Optional[str]:
    """Pull the trailing/last hex SHA out of a build_rev string, for logging."""
    if not build_rev:
        return None
    hits = _SHA_RE.findall(build_rev)
    return hits[-1].lower() if hits else None


async def _http_fetch_build_rev(app: str, timeout: float = 15.0) -> Optional[str]:
    """Default fetcher: GET /health/live and return its build_rev field."""
    url = _APP_HEALTH.get(app)
    if not url:
        return None
    try:
        import httpx
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            return None
        try:
            data = resp.json()
            if isinstance(data, dict):
                return str(data.get("build_rev") or "")
        except Exception:
            return resp.text
    except Exception as exc:
        logger.debug("[deploy_verifier] fetch %s failed: %s", app, exc)
        return None
    return None


async def verify_deploy_landed(
    expected_sha: str,
    app: str = "aria-intel",
    *,
    fetcher: Optional[Fetcher] = None,
) -> dict:
    """Single-shot check: is `expected_sha` the live build_rev right now?

    Returns {"landed": bool, "expected": <short>, "live_build_rev": str|None,
             "live_sha": str|None, "app": app}. No polling — for the
    reconcile loop which has its own cadence.
    """
    _fetch = fetcher or _http_fetch_build_rev
    live = await _fetch(app)
    landed = build_rev_matches(live, expected_sha)
    return {
        "landed": landed,
        "expected": _short(expected_sha),
        "live_build_rev": live,
        "live_sha": extract_live_sha(live),
        "app": app,
    }


async def is_sha_live(
    expected_sha: str,
    app: str = "aria-intel",
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    interval_s: float = DEFAULT_INTERVAL_S,
    fetcher: Optional[Fetcher] = None,
) -> bool:
    """Poll until the live build_rev contains `expected_sha`, or give up.

    Returns True only when PROVEN live. False (honest failure) on timeout —
    NEVER assume success. This is the gate that turns "I committed it" into
    "it is provably running".
    """
    if not expected_sha:
        return False
    _fetch = fetcher or _http_fetch_build_rev
    short = _short(expected_sha)
    for i in range(max(1, attempts)):
        live = await _fetch(app)
        if build_rev_matches(live, expected_sha):
            logger.info("[deploy_verifier] ✅ %s LIVE serves %s (attempt %d)",
                        app, short, i + 1)
            return True
        logger.debug("[deploy_verifier] poll %d/%d: %s live=%r (want %s)",
                     i + 1, attempts, app, live, short)
        if i < attempts - 1:
            await asyncio.sleep(interval_s)
    logger.warning(
        "[deploy_verifier] ❌ %s did NOT advance to %s within %d attempts "
        "(~%.0fs) — deploy NOT verified live",
        app, short, attempts, attempts * interval_s,
    )
    return False


async def reconcile_committed_deploys(
    items: list[dict],
    *,
    app: str = "aria-intel",
    fetcher: Optional[Fetcher] = None,
) -> list[dict]:
    """Reconcile committed-but-unverified self-improve items against live.

    The deploy-proprioception step: ARIA's self-improve commits a change locally
    and the fly deploy is async (ci_deploy/CI). This checks, for each item that
    has a commit_sha and isn't yet confirmed live, whether the live build_rev now
    serves that SHA. PURE — returns verdicts; the caller decides what to record
    (mark truly deployed + truthful wire_success, or wire_failure
    deploy_verification_failure so self-heal/coder retries the deploy).

    A verdict: {"id", "commit_sha", "verified_live": bool, "live_build_rev"}.
    Items without a commit_sha, or already flagged verified_live, are skipped.
    """
    verdicts: list[dict] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        sha = str(it.get("commit_sha") or "").strip()
        if not sha or it.get("verified_live") is True:
            continue
        v = await verify_deploy_landed(sha, app, fetcher=fetcher)
        verdicts.append({
            "id": it.get("id"),
            "commit_sha": sha,
            "verified_live": v["landed"],
            "live_build_rev": v["live_build_rev"],
        })
    return verdicts

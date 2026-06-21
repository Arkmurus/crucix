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
import time
from typing import Awaitable, Callable, Optional

logger = logging.getLogger("aria.deploy_verifier")

# R-F1773 — UNIVERSAL deploy-intent ledger key. reconcile_committed_deploys (above)
# only covers self_improve items that carry a commit_sha; a RAW `git push` (as ARIA
# did for R-F1770) bypasses it entirely and can confabulate "deployed". This ledger
# closes that hole: a git pre-push hook POSTs EVERY push's HEAD sha here, and the
# proprioception loop verifies each one actually went live — no push escapes.
DEPLOY_INTENTS_KEY = "crucix:aria:deploy:intents"
MAX_INTENTS = 50
# CI/rolling-restart grace before an unlanded intent is declared a failure. Mirrors
# ARIA_DEPLOY_VERIFY_GRACE_S used by self_improve.reconcile_live_deploys.
DEFAULT_INTENT_GRACE_S = 1200.0

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


# ──────────────────────────────────────────────────────────────────────────
# R-F1773 — UNIVERSAL deploy-intent ledger (catches RAW push + ci_deploy +
# self_improve). Pure functions; the rs persistence + gap-recording live in the
# endpoint (routes/aria.py) and the proprioception loop (main.py).
# ──────────────────────────────────────────────────────────────────────────

def add_deploy_intent(
    intents: Optional[list],
    commit_sha: str,
    source: str,
    *,
    at: Optional[float] = None,
    max_keep: int = MAX_INTENTS,
) -> list[dict]:
    """Append a deploy-intent record (pure). Dedups by sha (a re-push of the same
    commit resets its verification clock), caps to the last `max_keep`.

    Each record: {commit_sha, source, at (epoch), verified_live, checks,
    failure_recorded}. Empty sha is a no-op (returns the list unchanged).
    """
    sha = (commit_sha or "").strip().lower()
    base = [i for i in (intents or []) if isinstance(i, dict)]
    if not sha:
        return base[-max_keep:]
    out = [i for i in base if str(i.get("commit_sha") or "").strip().lower() != sha]
    out.append({
        "commit_sha": sha,
        "source": source or "unknown",
        "at": float(at if at is not None else time.time()),
        "verified_live": False,
        "checks": 0,
        "failure_recorded": False,
    })
    return out[-max_keep:]


async def reconcile_deploy_intents(
    intents: Optional[list],
    *,
    app: str = "aria-intel",
    fetcher: Optional[Fetcher] = None,
    grace_s: float = DEFAULT_INTENT_GRACE_S,
    now: Optional[float] = None,
) -> tuple[list[dict], list[dict]]:
    """Reconcile the universal intent ledger against the live build_rev (pure-ish;
    network is injectable). For each not-yet-verified intent:
      • live build_rev now serves its SHA → mark verified_live=True.
      • else still within grace → leave pending (a fly rolling restart takes time).
      • else past grace and not yet flagged → emit a failure gap ONCE
        (failure_recorded=True so it isn't re-emitted every tick).

    Returns (updated_intents, gaps). gaps feed capability_gaps.record_gap so the
    self-heal/coder loop retries a push that silently never went live.
    """
    _now = float(now if now is not None else time.time())
    updated: list[dict] = []
    gaps: list[dict] = []
    for it in intents or []:
        if not isinstance(it, dict):
            continue
        rec = dict(it)
        sha = str(rec.get("commit_sha") or "").strip()
        if not sha or rec.get("verified_live") is True:
            updated.append(rec)
            continue
        v = await verify_deploy_landed(sha, app, fetcher=fetcher)
        rec["checks"] = int(rec.get("checks") or 0) + 1
        if v["landed"]:
            rec["verified_live"] = True
            rec["live_build_rev"] = v["live_build_rev"]
        else:
            age = _now - float(rec.get("at") or _now)
            if age >= grace_s and not rec.get("failure_recorded"):
                rec["failure_recorded"] = True
                gaps.append({
                    "commit_sha": sha,
                    "source": rec.get("source"),
                    "live_build_rev": v["live_build_rev"],
                    "age_s": round(age, 1),
                })
        updated.append(rec)
    return updated, gaps


async def reconcile_intents_via_store(
    rs_module,
    *,
    app: str = "aria-intel",
    fetcher: Optional[Fetcher] = None,
    grace_s: float = DEFAULT_INTENT_GRACE_S,
    gap_recorder: Optional[Callable[[dict], Awaitable[None]]] = None,
) -> dict:
    """The proprioception loop's GLUE, module-level so it is actually TESTABLE.

    Reads the intent ledger from `rs_module` (must expose async get_json(key) /
    set_json(key, val, ex=...)), reconciles each intent against the live build_rev,
    persists the updated ledger, and feeds any failure gaps to `gap_recorder`.

    This exists because the first cut inlined this glue in main.py with a WRONG
    store import (`from .state_store import rs` — no such module; the real one is
    intel.redis_store) which raised every tick, caught+logged, so the verification
    loop was SILENTLY dead (checks stayed 0). A module-level function with an
    injected store + gap_recorder lets a capability test prove the whole path runs.

    Returns {"verified", "failed", "pending"}.
    """
    intents = await rs_module.get_json(DEPLOY_INTENTS_KEY) or []
    if not intents:
        return {"verified": 0, "failed": 0, "pending": 0}
    updated, gaps = await reconcile_deploy_intents(
        intents, app=app, fetcher=fetcher, grace_s=grace_s)
    await rs_module.set_json(DEPLOY_INTENTS_KEY, updated, ex=30 * 86400)
    if gap_recorder:
        for g in gaps:
            try:
                await gap_recorder(g)
            except Exception as exc:  # a gap-record failure never breaks reconcile
                logger.warning("[deploy_verifier] gap_recorder failed: %s", exc)
    verified = sum(1 for i in updated if isinstance(i, dict) and i.get("verified_live"))
    pending = sum(1 for i in updated if isinstance(i, dict) and not i.get("verified_live"))
    return {"verified": verified, "failed": len(gaps), "pending": pending}

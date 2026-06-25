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
from ..intel.wire import fail_wire  # R-F1789 §21 brain-wiring

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


@fail_wire(module="deploy_verifier", gap_type="agent_cycle_failure")
def build_rev_matches(build_rev: Optional[str], expected_sha: str) -> bool:
    """True iff the expected commit's short-SHA appears in the live build_rev.

    Substring match (not equality) so a build_rev like "R-F1765 · sha 1a939896"
    matches expected "1a939896c0ffee…". Empty/None never matches.
    """
    if not build_rev or not expected_sha:
        return False
    return _short(expected_sha) in build_rev.strip().lower()


@fail_wire(module="deploy_verifier", gap_type="agent_cycle_failure")
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


@fail_wire(module="deploy_verifier", gap_type="agent_cycle_failure")
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


@fail_wire(module="deploy_verifier", gap_type="agent_cycle_failure")
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


@fail_wire(module="deploy_verifier", gap_type="agent_cycle_failure")
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

@fail_wire(module="deploy_verifier", gap_type="agent_cycle_failure")
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


@fail_wire(module="deploy_verifier", gap_type="agent_cycle_failure")
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
      • else SUPERSEDED (a newer push landed live) → mark superseded, NO gap. A commit
        that was live but got replaced by a newer deploy before we verified it did NOT
        fail — flagging it would be crying-wolf confabulation. This matters when an
        older intent sat un-verified (e.g. the loop was down) then a newer deploy went out.
      • else past grace and not superseded → emit a failure gap ONCE
        (failure_recorded=True so it isn't re-emitted every tick).

    Returns (updated_intents, gaps). gaps feed capability_gaps.record_gap so the
    self-heal/coder loop retries a push that silently never went live.
    """
    _now = float(now if now is not None else time.time())
    # Pass 1: resolve each intent's live status once (avoid double-fetching).
    landed_map: dict[int, dict] = {}
    for idx, it in enumerate(intents or []):
        if isinstance(it, dict) and it.get("verified_live") is not True:
            sha = str(it.get("commit_sha") or "").strip()
            if sha:
                landed_map[idx] = await verify_deploy_landed(sha, app, fetcher=fetcher)
    # The most-recent `at` among intents confirmed live THIS run (or already verified)
    # — any older un-landed intent has been superseded by it.
    latest_live_at = None
    for idx, it in enumerate(intents or []):
        if not isinstance(it, dict):
            continue
        is_live = it.get("verified_live") is True or (landed_map.get(idx, {}).get("landed"))
        if is_live:
            at = float(it.get("at") or 0.0)
            latest_live_at = at if latest_live_at is None else max(latest_live_at, at)

    updated: list[dict] = []
    gaps: list[dict] = []
    for idx, it in enumerate(intents or []):
        if not isinstance(it, dict):
            continue
        rec = dict(it)
        sha = str(rec.get("commit_sha") or "").strip()
        if not sha or rec.get("verified_live") is True:
            updated.append(rec)
            continue
        v = landed_map.get(idx) or await verify_deploy_landed(sha, app, fetcher=fetcher)
        rec["checks"] = int(rec.get("checks") or 0) + 1
        if v["landed"]:
            rec["verified_live"] = True
            rec["live_build_rev"] = v["live_build_rev"]
        else:
            age = _now - float(rec.get("at") or _now)
            superseded = (latest_live_at is not None
                          and float(rec.get("at") or 0.0) < latest_live_at)
            if superseded:
                # newer deploy is live → this one was replaced, not failed.
                rec["superseded"] = True
                rec["failure_recorded"] = True  # suppress any future failure gap
            elif age >= grace_s and not rec.get("failure_recorded"):
                rec["failure_recorded"] = True
                gaps.append({
                    "commit_sha": sha,
                    "source": rec.get("source"),
                    "live_build_rev": v["live_build_rev"],
                    "age_s": round(age, 1),
                })
        updated.append(rec)
    return updated, gaps


@fail_wire(module="deploy_verifier", gap_type="agent_cycle_failure")
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


# ──────────────────────────────────────────────────────────────────────────
# R-F1920 — ORIGIN-vs-LIVE reconciler. The intent ledger (R-F1773) catches a
# push that doesn't go live but only records a CODER gap — which the coder
# cannot action for human/Claude-authored commits (its deploy path ships only
# its OWN self-improvements). So when a batch of Claude commits sat undeployed,
# nobody told the operator and he discovered it himself — the §19e worst case.
# This reconciler uses GitHub as the authoritative "what SHOULD be live"
# (origin/<branch> HEAD via GH_TOKEN — robust, independent of the pre-push hook
# that the intent ledger relies on) and ALERTS THE OPERATOR when the live
# build_rev sits behind origin past a threshold. Detect+alert only: it never
# auto-deploys (deploying arbitrary origin commits from the brain — no review,
# no canary/rollback — is too risky to do unsupervised).
# ──────────────────────────────────────────────────────────────────────────

ORIGIN_RECONCILE_STATE_KEY = "crucix:aria:deploy:origin_behind_state"
# How long live may lag origin before we alert. Shorter than the 1200s intent
# grace, but longer than a normal fly rolling deploy (~3-5 min) so an in-flight
# deploy never cries wolf.
DEFAULT_BEHIND_ALERT_S = 600.0
_GITHUB_REPO = "Arkmurus/crucix"

# A GitHub fetcher returns origin/<branch> HEAD sha (or None on unreachable).
GhFetcher = Callable[[str, str, Optional[str]], Awaitable[Optional[str]]]


async def _http_github_head_sha(
    repo: str, branch: str, token: Optional[str], timeout: float = 15.0
) -> Optional[str]:
    """Default GH fetcher: origin/<branch> HEAD sha via the GitHub commits API."""
    try:
        import httpx
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        url = f"https://api.github.com/repos/{repo}/commits/{branch}"
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            logger.debug("[deploy_verifier] github head %s@%s -> %s",
                         repo, branch, resp.status_code)
            return None
        data = resp.json()
        if isinstance(data, dict) and data.get("sha"):
            return str(data["sha"])
    except Exception as exc:
        logger.debug("[deploy_verifier] github head fetch failed: %s", exc)
    return None


@fail_wire(module="deploy_verifier", gap_type="agent_cycle_failure")
async def reconcile_origin_vs_live(
    *,
    state: Optional[dict],
    app: str = "aria-intel",
    repo: str = _GITHUB_REPO,
    branch: str = "main",
    token: Optional[str] = None,
    gh_fetcher: Optional[GhFetcher] = None,
    live_fetcher: Optional[Fetcher] = None,
    behind_alert_s: float = DEFAULT_BEHIND_ALERT_S,
    now: Optional[float] = None,
) -> dict:
    """Is origin/<branch> HEAD actually live? Pure-ish (both fetches injectable).

    `state` persists across ticks: {"origin_sha", "first_seen_at", "alerted"}.
    Logic:
      • origin HEAD is live → clear state (all good).
      • origin HEAD undetermined (API down / no token) → no-op, keep state.
      • origin ahead of live → track since-when; if it moved, RESET the clock
        (a brand-new commit just landed — give the deploy time). When the SAME
        head has been behind ≥ behind_alert_s and we haven't alerted for it yet
        → return an `alert` (and flip alerted=True so it fires ONCE per head).

    Returns {"behind", "origin_sha", "live_sha", "live_build_rev", "age_s",
             "alert": dict|None, "state": <new state>}.
    """
    _now = float(now if now is not None else time.time())
    _gh = gh_fetcher or _http_github_head_sha
    _live = live_fetcher or _http_fetch_build_rev
    st = dict(state or {})

    origin_sha = await _gh(repo, branch, token)
    live_build_rev = await _live(app)
    out = {
        "behind": False,
        "origin_sha": _short(origin_sha) if origin_sha else None,
        "live_sha": extract_live_sha(live_build_rev),
        "live_build_rev": live_build_rev,
        "age_s": 0.0,
        "alert": None,
        "state": st,
    }
    if not origin_sha:
        return out  # can't determine origin → don't touch state, don't alert
    if build_rev_matches(live_build_rev, origin_sha):
        out["state"] = {}  # origin is live → clear any behind-tracking
        return out

    # origin is AHEAD of live. Reset the clock if the head moved (new push).
    if st.get("origin_sha") != _short(origin_sha):
        st = {"origin_sha": _short(origin_sha), "first_seen_at": _now, "alerted": False}
    _fs = st.get("first_seen_at")  # explicit None-check: 0.0 is a valid epoch, not "missing"
    age = _now - (float(_fs) if _fs is not None else _now)
    out["behind"] = True
    out["age_s"] = round(age, 1)
    out["state"] = st
    if age >= behind_alert_s and not st.get("alerted"):
        st["alerted"] = True
        out["alert"] = {
            "origin_sha": _short(origin_sha),
            "live_sha": out["live_sha"],
            "live_build_rev": live_build_rev,
            "age_s": round(age, 1),
        }
    return out


@fail_wire(module="deploy_verifier", gap_type="agent_cycle_failure")
async def reconcile_origin_via_store(
    rs_module,
    *,
    app: str = "aria-intel",
    repo: str = _GITHUB_REPO,
    branch: str = "main",
    token: Optional[str] = None,
    gh_fetcher: Optional[GhFetcher] = None,
    live_fetcher: Optional[Fetcher] = None,
    behind_alert_s: float = DEFAULT_BEHIND_ALERT_S,
    operator_notifier: Optional[Callable[[dict], Awaitable[None]]] = None,
) -> dict:
    """Proprioception-loop glue (module-level so it is testable): read the
    behind-state from `rs_module`, reconcile origin vs live, persist the new
    state, and call `operator_notifier(alert)` when live has lagged past grace.

    Returns {"behind", "age_s", "origin_sha", "live_sha", "alerted"}.
    """
    state = await rs_module.get_json(ORIGIN_RECONCILE_STATE_KEY) or {}
    res = await reconcile_origin_vs_live(
        state=state, app=app, repo=repo, branch=branch, token=token,
        gh_fetcher=gh_fetcher, live_fetcher=live_fetcher, behind_alert_s=behind_alert_s)
    await rs_module.set_json(ORIGIN_RECONCILE_STATE_KEY, res["state"], ex=30 * 86400)
    if res.get("alert") and operator_notifier:
        try:
            await operator_notifier(res["alert"])
        except Exception as exc:  # an alert failure never breaks reconcile
            logger.warning("[deploy_verifier] origin operator_notifier failed: %s", exc)
    return {
        "behind": res["behind"],
        "age_s": res["age_s"],
        "origin_sha": res.get("origin_sha"),
        "live_sha": res.get("live_sha"),
        "alerted": bool(res.get("alert")),
    }

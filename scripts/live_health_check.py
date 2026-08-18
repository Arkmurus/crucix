#!/usr/bin/env python
"""R-F1125 — Live health regression suite for post-deploy verification.

Runs health checks against the LIVE aria-intel server (not TestClient).
This catches boot failures, config mismatches, and environment-specific bugs that
unit tests miss.

Usage:
    python scripts/live_health_check.py              # default: aria-intel
    python scripts/live_health_check.py --app web    # aria-web
    python scripts/live_health_check.py --app wa     # aria-wa
    python scripts/live_health_check.py --app all    # all three

Exit code: 0 if all checks pass, 1 if any fail.
"""
from __future__ import annotations

import json
import re          # R-F3552 — parse the live sha out of build_rev
import subprocess  # R-F3552 — git ancestry proof
import sys
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent

# Read expected SHA from the file written by deploy.sh/deploy.ps1. This is only a
# FALLBACK — R-F1478: the deploy passes --expected-sha explicitly so the check
# verifies what THIS deploy actually shipped. The file is shared mutable state:
# ARIA's autonomous ci_deploy commits as the operator and overwrites
# .last_deploy_sha mid-deploy, which made the regression compare the live app
# (correct commit) against the wrong sha and false-fail every manual deploy
# (cry-wolf — masks a real outage). The explicit arg is immune to that race.
_SHA_FILE = REPO_ROOT / ".last_deploy_sha"
_FILE_SHA = ""
if _SHA_FILE.exists():
    _FILE_SHA = _SHA_FILE.read_text(encoding="utf-8").strip()

# health_check(data, expected_sha) — expected_sha threaded in (not a module
# global) so the value is fixed at call time, not at import time.
APPS = {
    "intel": {
        "url": "https://aria-intel.fly.dev",
        "health_endpoint": "/health/live",
        "health_check": lambda d, exp: (
            isinstance(d, dict)
            and d.get("status") == "alive"
        ),
    },
    "web": {
        "url": "https://aria-web.fly.dev",
        "health_endpoint": "/api/health",
        "health_check": lambda d, exp: isinstance(d, dict) and d.get("status") == "ok",
    },
    "wa": {
        "url": "https://aria-wa.fly.dev",
        "health_endpoint": "/health",
        "health_check": lambda d, exp: isinstance(d, dict) and d.get("status") == "connected",
    },
}


def fetch_json(client: httpx.Client, url: str, timeout: int = 15) -> dict | str | None:
    """Fetch a URL and parse JSON. Returns None on failure."""
    try:
        resp = client.get(url, timeout=timeout)
        resp.raise_for_status()
        raw = resp.text
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw.strip()
    except (httpx.HTTPError, httpx.TimeoutException, OSError) as e:
        print(f"  WARNING: HTTP error: {e}")
        return None


def _live_contains_expected(data, expected_sha: str) -> bool:
    """R-F3552 — is the LIVE sha a descendant of the one this deploy shipped?

    THE CRY-WOLF THIS REMOVES. The check is `expected_sha in build_rev`, a string match.
    On a shared tree a peer's deploy routinely batches several commits, so the live
    build_rev is a DESCENDANT of mine rather than mine — and the substring misses. On
    2026-07-31 that produced:

        FAIL: intel: health check FAILED — response:
              {'build_rev': 'R-F3548+R-F3549+R-F3550+R-F3551 · sha 18fd5eb8'}

    while `git merge-base --is-ancestor 464a28a8 18fd5eb8` proved the commit WAS live.
    R-F1478 already fixed one false-fail here (a concurrent ci_deploy overwriting
    `.last_deploy_sha`); this is the other one. It cost time three times in one session,
    and per the standing rule a guard that cries wolf gets switched off — which would
    cost far more than it saves.

    FAILS CLOSED. Ancestry is only accepted when git PROVES it. No git, no repo, an
    unparseable build_rev, or a non-zero exit all return False: "cannot prove it is live"
    must never render as "it is live", which is the whole point of a deploy check.
    """
    if not expected_sha or not isinstance(data, dict):
        return False
    rev = str(data.get("build_rev") or "")
    m = re.search(r"\bsha\s+([0-9a-f]{7,40})\b", rev) or re.search(r"\b([0-9a-f]{7,40})\b", rev)
    if not m:
        return False
    live = m.group(1)
    if live.startswith(expected_sha) or expected_sha.startswith(live):
        return True                     # same commit, differently abbreviated
    try:
        r = subprocess.run(
            ["git", "merge-base", "--is-ancestor", expected_sha, live],
            cwd=str(Path(__file__).resolve().parent.parent),
            capture_output=True, timeout=20,
        )
        return r.returncode == 0
    except Exception:                   # noqa: BLE001 — unprovable is NOT proven
        return False


def check_app_health(client: httpx.Client, app_name: str, config: dict, expected_sha: str) -> bool:
    """Check that the app is alive and serving the expected build."""
    print(f"\n--- {app_name}: health check ---")
    health_url = f"{config['url']}{config['health_endpoint']}"
    data = fetch_json(client, health_url)
    if data is None:
        print(f"  FAIL: {app_name}: UNREACHABLE at {health_url}")
        return False

    if not config["health_check"](data, expected_sha):
        print(f"  FAIL: {app_name}: health check FAILED — response: {str(data)[:200]}")
        return False

    rev = data.get("build_rev", "?") if isinstance(data, dict) else "?"
    if expected_sha and expected_sha in str(rev):
        print(f"  PASS: {app_name}: alive (build_rev={rev})")
        return True
    if _live_contains_expected(data, expected_sha):
        # R-F3552 — a peer's batch shipped a DESCENDANT of this commit. The code IS
        # live; the sha simply moved past it.
        print(f"  PASS: {app_name}: alive and CONTAINS {expected_sha} "
              f"(build_rev={rev} — a later batch shipped past this commit)")
        return True
    print(
        f"  FAIL: {app_name}: alive but expected build {expected_sha} is not proven "
        f"(build_rev={rev})"
    )
    return False


def select_apps(args: list) -> "list | None":
    """Resolve `--app` to the tiers to check. Returns None on a bad selection.

    R-F2868 — accepts a COMMA-SEPARATED list so a deploy can name exactly the
    tiers it shipped. Previously `--app` took one tier or `all`, so a two-tier
    deploy had no way to express itself and `deploy.ps1` passed `--app all`.
    That asserted the new sha against apps the deploy never touched, and a
    correct `-Web` deploy printed a red FAIL because intel was legitimately on
    the previous commit (observed live on R-F2867).

    An unknown tier is REJECTED rather than skipped: silently dropping a typo
    (`--app intel,wbe`) would run a green suite that never checked the web tier
    — a false clean manufactured by a spelling mistake.
    """
    args = [a.lower() for a in args]
    if "--app" not in args:
        return ["intel"]                       # unchanged default
    idx = args.index("--app")
    if idx + 1 >= len(args):
        print("--app requires an argument (intel/web/wa/all, or a comma-separated list)")
        return None
    target = args[idx + 1]
    if target == "all":
        return list(APPS.keys())
    selected = []
    for name in (part.strip() for part in target.split(",")):
        if not name:
            continue
        if name not in APPS:
            print(f"Unknown app: {name}")
            return None
        if name not in selected:               # tolerate duplicates
            selected.append(name)
    if not selected:
        print("--app requires at least one tier (intel/web/wa/all)")
        return None
    return selected


def main() -> int:
    args = [a.lower() for a in sys.argv[1:]]

    apps_to_check = select_apps(args)
    if apps_to_check is None:
        return 1

    # R-F1478: prefer the explicitly-passed sha (what THIS deploy shipped) over
    # the shared .last_deploy_sha file, which a concurrent ci_deploy can overwrite.
    expected_sha = ""
    sha_source = "none"
    if "--expected-sha" in args:
        idx = args.index("--expected-sha")
        if idx + 1 < len(args):
            expected_sha = args[idx + 1].strip()
            sha_source = "cli"
    if not expected_sha and _FILE_SHA:
        expected_sha = _FILE_SHA
        sha_source = ".last_deploy_sha"

    print(f"=== Live health regression suite (R-F1125) ===")
    print(f"  expected build: {expected_sha or '(none)'} (source: {sha_source})")
    print(f"  apps: {', '.join(apps_to_check)}")
    print()

    failures = 0
    with httpx.Client() as client:
        for app_name in apps_to_check:
            config = APPS[app_name]
            if not check_app_health(client, app_name, config, expected_sha):
                failures += 1

    print()
    if failures == 0:
        print(f"=== ALL CHECKS PASSED (commit {expected_sha}) ===")
        return 0
    else:
        print(f"=== {failures} app(s) FAILED checks ===")
        return 1


if __name__ == "__main__":
    sys.exit(main())

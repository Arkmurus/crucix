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
            and bool(exp)
            and exp in d.get("build_rev", "")
        ),
    },
    "web": {
        "url": "https://aria-web.fly.dev",
        "health_endpoint": "/healthz",
        "health_check": lambda d, exp: d == "ok" if isinstance(d, str) else d.get("status") == "ok",
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


def check_app_health(client: httpx.Client, app_name: str, config: dict, expected_sha: str) -> bool:
    """Check that the app is alive and serving the expected build."""
    print(f"\n--- {app_name}: health check ---")
    health_url = f"{config['url']}{config['health_endpoint']}"
    data = fetch_json(client, health_url)
    if data is None:
        print(f"  FAIL: {app_name}: UNREACHABLE at {health_url}")
        return False

    if config["health_check"](data, expected_sha):
        if isinstance(data, dict):
            rev = data.get("build_rev", "?")
            print(f"  PASS: {app_name}: alive (build_rev={rev})")
        else:
            print(f"  PASS: {app_name}: alive (response={data})")
        return True
    else:
        print(f"  FAIL: {app_name}: health check FAILED — response: {str(data)[:200]}")
        return False


def main() -> int:
    apps_to_check = []
    args = [a.lower() for a in sys.argv[1:]]

    if "--app" in args:
        idx = args.index("--app")
        if idx + 1 < len(args):
            target = args[idx + 1]
            if target == "all":
                apps_to_check = list(APPS.keys())
            elif target in APPS:
                apps_to_check = [target]
            else:
                print(f"Unknown app: {target}")
                return 1
        else:
            print("--app requires an argument (intel/web/wa/all)")
            return 1
    else:
        apps_to_check = ["intel"]

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

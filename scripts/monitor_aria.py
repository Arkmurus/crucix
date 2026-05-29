#!/usr/bin/env python
"""R-F1079 — ARIA Production Monitor.

Checks fly logs via API and health endpoints every 30 minutes.
Compiles findings into data/monitor_reports/.

Usage:
    python scripts/monitor_aria.py              # Single check
    python scripts/monitor_aria.py --loop       # Every 30 min
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data" / "monitor_reports"
DATA_DIR.mkdir(parents=True, exist_ok=True)

APPS = [
    {"name": "aria-intel", "health_url": "https://aria-intel.fly.dev/health"},
    {"name": "aria-web", "health_url": "https://aria-web.fly.dev/healthz"},
    {"name": "aria-wa", "health_url": "https://aria-wa.fly.dev/health"},
]

FLY_API_TOKEN = os.environ.get("FLY_API_TOKEN", "")
FLY_GRAPHQL_URL = "https://api.fly.io/graphql"


def fetch_fly_logs(app_name: str, limit: int = 100) -> list[dict]:
    """Fetch recent logs from a fly app via GraphQL API."""
    if not FLY_API_TOKEN:
        return [{"error": "FLY_API_TOKEN not set"}]

    query = """
    {
      app(name: "%s") {
        logs(limit: %d) {
          edges {
            node {
              timestamp
              message
              level
              region
            }
          }
        }
      }
    }
    """ % (app_name, limit)

    try:
        r = httpx.post(
            FLY_GRAPHQL_URL,
            json={"query": query},
            headers={"Authorization": f"Bearer {FLY_API_TOKEN}"},
            timeout=15,
        )
        if r.status_code == 200:
            data = r.json()
            edges = data.get("data", {}).get("app", {}).get("logs", {}).get("edges", [])
            return [e["node"] for e in edges]
        return [{"error": f"HTTP {r.status_code}: {r.text[:200]}"}]
    except Exception as e:
        return [{"error": str(e)}]


def fetch_logs_via_health_api(app_name: str) -> list[dict]:
    """Fallback: fetch logs via the app's own health/debug endpoints."""
    urls = {
        "aria-intel": "https://aria-intel.fly.dev/api/aria/health/perf",
    }
    url = urls.get(app_name)
    if not url:
        return [{"info": f"No debug endpoint for {app_name}"}]
    try:
        r = httpx.get(url, timeout=10)
        if r.status_code == 200:
            return [{"health_data": r.text[:1000]}]
        return [{"error": f"HTTP {r.status_code}"}]
    except Exception as e:
        return [{"error": str(e)}]


def analyze_logs(logs: list[dict], app_name: str) -> dict[str, Any]:
    """Analyze logs for errors, warnings, and patterns."""
    result: dict[str, Any] = {
        "app": app_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "error_count": 0,
        "warning_count": 0,
        "critical_count": 0,
        "errors": [],
        "warnings": [],
        "patterns": {},
    }

    for entry in logs:
        level = (entry.get("level") or "").upper()
        message = entry.get("message") or entry.get("error") or ""

        if level == "ERROR":
            result["error_count"] += 1
            result["errors"].append(message[:300])
        elif level == "CRITICAL":
            result["critical_count"] += 1
            result["errors"].append(message[:300])
        elif level == "WARNING":
            result["warning_count"] += 1
            result["warnings"].append(message[:300])

        # Pattern detection on message text
        msg_lower = message.lower()
        if "rate limit hit" in msg_lower:
            result["patterns"]["coder_rate_limited"] = True
        if "concurrency cap" in msg_lower:
            result["patterns"]["brain_concurrency_cap"] = True
        if "neural: timeout" in msg_lower:
            result["patterns"]["neural_timeout"] = True
        if "traceback" in msg_lower or "traceback" in str(entry):
            result["patterns"]["traceback"] = True
        if "event loop stalled" in msg_lower:
            result["patterns"]["event_loop_stall"] = True

    return result


def check_health(app: dict) -> dict[str, Any]:
    """Check the health endpoint of an app."""
    try:
        r = httpx.get(app["health_url"], timeout=10)
        return {
            "app": app["name"],
            "status_code": r.status_code,
            "body_preview": r.text[:300],
            "healthy": r.status_code == 200,
        }
    except httpx.TimeoutException:
        return {"app": app["name"], "error": "timeout", "healthy": False}
    except httpx.ConnectError:
        return {"app": app["name"], "error": "connection refused", "healthy": False}
    except Exception as e:
        return {"app": app["name"], "error": str(e), "healthy": False}


def run_monitor() -> dict[str, Any]:
    """Run a full monitor cycle."""
    report: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "apps": {},
        "health": {},
        "summary": {},
    }

    for app in APPS:
        name = app["name"]
        logs = fetch_fly_logs(name)
        if not logs or logs == [{"error": "FLY_API_TOKEN not set"}] or any(
            "error" in l for l in logs
        ):
            # Fallback to health API
            logs = fetch_logs_via_health_api(name)
        report["apps"][name] = analyze_logs(logs, name)
        report["health"][name] = check_health(app)

    # Summary
    total_errors = sum(report["apps"][a["name"]]["error_count"] for a in APPS)
    total_warnings = sum(report["apps"][a["name"]]["warning_count"] for a in APPS)
    total_criticals = sum(report["apps"][a["name"]]["critical_count"] for a in APPS)
    unhealthy = [a["name"] for a in APPS if not report["health"][a["name"]].get("healthy", False)]

    report["summary"] = {
        "total_errors": total_errors,
        "total_warnings": total_warnings,
        "total_criticals": total_criticals,
        "unhealthy_apps": unhealthy,
        "patterns_found": list(set(
            p for a in APPS for p in report["apps"][a["name"]].get("patterns", {})
        )),
    }

    # Save
    filename = f"monitor_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.json"
    path = DATA_DIR / filename
    with open(path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"Monitor report saved to {path}")

    return report


def print_report(report: dict[str, Any]) -> None:
    """Print a human-readable summary."""
    s = report["summary"]
    print(f"\n=== ARIA Monitor Report ===")
    print(f"Time: {report['timestamp']}")
    print(f"Errors: {s['total_errors']} | Warnings: {s['total_warnings']} | Criticals: {s['total_criticals']}")
    print(f"Unhealthy: {s['unhealthy_apps'] or 'none'}")
    print(f"Patterns: {s['patterns_found'] or 'none'}")
    print()

    for app in APPS:
        name = app["name"]
        a = report["apps"].get(name, {})
        h = report["health"].get(name, {})
        status = "OK" if h.get("healthy") else "DOWN"
        print(f"  [{status}] {name}: {a.get('error_count', 0)} errors, {a.get('warning_count', 0)} warnings")
        if a.get("errors"):
            for e in a["errors"][:5]:
                print(f"    ! {e[:200]}")
        if a.get("patterns"):
            for p, v in a["patterns"].items():
                if v:
                    print(f"    PATTERN: {p}")


def main() -> None:
    if "--loop" in sys.argv:
        print("Starting ARIA monitor loop (30-min intervals)...")
        while True:
            report = run_monitor()
            print_report(report)
            next_time = datetime.now(timezone.utc).replace(second=0).isoformat()
            print(f"\nNext check at {next_time}")
            time.sleep(1800)
    else:
        report = run_monitor()
        print_report(report)


if __name__ == "__main__":
    main()

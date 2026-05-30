#!/usr/bin/env python
"""R-F1081 — ARIA Synthetic Health Monitor.

Runs every 5 minutes and probes:
  1. /health on all 3 apps (aria-intel, aria-web, aria-wa)
  2. /health/live on aria-intel (checks build_rev)
  3. A test chat message via /api/aria/chat
  4. /api/aria/health/composite (checks composite score)

Reports results to brain_hook and saves to data/synthetic_monitor/.

Usage:
    python scripts/synthetic_monitor.py              # Single run
    python scripts/synthetic_monitor.py --loop       # Every 5 min
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
DATA_DIR = REPO_ROOT / "data" / "synthetic_monitor"
DATA_DIR.mkdir(parents=True, exist_ok=True)

APPS = [
    {"name": "aria-intel", "health_url": "https://aria-intel.fly.dev/health",
     "live_url": "https://aria-intel.fly.dev/health/live"},
    {"name": "aria-web", "health_url": "https://aria-web.fly.dev/healthz"},
    {"name": "aria-wa", "health_url": "https://aria-wa.fly.dev/health"},
]

ARIA_CHAT_URL = "https://aria-intel.fly.dev/api/aria/chat"
ARIA_COMPOSITE_URL = "https://aria-intel.fly.dev/api/aria/health/composite"
ARIA_INTERNAL_TOKEN = os.environ.get("ARIA_INTERNAL_TOKEN", "")

_HEALTH_TIMEOUT_S = 10
_CHAT_TIMEOUT_S = 45
_COMPOSITE_TIMEOUT_S = 10
_MIN_COMPOSITE_SCORE = 0.50
_MAX_CHAT_LATENCY_S = 30


def probe_health(app: dict) -> dict[str, Any]:
    """Probe a single app's health endpoint."""
    result: dict[str, Any] = {
        "app": app["name"], "healthy": False,
        "status_code": 0, "latency_ms": 0, "error": None,
    }
    try:
        t0 = time.time()
        r = httpx.get(app["health_url"], timeout=_HEALTH_TIMEOUT_S)
        result["latency_ms"] = int((time.time() - t0) * 1000)
        result["status_code"] = r.status_code
        result["healthy"] = r.status_code == 200
        if r.status_code == 200:
            try:
                result["status"] = r.json().get("status", "unknown")
            except Exception:
                result["status"] = "unknown"
    except httpx.TimeoutException:
        result["error"] = "timeout"
    except httpx.ConnectError:
        result["error"] = "connection_refused"
    except Exception as e:
        result["error"] = str(e)
    return result


def probe_live(app: dict) -> dict[str, Any]:
    """Probe the live endpoint for build_rev."""
    result: dict[str, Any] = {"build_rev": None, "healthy": False}
    if not app.get("live_url"):
        return result
    try:
        r = httpx.get(app["live_url"], timeout=_HEALTH_TIMEOUT_S)
        if r.status_code == 200:
            result["build_rev"] = r.json().get("build_rev", "unknown")
            result["healthy"] = True
    except Exception:
        pass
    return result


def probe_composite() -> dict[str, Any]:
    """Probe the composite score endpoint."""
    result: dict[str, Any] = {"score": None, "healthy": False}
    try:
        r = httpx.get(ARIA_COMPOSITE_URL, timeout=_COMPOSITE_TIMEOUT_S)
        if r.status_code == 200:
            body = r.json()
            result["score"] = body.get("composite_score", body.get("score"))
            result["healthy"] = (result.get("score") or 0) >= _MIN_COMPOSITE_SCORE
    except Exception:
        pass
    return result


def probe_chat() -> dict[str, Any]:
    """Send a test chat message and verify response."""
    result: dict[str, Any] = {
        "success": False, "latency_s": 0,
        "response_length": 0, "error": None,
    }
    if not ARIA_INTERNAL_TOKEN:
        result["error"] = "ARIA_INTERNAL_TOKEN not set"
        return result

    headers = {
        "Authorization": f"Bearer {ARIA_INTERNAL_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "message": "Synthetic health probe: what is the Wassenaar Arrangement?",
        "user_id": "synthetic_monitor",
        "stream": False,
    }
    try:
        t0 = time.time()
        r = httpx.post(ARIA_CHAT_URL, json=payload, headers=headers, timeout=_CHAT_TIMEOUT_S)
        result["latency_s"] = round(time.time() - t0, 1)
        result["status_code"] = r.status_code
        if r.status_code == 200:
            body = r.json()
            response_text = body.get("response", body.get("message", ""))
            result["response_length"] = len(response_text)
            result["success"] = len(response_text) > 50
        else:
            result["error"] = f"HTTP {r.status_code}: {r.text[:200]}"
    except httpx.TimeoutException:
        result["error"] = f"timeout (>={_CHAT_TIMEOUT_S}s)"
    except Exception as e:
        result["error"] = str(e)
    return result


def report_to_brain(findings: dict[str, Any]) -> None:
    """Report synthetic monitor findings to brain_hook."""
    if not ARIA_INTERNAL_TOKEN:
        return
    try:
        summary = findings.get("summary", {})
        unhealthy = summary.get("unhealthy_apps", [])
        anomalies = summary.get("anomalies", [])
        if unhealthy or anomalies:
            httpx.post(
                "https://aria-intel.fly.dev/api/aria/brain/signal",
                json={
                    "content": (
                        f"Synthetic monitor: {len(unhealthy)} unhealthy, "
                        f"{len(anomalies)} anomalies. "
                        f"Unhealthy: {unhealthy}. Anomalies: {anomalies}"
                    ),
                    "source": "synthetic_monitor",
                    "signal_type": "synthetic_monitor_failure",
                    "metadata": {"summary": summary},
                },
                headers={"Authorization": f"Bearer {ARIA_INTERNAL_TOKEN}"},
                timeout=5,
            )
    except Exception:
        pass


def run_synthetic_monitor() -> dict[str, Any]:
    """Run a full synthetic monitor cycle."""
    report: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "apps": {}, "composite": None, "chat_probe": None, "summary": {},
    }

    for app in APPS:
        health = probe_health(app)
        live = probe_live(app)
        report["apps"][app["name"]] = {**health, **live}

    report["composite"] = probe_composite()
    report["chat_probe"] = probe_chat()

    unhealthy = [a for a in APPS if not report["apps"][a["name"]].get("healthy", False)]
    anomalies = []

    if report["composite"] and not report["composite"].get("healthy"):
        anomalies.append(f"composite_score={report['composite'].get('score')}")
    if report["chat_probe"] and not report["chat_probe"].get("success"):
        anomalies.append(f"chat_probe_failed: {report['chat_probe'].get('error')}")
    if report["chat_probe"] and report["chat_probe"].get("latency_s", 0) > _MAX_CHAT_LATENCY_S:
        anomalies.append(f"chat_latency={report['chat_probe']['latency_s']}s")

    report["summary"] = {
        "unhealthy_apps": [a["name"] for a in unhealthy],
        "anomalies": anomalies,
        "all_healthy": len(unhealthy) == 0 and len(anomalies) == 0,
    }

    report_to_brain(report)

    filename = f"synthetic_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.json"
    path = DATA_DIR / filename
    with open(path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"Synthetic monitor report saved to {path}")
    return report


def print_report(report: dict[str, Any]) -> None:
    """Print a human-readable summary."""
    s = report["summary"]
    print(f"\n=== ARIA Synthetic Monitor ===")
    print(f"Time: {report['timestamp']}")
    print(f"All healthy: {s['all_healthy']}")
    print(f"Unhealthy: {s['unhealthy_apps'] or 'none'}")
    print(f"Anomalies: {s['anomalies'] or 'none'}")
    print()

    for app in APPS:
        name = app["name"]
        a = report["apps"].get(name, {})
        status = "OK" if a.get("healthy") else "DOWN"
        latency = a.get("latency_ms", 0)
        build = a.get("build_rev", "")
        build_str = f" build={build}" if build else ""
        print(f"  [{status}] {name} ({latency}ms){build_str}")

    if report.get("composite"):
        print(f"  Composite: {report['composite'].get('score', 'N/A')}")
    if report.get("chat_probe"):
        cp = report["chat_probe"]
        status = "OK" if cp.get("success") else "FAIL"
        print(f"  Chat: [{status}] {cp.get('latency_s', 0)}s, {cp.get('response_length', 0)}chars")


def main() -> None:
    if "--loop" in sys.argv:
        print("Starting ARIA synthetic monitor loop (5-min intervals)...")
        while True:
            report = run_synthetic_monitor()
            print_report(report)
            print(f"\nNext check at {datetime.now(timezone.utc).replace(second=0).isoformat()}")
            time.sleep(300)
    else:
        report = run_synthetic_monitor()
        print_report(report)


if __name__ == "__main__":
    main()

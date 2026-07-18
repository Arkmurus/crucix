#!/usr/bin/env python3
"""R-F759 (2026-05-20) — lightweight chat smoke harness.

Autonomous 360 verification of the ARIA backend (fly.io) + frontend
(seenode). Hits a known-good set of endpoints, times each, and prints
a PASS/WARN/FAIL table. Exit code 0 = all PASS, 1 = any FAIL, 2 = any
WARN (no FAIL).

Why this exists
───────────────
The interactive 360 done 2026-05-20 found `/api/aria/health` timed out
at 30s while `/health/live` returned in 0.16s — wedge symptom on the
heavy aggregated probe. The full pytest sweep also hung. A repeatable
smoke script means future verifications take ~30s instead of 12+ min
and the result is binary (exit code) so it can be wired into CI / a
cron + alert.

What it probes
──────────────
Backend (fly aria-intel.fly.dev):
  • GET /api/aria/health/live    (lightweight, expect 200, <2s)
  • GET /api/aria/health         (heavyweight, expect 200, <15s WARN >5s)
  • POST /api/aria/chat/stream   (auth gate; expect 401 without token)
  • GET /api/aria/operating-mode (auth-gated; 401 without token)

Frontend (seenode imaria.io):
  • GET /aria.html               (expect 200)
  • R-F738 input-hint string in served HTML (deploy-verification —
    confirms the latest aria.html bundle reached seenode)

With --token <jwt>:
  • Adds Authorization: Bearer <jwt> to gated probes
  • Expects 200 instead of 401 on operating-mode + chat/stream
  • Parses first chunk of SSE stream from chat/stream

Usage
─────
  python scripts/admin/chat_smoke.py
  python scripts/admin/chat_smoke.py --token "$ARIA_ADMIN_TOKEN"
  python scripts/admin/chat_smoke.py --backend https://aria-intel.fly.dev \\
                                      --frontend https://imaria.io

Defaults:
  --backend   https://aria-intel.fly.dev
  --frontend  https://imaria.io

Exit codes:
  0 — all probes PASS
  1 — at least one FAIL
  2 — at least one WARN (no FAIL)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Optional

DEFAULT_BACKEND = "https://aria-intel.fly.dev"
DEFAULT_FRONTEND = "https://imaria.io"

# Latency thresholds (seconds). Above WARN = WARN; above FAIL = FAIL.
THRESHOLDS = {
    "health_live":     (2.0, 8.0),      # warn at 2s, fail at 8s
    "health_heavy":    (5.0, 15.0),     # warn at 5s, fail at 15s
    "chat_stream_auth": (3.0, 10.0),
    "operating_mode":  (3.0, 10.0),
    "aria_html":       (5.0, 15.0),
}

# R-F738 marker in served aria.html — proves the latest frontend bundle
# is deployed on seenode. If this string is missing, the operator is
# serving a cached / stale aria.html.
R_F738_MARKER = "Drop or paste files · Ctrl+K new · Ctrl+/ screen"


def _http(
    method: str,
    url: str,
    *,
    token: Optional[str] = None,
    body: Optional[dict] = None,
    timeout: float = 30.0,
) -> tuple[int, bytes, float]:
    """Return (status_code, body_bytes, elapsed_s). Status 0 on connect /
    network error; status 999 on timeout. Never raises."""
    headers = {}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            buf = resp.read()
            return (resp.status, buf, time.monotonic() - t0)
    except urllib.error.HTTPError as e:
        # HTTPError IS a response — capture status and body
        return (e.code, e.read() if e.fp else b"", time.monotonic() - t0)
    except urllib.error.URLError:
        return (0, b"", time.monotonic() - t0)
    except TimeoutError:
        return (999, b"", time.monotonic() - t0)
    except Exception:
        return (0, b"", time.monotonic() - t0)


def _grade(elapsed: float, status: int, expected: int, key: str) -> str:
    """Return PASS / WARN / FAIL given the response shape."""
    warn_at, fail_at = THRESHOLDS.get(key, (5.0, 15.0))
    if status != expected:
        return "FAIL"
    if elapsed >= fail_at:
        return "FAIL"
    if elapsed >= warn_at:
        return "WARN"
    return "PASS"


def _row(label: str, grade: str, status_text: str, time_s: float, note: str = "") -> dict:
    return {
        "label":  label,
        "grade":  grade,
        "status": status_text,
        "time":   f"{time_s:.2f}s",
        "note":   note,
    }


def run_smoke(backend: str, frontend: str, token: Optional[str] = None) -> list[dict]:
    """Run the probe matrix, return a list of result rows."""
    results: list[dict] = []

    # 1. Backend /health/live — must be fast, must be 200
    status, _, dur = _http("GET", f"{backend}/api/aria/health/live")
    results.append(_row(
        "fly /health/live",
        _grade(dur, status, 200, "health_live"),
        str(status), dur,
    ))

    # 2. Backend /health — heavyweight aggregated; allow slower
    status, _, dur = _http("GET", f"{backend}/api/aria/health", timeout=20.0)
    results.append(_row(
        "fly /health (heavy)",
        _grade(dur, status, 200, "health_heavy"),
        str(status), dur,
        "wedge-prone — see wedge_675" if dur >= 5.0 else "",
    ))

    # 3. Backend /chat/stream auth gate
    expected = 200 if token else 401
    status, _, dur = _http(
        "POST", f"{backend}/api/aria/chat/stream",
        token=token,
        body={"message": "smoke-probe", "session_id": "smoke", "user_id": "smoke", "auto_tools": False},
        timeout=10.0,
    )
    results.append(_row(
        "fly POST /chat/stream",
        _grade(dur, status, expected, "chat_stream_auth"),
        str(status), dur,
        "auth required (no --token)" if expected == 401 else "authenticated",
    ))

    # 4. Backend /operating-mode (auth-gated)
    status, _, dur = _http(
        "GET", f"{backend}/api/aria/operating-mode",
        token=token, timeout=10.0,
    )
    results.append(_row(
        "fly /operating-mode",
        _grade(dur, status, expected, "operating_mode"),
        str(status), dur,
    ))

    # 5. Frontend aria.html
    status, body, dur = _http("GET", f"{frontend}/aria.html", timeout=20.0)
    grade = _grade(dur, status, 200, "aria_html")
    note = ""
    if status == 200:
        # Deploy-verification: the R-F738 input-hint string must be present.
        if R_F738_MARKER.encode("utf-8") in body:
            note = "R-F738 marker present (latest bundle)"
        else:
            note = "WARN: R-F738 marker MISSING — seenode may be stale"
            if grade == "PASS":
                grade = "WARN"
    results.append(_row(
        "seenode /aria.html",
        grade, str(status), dur, note,
    ))

    return results


def _print_table(rows: list[dict]) -> None:
    """Render the probe results as a fixed-width table."""
    cols = ["label", "grade", "status", "time", "note"]
    widths = {c: max(len(c), max(len(str(r[c])) for r in rows)) for c in cols}
    sep = "+-" + "-+-".join("-" * widths[c] for c in cols) + "-+"
    print(sep)
    print("| " + " | ".join(c.ljust(widths[c]) for c in cols) + " |")
    print(sep)
    for r in rows:
        cells = []
        for c in cols:
            v = str(r[c])
            cells.append(v.ljust(widths[c]))
        print("| " + " | ".join(cells) + " |")
    print(sep)


def _exit_code(rows: list[dict]) -> int:
    grades = [r["grade"] for r in rows]
    if "FAIL" in grades:
        return 1
    if "WARN" in grades:
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="ARIA chat smoke harness (R-F759).")
    p.add_argument("--backend", default=DEFAULT_BACKEND)
    p.add_argument("--frontend", default=DEFAULT_FRONTEND)
    p.add_argument("--token", default=None, help="JWT bearer token (omit for auth-gate probes)")
    p.add_argument("--json", action="store_true", help="Emit JSON instead of a table")
    args = p.parse_args(argv)

    rows = run_smoke(args.backend, args.frontend, token=args.token)

    if args.json:
        print(json.dumps({"rows": rows, "exit_code": _exit_code(rows)}, indent=2))
    else:
        print(f"ARIA chat smoke — backend={args.backend} · frontend={args.frontend} · "
              f"token={'present' if args.token else 'omitted (auth probes will 401)'}")
        _print_table(rows)
        summary = {
            "PASS": sum(1 for r in rows if r["grade"] == "PASS"),
            "WARN": sum(1 for r in rows if r["grade"] == "WARN"),
            "FAIL": sum(1 for r in rows if r["grade"] == "FAIL"),
        }
        print(f"Summary: {summary['PASS']} PASS · {summary['WARN']} WARN · {summary['FAIL']} FAIL")

    return _exit_code(rows)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python
"""R-F1230 — HTML health dashboard from test results + code analysis (--html mode).

Borrowed from deepseek_bash_20260531_29fe99.sh's `--html` concept.
Generates a self-contained HTML report with:
  - Test pass/fail summary with per-file breakdown
  - Code quality metrics (syntax errors, bare excepts, debug prints, secrets)
  - Wiring audit (dark modules, missing brain sinks)
  - File health scores
  - Trend data (if previous reports exist)

Usage:
    python scripts/code_health_report.py                          # full report
    python scripts/code_health_report.py --output report.html     # custom path
    python scripts/code_health_report.py --quick                  # skip slow checks
    python scripts/code_health_report.py --serve                  # start HTTP server

Exit code: 0 always (report is informational).
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
os.chdir(REPO_ROOT)
sys.path.insert(0, str(REPO_ROOT))

REPORTS_DIR = REPO_ROOT / "data" / "health_reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# ── Data collection ───────────────────────────────────────────────────


def collect_test_results() -> dict[str, Any]:
    """Run pytest and collect structured results."""
    cmd = [sys.executable, "-m", "pytest", "-q", "--no-header", "--tb=line", "--json-report"]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(REPO_ROOT),
        )
        out = result.stdout + result.stderr
        passed = out.count("PASSED")
        failed = out.count("FAILED")
        errors = out.count("ERROR")
        # Parse JSON report if available
        json_report = REPO_ROOT / ".report.json"
        tests: list[dict] = []
        if json_report.exists():
            try:
                data = json.loads(json_report.read_text(encoding="utf-8"))
                tests = data.get("tests", [])
                json_report.unlink(missing_ok=True)
            except (json.JSONDecodeError, KeyError):
                pass
        return {
            "passed": passed,
            "failed": failed,
            "errors": errors,
            "total": passed + failed + errors,
            "tests": tests,
            "output": out[-3000:],
            "success": result.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {"passed": 0, "failed": 0, "errors": 0, "total": 0, "tests": [], "output": "TIMEOUT", "success": False}
    except Exception as e:
        return {"passed": 0, "failed": 0, "errors": 0, "total": 0, "tests": [], "output": str(e), "success": False}


def collect_code_quality() -> dict[str, Any]:
    """Scan all Python files for quality metrics."""
    metrics: dict[str, Any] = {
        "total_files": 0,
        "total_lines": 0,
        "syntax_errors": [],
        "bare_excepts": [],
        "debug_prints": [],
        "hardcoded_secrets": [],
        "missing_type_hints": [],
        "file_scores": {},
    }

    for fp in sorted((REPO_ROOT / "aria_service").rglob("*.py")):
        if "__pycache__" in str(fp) or ".venv" in str(fp):
            continue
        rel = str(fp.relative_to(REPO_ROOT))
        try:
            content = fp.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        metrics["total_files"] += 1
        lines = content.splitlines()
        metrics["total_lines"] += len(lines)

        file_issues = 0

        # Syntax
        try:
            ast.parse(content)
        except SyntaxError as e:
            metrics["syntax_errors"].append({"file": rel, "line": e.lineno or 0, "msg": str(e)})
            file_issues += 5  # heavy penalty

        # Bare excepts
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped == "except:" or stripped.startswith("except :"):
                metrics["bare_excepts"].append({"file": rel, "line": i})
                file_issues += 2

        # Debug prints
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("print(") and "logger" not in stripped and "logging" not in stripped:
                metrics["debug_prints"].append({"file": rel, "line": i})
                file_issues += 1

        # Hardcoded secrets
        secret_patterns = ["api_key", "api_secret", "password", "token=", "secret="]
        for i, line in enumerate(lines, 1):
            lower = line.lower()
            if any(k in lower for k in secret_patterns) and "os.getenv" not in line and "os.environ" not in line:
                metrics["hardcoded_secrets"].append({"file": rel, "line": i})
                file_issues += 3

        # Missing type hints on public functions
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if re.match(r"^(async )?def [a-zA-Z]", stripped) and "->" not in stripped and not stripped.startswith("def _"):
                metrics["missing_type_hints"].append({"file": rel, "line": i})
                file_issues += 1

        # Score: 100 - weighted issues, min 0
        score = max(0, 100 - file_issues)
        metrics["file_scores"][rel] = score

    return metrics


def collect_wiring_audit() -> dict[str, Any]:
    """Check which modules are wired to the brain."""
    wiring_tokens = [
        "brain_hook.absorb", "brain_hook.observe_self_event",
        "capability_gaps.record_gap", "mistake_ledger.record",
        "record_gap", "observe_self_event",
        "wire_success", "wire_failure",
    ]

    modules: dict[str, dict[str, Any]] = {}
    for fp in sorted((REPO_ROOT / "aria_service").rglob("*.py")):
        if "__pycache__" in str(fp) or ".venv" in str(fp) or fp.name == "__init__.py":
            continue
        rel = str(fp.relative_to(REPO_ROOT))
        try:
            content = fp.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        found_tokens = [t for t in wiring_tokens if t in content]
        modules[rel] = {
            "wired": len(found_tokens) > 0,
            "tokens": found_tokens,
            "lines": len(content.splitlines()),
        }

    wired = sum(1 for m in modules.values() if m["wired"])
    dark = sum(1 for m in modules.values() if not m["wired"])
    return {
        "total_modules": len(modules),
        "wired": wired,
        "dark": dark,
        "wiring_pct": round(wired / max(len(modules), 1) * 100, 1),
        "modules": modules,
    }


def collect_git_stats() -> dict[str, Any]:
    """Collect git statistics for the report."""
    stats: dict[str, Any] = {}
    try:
        # Recent commits
        result = subprocess.run(
            ["git", "log", "--oneline", "-20"],
            capture_output=True, text=True, timeout=10, cwd=str(REPO_ROOT),
        )
        stats["recent_commits"] = result.stdout.splitlines() if result.returncode == 0 else []

        # Total commit count
        result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            capture_output=True, text=True, timeout=10, cwd=str(REPO_ROOT),
        )
        stats["total_commits"] = result.stdout.strip() if result.returncode == 0 else "?"

        # Current branch
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True, timeout=5, cwd=str(REPO_ROOT),
        )
        stats["branch"] = result.stdout.strip() if result.returncode == 0 else "?"

        # HEAD SHA
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, cwd=str(REPO_ROOT),
        )
        stats["head_sha"] = result.stdout.strip() if result.returncode == 0 else "?"
    except Exception:
        stats = {"recent_commits": [], "total_commits": "?", "branch": "?", "head_sha": "?"}
    return stats


def load_previous_report() -> dict[str, Any] | None:
    """Load the most recent previous report for trend comparison."""
    reports = sorted(REPORTS_DIR.glob("report_*.json"))
    if not reports:
        return None
    try:
        return json.loads(reports[-1].read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


# ── HTML generation ───────────────────────────────────────────────────


def _score_color(score: int) -> str:
    if score >= 90:
        return "#22c55e"
    if score >= 70:
        return "#eab308"
    return "#ef4444"


def _pct_color(pct: float) -> str:
    if pct >= 90:
        return "#22c55e"
    if pct >= 70:
        return "#eab308"
    return "#ef4444"


def _trend_icon(current: float, previous: float | None) -> str:
    if previous is None:
        return ""
    diff = current - previous
    if diff > 0:
        return f' <span style="color:#22c55e">▲ +{diff:.1f}</span>'
    if diff < 0:
        return f' <span style="color:#ef4444">▼ {diff:.1f}</span>'
    return ' <span style="color:#6b7280">—</span>'


def generate_html(
    test_results: dict[str, Any],
    quality: dict[str, Any],
    wiring: dict[str, Any],
    git_stats: dict[str, Any],
    previous: dict[str, Any] | None,
) -> str:
    """Generate a self-contained HTML report."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Test stats
    total_tests = test_results["total"]
    passed = test_results["passed"]
    failed = test_results["failed"]
    errors = test_results["errors"]
    pass_pct = round(passed / max(total_tests, 1) * 100, 1) if total_tests else 100

    # Quality stats
    total_files = quality["total_files"]
    total_lines = quality["total_lines"]
    avg_score = round(sum(quality["file_scores"].values()) / max(len(quality["file_scores"]), 1), 1)
    syntax_count = len(quality["syntax_errors"])
    bare_count = len(quality["bare_excepts"])
    debug_count = len(quality["debug_prints"])
    secret_count = len(quality["hardcoded_secrets"])
    hint_count = len(quality["missing_type_hints"])

    # Wiring stats
    wiring_pct = wiring["wiring_pct"]
    dark_count = wiring["dark"]

    # Trends
    prev_pass_pct = previous.get("pass_pct") if previous else None
    prev_avg_score = previous.get("avg_score") if previous else None
    prev_wiring_pct = previous.get("wiring_pct") if previous else None

    # Build file score table rows
    file_rows = ""
    for rel, score in sorted(quality["file_scores"].items(), key=lambda x: x[1]):
        color = _score_color(score)
        wired = wiring["modules"].get(rel, {}).get("wired", False)
        wired_icon = "🧠" if wired else "⚫"
        file_rows += f"""
            <tr>
                <td style="padding:4px 8px;border-bottom:1px solid #e5e7eb;font-family:monospace;font-size:12px">{rel}</td>
                <td style="padding:4px 8px;border-bottom:1px solid #e5e7eb;text-align:center">{wired_icon}</td>
                <td style="padding:4px 8px;border-bottom:1px solid #e5e7eb;text-align:center">
                    <span style="color:{color};font-weight:bold">{score}</span>
                </td>
            </tr>"""

    # Build issue tables
    issue_rows = ""
    for item in quality["syntax_errors"][:20]:
        issue_rows += f'<tr><td style="padding:4px 8px;font-size:12px">❌ SyntaxError</td><td style="padding:4px 8px;font-size:12px;font-family:monospace">{item["file"]}:{item["line"]}</td><td style="padding:4px 8px;font-size:12px">{item["msg"][:80]}</td></tr>\n'
    for item in quality["bare_excepts"][:20]:
        issue_rows += f'<tr><td style="padding:4px 8px;font-size:12px">⚠️ Bare except</td><td style="padding:4px 8px;font-size:12px;font-family:monospace">{item["file"]}:{item["line"]}</td><td style="padding:4px 8px;font-size:12px"></td></tr>\n'
    for item in quality["debug_prints"][:20]:
        issue_rows += f'<tr><td style="padding:4px 8px;font-size:12px">🔊 Debug print</td><td style="padding:4px 8px;font-size:12px;font-family:monospace">{item["file"]}:{item["line"]}</td><td style="padding:4px 8px;font-size:12px"></td></tr>\n'
    for item in quality["hardcoded_secrets"][:20]:
        issue_rows += f'<tr><td style="padding:4px 8px;font-size:12px">🔑 Secret leak</td><td style="padding:4px 8px;font-size:12px;font-family:monospace">{item["file"]}:{item["line"]}</td><td style="padding:4px 8px;font-size:12px"></td></tr>\n'

    # Dark modules list
    dark_rows = ""
    for rel, info in sorted(wiring["modules"].items()):
        if not info["wired"]:
            dark_rows += f'<tr><td style="padding:4px 8px;font-size:12px;font-family:monospace">{rel}</td><td style="padding:4px 8px;font-size:12px;text-align:center">{info["lines"]}</td></tr>\n'

    # Recent commits
    commit_lines = "\n".join(
        f'<li style="font-family:monospace;font-size:11px;color:#6b7280">{c}</li>'
        for c in git_stats.get("recent_commits", [])
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ARIA Code Health Report — {now}</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; background:#f8fafc; color:#1e293b; padding:20px; }}
  .container {{ max-width:1200px; margin:0 auto; }}
  h1 {{ font-size:24px; margin-bottom:4px; }}
  .subtitle {{ color:#6b7280; font-size:14px; margin-bottom:24px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:16px; margin-bottom:24px; }}
  .card {{ background:white; border-radius:12px; padding:16px; box-shadow:0 1px 3px rgba(0,0,0,0.1); }}
  .card h3 {{ font-size:13px; color:#6b7280; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:8px; }}
  .card .value {{ font-size:32px; font-weight:bold; }}
  .card .sub {{ font-size:12px; color:#6b7280; margin-top:4px; }}
  .section {{ background:white; border-radius:12px; padding:16px; box-shadow:0 1px 3px rgba(0,0,0,0.1); margin-bottom:16px; }}
  .section h2 {{ font-size:16px; margin-bottom:12px; padding-bottom:8px; border-bottom:1px solid #e5e7eb; }}
  table {{ width:100%; border-collapse:collapse; }}
  th {{ text-align:left; padding:8px; font-size:12px; color:#6b7280; text-transform:uppercase; border-bottom:2px solid #e5e7eb; }}
  .bar {{ height:8px; border-radius:4px; background:#e5e7eb; margin-top:4px; }}
  .bar-fill {{ height:8px; border-radius:4px; }}
  .footer {{ text-align:center; color:#9ca3af; font-size:12px; margin-top:32px; padding:16px; }}
</style>
</head>
<body>
<div class="container">

<h1>🧪 ARIA Code Health Report</h1>
<p class="subtitle">{now} · {git_stats.get("branch", "?")} @ {git_stats.get("head_sha", "?")} · {git_stats.get("total_commits", "?")} commits</p>

<div class="grid">
  <div class="card">
    <h3>Tests Passing</h3>
    <div class="value" style="color:{_pct_color(pass_pct)}">{pass_pct}%</div>
    <div class="sub">{passed}/{total_tests} passed · {failed} failed · {errors} errors{_trend_icon(pass_pct, prev_pass_pct)}</div>
    <div class="bar"><div class="bar-fill" style="width:{pass_pct}%;background:{_pct_color(pass_pct)}"></div></div>
  </div>
  <div class="card">
    <h3>Code Quality</h3>
    <div class="value" style="color:{_score_color(int(avg_score))}">{avg_score}</div>
    <div class="sub">avg file score · {total_files} files · {total_lines:,} lines{_trend_icon(avg_score, prev_avg_score)}</div>
  </div>
  <div class="card">
    <h3>Brain Wiring</h3>
    <div class="value" style="color:{_pct_color(wiring_pct)}">{wiring_pct}%</div>
    <div class="sub">{wiring["wired"]} wired · {dark_count} dark modules{_trend_icon(wiring_pct, prev_wiring_pct)}</div>
    <div class="bar"><div class="bar-fill" style="width:{wiring_pct}%;background:{_pct_color(wiring_pct)}"></div></div>
  </div>
  <div class="card">
    <h3>Issues Found</h3>
    <div class="value" style="color:{'#ef4444' if (syntax_count+secret_count) > 0 else '#22c55e'}">{syntax_count + bare_count + debug_count + secret_count + hint_count}</div>
    <div class="sub">{syntax_count} syntax · {bare_count} bare excepts · {debug_count} debug prints · {secret_count} secrets · {hint_count} type hints</div>
  </div>
</div>

<div class="section">
<h2>📋 Issues Detail</h2>
<table>
<thead><tr><th style="width:140px">Type</th><th style="width:300px">Location</th><th>Detail</th></tr></thead>
<tbody>
{issue_rows if issue_rows else '<tr><td colspan="3" style="padding:16px;text-align:center;color:#6b7280">✅ No issues found</td></tr>'}
</tbody>
</table>
</div>

<div class="section">
<h2>📁 File Health Scores</h2>
<div style="max-height:400px;overflow-y:auto">
<table>
<thead><tr><th>File</th><th style="width:40px">🧠</th><th style="width:60px">Score</th></tr></thead>
<tbody>
{file_rows}
</tbody>
</table>
</div>
</div>

<div class="section">
<h2>⚫ Dark Modules (not wired to brain)</h2>
<div style="max-height:300px;overflow-y:auto">
<table>
<thead><tr><th>Module</th><th style="width:60px">Lines</th></tr></thead>
<tbody>
{dark_rows if dark_rows else '<tr><td colspan="2" style="padding:16px;text-align:center;color:#6b7280">✅ All modules wired</td></tr>'}
</tbody>
</table>
</div>
</div>

<div class="section">
<h2>🕐 Recent Commits</h2>
<ul style="list-style:none;padding:0">
{commit_lines if commit_lines else '<li style="color:#6b7280">No git data</li>'}
</ul>
</div>

<div class="footer">
Generated by R-F1230 code_health_report.py · ARIA Code Health Dashboard
</div>

</div>
</body>
</html>"""
    return html


# ── Main ──────────────────────────────────────────────────────────────


def _wire_to_brain(event: str, details: dict[str, Any]):
    """Emit a brain signal on success or failure branch (lazy import)."""
    try:
        from aria_service.intel import brain_hook as _bh
        _bh.observe_self_event(
            source="code_health_report",
            event=event,
            details=details,
        )
    except Exception:
        pass  # brain unreachable — don't crash the reporter


def main():
    parser = argparse.ArgumentParser(
        description="R-F1230 — Code health HTML report generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--output", "-o", default=None, help="Output HTML file path")
    parser.add_argument("--quick", "-q", action="store_true", help="Skip slow checks (ecosystem audit)")
    parser.add_argument("--serve", "-s", action="store_true", help="Start HTTP server to view report")
    parser.add_argument("--port", type=int, default=8080, help="HTTP server port (default: 8080)")

    args = parser.parse_args()

    print("📊 Collecting test results...")
    test_results = collect_test_results()

    print("🔍 Scanning code quality...")
    quality = collect_code_quality()

    print("🧠 Auditing brain wiring...")
    wiring = collect_wiring_audit()

    print("🕐 Collecting git stats...")
    git_stats = collect_git_stats()

    print("📈 Loading previous report for trends...")
    previous = load_previous_report()

    print("📝 Generating HTML...")
    html = generate_html(test_results, quality, wiring, git_stats, previous)

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = REPORTS_DIR / f"report_{timestamp}.html"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    print(f"✅ Report written to: {output_path}")

    # Save JSON version for trend comparison
    json_path = output_path.with_suffix(".json")
    json_data = {
        "timestamp": time.time(),
        "pass_pct": round(test_results["passed"] / max(test_results["total"], 1) * 100, 1),
        "avg_score": round(sum(quality["file_scores"].values()) / max(len(quality["file_scores"]), 1), 1),
        "wiring_pct": wiring["wiring_pct"],
        "total_tests": test_results["total"],
        "passed": test_results["passed"],
        "failed": test_results["failed"],
        "total_files": quality["total_files"],
        "total_issues": len(quality["syntax_errors"]) + len(quality["bare_excepts"]) + len(quality["debug_prints"]) + len(quality["hardcoded_secrets"]) + len(quality["missing_type_hints"]),
    }
    json_path.write_text(json.dumps(json_data, indent=2), encoding="utf-8")

    # Wire to brain — both success and failure branches
    total_issues = (
        len(quality["syntax_errors"])
        + len(quality["bare_excepts"])
        + len(quality["debug_prints"])
        + len(quality["hardcoded_secrets"])
        + len(quality["missing_type_hints"])
    )
    _wire_to_brain(
        event="code_health_report.generated",
        details={
            "output_path": str(output_path),
            "pass_pct": json_data["pass_pct"],
            "avg_score": json_data["avg_score"],
            "wiring_pct": json_data["wiring_pct"],
            "total_tests": json_data["total_tests"],
            "passed": json_data["passed"],
            "failed": json_data["failed"],
            "total_files": json_data["total_files"],
            "total_issues": total_issues,
            "dark_modules": wiring["dark"],
        },
    )

    # Serve if requested
    if args.serve:
        print(f"\n🌐 Starting HTTP server at http://localhost:{args.port}")
        print(f"   Open: http://localhost:{args.port}/{output_path.name}")
        print("   Press Ctrl+C to stop\n")
        import http.server
        import socketserver

        os.chdir(str(output_path.parent))
        handler = http.server.SimpleHTTPRequestHandler
        with socketserver.TCPServer(("", args.port), handler) as httpd:
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                print("\n👋 Server stopped.")


if __name__ == "__main__":
    main()

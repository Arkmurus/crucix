"""sprint_metrics — generate sprint summary from git log (R-F85, 2026-05-09).

Single source of truth for R-number counts. Replaces prose-asserted
metrics in sprint reports.

Usage:
    py scripts/sprint_metrics.py                      # last 24h
    py scripts/sprint_metrics.py --since 2026-05-09   # since date
    py scripts/sprint_metrics.py --since 2026-05-09 --until 2026-05-10
    py scripts/sprint_metrics.py --json               # machine-readable

Output:
  - Distinct R-numbers (with sub-numbering preserved: R-F66b is distinct
    from R-F66)
  - Per-R-number commit hash + first-line description
  - Files changed per R-number
  - Total lines added / removed
  - Deploy-target classification (fly / seenode / both / docs)

A letter-suffix on an R-number (R-F66b, R-F48a) is treated as a
distinct R-number — matches the convention adopted in
`docs/recommendations_complete_2026_05_09.md`.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

R_NUMBER_RE = re.compile(r"R-F\d+[a-z]?")
REPO_ROOT = Path(__file__).resolve().parent.parent


def _git(*args: str) -> str:
    """Run a git command and return stdout. Raises on non-zero exit."""
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True, text=True, encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _classify_deploy_target(files: list[str]) -> str:
    """Infer deploy target from the set of files touched."""
    has_fly = any(f.startswith("aria_service/") for f in files)
    has_seenode = any(
        f.startswith(("server.mjs", "lib/", "public/", "apis/"))
        for f in files
    )
    has_docs = any(f.startswith("docs/") for f in files)
    if has_fly and has_seenode:
        return "both"
    if has_fly:
        return "fly"
    if has_seenode:
        return "seenode"
    if has_docs:
        return "docs"
    return "unknown"


def collect_r_numbers(
    since: str | None = None,
    until: str | None = None,
) -> list[dict[str, Any]]:
    """Return a list of R-number records from git log in the window.

    Uses a unique commit marker rather than a body separator — git's
    `--name-only` puts the file list AFTER the body, so any in-format
    separator would mis-split. Commit boundary detected by SHA pattern
    at the start of a line.
    """
    def _iso(d: str | None) -> str | None:
        if not d: return None
        if "T" in d or " " in d: return d
        return f"{d}T00:00:00+00:00"

    git_args = ["log"]
    if since:
        git_args.append(f"--since={_iso(since)}")
    if until:
        git_args.append(f"--until={_iso(until)}")
    # Marker-prefixed format. Lines starting with COMMIT::: are head
    # lines; everything else is body or files until the next COMMIT:::.
    git_args += [
        "--reverse",
        "--pretty=format:COMMIT:::%H%x09%ai%x09%s",
        "--name-only",
        "--no-merges",
    ]
    raw = _git(*git_args)

    # Walk line by line. State machine: when we see COMMIT:::<sha>...,
    # close the current record and open a new one.
    records: list[dict[str, Any]] = []
    current_head: dict[str, str] | None = None
    current_text: list[str] = []
    current_files: list[str] = []

    def _flush() -> None:
        if not current_head:
            return
        full_text = current_head["subject"] + " " + " ".join(current_text)
        r_nums = sorted(set(R_NUMBER_RE.findall(full_text)))
        for rn in r_nums:
            records.append({
                "r_number": rn,
                "sha":      current_head["sha"][:8],
                "date":     current_head["date"].split(" ")[0],
                "subject":  current_head["subject"],
                "files":    list(current_files),
                "deploy":   _classify_deploy_target(current_files),
            })

    for ln in raw.split("\n"):
        if ln.startswith("COMMIT:::"):
            _flush()
            current_text = []
            current_files = []
            head_payload = ln[len("COMMIT:::"):]
            parts = head_payload.split("\t")
            if len(parts) >= 3:
                current_head = {
                    "sha": parts[0],
                    "date": parts[1],
                    "subject": parts[2],
                }
            else:
                current_head = None
            continue
        ln_stripped = ln.strip()
        if not ln_stripped:
            continue
        # File-path heuristic: contains slash AND no leading bullet/quote
        if (("/" in ln_stripped or "\\" in ln_stripped)
                and not ln_stripped.startswith(("- ", "* ", "> ", "  "))
                and " " not in ln_stripped.split("/")[0]):
            current_files.append(ln_stripped)
        else:
            current_text.append(ln_stripped)
    _flush()

    return records


def aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the sprint summary dict from raw records."""
    by_rnum: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        by_rnum[r["r_number"]].append(r)

    distinct = sorted(
        by_rnum.keys(),
        key=lambda x: (
            int(re.match(r"R-F(\d+)", x).group(1)),
            x[5:],  # the suffix letter, if any
        ),
    )

    by_deploy = defaultdict(int)
    for r_nums_records in by_rnum.values():
        for r in r_nums_records:
            by_deploy[r["deploy"]] += 1

    return {
        "distinct_r_numbers": distinct,
        "count":              len(distinct),
        "first_seen": {
            rn: by_rnum[rn][0]["sha"]
            for rn in distinct
        },
        "subjects": {
            rn: by_rnum[rn][0]["subject"]
            for rn in distinct
        },
        "deploy_targets": {
            rn: by_rnum[rn][0]["deploy"]
            for rn in distinct
        },
        "by_deploy_count": dict(by_deploy),
    }


def render_table(summary: dict[str, Any]) -> str:
    """Render a markdown table — same format as the recommendations doc."""
    lines = [
        f"# Sprint metrics — {summary['count']} distinct R-numbers",
        "",
        "| R-F# | Subject | Commit | Deploy |",
        "|---|---|---|---|",
    ]
    for rn in summary["distinct_r_numbers"]:
        subj = summary["subjects"][rn].replace("|", "\\|")[:80]
        lines.append(
            f"| {rn} | {subj} | `{summary['first_seen'][rn]}` | "
            f"{summary['deploy_targets'][rn]} |"
        )
    lines.append("")
    lines.append("## Deploy-target distribution")
    for tgt, n in sorted(summary["by_deploy_count"].items()):
        lines.append(f"- **{tgt}**: {n}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="sprint_metrics — generate R-number summary from git log")
    ap.add_argument("--since", help="ISO date or relative (e.g. '2026-05-09', '1.day')")
    ap.add_argument("--until", help="ISO date or relative")
    ap.add_argument("--json",  action="store_true", help="emit JSON instead of markdown table")
    args = ap.parse_args()

    since = args.since
    if not since:
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%d")

    records = collect_r_numbers(since=since, until=args.until)
    summary = aggregate(records)

    if args.json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        print(render_table(summary))


if __name__ == "__main__":
    main()

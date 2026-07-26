#!/usr/bin/env python3
"""CLI for R-F540 reservation log.

Usage:
    python scripts/admin/reserve_r_number.py reserve "fix sanctions regex"
    python scripts/admin/reserve_r_number.py reserve "fix sanctions regex" --agent claude-A
    python scripts/admin/reserve_r_number.py peek
    python scripts/admin/reserve_r_number.py ship R-F555 abc1234
    python scripts/admin/reserve_r_number.py list --status in_progress
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running from repo root or scripts/
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aria_service.intel import r_number_registry as reg


def main() -> int:
    p = argparse.ArgumentParser(description="R-number reservation log (R-F540)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s_res = sub.add_parser("reserve", help="claim next R-number")
    s_res.add_argument("title", help="short title for the work")
    s_res.add_argument("--agent", default="claude", help="claiming agent/session id")
    s_res.add_argument("--notes", default="", help="optional notes")

    sub.add_parser("peek", help="show next R-number without claiming")

    s_ship = sub.add_parser("ship", help="mark R-number shipped")
    s_ship.add_argument("r_number")
    s_ship.add_argument("commit_sha")

    s_abn = sub.add_parser("abandon", help="mark R-number abandoned")
    s_abn.add_argument("r_number")
    s_abn.add_argument("reason")

    s_list = sub.add_parser("list", help="list reservations")
    s_list.add_argument("--status", default=None, choices=["in_progress", "shipped", "abandoned"])

    # R-F3095 — §2 says "mark shipped at push"; nothing enforced it, and 372
    # in_progress entries with no SHA accumulated. Git cannot forget, so reconcile
    # against it. Dry-run by default.
    s_rec = sub.add_parser("reconcile", help="ship-mark R-numbers already present in git history")
    s_rec.add_argument("--ref", default="HEAD", help="only count commits reachable from this ref")
    s_rec.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")

    args = p.parse_args()

    if args.cmd == "reserve":
        r = reg.reserve(args.title, agent=args.agent, notes=args.notes)
        print(r)
    elif args.cmd == "peek":
        print(reg.peek_next())
    elif args.cmd == "ship":
        reg.mark_shipped(args.r_number, args.commit_sha)
        print(f"shipped: {args.r_number} @ {args.commit_sha}")
    elif args.cmd == "abandon":
        reg.mark_abandoned(args.r_number, args.reason)
        print(f"abandoned: {args.r_number}")
    elif args.cmd == "list":
        rs = reg.list_reservations(status_filter=args.status)
        print(json.dumps(rs, indent=2))
    elif args.cmd == "reconcile":
        res = reg.reconcile_with_git(args.ref, apply=args.apply)
        for e in res["entries"]:
            print(f"  {e['r_number']} -> {e['commit_sha']}  {e['title'][:60]}")
        verb = "ship-marked" if args.apply else "would ship-mark (dry run; pass --apply)"
        print(f"{res['drifted']} of {res['checked']} reservation(s) {verb}"
              + (f"; {res['applied']} written" if args.apply else ""))
        if res["review"]:
            print(f"\n{len(res['review'])} mentioned ONLY in a commit body — NOT applied.")
            print("A body mention is a reference, not a ship record. Judge each by hand:")
            for e in res["review"]:
                print(f"  {e['r_number']} ~ {e['commit_sha']}  {e['title'][:56]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

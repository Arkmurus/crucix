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
    # R-F4196 — Windows PowerShell may expose a cp1252 stdout even though the
    # UTF-8 ledger legitimately contains arrows and em dashes. Reporting must
    # not crash halfway through an audit because one title is unrepresentable.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
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

    s_stale = sub.add_parser(
        "stale",
        help="report old in-progress claims for evidence-based review",
    )
    s_stale.add_argument("--days", type=int, default=14,
                         help="age threshold in whole days (default: 14)")
    s_stale.add_argument("--limit", type=int, default=25,
                         help="maximum entries to print (default: 25)")

    # R-F3095 — §2 says "mark shipped at push"; nothing enforced it, and 372
    # in_progress entries with no SHA accumulated. Git cannot forget, so reconcile
    # against it. Dry-run by default.
    # R-F4077 (C-127) — surface claims that exist only in this tree. Those are
    # the ones a concurrent ledger merge can lose, which is how R-F4061/R-F4062
    # were issued twice on 2026-08-16.
    s_unp = sub.add_parser(
        "unpublished",
        help="list R-numbers reserved locally but absent from the published ledger",
    )
    s_unp.add_argument("--ref", default="origin/main",
                       help="published ref to compare against")

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
    elif args.cmd == "stale":
        if args.limit < 1:
            p.error("--limit must be positive")
        stale = reg.stale_reservations(args.days)
        if not stale:
            print(f"OK — no in-progress reservation is older than {args.days} day(s).")
            return 0
        print(
            f"STALE — {len(stale)} in-progress reservation(s) are older than "
            f"{args.days} day(s). Review against commits, tests, and live probes; "
            "age alone never closes work."
        )
        for entry in stale[:args.limit]:
            age = "UNKNOWN" if entry["age_days"] is None else str(entry["age_days"])
            print(f"  {entry['r_number']}  age_days={age}  {entry['title']}")
        if len(stale) > args.limit:
            print(f"  ... {len(stale) - args.limit} more (raise --limit to inspect)")
        return 1
    elif args.cmd == "unpublished":
        res = reg.unpublished_claims(ref=args.ref)
        if not res["readable"]:
            print(f"UNKNOWN — could not read the published ledger at {args.ref}. "
                  "That is not the same as 'nothing unpublished'.")
            return 2
        if res.get("displaced"):
            print(f"{len(res['displaced'])} DISPLACED claim(s) — the published "
                  f"ledger carries a DIFFERENT claim under these numbers. Your "
                  f"work references numbers it does not own; renumber before "
                  f"shipping:")
            for n in res["displaced"]:
                print(f"  {n}")
            return 1
        if not res["unpublished"]:
            print(f"OK — all {res['local_total']} reservations are published.")
            return 0
        print(f"{len(res['unpublished'])} UNPUBLISHED claim(s) — a concurrent "
              f"ledger merge can lose these. Commit+push the ledger before "
              f"building on them:")
        for n in res["unpublished"]:
            print(f"  {n}")
        return 1
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

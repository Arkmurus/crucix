#!/usr/bin/env python3
"""CLI for the Cure defect-register C-number log (R-F3878).

Mirrors `reserve_r_number.py`, for the same reason §2 exists: a number claimed by
writing it into a document is not claimed. The live register collided FOUR times
(C-18, C-19, C-22, C-23) before this existed.

Usage:
    python scripts/admin/reserve_c_number.py reserve "search health blind to blocks"
    python scripts/admin/reserve_c_number.py peek
    python scripts/admin/reserve_c_number.py close C-26 R-F3873 R-F3874
    python scripts/admin/reserve_c_number.py list --status open
    python scripts/admin/reserve_c_number.py audit
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running from repo root or scripts/
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aria_service.intel import c_number_registry as reg


def _utf8_stdout() -> None:
    """Windows consoles default to cp1252, and this tool prints the register's own
    text back at you.

    THIS IS NOT COSMETIC. The first NEW collision this gate ever caught crashed the
    audit with `UnicodeEncodeError: '\\u2190'` — the arrow in the "<- NEW" marker —
    so the ONE code path that matters, the one that reports a collision, was the one
    that died. The clean path printed fine, which is why it looked healthy: the
    failure branch had never been exercised (R-F3858). Defect titles are arbitrary
    prose and will contain characters cp1252 cannot encode, so the marker is now
    ASCII *and* the stream is reconfigured.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main() -> int:
    _utf8_stdout()
    p = argparse.ArgumentParser(description="C-number reservation log (R-F3878)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s_res = sub.add_parser("reserve", help="claim next C-number")
    s_res.add_argument("title", help="short title for the defect")
    s_res.add_argument("--agent", default="claude", help="claiming agent/session id")
    s_res.add_argument("--notes", default="", help="optional notes")

    sub.add_parser("peek", help="show next C-number without claiming")

    s_close = sub.add_parser("close", help="close a C-number against its R-numbers")
    s_close.add_argument("c_number")
    s_close.add_argument("r_numbers", nargs="+", help="R-numbers that fixed it")

    s_list = sub.add_parser("list", help="list reservations")
    s_list.add_argument("--status", default=None, choices=["open", "closed"])

    sub.add_parser("audit", help="report collisions and register drift")
    sub.add_parser("backfill", help="import existing defects.md headings into the ledger (once)")

    args = p.parse_args()

    if args.cmd == "reserve":
        print(reg.reserve(args.title, agent=args.agent, notes=args.notes))
    elif args.cmd == "peek":
        print(reg.peek_next())
    elif args.cmd == "close":
        reg.mark_closed(args.c_number, args.r_numbers)
        print(f"closed: {args.c_number} <- {', '.join(args.r_numbers)}")
    elif args.cmd == "list":
        print(json.dumps(reg.list_reservations(status_filter=args.status), indent=2))
    elif args.cmd == "backfill":
        res = reg.backfill_from_register()
        print(f"backfilled {res['count']}: {', '.join(res['added']) or '(nothing to do)'}")
        print("These are imported headings, NOT reservations — they are stamped\n"
              "claimed_by=backfill:register with no timestamp, because nobody\n"
              "reserved them and inventing one would put fiction in the log.")
    elif args.cmd == "audit":
        rep = reg.audit()
        if not rep["register_readable"]:
            print("REGISTER UNREADABLE — cannot audit. Nothing below is trustworthy.")
            return 2
        print(f"claims: {rep['claims']}   next available: {rep['next_available']}")
        if rep["collisions"]:
            print("\nCOLLISIONS (one number, unrelated work):")
            for c, titles in rep["collisions"].items():
                legacy = " [baselined]" if c in rep["legacy_collisions"] else "  <-- NEW"
                print(f"  {c}{legacy}")
                for t in titles:
                    print(f"      · {t[:88]}")
        if rep["unreserved"]:
            print(f"\nIn the register but never reserved ({len(rep['unreserved'])}) —"
                  f" claimed by writing a heading, which is how every collision so far"
                  f" happened:\n  {', '.join(rep['unreserved'])}")
        if rep["unwritten"]:
            print(f"\nReserved but not yet written up (normal while work is in "
                  f"flight):\n  {', '.join(rep['unwritten'])}")
        # Only a NEW collision is a failure; the baselined four are recorded debt.
        if rep["new_collisions"]:
            print("\nFAIL: new collision(s) beyond the baseline: "
                  + ", ".join(rep["new_collisions"]))
            return 1
        print("\nOK — no new collisions.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

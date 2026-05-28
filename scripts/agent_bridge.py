"""R-F990 — Claude side of the Claude<->ARIA back-door mailbox.

Run from the crucix repo (or any subdir). Lets Claude Code (or the operator)
see ARIA's questions and reply. ARIA's side is the ask_claude / check_claude
tools in the aria CLI; both talk to the same `<repo>/.agent_bridge/` mailbox.

Usage:
  python scripts/agent_bridge.py inbox            # new questions/notes from ARIA (marks read)
  python scripts/agent_bridge.py peek             # same, but don't mark read
  python scripts/agent_bridge.py reply <id> "..." # answer a specific question
  python scripts/agent_bridge.py send "..."       # send ARIA an unprompted note
  python scripts/agent_bridge.py log [N]          # last N messages, both directions
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from aria_cli import bridge
    from aria_cli.cli import find_repo_root
except ModuleNotFoundError:  # allow running without an editable install
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from aria_cli import bridge
    from aria_cli.cli import find_repo_root


def _base() -> Path:
    return find_repo_root(Path.cwd()) or Path.cwd()


def _fmt(m: dict) -> str:
    tag = m.get("kind", "note")
    rt = f" reply_to={m['reply_to']}" if m.get("reply_to") else ""
    return f"[{m.get('iso','')}] {m.get('frm','?')}->{m.get('to','?')} {tag} id={m.get('id','')}{rt}\n    {m.get('text','')}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="agent_bridge", description="Claude<->ARIA mailbox")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("inbox", help="new messages from ARIA (marks read)")
    sub.add_parser("peek", help="new messages from ARIA (does not mark read)")
    r = sub.add_parser("reply", help="answer a question by id")
    r.add_argument("id")
    r.add_argument("text")
    s = sub.add_parser("send", help="send ARIA a note")
    s.add_argument("text")
    lg = sub.add_parser("log", help="recent messages both ways")
    lg.add_argument("n", nargs="?", type=int, default=20)
    args = p.parse_args(argv)

    base = _base()

    if args.cmd in ("inbox", "peek"):
        msgs = bridge.read_new(base, "claude") if args.cmd == "inbox" else bridge.peek(base, "claude")
        if not msgs:
            print("(no new messages from ARIA)")
        else:
            for m in msgs:
                print(_fmt(m))
                print()
        return 0

    if args.cmd == "reply":
        msg = bridge.send(base, frm="claude", to="aria", text=args.text,
                          kind="answer", reply_to=args.id)
        print(f"replied (id {msg['id']}, reply_to {args.id})")
        return 0

    if args.cmd == "send":
        msg = bridge.send(base, frm="claude", to="aria", text=args.text, kind="note")
        print(f"sent note to ARIA (id {msg['id']})")
        return 0

    if args.cmd == "log":
        allm = bridge._all(base)[-args.n:]
        for m in allm:
            print(_fmt(m))
            print()
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())

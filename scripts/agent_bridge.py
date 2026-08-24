"""R-F990 — Claude side of the Claude<->ARIA back-door mailbox.

Run from the crucix repo (or any subdir). Lets Claude Code (or the operator)
see ARIA's questions and reply. ARIA's side is the ask_claude / check_claude
tools in the aria CLI; both talk to the same `<repo>/.agent_bridge/` mailbox.

R-F1373: `--as {claude,aria}` sets WHO is speaking (default claude). This
exists because on 2026-06-06 ARIA used this script directly (instead of her
ask_claude tool) to answer Claude — every message was hardcoded
frm="claude", so her replies were tagged claude->aria and Claude's
inbox/watcher (which reads to="claude") never surfaced them; the operator
had to relay "aria left you a note" by hand. If you are ARIA, either use
your ask_claude tool or pass `--as aria` here.

Usage:
  python scripts/agent_bridge.py inbox            # new questions/notes from ARIA (marks read)
  python scripts/agent_bridge.py peek             # same, but don't mark read
  python scripts/agent_bridge.py reply <id> "..." # answer a specific question
  python scripts/agent_bridge.py send "..."       # send ARIA an unprompted note
  python scripts/agent_bridge.py log [N]          # last N messages, both directions
  python scripts/agent_bridge.py --as aria send "..."   # ARIA -> Claude
  python scripts/agent_bridge.py --as aria inbox        # ARIA reads Claude's notes
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


# ── R-F4302 (C-255) / R-F4307 (C-260) — forwarding lives in ONE place ──────
#
# This file used to carry its own POST to /api/aria/collab/ingest. That was a
# DUPLICATE: `aria_cli.bridge.send()` has forwarded since R-F2400, and send() is
# exactly what the commands below call. Both would have fired the moment
# ARIA_SERVICE_URL was exported instead of kept in `.env`, POSTing every note
# twice and doubling the teacher corpus — C-254's amplification in miniature,
# reintroduced by the fix for C-255.
#
# What R-F4302 got right and is KEPT: the outcome must be VISIBLE. A silent
# best-effort forward is how a corpus reaches 24 substantial notes with nobody
# noticing. send() now forwards once and hands back the (ok, reason); this file
# only prints it. The .env fallback moved to the canonical forwarder too — that
# omission, not a missing implementation, is why nothing ever forwarded.


def _report_forward(msg: dict) -> None:
    """Print the outcome of the forward `bridge.send` already performed.

    Deliberately does NOT forward. Calling the forwarder here would re-post the
    note and reintroduce the duplication this exists to remove.
    """
    fwd = (msg or {}).get("_forward") or {}
    ok, reason = bool(fwd.get("ok")), str(fwd.get("reason") or "no forward attempted")
    if ok:
        print(f"  forwarded to ARIA's brain ({reason})")
    elif "not teacher signal" in reason:
        pass                    # ARIA->Claude: nothing to say
    else:
        print(f"  NOT forwarded - {reason}")


def _base() -> Path:
    return find_repo_root(Path.cwd()) or Path.cwd()


def _fmt(m: dict) -> str:
    tag = m.get("kind", "note")
    rt = f" reply_to={m['reply_to']}" if m.get("reply_to") else ""
    return f"[{m.get('iso','')}] {m.get('frm','?')}->{m.get('to','?')} {tag} id={m.get('id','')}{rt}\n    {m.get('text','')}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="agent_bridge", description="Claude<->ARIA mailbox")
    # R-F1373 — identity of the SPEAKER. Hardcoded "claude" made ARIA's
    # script-sent replies invisible to Claude's inbox (see module docstring).
    p.add_argument(
        "--as", dest="ident", choices=["claude", "aria"], default="claude",
        help="who is speaking (default: claude). ARIA must pass --as aria.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("inbox", help="new messages addressed to you (marks read)")
    sub.add_parser("peek", help="new messages addressed to you (does not mark read)")
    r = sub.add_parser("reply", help="answer a question by id")
    r.add_argument("id")
    r.add_argument("text")
    s = sub.add_parser("send", help="send the other agent a note")
    s.add_argument("text")
    lg = sub.add_parser("log", help="recent messages both ways")
    lg.add_argument("n", nargs="?", type=int, default=20)
    args = p.parse_args(argv)

    base = _base()
    me = args.ident
    other = "aria" if me == "claude" else "claude"

    if args.cmd in ("inbox", "peek"):
        msgs = bridge.read_new(base, me) if args.cmd == "inbox" else bridge.peek(base, me)
        if not msgs:
            print(f"(no new messages from {other.upper()})")
        else:
            for m in msgs:
                print(_fmt(m))
                print()
        return 0

    if args.cmd == "reply":
        msg = bridge.send(base, frm=me, to=other, text=args.text,
                          kind="answer", reply_to=args.id)
        print(f"replied (id {msg['id']}, reply_to {args.id})")
        _report_forward(msg)          # R-F4302 — local write first, then forward
        return 0

    if args.cmd == "send":
        msg = bridge.send(base, frm=me, to=other, text=args.text, kind="note")
        print(f"sent note to {other.upper()} (id {msg['id']})")
        _report_forward(msg)          # R-F4302 — local write first, then forward
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

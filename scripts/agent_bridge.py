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
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

try:
    from aria_cli import bridge
    from aria_cli.cli import find_repo_root
except ModuleNotFoundError:  # allow running without an editable install
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from aria_cli import bridge
    from aria_cli.cli import find_repo_root


# ── R-F4302 (C-255) — forward Claude's side to the SERVER brain ────────────
#
# `POST /api/aria/collab/ingest` was built to close this loop and its docstring
# claims "The local scripts/agent_bridge.py now best-effort POSTs Claude's
# messages here." That was never true: `git log -S "collab/ingest"` on this file
# is EMPTY. The mailbox is a LOCAL file, aria-intel never saw it, and the teacher
# corpus accumulated 24 substantial notes in its entire lifetime as a result.
#
# The local write stays the source of truth. This is strictly additive: it runs
# AFTER the note is safely on disk, and a failure here can never lose a message.

_INGEST_PATH = "/api/aria/collab/ingest"
_TIMEOUT_S = 10.0


def _env_file_values() -> dict:
    """Values from the repo `.env`, if present. Never raises.

    The operator keeps credentials in `.env` (gitignored) rather than the shell,
    so reading it is what makes the forward work in practice. os.environ still
    wins — an explicit export must override a stale file.
    """
    out: dict = {}
    try:
        f = _base() / ".env"
        if not f.exists():
            return out
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        return {}
    return out


def _cfg(name: str) -> str:
    return (os.environ.get(name) or _env_file_values().get(name) or "").strip()


def _http_post(url: str, payload: str, headers: dict, timeout: float):
    """One POST. Returns (status, body). Separated so tests drive it directly."""
    req = urllib.request.Request(url, data=payload.encode("utf-8"),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(2048).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(2048).decode("utf-8", "replace")


def forward_to_server(msg: dict) -> tuple[bool, str]:
    """Best-effort POST of one Claude->ARIA note to the server collab log.

    Returns (ok, reason). NEVER raises and NEVER touches the local mailbox.

    Three properties are load-bearing:

    * UNCONFIGURED IS REPORTED, NOT SILENT. A quiet best-effort forward is
      exactly how a corpus reaches 24 notes with nobody noticing, so every
      non-success path returns a reason the CLI prints.
    * NEVER POST UNAUTHENTICATED. The ingest route is a brain WRITE; without a
      token we refuse rather than send and be rejected.
    * TEACHER SIGNAL ONLY. Only claude->aria is forwarded. Pushing ARIA's own
      notes into the corpus would feed her output back as her teacher.
    """
    if (msg.get("frm") or "").lower() != "claude":
        return False, "not teacher signal (only claude->aria is forwarded)"

    base = _cfg("ARIA_SERVICE_URL") or _cfg("ARIA_BRAIN_URL")
    if not base:
        return False, "ARIA_SERVICE_URL not set - note is LOCAL ONLY"
    token = _cfg("ARIA_INTERNAL_TOKEN")
    if not token:
        return False, "ARIA_INTERNAL_TOKEN not set - refusing to POST unauthenticated"

    url = base.rstrip("/") + _INGEST_PATH
    payload = json.dumps({
        "text": msg.get("text") or "",
        "kind": msg.get("kind") or "note",
        "reply_to": msg.get("reply_to") or "",
        "frm": "claude",
        "to": "aria",
    })
    headers = {"Content-Type": "application/json",
               "Authorization": "Bearer " + token}
    try:
        status, body = _http_post(url, payload, headers, _TIMEOUT_S)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    if 200 <= int(status) < 300:
        try:
            sid = (json.loads(body) or {}).get("id") or ""
        except Exception:
            sid = ""
        return True, f"server id {sid}" if sid else "ok"
    return False, f"HTTP {status}: {str(body)[:120]}"


def _report_forward(msg: dict) -> None:
    """Print what happened to the forward. Never raises."""
    try:
        ok, reason = forward_to_server(msg)
    except Exception as e:                      # belt and braces
        ok, reason = False, f"{type(e).__name__}: {e}"
    if ok:
        print(f"  forwarded to ARIA's brain ({reason})")
    elif "not teacher signal" in reason:
        pass                                    # ARIA->Claude: nothing to say
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

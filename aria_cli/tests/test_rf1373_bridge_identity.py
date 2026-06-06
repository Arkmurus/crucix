"""R-F1373 — agent_bridge.py --as identity flag.

The live failure (2026-06-06): ARIA answered Claude's questions by invoking
scripts/agent_bridge.py directly. The script hardcoded frm="claude", so her
replies were logged claude->aria — and Claude's inbox/watcher, which reads
messages addressed to "claude", NEVER surfaced them. The operator had to
relay "aria left you a note" by hand.

Capability: a message ARIA sends through the script (--as aria) MUST appear
in Claude's inbox. Pre-fix this fails (no --as flag; message tagged
claude->aria; claude inbox empty).
"""
from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from aria_cli import bridge  # noqa: E402
import agent_bridge  # noqa: E402


@pytest.fixture()
def mailbox(tmp_path, monkeypatch):
    """Isolated mailbox: point the CLI's repo-root resolution at tmp_path."""
    monkeypatch.setattr(agent_bridge, "_base", lambda: tmp_path)
    return tmp_path


def _run(argv) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = agent_bridge.main(argv)
    assert rc == 0
    return buf.getvalue()


def test_rf1373_capability_aria_send_reaches_claude_inbox(mailbox):
    """THE capability: ARIA speaks via the script and Claude's inbox sees it."""
    _run(["--as", "aria", "send", "deploy done, build_rev abc123 — please verify"])
    out = _run(["inbox"])  # default identity: claude
    assert "aria->claude" in out
    assert "build_rev abc123" in out


def test_rf1373_aria_reply_is_tagged_from_aria(mailbox):
    """A reply sent --as aria must be frm=aria (the live bug was frm=claude)."""
    _run(["send", "ARIA, which path deployed 051bcc3c?"])  # claude -> aria
    aria_in = _run(["--as", "aria", "inbox"])
    assert "claude->aria" in aria_in
    msg_id = bridge._all(mailbox)[-1]["id"]
    _run(["--as", "aria", "reply", msg_id, "deploy.ps1, not ci_deploy"])
    last = bridge._all(mailbox)[-1]
    assert last["frm"] == "aria" and last["to"] == "claude"
    assert last["reply_to"] == msg_id
    # And Claude actually receives it.
    claude_in = _run(["inbox"])
    assert "deploy.ps1, not ci_deploy" in claude_in


def test_rf1373_default_identity_unchanged(mailbox):
    """Regression: bare `send` still speaks as claude (existing behavior)."""
    out = _run(["send", "note for aria"])
    assert "sent note to ARIA" in out
    last = bridge._all(mailbox)[-1]
    assert last["frm"] == "claude" and last["to"] == "aria"

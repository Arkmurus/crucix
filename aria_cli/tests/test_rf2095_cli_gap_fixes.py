"""R-F2095 — root fixes for the remaining ARIA CLI gaps:
  GAP1 read_file now PAGES by lines (no silent 100 KB truncation) and tells the
       agent the window + how to read the rest.
  GAP2 a mistyped /command is reported, not silently run as an agent task.
  GAP4 git paths/messages are shell-quoted (spaces, quotes, metacharacters safe).
"""
import tempfile
from pathlib import Path

from aria_cli.tools import Toolbox
from aria_cli.safety import WriteGuard
from aria_cli.cli import _is_mistyped_command
from aria_cli.coder_tools import _shq


def _tb():
    tmp = tempfile.mkdtemp()
    return Toolbox(root=Path(tmp), guard=WriteGuard(self_mode=False)), Path(tmp)


# ── GAP1: paged read_file ────────────────────────────────────────────────────
def test_rf2095_read_file_pages_past_old_100kb_cap():
    tb, root = _tb()
    # 6000 lines of ~30 bytes each ≈ 180 KB — well past the old 100 KB raw cap.
    big = "\n".join(f"line-{i:05d}-payload-xxxxxxxxxx" for i in range(6000))
    (root / "big.txt").write_text(big, encoding="utf-8")
    # read a window that starts at line 5000 — UNREACHABLE under the old byte cap.
    r = tb.read_file("big.txt", offset=5000, limit=10)
    assert not r.is_error
    assert "line-05000" in r.output, "must reach lines beyond the old 100 KB cap"
    assert "5001\t" in r.output  # 1-based line numbers preserved


def test_rf2095_read_file_marks_truncation_not_silent():
    tb, root = _tb()
    (root / "f.txt").write_text("\n".join(str(i) for i in range(5000)), encoding="utf-8")
    r = tb.read_file("f.txt")  # default window < 5000 lines
    assert "of 5000" in r.output and "offset=" in r.output, "must tell the agent it's partial + how to continue"


def test_rf2095_read_file_small_file_no_marker_noise():
    tb, root = _tb()
    (root / "s.txt").write_text("a\nb\nc", encoding="utf-8")
    r = tb.read_file("s.txt")
    assert "read_file:" not in r.output, "a whole small file must not carry a truncation marker"
    assert "1\ta" in r.output


def test_rf2095_read_file_refuses_oversize_with_guidance():
    tb, root = _tb()
    huge = root / "huge.bin"
    huge.write_bytes(b"x" * (10_000_001))  # just over the 10 MB hard ceiling
    r = tb.read_file("huge.bin")
    assert r.is_error and "too large" in r.output and "offset" in r.output


# ── GAP2: mistyped command detection ─────────────────────────────────────────
def test_rf2095_mistyped_command_detection():
    assert _is_mistyped_command("/help") is True
    assert _is_mistyped_command("/fix-bug") is True
    assert _is_mistyped_command("/reset") is True
    # NOT commands — must still run as tasks:
    assert _is_mistyped_command("/etc/hosts") is False      # a path
    assert _is_mistyped_command("hello there") is False     # a sentence
    assert _is_mistyped_command("") is False
    assert _is_mistyped_command("/123") is False            # must start with a letter


# ── GAP4: git shell quoting ──────────────────────────────────────────────────
def test_rf2095_shq_quotes_spaces_and_metacharacters():
    # spaces, quotes, and shell metacharacters must be neutralised (no splitting/injection)
    for nasty in ["file (1).py", "a b c.txt", 'msg "q" ; rm -rf /', "x && y", "$(evil)"]:
        q = _shq(nasty)
        assert q != nasty or nasty == ""   # something was quoted/escaped
        # the dangerous bareword must not appear UNquoted at the start
        assert q[0] in ("'", '"') or "\\" in q

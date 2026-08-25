"""R-F2128 — coder_tools must quote shell args in ci_deploy/reserve/ship.

R-F2095 fixed git_commit/git_push/git add (they use _shq), but ci_deploy's
commit (`git commit -m "{msg}"`), reserve_r_number (`reserve "{title}"`) and
ship_r_number (`ship {r_number} {sha}`) still interpolated raw values into the
shell — a backtick/$()/quote in an LLM-generated summary or title would execute.
These tests drive the real methods with an injection payload and assert the
value reaches the command in its _shq-quoted form (so it can't break out).
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from aria_cli.coder_tools import CoderToolbox, _shq  # noqa: E402

EVIL = "x'; $(rm -rf /) `id` \"q\""


class _Result:
    def __init__(self, output="", is_error=False):
        self.output = output
        self.is_error = is_error


class _FakeTB:
    def __init__(self, root):
        self.root = root
        self.commands = []

    def run(self, cmd, timeout=None):
        self.commands.append(cmd)
        if cmd.startswith("git rev-parse"):
            return _Result("abc1234\n")
        if cmd.startswith("git status"):
            return _Result(" M somefile.py\n")  # pretend there are changes
        if cmd.startswith("git log"):
            return _Result("some subject\n")
        return _Result("")


def _mk(tmp_path):
    admin = tmp_path / "scripts" / "admin"
    admin.mkdir(parents=True)
    (admin / "reserve_r_number.py").write_text("# stub", encoding="utf-8")
    return CoderToolbox(_FakeTB(tmp_path))


def test_rf2128_reserve_quotes_title(tmp_path):
    box = _mk(tmp_path)
    box.reserve_r_number(EVIL)
    cmd = box._tb.commands[-1]
    assert _shq(EVIL) in cmd, f"title must be _shq-quoted in: {cmd}"
    # the raw payload must appear ONLY inside the quoted form
    assert "$(rm -rf /)" not in cmd.replace(_shq(EVIL), "")


def test_rf2128_ship_quotes_args(tmp_path):
    box = _mk(tmp_path)
    box.ship_r_number(EVIL, EVIL)
    # R-F2162's _record_shipped_fix may append a `git log` side-effect call after
    # the ship command, so assert the SHIP command (the one invoking the admin
    # script) quotes the args rather than assuming it is last.
    ship_cmds = [c for c in box._tb.commands if "reserve_r_number.py" in c and " ship " in c]
    assert ship_cmds, "ship_r_number should have issued a ship command"
    cmd = ship_cmds[-1]
    assert _shq(EVIL) in cmd
    assert "$(rm -rf /)" not in cmd.replace(_shq(EVIL), "")


def test_rf2128_ci_deploy_commit_quotes_msg(tmp_path):
    box = _mk(tmp_path)
    box.ci_deploy(summary=EVIL, r_number="R-F0001")
    commit_cmds = [c for c in box._tb.commands if c.startswith("git commit")]
    assert commit_cmds, "ci_deploy should have issued a git commit"
    # msg is sanitized (quotes->') then _shq-quoted; assert no bare $() survives
    for c in commit_cmds:
        # everything after `-m ` must be a single _shq token (starts with a quote)
        after = c.split("-m ", 1)[1]
        assert after and after[0] in ("'", '"'), f"commit msg not quoted: {c}"

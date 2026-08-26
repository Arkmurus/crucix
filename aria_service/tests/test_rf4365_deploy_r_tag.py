"""R-F4365 (C-311) — the deploy banner must name what the build ships.

MEASURED LIVE 2026-08-26: aria-intel served

    {"build_rev": "no-r-tag · sha f70d073f"}

for a build containing **R-F4362 and R-F4364**. The last commit before the deploy
was a docs commit, and all four deploy workflows derived the tag from HEAD's
subject alone:

    R_TAG=$(git log -1 --pretty=%s | grep -oE 'R-F[0-9]+' | head -1)
    R_TAG=${R_TAG:-no-r-tag}

"no-r-tag" reads as *this build ships nothing* on a build containing everything.

WHY IT IS NOT COSMETIC. `/health/live` renders this as `build_rev`, and
CLAUDE.md §11 makes it the anchor of deploy verification — *"a deploy is NOT done
until you have PROVEN it live … CONFIRM the build_rev matches your commit"*. A
banner that misreports the build corrodes the one check the protocol rests on.

`scripts/deploy.ps1` had already solved this THREE times — R-F3357 (empty range),
R-F3247 (registry bookkeeping), R-F3371 (mentioned-not-shipped and ships-nothing)
— and none of it reached the workflows, which §11 now names as the PRIMARY deploy
path ("prefer dispatch over deploy.ps1"). Fixed in one place, still open in the
one that runs.

Four naive copies existed (deploy-fly ×2, deploy-wa, deploy-web). They now call
ONE script, so the next workflow cannot reintroduce a fifth variant.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "scripts" / "ci" / "derive_r_tag.sh"
_BASH = shutil.which("bash") or shutil.which("sh")


def test_the_script_exists_and_is_the_one_definition() -> None:
    assert _SCRIPT.exists(), "no shared derivation — the four copies would drift"


def test_no_workflow_still_derives_the_tag_from_HEAD_alone() -> None:
    """THE DEFECT. Four workflows each had their own `git log -1` version. A
    shared script only helps if nothing bypasses it — this is the guard that
    keeps a fifth copy from appearing."""
    wf_dir = _REPO / ".github" / "workflows"
    offenders = []
    for wf in wf_dir.glob("deploy-*.yml"):
        text = wf.read_text(encoding="utf-8", errors="replace")
        if "R_TAG=$(git log -1" in text:
            offenders.append(wf.name)
    assert not offenders, (
        f"{offenders} still derive the R-tag from HEAD's subject alone, so a "
        "docs or ship-mark commit at HEAD renders 'no-r-tag' on a build that "
        "ships R-numbers")


def test_every_deploy_workflow_uses_the_shared_script() -> None:
    """Each deploy workflow must actually call it — presence is not adoption."""
    wf_dir = _REPO / ".github" / "workflows"
    missing = []
    for wf in wf_dir.glob("deploy-*.yml"):
        text = wf.read_text(encoding="utf-8", errors="replace")
        if "R_TAG=" in text and "derive_r_tag.sh" not in text:
            missing.append(wf.name)
    assert not missing, f"{missing} set R_TAG without using the shared derivation"


@pytest.mark.skipif(_BASH is None, reason="no POSIX shell available")
def test_it_names_real_r_numbers_on_this_repo() -> None:
    """CAPABILITY — run the real script against the real history.

    The live symptom was a build that shipped R-numbers reporting none, so the
    property that matters is: on a history containing shipping commits, it must
    NOT say 'no-r-tag'.
    """
    out = subprocess.run(
        [_BASH, str(_SCRIPT)], cwd=str(_REPO), capture_output=True,
        text=True, timeout=180,
    ).stdout.strip()

    assert out, "produced no output at all"
    # Either a real tag, or an honest no-r-tag ONLY if nothing shipped since the
    # last deploy point. This repo has shipped today, so it must name something.
    assert out.startswith("R-F"), (
        f"rendered {out!r} on a repo with shipping commits since the last "
        "deploy tag — the under-claim is still present")


@pytest.mark.skipif(_BASH is None, reason="no POSIX shell available")
def test_exclusion_rules_against_a_CONTROLLED_history(tmp_path) -> None:
    """R-F3247 + R-F3371, tested where they can actually fail.

    An earlier version of this test asserted `out != <bookkeeping R-number>`
    against the REAL repo — vacuous, because those same R-numbers legitimately
    appear via their real fix commits, so it could never fail. A mutation run
    proved it: deleting both exclusion rules left every test green.

    So build a history where each rule is the ONLY thing that can exclude a
    commit: one real code change, one registry ship-mark, one docs-only commit,
    each carrying a distinct R-number.
    """
    import shutil as _sh

    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, capture_output=True,
                              text=True, timeout=60)

    def commit(path, subject):
        f = tmp_path / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(chr(120) + chr(10), encoding="utf-8")
        git("add", "-A")
        git("commit", "-q", "-m", subject)

    git("init", "-q")
    git("config", "user.email", "t@t.t")
    git("config", "user.name", "t")
    git("commit", "-q", "--allow-empty", "-m", "root")

    commit("aria_service/x.py", "fix: R-F9003 a real change that ships code")
    commit("data/r_number_reservations.json", "chore: ship-mark R-F9002, close C-1")
    commit("docs/note.md", "docs: R-F9001 record something")
    # R-F3247 acts BY SUBJECT, so it must exclude a bookkeeping commit even when
    # that commit touches code. Without this case the ships-nothing rule already
    # covers the ledger-only shape and the chore rule is untestable — a mutation
    # run proved exactly that by deleting it with every test still green.
    commit("aria_service/y.py", "chore: ship-mark R-F9004, close C-2")

    _sh.copy2(_SCRIPT, tmp_path / "derive.sh")
    out = subprocess.run([_BASH, "./derive.sh"], cwd=tmp_path, capture_output=True,
                         text=True, timeout=120).stdout.strip()

    assert "R-F9003" in out, f"the real code commit did not supply the tag ({out!r})"
    assert "R-F9002" not in out, (
        f"a `chore: ship-mark` supplied the tag ({out!r}) — it touches the "
        "registry, not the image (R-F3247)")
    assert "R-F9001" not in out, (
        f"a docs-only commit supplied the tag ({out!r}) — it ships nothing into "
        "the image (R-F3371)")
    assert "R-F9004" not in out, (
        f"a `chore: ship-mark` supplied the tag ({out!r}) even though it is "
        "bookkeeping by subject — R-F3247 excludes those regardless of the "
        "paths they happen to touch")


@pytest.mark.skipif(_BASH is None, reason="no POSIX shell available")
def test_a_window_with_no_shipping_commit_says_so_honestly(tmp_path) -> None:
    """`no-r-tag` must remain REACHABLE and TRUE: when nothing in the window
    ships, saying so is correct. The defect was saying it when things did."""
    import shutil as _sh

    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, capture_output=True,
                              text=True, timeout=60)

    git("init", "-q"); git("config", "user.email", "t@t.t"); git("config", "user.name", "t")
    git("commit", "-q", "--allow-empty", "-m", "root")
    (tmp_path / "docs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / "a.md").write_text(chr(120) + chr(10), encoding="utf-8")
    git("add", "-A"); git("commit", "-q", "-m", "docs: R-F9001 prose only")

    _sh.copy2(_SCRIPT, tmp_path / "derive.sh")
    out = subprocess.run([_BASH, "./derive.sh"], cwd=tmp_path, capture_output=True,
                         text=True, timeout=120).stdout.strip()
    assert out == "no-r-tag", f"expected an honest no-r-tag, got {out!r}"

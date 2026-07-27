"""R-F3248 — `reserve()` must refuse a number git history has already burned.

SYMPTOM (live, 2026-07-27, three collisions in ONE session): R-F3237, R-F3243 and
R-F3245 were each issued to a second agent while a commit subject already claimed
them. R-F3243 is the worst of the three — `cb061cfe` ("fix: R-F3238/R-F3239/
R-F3243/R-F3244 - the 360 sweep the live DD exposed") and `d98c2063` ("fix: R-F3243
— the weekly report's executive summary has never once worked") BOTH carry it, and
the registry entry ended up holding one agent's TITLE against the other agent's SHA.
That is the same "wrong work stamped with the wrong SHA" outcome R-F3187 recorded
for R-F3183.

CAUSE: `reserve()` and `peek_next()` allocate from `data/r_number_reservations.json`
ALONE — the `while n in existing_nums` skip loop is built purely from entries in the
JSON file. Git history is never consulted. So the moment a claim is missing from the
file, the number is handed out AGAIN:

  * a fail-open clobber lost the entry (the R-F3187 class — the lock is deliberately
    fail-open and must stay that way);
  * the agent is in a worktree whose checkout of the file predates the claim;
  * an agent committed an R-number without reserving it at all.

Git is the one record in this system that cannot be clobbered, and a commit subject
is a permanent, public claim. R-F3095 already built the scanner (`scan_shipped_r_numbers`)
for the reconcile path; allocation never used it.

These tests drive the REAL `reserve()` / `peek_next()` against a repo whose history
burns a number the registry file does not know about, and assert it is never reissued.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from aria_service.intel import r_number_registry as reg


# ──────────────────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────────────────
def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=str(repo), check=True,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def _make_repo(tmp_path: Path, subjects: list[str], bodies: list[str] | None = None) -> Path:
    """A throwaway git repo whose commit SUBJECTS burn the given R-numbers.

    The registry lives at `<repo>/data/r_number_reservations.json` so the repo root
    is derived exactly the way production derives it — the test drives the default
    path, it does not hand the code a special-cased root.
    """
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "test")
    _git(repo, "config", "commit.gpgsign", "false")
    bodies = bodies or [""] * len(subjects)
    for i, (subject, body) in enumerate(zip(subjects, bodies)):
        (repo / f"f{i}.txt").write_text(f"change {i}\n", encoding="utf-8")
        _git(repo, "add", "-A")
        args = ["commit", "-q", "-m", subject]
        if body:
            args += ["-m", body]
        _git(repo, *args)
    return repo


def _registry(repo: Path, next_available: int, reservations: list[dict] | None = None) -> Path:
    p = repo / "data" / "r_number_reservations.json"
    p.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "next_available": next_available,
                "reservations": reservations or [],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return p


# ──────────────────────────────────────────────────────────────────────────────
# THE CAPABILITY TEST — the live symptom, reproduced
# ──────────────────────────────────────────────────────────────────────────────
def test_reserve_does_not_reissue_a_number_burned_by_a_commit_subject(tmp_path: Path) -> None:
    """The R-F3243 collision, reproduced end to end.

    The registry file has NO record of R-F9001 (exactly the state after a fail-open
    clobber), but a commit subject already shipped it. Pre-fix, `reserve()` hands
    out R-F9001 a second time.
    """
    repo = _make_repo(tmp_path, ["fix: R-F9001 - the number a peer already shipped"])
    path = _registry(repo, next_available=9001)

    claimed = reg.reserve("my new work", path=path)

    assert claimed != "R-F9001", (
        "reserve() reissued R-F9001 — a commit subject already claims it. "
        "This is the R-F3237/R-F3243/R-F3245 collision."
    )
    assert claimed == "R-F9002"
    # and the claim is genuinely recorded (R-F3187 contract still holds)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert [r["r_number"] for r in data["reservations"]] == ["R-F9002"]


def test_peek_next_agrees_with_reserve(tmp_path: Path) -> None:
    """A peek that promises a number reserve would skip is a lie the caller acts on."""
    repo = _make_repo(tmp_path, ["fix: R-F9001 - already shipped"])
    path = _registry(repo, next_available=9001)

    peeked = reg.peek_next(path=path)
    claimed = reg.reserve("work", path=path)

    assert peeked == claimed == "R-F9002"


def test_a_run_of_burned_numbers_is_skipped_entirely(tmp_path: Path) -> None:
    """The real collisions came in a run (R-F3238/R-F3239/R-F3243/R-F3244 in one subject)."""
    repo = _make_repo(tmp_path, ["fix: R-F9001/9002/9003 - a batch subject, shorthand form"])
    path = _registry(repo, next_available=9001)

    assert reg.reserve("work", path=path) == "R-F9004"


def test_body_only_mention_also_blocks_allocation(tmp_path: Path) -> None:
    """Deliberately STRICTER than `reconcile_with_git`, and the asymmetry is the point.

    Reconcile refuses to ship-mark on a body mention because writing a false ship
    record corrupts the audit trail. Allocation faces the opposite cost function: the
    penalty for skipping a number that was only ever mentioned is ONE unused integer,
    while the penalty for issuing a number someone else is already using is a rename
    pass across code, commits and the registry. Skip it.
    """
    repo = _make_repo(
        tmp_path,
        ["chore: unrelated housekeeping"],
        ["Follow-up tracked as R-F9001 - do not reuse."],
    )
    path = _registry(repo, next_available=9001)

    assert reg.reserve("work", path=path) == "R-F9002"


def test_file_entries_and_git_are_unioned_not_substituted(tmp_path: Path) -> None:
    """Git must ADD to the file's knowledge, never replace it."""
    repo = _make_repo(tmp_path, ["fix: R-F9002 - burned in git only"])
    path = _registry(
        repo,
        next_available=9001,
        reservations=[{
            "r_number": "R-F9001", "title": "known only to the file",
            "claimed_at": "2026-07-27T09:00:00Z", "claimed_by": "peer",
            "status": "in_progress", "commit_sha": None, "notes": None,
        }],
    )

    # 9001 blocked by the file, 9002 blocked by git → 9003.
    assert reg.reserve("work", path=path) == "R-F9003"


# ──────────────────────────────────────────────────────────────────────────────
# fail-open: the lock is fail-open by design (R-F1026/R-F3187) and so is this
# ──────────────────────────────────────────────────────────────────────────────
def test_reserve_still_works_when_there_is_no_git_repo(tmp_path: Path) -> None:
    """No history readable → allocate from the file alone, exactly as before.

    This must NEVER hang or refuse: `_file_lock` proceeds without the lock rather
    than block a deploy, and an unreadable history must degrade the same way. It is
    also what keeps every existing tmp-registry test (R-F1026, R-F3187) valid.
    """
    path = tmp_path / "data" / "r_number_reservations.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"schema_version": 1, "next_available": 700, "reservations": []}),
        encoding="utf-8",
    )

    assert reg.reserve("work", path=path) == "R-F700"


def test_git_readability_is_reported_not_guessed(tmp_path: Path) -> None:
    """`(numbers, readable)` — an empty set from a dead git must not read as 'nothing shipped'.

    ABSENT IS NOT FALSE: if the scan cannot run, the caller has to know that the
    guarantee did not apply, or a fail-open degrade silently becomes a false clean.
    """
    nums, readable = reg.r_numbers_known_to_git(repo_root=tmp_path / "not-a-repo")
    assert nums == set()
    assert readable is False

    repo = _make_repo(tmp_path, ["fix: R-F9001 - shipped"])
    nums, readable = reg.r_numbers_known_to_git(repo_root=repo)
    assert readable is True
    assert 9001 in nums


# ──────────────────────────────────────────────────────────────────────────────
# the live defect data
# ──────────────────────────────────────────────────────────────────────────────
def test_the_real_repo_will_not_reissue_the_collided_numbers() -> None:
    """R-F3237, R-F3243 and R-F3245 are burned in this repo's history — forever."""
    nums, readable = reg.r_numbers_known_to_git()
    if not readable:
        pytest.skip("git history unreadable here (shallow clone / no git) — cannot assert")
    if 3243 not in nums and 3247 not in nums:
        pytest.skip("history does not reach the 2026-07-27 commits (shallow clone)")
    for burned in (3237, 3243, 3245):
        assert burned in nums, f"R-F{burned} is in a commit subject but the scan missed it"

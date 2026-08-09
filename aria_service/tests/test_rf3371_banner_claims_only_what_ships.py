"""R-F3371 — the build_rev banner claimed R-numbers the build did not ship.

The banner is a claim about what is IN the build. Two over-claims were measured
live on 2026-07-28, both from taking EVERY R-number out of EVERY commit subject:

  MENTIONED-NOT-SHIPPED — "fix: R-F3365 - wedge #5: R-F3347 fixed one lifespan
  entry, not the class" put R-F3347 in the banner. R-F3347 had shipped days
  earlier; that commit merely cites it. Subjects in this repo are
  "<type>: R-F#### - ...", so the FIRST R-number is the one the commit ships and
  any others are prose.

  SHIPS-NOTHING — "docs: R-F3368 - record the measured suite baseline" put
  R-F3368 in the banner for a commit touching only docs/, CLAUDE.md and the
  R-number registry, none of which is in the image. The session-record commits
  did the same, re-announcing R-numbers that were already live.

This is the third instalment of one family and the reason to fix the rule rather
than the symptom: R-F3247 removed reservation commits, R-F3357 removed the
empty-range "no-new-r-numbers" case, and both left the underlying assumption
intact — that a commit message is evidence of what a build contains.

The rule is mirrored here in Python and exercised against REAL commits from this
repo, so it is a behavioural check rather than a spelling assertion about the
scripts. Both writers must carry it: this repo has shipped the same defect twice
by fixing one of two (R-F3019->R-F3031->R-F3038, R-F3039->R-F3050).
"""
from __future__ import annotations

import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
PS1 = ROOT / "scripts" / "deploy.ps1"
SH = ROOT / "scripts" / "deploy.sh"

BOOKKEEPING = re.compile(r"^chore:\s*(reserve|mark|ship)")
SHIPS_NOTHING = re.compile(r"^(docs/|memory/|[^/]*\.md$|data/r_number_reservations\.json$)")
R_NUMBER = re.compile(r"R-F[0-9]+")


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
    ).stdout.strip()


def _banner_r_numbers(commits: list[str]) -> list[str]:
    """The R-F3371 rule: own R-number only, and only from commits that ship."""
    out: set[str] = set()
    for sha in commits:
        subject = _git("log", "-1", "--pretty=%s", sha)
        if BOOKKEEPING.match(subject):
            continue
        files = [f for f in _git("diff-tree", "--no-commit-id", "--name-only", "-r", sha).splitlines() if f]
        if files and not any(not SHIPS_NOTHING.match(f) for f in files):
            continue
        own = R_NUMBER.search(subject)
        if own:
            out.add(own.group(0))
    return sorted(out)


def _find(pattern: str) -> str | None:
    """Newest commit whose SUBJECT matches `pattern`, searched over ALL history.

    R-F3796 — this used to walk only the newest 400 commits, so the three commits
    these tests reproduce against silently fell out of the window as the repo grew.
    Measured 2026-08-09: `fix: R-F3365` is 600 commits back. `_find` returned None,
    and all three tests failed claiming "the commit must exist" — for commits that
    do exist. A moving window that reports absence is the same defect class §1
    records elsewhere: a lookup whose failure is indistinguishable from a real
    negative. Raising the limit would only move the date it breaks again.

    `--grep` also makes this ONE subprocess instead of 401 (the old loop shelled out
    per commit to read each subject, which is why this file took ~55s).

    `-E` for POSIX ERE, and `--no-merges` so a merge commit that quotes the subject
    in its own message cannot shadow the real one.
    """
    sha = _git("log", "--all", "--no-merges", "-E", f"--grep={pattern}",
               "--pretty=%H", "-1")
    return sha or None


def test_rf3371_a_cited_r_number_does_not_reach_the_banner():
    """THE OVER-CLAIM, reproduced on the real commit that caused it."""
    sha = _find(r"^fix: R-F3365")
    assert sha, "the R-F3365 commit must exist for this reproduction"
    subject = _git("log", "-1", "--pretty=%s", sha)
    assert "R-F3347" in subject, (
        "precondition: that subject must still CITE R-F3347, or this test no "
        "longer reproduces the defect"
    )
    assert _banner_r_numbers([sha]) == ["R-F3365"], (
        "a commit must claim only its OWN R-number; R-F3347 is cited prose and "
        "shipped days earlier"
    )


def test_rf3371_a_docs_only_commit_claims_nothing():
    """A commit that changes no file in the image cannot be in the banner."""
    sha = _find(r"^docs: R-F3368")
    assert sha, "the R-F3368 docs commit must exist for this reproduction"
    files = _git("diff-tree", "--no-commit-id", "--name-only", "-r", sha).splitlines()
    assert files, "precondition: the commit must actually touch files"
    assert _banner_r_numbers([sha]) == [], (
        f"a docs-only commit reached the banner; it touched {files}"
    )


def test_rf3371_a_real_code_commit_still_claims_its_number():
    """The filter must stay narrow — this is how an over-correction would show."""
    sha = _find(r"^fix: R-F3370")
    assert sha, "the R-F3370 commit must exist"
    assert _banner_r_numbers([sha]) == ["R-F3370"], "a test-tier code change still ships"


def test_rf3371_both_deploy_writers_carry_the_rule():
    """One fix reaching one of two writers is how this repo has shipped the same
    defect twice before (R-F3019->R-F3031->R-F3038, R-F3039->R-F3050)."""
    ps1 = PS1.read_text(encoding="utf-8")
    sh = SH.read_text(encoding="utf-8")
    for name, src in (("deploy.ps1", ps1), ("deploy.sh", sh)):
        assert "R-F3371" in src, f"{name} lost the fix"
        assert "diff-tree" in src, (
            f"{name} still derives R-numbers from subjects alone — it cannot tell "
            f"a docs-only commit from one that ships"
        )
    # The earlier members of this family must survive.
    for name, src in (("deploy.ps1", ps1), ("deploy.sh", sh)):
        assert "R-F3247" in src, f"{name} lost the reservation-commit filter"
        assert "R-F3357" in src, f"{name} lost the tag-at-HEAD fix"

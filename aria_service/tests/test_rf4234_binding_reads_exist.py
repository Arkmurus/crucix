"""R-F4234 — §1 and §20 bound every session to read a file that never existed.

`memory/platform_buildout_north_star.md` was named in CLAUDE.md §1 (the Phase
line) AND §20 (the session-OPEN ritual) as a required read. It is not in the repo
and never has been:

    git log --oneline --all --diff-filter=A -- memory/platform_buildout_north_star.md
    -> (empty)
    git log --oneline --all --diff-filter=D -- memory/platform_buildout_north_star.md
    -> (empty)

Never added, never deleted. So the first binding instruction of every session was
unperformable, and — because a missing file just reads as "nothing to see" — the
step could be skipped forever without anyone noticing. That is the shape §20
already records for the coding-RAG priming step (R-F3099: *"a mandatory step
certified by an absence"*) and §1 records for three Phase A gates.

The real documents are `docs/golden_intel_north_star_2026_07_14.md` (the USP:
Golden Intel as ARIA's decision-signal layer; the named gap is **value density**,
not guards) and `docs/aria_source_coverage_north_star_2026_07_14.md`.

## Why this test is a sweep and not a one-line assertion

Re-pointing one path fixes one path. What made it survive is that **nothing
checked**, so any future edit can reintroduce it just as silently. This asserts
the general property: every repo-relative document CLAUDE.md instructs a session
to READ must exist on disk.
"""
from __future__ import annotations

import re

import pytest

from ._source_probe import repo_path

# Lines that TELL a session to read something. Kept narrow on purpose: CLAUDE.md
# legitimately mentions many paths in passing (incident write-ups, corrected
# claims, historical notes), and asserting existence for all of them would be a
# guard nobody could keep green — which is how guards get deleted.
_INSTRUCTION_MARKERS = ("**Open**", "**Open (", "- **Phase**:")

# NO PROSE ESCAPE HATCH, AND THAT IS THE POINT.
#
# The first version of this guard skipped any line containing "never existed" /
# "DOES NOT EXIST", so a correction note could sit on the instruction line. In
# CLAUDE.md it did — and the exemption therefore blinded the guard on the single
# line that matters most. Proven by mutation: re-adding the phantom to the §20
# "Open" bullet still PASSED. The fix was to the DATA, not the check: historical
# notes now live on their own lines (which carry no instruction marker, so they
# are never scanned), and an instruction line is checked unconditionally.
#
# R-F3858: a guard that can be talked out of firing is not a guard.

_PATH_RE = re.compile(r"`((?:docs|memory)/[A-Za-z0-9_./-]+\.md)`")


def _claude_md() -> list[str]:
    return repo_path("CLAUDE.md").read_text(encoding="utf-8").splitlines()


def _instructed_paths() -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for i, line in enumerate(_claude_md(), start=1):
        if not any(m in line for m in _INSTRUCTION_MARKERS):
            continue
        for m in _PATH_RE.finditer(line):
            out.append((i, m.group(1)))
    return out


class TestEveryBindingReadExists:

    def test_the_sweep_finds_something_to_check(self):
        """A guard whose universe is empty always certifies (§16/R-F3791).

        If the markers stop matching, every assertion below passes vacuously and
        this file becomes decoration.
        """
        found = _instructed_paths()
        assert found, (
            "no instructed document paths found in CLAUDE.md — the §20 'Open' / "
            "§1 'Phase' markers have changed shape and this guard is now blind")

    def test_every_instructed_document_is_actually_in_the_repo(self):
        missing = [(ln, p) for ln, p in _instructed_paths()
                   if not repo_path(p).exists()]
        assert not missing, (
            "CLAUDE.md instructs every session to read files that are not in the "
            f"repo: {missing}. A binding step pointing at a missing file is "
            "unperformable AND silent — R-F4234 found "
            "`memory/platform_buildout_north_star.md` had been named in §1 and "
            "§20 without ever having existed. Either add the file or re-point "
            "the instruction.")


class TestThePhantomIsNotSilentlyReinstated:

    def test_the_north_star_documents_that_do_exist_are_the_ones_named(self):
        for real in ("docs/golden_intel_north_star_2026_07_14.md",
                     "docs/aria_source_coverage_north_star_2026_07_14.md"):
            assert repo_path(real).exists(), f"{real} is missing from the repo"

    def test_the_phantom_path_is_only_ever_mentioned_as_a_correction(self):
        """If someone re-points at it, they must first create it.

        This deliberately does NOT forbid the string — the §1/§20 correction notes
        name it so a future reader understands what happened. It forbids naming it
        on an INSTRUCTION line while it does not exist, which is the defect.
        """
        phantom = "memory/platform_buildout_north_star.md"
        if repo_path(phantom).exists():
            pytest.skip("the file now exists — pointing at it is legitimate")
        offenders = [(ln, p) for ln, p in _instructed_paths() if p == phantom]
        assert not offenders, (
            f"CLAUDE.md instructs a session to read {phantom} at line(s) "
            f"{[ln for ln, _ in offenders]}, and it does not exist "
            f"(git --diff-filter=A and =D are both empty for it — never added, "
            f"never deleted)")

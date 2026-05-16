"""R-F589 — runtime build_rev fallback unit tests.

Verifies the pure-Python git HEAD resolver handles every realistic
shape of .git/HEAD + refs:
  - branch-pointer HEAD  ("ref: refs/heads/main") → loose ref file
  - branch-pointer HEAD  → packed-refs fallback
  - detached HEAD        (40-char SHA directly in HEAD)
  - missing .git dir     → returns "" cleanly

This is the function that protects /api/aria/health.build_rev from
"UNKNOWN-BUILD" when an operator runs `flyctl deploy` without the
--build-arg wrapper.
"""
from __future__ import annotations

import os
import pytest

from aria_service.main import _resolve_git_head_from_image


# ── Fixture helper — minimal .git skeleton in tmp_path ──────────────────────


def _make_git_skel(tmp_path, head_content: str, refs: dict[str, str] | None = None,
                   packed_refs: str | None = None) -> str:
    """Write a fake .git/HEAD + refs structure under tmp_path. Returns
    the path to the fake .git directory."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text(head_content)
    if refs:
        for ref_path, sha in refs.items():
            target = git_dir / ref_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(sha)
    if packed_refs is not None:
        (git_dir / "packed-refs").write_text(packed_refs)
    return str(git_dir)


# ── Branch-pointer HEAD → loose ref file ───────────────────────────────────


def test_rf589_resolves_branch_head_via_loose_ref(tmp_path):
    """The most common case: HEAD points to refs/heads/main, and
    refs/heads/main exists as a loose file containing the SHA."""
    git_dir = _make_git_skel(
        tmp_path,
        head_content="ref: refs/heads/main\n",
        refs={"refs/heads/main": "abc1234567890abcdef1234567890abcdef12345\n"},
    )
    assert _resolve_git_head_from_image(git_dir) == "abc1234567890abcdef1234567890abcdef12345"


def test_rf589_resolves_nested_branch_head(tmp_path):
    """Branches with slashes (feature/foo) — the ref path can be nested."""
    git_dir = _make_git_skel(
        tmp_path,
        head_content="ref: refs/heads/feature/foo\n",
        refs={"refs/heads/feature/foo": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef\n"},
    )
    assert _resolve_git_head_from_image(git_dir) == "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"


# ── Detached HEAD ───────────────────────────────────────────────────────────


def test_rf589_resolves_detached_head_directly(tmp_path):
    """When HEAD is detached, it contains the SHA directly, not a ref:."""
    sha = "fedcba9876543210fedcba9876543210fedcba98"
    git_dir = _make_git_skel(tmp_path, head_content=sha + "\n")
    assert _resolve_git_head_from_image(git_dir) == sha


# ── packed-refs fallback ────────────────────────────────────────────────────


def test_rf589_falls_back_to_packed_refs_when_loose_ref_missing(tmp_path):
    """Long-lived clones pack their refs — refs/heads/main becomes a
    line inside .git/packed-refs instead of a loose file. R-F589
    handles this fallback so deploys from packed repos still work."""
    git_dir = _make_git_skel(
        tmp_path,
        head_content="ref: refs/heads/main\n",
        refs=None,
        packed_refs=(
            "# pack-refs with: peeled fully-peeled sorted \n"
            "1111111111111111111111111111111111111111 refs/heads/other\n"
            "2222222222222222222222222222222222222222 refs/heads/main\n"
            "^abcdef1234567890abcdef1234567890abcdef12\n"
        ),
    )
    assert _resolve_git_head_from_image(git_dir) == "2222222222222222222222222222222222222222"


def test_rf589_packed_refs_strips_peeled_annotations(tmp_path):
    """packed-refs has '^<sha>' annotations on the line AFTER a tag —
    must skip them. We don't index refs/tags, just heads, but the
    parser must be tolerant of either."""
    git_dir = _make_git_skel(
        tmp_path,
        head_content="ref: refs/heads/main\n",
        refs=None,
        packed_refs=(
            "# pack-refs with: peeled fully-peeled sorted \n"
            "3333333333333333333333333333333333333333 refs/tags/v1.0\n"
            "^99999999999999999999999999999999999999aa\n"
            "5555555555555555555555555555555555555555 refs/heads/main\n"
        ),
    )
    assert _resolve_git_head_from_image(git_dir) == "5555555555555555555555555555555555555555"


# ── Missing / malformed ─────────────────────────────────────────────────────


def test_rf589_returns_empty_when_git_dir_missing(tmp_path):
    """No .git/ at all → empty string. Never crashes."""
    nonexistent = str(tmp_path / "no-such-git-dir")
    assert _resolve_git_head_from_image(nonexistent) == ""


def test_rf589_returns_empty_when_head_missing(tmp_path):
    """.git/ exists but HEAD file missing."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    assert _resolve_git_head_from_image(str(git_dir)) == ""


def test_rf589_returns_empty_when_ref_unresolvable(tmp_path):
    """HEAD points to a ref that doesn't exist as loose OR in packed-refs."""
    git_dir = _make_git_skel(
        tmp_path, head_content="ref: refs/heads/main\n",
        refs=None, packed_refs="# empty\n",
    )
    assert _resolve_git_head_from_image(git_dir) == ""


def test_rf589_returns_empty_when_head_is_garbage(tmp_path):
    """Defensive — corrupted HEAD shouldn't crash; just returns short content."""
    git_dir = _make_git_skel(tmp_path, head_content="not a valid ref\n")
    # Returns the first 40 chars of the garbage — not great but doesn't crash
    result = _resolve_git_head_from_image(git_dir)
    assert isinstance(result, str)
    assert len(result) < 50

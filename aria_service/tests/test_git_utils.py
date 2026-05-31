"""R-F1188 — Tests for aria_service.utils.git_utils.

Tests the pure-Python git utilities that read .git/ files directly
without subprocess calls.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from aria_service.utils.git_utils import (
    check_push_guard,
    find_in_packed_refs,
    get_current_commit,
    get_head_sha,
    get_origin_main_sha,
    read_git_ref,
    resolve_git_root,
)


class TestReadGitRef:
    """read_git_ref reads SHA from ref files."""

    def test_returns_sha(self, tmp_path: Path) -> None:
        """Returns SHA from a plain ref file."""
        ref = tmp_path / "ref"
        ref.write_text(
            "abcdef1234567890abcdef1234567890abcdef12\n", encoding="utf-8",
        )
        assert read_git_ref(ref) == "abcdef1234567890abcdef1234567890abcdef12"

    def test_returns_none_for_symref(self, tmp_path: Path) -> None:
        """Returns None for symbolic refs."""
        ref = tmp_path / "HEAD"
        ref.write_text("ref: refs/heads/main\n", encoding="utf-8")
        assert read_git_ref(ref) is None

    def test_returns_none_for_missing(self, tmp_path: Path) -> None:
        """Returns None for missing files."""
        assert read_git_ref(tmp_path / "nonexistent") is None

    def test_returns_empty_for_empty(self, tmp_path: Path) -> None:
        """Returns empty string for empty files."""
        ref = tmp_path / "empty"
        ref.write_text("", encoding="utf-8")
        assert read_git_ref(ref) == ""


class TestFindInPackedRefs:
    """find_in_packed_refs finds refs in packed-refs format."""

    def test_finds_ref(self, tmp_path: Path) -> None:
        """Finds a ref in packed-refs."""
        packed = tmp_path / "packed-refs"
        packed.write_text(
            "# pack-refs with: peeled fully-peeled sorted\n"
            + "a" * 40 + " refs/heads/main\n"
            + "^b" * 20 + "\n"
            + "c" * 40 + " refs/remotes/origin/main\n",
            encoding="utf-8",
        )
        sha = find_in_packed_refs(packed, "refs/heads/main")
        assert sha == "a" * 40

    def test_not_found(self, tmp_path: Path) -> None:
        """Returns None when ref not found."""
        packed = tmp_path / "packed-refs"
        packed.write_text(
            "a" * 40 + " refs/heads/main\n", encoding="utf-8",
        )
        assert find_in_packed_refs(packed, "refs/heads/other") is None

    def test_missing_file(self, tmp_path: Path) -> None:
        """Returns None when file doesn't exist."""
        assert find_in_packed_refs(tmp_path / "nonexistent", "ref") is None


class TestGetHeadSha:
    """get_head_sha reads HEAD from .git/ files."""

    def test_from_loose_ref(self, tmp_path: Path) -> None:
        """Reads HEAD from .git/refs/heads/main."""
        git_dir = tmp_path / ".git"
        heads = git_dir / "refs" / "heads"
        heads.mkdir(parents=True)
        (heads / "main").write_text(
            "abcdef1234567890abcdef1234567890abcdef12\n", encoding="utf-8",
        )
        sha = get_head_sha(tmp_path)
        assert sha == "abcdef1234567890abcdef1234567890abcdef12"

    def test_from_packed_refs(self, tmp_path: Path) -> None:
        """Reads HEAD from .git/packed-refs when loose ref missing."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "packed-refs").write_text(
            "a" * 40 + " refs/heads/main\n", encoding="utf-8",
        )
        sha = get_head_sha(tmp_path)
        assert sha == "a" * 40

    def test_returns_none_when_no_git(self, tmp_path: Path) -> None:
        """Returns None when there's no .git directory."""
        sha = get_head_sha(tmp_path)
        assert sha is None


class TestGetOriginMainSha:
    """get_origin_main_sha reads origin/main from .git/ files."""

    def test_from_loose_ref(self, tmp_path: Path) -> None:
        """Reads origin/main from .git/refs/remotes/origin/main."""
        git_dir = tmp_path / ".git"
        remotes = git_dir / "refs" / "remotes" / "origin"
        remotes.mkdir(parents=True)
        (remotes / "main").write_text(
            "abcdef1234567890abcdef1234567890abcdef12\n", encoding="utf-8",
        )
        sha = get_origin_main_sha(tmp_path)
        assert sha == "abcdef1234567890abcdef1234567890abcdef12"

    def test_returns_none_when_missing(self, tmp_path: Path) -> None:
        """Returns None when origin/main doesn't exist."""
        sha = get_origin_main_sha(tmp_path)
        assert sha is None


class TestCheckPushGuard:
    """check_push_guard verifies HEAD matches origin/main."""

    def test_passes_when_matched(self, tmp_path: Path) -> None:
        """Returns True when HEAD matches origin/main."""
        git_dir = tmp_path / ".git"
        heads = git_dir / "refs" / "heads"
        heads.mkdir(parents=True)
        (heads / "main").write_text(
            "abcdef1234567890abcdef1234567890abcdef12\n", encoding="utf-8",
        )
        remotes = git_dir / "refs" / "remotes" / "origin"
        remotes.mkdir(parents=True)
        (remotes / "main").write_text(
            "abcdef1234567890abcdef1234567890abcdef12\n", encoding="utf-8",
        )
        assert check_push_guard(
            "abcdef1234567890abcdef1234567890abcdef12",
            git_root=tmp_path,
        ) is True

    def test_fails_when_not_pushed(self, tmp_path: Path) -> None:
        """Returns False when HEAD != origin/main."""
        git_dir = tmp_path / ".git"
        heads = git_dir / "refs" / "heads"
        heads.mkdir(parents=True)
        (heads / "main").write_text(
            "b" * 40 + "\n", encoding="utf-8",
        )
        remotes = git_dir / "refs" / "remotes" / "origin"
        remotes.mkdir(parents=True)
        (remotes / "main").write_text(
            "a" * 40 + "\n", encoding="utf-8",
        )
        assert check_push_guard(
            "b" * 40, git_root=tmp_path,
        ) is False

    def test_fails_when_no_remote(self, tmp_path: Path) -> None:
        """Returns False when origin/main doesn't exist."""
        git_dir = tmp_path / ".git"
        heads = git_dir / "refs" / "heads"
        heads.mkdir(parents=True)
        (heads / "main").write_text(
            "a" * 40 + "\n", encoding="utf-8",
        )
        assert check_push_guard(
            "a" * 40, git_root=tmp_path,
        ) is False

    def test_fails_when_no_git(self, tmp_path: Path) -> None:
        """Returns False when there's no .git directory."""
        assert check_push_guard(
            "a" * 40, git_root=tmp_path,
        ) is False


class TestGetCurrentCommit:
    """get_current_commit returns commit hash and message."""

    def test_returns_commit(self, tmp_path: Path) -> None:
        """Returns short SHA from .git/refs/heads/main."""
        git_dir = tmp_path / ".git"
        heads = git_dir / "refs" / "heads"
        heads.mkdir(parents=True)
        (heads / "main").write_text(
            "abcdef1234567890abcdef1234567890abcdef12\n", encoding="utf-8",
        )
        with patch(
            "aria_service.utils.git_utils.resolve_git_root",
            return_value=tmp_path,
        ):
            commit_hash, commit_message = get_current_commit()
            assert commit_hash == "abcdef12"

    def test_fallback(self) -> None:
        """Returns 'unknown' when no .git."""
        with patch(
            "aria_service.utils.git_utils.resolve_git_root",
            return_value=None,
        ):
            commit_hash, commit_message = get_current_commit()
            assert commit_hash == "unknown"
            assert commit_message == "Manual deployment"


class TestResolveGitRoot:
    """resolve_git_root finds the git root directory."""

    def test_finds_git_root(self, tmp_path: Path) -> None:
        """Finds the repo root when .git/ exists."""
        (tmp_path / ".git").mkdir()
        root = resolve_git_root(start_path=tmp_path)
        assert root == tmp_path

    def test_walks_up(self, tmp_path: Path) -> None:
        """Walks up from a subdirectory to find .git/."""
        (tmp_path / ".git").mkdir()
        subdir = tmp_path / "a" / "b" / "c"
        subdir.mkdir(parents=True)
        root = resolve_git_root(start_path=subdir)
        assert root == tmp_path

    def test_returns_none(self, tmp_path: Path) -> None:
        """Returns None when no .git/ found."""
        root = resolve_git_root(start_path=tmp_path)
        assert root is None

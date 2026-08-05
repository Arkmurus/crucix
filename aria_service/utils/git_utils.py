"""R-F1188 — Pure-Python git utilities (no subprocess).

Reads git refs directly from .git/ files so ARIA can verify commit
SHAs and push status without shelling out to `git`. Constitutional
validator compliant — no subprocess calls.

All functions are synchronous and stateless. No dependencies beyond
the standard library.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("aria.utils.git_utils")


def read_git_ref(ref_path: Path) -> Optional[str]:
    """Read a git ref file and return the SHA, or None.

    Handles:
      - Plain SHA files (refs/heads/main, refs/remotes/origin/main)
      - Symbolic refs like "ref: refs/heads/main" (returns None)
      - Missing files (returns None)
      - Corrupt files (returns None)
    """
    try:
        if ref_path.is_file():
            content = ref_path.read_text(encoding="utf-8").strip()
            if content.startswith("ref: "):
                return None
            return content
    except (OSError, UnicodeDecodeError):
        pass
    return None


def find_in_packed_refs(
    packed_refs_path: Path, ref_name: str,
) -> Optional[str]:
    """Find a ref in .git/packed-refs and return its SHA.

    Parses the packed-refs format:
      <sha> <refname>
      ^<peeled_sha>
      # comments
    """
    try:
        if packed_refs_path.is_file():
            for line in packed_refs_path.read_text(
                encoding="utf-8",
            ).splitlines():
                line = line.strip()
                if (
                    line
                    and not line.startswith("#")
                    and not line.startswith("^")
                ):
                    parts = line.split(" ", 1)
                    if len(parts) == 2 and parts[1] == ref_name:
                        return parts[0]
    except (OSError, UnicodeDecodeError):
        pass
    return None


def resolve_git_root(start_path: Optional[Path] = None) -> Optional[Path]:
    """Find the git root directory by walking up looking for .git/.

    Args:
        start_path: Directory to start from (default: cwd).

    Returns:
        Path to the repo root, or None if no .git/ found within 10 levels.
    """
    current = (start_path or Path.cwd()).resolve()
    for _ in range(10):
        if (current / ".git").is_dir():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def get_head_sha(git_root: Path) -> Optional[str]:
    """Get the SHA of HEAD by reading .git/ files.

    Tries, in order:
      1. .git/refs/heads/main (loose ref)
      2. .git/packed-refs (packed ref)
      3. .git/HEAD symref resolution

    Returns the full 40-char SHA, or None.
    """
    git_dir = git_root / ".git"

    # Try loose ref first
    head_sha = read_git_ref(git_dir / "refs" / "heads" / "main")
    if head_sha:
        return head_sha

    # Try packed refs
    head_sha = find_in_packed_refs(
        git_dir / "packed-refs", "refs/heads/main",
    )
    if head_sha:
        return head_sha

    # Try HEAD symref resolution
    head_content = read_git_ref(git_dir / "HEAD")
    if head_content and head_content.startswith("ref: "):
        ref_path_str = head_content[5:].strip()
        return read_git_ref(git_dir / ref_path_str)

    return None


def get_origin_main_sha(git_root: Path) -> Optional[str]:
    """Get the SHA of origin/main by reading .git/ files.

    Tries, in order:
      1. .git/refs/remotes/origin/main (loose ref)
      2. .git/packed-refs (packed ref)

    Returns the full 40-char SHA, or None.
    """
    git_dir = git_root / ".git"

    origin_sha = read_git_ref(
        git_dir / "refs" / "remotes" / "origin" / "main",
    )
    if origin_sha:
        return origin_sha

    return find_in_packed_refs(
        git_dir / "packed-refs", "refs/remotes/origin/main",
    )


def check_push_guard(
    commit_sha: str,
    git_root: Optional[Path] = None,
) -> bool:
    """Verify HEAD matches origin/main. §21a-wired wrapper — see _impl below.

    R-F3731 — THE REFUSALS WENT INTO A LOG NOBODY READS.

    This guard is what stops a deploy of un-pushed code (§11), and every one of
    its SEVEN refusal paths ended at `logger.warning`/`logger.error` and nothing
    else. So "the deploy guard refused" — including "I could not read
    origin/main", which is an infrastructure fault, not a developer mistake —
    never reached the brain, could not raise a gap, and could not self-heal.
    §21a is explicit that a local log is DARK, not wired.

    Wrapping rather than patching each `return False`: the refusals are spread
    over seven branches today and a future eighth would silently miss its
    signal. A wrapper covers every path by construction, including ones not yet
    written.

    The import is LAZY and failure-tolerant on purpose — this module's contract
    is "no dependencies beyond the standard library" (see the module docstring),
    and a wiring import must never be what stops a deploy guard from answering.
    """
    ok = False
    try:
        ok = _check_push_guard_impl(commit_sha, git_root)
        return ok
    finally:
        try:
            from ..intel.engine_wiring import wire_failure, wire_success
            if ok:
                wire_success(
                    module="git_utils",
                    summary="push guard passed: HEAD == origin/main",
                    detail=f"commit {commit_sha[:8]}",
                    source_id="git_utils:check_push_guard",
                )
            else:
                wire_failure(
                    module="git_utils",
                    detail=(f"push guard REFUSED deploy of {commit_sha[:8]} — "
                            f"HEAD/origin-main mismatch or refs unreadable "
                            f"(see git_utils warnings for which)"),
                    gap_type="engine_failure",
                    source="git_utils:check_push_guard:R-F3731",
                )
        except Exception:       # never let wiring break the guard itself
            pass


def _check_push_guard_impl(
    commit_sha: str,
    git_root: Optional[Path] = None,
) -> bool:
    """Verify HEAD matches origin/main.

    This prevents deploying un-pushed commits — the live server would
    run un-backed-up code and origin/main would diverge from production.

    Args:
        commit_sha: The full commit SHA to verify.
        git_root: Git repo root (auto-detected if None).

    Returns:
        True if HEAD == origin/main == commit_sha.
    """
    resolved_root = git_root or resolve_git_root()
    if not resolved_root:
        logger.warning(
            "[git_utils] push guard: no .git directory found — "
            "cannot verify push status",
        )
        return False

    git_dir = resolved_root / ".git"
    if not git_dir.is_dir():
        logger.warning(
            "[git_utils] push guard: no .git directory at %s — "
            "cannot verify push status",
            git_dir,
        )
        return False

    # Read HEAD SHA
    head_sha = get_head_sha(resolved_root)
    if not head_sha:
        logger.warning(
            "[git_utils] push guard: cannot read HEAD ref — refusing deploy",
        )
        return False

    # Verify HEAD matches the provided commit_sha
    if head_sha != commit_sha:
        logger.warning(
            "[git_utils] push guard: HEAD (%s) != "
            "provided commit_sha (%s) — mismatch",
            head_sha[:8], commit_sha[:8],
        )
        return False

    # Read origin/main SHA
    origin_sha = get_origin_main_sha(resolved_root)
    if not origin_sha:
        logger.warning(
            "[git_utils] push guard: cannot read origin/main "
            "ref — no remote or not fetched. Push manually first.",
        )
        return False

    if head_sha != origin_sha:
        logger.error(
            "[git_utils] PUSH GUARD: HEAD (%s) != "
            "origin/main (%s) — push your commit before deploying",
            head_sha[:8], origin_sha[:8],
        )
        return False

    logger.info(
        "[git_utils] push guard: HEAD matches origin/main (%s)",
        commit_sha[:8],
    )
    return True


def get_current_commit(git_root: Optional[Path] = None) -> tuple[str, str]:
    """Get current git commit hash and message.

    Reads .git/ files directly — no subprocess calls.
    Falls back to ('unknown', 'Manual deployment') if git is not available.

    Returns:
        (short_sha_8chars, commit_message)
    """
    resolved_root = git_root or resolve_git_root()
    if not resolved_root:
        return "unknown", "Manual deployment"

    head_sha = get_head_sha(resolved_root)
    commit_hash = (head_sha or "unknown")[:8]

    # Try to read commit message from .git/COMMIT_EDITMSG
    # Note: this file only exists during an active git commit.
    # In production (on Fly.io), this will fall back to
    # "Autonomous deployment".
    commit_message = "Autonomous deployment"
    try:
        msg_file = resolved_root / ".git" / "COMMIT_EDITMSG"
        if msg_file.is_file():
            commit_message = (
                msg_file.read_text(encoding="utf-8")
                .strip()
                .split("\n")[0]
            )
    except (OSError, UnicodeDecodeError):
        pass

    return commit_hash, commit_message

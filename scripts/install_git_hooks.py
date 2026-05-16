#!/usr/bin/env python3
"""R-F559 — one-shot installer for repo git hooks.

Configures `core.hooksPath` to point at `scripts/git-hooks` so the
pre-push verifier runs on every push.

Idempotent. Safe to re-run.
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    hooks_dir = repo / "scripts" / "git-hooks"
    pre_push = hooks_dir / "pre-push"

    if not pre_push.exists():
        print(f"ERROR: {pre_push} missing — repo state broken", file=sys.stderr)
        return 1

    # Make the hook executable on POSIX systems.
    if os.name != "nt":
        st = pre_push.stat()
        pre_push.chmod(st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    # Point git at our hooks dir.
    rel = pre_push.parent.relative_to(repo).as_posix()
    subprocess.check_call(
        ["git", "config", "core.hooksPath", rel],
        cwd=repo,
    )
    print(f"[R-F559] core.hooksPath -> {rel}")
    print(f"[R-F559] pre-push hook installed at {pre_push}")
    print("[R-F559] verify with: git config core.hooksPath")
    return 0


if __name__ == "__main__":
    sys.exit(main())

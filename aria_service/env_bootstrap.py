"""R-F3398 — find the project's own environment, and refuse to run without it.

Nothing in the training tooling loaded the repo `.env`, so a capture run on a
machine where Companies House IS configured still executed with no key. The
workaround was to source it by hand on every invocation, which CLAUDE.md §1
forbids and which leaks secret values into shell history.

The failure that followed was the serious one. With no key, `search_companies`
returned nothing and the capture printed `SKIP <subject>: no registry match` for
all 44 subjects, then wrote an empty corpus over a populated one and exited 0.
"We are not configured to look" and "we looked and found nothing" are opposite
claims, and the tooling asserted the second while the first was true — the
absence-is-not-evidence class, pointed straight at the data that trains the
model. `require_env` exists so that path fails at the precondition instead.

STDLIB ONLY (§6). aria_cli already carried this loader; it now delegates here
rather than keeping a second copy, because two loaders drift. It lives in
`aria_service` rather than `aria_cli` so scripts can import it without dragging
in prompt_toolkit and ~390ms of interactive-CLI startup.

EXISTING ENVIRONMENT ALWAYS WINS. On fly the real secrets arrive as environment
variables; a stale `.env` inside an image must never override them.
"""
from __future__ import annotations

import os
from pathlib import Path


class MissingCredentials(RuntimeError):
    """A required credential is absent. Never carries a credential VALUE."""


def find_repo_root(start: Path | None = None) -> Path | None:
    """Walk up from ``start`` to the crucix root (holds aria_service + CLAUDE.md)."""
    start = (start or Path(__file__)).resolve()
    if start.is_file():
        start = start.parent
    for d in [start, *start.parents]:
        if (d / "aria_service").is_dir() and (d / "CLAUDE.md").is_file():
            return d
    return None


def load_project_env(start: Path | None = None, filename: str = ".env") -> int:
    """Find the repo root and load its `.env`. Returns the number of keys set.

    Discovery mode — for callers that only know they are "somewhere in the
    repo". Callers holding an explicit path want `load_env_file`, which does not
    require the file to sit inside a checkout.
    """
    root = find_repo_root(start)
    if root is None:
        return 0
    n = load_env_file(root / filename)
    if n:
        return n
    # A linked git worktree is the SAME project, but `.env` is gitignored and so
    # exists only in the primary checkout. Without this, every agent working in
    # a worktree is pushed back to sourcing secrets by hand on each invocation —
    # the exact band-aid this module removes. Resolved from the worktree's `.git`
    # POINTER FILE rather than by shelling out to git: no subprocess, no PATH
    # dependency, and it works when git is absent.
    main = _primary_checkout(root)
    if main is not None and main != root:
        return load_env_file(main / filename)
    return 0


def _primary_checkout(worktree_root: Path) -> Path | None:
    """The main checkout backing a linked worktree, or None if this IS the main one.

    In a linked worktree `.git` is a file reading
    `gitdir: <main>/.git/worktrees/<name>`; the primary checkout is the parent
    of that `.git` directory.
    """
    marker = worktree_root / ".git"
    if not marker.is_file():
        return None
    try:
        line = marker.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not line.startswith("gitdir:"):
        return None
    gitdir = Path(line.partition(":")[2].strip())
    for parent in gitdir.parents:
        if parent.name == ".git":
            return parent.parent
    return None


def load_env_file(path: Path) -> int:
    """Load one `.env` file into os.environ. Returns the number of keys set.

    Never raises: a malformed `.env` must not take down a capture or a boot
    path. Never prints: this function handles secrets, so it reports a COUNT
    and the caller may say how many, never which or what.
    """
    if not path.is_file():
        return 0

    loaded = 0
    try:
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.lower().startswith("export "):
                line = line[7:].lstrip()
            key, _, val = line.partition("=")   # partition, not split: values contain '='
            key = key.strip()
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                val = val[1:-1]
            if key and key not in os.environ:
                os.environ[key] = val
                loaded += 1
    except Exception:      # noqa: BLE001 — a broken .env is not a reason to die
        return loaded
    return loaded


def require_env(names: list[str] | tuple[str, ...], *, purpose: str) -> None:
    """Raise unless every named variable is set to something non-blank.

    Call this BEFORE doing work that silently degrades without the credential.
    The message names the missing variables and what they were needed for, and
    never echoes the value of a variable that IS set.
    """
    missing = [n for n in names if not (os.getenv(n) or "").strip()]
    if missing:
        raise MissingCredentials(
            f"{', '.join(missing)} not set — required for {purpose}. "
            f"Set it in the repo .env or the shell environment. Refusing to "
            f"continue: without it this run cannot distinguish 'nothing found' "
            f"from 'never looked'."
        )

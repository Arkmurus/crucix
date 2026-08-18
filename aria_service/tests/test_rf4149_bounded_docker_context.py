"""R-F4149 capability guard for bounded, complete Docker build contexts."""
from __future__ import annotations

import re
from pathlib import Path


DOCKERFILES = (
    Path("aria_service/Dockerfile"),
    Path("Dockerfile.web"),
    Path("Dockerfile.wa"),
    Path("Dockerfile.trainer"),
)


def _copy_sources(dockerfile: Path) -> set[str]:
    sources: set[str] = set()
    for line in dockerfile.read_text(encoding="utf-8").splitlines():
        match = re.match(r"COPY\s+(?:--from=\S+\s+)?(\S+)", line)
        if match and not match.group(1).startswith("--from="):
            sources.add(match.group(1).rstrip("/"))
    return sources


def test_large_local_state_is_excluded_but_every_copy_source_remains_available() -> None:
    rules = set(Path(".dockerignore").read_text(encoding="utf-8").splitlines())
    assert {
        ".agent_bridge/",
        ".aria/",
        ".claude/",
        ".mypy_cache/",
        ".venv/",
        ".scratch/",
        ".pytest_cache/",
        "data/*",
        "!data/r_number_reservations.json",
    } <= rules

    copied = set().union(*(_copy_sources(path) for path in DOCKERFILES))
    assert "data/r_number_reservations.json" in copied
    assert not copied & {
        ".agent_bridge",
        ".aria",
        ".claude",
        ".mypy_cache",
        ".venv",
        ".scratch",
        ".pytest_cache",
        "data/eval_reports",
        "data/training",
    }

"""R-F4151 guards per-image Docker contexts against drift and bloat."""
from __future__ import annotations

import fnmatch
import shlex
from pathlib import Path

import pytest


CASES = (
    (Path("Dockerfile.web"), Path("Dockerfile.web.dockerignore")),
    (Path("Dockerfile.wa"), Path("Dockerfile.wa.dockerignore")),
)


def _copy_sources(dockerfile: Path) -> list[str]:
    sources = []
    for line in dockerfile.read_text(encoding="utf-8").splitlines():
        if not line.startswith("COPY "):
            continue
        tokens = shlex.split(line, posix=True)
        if not tokens or tokens[0] != "COPY" or any(t.startswith("--from=") for t in tokens):
            continue
        sources.extend(token.rstrip("/") for token in tokens[1:-1])
    return sources


def _is_included(source: str, rules: list[str]) -> bool:
    included = True
    for rule in rules:
        if not rule or rule.startswith("#"):
            continue
        negate = rule.startswith("!")
        pattern = rule[1:] if negate else rule
        pattern = pattern.rstrip("/")
        if pattern == "**" or fnmatch.fnmatch(source, pattern):
            included = negate
    return included


@pytest.mark.parametrize(("dockerfile", "ignore_file"), CASES)
def test_every_external_copy_source_is_present_in_its_image_context(
    dockerfile: Path, ignore_file: Path,
) -> None:
    rules = ignore_file.read_text(encoding="utf-8").splitlines()
    missing = [source for source in _copy_sources(dockerfile) if not _is_included(source, rules)]

    assert rules[1] == "**"
    assert missing == []


def test_web_and_wa_contexts_exclude_unrelated_heavy_trees() -> None:
    for _, ignore_file in CASES:
        rules = ignore_file.read_text(encoding="utf-8").splitlines()
        for unrelated in ("aria_service", "data", ".venv", ".claude", "public"):
            if ignore_file.name.startswith("Dockerfile.web") and unrelated == "public":
                continue
            assert not _is_included(unrelated, rules)

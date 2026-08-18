"""R-F4151 keeps one proven Docker context boundary authoritative."""
from __future__ import annotations

from pathlib import Path


def test_production_dockerfiles_use_the_root_context_boundary() -> None:
    """Per-Dockerfile filters must not replace the proven root exclusion pass."""
    assert not Path("Dockerfile.web.dockerignore").exists()
    assert not Path("Dockerfile.wa.dockerignore").exists()


def test_root_context_excludes_unreadable_and_generated_state() -> None:
    """The shared boundary excludes trees that made the Docker sender fail."""
    rules = set(Path(".dockerignore").read_text(encoding="utf-8").splitlines())
    assert {".scratch/", ".pytest_cache/", ".venv/", "data/*"} <= rules
    assert "!data/r_number_reservations.json" in rules

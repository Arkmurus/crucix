"""R-F1709 — Dockerfile build-cache + image-size invariants that keep deploys
from timing out.

Two root causes of slow/timing-out aria-intel deploys:
  1. torch was pulled transitively with NO pin → PyPI served the CUDA build
     (2.12.0+cu130, ~2 GB of unused nvidia-cuda-* libs) on a CPU-only machine.
  2. `playwright install --with-deps chromium` (~400 MB) sat AFTER `COPY .git/HEAD`
     (which changes every commit) → its layer cache was busted on EVERY deploy.

These are structural ORDERING invariants — for a Dockerfile the source IS the
artifact, so we guard the ordering directly (a real reorder regression would
re-introduce the timeouts).
"""
from __future__ import annotations

import re
from pathlib import Path

_DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"


def _text() -> str:
    return _DOCKERFILE.read_text(encoding="utf-8")


def _idx(pattern: str, text: str) -> int:
    # Anchor at line start (MULTILINE) so we match real instructions, not
    # comment lines that happen to mention the same string.
    m = re.search(pattern, text, re.MULTILINE)
    assert m, f"pattern not found in Dockerfile: {pattern!r}"
    return m.start()


def test_cpu_torch_pinned_before_requirements_install():
    """torch must be the CPU wheel, installed BEFORE the requirements install so
    the CUDA build is never pulled."""
    t = _text()
    assert "torch==2.12.0+cpu" in t, "torch must be pinned to the CPU wheel"
    assert "download.pytorch.org/whl/cpu" in t, "must use the CPU wheel index"
    cpu_torch = _idx(r"^RUN pip install[^\n]*torch==2\.12\.0\+cpu", t)
    reqs = _idx(r"^RUN pip install[^\n]*-r requirements\.txt", t)
    assert cpu_torch < reqs, (
        "CPU torch must install BEFORE `-r requirements.txt`, else the "
        "requirements resolve pulls the CUDA torch from PyPI first."
    )


def test_playwright_install_cached_before_app_and_git_copy():
    """The ~400 MB chromium install must come BEFORE the app + .git COPYs so a
    per-commit code/.git change doesn't bust its layer cache."""
    t = _text()
    pw = _idx(r"^RUN playwright install --with-deps chromium", t)
    app_copy = _idx(r"^COPY aria_service/ \./aria_service/", t)
    git_copy = _idx(r"^COPY \.git/HEAD", t)
    assert pw < app_copy, "playwright install must precede `COPY aria_service/`"
    assert pw < git_copy, "playwright install must precede `COPY .git/HEAD`"


def test_single_playwright_install():
    """Exactly one chromium install (the old post-COPY copy was removed)."""
    assert _text().count("RUN playwright install --with-deps chromium") == 1

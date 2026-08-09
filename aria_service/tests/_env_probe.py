"""R-F3795 — skip a test whose OPTIONAL dependency is absent; never fail as if code broke.

THE DEFECT THIS REPLACES, measured on 2026-08-09.

Six tests in the §16 baseline fail on this box for one reason: the dependency they
drive is not installed. `ModuleNotFoundError: No module named 'chromadb'`,
`No module named 'fitz'`, and "Tesseract OCR may not be installed" are not findings
about ARIA's code — they are findings about the machine.

CLAUDE.md §16 already records why: five `requirements.txt` entries publish no
win-arm64 wheel (`PyMuPDF`, `chromadb`, `opencv-python`, `sentence-transformers`,
`faster-whisper`). All are import-guarded, so the service boots without them and
those features are simply inert locally. The tests were not guarded to match.

WHY THIS MATTERS BEYOND TIDINESS. A failing test and a skipped test mean different
things, and the baseline cannot tell them apart. Six permanent environment failures
sit in the failure set looking exactly like regressions, and every future session
pays to re-triage them — which is the cost R-F3794 was written about, one layer
down. A test that cannot run should say so.

THE RISK THIS CREATES, AND THE GUARD FOR IT. `skipif` can hide a real regression: if
`chromadb` were dropped from `requirements.txt`, these tests would go quietly green-
by-skip instead of failing. So skipping is paired with
`test_rf3795_optional_deps_declared.py`, which asserts each of these is still
DECLARED and PINNED in `requirements.txt` and CANNOT itself be skipped. Absence from
the machine is tolerated; absence from the manifest is not.

Note the two are different questions, and only checking the first would be wrong:
`pytesseract` (the Python binding) is INSTALLED here while the `tesseract` BINARY it
shells out to is not. A module probe would report that dependency satisfied and the
test would fail anyway.
"""
from __future__ import annotations

import importlib.util
import shutil

import pytest

#: Optional dependencies this repo tolerates missing LOCALLY but requires in the
#: shipped Linux image. Keep in step with test_rf3795_optional_deps_declared.py —
#: that test reads this mapping, so a name added here is automatically required to
#: be declared in requirements.txt.
OPTIONAL_MODULES: dict[str, str] = {
    "chromadb": "chromadb",
    "fitz": "PyMuPDF",
    "cv2": "opencv-python",
    "sentence_transformers": "sentence-transformers",
    "faster_whisper": "faster-whisper",
    "pytesseract": "pytesseract",
}

#: External executables a test may shell out to. Not covered by a module probe.
OPTIONAL_BINARIES: dict[str, str] = {
    "tesseract": "pytesseract",
}


def module_present(name: str) -> bool:
    """True if `name` can be imported without importing it.

    `find_spec` avoids paying the import cost (and any side effects) at collection
    time — chromadb in particular is heavy and has a native component.
    """
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        # A partially installed package can raise rather than return None.
        return False


def binary_present(name: str) -> bool:
    """True if `name` is on PATH as an executable."""
    return shutil.which(name) is not None


def requires_module(name: str):
    """Skip marker for a test that cannot run without importable `name`."""
    dist = OPTIONAL_MODULES.get(name, name)
    return pytest.mark.skipif(
        not module_present(name),
        reason=(f"optional dependency {dist!r} (import {name!r}) is not installed — "
                f"§16: it publishes no win-arm64 wheel. This is an ENVIRONMENT gap, "
                f"not a code defect; it runs in the Linux image."),
    )


def requires_binary(name: str):
    """Skip marker for a test that shells out to the `name` executable."""
    dist = OPTIONAL_BINARIES.get(name, name)
    return pytest.mark.skipif(
        not binary_present(name),
        reason=(f"the {name!r} executable is not on PATH (python package {dist!r} may "
                f"still be installed — the binding and the binary are separate). "
                f"ENVIRONMENT gap, not a code defect."),
    )

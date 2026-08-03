"""R-F1908 (G1 vaccine) — the undefined-name (NameError) gene must never re-express.

This is the regression guard for the "weak gene" eradicated in R-F1908: names
used but never bound in scope (`logger` where the module logger is `_log`, a
stale loop-var `key`, a missing `import time`/`import asyncio`, a helper called
but defined nowhere, a wrong symbol name). Each is a latent `NameError` that
fires only when its branch executes — and most sat inside `except`/background
paths, so they bled silently. R-F1842 fixed ONE such site; the gene grew back in
24 more places. This gate runs the proper scope-aware linter (pyflakes F821)
across the live backend and FAILS if a single undefined name reappears.

If this test fails: a name is used but not defined/imported in its scope. The
failure message lists every site. Fix the binding (add the import, correct the
symbol, define the helper) — do NOT add the file to an allowlist.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
TARGET = REPO / "aria_service"


def _pyflakes_available() -> bool:
    try:
        import pyflakes  # noqa: F401
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _pyflakes_available(),
                    reason="pyflakes not installed (pip install -r requirements-dev.txt)")
def test_no_undefined_names_in_backend():
    """Zero F821 'undefined name' across aria_service/ (tests/ excluded — test
    fixtures legitimately reference names defined by conftest/parametrize)."""
    proc = subprocess.run(
        [sys.executable, "-m", "pyflakes", str(TARGET)],
        capture_output=True, text=True,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    offenders = []
    for ln in out.splitlines():
        if "undefined name" not in ln.lower():
            continue
        # Extract the path by anchoring on the trailing ":<line>:<col>:" that
        # pyflakes always emits. The previous `split(":", 1)[0]` broke on Windows:
        # "C:/Code/.../tests/x.py:90:34: ..." split at the DRIVE-LETTER colon and
        # yielded "C", so `"/tests/" in path` was never true and every test-file
        # hit leaked through as a false offender. Correct on Linux CI, wrong on a
        # Windows dev box — the gate has to behave identically on both.
        m = re.match(r"^(.*?):\d+:\d+:", ln)
        path = (m.group(1) if m else ln).replace("\\", "/")
        if "/tests/" in path:        # test fixtures legitimately reference conftest/parametrize names
            continue
        offenders.append(ln.strip())
    assert not offenders, (
        "R-F1908: %d undefined-name (latent NameError) site(s) reintroduced — "
        "the eradicated gene is re-expressing. Fix the binding, do not allowlist:\n%s"
        % (len(offenders), "\n".join(offenders))
    )


def test_routes_aria_uses_no_bare_logger():
    """The recurring instance of the gene: routes/aria.py's module logger is
    `_log`; a bare `logger.` call is undefined. Pin it precisely (zero-FP) so the
    exact symptom that recurred (R-F1842 → R-F1908) cannot return even if pyflakes
    is unavailable in some environment."""
    import re
    src = (TARGET / "routes" / "aria.py").read_text(encoding="utf-8", errors="ignore")
    # match a bare `logger.<method>(` CALL not preceded by a word char or dot
    # (so `_log.`/`.logger.` are fine). Requiring the `(` call-paren excludes the
    # R-F1842 explanatory comment that mentions `logger.debug` in backticks.
    bad = re.findall(r"(?<![\w.])logger\.(?:debug|info|warning|error|exception|critical)\s*\(", src)
    assert not bad, (
        "routes/aria.py: %d bare `logger.` call(s) — the module logger is `_log`; "
        "`logger` is undefined (latent NameError). Use `_log`." % len(bad)
    )

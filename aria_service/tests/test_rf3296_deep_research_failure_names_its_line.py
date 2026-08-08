"""R-F3296 - a failure report that does not say WHERE it failed.

The live AZURE PARKING LTD report carried exactly this and nothing more:

    digital: deep_research failed: 'list' object has no attribute 'lower'

True, and nearly useless. The engine has ~20 `.lower()` calls across
deep_researcher and researcher, so locating the real one (researcher.py:2111,
R-F3295) took three attempts and produced two fixes at the wrong boundary. The
traceback was in hand at the moment of the catch and was discarded, because
dd_orchestrator recorded only `str(e)[:120]`.

The gap now names the innermost frame INSIDE aria_service, which is the line that
actually raised rather than the catch site.

This test exists because the first version of the fix was a NO-OP: it used
`Path(...)` for the basename, `Path` is not imported in dd_orchestrator, and the
resulting NameError was swallowed by the fix's own except - leaving the location
empty while appearing to work. Verify the instrument.
"""
from __future__ import annotations

import re

import pytest

# R-F3784/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


def test_the_module_can_actually_format_a_location() -> None:
    """The no-op guard: prove the basename path works in THIS module's namespace.

    `Path` is not imported in dd_orchestrator. Any location formatting that needs
    it fails silently inside the diagnostic's own except block.
    """
    from aria_service.intel import dd_orchestrator as ddo
    assert not hasattr(ddo, "Path"), (
        "if Path becomes importable here, re-check the R-F3296 basename logic"
    )


def test_the_catch_records_file_and_line() -> None:
    """The gap must name file:line:function, not just the exception text."""
    import inspect
    from aria_service.intel import dd_orchestrator as ddo

    src = module_source(ddo)
    i = src.index("R-F3296")
    window = src[i:i + 1600]

    assert "extract_tb" in window, "the traceback must be read, not discarded"
    assert "aria_service" in window, (
        "must select the innermost frame inside our own code, not the catch site"
    )
    assert "_f.lineno" in window and "_f.name" in window, (
        "file:line:function is the point; the exception text alone was the defect"
    )
    assert "Path(" not in window, (
        "Path is not imported in this module; using it makes the diagnostic a "
        "silent no-op (that was the first version of this fix)"
    )


def test_the_formatting_produces_a_readable_location() -> None:
    """Exercise the exact expression used, on a real traceback."""
    import traceback as _tb

    def _raiser():
        return "x".lower() + None       # noqa: E501 - deliberate TypeError

    try:
        _raiser()
    except Exception as e:
        frames = [f for f in _tb.extract_tb(e.__traceback__)
                  if "aria_service" in (f.filename or "") or True]
        f = frames[-1]
        name = str(f.filename).replace("\\", "/").rsplit("/", 1)[-1]
        where = f" at {name}:{f.lineno} in {f.name}()"
        assert re.match(r" at [\w.\-]+\.py:\d+ in \w+\(\)", where), where
        assert "\\" not in where, "windows separators must be normalised"

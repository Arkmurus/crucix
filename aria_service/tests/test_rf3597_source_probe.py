"""R-F3597 — `inspect.getsource` misreads a file that changed after import.

THE MEASUREMENT THIS COMES FROM (2026-07-31). The first complete single-process
full-suite run reported 147 failures. Triage found 72 that passed when run alone, and
exactly half of those (36) assert on source TEXT.

They were not order-dependent. A peer agent committed four times during the 77-minute
run and touched `aria_service/routes/aria.py`. The suite had imported that module at
the start, so `inspect.getsource` was slicing the NEW file at the OLD line numbers and
returning a DIFFERENT function's body. Eight tests in one file failed on text that was
never theirs.

The failure is SILENT: the wrong slice is still valid Python, so the only symptom is an
assertion that stops matching. These tests prove the misread happens, and that
resolving BY NAME through the current AST does not.
"""
from __future__ import annotations

import inspect
import importlib.util
import sys

import pytest

from aria_service.tests._source_probe import (
    SourceProbeError,
    function_source,
    invalidate,
    module_source,
)

_ORIGINAL = '''
def target_function():
    """MARKER_ALPHA"""
    return 1


def other_function():
    """MARKER_BETA"""
    return 2
'''

# The same file after someone inserts lines ABOVE the target — exactly what a peer
# commit does. `target_function` now starts lower down; its old line range now covers
# a different part of the file.
_EDITED = '''
# a peer added a long explanatory comment here, as peers do
# line
# line
# line
# line
# line
# line


def target_function():
    """MARKER_ALPHA"""
    return 1


def other_function():
    """MARKER_BETA"""
    return 2
'''


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def edited_module(tmp_path):
    """Import a module, THEN edit its file — the real-world sequence."""
    p = tmp_path / "rf3597_subject.py"
    p.write_text(_ORIGINAL, encoding="utf-8")
    mod = _load(str(p), "rf3597_subject")
    p.write_text(_EDITED, encoding="utf-8")       # the concurrent commit
    invalidate()                                   # our own cache must not hide it
    yield mod, p
    sys.modules.pop("rf3597_subject", None)
    invalidate()


def test_inspect_getsource_misreads_a_file_edited_after_import(edited_module):
    """PROVE THE DEFECT. This is what happened to 36 tests in the live run."""
    mod, _ = edited_module
    import linecache
    linecache.checkcache()                         # even with a fresh cache
    try:
        src = inspect.getsource(mod.target_function)
    except (OSError, IndexError):
        return                                     # also a real failure mode; acceptable

    # MEASURED on Python 3.14.3: getsource starts at the STALE co_firstlineno (2),
    # which the edit turned into comment lines, and returns a MISALIGNED block:
    #     '# c\n# c\n...\n\ndef target_function():\n    """'
    # In this small fixture that block still happens to CONTAIN the target, which is
    # why the first version of this test asserted the wrong thing. The defect is the
    # MISALIGNMENT — in a 20,000-line module the same shift lands on other code
    # entirely, which is exactly what happened to routes/aria.py in the live run.
    assert not src.lstrip().startswith("def target_function"), (
        "getsource stayed aligned; this fixture no longer reproduces the shift"
    )


def test_the_probe_reads_the_right_function_after_an_edit(edited_module):
    """CAPABILITY: resolve by NAME, so a shifted file is simply found where it now is."""
    mod, _ = edited_module
    src = function_source(mod, "target_function")
    assert "MARKER_ALPHA" in src
    assert "MARKER_BETA" not in src, "the probe bled into the neighbouring function"


def test_the_probe_returns_the_whole_function_not_a_byte_window(edited_module):
    """Fixes the R-F3595 class too: an AST node knows where a function ENDS, so no
    fixed window can be pushed off the end by an added comment."""
    mod, _ = edited_module
    src = function_source(mod, "other_function")
    assert src.strip().startswith("def other_function")
    assert src.rstrip().endswith("return 2")


def test_a_missing_function_RAISES_rather_than_returning_empty():
    """A probe that returns "" turns every `assert "X" in src` into a failure that
    looks like a missing feature. 'The code does not say this' and 'I could not read
    the code' must stay distinguishable."""
    from aria_service.intel import dd_orchestrator as ddo
    with pytest.raises(SourceProbeError):
        function_source(ddo, "a_function_that_does_not_exist_anywhere")


def test_a_missing_file_raises_too(tmp_path):
    with pytest.raises(SourceProbeError):
        function_source(str(tmp_path / "nope.py"), "whatever")


def test_module_source_reads_the_current_text(edited_module):
    mod, _ = edited_module
    assert "a peer added a long explanatory comment" in module_source(mod)


def test_it_works_on_a_real_module():
    """Not just fixtures — the live path these tests actually use."""
    import aria_service.routes.aria as _aria

    src = function_source(_aria, "_execute_tool")
    assert src.lstrip().startswith(("def _execute_tool", "async def _execute_tool", "@")), src[:80]
    assert len(src) > 200


def test_a_decorated_function_includes_its_decorators():
    """Decorators carry contract (@fail_wire, @router.post) that tests assert on."""
    from aria_service.intel import dd_orchestrator as ddo

    src = function_source(ddo, "positive_register_findings")
    assert "@fail_wire" in src, "decorators were dropped from the resolved source"

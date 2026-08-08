"""R-F3787 — CAPABILITY: class_source reads a CLASS body.

The last shape in the §16 getsource backlog with no reader. `function_source` matches
FunctionDef/AsyncFunctionDef only, so `inspect.getsource(SomeClass)` could not be
converted — `function_source(mod, "SomeClass")` raises "no function named". The
converter refused those files, which was right, and left them stranded.

Run: python -m pytest aria_service/tests/test_rf3787_class_source.py -v
"""
from __future__ import annotations

import pytest

from ._source_probe import SourceProbeError, class_source, function_source, repo_path


def test_a_class_body_is_returned():
    """THE HEADLINE."""
    src = class_source("aria_service.intel.dd_vault", "DDVault")
    assert src.lstrip().startswith(("class DDVault", "@"))
    assert "def get_case" in src, "the class body must include its methods"


def test_it_works_from_a_module_object_and_a_path_too():
    from aria_service.intel import dd_vault
    a = class_source(dd_vault, "DDVault")
    b = class_source(repo_path("aria_service/intel/dd_vault.py"), "DDVault")
    c = class_source("aria_service.intel.dd_vault", "DDVault")
    assert a == b == c, "all three target forms must resolve identically"


def test_decorators_are_included():
    """Without this, an assertion about @dataclass fails on a correct class."""
    src = class_source(repo_path("aria_service/tests/test_rf3787_class_source.py"),
                       "_Decorated")
    assert src.lstrip().startswith("@"), f"decorator missing: {src[:40]!r}"


def test_a_missing_class_reports_a_RENAME_not_an_empty_read():
    """Returning "" would turn every `assert "X" in src` into a fake missing feature."""
    with pytest.raises(SourceProbeError) as ei:
        class_source("aria_service.intel.dd_vault", "NoSuchClassRF3787")
    msg = str(ei.value)
    assert "no class named" in msg
    assert "real change, not a read failure" in msg


def test_a_function_is_not_returned_as_a_class():
    """class_source must not fall back to a function of the same name."""
    with pytest.raises(SourceProbeError):
        class_source(repo_path("aria_service/tests/_source_probe.py"), "module_source")


def test_a_class_is_not_returned_by_function_source():
    """The mirror: this is exactly why the class shape needed its own reader."""
    with pytest.raises(SourceProbeError):
        function_source("aria_service.intel.dd_vault", "DDVault")


def test_a_nested_class_is_NOT_searched():
    """Ambiguity is refused, not silently resolved — the whole point of this module."""
    with pytest.raises(SourceProbeError):
        class_source(repo_path("aria_service/tests/test_rf3787_class_source.py"),
                     "_Nested")


def _noop(c):
    return c


@_noop
class _Decorated:
    pass


class _Outer:
    class _Nested:
        pass

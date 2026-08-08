"""R-F3771 — CAPABILITY: _source_probe must resolve a CLASS, and scope to its body.

`function_source` accepted a module or a path. A CLASS fell through — `getattr(cls,
"__file__")` is None, so it became `str(cls)` and raised "source file not found".
That blocked roughly 120 of the ~196 files in the §16 getsource backlog, every one
written as `inspect.getsource(SomeClass.method)`. The majority of the migration was
stuck behind a two-line gap.

Resolving a class also makes the lookup STRICTER, which is the larger gain. The
module-level search matches a method by NAME anywhere in the file, so
`function_source(mod, "start")` will return whichever class defines `start` first.
Given the class, the method is found inside THAT class only — and a wrong slice
returned silently is precisely what this module exists to prevent.

Run: python -m pytest aria_service/tests/test_rf3771_source_probe_class_target.py -v
"""
from __future__ import annotations

import pytest

from ._source_probe import SourceProbeError, function_source


def test_a_class_target_resolves_its_method():
    """THE HEADLINE: pass the class, get that class's method."""
    from aria_service.intel.dd_vault import DDVault
    src = function_source(DDVault, "get_case")
    assert "def get_case" in src
    assert "canonical_entity_id" in src


def test_a_class_target_scopes_to_that_class_only():
    """The strictness gain: a name reused in another class must not be returned.

    `AttrProbe.target` and `Decoy.target` both exist below. A module-level search
    would return whichever appears first; a class-scoped search cannot.
    """
    a = function_source(_AttrProbe, "target")
    b = function_source(_Decoy, "target")
    assert "MARKER_A" in a and "MARKER_B" not in a
    assert "MARKER_B" in b and "MARKER_A" not in b


def test_a_missing_method_names_the_class_it_searched():
    """A read failure must be distinguishable from a real rename."""
    with pytest.raises(SourceProbeError) as ei:
        function_source(_AttrProbe, "no_such_method")
    msg = str(ei.value)
    assert "_AttrProbe" in msg, f"the error does not name the class searched: {msg}"
    assert "renamed or removed" in msg


def test_a_class_scoped_lookup_does_not_fall_back_to_the_file():
    """Falling back would silently restore the ambiguity the class argument removes.

    `module_level_target` exists at module scope with the same name pattern; asking
    _AttrProbe for it must FAIL rather than return the module-level function.
    """
    with pytest.raises(SourceProbeError):
        function_source(_AttrProbe, "module_level_target")


def test_module_and_path_targets_still_work():
    """The pre-existing contract must not regress."""
    from aria_service.intel import operating_modes as om
    src = function_source(om, "get_mode")
    assert "def get_mode" in src

    from ._source_probe import repo_path
    src2 = function_source(repo_path("aria_service/intel/operating_modes.py"), "get_mode")
    assert src2 == src, "module and path targets must resolve identically"


def test_decorators_are_included_for_a_method():
    """The slice must start at the first decorator, or a @fail_wire assertion breaks."""
    src = function_source(_AttrProbe, "decorated")
    assert src.lstrip().startswith("@"), f"decorator missing from slice: {src[:60]!r}"


def _noop(fn):
    return fn


def module_level_target():
    return "MODULE_LEVEL"


class _AttrProbe:
    def target(self):
        return "MARKER_A"

    @_noop
    def decorated(self):
        return "DECORATED"


class _Decoy:
    def target(self):
        return "MARKER_B"

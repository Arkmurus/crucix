"""R-F3778 — CAPABILITY: a DOTTED MODULE NAME resolves, so bare-Name calls can migrate.

160 of the ~231 getsource calls left in the §16 backlog are `inspect.getsource(foo)`
on a bare name — ~70%, and the converter could not touch any of them. A bare name
carries no module, and for the common `from a.b.c import foo` there is no module
BOUND in the file: `a.b.c` was never named, only `foo` was. There was nothing to pass.

A dotted string is exactly as specific as the import that introduced the name, and
needs no new import line in the test — which is what keeps the conversion mechanical.

Run: python -m pytest aria_service/tests/test_rf3778_source_probe_module_name.py -v
"""
from __future__ import annotations

import pytest

from ._source_probe import SourceProbeError, function_source, repo_path


def test_a_dotted_module_name_resolves():
    """THE HEADLINE: the shape the converter needs."""
    src = function_source("aria_service.intel.operating_modes", "get_mode")
    assert "def get_mode" in src


def test_it_agrees_with_the_module_object():
    """A second route to the same text must not be a second answer."""
    from aria_service.intel import operating_modes as om
    assert function_source("aria_service.intel.operating_modes", "get_mode") == \
           function_source(om, "get_mode")


def test_it_agrees_with_the_path_form():
    assert function_source("aria_service.intel.operating_modes", "get_mode") == \
           function_source(repo_path("aria_service/intel/operating_modes.py"), "get_mode")


def test_a_path_is_still_a_path_not_a_module_name():
    """The pre-existing contract: a string with a separator or .py stays a PATH.

    `aria_service/intel/operating_modes.py` contains dots. If the module branch
    grabbed it, every existing path-based caller would break.
    """
    src = function_source("aria_service/intel/operating_modes.py", "get_mode")
    assert "def get_mode" in src


def test_a_bare_relative_py_filename_is_treated_as_a_path():
    """`foo.py` has a dot and no separator — it must NOT be read as a module name."""
    with pytest.raises(SourceProbeError) as ei:
        function_source("definitely_not_here.py", "x")
    assert "source file not found" in str(ei.value)


def test_an_unimportable_module_is_a_REAL_failure_not_a_read_failure():
    """A module that will not import must say so, loudly, and never return "".

    Returning empty text would turn `assert "X" in src` into a missing-feature
    report — the exact confusion this whole module exists to prevent.
    """
    with pytest.raises(SourceProbeError) as ei:
        function_source("aria_service.no_such_module_rf3778", "whatever")
    msg = str(ei.value)
    assert "cannot import" in msg
    assert "real failure, not a" in msg


def test_a_missing_function_in_a_named_module_still_reports_a_rename():
    with pytest.raises(SourceProbeError) as ei:
        function_source("aria_service.intel.operating_modes", "not_a_real_function")
    assert "renamed or removed" in str(ei.value)


def test_a_single_word_string_is_not_mistaken_for_a_module():
    """No dot -> not a dotted name -> stays a path, and fails as a path."""
    with pytest.raises(SourceProbeError) as ei:
        function_source("operating_modes", "get_mode")
    assert "source file not found" in str(ei.value)


def test_class_and_module_targets_did_not_regress():
    """R-F3771 and the original contract must both survive."""
    from aria_service.intel.dd_vault import DDVault
    assert "def get_case" in function_source(DDVault, "get_case")
    from aria_service.intel import operating_modes as om
    assert "def get_mode" in function_source(om, "get_mode")

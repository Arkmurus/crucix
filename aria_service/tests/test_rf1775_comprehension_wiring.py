"""R-F1775: Wire comprehension.analyse() into the chat pipeline.

Tests:
1. _format_history_user_prompt includes the comprehension prefix
2. The comprehension prefix contains "UNDERSTOOD AS" for non-trivial messages
3. The comprehension prefix is empty for trivial messages
4. The comprehension prefix falls back to static directive on error
"""
from __future__ import annotations

import inspect

import pytest

from aria_service import aria_engine

# R-F3781/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


def _get_source() -> str:
    """Return the full source of aria_engine.py for static analysis."""
    return module_source(aria_engine)


def test_rf1775_comprehension_import_present():
    """The comprehension module must be imported in aria_engine.py."""
    src = _get_source()
    assert "from .intel import comprehension as _comprehension" in src, (
        "R-F1775: comprehension module not imported in aria_engine.py"
    )


def test_rf1775_comprehension_analyse_called():
    """_format_history_user_prompt must call comprehension.analyse()."""
    src = _get_source()
    assert "_comprehension.analyse(message)" in src, (
        "R-F1775: comprehension.analyse() not called in _format_history_user_prompt"
    )


def test_rf1775_comprehension_build_prefix_called():
    """_format_history_user_prompt must call comprehension.build_prefix()."""
    src = _get_source()
    assert "_comprehension.build_prefix(_ca)" in src, (
        "R-F1775: comprehension.build_prefix() not called in _format_history_user_prompt"
    )


def test_rf1775_fallback_present():
    """The fallback static directive must still be present."""
    src = _get_source()
    assert "COMPREHENSION DIRECTIVE" in src, (
        "R-F1775: fallback COMPREHENSION DIRECTIVE missing"
    )


def test_rf1775_understood_as_in_build_prefix():
    """comprehension.build_prefix() must include 'UNDERSTOOD AS' for non-trivial messages."""
    from aria_service.intel.comprehension import analyse, build_prefix

    # A non-trivial question about UAE law
    analysis = analyse("Under the UAE law, once a contract is cancelled and promising to return payments several times and you don't what can be done?")
    prefix = build_prefix(analysis)

    assert not analysis.is_trivial, "UAE law question should not be trivial"
    assert "UNDERSTOOD AS" in prefix, (
        "R-F1775: build_prefix() must include 'UNDERSTOOD AS' for non-trivial messages"
    )
    assert "COMPREHENSION PASS" in prefix, (
        "R-F1775: build_prefix() must include 'COMPREHENSION PASS' marker"
    )


def test_rf1775_trivial_message_empty_prefix():
    """comprehension.build_prefix() must return empty string for trivial messages."""
    from aria_service.intel.comprehension import analyse, build_prefix

    analysis = analyse("hello")
    prefix = build_prefix(analysis)

    assert analysis.is_trivial, "Greeting should be trivial"
    assert prefix == "", "Trivial messages should get empty prefix"


def test_rf1775_complexity_detection():
    """UAE law question should be detected as COMPLEX or CRITICAL."""
    from aria_service.intel.comprehension import analyse

    analysis = analyse("Under the UAE law, once a contract is cancelled and promising to return payments several times and you don't what can be done?")

    assert analysis.complexity.value in ("COMPLEX", "CRITICAL"), (
        f"UAE law question should be COMPLEX or CRITICAL, got {analysis.complexity.value}"
    )


def test_rf1775_confidence_clear():
    """A well-formed question should have CLEAR or PROBABLE confidence."""
    from aria_service.intel.comprehension import analyse

    analysis = analyse("Under the UAE law, once a contract is cancelled and promising to return payments several times and you don't what can be done?")

    assert analysis.confidence.value in ("CLEAR", "PROBABLE"), (
        f"UAE law question should have CLEAR or PROBABLE confidence, got {analysis.confidence.value}"
    )


def test_rf1775_should_proceed():
    """A clear UAE law question should proceed without clarification."""
    from aria_service.intel.comprehension import analyse

    analysis = analyse("Under the UAE law, once a contract is cancelled and promising to return payments several times and you don't what can be done?")

    assert analysis.should_proceed, "Clear UAE law question should proceed"
    assert not analysis.need_clarification, "Clear UAE law question should not need clarification"

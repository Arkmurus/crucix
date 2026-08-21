"""R-F4224 / C-204: the DD grading consumer and its builder must agree on keys.

WHAT HAPPENED. `_quality_penalties(metrics)` reads its inputs with hard
subscripts — `metrics["claim_grounded_rate"]`, `metrics["citations_checked"]`,
and a dozen more. Its only production feeder is `_quality_metrics()`, so in a
real DD run the shapes match. But two test files hand-rolled their own metrics
dicts (17 keys and 14 keys against a builder that emits 27). When
`claim_grounded_rate` was added to the builder, `_quality_penalties` began
raising `KeyError` inside those fixtures and **18 tests went permanently red** —
12 in test_rf3183_memory_only_tiering, 6 in test_rf3132_citation_sample.

A permanently-red test carries no information (CLAUDE.md §16): it can never go
green, so it can never signal a regression. Eighteen of them sat across the DD
evidence-grading logic — the code that decides whether a report is Grade A —
saying nothing. Both fixtures now derive from `_quality_metrics({})`, so a new
key appears in them automatically.

WHY THIS FILE EXISTS ANYWAY. Deriving the fixtures fixes today's instance; this
closes the class, and it guards PRODUCTION rather than test hygiene. If
`_quality_penalties` is ever edited to read a key `_quality_metrics` does not
emit, the failure is not a red test — it is a `KeyError` raised inside
`_dd_quality_assessment` on a live report, i.e. a DD that cannot be graded.
This asserts the two halves agree, and it fails with the missing key named.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

from aria_service.intel import dd_schema
from aria_service.intel.dd_schema import _quality_metrics, _quality_penalties


def _subscripted_keys(func_name: str) -> set[str]:
    """Every `metrics["literal"]` key the named function reads, via AST.

    Resolved by NAME from the current file (R-F3597: never by line number).
    """
    path = inspect.getsourcefile(dd_schema)
    tree = ast.parse(pathlib.Path(path).read_text(encoding="utf-8", errors="replace"))
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name == func_name), None)
    assert fn is not None, f"{func_name} not found in dd_schema"
    keys: set[str] = set()
    for node in ast.walk(fn):
        if (isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Name)
                and node.value.id == "metrics"
                and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, str)):
            keys.add(node.slice.value)
    return keys


def test_the_builder_emits_every_key_the_grader_reads():
    """The contract, stated once. A gap here is a live KeyError, not a red test."""
    produced = set(_quality_metrics({}))
    consumed = _subscripted_keys("_quality_penalties")
    missing = sorted(consumed - produced)
    assert not missing, (
        "_quality_penalties reads these with a hard subscript but _quality_metrics "
        f"does not emit them: {missing}. In production that is a KeyError inside "
        "_dd_quality_assessment — a DD report that cannot be graded. Either emit "
        "the key in the builder or read it with .get() and a stated default."
    )


def test_the_probe_can_actually_see_a_key():
    """A guard that cannot fire is not a guard (R-F3858)."""
    consumed = _subscripted_keys("_quality_penalties")
    assert "claim_grounded_rate" in consumed, (
        "the AST probe no longer finds the very key that caused C-204 — the "
        "detection shape has stopped matching the code it reads")
    assert len(consumed) >= 8, consumed


def test_grading_survives_a_completely_empty_report():
    """The DD grader must never raise on a sparse report — it must GRADE it low."""
    metrics = _quality_metrics({})
    penalties = _quality_penalties(metrics)          # must not raise
    assert isinstance(penalties, list)
    assert penalties, "an empty report must attract penalties, not a clean score"


@pytest.mark.parametrize("fixture_module", [
    "aria_service.tests.test_rf3183_memory_only_tiering",
    "aria_service.tests.test_rf3132_citation_sample",
])
def test_fixtures_are_derived_from_the_builder_not_hand_rolled(fixture_module):
    """The fixtures that went blind must stay derived, or they go blind again."""
    import importlib
    mod = importlib.import_module(fixture_module)
    produced = set(_quality_metrics({}))
    got = set(mod._metrics())
    missing = sorted(produced - got)
    assert not missing, (
        f"{fixture_module}._metrics() is missing {missing} — it has been "
        "hand-rolled again. Start from _quality_metrics({}) so a new builder key "
        "cannot blind these tests (C-204)."
    )

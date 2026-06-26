"""R-F1962 — reproduce-symptom genuineness gate (capability test).

The gameable hole: reproduce_symptom accepted ANY non-zero pytest exit as
"symptom reproduced" as long as the output mentioned the module name. But pytest
prints the module/file name on collection/import errors too (exit 2/5), so an
LLM-written test that can't even import — or any unrelated failure — was accepted
as a valid reproduction, letting a fix proceed on a fake repro. The gate now
requires a GENUINE test FAILURE (pytest exit 1) with no collection/import marker.
"""
from pathlib import Path

from aria_service.autonomous import gap_detector as gd


def test_genuine_assertion_failure_accepted():
    assert gd._is_symptom_failure(1, "test_x.py::test_foo failed: assert 1 == 2") is True


def test_collection_error_rejected_even_when_it_mentions_the_module():
    # THE gameable case: exit 2 (collection error) whose output contains the
    # module name — the old clue check accepted this; the gate must reject it.
    out = ("errors during collection\n"
           "error collecting aria_service/intel/foo.py\n"
           "importerror while importing test module").lower()
    assert gd._is_symptom_failure(2, out) is False


def test_import_error_at_exit1_rejected():
    assert gd._is_symptom_failure(1, "importerror: no module named foo") is False
    assert gd._is_symptom_failure(1, "modulenotfounderror: no module named bar") is False


def test_no_tests_and_usage_and_pass_all_rejected():
    assert gd._is_symptom_failure(5, "no tests ran in 0.01s") is False   # exit 5
    assert gd._is_symptom_failure(4, "usage: pytest [options]") is False  # exit 4
    assert gd._is_symptom_failure(0, "1 passed in 0.1s") is False         # passed


def test_gate_is_wired_into_BOTH_reproduce_paths():
    """Regression guard: the gate must guard both the auto-written and the
    existing-test reproduction paths, not just be defined."""
    src = Path(gd.__file__).read_text(encoding="utf-8")
    # 1 definition + 2 call sites (auto-written path + existing-test path)
    assert src.count("_is_symptom_failure(") >= 3

"""R-F1677 — SL-CAI revision-quality gate. The critique collector previously
trusted the LLM's self-revision blindly; a "fix" that didn't actually fix the
violation would poison the training set. A pair is training-grade ONLY when the
revision is VERIFIED clean by re-critique (rejection sampling), or the original
was already clean. This gate is the keystone — unit-tested here.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from aria_service.intel.critique_collector import _is_training_grade


def test_clean_original_is_training_grade():
    # no violations -> already-good behaviour, valid positive target
    assert _is_training_grade([], "", None) is True


def test_verified_clean_revision_is_training_grade():
    # original violated, revision produced, re-critique came back CLEAN -> fix worked
    assert _is_training_grade([14, 20], "A properly revised, grounded answer text.", []) is True


def test_revision_still_violating_is_rejected():
    # original violated, revision produced, but re-critique STILL finds violations
    assert _is_training_grade([14], "Still fabricates specifics...", [14]) is False


def test_recritique_not_run_is_rejected():
    # claimed a violation + a revision, but re-critique didn't run/failed -> not verified
    assert _is_training_grade([14], "Some revision text here, long enough.", None) is False


def test_missing_or_short_revision_is_rejected():
    # claimed a violation but produced no real revision
    assert _is_training_grade([14], "", []) is False
    assert _is_training_grade([14], "too short", []) is False


def test_clause_coverage_expanded_for_high_stakes_domains():
    # R-F1677 also widened clause coverage to compliance/sanctions/doc-review
    from aria_service.intel.critique_collector import _CLAUSE_SUMMARIES as C
    for clause in ("Clause 1", "Clause 3", "Clause 12", "Clause 15", "Clause 23"):
        assert clause in C, f"{clause} missing from critique clause coverage"

"""
R-F1319 capability tests — writers/ and metacognitive/ package wiring.

Tests:
  1. All writers modules compile and have wire_success
  2. All metacognitive modules compile and have wire_success
"""
from __future__ import annotations

import ast
import os


WRITERS_MODULES = [
    "anti_corruption_law.py",
    "assessment_writer.py",
    "procurement_paper_writer.py",
    "tech_spec_and_portuguese_writer.py",
    "writer_orchestrator.py",
]

METACOGNITIVE_MODULES = [
    "calibration.py",
    "coding_lessons.py",
    "consciousness.py",
    "cycle.py",
    "domains.py",
    "gaps.py",
    "identity.py",
    "self_improvement_codegen.py",
]


def test_writers_compile():
    """Every writers module must compile without SyntaxError."""
    for f in WRITERS_MODULES:
        path = os.path.join("aria_service", "writers", f)
        with open(path, "r", encoding="utf-8") as fh:
            source = fh.read()
        try:
            ast.parse(source)
        except SyntaxError as e:
            raise AssertionError(f"SyntaxError in {f}:{e.lineno}: {e.msg}")


def test_writers_have_wire_success():
    """Every writers module must have wire_success at module level."""
    for f in WRITERS_MODULES:
        path = os.path.join("aria_service", "writers", f)
        with open(path, "r", encoding="utf-8") as fh:
            source = fh.read()
        assert "wire_success" in source, f"{f} missing wire_success"
        assert "R-F1319" in source, f"{f} missing R-F1319 reference"


def test_metacognitive_compile():
    """Every metacognitive module must compile without SyntaxError."""
    for f in METACOGNITIVE_MODULES:
        path = os.path.join("aria_service", "metacognitive", f)
        with open(path, "r", encoding="utf-8") as fh:
            source = fh.read()
        try:
            ast.parse(source)
        except SyntaxError as e:
            raise AssertionError(f"SyntaxError in {f}:{e.lineno}: {e.msg}")


def test_metacognitive_have_wire_success():
    """Every metacognitive module must have wire_success at module level."""
    for f in METACOGNITIVE_MODULES:
        path = os.path.join("aria_service", "metacognitive", f)
        with open(path, "r", encoding="utf-8") as fh:
            source = fh.read()
        assert "wire_success" in source, f"{f} missing wire_success"
        assert "R-F1319" in source, f"{f} missing R-F1319 reference"

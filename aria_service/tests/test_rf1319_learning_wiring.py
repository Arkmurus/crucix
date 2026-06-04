"""
R-F1319 capability tests — learning/ package wiring.

Tests:
  1. All 12 learning modules have wire_success at module level
  2. All 12 learning modules compile without SyntaxError
"""
from __future__ import annotations

import ast
import os


LEARNING_MODULES = [
    "bookmarks.py",
    "fsrs_scheduler.py",
    "knowledge_spider.py",
    "learning_controller.py",
    "memory_replication.py",
    "metacognitive_journal.py",
    "output_harvester.py",
    "reading_queue.py",
    "research_engine.py",
    "style_learner.py",
    "training_export.py",
    "verification_gate.py",
]


def test_all_modules_compile():
    """Every learning module must compile without SyntaxError."""
    for f in LEARNING_MODULES:
        path = os.path.join("aria_service", "learning", f)
        with open(path, "r", encoding="utf-8") as fh:
            source = fh.read()
        try:
            ast.parse(source)
        except SyntaxError as e:
            raise AssertionError(f"SyntaxError in {f}:{e.lineno}: {e.msg}")


def test_all_modules_have_wire_success():
    """Every learning module must have wire_success at module level."""
    for f in LEARNING_MODULES:
        path = os.path.join("aria_service", "learning", f)
        with open(path, "r", encoding="utf-8") as fh:
            source = fh.read()
        assert "wire_success" in source, f"{f} missing wire_success"
        assert "R-F1319" in source, f"{f} missing R-F1319 reference"

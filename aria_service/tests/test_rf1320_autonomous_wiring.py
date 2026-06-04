"""
R-F1320 capability tests — autonomous/ package wiring.

Tests:
  1. All autonomous modules compile without SyntaxError
  2. All autonomous modules have wire_success at module level
"""
from __future__ import annotations

import ast
import os


AUTONOMOUS_MODULES = [
    "autonomous_deploy.py",
    "claude_reviewer.py",
    "codebase_reader.py",
    "coder_entrypoint.py",
    "constitutional_validator.py",
    "cost_monitor.py",
    "delivery.py",
    "dryrun_history.py",
    "fly_deployer.py",
    "machines_deployer.py",
    "r_counter.py",
    "review_ticket.py",
    "tasks.py",
    "test_runner.py",
]


def test_all_modules_compile():
    """Every autonomous module must compile without SyntaxError."""
    for f in AUTONOMOUS_MODULES:
        path = os.path.join("aria_service", "autonomous", f)
        with open(path, "r", encoding="utf-8") as fh:
            source = fh.read()
        try:
            ast.parse(source)
        except SyntaxError as e:
            raise AssertionError(f"SyntaxError in {f}:{e.lineno}: {e.msg}")


def test_all_modules_have_wire_success():
    """Every autonomous module must have wire_success at module level."""
    for f in AUTONOMOUS_MODULES:
        path = os.path.join("aria_service", "autonomous", f)
        with open(path, "r", encoding="utf-8") as fh:
            source = fh.read()
        assert "wire_success" in source, f"{f} missing wire_success"
        assert "R-F1320" in source, f"{f} missing R-F1320 reference"

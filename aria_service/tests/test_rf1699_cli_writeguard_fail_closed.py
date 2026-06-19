"""R-F1699 — the ARIA Coder CLI write guard must enforce the constitutional
validator in self-mode, fail-closed.

Before R-F1699 the CLI WriteGuard ran ONLY a truncation guard
(`constitution_active` hardcoded False, R-F1191). In self-mode (editing the
crucix repo) that left an R-F995-class bypass: the CLI could overwrite
honesty-critical files (constitutional_validator.py, safety.py, sanctions.py,
…) and deploy them with no PROTECTED_FILES / weakening-pattern / AST check —
the exact path by which ARIA once gutted her own validator. The server
self-coder runs this validator (R-F1287); the CLI must match it.

These capability tests drive the REAL WriteGuard.review() in self-mode.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from aria_cli.safety import WriteGuard

# resolver = identity, so target paths passed in are already repo-relative
_IDENTITY = lambda p: p


def _self_guard():
    return WriteGuard(self_mode=True, repo_relative_resolver=_IDENTITY)


def test_self_mode_blocks_write_to_protected_file():
    """ARIA's own honesty-critical files cannot be overwritten via the CLI."""
    g = _self_guard()
    v = g.review("aria_service/autonomous/safety.py", "x = 1\n", "x = 2\n")
    assert v.allowed is False
    assert "PROTECTED_FILES" in v.reason or "BLOCKED" in v.reason


def test_self_mode_blocks_validator_weakening():
    """A write that neuters the constitution (emptying PROTECTED_FILES) is blocked."""
    g = _self_guard()
    v = g.review(
        "aria_service/intel/some_module.py",
        "",
        "PROTECTED_FILES = frozenset()\n",
    )
    assert v.allowed is False


def test_self_mode_allows_benign_nonprotected_write():
    g = _self_guard()
    v = g.review(
        "aria_service/intel/some_new_helper.py",
        "",
        "def add(a, b):\n    return a + b\n",
    )
    assert v.allowed is True


def test_general_mode_does_not_run_constitution():
    """General-mode (arbitrary user projects) keeps truncation-guard-only — the
    crucix constitution must NOT block edits to someone else's codebase, even a
    path that happens to collide with a crucix PROTECTED_FILES name."""
    g = WriteGuard(self_mode=False, repo_relative_resolver=_IDENTITY)
    v = g.review("aria_service/autonomous/safety.py", "x = 1\n", "x = 2\n")
    assert v.allowed is True


def test_self_mode_fails_closed_when_validator_errors():
    """If the validator can't run in self-mode, the write is REFUSED (never
    silently allowed) — an unvalidated write to the constitutional repo is the
    hole."""
    g = _self_guard()
    with patch(
        "aria_service.autonomous.constitutional_validator.ConstitutionalValidator.validate",
        side_effect=RuntimeError("validator import/exec failure"),
    ):
        v = g.review("aria_service/intel/x.py", "", "def f():\n    return 1\n")
    assert v.allowed is False
    assert "fail-closed" in v.reason.lower()


def test_truncation_guard_still_fires_first_in_self_mode():
    """Layer 1 (truncation) still applies — a >50% shrink of a non-trivial file
    is blocked before the constitutional layer."""
    g = _self_guard()
    old = "\n".join(f"line {i}" for i in range(60)) + "\n"
    v = g.review("aria_service/intel/x.py", old, "line 0\n")
    assert v.allowed is False
    assert "truncation" in v.reason.lower()

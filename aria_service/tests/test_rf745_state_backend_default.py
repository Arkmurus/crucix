"""Capability test for R-F745 — ARIA_STATE_BACKEND default flipped to sqlite.

User-visible promise: a fly deploy (or local dev / fresh CI runner) that
forgets to set ARIA_STATE_BACKEND should default to SQLite — not the
cancelled Upstash subscription. Pre-R-F745 the default was "upstash",
which made Phase A gate #5 (env vars set) a false closure per CLAUDE.md:97.

Strategy: redis_store._BACKEND is captured at import time from
os.environ.get("ARIA_STATE_BACKEND"). To test the default-without-env
behaviour we strip the env var, drop the cached module, and re-import.
"""
from __future__ import annotations

import importlib
import os
import sys

import pytest


def _fresh_import_with_env(env: dict[str, str | None]) -> object:
    """Re-import aria_service.intel.redis_store under a curated env.

    `env` may contain `None` values — those mean "ensure unset".
    """
    saved: dict[str, str | None] = {}
    try:
        for k, v in env.items():
            saved[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

        sys.modules.pop("aria_service.intel.redis_store", None)
        mod = importlib.import_module("aria_service.intel.redis_store")
        return mod
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        # Leave a clean module slate for the next test.
        sys.modules.pop("aria_service.intel.redis_store", None)


def test_rf745_default_backend_is_sqlite_when_env_unset():
    """The whole point of R-F745: no env var ⇒ default = sqlite, not upstash."""
    mod = _fresh_import_with_env({"ARIA_STATE_BACKEND": None})
    assert mod._BACKEND == "sqlite", (
        f"R-F745 regression: default ARIA_STATE_BACKEND must be 'sqlite' "
        f"(was 'upstash' until R-F745). Found '{mod._BACKEND}'."
    )
    assert mod._use_sqlite() is True
    assert mod._use_memory() is False


def test_rf745_explicit_upstash_still_honoured():
    """Operators can still pin to 'upstash' (legacy backend)."""
    mod = _fresh_import_with_env({"ARIA_STATE_BACKEND": "upstash"})
    assert mod._BACKEND == "upstash"
    assert mod._use_sqlite() is False
    assert mod._use_memory() is False


def test_rf745_explicit_memory_still_honoured():
    """The 'memory' break-glass backend still selects in-process dict."""
    mod = _fresh_import_with_env({"ARIA_STATE_BACKEND": "memory"})
    assert mod._BACKEND == "memory"
    assert mod._use_sqlite() is False
    assert mod._use_memory() is True


def test_rf745_whitespace_and_case_normalised():
    """The env var is stripped + lowercased — '  SQLite ' is still sqlite."""
    mod = _fresh_import_with_env({"ARIA_STATE_BACKEND": "  SQLite "})
    assert mod._BACKEND == "sqlite"
    assert mod._use_sqlite() is True


def test_rf745_docstring_reflects_new_default():
    """Audit-trail: the module docstring must name R-F745 + sqlite default
    so anyone grepping for 'upstash default' lands on the migration note."""
    import aria_service.intel.redis_store as rs
    doc = rs.__doc__ or ""
    assert "R-F745" in doc, "Module docstring must reference R-F745 (default flip)."
    assert "default" in doc.lower() and "sqlite" in doc.lower(), (
        "Module docstring must declare sqlite as the default."
    )

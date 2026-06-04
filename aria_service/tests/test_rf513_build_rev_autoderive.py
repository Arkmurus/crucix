"""R-F513 — ARIA_BUILD_REV auto-derived from Dockerfile build-args.

Pre-R-F513 main.py:53 was a hand-edited string. On 2026-05-14, 27
commits shipped (R-F454..F510) with zero bumps; /health/live kept
reporting "R-F242+F243+F409+F422 · 2026-05-13" while R-F509/F510 were
live. The verify-after-fix discipline lost its authoritative tell —
no way to confirm a deploy had landed except by behavioural probe.

R-F513 reads ARIA_BUILD_GIT_SHA and ARIA_BUILD_R_TAG from environment
(set in aria_service/Dockerfile via ARG → ENV, populated by deploy-
fly.yml's --build-arg or by local `flyctl deploy --build-arg ...`).
Module-load time, so /api/aria/health/live carries the deployed SHA.

These tests load main.py with controlled env so we don't depend on
the real build context.
"""
from __future__ import annotations

import importlib
import os

import pytest


def _reload_main_with_env(monkeypatch, sha: str | None, r_tag: str | None):
    """Reset env then re-import aria_service.main so ARIA_BUILD_REV
    is recomputed against the test fixture."""
    if sha is None:
        monkeypatch.delenv("ARIA_BUILD_GIT_SHA", raising=False)
    else:
        monkeypatch.setenv("ARIA_BUILD_GIT_SHA", sha)
    if r_tag is None:
        monkeypatch.delenv("ARIA_BUILD_R_TAG", raising=False)
    else:
        monkeypatch.setenv("ARIA_BUILD_R_TAG", r_tag)

    # Re-import the module so the module-level ARIA_BUILD_REV branch
    # runs again with our env.
    import aria_service.main as _m
    return importlib.reload(_m)


def test_rf513_both_vars_set_builds_full_rev(monkeypatch):
    """Happy path: CI passes both ARGs; build_rev gets the R-tag + sha."""
    m = _reload_main_with_env(
        monkeypatch,
        sha="a698a4fcdb22ec1aabb9c44557b76b8a8c7b18d2",
        r_tag="R-F511+F512+F513",
    )
    assert m.ARIA_BUILD_REV == "R-F511+F512+F513 · sha a698a4fc", (
        f"Expected combined R-tag + 8-char SHA, got: {m.ARIA_BUILD_REV!r}"
    )


def test_rf513_only_sha_set_uses_sha_only(monkeypatch):
    """SHA set but no R-tag (commit without R-number): use SHA alone."""
    m = _reload_main_with_env(
        monkeypatch,
        sha="0123456789abcdef0123456789abcdef01234567",
        r_tag=None,
    )
    assert m.ARIA_BUILD_REV == "sha 01234567", (
        f"Expected sha-only format, got: {m.ARIA_BUILD_REV!r}"
    )


def test_rf513_unknown_sha_falls_back_to_sentinel(monkeypatch):
    """Dockerfile default ARG is 'unknown' — if nobody passed
    --build-arg, surface that loudly in /health/live."""
    m = _reload_main_with_env(monkeypatch, sha="unknown", r_tag=None)
    assert "UNKNOWN-BUILD" in m.ARIA_BUILD_REV, (
        f"Expected sentinel marker, got: {m.ARIA_BUILD_REV!r}"
    )
    assert "--build-arg" in m.ARIA_BUILD_REV, (
        "Sentinel should hint at the fix (pass --build-arg)."
    )


def test_rf513_empty_env_falls_back_to_sentinel(monkeypatch):
    """Env vars not set at all: same sentinel as 'unknown'."""
    m = _reload_main_with_env(monkeypatch, sha=None, r_tag=None)
    assert "UNKNOWN-BUILD" in m.ARIA_BUILD_REV


def test_rf513_health_live_returns_new_rev(monkeypatch):
    """Capability test: /api/aria/health/live picks up the new
    ARIA_BUILD_REV. The endpoint reads main.ARIA_BUILD_REV lazily so
    we need the module reload to take effect."""
    _reload_main_with_env(
        monkeypatch,
        sha="deadbeefcafe1234",
        r_tag="R-F513",
    )

    # /health/live is a router-level endpoint that imports
    # ARIA_BUILD_REV lazily inside the handler — re-import the routes
    # module too so its cached reference (if any) is dropped.
    import aria_service.routes.aria as _aria
    importlib.reload(_aria)

    # Call the endpoint function directly — it's a sync def (R-F723).
    response = _aria.health_live_ep()
    assert response["status"] == "alive"
    assert response["build_rev"] == "R-F513 · sha deadbeef", (
        f"/health/live build_rev mismatch: {response['build_rev']!r}"
    )


def test_rf513_sha_length_caps_at_eight_chars(monkeypatch):
    """Defensive: even on a short SHA we don't index past length."""
    m = _reload_main_with_env(monkeypatch, sha="abc", r_tag="R-F513")
    # 'abc'[:8] == 'abc' — main.py uses slice notation so no IndexError.
    assert m.ARIA_BUILD_REV == "R-F513 · sha abc"

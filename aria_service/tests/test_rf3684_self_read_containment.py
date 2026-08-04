"""R-F3684 — CAPABILITY test: self/read cannot escape the project or the
protected set, and is operator-tier at the HTTP boundary.

THE DEFECT (360 DD sweep, 2026-08-04) — three failures composing into an
arbitrary-file-read reachable by any signed-in viewer:

1. ``POST /api/aria/self/read`` was absent from ``_OPERATOR_ONLY_RE``, and
   ``server.mjs`` has no route for it (it *does* have an explicit
   ``requireAdmin`` for ``/self/code`` at :3870), so it fell through to the
   ``app.use('/api/aria', requireAuth, …)`` catch-all — which admits a viewer.
2. ``read_own_code`` compared ``file_path in PROTECTED_FILES`` — an exact
   STRING match — *before* ``resolve()``. ``.env`` was refused; ``./.env``,
   ``.//.env`` and ``aria_service/../.env`` were not.
3. Containment used ``str(full_path).startswith(str(_root.resolve()))``, a
   string prefix test that a sibling directory (``…/Aria-backup``) satisfies.

``scripts/`` is copied into the image (aria_service/Dockerfile:169), so the
payoff was reading ``scripts/verify_all.py`` — which until R-F3683 carried a
live bearer token.

§3c: this drives ``self_improve.read_own_code`` — the function that was broken
— and asserts the user-visible outcome (refusal, no content), not a helper.

Run: python -m pytest aria_service/tests/test_rf3684_self_read_containment.py -v
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from aria_service.intel import self_improve


def _read(path: str) -> dict:
    return asyncio.run(self_improve.read_own_code(path))


def _is_refusal(result: dict) -> bool:
    return "error" in result and "content" not in result


# ── The protected-set bypass (defect 2) ────────────────────────────────────

@pytest.mark.parametrize(
    "variant",
    [
        ".env",                       # the plain form — refused even before
        "./.env",                     # THE bypass: missed the string set
        ".//.env",
        "aria_service/../.env",
        "./scripts/../.env",
        "aria_service/./../.env",
    ],
)
def test_protected_file_cannot_be_reached_by_an_equivalent_path(variant):
    result = _read(variant)
    assert _is_refusal(result), (
        f"{variant!r} returned content — every path that RESOLVES to a protected "
        f"file must be refused, not just the exact string in PROTECTED_FILES"
    )


@pytest.mark.parametrize(
    "variant",
    ["server.mjs", "./server.mjs", "aria_service/../server.mjs", ".//server.mjs"],
)
def test_protected_source_file_variants_are_refused(variant):
    result = _read(variant)
    assert _is_refusal(result), f"{variant!r} must not be readable"
    # A missing file and a protected file must stay distinguishable, so the
    # refusal is not accidentally coming from "not found".
    if Path(self_improve._root / "server.mjs").exists():
        assert "Protected" in result["error"], (
            f"{variant!r} was refused for the wrong reason: {result['error']!r}"
        )


# ── The containment bypass (defect 3) ──────────────────────────────────────

@pytest.mark.parametrize(
    "escape",
    [
        "../secrets.txt",
        "../../etc/passwd",
        "../../../../../../etc/passwd",
        "..\\..\\Windows\\System32\\drivers\\etc\\hosts",
        "../Aria-backup/.env",       # the sibling-prefix escape
        "../Aria2/server.mjs",
    ],
)
def test_paths_outside_the_project_root_are_refused(escape):
    result = _read(escape)
    assert _is_refusal(result), f"{escape!r} escaped the project root"
    assert "content" not in result


def test_sibling_directory_sharing_the_root_name_is_refused():
    """`startswith` admitted `C:\\Code\\Aria-backup` for root `C:\\Code\\Aria`."""
    root = self_improve._root.resolve()
    sibling = f"../{root.name}-backup/anything.txt"
    result = _read(sibling)
    assert _is_refusal(result)
    assert "outside project root" in result["error"] or "not found" in result["error"].lower()


def test_malformed_paths_do_not_raise():
    """A NUL byte must be a refusal, not a 500."""
    for bad in ["\0", "a\0b.py", ""]:
        result = _read(bad)
        assert isinstance(result, dict)
        assert _is_refusal(result), f"{bad!r} should be refused cleanly"


# ── The gate is not over-broad ─────────────────────────────────────────────

def test_a_normal_source_file_is_still_readable():
    """The coder reads its own source in-process; that must keep working."""
    result = _read("aria_service/intel/self_improve.py")
    assert "error" not in result, f"legitimate read broke: {result.get('error')!r}"
    assert result["content"], "expected file content"
    assert "read_own_code" in result["functions"]


# ── The HTTP boundary (defect 1) ───────────────────────────────────────────

def test_self_read_and_self_files_are_operator_tier():
    from aria_service.routes.aria import _OPERATOR_ONLY_RE

    for path in ("/api/aria/self/read", "/api/aria/self/files"):
        assert _OPERATOR_ONLY_RE.search(path), (
            f"{path} must require the OPERATOR tier — the shared service token "
            f"held by aria-web/aria-wa must not reach an arbitrary file read"
        )


def test_the_existing_operator_gates_did_not_regress():
    from aria_service.routes.aria import _OPERATOR_ONLY_RE

    for path in (
        "/api/aria/self/improve",
        "/api/aria/self/deploy/abc",
        "/api/aria/self/code",
        "/api/aria/autonomous/pause",
        "/api/aria/admin/llm/cooldown/clear",
        "/api/aria/operating-mode/set",
    ):
        assert _OPERATOR_ONLY_RE.search(path), f"{path} lost its operator gate"


def test_read_only_surfaces_are_not_newly_gated():
    """R-F2567: over-gating broke the coder's LLM calls. Do not repeat it."""
    from aria_service.routes.aria import _OPERATOR_ONLY_RE

    for path in (
        "/api/aria/coder/llm",
        "/api/aria/coder/rag/query",
        "/api/aria/health/perf",
        "/api/aria/self/staged",
        "/api/aria/self/recent-errors",
    ):
        assert not _OPERATOR_ONLY_RE.search(path), (
            f"{path} must NOT be operator-only — over-gating is its own outage"
        )

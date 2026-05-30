"""R-F396 — /api/aria/health/perf self-introspection endpoint.

Live evidence 2026-05-13: ARIA self-reported that "Build a /api/aria/
health/perf endpoint — I currently have no way to answer 'how are
you performing?' except by observing this conversation. A structured
performance diagnostic that I can call would save hours of guesswork
and let me report hard metrics instead of self-assessments."

Fix: add `/health/perf` to routes/aria.py that returns a single
LLM-readable JSON aggregating operational signals (status / mode /
infra / quality), verification stats, autonomy state, LLM provider
availability, and a curated `advisories` array ARIA can quote in
self-assessments.

These tests pin the endpoint exists with the contract ARIA's tool
dispatch will rely on. Behavioural HTTP tests aren't possible without
a running FastAPI app; we lean on source assertions + shape verification.
"""
from __future__ import annotations

import inspect
import pathlib


def _src() -> str:
    return pathlib.Path(
        "C:/code/crucix/aria_service/routes/aria.py"
    ).read_text(encoding="utf-8", errors="ignore")


def test_rf396_endpoint_registered():
    """The new route must be wired into the FastAPI router."""
    src = _src()
    assert '@router.get("/health/perf")' in src, (
        "R-F396 regression: /health/perf route not registered."
    )
    assert "async def health_perf_ep" in src, (
        "R-F396: handler function missing or renamed."
    )


def test_rf396_endpoint_callable_as_async():
    """The handler must be importable and async."""
    from aria_service.routes.aria import health_perf_ep
    assert inspect.iscoroutinefunction(health_perf_ep)


def test_rf396_endpoint_returns_expected_top_level_keys():
    """The schema is the contract ARIA's tool dispatch depends on.
    If these keys ever change, the prompt that tells her how to quote
    them must be updated in lockstep."""
    src = _src()
    # Look for the return dict block inside health_perf_ep
    idx = src.find("async def health_perf_ep")
    assert idx > 0
    # The function body is ~200+ lines; search the full function
    next_def = src.find("\n@", idx + 10)
    end = next_def if next_def > 0 else idx + 8000
    block = src[idx:end]

    required = [
        "build_rev",
        "status",
        "operating_mode",
        "degraded_reasons",
        "quality",
        "infra",
        "circuit_breakers",
        "verification_24h",
        "autonomy",
        "llm_providers",
        "advisories",
        "_schema_version",
    ]
    for key in required:
        assert f'"{key}"' in block, (
            f"R-F396 regression: response key {key!r} missing from "
            f"health_perf_ep return dict."
        )


def test_rf396_endpoint_reuses_health_check_base():
    """The handler must call health_check_ep() to inherit the existing
    operational signals — avoids drift between /health and /health/perf."""
    src = _src()
    idx = src.find("async def health_perf_ep")
    block = src[idx:idx + 4000]
    assert "await health_check_ep()" in block, (
        "R-F396: handler doesn't reuse health_check_ep — risk of "
        "drift between /health and /health/perf."
    )


def test_rf396_endpoint_swallows_subsystem_errors():
    """Each subsystem probe must be wrapped in try/except so a single
    missing module (e.g. verification_gate import failure) doesn't
    crash the whole response. ARIA must always get *something*."""
    src = _src()
    idx = src.find("async def health_perf_ep")
    block = src[idx:idx + 4000]
    # Count try/except blocks — should be at least 4 (base, verify,
    # autonomy, providers, advisories).
    try_count = block.count("try:")
    except_count = block.count("except Exception:")
    assert try_count >= 4, (
        f"R-F396: only {try_count} try/ blocks — missing per-subsystem "
        f"error isolation. A single broken import would break the endpoint."
    )
    assert except_count >= 4


def test_rf396_advisories_array_is_populated():
    """The advisories array is ARIA's quotable summary — must always
    include at least the build_rev so self-assessments have a
    timestamp/marker even when nothing's degraded."""
    src = _src()
    idx = src.find("async def health_perf_ep")
    next_def = src.find("\n@", idx + 10)
    end = next_def if next_def > 0 else idx + 8000
    block = src[idx:end]
    assert "advisories.append" in block, (
        "R-F396: advisories never populated — ARIA gets an empty array."
    )
    assert "ARIA_BUILD_REV" in block, (
        "R-F396: build_rev marker missing from advisories — "
        "self-assessment loses the timestamp anchor."
    )


def test_rf396_schema_version_pinned():
    """The _schema_version field exists so the LLM prompt that
    instructs ARIA how to quote the response can be versioned alongside.
    A version bump signals an intentional contract change.

    R-F408 (2026-05-13): originally asserted "rf396.v1". R-F400 bumped
    to "rf400.v1" intentionally (added inventory + retention fields).
    The forward-compat shape `"_schema_version": "rfNNN.vN"` is what
    really matters — pin that pattern, not a specific version, so a
    future bump doesn't regress without breaking tests that have a
    good reason to care.
    """
    import re
    src = _src()
    idx = src.find("async def health_perf_ep")
    next_def = src.find("\n@", idx + 10)
    end = next_def if next_def > 0 else idx + 8000
    block = src[idx:end]
    assert '"_schema_version"' in block
    # Pattern: "_schema_version": "rfNNN.vN" — any R-F number + minor version.
    m = re.search(r'"_schema_version":\s*"(rf\d+\.v\d+)"', block)
    assert m is not None, (
        "R-F408: _schema_version no longer follows rfNNN.vN pattern. "
        "Consumers depend on this shape — fix the value, not the test."
    )
    # Pinned: at least rf400.v1 (R-F400 added inventory + retention).
    # If a future contributor LOWERS this, they removed a field.
    version_str = m.group(1)
    rev_num = int(re.match(r"rf(\d+)", version_str).group(1))
    assert rev_num >= 400, (
        f"R-F408: schema version regressed to {version_str}. "
        f"R-F400 added inventory + retention — version must be rf400.v1 "
        f"or higher. Fix: don't remove those fields."
    )

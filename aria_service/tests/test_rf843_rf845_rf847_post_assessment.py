"""R-F843 + R-F845 + R-F847 — fixes from the 2026-05-23 web-UI 360 assessment.

The Node-side fixes (R-F843 /api/status, R-F844 /api/v1/health,
R-F846 aria-web build_rev) live in JS files — regression-tested via
pattern checks rather than via Node spinup in pytest (no Node runtime
in the Python venv).

R-F843: /api/status overall calc must factor in bridge.healthy for ANY
        unhealthy bridge — not just the case where the token is missing.
        Previously a live R-F838 .internal outage with a valid token
        still rendered "All systems operational" on the public page.

R-F844: /api/v1/health must return 200 BEFORE the publicApiEnabled gate
        so external monitors can distinguish "service is alive but
        public API off" from "service is unreachable".

R-F845: Autonomous engine STARTUP_DELAY_SECONDS bumped 90 → 180.
        Cold-start absorb storm wedged event loop; CI now redeploys on
        every push (R-F841), making this more frequent.

R-F846: aria-web Dockerfile passes ARIA_BUILD_GIT_SHA, server.mjs reads
        it as priority 0 in _resolveBuildRev (env beats file beats git).

R-F847: WANotifier reads ARIA_WA_INTERNAL_URL first, then falls back to
        SEENODE_BASE_URL. Already covered by test_rf839; this just
        verifies the new env var name appears in the codebase.
"""
from __future__ import annotations

import os
import pathlib


_ROOT = pathlib.Path(__file__).parent.parent.parent
_SERVER = _ROOT / "server.mjs"
_STATUS = _ROOT / "lib" / "status" / "routes.mjs"
_V1 = _ROOT / "lib" / "api_keys" / "routes.mjs"
_DOCKERFILE_WEB = _ROOT / "Dockerfile.web"


# ── R-F843 ────────────────────────────────────────────────────────────


def test_rf843_status_degrades_on_any_unhealthy_bridge():
    """The /api/status overall calc must demote to 'degraded' when the
    bridge is unhealthy regardless of has_token state."""
    text = _STATUS.read_text(encoding="utf-8")
    # The OLD guard required BOTH conditions — that's the bug.
    assert "!bridge.healthy && bridge.has_token === false" not in text, (
        "Status page still requires (!healthy && !has_token) — must demote "
        "on (!healthy) alone, otherwise it lies during .internal outages."
    )
    # The NEW guard should only check !healthy, with the has_token check
    # used only to choose summary copy.
    assert "else if (bridge && !bridge.healthy)" in text, (
        "Expected the new R-F843 guard pattern."
    )


# ── R-F844 ────────────────────────────────────────────────────────────


def test_rf844_v1_health_route_registered_before_public_api_gate():
    """/api/v1/health must be registered BEFORE the publicApiEnabled
    middleware so it answers 200 regardless of the soft-rollout flag.

    The file has TWO router factories — scope the search to the V1
    block so we don't accidentally hit the keys-router gate."""
    text = _V1.read_text(encoding="utf-8")
    v1_start = text.find("export function createV1Router")
    assert v1_start > 0, "createV1Router missing — refactor?"
    # End of function body = the closing `return router; }` of createV1Router.
    v1_block = text[v1_start:]
    health_pos = v1_block.find("router.get('/health'")
    gate_pos = v1_block.find("if (!publicApiEnabled())")
    assert health_pos > 0, "Missing /api/v1/health route — R-F844 lost?"
    assert gate_pos > 0, "Missing publicApiEnabled gate inside createV1Router (sanity)"
    assert health_pos < gate_pos, (
        "/api/v1/health must be defined BEFORE the publicApiEnabled gate "
        "inside createV1Router so it returns 200 when the public API is "
        "off — otherwise external monitors can't tell 'off by config' "
        "from 'service down'."
    )


def test_rf844_v1_health_payload_contains_public_api_enabled():
    text = _V1.read_text(encoding="utf-8")
    # The route should surface the gate state so monitors can record it.
    assert "public_api_enabled: publicApiEnabled()" in text


# ── R-F845 ────────────────────────────────────────────────────────────


def test_rf845_engine_startup_delay_raised_to_180():
    """The autonomous engine's STARTUP_DELAY_SECONDS must be >= 180 to
    survive the cold-start absorb storm (R-F838/F841 increased deploy
    frequency)."""
    from aria_service.autonomous import engine as eng
    assert eng.STARTUP_DELAY_SECONDS >= 180, (
        f"STARTUP_DELAY_SECONDS={eng.STARTUP_DELAY_SECONDS} too short — "
        f"cold-start absorb storm wedges event loop before lifespan warmup "
        f"completes. R-F845 set this to 180."
    )


# ── R-F846 ────────────────────────────────────────────────────────────


def test_rf846_dockerfile_web_has_build_arg():
    """Dockerfile.web must declare ARG ARIA_BUILD_GIT_SHA so CI can
    pass the SHA via --build-arg, mirroring aria-intel."""
    text = _DOCKERFILE_WEB.read_text(encoding="utf-8")
    assert "ARG ARIA_BUILD_GIT_SHA" in text, (
        "Dockerfile.web missing ARG ARIA_BUILD_GIT_SHA — build_rev "
        "will stay UNKNOWN-BUILD on every aria-web deploy."
    )
    assert "ENV ARIA_BUILD_GIT_SHA=$ARIA_BUILD_GIT_SHA" in text, (
        "ARG without ENV pass-through — runtime can't read it."
    )


def test_rf846_server_mjs_reads_env_var_first():
    """server.mjs _resolveBuildRev must check ARIA_BUILD_GIT_SHA env
    var as priority 0 — before the file path and before the git
    fallback — so the value baked in by Docker beats stale files."""
    text = _SERVER.read_text(encoding="utf-8")
    func_start = text.find("function _resolveBuildRev()")
    assert func_start > 0, "Can't find _resolveBuildRev — refactor?"
    func_chunk = text[func_start : func_start + 1500]
    env_pos = func_chunk.find("ARIA_BUILD_GIT_SHA")
    file_pos = func_chunk.find("build_rev.txt")
    assert env_pos > 0, "Missing ARIA_BUILD_GIT_SHA env-var read in _resolveBuildRev"
    assert file_pos > 0, "Missing build_rev.txt path (sanity)"
    assert env_pos < file_pos, (
        "ARIA_BUILD_GIT_SHA env var must be checked BEFORE the "
        "build_rev.txt fallback — otherwise stale files override the "
        "fresh deploy SHA."
    )


# ── R-F847 (smoke) ────────────────────────────────────────────────────


def test_rf847_coder_entrypoint_warning_mentions_new_env_var():
    """The boot-time warning that fires when WANotifier is dormant
    must mention ARIA_WA_INTERNAL_URL — that's the canonical env var
    post-R-F839. Pointing operators at SEENODE_BASE_URL alone makes
    them set the legacy var instead of the supported one."""
    entry = _ROOT / "aria_service" / "autonomous" / "coder_entrypoint.py"
    text = entry.read_text(encoding="utf-8")
    assert "ARIA_WA_INTERNAL_URL" in text, (
        "coder_entrypoint.py doesn't reference ARIA_WA_INTERNAL_URL — "
        "operators reading the dormant-warning log will be told to set "
        "the deprecated SEENODE_BASE_URL instead."
    )

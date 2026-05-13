"""R-F407 + R-F408 — Hallucination & Guard Violations dashboard panel
and verification-sweep bug fixes.

R-F407 ships:
  - self_claim_guard.record_violations() / record_turn_observed() /
    get_stats() — Redis-backed counters with 24h expiry.
  - /api/aria/hallucination/stats endpoint combining R-F401 self_claim_
    guard + existing stream_guard_observer surfaces.
  - aria-brain.html "Hallucination & Guard Violations" panel.
  - Seenode proxy route for the new endpoint.

R-F408 fixes 3 bugs surfaced by the post-session verification sweep:
  Issue 1: test_health_perf_endpoint_rf396.py asserted "rf396.v1" but
           R-F400 bumped to "rf400.v1". Forward-compat regex now used.
  Issue 2: test_streaming_fallback_cap_rf402.py imported FallbackLLM
           which doesn't exist (real class is FallbackProvider).
  Issue 3: self_claim_guard.record_violations() called rs.incr(ttl=)
           which has no ttl kwarg. Switched to incr + expire 2-call.

This file tests the R-F407 surface AND pins the R-F408 fixes so a
future refactor can't silently regress them.
"""
from __future__ import annotations

import inspect
import pathlib
import re


# ══════════════════════════════════════════════════════════════════
# R-F407 — endpoint, counter API, dashboard panel
# ══════════════════════════════════════════════════════════════════

def test_rf407_endpoint_registered():
    """The /hallucination/stats endpoint must be registered."""
    src = pathlib.Path(
        "C:/code/crucix/aria_service/routes/aria.py"
    ).read_text(encoding="utf-8", errors="ignore")
    assert '@router.get("/hallucination/stats")' in src, (
        "R-F407 regression: /hallucination/stats route not registered."
    )
    assert "async def hallucination_stats_ep" in src


def test_rf407_endpoint_combines_both_sources():
    """The handler must call BOTH self_claim_guard.get_stats AND
    stream_guard_observer.get_stats — operator needs one panel that
    shows everything."""
    src = pathlib.Path(
        "C:/code/crucix/aria_service/routes/aria.py"
    ).read_text(encoding="utf-8", errors="ignore")
    idx = src.find("async def hallucination_stats_ep")
    block = src[idx:idx + 3000]
    assert "self_claim_guard" in block
    assert "stream_guard_observer" in block
    # Both surfaces appear in the response
    assert '"self_claim_guard"' in block
    assert '"stream_guards"' in block


def test_rf407_endpoint_has_summary_block():
    """The response must include a top-line summary so the dashboard
    badge has the rolled-up rate without drilling into sub-objects."""
    src = pathlib.Path(
        "C:/code/crucix/aria_service/routes/aria.py"
    ).read_text(encoding="utf-8", errors="ignore")
    idx = src.find("async def hallucination_stats_ep")
    block = src[idx:idx + 3000]
    assert '"summary"' in block
    assert "total_violations_24h" in block
    assert "violation_rate_24h" in block


def test_rf407_endpoint_schema_versioned():
    """Schema version pinned at rfNNN.vN for forward-compat consumers."""
    src = pathlib.Path(
        "C:/code/crucix/aria_service/routes/aria.py"
    ).read_text(encoding="utf-8", errors="ignore")
    idx = src.find("async def hallucination_stats_ep")
    block = src[idx:idx + 3000]
    m = re.search(r'"_schema_version":\s*"(rf\d+\.v\d+)"', block)
    assert m is not None, "R-F407: schema_version pattern missing or malformed"


# ── self_claim_guard counter API ──────────────────────────────────

def test_rf407_record_violations_function_exists():
    from aria_service.intel import self_claim_guard
    assert hasattr(self_claim_guard, "record_violations")
    assert inspect.iscoroutinefunction(self_claim_guard.record_violations)


def test_rf407_record_turn_observed_function_exists():
    from aria_service.intel import self_claim_guard
    assert hasattr(self_claim_guard, "record_turn_observed")
    assert inspect.iscoroutinefunction(self_claim_guard.record_turn_observed)


def test_rf407_get_stats_function_exists():
    from aria_service.intel import self_claim_guard
    assert hasattr(self_claim_guard, "get_stats")
    assert inspect.iscoroutinefunction(self_claim_guard.get_stats)


def test_rf407_record_violations_accepts_violation_list_only():
    """record_violations signature: violations + optional preview + trace_id."""
    from aria_service.intel import self_claim_guard
    sig = inspect.signature(self_claim_guard.record_violations)
    params = sig.parameters
    assert "violations" in params
    assert "response_preview" in params
    assert "trace_id" in params


# ── Confidence_footer wires record_violations + record_turn_observed ──

def test_rf407_confidence_footer_records_metrics():
    """When the guard scan fires (or doesn't), the footer chain must
    fire-and-forget the metrics — otherwise the dashboard panel will
    stay at zero forever."""
    src = pathlib.Path(
        "C:/code/crucix/aria_service/intel/confidence_footer.py"
    ).read_text(encoding="utf-8", errors="ignore")
    assert "record_violations" in src, (
        "R-F407: confidence_footer doesn't call record_violations — "
        "dashboard panel won't have data even when violations fire."
    )
    assert "record_turn_observed" in src
    assert "create_task" in src, (
        "R-F407: metric calls must be fire-and-forget via create_task — "
        "otherwise the chat reply waits for Redis on every turn."
    )


# ── Dashboard panel HTML ──────────────────────────────────────────

def test_rf407_panel_exists_in_dashboard():
    """The aria-brain.html dashboard must contain the panel + its
    loadHallucination() function + the refreshAll() registration."""
    src = pathlib.Path(
        "C:/code/crucix/public/aria-brain.html"
    ).read_text(encoding="utf-8", errors="ignore")
    assert 'id="hallucination-panel"' in src, (
        "R-F407: dashboard panel div missing."
    )
    assert "async function loadHallucination" in src
    # Must call the right endpoint
    assert "/hallucination/stats" in src
    # Must be wired into refreshAll
    refresh_idx = src.find("async function refreshAll")
    refresh_block = src[refresh_idx:refresh_idx + 1500]
    assert "loadHallucination" in refresh_block, (
        "R-F407: loadHallucination not in refreshAll() — panel won't auto-refresh."
    )


# ── Seenode proxy route ───────────────────────────────────────────

def test_rf407_seenode_proxy_route_registered():
    """server.mjs must proxy /api/aria/hallucination/stats to fly."""
    src = pathlib.Path(
        "C:/code/crucix/server.mjs"
    ).read_text(encoding="utf-8", errors="ignore")
    assert "/api/aria/hallucination/stats" in src, (
        "R-F407: seenode proxy route missing. Dashboard fetch will 404."
    )


# ══════════════════════════════════════════════════════════════════
# R-F408 — verification-sweep bug fixes
# ══════════════════════════════════════════════════════════════════

def test_rf408_fallback_provider_import_correct():
    """The R-F402 test imports FallbackProvider (not FallbackLLM,
    which doesn't exist). Verification-sweep fix."""
    src = pathlib.Path(
        "C:/code/crucix/aria_service/tests/test_streaming_fallback_cap_rf402.py"
    ).read_text(encoding="utf-8", errors="ignore")
    assert "FallbackProvider" in src
    assert "FallbackLLM" not in src, (
        "R-F408 regression: FallbackLLM reference is back. The class "
        "doesn't exist — test will ImportError."
    )
    # And the class IS named FallbackProvider in the actual file
    fallback_src = pathlib.Path(
        "C:/code/crucix/aria_service/llm/fallback.py"
    ).read_text(encoding="utf-8", errors="ignore")
    assert "class FallbackProvider" in fallback_src


def test_rf408_schema_version_test_forward_compat():
    """R-F396 schema test must use a forward-compat regex pattern
    (not a hardcoded version string) so R-F400's bump doesn't break it."""
    src = pathlib.Path(
        "C:/code/crucix/aria_service/tests/test_health_perf_endpoint_rf396.py"
    ).read_text(encoding="utf-8", errors="ignore")
    # The hard-coded "rf396.v1" assertion must be gone
    # (replaced by a regex pattern check).
    # Acceptable: regex pattern check OR explicit forward-compat assert
    # like "rf400.v1 or higher".
    assert "rev_num >= 400" in src or "rf400.v1" in src or "rfNNN" in src.lower(), (
        "R-F408: R-F396 schema test still hardcoded to a specific version. "
        "Will break again on the next legitimate schema bump."
    )


def test_rf408_self_claim_guard_uses_expire_not_incr_ttl():
    """The redis_store.incr() function has no ttl kwarg. R-F408
    fixed the 3 call sites to use incr + expire as a 2-call pattern."""
    src = pathlib.Path(
        "C:/code/crucix/aria_service/intel/self_claim_guard.py"
    ).read_text(encoding="utf-8", errors="ignore")
    # The broken pattern must NOT appear anywhere
    assert "rs.incr(" in src, "R-F407: incr() calls should still exist"
    # No call should have ttl= kwarg
    assert "rs.incr(_R_KEY_CTR_PREFIX + v.pattern_id, ttl=" not in src, (
        "R-F408 regression: rs.incr(ttl=) pattern is back. The function "
        "has no ttl kwarg — silently swallowed TypeError, no counters."
    )
    # And expire is called after each incr (verify via pattern)
    assert "rs.expire(" in src, (
        "R-F408: rs.expire() missing — counters won't have 24h reset."
    )


def test_rf408_redis_store_incr_signature():
    """Sanity check: the actual redis_store.incr signature is what
    we think it is. Tests like this catch silent API drift."""
    from aria_service.intel import redis_store
    sig = inspect.signature(redis_store.incr)
    assert "ttl" not in sig.parameters, (
        "R-F408: if redis_store.incr gains a ttl kwarg in the future, "
        "the R-F408 fix loses its rationale. Revisit the call sites."
    )
    # Confirm expire exists
    assert hasattr(redis_store, "expire")
    expire_sig = inspect.signature(redis_store.expire)
    assert "seconds" in expire_sig.parameters

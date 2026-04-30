"""F100 (2026-04-30): when a circuit breaker trips, the capability
gap recorded must reflect the actual failure reason — not the legacy
"timeout" placeholder.

Live trigger: 09:03:26 fly logs — Brave 402 (billing) and Semantic
Scholar 429 (rate limit) both produced
`Capability gap recorded: [timeout] Backend X unreachable`. Misrouted
triage onto "transient network issue" when the real fix was a key
rotation / paid-tier upgrade / caller-side rate limiter.

These tests pin the new mapping so the next regression is caught
before it reaches prod.
"""
from __future__ import annotations

import asyncio

from aria_service.intel.circuit_breaker import (
    CircuitBreaker,
    _gap_for_reason,
    _REASON_TO_GAP,
)


def test_billing_reason_maps_to_billing_required_gap():
    gap_type, gap_detail = _gap_for_reason("brave_search", "billing")
    assert gap_type == "billing_required"
    assert "brave_search" in gap_detail
    # Detail should mention the operator action — that's the whole
    # point of the new classification.
    assert any(word in gap_detail.lower() for word in ("credit", "rotate", "key", "operator"))


def test_rate_limit_reason_maps_to_rate_limited_gap():
    gap_type, gap_detail = _gap_for_reason("semantic_scholar", "rate_limit")
    assert gap_type == "rate_limited"
    assert "semantic_scholar" in gap_detail
    assert any(word in gap_detail.lower() for word in ("rate", "quota", "key", "bucket"))


def test_auth_reason_maps_to_auth_failure_gap():
    gap_type, _ = _gap_for_reason("opensanctions", "auth")
    assert gap_type == "auth_failure"


def test_unknown_or_empty_reason_falls_back_to_timeout_gap():
    """Backward compat: existing callers that don't pass a reason still
    get a recorded gap — just classified as 'timeout' as before. This
    guards against a noisy migration where every legacy caller breaks
    if they don't update at the same time."""
    gap_type, _ = _gap_for_reason("legacy_backend", "")
    assert gap_type == "timeout"
    gap_type, _ = _gap_for_reason("weird_backend", "kraken_attack")
    assert gap_type == "timeout"


def test_record_failure_remembers_reason_for_classification():
    cb = CircuitBreaker(name="test_brave", failure_threshold=3)
    cb.record_failure(reason="billing")
    cb.record_failure(reason="billing")
    cb.record_failure(reason="billing")
    assert cb.state == "OPEN"
    assert cb.last_failure_reason == "billing"


def test_record_failure_without_reason_does_not_clobber_previous():
    """If a caller has been updated to pass a reason, but a peer call
    on the same breaker hasn't been (e.g. retry path), the latest known
    reason should stick — not be reset to '' just because one call
    didn't supply it."""
    cb = CircuitBreaker(name="test_mixed", failure_threshold=10)
    cb.record_failure(reason="rate_limit")
    cb.record_failure()  # legacy call site, no reason
    assert cb.last_failure_reason == "rate_limit"


def test_valid_gap_types_includes_new_categories():
    """Capability_gaps.VALID_GAP_TYPES must include the three new
    categories or record_gap will emit 'Unknown gap type' WARNINGs
    every time the breaker trips, polluting the error ledger."""
    from aria_service.intel.capability_gaps import VALID_GAP_TYPES
    assert "billing_required" in VALID_GAP_TYPES
    assert "rate_limited" in VALID_GAP_TYPES
    assert "auth_failure" in VALID_GAP_TYPES


def test_reason_mapping_is_complete_for_documented_reasons():
    """Every reason listed in the record_failure docstring must have a
    mapping in _REASON_TO_GAP. Catching this drift prevents the case
    where someone adds a new reason tag in a caller and the breaker
    silently falls back to 'timeout'."""
    documented = {"billing", "rate_limit", "auth", "timeout", "server"}
    assert documented.issubset(set(_REASON_TO_GAP.keys()))

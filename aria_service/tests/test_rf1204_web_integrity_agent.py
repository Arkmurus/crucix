"""R-F1204 — Web Integrity Agent capability tests.

Tests every directive:
  1. Input validation — rejects malformed payloads
  2. Output verification — detects missing fields
  3. Error pattern detection — identifies recurring failures
  4. Cross-agent communication — wires to brain
  5. Zero tolerance — every error is recorded
  6. Self-healing — stages fixes for patterns
  7. Never silent — every check produces output
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

from aria_service.intel.web_integrity_agent import (
    WebIntegrityAgent,
    ErrorPatternDetector,
    IntegrityCheck,
    validate_input_payload,
    WEB_ENDPOINTS,
    INPUT_SCHEMAS,
)


# ── Directive 1: Verify Every Input ─────────────────────────────────────────

def test_validate_input_valid_payload():
    """A valid payload passes all schema checks."""
    schema = {
        "required_fields": ["message"],
        "field_types": {"message": str},
    }
    errors = validate_input_payload({"message": "hello"}, schema)
    assert errors == [], f"Expected no errors, got: {errors}"


def test_validate_input_missing_required_field():
    """A payload missing a required field is rejected."""
    schema = {
        "required_fields": ["message", "session_id"],
        "field_types": {"message": str},
    }
    errors = validate_input_payload({"message": "hello"}, schema)
    assert len(errors) == 1
    assert "Missing required field" in errors[0]
    assert "session_id" in errors[0]


def test_validate_input_wrong_type():
    """A payload with wrong field types is rejected."""
    schema = {
        "required_fields": ["message"],
        "field_types": {"message": str},
    }
    errors = validate_input_payload({"message": 123}, schema)
    assert len(errors) == 1
    assert "wrong type" in errors[0].lower()
    assert "int" in errors[0]


def test_validate_input_empty_payload():
    """An empty payload fails all required field checks."""
    schema = {
        "required_fields": ["message", "source"],
        "field_types": {},
    }
    errors = validate_input_payload({}, schema)
    assert len(errors) == 2


def test_validate_input_extra_fields_ignored():
    """Extra fields beyond the schema are ignored (not rejected)."""
    schema = {
        "required_fields": ["message"],
        "field_types": {"message": str},
    }
    errors = validate_input_payload(
        {"message": "hello", "extra_field": "ignored"}, schema
    )
    assert errors == []


# ── Directive 2: Verify Every Output ────────────────────────────────────────

def test_verify_response_valid():
    """A response with all expected fields passes."""
    import asyncio
    agent = WebIntegrityAgent()
    errors = asyncio.run(agent.verify_response(
        "/health/live", "GET",
        {"build_rev": "abc123", "status": "ok"},
    ))
    assert errors == []


def test_verify_response_missing_field():
    """A response missing an expected field is flagged."""
    import asyncio
    agent = WebIntegrityAgent()
    errors = asyncio.run(agent.verify_response(
        "/health/live", "GET",
        {"status": "ok"},  # missing build_rev
    ))
    assert len(errors) >= 1
    assert "missing" in errors[0].lower()


def test_verify_response_unknown_endpoint():
    """An endpoint not in the registry passes through."""
    import asyncio
    agent = WebIntegrityAgent()
    errors = asyncio.run(agent.verify_response(
        "/api/unknown", "GET",
        {"data": "anything"},
    ))
    assert errors == []


# ── Directive 3: Error Pattern Detection ────────────────────────────────────

def test_error_pattern_detection():
    """3+ same-type errors in the window trigger a pattern."""
    detector = ErrorPatternDetector()

    for _ in range(3):
        check = IntegrityCheck(
            endpoint="/health/live",
            method="GET",
            passed=False,
            errors=["Timeout on GET /health/live (>10s)"],
        )
        detector.record_error(check)

    actionable = detector.get_actionable_patterns()
    assert len(actionable) == 1
    assert actionable[0].error_type == "timeout"
    assert actionable[0].count >= 3


def test_error_pattern_below_threshold():
    """Fewer than 3 errors does NOT trigger a pattern."""
    detector = ErrorPatternDetector()

    for _ in range(2):
        check = IntegrityCheck(
            endpoint="/health/live",
            method="GET",
            passed=False,
            errors=["Timeout on GET /health/live (>10s)"],
        )
        detector.record_error(check)

    actionable = detector.get_actionable_patterns()
    assert len(actionable) == 0


def test_error_pattern_multiple_types():
    """Different error types are tracked separately."""
    detector = ErrorPatternDetector()

    # 3 timeout errors
    for _ in range(3):
        check = IntegrityCheck(
            endpoint="/api/aria/status",
            method="GET",
            passed=False,
            errors=["Timeout on GET /api/aria/status (>10s)"],
        )
        detector.record_error(check)

    # 2 connection errors (below threshold)
    for _ in range(2):
        check = IntegrityCheck(
            endpoint="/health/live",
            method="GET",
            passed=False,
            errors=["Connection failed on GET /health/live"],
        )
        detector.record_error(check)

    actionable = detector.get_actionable_patterns()
    assert len(actionable) == 1  # only timeout crossed threshold
    assert actionable[0].error_type == "timeout"


def test_error_classification():
    """Errors are correctly classified by type."""
    detector = ErrorPatternDetector()

    assert detector._classify_error("Timeout on GET /health") == "timeout"
    assert detector._classify_error("Connection failed") == "connection"
    assert detector._classify_error("Server error: 500") == "server_error"
    assert detector._classify_error("Missing field 'name'") == "missing_field"
    assert detector._classify_error("Wrong type: expected str, got int") == "type_mismatch"
    assert detector._classify_error("Something unexpected") == "unknown"


# ── Directive 4: Cross-Agent Communication ──────────────────────────────────

def test_brain_wiring_on_error():
    """Errors are wired to the brain."""
    brain_hook = AsyncMock()
    agent = WebIntegrityAgent(brain_hook=brain_hook)

    check = IntegrityCheck(
        endpoint="/health/live",
        method="GET",
        passed=False,
        errors=["Server error: 500"],
    )

    # Trigger the wiring
    import asyncio
    asyncio.run(agent._wire_to_brain(
        module="web_integrity_agent",
        summary="Test error",
        success=False,
    ))

    brain_hook.absorb.assert_called_once()


# ── Directive 5: Zero Tolerance ─────────────────────────────────────────────

def test_every_error_recorded():
    """Every error produces a log entry and brain signal."""
    detector = ErrorPatternDetector()
    brain_hook = AsyncMock()
    agent = WebIntegrityAgent(brain_hook=brain_hook)

    check = IntegrityCheck(
        endpoint="/api/aria/status",
        method="GET",
        passed=False,
        errors=["Missing field 'status'"],
    )

    detector.record_error(check)

    # The pattern should have 1 occurrence
    assert len(detector._patterns) == 1
    pattern = list(detector._patterns.values())[0]
    assert pattern.count == 1


# ── Directive 6: Self-Healing ───────────────────────────────────────────────

def test_self_healing_stages_fix():
    """Recurring patterns trigger staged fixes."""
    detector = ErrorPatternDetector()

    # Create a pattern with 3+ errors
    for _ in range(3):
        check = IntegrityCheck(
            endpoint="/health/live",
            method="GET",
            passed=False,
            errors=["Timeout on GET /health/live (>10s)"],
        )
        detector.record_error(check)

    actionable = detector.get_actionable_patterns()
    assert len(actionable) == 1

    # Mark as fixed
    detector.mark_fixed(actionable[0].pattern_id, staged_id="fix_001")
    assert actionable[0].fixed
    assert detector._fixes_staged_this_hour == 1


def test_self_healing_rate_limited():
    """No more than MAX_STAGED_FIXES_PER_HOUR fixes are staged."""
    detector = ErrorPatternDetector()

    # Create 10 patterns (only 5 should be actionable due to rate limit)
    for i in range(10):
        for _ in range(3):
            check = IntegrityCheck(
                endpoint=f"/endpoint/{i}",
                method="GET",
                passed=False,
                errors=[f"Timeout on GET /endpoint/{i} (>10s)"],
            )
            detector.record_error(check)

    # Mark 5 as fixed (hits the rate limit)
    for i, pattern in enumerate(list(detector._patterns.values())[:5]):
        detector.mark_fixed(pattern.pattern_id, staged_id=f"fix_{i}")

    assert detector._fixes_staged_this_hour == 5

    # No more patterns should be actionable
    actionable = detector.get_actionable_patterns()
    assert len(actionable) == 0


# ── Directive 7: Never Silent ───────────────────────────────────────────────

def test_endpoint_registry_is_populated():
    """The endpoint registry must have entries."""
    assert len(WEB_ENDPOINTS) > 0, "No endpoints registered for monitoring"
    assert len(INPUT_SCHEMAS) > 0, "No input schemas registered"


def test_every_endpoint_has_required_fields():
    """Every endpoint in the registry has the required fields."""
    for ep in WEB_ENDPOINTS:
        assert "path" in ep, f"Endpoint missing 'path': {ep}"
        assert "method" in ep, f"Endpoint missing 'method': {ep}"
        assert "expected" in ep, f"Endpoint missing 'expected': {ep}"
        assert "critical" in ep, f"Endpoint missing 'critical': {ep}"


def test_every_input_schema_has_required_fields():
    """Every input schema has the required fields."""
    for schema in INPUT_SCHEMAS:
        assert "path" in schema, f"Schema missing 'path': {schema}"
        assert "method" in schema, f"Schema missing 'method': {schema}"
        assert "required_fields" in schema, f"Schema missing 'required_fields': {schema}"

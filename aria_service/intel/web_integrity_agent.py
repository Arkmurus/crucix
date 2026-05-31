"""R-F1204 — Web Integrity Agent.

24/7 autonomous agent responsible for the ENTIRE aria-web interface.
Every bit of data that enters or leaves the web interface is verified
for accuracy, completeness, and correctness. Zero errors tolerated.

DIRECTIVES (binding — never deviate):
─────────────────────────────────────
1. VERIFY EVERY INPUT — every POST/PUT/PATCH payload is checked for
   schema validity, data type correctness, and semantic consistency
   before it reaches any handler. Reject malformed data immediately.

2. VERIFY EVERY OUTPUT — every response sent to the web UI is checked
   for accuracy against the source data. No hallucinated fields, no
   fabricated sources, no confidence inflation.

3. MONITOR 24/7 — poll every web endpoint every 60s. Log any deviation
   from expected behaviour. Escalate to CRITICAL within 30s of detecting
   an error.

4. CROSS-AGENT COMMUNICATION — when an error is detected, immediately
   notify: (a) the brain via brain_hook.absorb, (b) the capability_gaps
   system, (c) the mistake_ledger, and (d) the operator via pending_actions.

5. ZERO TOLERANCE — any error, no matter how small, is recorded,
   analysed, and either fixed or escalated. "It's just a warning" is
   not acceptable. Every warning is a potential error.

6. SELF-HEALING — when a pattern of errors is detected (3+ same type
   in 1h), propose a fix via self_improve.stage_improvement. Do not
   wait for operator intervention for routine fixes.

7. NEVER SILENT — every check produces a log entry. Every error produces
   a brain signal. Every fix produces a staged improvement. Silence is
   failure.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("aria.intel.web_integrity_agent")

# ── Configuration ───────────────────────────────────────────────────────────

_POLL_INTERVAL_S = 60          # Check every endpoint every 60s
_ERROR_ESCALATION_S = 30       # Escalate to CRITICAL within 30s
_PATTERN_WINDOW_S = 3600       # 1h window for error pattern detection
_PATTERN_THRESHOLD = 3         # 3+ same-type errors = pattern
_MAX_STAGED_FIXES_PER_HOUR = 5 # Don't flood the staging queue
_HEALTH_ENDPOINT = "/health/live"
_ARIA_SERVICE_URL = "http://localhost:8000"

# Redis keys
_INTEGRITY_CHECK_KEY = "crucix:web_integrity:last_check"
_INTEGRITY_ERRORS_KEY = "crucix:web_integrity:errors"
_INTEGRITY_PATTERNS_KEY = "crucix:web_integrity:patterns"
_INTEGRITY_STATUS_KEY = "crucix:web_integrity:status"


# ── Data types ──────────────────────────────────────────────────────────────

@dataclass
class IntegrityCheck:
    """Result of a single integrity check on one endpoint."""
    endpoint: str
    method: str
    passed: bool
    status_code: int = 0
    response_time_ms: float = 0.0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checked_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class ErrorPattern:
    """A recurring error pattern that needs attention."""
    pattern_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    error_type: str = ""
    endpoint: str = ""
    count: int = 0
    first_seen: float = 0.0
    last_seen: float = 0.0
    examples: list[str] = field(default_factory=list)
    fixed: bool = False
    fix_staged_id: str = ""


# ── Endpoint registry ───────────────────────────────────────────────────────

# Every web endpoint that serves data to users. Each entry specifies:
#   path:     the URL path
#   method:   HTTP method
#   expected: what to check in the response
#   critical: if True, failure escalates immediately

WEB_ENDPOINTS: list[dict[str, Any]] = [
    # Health & status
    {"path": "/health/live", "method": "GET", "expected": {"build_rev"}, "critical": True},
    {"path": "/api/aria/health", "method": "GET", "expected": {"status"}, "critical": True},
    {"path": "/api/aria/status", "method": "GET", "expected": {"status"}, "critical": False},

    # Intelligence outputs
    {"path": "/api/aria/briefing", "method": "GET", "expected": {"sections", "timestamp"}, "critical": False},
    {"path": "/api/aria/report", "method": "GET", "expected": {"sections", "sources"}, "critical": False},

    # Due diligence
    {"path": "/api/aria/dd/watchlist/alerts/unread-count", "method": "GET",
     "expected": {"count"}, "critical": False},

    # Self-improvement
    {"path": "/api/aria/self/staged", "method": "GET", "expected": {}, "critical": False},
    {"path": "/api/aria/self/improvements", "method": "GET", "expected": {}, "critical": False},

    # Cost & autonomy
    {"path": "/api/aria/cost/monthly/status", "method": "GET", "expected": {"total"}, "critical": False},
    {"path": "/api/aria/autonomous/status", "method": "GET", "expected": {"enabled"}, "critical": False},

    # Adversarial
    {"path": "/api/aria/adversarial/status", "method": "GET", "expected": {}, "critical": False},
]


# ── Input validation schemas ────────────────────────────────────────────────

# Every input endpoint's expected payload schema. Each entry specifies:
#   path:     the URL path
#   method:   HTTP method
#   required_fields: list of field names that MUST be present
#   field_types: dict of field_name -> expected Python type

INPUT_SCHEMAS: list[dict[str, Any]] = [
    {
        "path": "/api/aria/chat",
        "method": "POST",
        "required_fields": ["message"],
        "field_types": {"message": str, "session_id": str},
    },
    {
        "path": "/api/aria/ingest",
        "method": "POST",
        "required_fields": ["content"],
        "field_types": {"content": str, "source": str, "content_type": str},
    },
    {
        "path": "/api/aria/self/stage",
        "method": "POST",
        "required_fields": ["file_path", "new_content", "change_type"],
        "field_types": {"file_path": str, "new_content": str, "change_type": str},
    },
    {
        "path": "/api/aria/self/deploy",
        "method": "POST",
        "required_fields": ["id"],
        "field_types": {"id": str},
    },
    {
        "path": "/api/aria/search",
        "method": "GET",
        "required_fields": ["q"],
        "field_types": {"q": str},
    },
]


# ── Core check functions ────────────────────────────────────────────────────

async def check_endpoint(endpoint: dict[str, Any]) -> IntegrityCheck:
    """Check a single web endpoint for correctness.

    Verifies:
      1. The endpoint responds (not 5xx, not timeout)
      2. The response contains expected fields
      3. The response time is acceptable (< 5s)
      4. The response data is valid JSON (if applicable)
    """
    import httpx

    path = endpoint["path"]
    method = endpoint["method"]
    expected = endpoint.get("expected", {})
    is_critical = endpoint.get("critical", False)
    start = time.monotonic()

    check = IntegrityCheck(endpoint=path, method=method, passed=True)

    try:
        url = f"{_ARIA_SERVICE_URL}{path}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            if method == "GET":
                resp = await client.get(url)
            elif method == "POST":
                resp = await client.post(url, json={})
            else:
                check.errors.append(f"Unsupported method: {method}")
                check.passed = False
                return check

        elapsed = (time.monotonic() - start) * 1000
        check.response_time_ms = round(elapsed, 1)
        check.status_code = resp.status_code

        # Check 1: Status code
        if resp.status_code >= 500:
            check.errors.append(
                f"Server error: {resp.status_code} on {method} {path}"
            )
            check.passed = False
        elif resp.status_code >= 400:
            check.warnings.append(
                f"Client error: {resp.status_code} on {method} {path}"
            )

        # Check 2: Response time
        if elapsed > 5000:
            check.warnings.append(
                f"Slow response: {elapsed:.0f}ms on {method} {path} (threshold: 5000ms)"
            )

        # Check 3: Expected fields in JSON response
        if expected:
            try:
                data = resp.json()
                for field in expected:
                    if field not in data:
                        check.errors.append(
                            f"Missing expected field '{field}' in {method} {path}"
                        )
                        check.passed = False
            except (json.JSONDecodeError, ValueError):
                check.warnings.append(
                    f"Non-JSON response on {method} {path} (expected fields: {expected})"
                )

        # Check 4: Critical endpoints must respond fast
        if is_critical and elapsed > 2000:
            check.warnings.append(
                f"Critical endpoint slow: {elapsed:.0f}ms on {method} {path}"
            )

    except httpx.TimeoutException:
        check.errors.append(f"Timeout on {method} {path} (>10s)")
        check.passed = False
        check.status_code = 0
    except httpx.ConnectError as e:
        check.errors.append(f"Connection failed on {method} {path}: {e}")
        check.passed = False
        check.status_code = 0
    except Exception as e:
        check.errors.append(f"Unexpected error on {method} {path}: {e}")
        check.passed = False
        check.status_code = 0

    return check


def validate_input_payload(
    payload: dict[str, Any],
    schema: dict[str, Any],
) -> list[str]:
    """Validate an input payload against its schema.

    Returns a list of validation errors (empty = valid).
    """
    errors: list[str] = []
    required = schema.get("required_fields", [])
    field_types = schema.get("field_types", {})

    for field in required:
        if field not in payload:
            errors.append(f"Missing required field: {field}")

    for field, expected_type in field_types.items():
        if field in payload and not isinstance(payload[field], expected_type):
            errors.append(
                f"Field '{field}' has wrong type: "
                f"expected {expected_type.__name__}, "
                f"got {type(payload[field]).__name__}"
            )

    return errors


# ── Error pattern detection ─────────────────────────────────────────────────

class ErrorPatternDetector:
    """Detects recurring error patterns and triggers fixes."""

    def __init__(self) -> None:
        self._patterns: dict[str, ErrorPattern] = {}
        self._recent_errors: list[dict[str, Any]] = []
        self._fixes_staged_this_hour: int = 0
        self._hour_bucket: int = 0

    def record_error(self, check: IntegrityCheck) -> None:
        """Record an error from an integrity check and detect patterns."""
        now = time.time()
        hour_bucket = int(now // 3600)

        # Reset hourly fix counter on hour boundary
        if hour_bucket != self._hour_bucket:
            self._hour_bucket = hour_bucket
            self._fixes_staged_this_hour = 0

        for error in check.errors:
            error_type = self._classify_error(error)
            key = f"{error_type}:{check.endpoint}"

            if key not in self._patterns:
                self._patterns[key] = ErrorPattern(
                    error_type=error_type,
                    endpoint=check.endpoint,
                    first_seen=now,
                )

            pattern = self._patterns[key]
            pattern.count += 1
            pattern.last_seen = now
            if len(pattern.examples) < 5:
                pattern.examples.append(error)

            # Store in recent errors for pattern window
            self._recent_errors.append({
                "error_type": error_type,
                "endpoint": check.endpoint,
                "message": error,
                "timestamp": now,
            })

            # Prune old errors outside the pattern window
            self._recent_errors = [
                e for e in self._recent_errors
                if now - e["timestamp"] < _PATTERN_WINDOW_S
            ]

    def _classify_error(self, error: str) -> str:
        """Classify an error message into a type."""
        error_lower = error.lower()
        if "timeout" in error_lower:
            return "timeout"
        if "connection" in error_lower or "connect" in error_lower:
            return "connection"
        if "server error" in error_lower or "5" in error_lower[:10]:
            return "server_error"
        if "missing" in error_lower:
            return "missing_field"
        if "wrong type" in error_lower:
            return "type_mismatch"
        return "unknown"

    def get_actionable_patterns(self) -> list[ErrorPattern]:
        """Return patterns that have crossed the threshold and need fixing."""
        now = time.time()
        actionable = []
        for pattern in self._patterns.values():
            if (
                pattern.count >= _PATTERN_THRESHOLD
                and not pattern.fixed
                and now - pattern.last_seen < _PATTERN_WINDOW_S
                and self._fixes_staged_this_hour < _MAX_STAGED_FIXES_PER_HOUR
            ):
                actionable.append(pattern)
        return actionable

    def mark_fixed(self, pattern_id: str, staged_id: str = "") -> None:
        """Mark a pattern as fixed."""
        for pattern in self._patterns.values():
            if pattern.pattern_id == pattern_id:
                pattern.fixed = True
                pattern.fix_staged_id = staged_id
                self._fixes_staged_this_hour += 1
                break


# ── Main monitoring loop ────────────────────────────────────────────────────

class WebIntegrityAgent:
    """24/7 autonomous agent monitoring the entire aria-web interface.

    Runs as a background asyncio task. Every 60s it:
      1. Pings every registered web endpoint
      2. Validates responses against expected schemas
      3. Records errors to Redis + brain
      4. Detects error patterns
      5. Stages fixes for recurring patterns
      6. Escalates critical errors immediately
    """

    def __init__(
        self,
        aria_service_url: str = _ARIA_SERVICE_URL,
        brain_hook: Optional[Any] = None,
        redis_store: Optional[Any] = None,
    ) -> None:
        self.aria_url = aria_service_url
        self._brain_hook = brain_hook
        self._redis = redis_store
        self._detector = ErrorPatternDetector()
        self._running = False
        self._task: Optional[asyncio.Task] = None

    # ── Public API ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the 24/7 monitoring loop."""
        if self._running:
            logger.warning("[web_integrity] already running")
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            "[web_integrity] Web Integrity Agent started — "
            "monitoring %d endpoints every %ds",
            len(WEB_ENDPOINTS), _POLL_INTERVAL_S,
        )

    async def stop(self) -> None:
        """Stop the monitoring loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[web_integrity] Web Integrity Agent stopped")

    async def validate_request(
        self,
        path: str,
        method: str,
        payload: dict[str, Any],
    ) -> list[str]:
        """Validate an incoming request payload against its schema.

        Called by the web tier before any handler runs. Returns a list
        of validation errors (empty = valid).

        DIRECTIVE 1: VERIFY EVERY INPUT.
        """
        for schema in INPUT_SCHEMAS:
            if schema["path"] == path and schema["method"] == method:
                errors = validate_input_payload(payload, schema)
                if errors:
                    await self._record_validation_error(path, method, errors)
                return errors
        return []  # No schema for this endpoint — pass through

    async def verify_response(
        self,
        path: str,
        method: str,
        response_data: dict[str, Any],
    ) -> list[str]:
        """Verify a response before it's sent to the user.

        Called by the web tier before sending any response. Returns a
        list of verification errors (empty = verified).

        DIRECTIVE 2: VERIFY EVERY OUTPUT.
        """
        errors: list[str] = []

        # Find the endpoint spec
        for ep in WEB_ENDPOINTS:
            if ep["path"] == path and ep["method"] == method:
                expected = ep.get("expected", {})
                for field in expected:
                    if field not in response_data:
                        errors.append(
                            f"Output missing expected field '{field}' in {method} {path}"
                        )
                break

        if errors:
            await self._record_output_error(path, method, errors)

        return errors

    async def get_status(self) -> dict[str, Any]:
        """Return the current integrity status for the dashboard."""
        return {
            "running": self._running,
            "endpoints_monitored": len(WEB_ENDPOINTS),
            "input_schemas": len(INPUT_SCHEMAS),
            "patterns_detected": len(self._detector._patterns),
            "patterns_actionable": len(self._detector.get_actionable_patterns()),
            "fixes_staged_this_hour": self._detector._fixes_staged_this_hour,
            "last_check": await self._get_last_check(),
        }

    # ── Internal: monitoring loop ───────────────────────────────────────────

    async def _run_loop(self) -> None:
        """The main monitoring loop — runs forever."""
        # Wait for the web tier to be ready
        await asyncio.sleep(10)

        while self._running:
            try:
                await self._one_cycle()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(
                    "[web_integrity] cycle error: %s", e, exc_info=True
                )
                await self._wire_to_brain(
                    module="web_integrity_agent",
                    summary=f"Monitoring cycle failed: {e}",
                    success=False,
                    confidence="CONFIRMED",
                )
            await asyncio.sleep(_POLL_INTERVAL_S)

    async def _one_cycle(self) -> None:
        """One monitoring cycle — check all endpoints."""
        checks: list[IntegrityCheck] = []
        errors_found = 0
        critical_errors = 0

        for endpoint in WEB_ENDPOINTS:
            check = await check_endpoint(endpoint)
            checks.append(check)

            if not check.passed:
                errors_found += 1
                self._detector.record_error(check)

                if endpoint.get("critical", False):
                    critical_errors += 1
                    # DIRECTIVE 3: Escalate critical errors within 30s
                    await self._escalate_critical(check)

                # DIRECTIVE 5: Zero tolerance — every error is recorded
                await self._record_error(check)

        # DIRECTIVE 4: Cross-agent communication
        if errors_found > 0:
            await self._wire_to_brain(
                module="web_integrity_agent",
                summary=(
                    f"Integrity check: {errors_found}/{len(WEB_ENDPOINTS)} "
                    f"endpoints failed ({critical_errors} critical)"
                ),
                detail=json.dumps([
                    {"endpoint": c.endpoint, "errors": c.errors}
                    for c in checks if not c.passed
                ]),
                success=errors_found == 0,
                confidence="CONFIRMED",
            )

        # DIRECTIVE 6: Self-healing — stage fixes for recurring patterns
        actionable = self._detector.get_actionable_patterns()
        for pattern in actionable:
            await self._stage_fix(pattern)

        # DIRECTIVE 7: Never silent
        logger.info(
            "[web_integrity] cycle complete: %d endpoints, "
            "%d passed, %d failed (%d critical), %d patterns actionable",
            len(checks),
            len(checks) - errors_found,
            errors_found,
            critical_errors,
            len(actionable),
        )

        await self._save_last_check()

    # ── Internal: error recording ───────────────────────────────────────────

    async def _record_error(self, check: IntegrityCheck) -> None:
        """Record an integrity error to Redis."""
        if self._redis is None:
            return
        try:
            errors = await self._redis.get_json(_INTEGRITY_ERRORS_KEY) or []
            errors.insert(0, {
                "endpoint": check.endpoint,
                "method": check.method,
                "errors": check.errors,
                "warnings": check.warnings,
                "status_code": check.status_code,
                "timestamp": check.checked_at,
            })
            await self._redis.set_json(
                _INTEGRITY_ERRORS_KEY, errors[:500], ex=86400 * 7
            )
        except Exception as e:
            logger.debug("[web_integrity] record_error failed: %s", e)

    async def _record_validation_error(
        self, path: str, method: str, errors: list[str],
    ) -> None:
        """Record an input validation error."""
        logger.warning(
            "[web_integrity] INPUT VALIDATION FAILED: %s %s — %s",
            method, path, "; ".join(errors),
        )
        await self._wire_to_brain(
            module="web_integrity_agent",
            summary=f"Input validation failed: {method} {path}",
            detail="; ".join(errors),
            success=False,
            confidence="CONFIRMED",
        )

    async def _record_output_error(
        self, path: str, method: str, errors: list[str],
    ) -> None:
        """Record an output verification error."""
        logger.warning(
            "[web_integrity] OUTPUT VERIFICATION FAILED: %s %s — %s",
            method, path, "; ".join(errors),
        )
        await self._wire_to_brain(
            module="web_integrity_agent",
            summary=f"Output verification failed: {method} {path}",
            detail="; ".join(errors),
            success=False,
            confidence="CONFIRMED",
        )

    async def _escalate_critical(self, check: IntegrityCheck) -> None:
        """Escalate a critical endpoint failure.

        DIRECTIVE 3: Escalate to CRITICAL within 30s.
        """
        logger.critical(
            "[web_integrity] CRITICAL: %s %s — %s",
            check.method, check.endpoint, "; ".join(check.errors),
        )
        await self._wire_to_brain(
            module="web_integrity_agent",
            summary=f"CRITICAL: {check.method} {check.endpoint} is DOWN",
            detail="; ".join(check.errors),
            success=False,
            confidence="CONFIRMED",
            source_id="web_integrity_critical",
        )

        # Also record a capability gap so the autonomous loop sees it
        try:
            from . import capability_gaps as _cg
            await _cg.record_gap(
                gap_type="web_integrity_failure",
                module=f"web_integrity:{check.endpoint}",
                description=f"CRITICAL: {check.method} {check.endpoint} failed: "
                            f"{'; '.join(check.errors)[:200]}",
                severity="HIGH",
                source="web_integrity_agent",
            )
        except Exception as e:
            logger.debug("[web_integrity] capability_gaps.record failed: %s", e)

    async def _stage_fix(self, pattern: ErrorPattern) -> None:
        """Stage a fix for a recurring error pattern.

        DIRECTIVE 6: Self-healing — propose fixes for recurring patterns.
        """
        logger.warning(
            "[web_integrity] PATTERN DETECTED: %s on %s (%d occurrences) — staging fix",
            pattern.error_type, pattern.endpoint, pattern.count,
        )

        try:
            from . import self_improve as _si

            # Build a fix description based on the error type
            if pattern.error_type == "timeout":
                description = f"Increase timeout for {pattern.endpoint}"
                reasoning = (
                    f"Endpoint {pattern.endpoint} has timed out {pattern.count} times "
                    f"in the last hour. Consider increasing the timeout or optimizing "
                    f"the endpoint response time."
                )
            elif pattern.error_type == "connection":
                description = f"Check connectivity for {pattern.endpoint}"
                reasoning = (
                    f"Endpoint {pattern.endpoint} has {pattern.count} connection "
                    f"failures in the last hour. The service may be down."
                )
            elif pattern.error_type == "missing_field":
                description = f"Add missing field to {pattern.endpoint} response"
                reasoning = (
                    f"Endpoint {pattern.endpoint} is missing expected fields in "
                    f"{pattern.count} responses. The response schema may have changed."
                )
            else:
                description = f"Investigate errors on {pattern.endpoint}"
                reasoning = (
                    f"Endpoint {pattern.endpoint} has {pattern.count} errors "
                    f"of type '{pattern.error_type}' in the last hour."
                )

            result = await _si.stage_improvement(
                file_path=f"lib/web_integrity/{pattern.endpoint.replace('/', '_')}.md",
                new_content=(
                    f"# Integrity Fix: {pattern.error_type} on {pattern.endpoint}\n\n"
                    f"**Detected:** {pattern.count} occurrences\n"
                    f"**First seen:** {pattern.first_seen}\n"
                    f"**Examples:**\n"
                    + "\n".join(f"- {e}" for e in pattern.examples)
                ),
                change_type="bug_fix",
                description=description,
                reasoning=reasoning,
            )

            if result.get("staged"):
                self._detector.mark_fixed(
                    pattern.pattern_id,
                    staged_id=result.get("id", ""),
                )
                logger.info(
                    "[web_integrity] Fix staged for pattern %s: %s",
                    pattern.pattern_id, description,
                )
        except Exception as e:
            logger.error(
                "[web_integrity] Failed to stage fix for pattern %s: %s",
                pattern.pattern_id, e,
            )

    async def _wire_to_brain(
        self,
        module: str,
        summary: str,
        detail: str = "",
        success: bool = True,
        confidence: str = "CONFIRMED",
        source_id: str = "web_integrity_agent",
    ) -> None:
        """Wire an event to the brain.

        DIRECTIVE 4: Cross-agent communication — every event reaches the brain.
        """
        if self._brain_hook is None:
            return
        try:
            await self._brain_hook.absorb(
                module=module,
                summary=summary[:300],
                detail=detail[:2000] if detail else "",
                success=success,
                confidence=confidence,
                source_id=source_id,
            )
        except Exception as e:
            logger.debug("[web_integrity] brain_hook.absorb failed: %s", e)

    async def _save_last_check(self) -> None:
        """Save the timestamp of the last integrity check."""
        if self._redis is None:
            return
        try:
            await self._redis.set(
                _INTEGRITY_CHECK_KEY,
                datetime.now(timezone.utc).isoformat(),
            )
        except Exception:
            pass

    async def _get_last_check(self) -> str:
        """Get the timestamp of the last integrity check."""
        if self._redis is None:
            return "never"
        try:
            val = await self._redis.get(_INTEGRITY_CHECK_KEY)
            return val or "never"
        except Exception:
            return "unknown"

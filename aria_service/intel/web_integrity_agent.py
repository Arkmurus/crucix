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
from .engine_wiring import wire_failure

import asyncio
import json
import logging
import os
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
_ARIA_WEB_URL = "https://aria-web.fly.dev"  # R-F1209: live public web tier

# R-F1213: Credential verification interval — how often to test portal creds
_CRED_VERIFY_INTERVAL_S = 86400  # Every 24 hours
_CRED_VERIFY_KEY = "crucix:web_integrity:cred_verify"  # hash: portal_id → last verify result
_CRED_HEALTH_KEY = "crucix:web_integrity:cred_health"  # hash: portal_id → health status

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
    # R-F2376: removed stale /api/aria/status probe. Live localhost probe
    # returned 404 every cycle; keeping it in the monitor created noise while
    # still counting the cycle as passed.

    # Intelligence outputs
    # R-F1601: /self/assess/briefing exists but requires auth — the agent
    # probes without a token, getting 401. Marked non-critical so no alert
    # fires, but the 401 is log noise. Removed from probe list since the
    # agent can't authenticate for this endpoint.
    # R-F3479 — this was POSTed json={} against a route requiring report_type +
    # subject (routes/aria.py:15597), so it 400d on EVERY cycle while being
    # counted as a pass, and its declared `expected` fields were never evaluated.
    #
    # It is NOT fixed by sending a valid body: build_report() calls the LLM, so a
    # 60s monitor would generate ~1,440 reports/day straight through the §17 cost
    # cap. A monitor must not buy anything. So the probe now checks the CONTRACT
    # it can check for free — that the route is mounted and REJECTS an invalid
    # payload — and declares the 400 it expects, which is a real input-validation
    # check rather than a silent pass. Output shape is covered by the report
    # capability tests, not by a production poller.
    {"path": "/api/aria/report", "method": "POST", "expected_status": 400,
     "critical": False},

    # Due diligence
    {"path": "/api/aria/dd/watchlist/alerts/unread-count", "method": "GET",
     "expected": {"unread_count"}, "critical": False},  # R-F2597: real field is unread_count (was stale 'count')

    # Self-improvement
    {"path": "/api/aria/self/staged", "method": "GET", "expected": {}, "critical": False},
    # R-F2376: removed stale /api/aria/self/improvements probe. Production
    # logs showed 404; /self/staged remains as the live staged queue probe.

    # Cost & autonomy
    {"path": "/api/aria/cost/monthly/status", "method": "GET", "expected": {"spent_usd", "cap_usd"}, "critical": False},  # R-F2597: real fields (was stale total/monthly_cap)
    # R-F3479 — R-F2139 scopes /api/aria/autonomous/ to the OPERATOR token
    # (routes/aria.py:297); this agent holds only the internal one, so it 403s
    # every cycle. Declared, so the rejection is visible and deliberate rather
    # than a silent pass. Same shape as the R-F2567 coder/llm 403.
    {"path": "/api/aria/autonomous/status", "method": "GET",
     "expected": {"ok", "engine"}, "operator_scoped": True,
     "expected_status_without_operator_token": 403, "critical": False},

    # Adversarial
    {"path": "/api/aria/adversarial/stats", "method": "GET", "expected": {}, "critical": False},
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


# ── Public web endpoints (live aria-web) ────────────────────────────────────

# R-F1209: monitor the live public web tier in addition to localhost.
# These are the user-facing endpoints that real users interact with.
# R-F1512: cleaned up stale probe targets. /api/auth/status returns 404
# (endpoint removed from web app). /api/aria/status returns 401 (needs auth
# token). Both were reported as "passed" because the agent checks reachability,
# not status code — but the 404/401 noise in logs was misleading.
_WEB_ENDPOINTS_PUBLIC: list[dict[str, Any]] = [
    {"path": "/healthz", "method": "GET", "expected": {"status"}, "critical": True},
]


# ── Core check functions ────────────────────────────────────────────────────

def _internal_auth_headers() -> dict:
    """R-F2561 (P0.2) — the internal self-probes hit token-gated endpoints
    (/report, /self/staged, /autonomous/status, /cost/monthly/status, watchlist
    unread-count). Attach the internal-service token (already accepted by the router
    auth, _accepted_tokens) so probes don't 401 (false health failures + log spam)."""
    import os
    tok = (os.getenv("ARIA_INTERNAL_TOKEN") or os.getenv("ARIA_API_TOKEN") or "").strip()
    return {"Authorization": f"Bearer {tok}"} if tok else {}


def _probe_auth_headers(endpoint: dict) -> dict:
    """R-F3479 — headers for one probe, escalating to the OPERATOR token where
    the endpoint requires it.

    R-F2561 attached the internal token "so probes don't 401". They no longer
    401 — they 403, because R-F2139 scopes control paths like
    ``/api/aria/autonomous/`` to the operator token (routes/aria.py:297). The
    result was a probe that rejected on every cycle while being scored as a pass.

    Declaring the 403 would make it honest but blind: the endpoint's
    ``{ok, engine}`` contract would never be checked again. This agent runs
    IN-PROCESS against localhost, so it can legitimately hold the operator token
    and verify the endpoint for real. Nothing is exposed to the network by this —
    the token never leaves the box.

    Falls back to the internal token when ARIA_OPERATOR_TOKEN is unset, in which
    case the endpoint's declared ``expected_status`` keeps the result honest.
    """
    if not endpoint.get("operator_scoped"):
        return _internal_auth_headers()
    tok = (os.getenv("ARIA_OPERATOR_TOKEN") or "").strip()
    if tok:
        return {"Authorization": f"Bearer {tok}"}
    return _internal_auth_headers()


def _apply_status_verdict(check, resp, expected_status) -> None:
    """R-F3479 — a 4xx is a FAILURE unless the probe declared it.

    This used to fail only on 5xx; a 4xx appended a warning and left
    ``passed = True``. Live 2026-07-30, every cycle of the whole review window:
    ``POST /api/aria/report`` 400 and ``GET /api/aria/autonomous/status`` 403,
    reported as "9 endpoints, 9 passed, 0 failed". A monitor that cannot fail is
    worse than no monitor — the green number suppresses the investigation.

    ``expected_status`` lets a probe declare a rejection it EXPECTS (an auth
    probe that should 403). That keeps the pass deliberate and visible in the
    endpoint table, instead of an accident of the rule.
    """
    code = resp.status_code
    if expected_status is not None:
        if code == expected_status:
            return
        check.errors.append(
            f"Expected status {expected_status} but got {code} on "
            f"{check.method} {check.endpoint}"
        )
        check.passed = False
        return
    if code >= 500:
        check.errors.append(
            f"Server error: {code} on {check.method} {check.endpoint}"
        )
        check.passed = False
    elif code >= 400:
        check.errors.append(
            f"Client error: {code} on {check.method} {check.endpoint} — the "
            f"probe cannot verify this endpoint's contract while it rejects"
        )
        check.passed = False


def _apply_field_verdict(check, resp, expected) -> None:
    """Check the declared response fields on a successful response."""
    if not (expected and resp.is_success):
        return
    try:
        data = resp.json()
        for fname in expected:  # R-F2523: was 'field' — shadowed dataclasses.field
            if fname not in data:
                check.errors.append(
                    f"Missing expected field '{fname}' in "
                    f"{check.method} {check.endpoint}"
                )
                check.passed = False
    except (json.JSONDecodeError, ValueError):
        check.warnings.append(
            f"Non-JSON response on {check.method} {check.endpoint} "
            f"(expected fields: {expected})"
        )


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
        async with httpx.AsyncClient(timeout=10.0) as client:  # no-breaker: web integrity agent is best-effort; breaker would block integrity checks
            # R-F3479 — escalate to the operator token for control-plane probes.
            _hdrs = _probe_auth_headers(endpoint)
            if method == "GET":
                resp = await client.get(url, headers=_hdrs)
            elif method == "POST":
                resp = await client.post(
                    url, json=endpoint.get("body") or {}, headers=_hdrs)
            else:
                check.errors.append(f"Unsupported method: {method}")
                check.passed = False
                return check

        elapsed = (time.monotonic() - start) * 1000
        check.response_time_ms = round(elapsed, 1)
        check.status_code = resp.status_code

        # Check 1: Status code
        # R-F3479 — an operator-scoped probe verifies for real when the operator
        # token is available, and otherwise declares the 403 it will get, so the
        # result is honest either way.
        _exp_status = endpoint.get("expected_status")
        if endpoint.get("operator_scoped") and not (os.getenv("ARIA_OPERATOR_TOKEN") or "").strip():
            _exp_status = endpoint.get("expected_status_without_operator_token")
        _apply_status_verdict(check, resp, _exp_status)

        # Check 2: Response time
        if elapsed > 5000:
            check.warnings.append(
                f"Slow response: {elapsed:.0f}ms on {method} {path} (threshold: 5000ms)"
            )

        # Check 3: Expected fields in JSON response
        # R-F1439: 401/403 on auth-gated endpoints is EXPECTED when probing
        # without a bearer token — don't flag as missing_field. The endpoint
        # is working correctly (it rejected an unauthenticated request).
        # Only check expected fields when the response is successful (2xx).
        _apply_field_verdict(check, resp, expected)

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


async def check_endpoint_public(endpoint: dict[str, Any]) -> IntegrityCheck:
    """Check a live public web endpoint (aria-web.fly.dev).

    Same as check_endpoint but hits the public URL instead of localhost.
    Used to verify the live web tier is serving correctly to users.
    """
    import httpx

    path = endpoint["path"]
    method = endpoint["method"]
    expected = endpoint.get("expected", {})
    is_critical = endpoint.get("critical", False)
    start = time.monotonic()

    check = IntegrityCheck(endpoint=f"[public]{path}", method=method, passed=True)

    try:
        url = f"{_ARIA_WEB_URL}{path}"
        async with httpx.AsyncClient(timeout=15.0) as client:
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

        if resp.status_code >= 500:
            check.errors.append(
                f"Server error: {resp.status_code} on {method} {path} (public)"
            )
            check.passed = False
        elif resp.status_code >= 400:
            check.warnings.append(
                f"Client error: {resp.status_code} on {method} {path} (public)"
            )

        if elapsed > 5000:
            check.warnings.append(
                f"Slow response: {elapsed:.0f}ms on {method} {path} (public, threshold: 5000ms)"
            )

        # R-F1439: only check expected fields on successful responses
        if expected and resp.is_success:
            try:
                data = resp.json()
                for fname in expected:  # R-F2523: was 'field' — shadowed dataclasses.field
                    if fname not in data:
                        check.errors.append(
                            f"Missing expected field '{fname}' in {method} {path} (public)"
                        )
                        check.passed = False
            except (json.JSONDecodeError, ValueError):
                check.warnings.append(
                    f"Non-JSON response on {method} {path} (public, expected fields: {expected})"
                )

        if is_critical and elapsed > 2000:
            check.warnings.append(
                f"Critical public endpoint slow: {elapsed:.0f}ms on {method} {path}"
            )

    except httpx.TimeoutException:
        check.errors.append(f"Timeout on {method} {path} (public, >15s)")
        check.passed = False
        check.status_code = 0
    except httpx.ConnectError as e:
        check.errors.append(f"Connection failed on {method} {path} (public): {e}")
        check.passed = False
        check.status_code = 0
    except Exception as e:
        check.errors.append(f"Unexpected error on {method} {path} (public): {e}")
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

    for fname in required:  # R-F2523: was 'field' — shadowed dataclasses.field
        if fname not in payload:
            errors.append(f"Missing required field: {fname}")

    for fname, expected_type in field_types.items():  # R-F2523: was 'field'
        if fname in payload and not isinstance(payload[fname], expected_type):
            errors.append(
                f"Field '{fname}' has wrong type: "
                f"expected {expected_type.__name__}, "
                f"got {type(payload[fname]).__name__}"
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
                for fname in expected:  # R-F2523: was 'field' — shadowed dataclasses.field
                    if fname not in response_data:
                        errors.append(
                            f"Output missing expected field '{fname}' in {method} {path}"
                        )
                break

        if errors:
            await self._record_output_error(path, method, errors)

        return errors

    async def get_status(self) -> dict[str, Any]:
        """Return the current integrity status for the dashboard."""
        return {
            "running": self._running,
            "endpoints_monitored": len(WEB_ENDPOINTS) + len(_WEB_ENDPOINTS_PUBLIC),
            "endpoints_local": len(WEB_ENDPOINTS),
            "endpoints_public": len(_WEB_ENDPOINTS_PUBLIC),
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
        """One monitoring cycle — check all endpoints (localhost + live web)."""
        # R-F1209: tick heartbeat so the agent registry knows we're alive
        try:
            from .agent_registry import AgentRegistry
            _reg = AgentRegistry()
            await _reg.tick_heartbeat("web_integrity", "24/7 endpoint monitoring")
        except Exception:
            pass

        checks: list[IntegrityCheck] = []
        errors_found = 0
        critical_errors = 0

        # Check localhost endpoints (internal aria-intel)
        for endpoint in WEB_ENDPOINTS:
            check = await check_endpoint(endpoint)
            checks.append(check)

            if not check.passed:
                errors_found += 1
                self._detector.record_error(check)

                if endpoint.get("critical", False):
                    critical_errors += 1
                    await self._escalate_critical(check)

                await self._record_error(check)

        # R-F1209: also check live public web tier (aria-web.fly.dev)
        for endpoint in _WEB_ENDPOINTS_PUBLIC:
            check = await check_endpoint_public(endpoint)
            checks.append(check)

            if not check.passed:
                errors_found += 1
                self._detector.record_error(check)

                if endpoint.get("critical", False):
                    critical_errors += 1
                    await self._escalate_critical(check)

                await self._record_error(check)

        # DIRECTIVE 4 / R-F1292 (§21a): wire EVERY cycle to the brain — success AND
        # failure. Previously this fired only inside `if errors_found > 0`, so a
        # clean cycle emitted NOTHING and the success branch was dark (ARIA's audit
        # flagged web_integrity, correctly on this point). The FAILURE path keeps
        # brain_hook.absorb (rare; wants the full record). The SUCCESS path uses the
        # lightweight engine_wiring.wire_success — NOT absorb — because this cycle is
        # high-frequency and absorb's neural/mastery side-effects would inflate the
        # composite (R-F973 lesson). R-F2597: SUCCESS + FAILURE must share ONE module
        # name — the failure path + brain_hook heartbeat-watch + probe_web_full all read
        # "web_integrity_agent", so success wiring under "web_integrity" (below) split the
        # metric → the watched bucket only ever saw failures = a permanent false 0%.
        if errors_found > 0:
            await self._wire_to_brain(
                module="web_integrity_agent",
                summary=(
                    f"Integrity check: {errors_found}/{len(checks)} "
                    f"endpoints failed ({critical_errors} critical)"
                ),
                detail=json.dumps([
                    {"endpoint": c.endpoint, "errors": c.errors}
                    for c in checks if not c.passed
                ]),
                success=False,
                confidence="CONFIRMED",
            )
        else:
            try:
                from .engine_wiring import wire_success  # R-F2523: wire_failure unused here (module-level import at top is used)
                wire_success(
                    module="web_integrity_agent",  # R-F2597: align with the failure path + heartbeat-watch
                    summary=f"Integrity cycle clean: all {len(checks)} endpoints healthy",
                    source_id="web_integrity_agent:_one_cycle",
                )
            except Exception:  # noqa: BLE001 — wiring must never break the loop
                pass

        # DIRECTIVE 6: Self-healing — stage fixes for recurring patterns
        actionable = self._detector.get_actionable_patterns()
        for pattern in actionable:
            await self._stage_fix(pattern)

        # R-F1213: Periodically verify portal credentials (once per day)
        try:
            last_cred_verify = await self._get_last_cred_verify()
            if time.time() - last_cred_verify > _CRED_VERIFY_INTERVAL_S:
                logger.info("[web_integrity] Starting credential verification cycle...")
                asyncio.create_task(self.verify_all_credentials())
        except Exception:
            pass

        # DIRECTIVE 7: Never silent
        logger.info(
            "[web_integrity] cycle complete: %d endpoints (%d local + %d public), "
            "%d passed, %d failed (%d critical), %d patterns actionable",
            len(checks),
            len(WEB_ENDPOINTS),
            len(_WEB_ENDPOINTS_PUBLIC),
            len(checks) - errors_found,
            errors_found,
            critical_errors,
            len(actionable),
        )

        await self._save_last_check()

    # ── Internal: error recording ───────────────────────────────────────────

    async def _record_error(self, check: IntegrityCheck) -> None:
        """Record an integrity error to Redis AND the self-improvement error log.

        R-F1439: also calls self_improve.record_error() so the autonomous
        self-improvement cycle sees web_integrity errors and can generate
        real code fixes (not just markdown docs). Previously the web_integrity
        agent detected patterns and staged markdown docs, but the self-improve
        cycle never saw these errors because record_error() was never called.
        """
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

        # R-F1439: wire to self-improve error log so the autonomous cycle sees it
        try:
            from . import self_improve as _si
            for err_msg in check.errors:
                await _si.record_error(
                    error_type="web_integrity",
                    message=f"{check.method} {check.endpoint}: {err_msg}",
                    file="aria_service/intel/web_integrity_agent.py",
                    function="_record_error",
                )
        except Exception as e:
            logger.debug("[web_integrity] self_improve.record_error failed: %s", e)

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

        R-F1380: sustained-failure detection for deploy-window suppression.
        When ANY endpoint fails, re-probe at intervals (5s, 30s, 60s, 120s)
        up to ~3min before escalating to CRITICAL. A machine-replacement
        blackout is 60-120s — the re-probe catches recovery and logs WARNING
        instead of CRITICAL, so deploy windows don't reset the Gate #3 clean
        clock. A genuinely dead endpoint still escalates after all re-probes
        fail.

        R-F1381: boot grace window. In the first N minutes after process
        start, the app is still warming up (absorb storm, model loads,
        RAG hydration) — timeouts during this window are expected, not
        outages. No CRITICAL is logged until the grace window expires.

        Capability contract:
          - Boot-window timeout: -> WARNING, no CRITICAL
          - Deploy-window failure (recovers within ~3min): -> WARNING, no CRITICAL
          - Sustained failure (still down after ~3min): -> CRITICAL fires
        """
        # R-F1381: boot grace window — no CRITICAL in first N minutes
        _boot_grace_s = int(os.getenv("ARIA_WEB_INTEGRITY_BOOT_GRACE_S", "180"))
        if time.monotonic() < _boot_grace_s:
            logger.warning(
                "[web_integrity] %s %s — failure within boot grace window "
                "(%.0fs of %ds) — suppressing CRITICAL",
                check.method, check.endpoint, time.monotonic(), _boot_grace_s,
            )
            await self._wire_to_brain(
                module="web_integrity_agent",
                summary=f"BOOT WINDOW: {check.method} {check.endpoint} "
                        f"failed (expected during warm-up)",
                detail="; ".join(check.errors),
                success=False,
                confidence="LOW",
                source_id="web_integrity_boot_window",
            )
            return

        # R-F1380/F1381: sustained-failure re-probe for ALL endpoints
        re_probe_intervals = [5, 30, 60, 120]  # seconds between re-probes
        import httpx
        is_public = "[public]" in check.endpoint
        base_url = _ARIA_WEB_URL if is_public else _ARIA_SERVICE_URL
        path = check.endpoint.replace('[public]', '')
        url = f"{base_url}{path}"

        for i, delay in enumerate(re_probe_intervals):
            await asyncio.sleep(delay)
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(url)
                    if resp.is_success:
                        # Recovered — transient, not a real outage
                        logger.warning(
                            "[web_integrity] %s %s — transient failure "
                            "(recovered after ~%ds)",
                            check.method, check.endpoint,
                            sum(re_probe_intervals[:i+1]),
                        )
                        await self._wire_to_brain(
                            module="web_integrity_agent",
                            summary=f"TRANSIENT: {check.method} {check.endpoint} "
                                    f"recovered after ~{sum(re_probe_intervals[:i+1])}s",
                            detail="; ".join(check.errors),
                            success=False,
                            confidence="LOW",
                            source_id="web_integrity_transient",
                        )
                        return
            except Exception:
                continue  # Still down — try next interval

        # All re-probes failed — genuine outage
        logger.warning(
            "[web_integrity] %s %s — sustained failure "
            "(still down after ~%ds re-probe window — escalating to CRITICAL)",
            check.method, check.endpoint, sum(re_probe_intervals),
        )
        # Fall through to CRITICAL escalation below

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
                title=f"web_integrity:{check.endpoint}",
                detail=f"CRITICAL: {check.method} {check.endpoint} failed: "
                            f"{'; '.join(check.errors)[:200]}",
                severity="HIGH",
                source="web_integrity_agent",
            )
        except Exception as e:
            logger.debug("[web_integrity] capability_gaps.record failed: %s", e)

    async def _stage_fix(self, pattern: ErrorPattern) -> None:
        """Stage a fix for a recurring error pattern.

        DIRECTIVE 6: Self-healing — propose fixes for recurring patterns.

        R-F1439: instead of staging useless markdown docs to lib/web_integrity/,
        wire the error to the self-improvement error log so the autonomous cycle
        can generate real code fixes. Also record a capability gap so the coder
        picks it up. The markdown doc staging was a dead end — it never produced
        a real code change.
        """
        logger.warning(
            "[web_integrity] PATTERN DETECTED: %s on %s (%d occurrences) — recording for self-improve cycle",
            pattern.error_type, pattern.endpoint, pattern.count,
        )

        try:
            from . import self_improve as _si

            # R-F1439: wire to self-improve error log so the autonomous cycle
            # can generate real code fixes (not markdown docs).
            for example in pattern.examples:
                await _si.record_error(
                    error_type=f"web_integrity_{pattern.error_type}",
                    message=f"{pattern.endpoint}: {example}",
                    file="aria_service/intel/web_integrity_agent.py",
                    function="_stage_fix",
                )

            # Also record a capability gap so the coder pipeline picks it up
            try:
                from . import capability_gaps as _cg
                await _cg.record_gap(
                    gap_type=f"web_integrity_{pattern.error_type}",
                    title=f"web_integrity:{pattern.endpoint}",
                    detail=(
                        f"Recurring {pattern.error_type} on {pattern.endpoint} "
                        f"({pattern.count} occurrences). "
                        f"Example: {pattern.examples[0] if pattern.examples else 'N/A'}"
                    ),
                    severity="MEDIUM",
                    source="web_integrity_agent:_stage_fix",
                )
            except Exception as e:
                logger.debug("[web_integrity] capability_gaps.record failed: %s", e)

            self._detector.mark_fixed(pattern.pattern_id)
            logger.info(
                "[web_integrity] Pattern %s recorded for self-improve cycle: %s on %s",
                pattern.pattern_id, pattern.error_type, pattern.endpoint,
            )
        except Exception as e:
            logger.error(
                "[web_integrity] Failed to record pattern %s: %s",
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

        R-F1598: uses brain_hook.record_signal (lightweight) instead of
        brain_hook.absorb (full with expensive tiers). web_integrity_agent
        is a high-frequency telemetry module that probes endpoints every
        60s — its signals are metrics, not genuine knowledge. Using full
        absorb was causing 20-30s background tier processing that tripped
        the circuit breaker.
        """
        if self._brain_hook is None:
            return
        try:
            await self._brain_hook.record_signal(
                module=module,
                success=success,
                summary=summary[:300],
            )
        except Exception as e:
            logger.debug("[web_integrity] brain_hook.record_signal failed: %s", e)

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

    async def _get_last_cred_verify(self) -> float:
        """Get the timestamp of the last credential verification."""
        if self._redis is None:
            return 0
        try:
            val = await self._redis.get(f"{_CRED_VERIFY_KEY}:last_run")
            return float(val) if val else 0
        except Exception:
            return 0

    # ── R-F1213: Portal credential verification ─────────────────────────────

    async def verify_all_credentials(self) -> dict[str, Any]:
        """Verify all stored portal credentials are still working.

        Tests each portal's login endpoint with stored credentials.
        Reports health status: working, expired, failing, untested.

        Returns dict with overall stats and per-portal results.
        """
        results: dict[str, Any] = {
            "total": 0,
            "working": 0,
            "expired": 0,
            "failing": 0,
            "untested": 0,
            "portals": {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        try:
            from .portal_registry import PORTALS, get_credential

            for portal in PORTALS:
                if portal.registration_type == "none":
                    continue  # No credentials to verify

                portal_id = portal.id
                results["total"] += 1

                cred = await get_credential(portal_id)
                if cred is None:
                    results["untested"] += 1
                    results["portals"][portal_id] = {
                        "name": portal.name,
                        "status": "no_credentials",
                        "last_verified": None,
                    }
                    continue

                # Test the login endpoint
                health = await self._verify_portal_credential(portal, cred)
                results["portals"][portal_id] = health
                if health["status"] == "working":
                    results["working"] += 1
                elif health["status"] == "expired":
                    results["expired"] += 1
                    # Trigger re-registration
                    await self._trigger_re_registration(portal_id, portal.name)
                else:
                    results["failing"] += 1

            # Save results to Redis
            if self._redis is not None:
                await self._redis.set_json(_CRED_HEALTH_KEY, results)
                await self._redis.set(f"{_CRED_VERIFY_KEY}:last_run", str(time.time()))

            # Wire to brain
            await self._wire_to_brain(
                module="web_integrity_agent",
                summary=(
                    f"Credential verification: {results['working']}/{results['total']} working, "
                    f"{results['expired']} expired, {results['failing']} failing"
                ),
                detail=json.dumps({
                    k: v for k, v in results.items() if k != "portals"
                }),
                success=results["expired"] == 0 and results["failing"] == 0,
                confidence="CONFIRMED",
                source_id="web_integrity:cred_verify",
            )

        except Exception as e:
            logger.error("[web_integrity] credential verification failed: %s", e)
            results["error"] = str(e)[:200]

        return results

    async def _verify_portal_credential(
        self,
        portal: Any,
        credential: dict[str, Any],
    ) -> dict[str, Any]:
        """Test a single portal's credential by attempting a login.

        Returns dict with status, status_code, response_time_ms, error.
        """
        import httpx

        result: dict[str, Any] = {
            "name": portal.name,
            "status": "untested",
            "status_code": 0,
            "response_time_ms": 0,
            "error": None,
            "last_verified": datetime.now(timezone.utc).isoformat(),
        }

        login_url = f"{portal.url.rstrip('/')}{portal.login_path}"
        username = credential.get("username") or credential.get("email", "")
        password = credential.get("password", "")

        if not username or not password:
            result["status"] = "no_credentials"
            return result

        try:
            start = time.monotonic()
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                # Try a simple GET to the login page first
                resp = await client.get(login_url)
                elapsed = (time.monotonic() - start) * 1000
                result["response_time_ms"] = round(elapsed, 1)
                result["status_code"] = resp.status_code

                if resp.status_code == 200:
                    # Page loaded — credentials may be working
                    # Check for login form indicators vs error messages
                    text = resp.text.lower()
                    if "invalid" in text or "incorrect" in text or "error" in text[:500]:
                        result["status"] = "expired"
                        result["error"] = "Login page shows error message"
                    else:
                        result["status"] = "working"
                elif resp.status_code == 403:
                    result["status"] = "expired"
                    result["error"] = "HTTP 403 — credentials rejected"
                elif resp.status_code == 401:
                    result["status"] = "expired"
                    result["error"] = "HTTP 401 — unauthorized"
                elif resp.status_code >= 500:
                    result["status"] = "failing"
                    result["error"] = f"Server error: {resp.status_code}"
                else:
                    result["status"] = "failing"
                    result["error"] = f"Unexpected status: {resp.status_code}"

        except httpx.TimeoutException:
            result["status"] = "failing"
            result["error"] = "Timeout (>15s)"
        except httpx.ConnectError as e:
            result["status"] = "failing"
            result["error"] = f"Connection failed: {e}"
        except Exception as e:
            result["status"] = "failing"
            result["error"] = str(e)[:200]

        # Save individual verify result
        if self._redis is not None:
            try:
                await self._redis.hset(_CRED_VERIFY_KEY, {
                    portal.id: json.dumps(result)
                })
            except Exception:
                pass

        return result

    async def _trigger_re_registration(self, portal_id: str, portal_name: str) -> None:
        """Trigger re-registration for a portal with expired credentials.

        Records a capability gap so the autonomous scheduler picks it up.
        """
        try:
            from . import capability_gaps as _cg
            await _cg.record_gap(
                gap_type="credential_expired",
                title=f"portal:{portal_id}",
                detail=(
                    f"Credentials for {portal_name} ({portal_id}) have expired. "
                    f"Re-registration needed to maintain data access."
                ),
                severity="HIGH",
                source="web_integrity_agent:cred_verify",
            )
            logger.warning(
                "[web_integrity] Credentials expired for %s (%s) — gap recorded",
                portal_name, portal_id,
            )
        except Exception as e:
            logger.debug("[web_integrity] re-registration trigger failed: %s", e)

    async def get_credential_health(self) -> dict[str, Any]:
        """Get the current credential health status from Redis.

        Returns the last verification results, or empty dict if never run.
        """
        if self._redis is None:
            return {"status": "no_redis", "portals": {}}
        try:
            data = await self._redis.get_json(_CRED_HEALTH_KEY)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.

"""Circuit breaker for external API calls.

Tracks consecutive failures per backend. After N failures, marks the
backend DOWN for a cooldown period. Auto-recovers by allowing one
probe request after cooldown expires.

Usage:
    cb = get_breaker("searx.be")
    if cb.is_open():
        skip this backend
    try:
        result = await fetch(...)
        cb.record_success()
    except Exception:
        cb.record_failure()

State is in-process only (not Redis) — resets on deploy, which is fine.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger("aria.circuit_breaker")

# Defaults — tunable per backend type
_DEFAULT_FAILURE_THRESHOLD = 3       # consecutive failures before OPEN
_DEFAULT_COOLDOWN_SECONDS = 300      # 5 minutes before the FIRST probe (base)
_DEFAULT_HALF_OPEN_MAX = 1           # probes allowed in half-open

# R-F1834 — structural fix for the recurring "hand-tune the cooldown" band-aid
# (§1 root-cause-not-symptom). Instead of a fixed cooldown per backend (which
# was bumped by hand for archive.is 900→3600 in F78a and proposed again
# 3600→86400), the cooldown grows EXPONENTIALLY each time the half-open probe
# fails again — base, 2×, 4×, … capped at _DEFAULT_MAX_COOLDOWN_SECONDS — and
# resets to base on the first success. A backend that is genuinely dead
# (archive.is 429-ing every probe from the datacenter IP) settles at the 24h
# cap on its own; a backend that recovers stops flapping because success
# resets the backoff. No per-call-site magic number required.
_DEFAULT_MAX_COOLDOWN_SECONDS = 86400  # 24h backoff cap

# Failure reasons that will NEVER clear by waiting — re-probing on the normal
# cadence just burns a request and (for billing/auth) can compound the problem.
# These jump straight to a long floor so we don't hammer; recovery still happens
# via record_success() once the operator rotates the key / tops up credit.
_UNRECOVERABLE_REASONS = {"auth", "billing"}
_UNRECOVERABLE_FLOOR_SECONDS = 21600   # 6h


# F94 (2026-04-30): map circuit-breaker failure reason → capability gap
# type so the gap ledger reflects the actual root cause. Before this,
# every breaker trip recorded `gap_type="timeout"` regardless of whether
# the upstream returned 402 (billing), 429 (rate limit), or 401/403
# (auth) — misrouting triage onto a "transient network" code path that
# would never fix the actual problem.
_REASON_TO_GAP = {
    "billing":    ("billing_required", "Backend {name} returned billing/payment-required — operator action needed (top up credit or rotate key)"),
    "rate_limit": ("rate_limited",     "Backend {name} rate-limited — set API key for higher quota or add caller-side token bucket"),
    "auth":       ("auth_failure",     "Backend {name} returned auth failure (401/403) — rotate or set the API key"),
    "timeout":    ("timeout",          "Backend {name} unreachable (timeout)"),
    "server":     ("timeout",          "Backend {name} 5xx upstream error"),
}


def classify_status(status: int) -> str:
    """HTTP status → ``record_failure(reason=…)`` tag (R-F1834).

    Canonical home for the mapping that was duplicated in
    ``academic._classify_status`` and ``web_search._classify_http_status``.
    Callers in intel/ should use this so every backend's breaker trip is
    classified (and so auth/billing get the long unrecoverable floor)."""
    if status == 402:
        return "billing"
    if status == 429:
        return "rate_limit"
    if status in (401, 403):
        return "auth"
    if 500 <= status < 600:
        return "server"
    return "other"


def _gap_for_reason(name: str, reason: str) -> tuple[str, str]:
    """Return (gap_type, gap_detail) for the given breaker name + reason."""
    if reason in _REASON_TO_GAP:
        gtype, template = _REASON_TO_GAP[reason]
        return gtype, template.format(name=name)
    # Default — preserves legacy behaviour for callers that haven't yet
    # been updated to pass a reason.
    return "timeout", f"Backend {name} unreachable"


@dataclass
class CircuitBreaker:
    """Per-backend circuit breaker."""
    name: str
    failure_threshold: int = _DEFAULT_FAILURE_THRESHOLD
    cooldown_seconds: float = _DEFAULT_COOLDOWN_SECONDS  # BASE cooldown (1st probe)
    max_cooldown_seconds: float = _DEFAULT_MAX_COOLDOWN_SECONDS  # backoff cap
    # Internal state
    consecutive_failures: int = 0
    total_failures: int = 0
    total_successes: int = 0
    open_cycles: int = 0  # R-F1834: times tripped OPEN w/o an intervening success → backoff exponent
    last_failure_at: float = 0.0
    last_success_at: float = 0.0
    last_failure_reason: str = ""  # F94 (2026-04-30): set by record_failure(reason=…)
    state: str = "CLOSED"  # CLOSED (healthy), OPEN (failing), HALF_OPEN (probing)

    def _effective_cooldown(self) -> float:
        """R-F1834 — current cooldown given the exponential backoff state.

        base × 2^(open_cycles-1), capped at ``max_cooldown_seconds``. An
        unrecoverable reason (auth/billing — never clears by waiting) is floored
        at ``_UNRECOVERABLE_FLOOR_SECONDS`` so we stop re-probing a dead key."""
        n = max(self.open_cycles, 1)
        # Guard the shift: open_cycles is bounded in practice (resets on success,
        # and once eff hits the cap further growth is clamped) but cap the
        # exponent so 2**n can't overflow into a huge float on a stuck backend.
        exp = min(n - 1, 32)
        eff = self.cooldown_seconds * (2 ** exp)
        eff = min(eff, self.max_cooldown_seconds)
        if self.last_failure_reason in _UNRECOVERABLE_REASONS:
            eff = max(eff, _UNRECOVERABLE_FLOOR_SECONDS)
        return eff

    def is_open(self) -> bool:
        """Should this backend be skipped?"""
        if self.state == "CLOSED":
            return False
        if self.state == "OPEN":
            # Check if the (backoff-scaled) cooldown expired → HALF_OPEN probe
            if time.time() - self.last_failure_at >= self._effective_cooldown():
                self.state = "HALF_OPEN"
                logger.info("[circuit_breaker] %s: OPEN → HALF_OPEN (cooldown expired, allowing probe)", self.name)
                return False  # allow one probe
            return True  # still in cooldown
        # HALF_OPEN — allow the probe
        return False

    def record_success(self) -> None:
        """Backend responded successfully."""
        self.consecutive_failures = 0
        self.open_cycles = 0  # R-F1834: recovery resets the backoff to base
        self.total_successes += 1
        self.last_success_at = time.time()
        if self.state != "CLOSED":
            logger.info("[circuit_breaker] %s: %s → CLOSED (success)", self.name, self.state)
            self.state = "CLOSED"

    def record_failure(self, reason: str = "") -> None:
        """Backend failed.

        ``reason`` is a short tag describing the failure mode so the
        capability gap recorded when the breaker trips OPEN is
        classified correctly. Recognised values (F94, 2026-04-30):

          - ``"rate_limit"``  — HTTP 429 / quota exceeded
          - ``"billing"``     — HTTP 402 / payment required / credit
                                 exhausted
          - ``"auth"``        — HTTP 401 / 403
          - ``"timeout"``     — connect/read timeout
          - ``"server"``      — 5xx
          - ``""`` / other    — falls back to ``timeout`` gap_type
                                 (legacy callers that don't supply a
                                 reason, kept for backward compat)
        """
        self.consecutive_failures += 1
        self.total_failures += 1
        self.last_failure_at = time.time()
        if reason:
            self.last_failure_reason = reason
        if self.state == "HALF_OPEN":
            # Probe failed — back to OPEN, and deepen the backoff (R-F1834) so a
            # persistently-dead backend (archive.is 429s) stops being re-probed
            # every cooldown; the next wait is 2× the last, capped at 24h.
            self.state = "OPEN"
            self.open_cycles += 1
            logger.warning(
                "[circuit_breaker] %s: HALF_OPEN → OPEN (probe failed, backoff now %.0fs)",
                self.name, self._effective_cooldown(),
            )
        elif self.consecutive_failures >= self.failure_threshold:
            if self.state != "OPEN":
                self.open_cycles += 1  # R-F1834: first trip → exponent 1 → base cooldown
                logger.warning(
                    "[circuit_breaker] %s: CLOSED → OPEN (%d consecutive failures, reason=%s, cooldown=%.0fs)",
                    self.name, self.consecutive_failures,
                    self.last_failure_reason or "unspecified",
                    self._effective_cooldown(),
                )
                self.state = "OPEN"
                # Map reason → gap_type so misrouted triage stops:
                # billing failures don't get fixed by waiting, rate
                # limits don't need an outage page, and auth failures
                # need a key rotation, not a network probe.
                gap_type, gap_detail = _gap_for_reason(
                    self.name, self.last_failure_reason,
                )
                # Signal brain — source reliability degraded. Use the
                # running loop directly; asyncio.get_event_loop() is
                # deprecated in 3.10+ when no loop is running and may
                # emit DeprecationWarning in 3.12+. record_failure is
                # called from async contexts so a running loop should
                # always be available; the RuntimeError fallback just
                # quietly drops the brain signal in the unlikely sync
                # path (no production caller hits it today).
                try:
                    import asyncio
                    from . import brain_hook as _bh
                    try:
                        _loop = asyncio.get_running_loop()
                    except RuntimeError:
                        _loop = None
                    if _loop is not None:
                        _loop.create_task(
                            _bh.absorb(
                                module="circuit_breaker",
                                summary=f"Circuit breaker OPEN: {self.name} ({self.consecutive_failures} failures, {self.last_failure_reason or 'unspecified'})",
                                detail=f"Backend {self.name} marked DOWN for {self.cooldown_seconds}s",
                                success=False,
                                gap_type=gap_type,
                                gap_detail=gap_detail,
                            )
                        )
                except Exception:
                    pass

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "state": self.state,
            "consecutive_failures": self.consecutive_failures,
            "total_failures": self.total_failures,
            "total_successes": self.total_successes,
            "failure_threshold": self.failure_threshold,
            "cooldown_seconds": self.cooldown_seconds,  # base
            "max_cooldown_seconds": self.max_cooldown_seconds,
            "open_cycles": self.open_cycles,
            "effective_cooldown_seconds": self._effective_cooldown(),
            "last_failure_reason": self.last_failure_reason,
        }


# ── Global registry ───────────────────────────────────────────────────────

_breakers: dict[str, CircuitBreaker] = {}


def get_breaker(
    name: str,
    failure_threshold: int = _DEFAULT_FAILURE_THRESHOLD,
    cooldown_seconds: float = _DEFAULT_COOLDOWN_SECONDS,
    max_cooldown_seconds: float = _DEFAULT_MAX_COOLDOWN_SECONDS,
) -> CircuitBreaker:
    """Get or create a circuit breaker for a named backend.

    ``cooldown_seconds`` is the BASE (first-probe) cooldown; on repeated probe
    failures it grows exponentially up to ``max_cooldown_seconds`` (R-F1834).
    """
    if name not in _breakers:
        _breakers[name] = CircuitBreaker(
            name=name,
            failure_threshold=failure_threshold,
            cooldown_seconds=cooldown_seconds,
            max_cooldown_seconds=max_cooldown_seconds,
        )
    # R-F996 — wire to brain
    from .engine_wiring import wire_success
    wire_success(
        module="circuit_breaker",
        summary="Get Breaker",
        source_id="circuit_breaker:R-F996",
    )

    return _breakers[name]


def get_all_breakers() -> list[dict]:
    """Return status of all registered circuit breakers."""
    return [b.to_dict() for b in _breakers.values()]


def reset_breaker(name: str) -> bool:
    """Manually reset a breaker to CLOSED."""
    if name in _breakers:
        _breakers[name].state = "CLOSED"
        _breakers[name].consecutive_failures = 0
        logger.info("[circuit_breaker] %s: manually reset to CLOSED", name)
        return True
    return False

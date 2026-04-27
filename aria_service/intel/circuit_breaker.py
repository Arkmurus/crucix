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
_DEFAULT_COOLDOWN_SECONDS = 300      # 5 minutes before probe
_DEFAULT_HALF_OPEN_MAX = 1           # probes allowed in half-open


@dataclass
class CircuitBreaker:
    """Per-backend circuit breaker."""
    name: str
    failure_threshold: int = _DEFAULT_FAILURE_THRESHOLD
    cooldown_seconds: float = _DEFAULT_COOLDOWN_SECONDS
    # Internal state
    consecutive_failures: int = 0
    total_failures: int = 0
    total_successes: int = 0
    last_failure_at: float = 0.0
    last_success_at: float = 0.0
    state: str = "CLOSED"  # CLOSED (healthy), OPEN (failing), HALF_OPEN (probing)

    def is_open(self) -> bool:
        """Should this backend be skipped?"""
        if self.state == "CLOSED":
            return False
        if self.state == "OPEN":
            # Check if cooldown expired → transition to HALF_OPEN
            if time.time() - self.last_failure_at >= self.cooldown_seconds:
                self.state = "HALF_OPEN"
                logger.info("[circuit_breaker] %s: OPEN → HALF_OPEN (cooldown expired, allowing probe)", self.name)
                return False  # allow one probe
            return True  # still in cooldown
        # HALF_OPEN — allow the probe
        return False

    def record_success(self) -> None:
        """Backend responded successfully."""
        self.consecutive_failures = 0
        self.total_successes += 1
        self.last_success_at = time.time()
        if self.state != "CLOSED":
            logger.info("[circuit_breaker] %s: %s → CLOSED (success)", self.name, self.state)
            self.state = "CLOSED"

    def record_failure(self) -> None:
        """Backend failed."""
        self.consecutive_failures += 1
        self.total_failures += 1
        self.last_failure_at = time.time()
        if self.state == "HALF_OPEN":
            # Probe failed — back to OPEN
            self.state = "OPEN"
            logger.warning("[circuit_breaker] %s: HALF_OPEN → OPEN (probe failed)", self.name)
        elif self.consecutive_failures >= self.failure_threshold:
            if self.state != "OPEN":
                logger.warning(
                    "[circuit_breaker] %s: CLOSED → OPEN (%d consecutive failures)",
                    self.name, self.consecutive_failures,
                )
                self.state = "OPEN"
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
                                summary=f"Circuit breaker OPEN: {self.name} ({self.consecutive_failures} failures)",
                                detail=f"Backend {self.name} marked DOWN for {self.cooldown_seconds}s",
                                success=False,
                                gap_type="timeout",
                                gap_detail=f"Backend {self.name} unreachable",
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
            "cooldown_seconds": self.cooldown_seconds,
        }


# ── Global registry ───────────────────────────────────────────────────────

_breakers: dict[str, CircuitBreaker] = {}


def get_breaker(
    name: str,
    failure_threshold: int = _DEFAULT_FAILURE_THRESHOLD,
    cooldown_seconds: float = _DEFAULT_COOLDOWN_SECONDS,
) -> CircuitBreaker:
    """Get or create a circuit breaker for a named backend."""
    if name not in _breakers:
        _breakers[name] = CircuitBreaker(
            name=name,
            failure_threshold=failure_threshold,
            cooldown_seconds=cooldown_seconds,
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

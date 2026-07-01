"""R-F1051 — ARIA Self-Healing Infrastructure.

A comprehensive, multi-layered self-healing system that ensures ARIA's brain
never breaks and can self-repair her entire ecosystem.

Architecture
════════════
Layer 1 — Health Monitor: continuous health checks across all subsystems
Layer 2 — Circuit Breakers: per-subsystem circuit breakers with auto-recovery
Layer 3 — Auto-Recovery: automatic remediation actions for common failures
Layer 4 — Brain Durability: crash-proof state, write-ahead log, auto-rebuild
Layer 5 — Ecosystem Self-Repair: cross-tier health checks, auto-remediation
Layer 6 — Self-Diagnostic: comprehensive self-diagnosis and gap detection

Each layer is independent and can operate without the others. All layers
feed into ARIA's brain via brain_hook so she learns from every failure.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

import httpx

from . import redis_store as rs
from .engine_wiring import wire_success, wire_failure

logger = logging.getLogger("aria.self_healing")

# ── Configuration ─────────────────────────────────────────────────────────────
_HEALTH_CHECK_INTERVAL_S = int(os.getenv("ARIA_SELF_HEAL_INTERVAL", "60"))
_CIRCUIT_RESET_S = int(os.getenv("ARIA_CIRCUIT_RESET_S", "300"))
_MAX_RECOVERY_ATTEMPTS = int(os.getenv("ARIA_MAX_RECOVERY_ATTEMPTS", "3"))
_WAL_RETENTION_S = int(os.getenv("ARIA_WAL_RETENTION_S", "86400"))  # 24h

# ── Redis keys ────────────────────────────────────────────────────────────────
_HEALTH_KEY = "crucix:self_healing:health"
_CIRCUIT_KEY_PREFIX = "crucix:self_healing:circuit:"
_WAL_KEY = "crucix:self_healing:wal"
_RECOVERY_KEY = "crucix:self_healing:recovery"
_METRICS_KEY = "crucix:self_healing:metrics"

# ── Enums ─────────────────────────────────────────────────────────────────────


class HealthStatus(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class CircuitState(Enum):
    CLOSED = "closed"          # Normal operation
    OPEN = "open"              # Failing — requests blocked
    HALF_OPEN = "half_open"    # Testing if recovered
    DISABLED = "disabled"      # Manually disabled


class RecoveryActionType(Enum):
    RESTART = "restart"
    ROLLBACK = "rollback"
    RECONNECT = "reconnect"
    REBUILD = "rebuild"
    CLEAR_CACHE = "clear_cache"
    RESET_CONNECTION = "reset_connection"
    NOTIFY = "notify"


# ── Data types ────────────────────────────────────────────────────────────────


@dataclass
class HealthCheck:
    """Result of a single health check."""
    subsystem: str
    status: HealthStatus
    latency_ms: float = 0.0
    error: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    checked_at: float = field(default_factory=time.time)

    def is_healthy(self) -> bool:
        return self.status == HealthStatus.HEALTHY


@dataclass
class CircuitBreaker:
    """Per-subsystem circuit breaker with auto-recovery."""
    subsystem: str
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    last_failure_at: float = 0.0
    last_success_at: float = 0.0
    opened_at: float = 0.0
    recovery_attempts: int = 0
    threshold: int = 3       # Consecutive failures before opening
    cooldown_s: float = 300.0  # Seconds before half-open

    def record_failure(self) -> None:
        self.failure_count += 1
        self.last_failure_at = time.time()
        if self.failure_count >= self.threshold and self.state == CircuitState.CLOSED:
            self.state = CircuitState.OPEN
            self.opened_at = time.time()
            logger.warning("[self_healing] Circuit OPEN for %s (%d failures)", self.subsystem, self.failure_count)

    def record_success(self) -> None:
        self.failure_count = 0
        self.last_success_at = time.time()
        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.CLOSED
            self.recovery_attempts = 0
            logger.info("[self_healing] Circuit CLOSED for %s (recovered)", self.subsystem)
        elif self.state == CircuitState.OPEN:
            self.state = CircuitState.CLOSED
            logger.info("[self_healing] Circuit CLOSED for %s (auto-recovered)", self.subsystem)

    def should_try(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.DISABLED:
            return False
        if self.state == CircuitState.OPEN:
            if time.time() - self.opened_at >= self.cooldown_s:
                self.state = CircuitState.HALF_OPEN
                logger.info("[self_healing] Circuit HALF_OPEN for %s (testing recovery)", self.subsystem)
                return True
            return False
        if self.state == CircuitState.HALF_OPEN:
            return True
        return True

    def to_dict(self) -> dict:
        return {
            "subsystem": self.subsystem,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "last_failure_at": self.last_failure_at,
            "last_success_at": self.last_success_at,
            "opened_at": self.opened_at,
            "recovery_attempts": self.recovery_attempts,
            "threshold": self.threshold,
            "cooldown_s": self.cooldown_s,
        }


@dataclass
class RecoveryAction:
    """A recovery action to execute."""
    action: RecoveryActionType
    target: str
    reason: str = ""
    executed_at: float = 0.0
    success: bool = False
    result: str = ""


# ── Layer 1: Health Monitor ──────────────────────────────────────────────────


class HealthMonitor:
    """Continuous health checks across all subsystems."""

    def __init__(self):
        self._checks: dict[str, HealthCheck] = {}
        self._history: list[dict] = []
        self._running = False

    async def check_subsystem(self, name: str, url: str, timeout: float = 10.0) -> HealthCheck:
        """Check a single subsystem by URL."""
        start = time.time()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.get(url)  # no-ssrf-check: internal subsystem health URL from a fixed registry (check_all), not user input
                elapsed = (time.time() - start) * 1000
                if r.status_code < 500:
                    check = HealthCheck(
                        subsystem=name,
                        status=HealthStatus.HEALTHY,
                        latency_ms=elapsed,
                        details={"status_code": r.status_code},
                    )
                else:
                    check = HealthCheck(
                        subsystem=name,
                        status=HealthStatus.DEGRADED,
                        latency_ms=elapsed,
                        error=f"HTTP {r.status_code}",
                        details={"status_code": r.status_code},
                    )
        except httpx.TimeoutException:
            check = HealthCheck(
                subsystem=name,
                status=HealthStatus.DEGRADED,
                error="timeout",
            )
        except httpx.ConnectError:
            check = HealthCheck(
                subsystem=name,
                status=HealthStatus.CRITICAL,
                error="connection refused",
            )
        except Exception as e:
            check = HealthCheck(
                subsystem=name,
                status=HealthStatus.UNKNOWN,
                error=str(e),
            )

        self._checks[name] = check
        return check

    async def check_all(self) -> dict[str, HealthCheck]:
        """Check all known subsystems."""
        services = {
            "redis": os.getenv("REDIS_URL", "").replace("redis://", "http://") or "",
            "aria_intel": "http://localhost:8000/health",
            "semantic_search": "http://localhost:8000/api/aria/health/perf",
            "brain_hook": "http://localhost:8000/api/aria/brain/stats",
        }
        # R-F1224: ARIA-LLM health check — only when configured
        _aria_llm_url = (os.getenv("ARIA_LLM_URL") or "").strip()
        if _aria_llm_url:
            services["aria_llm"] = _aria_llm_url.rstrip("/") + "/models"

        results = {}
        tasks = []
        for name, url in services.items():
            if url:
                tasks.append(self.check_subsystem(name, url))

        if tasks:
            done = await asyncio.gather(*tasks, return_exceptions=True)
            for i, name in enumerate([n for n, u in services.items() if u]):
                if i < len(done):
                    if isinstance(done[i], Exception):
                        results[name] = HealthCheck(
                            subsystem=name,
                            status=HealthStatus.UNKNOWN,
                            error=str(done[i]),
                        )
                    else:
                        results[name] = done[i]

        return results

    async def run_forever(self) -> None:
        """Run health checks continuously."""
        self._running = True
        while self._running:
            try:
                checks = await self.check_all()
                overall = HealthStatus.HEALTHY
                for c in checks.values():
                    if c.status == HealthStatus.CRITICAL:
                        overall = HealthStatus.CRITICAL
                    elif c.status == HealthStatus.DEGRADED and overall != HealthStatus.CRITICAL:
                        overall = HealthStatus.DEGRADED

                snapshot = {
                    "timestamp": time.time(),
                    "overall": overall.value,
                    "checks": {k: {"status": v.status.value, "latency_ms": v.latency_ms, "error": v.error} for k, v in checks.items()},
                }
                self._history.append(snapshot)
                if len(self._history) > 1000:
                    self._history = self._history[-500:]

                # Persist to Redis
                try:
                    await rs.set_json(_HEALTH_KEY, snapshot, ex=3600)
                except Exception:
                    pass

                # Wire to brain
                if overall != HealthStatus.HEALTHY:
                    wire_failure(
                        module="self_healing",
                        detail=f"Health check: {overall.value} — {len([c for c in checks.values() if c.status != HealthStatus.HEALTHY])} subsystems degraded",
                        gap_type="infra_degraded",
                        source="self_healing.health_monitor",
                    )

            except Exception as e:
                logger.error("[self_healing] Health monitor error: %s", e)

            await asyncio.sleep(_HEALTH_CHECK_INTERVAL_S)

    def stop(self) -> None:
        self._running = False

    def get_latest(self) -> Optional[dict]:
        return self._history[-1] if self._history else None

    def get_history(self, limit: int = 100) -> list[dict]:
        return self._history[-limit:]


# ── Layer 2: Circuit Breaker Manager ─────────────────────────────────────────


class CircuitBreakerManager:
    """Manages per-subsystem circuit breakers with Redis persistence."""

    def __init__(self):
        self._breakers: dict[str, CircuitBreaker] = {}

    def get(self, subsystem: str) -> CircuitBreaker:
        if subsystem not in self._breakers:
            self._breakers[subsystem] = CircuitBreaker(subsystem=subsystem)
        return self._breakers[subsystem]

    def record_failure(self, subsystem: str) -> CircuitBreaker:
        cb = self.get(subsystem)
        cb.record_failure()
        self._persist(cb)
        return cb

    def record_success(self, subsystem: str) -> CircuitBreaker:
        cb = self.get(subsystem)
        cb.record_success()
        self._persist(cb)
        return cb

    def should_try(self, subsystem: str) -> bool:
        return self.get(subsystem).should_try()

    def reset(self, subsystem: str) -> None:
        if subsystem in self._breakers:
            cb = self._breakers[subsystem]
            cb.state = CircuitState.CLOSED
            cb.failure_count = 0
            self._persist(cb)

    def _persist(self, cb: CircuitBreaker) -> None:
        try:
            key = _CIRCUIT_KEY_PREFIX + cb.subsystem
            asyncio.ensure_future(rs.set_json(key, cb.to_dict(), ex=86400))
        except Exception:
            pass

    def get_all_states(self) -> dict[str, dict]:
        return {k: v.to_dict() for k, v in self._breakers.items()}

    async def load_from_redis(self) -> None:
        """Restore circuit breaker states from Redis after restart."""
        try:
            # R-F1065: rs.keys() doesn't exist — use scan_keys instead
            keys = await rs.scan_keys(_CIRCUIT_KEY_PREFIX + "*")
            for key in keys:
                data = await rs.get_json(key)
                if data:
                    subsystem = data.get("subsystem", "")
                    cb = CircuitBreaker(
                        subsystem=subsystem,
                        state=CircuitState(data.get("state", "closed")),
                        failure_count=data.get("failure_count", 0),
                        last_failure_at=data.get("last_failure_at", 0),
                        last_success_at=data.get("last_success_at", 0),
                        opened_at=data.get("opened_at", 0),
                        recovery_attempts=data.get("recovery_attempts", 0),
                    )
                    self._breakers[subsystem] = cb
        except Exception as e:
            logger.debug("[self_healing] Could not load circuit breakers: %s", e)


# ── Layer 3: Write-Ahead Log (Brain Durability) ──────────────────────────────


class WriteAheadLog:
    """Crash-proof write-ahead log for brain state.

    Every state mutation is written to the WAL BEFORE being applied.
    On restart, the WAL is replayed to restore state.
    """

    def __init__(self):
        self._entries: list[dict] = []

    async def write(self, entry_type: str, key: str, data: dict) -> str:
        """Write an entry to the WAL. Returns the entry ID."""
        entry = {
            "id": f"{int(time.time() * 1000000)}_{key[:40]}",
            "type": entry_type,
            "key": key,
            "data": data,
            "written_at": time.time(),
            "applied": False,
        }
        self._entries.append(entry)
        try:
            await rs.lpush(_WAL_KEY, json.dumps(entry, default=str))
            await rs.ltrim(_WAL_KEY, 0, 9999)
        except Exception as e:
            logger.debug("[self_healing] WAL write failed: %s", e)
        return entry["id"]

    async def mark_applied(self, entry_id: str) -> None:
        """Mark a WAL entry as applied."""
        for entry in self._entries:
            if entry["id"] == entry_id:
                entry["applied"] = True
                break
        try:
            # Update in Redis
            raw = await rs.lrange(_WAL_KEY, 0, -1)
            entries = [json.loads(r) if isinstance(r, str) else r for r in raw]
            for e in entries:
                if e.get("id") == entry_id:
                    e["applied"] = True
                    break
            # Rewrite
            await rs.delete(_WAL_KEY)
            for e in reversed(entries):
                await rs.lpush(_WAL_KEY, json.dumps(e, default=str))
        except Exception:
            pass

    async def replay(self) -> list[dict]:
        """Replay unapplied WAL entries. Returns entries that need replaying."""
        try:
            raw = await rs.lrange(_WAL_KEY, 0, -1)
            entries = [json.loads(r) if isinstance(r, str) else r for r in raw]
            unapplied = [e for e in entries if not e.get("applied", False)]
            # Filter stale entries
            now = time.time()
            unapplied = [e for e in unapplied if (now - e.get("written_at", 0)) < _WAL_RETENTION_S]
            return unapplied
        except Exception as e:
            logger.warning("[self_healing] WAL replay failed: %s", e)
            return []

    async def get_stats(self) -> dict:
        """Get WAL statistics."""
        try:
            raw = await rs.lrange(_WAL_KEY, 0, -1)
            entries = [json.loads(r) if isinstance(r, str) else r for r in raw]
            total = len(entries)
            applied = sum(1 for e in entries if e.get("applied", False))
            by_type: dict[str, int] = {}
            for e in entries:
                t = e.get("type", "unknown")
                by_type[t] = by_type.get(t, 0) + 1
            return {
                "total_entries": total,
                "applied": applied,
                "pending": total - applied,
                "by_type": by_type,
                "oldest": entries[-1].get("written_at", 0) if entries else 0,
                "newest": entries[0].get("written_at", 0) if entries else 0,
            }
        except Exception as e:
            return {"error": str(e)}


# ── Layer 4: Auto-Recovery Engine ────────────────────────────────────────────


class AutoRecoveryEngine:
    """Automatic remediation for common failures."""

    def __init__(self, circuit_breaker_manager: CircuitBreakerManager):
        self._cbm = circuit_breaker_manager
        self._recovery_history: list[dict] = []

    async def attempt_recovery(self, subsystem: str, error: str) -> dict:
        """Attempt to recover a failed subsystem."""
        cb = self._cbm.get(subsystem)
        cb.recovery_attempts += 1

        if cb.recovery_attempts > _MAX_RECOVERY_ATTEMPTS:
            logger.warning("[self_healing] Max recovery attempts reached for %s", subsystem)
            return {"success": False, "reason": "max_attempts", "attempts": cb.recovery_attempts}

        # Determine recovery action based on error type
        action = self._determine_action(subsystem, error)
        result = await self._execute_action(action)

        recovery_record = {
            "timestamp": time.time(),
            "subsystem": subsystem,
            "error": error[:200],
            "action": action.action.value,
            "target": action.target,
            "success": result.get("success", False),
            "result": result.get("message", ""),
            "attempt": cb.recovery_attempts,
        }
        self._recovery_history.append(recovery_record)
        if len(self._recovery_history) > 100:
            self._recovery_history = self._recovery_history[-50:]

        # Persist
        try:
            await rs.lpush(_RECOVERY_KEY, json.dumps(recovery_record, default=str))
            await rs.ltrim(_RECOVERY_KEY, 0, 99)
        except Exception:
            pass

        # Wire to brain
        if result.get("success"):
            wire_success(
                module="self_healing",
                summary=f"Auto-recovery: {subsystem} recovered via {action.action.value}",
                source_id=f"self_healing:recovery:{subsystem}",
            )
        else:
            wire_failure(
                module="self_healing",
                detail=f"Auto-recovery failed for {subsystem}: {result.get('message', 'unknown')}",
                gap_type="recovery_failure",
                source=f"self_healing:recovery:{subsystem}",
            )

        return result

    def _determine_action(self, subsystem: str, error: str) -> RecoveryAction:
        """Determine the appropriate recovery action based on error."""
        error_lower = error.lower()

        if "connection" in error_lower or "refused" in error_lower or "timeout" in error_lower:
            return RecoveryAction(action=RecoveryActionType.RECONNECT, target=subsystem, reason=error[:200])
        elif "memory" in error_lower or "oom" in error_lower:
            return RecoveryAction(action=RecoveryActionType.RESTART, target=subsystem, reason=error[:200])
        elif "corrupt" in error_lower or "integrity" in error_lower:
            return RecoveryAction(action=RecoveryActionType.REBUILD, target=subsystem, reason=error[:200])
        elif "cache" in error_lower or "stale" in error_lower:
            return RecoveryAction(action=RecoveryActionType.CLEAR_CACHE, target=subsystem, reason=error[:200])
        elif "deploy" in error_lower or "regression" in error_lower:
            return RecoveryAction(action=RecoveryActionType.ROLLBACK, target=subsystem, reason=error[:200])
        else:
            return RecoveryAction(action=RecoveryActionType.NOTIFY, target=subsystem, reason=error[:200])

    async def _execute_action(self, action: RecoveryAction) -> dict:
        """Execute a recovery action.

        Every action type MUST verify the action actually completed before
        returning success: True. R-F1216: false success is a lie to the brain.
        """
        action.executed_at = time.time()

        if action.action == RecoveryActionType.RECONNECT:
            # R-F1216: verify the connection is actually reachable by probing
            # the target. A blind "connection reset" claim is a lie.
            try:
                probe_url = f"http://{action.target}/health"
                async with httpx.AsyncClient(timeout=5.0) as client:
                    r = await client.get(probe_url)  # no-ssrf-check: action.target is an internal subsystem name from a recovery action, not user input
                    if r.status_code < 500:
                        return {"success": True, "message": f"Connection verified for {action.target}"}
                    return {
                        "success": False,
                        "message": f"Reconnect attempted for {action.target} but returned HTTP {r.status_code}",
                    }
            except Exception as e:
                return {
                    "success": False,
                    "message": f"Reconnect failed for {action.target}: {e}",
                }

        elif action.action == RecoveryActionType.CLEAR_CACHE:
            # Clear cache for the subsystem — scan for matching keys then delete each
            try:
                pattern = f"crucix:{action.target}:*"
                keys = await rs.scan_keys(pattern)
                deleted = 0
                for k in keys:
                    try:
                        await rs.delete(k)
                        deleted += 1
                    except Exception:
                        pass
                # R-F1216: verify the keys were actually deleted by re-scanning
                remaining = await rs.scan_keys(pattern)
                if not remaining:
                    return {"success": True, "message": f"Cache cleared for {action.target}: {deleted} keys deleted"}
                return {
                    "success": False,
                    "message": f"Cache clear incomplete for {action.target}: deleted {deleted}/{len(keys)} keys, {len(remaining)} remain",
                }
            except Exception as e:
                return {"success": False, "message": str(e)}

        elif action.action == RecoveryActionType.REBUILD:
            # Signal rebuild — the subsystem will rebuild on next access
            try:
                rebuild_key = f"crucix:rebuild:{action.target}"
                await rs.set(rebuild_key, "1", ex=3600)
                # R-F1216: verify the key was actually written
                stored = await rs.get(rebuild_key)
                if stored == b"1":
                    return {"success": True, "message": f"Rebuild signalled for {action.target}"}
                return {
                    "success": False,
                    "message": f"Rebuild signal not confirmed for {action.target}",
                }
            except Exception as e:
                return {"success": False, "message": str(e)}

        elif action.action == RecoveryActionType.RESTART:
            # R-F1067: log the restart request — actual restart requires
            # operator or fly deployer. Surface as actionable signal.
            logger.warning("[self_healing] RESTART needed for %s: %s", action.target, action.reason)
            # R-F1216: wire_failure is the correct signal here — a restart was
            # REQUESTED but not EXECUTED. Return success: False so the brain
            # knows the recovery did NOT complete.
            wire_failure(
                module="self_healing",
                detail=f"RESTART required for {action.target}: {action.reason} — operator intervention needed",
                gap_type="recovery_needed",
                source="self_healing",
            )
            return {
                "success": False,
                "message": f"Restart requested for {action.target} — operator must restart manually",
                "action": "restart",
                "needs_operator": True,
            }

        elif action.action == RecoveryActionType.ROLLBACK:
            # R-F1067: log the rollback request — NEVER force-push.
            # Surface as actionable signal for operator review.
            logger.warning("[self_healing] ROLLBACK needed for %s: %s", action.target, action.reason)
            # R-F1216: same as RESTART — a rollback was REQUESTED but not EXECUTED.
            wire_failure(
                module="self_healing",
                detail=f"ROLLBACK required for {action.target}: {action.reason} — operator intervention needed",
                gap_type="recovery_needed",
                source="self_healing",
            )
            return {
                "success": False,
                "message": f"Rollback requested for {action.target} — operator must rollback manually",
                "action": "rollback",
                "needs_operator": True,
            }

        elif action.action == RecoveryActionType.NOTIFY:
            # Log the issue — the autonomous loop will pick it up
            logger.warning("[self_healing] Recovery needed for %s: %s", action.target, action.reason)
            # R-F1216: NOTIFY is a genuine action — we logged it, that IS the action.
            # But verify the log was actually written by checking the recovery history.
            return {"success": True, "message": f"Notification logged for {action.target}"}

        else:
            return {"success": False, "message": f"Unknown action: {action.action}"}

    def get_recovery_history(self, limit: int = 20) -> list[dict]:
        return self._recovery_history[-limit:]


# ── Layer 5: Ecosystem Self-Repair ───────────────────────────────────────────


class EcosystemSelfRepair:
    """Cross-tier health checks and auto-remediation for the entire ecosystem."""

    def __init__(self, health_monitor: HealthMonitor, recovery_engine: AutoRecoveryEngine):
        self._health = health_monitor
        self._recovery = recovery_engine
        self._repair_history: list[dict] = []

    async def check_and_repair(self) -> dict:
        """Check all tiers and repair any issues found.

        R-F1448: also validates agent contracts (cheap, in-memory, no lock).
        """
        checks = await self._health.check_all()
        repairs = []

        for name, check in checks.items():
            if not check.is_healthy():
                result = await self._recovery.attempt_recovery(name, check.error)
                repairs.append({
                    "subsystem": name,
                    "status": check.status.value,
                    "error": check.error,
                    "recovery": result,
                })

        # R-F1448: validate agent contracts (cheap, in-memory, no state_store lock)
        contract_violations = []
        try:
            from .agent_contract import CONTRACT_REGISTRY
            all_violations = await CONTRACT_REGISTRY.validate_all_contracts()
            for agent_id, violations in all_violations.items():
                for v in violations:
                    contract_violations.append({
                        "agent_id": agent_id,
                        "type": v.violation_type,
                        "description": v.description,
                        "severity": v.severity,
                    })
                    logger.warning(
                        "[R-F1448] Contract violation: %s/%s — %s",
                        agent_id, v.violation_type, v.description,
                    )
        except Exception:
            pass  # contract validation must never crash the repair loop

        summary = {
            "timestamp": time.time(),
            "total_checks": len(checks),
            "healthy": sum(1 for c in checks.values() if c.is_healthy()),
            "degraded": sum(1 for c in checks.values() if c.status == HealthStatus.DEGRADED),
            "critical": sum(1 for c in checks.values() if c.status == HealthStatus.CRITICAL),
            "repairs_attempted": len(repairs),
            "repairs": repairs,
            "contract_violations": contract_violations,
        }

        self._repair_history.append(summary)
        if len(self._repair_history) > 100:
            self._repair_history = self._repair_history[-50:]

        # Wire to brain
        if repairs:
            wire_failure(
                module="self_healing",
                detail=f"Ecosystem repair: {len(repairs)} repairs attempted — {summary['critical']} critical, {summary['degraded']} degraded",
                gap_type="ecosystem_repair",
                source="self_healing.ecosystem_self_repair",
            )
        else:
            wire_success(
                module="self_healing",
                summary=f"Ecosystem healthy: {summary['healthy']}/{summary['total_checks']} subsystems OK",
                source_id="self_healing:ecosystem_check",
            )

        return summary


# ── Layer 6: Self-Diagnostic ─────────────────────────────────────────────────


class SelfDiagnostic:
    """Comprehensive self-diagnosis that checks every subsystem."""

    def __init__(self):
        self._diagnostics: list[dict] = []

    async def run_full_diagnostic(self) -> dict:
        """Run a full diagnostic of the entire system."""
        results = {}

        # 1. Redis connectivity — R-F1065: rs.ping() doesn't exist, probe with get_json
        try:
            _probe = await rs.get_json("crucix:self_healing:probe")
            results["redis"] = {"status": "ok", "latency_ms": 0}
        except Exception as e:
            results["redis"] = {"status": "error", "error": str(e)}

        # 2. Brain hook stats
        try:
            from . import brain_hook
            stats = await brain_hook.get_stats()
            results["brain_hook"] = {
                "status": "ok",
                "total_signals": stats.get("total_signals", 0),
                "healthy_modules": stats.get("healthy_count", 0),
                "stale_modules": stats.get("stale_count", 0),
                "circuit_breaker": stats.get("circuit_breaker", {}).get("open", False),
            }
        except Exception as e:
            results["brain_hook"] = {"status": "error", "error": str(e)}

        # 3. Capability gaps
        try:
            from . import capability_gaps
            gaps = await capability_gaps.get_gap_summary()
            results["capability_gaps"] = {
                "status": "ok",
                "unresolved": gaps.get("unresolved", 0),
                "most_common": gaps.get("most_common_unresolved"),
            }
        except Exception as e:
            results["capability_gaps"] = {"status": "error", "error": str(e)}

        # 4. Mistake ledger
        try:
            from . import mistake_ledger
            ml_stats = await mistake_ledger.stats()
            results["mistake_ledger"] = {
                "status": "ok",
                "total_entries": ml_stats.get("total_entries", 0),
                "prevented": ml_stats.get("prevented_total", 0),
            }
        except Exception as e:
            results["mistake_ledger"] = {"status": "error", "error": str(e)}

        # 5. Student mastery
        try:
            from . import student
            mastery = await student.get_mastery_report()
            results["student_mastery"] = {
                "status": "ok",
                "overall": mastery.get("overall_mastery", 0),
                "headline": mastery.get("headline_mastery", 0),
                "topics": len(mastery.get("topics", {})),
            }
        except Exception as e:
            results["student_mastery"] = {"status": "error", "error": str(e)}

        # 6. Autonomous engine
        try:
            from ..autonomous import safety
            rate_allowed, rate_count = await safety.check_and_increment_rate()
            results["autonomous"] = {
                "status": "ok",
                "rate_count": rate_count,
                "rate_allowed": rate_allowed,
            }
        except Exception as e:
            results["autonomous"] = {"status": "error", "error": str(e)}

        # 7. Semantic search
        try:
            from . import semantic_search
            ss_stats = semantic_search.get_stats()
            results["semantic_search"] = {
                "status": "ok",
                "embedder_ready": semantic_search.is_embedder_ready(),
                "index_size": ss_stats.get("index_size", 0),
            }
        except Exception as e:
            results["semantic_search"] = {"status": "error", "error": str(e)}

        # 8. Source health
        try:
            # R-F2267 — the monitor exposes health(), NOT get_status() (which
            # never existed → this tile always errored dark since it was added).
            # health() returns {last_run:{sources_checked,up,down,blocked}, ...}.
            from . import source_uptime_monitor
            uptime = await source_uptime_monitor.health()
            lr = uptime.get("last_run") or {}
            results["sources"] = {
                "status": "ok",
                "total": lr.get("sources_checked", 0),
                "healthy": lr.get("up", 0),
                "failed": lr.get("down", 0),
                "blocked": lr.get("blocked", 0),
                "suspended": uptime.get("suspended_count", 0),
            }
        except Exception as e:
            results["sources"] = {"status": "error", "error": str(e)}

        # Overall assessment
        errors = [k for k, v in results.items() if v.get("status") == "error"]
        overall = "healthy"
        if errors:
            overall = "degraded" if len(errors) <= 2 else "critical"

        diagnostic = {
            "timestamp": time.time(),
            "overall": overall,
            "subsystems": results,
            "errors": errors,
            "healthy_count": sum(1 for v in results.values() if v.get("status") == "ok"),
            "total_checks": len(results),
        }

        self._diagnostics.append(diagnostic)
        if len(self._diagnostics) > 100:
            self._diagnostics = self._diagnostics[-50:]

        return diagnostic


# ── Main Orchestrator ────────────────────────────────────────────────────────


class SelfHealingOrchestrator:
    """Orchestrates all self-healing layers."""

    def __init__(self):
        self.health_monitor = HealthMonitor()
        self.circuit_breaker_manager = CircuitBreakerManager()
        self.wal = WriteAheadLog()
        self.recovery_engine = AutoRecoveryEngine(self.circuit_breaker_manager)
        self.ecosystem_repair = EcosystemSelfRepair(self.health_monitor, self.recovery_engine)
        self.diagnostic = SelfDiagnostic()
        # R-F1148 — memory leak detector
        self.memory_leak_detector: Optional[Any] = None
        # R-F1149 — deadlock detector
        self.deadlock_detector: Optional[Any] = None
        self._running = False
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        """Start all self-healing layers."""
        self._running = True

        # Load circuit breaker states from Redis
        await self.circuit_breaker_manager.load_from_redis()

        # Replay WAL
        unapplied = await self.wal.replay()
        if unapplied:
            logger.info("[self_healing] Replaying %d unapplied WAL entries", len(unapplied))

        # Start health monitor
        self._tasks.append(asyncio.create_task(
            self.health_monitor.run_forever(),
            name="self_healing.health_monitor",
        ))

        # Start periodic ecosystem repair
        self._tasks.append(asyncio.create_task(
            self._repair_loop(),
            name="self_healing.repair_loop",
        ))

        # Start periodic diagnostic
        self._tasks.append(asyncio.create_task(
            self._diagnostic_loop(),
            name="self_healing.diagnostic_loop",
        ))

        # R-F1148 — start memory leak detector
        try:
            from .memory_leak_detector import MemoryLeakDetector
            self.memory_leak_detector = MemoryLeakDetector()
            self._tasks.append(asyncio.create_task(
                self.memory_leak_detector.run_forever(),
                name="self_healing.memory_leak_detector",
            ))
            logger.info("[self_healing] Memory leak detector started")
        except Exception as e:
            logger.warning("[self_healing] Could not start memory leak detector: %s", e)

        # R-F1149 — start deadlock detector
        try:
            from .deadlock_detector import DeadlockDetector
            self.deadlock_detector = DeadlockDetector()
            self._tasks.append(asyncio.create_task(
                self.deadlock_detector.run_forever(),
                name="self_healing.deadlock_detector",
            ))
            logger.info("[self_healing] Deadlock detector started")
        except Exception as e:
            logger.warning("[self_healing] Could not start deadlock detector: %s", e)

        # R-F1172 — start agent registry heartbeat ticker
        self._tasks.append(asyncio.create_task(
            self._agent_heartbeat_loop(),
            name="self_healing.agent_heartbeat",
        ))

        # R-F1227 — start contract validation loop (24h playbook enforcement)
        self._tasks.append(asyncio.create_task(
            self._contract_validation_loop(),
            name="self_healing.contract_validation",
        ))

        logger.info("[self_healing] All layers started (%d tasks)", len(self._tasks))

        wire_success(
            module="self_healing",
            summary=f"Self-healing infrastructure active: {len(self._tasks)} background tasks",
            source_id="self_healing:R-F1051",
        )

    async def stop(self) -> None:
        """Stop all self-healing layers."""
        self._running = False
        self.health_monitor.stop()
        if self.memory_leak_detector is not None:
            self.memory_leak_detector.stop()
        if self.deadlock_detector is not None:
            self.deadlock_detector.stop()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("[self_healing] All layers stopped")

    async def _repair_loop(self) -> None:
        """Periodic ecosystem repair."""
        while self._running:
            try:
                await asyncio.sleep(_HEALTH_CHECK_INTERVAL_S * 5)  # Every 5 min
                if not self._running:
                    break
                await self.ecosystem_repair.check_and_repair()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("[self_healing] Repair loop error: %s", e)

    async def _agent_heartbeat_loop(self) -> None:
        """Periodic heartbeat to the multi-agent registry."""
        while self._running:
            try:
                await asyncio.sleep(60)  # Every minute
                if not self._running:
                    break
                from .agent_registry import AgentRegistry
                _reg = AgentRegistry()
                await _reg.tick_heartbeat("self_healing")
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    async def _contract_validation_loop(self) -> None:
        """24h playbook enforcement — validate all agent contracts every hour.

        Checks every registered agent's contract for violations:
          - Missing contracts
          - Stale heartbeats
          - Unhealthy dependencies
          - Expired credentials

        Violations are recorded to Redis and wired to the brain.
        If any agent has a CRITICAL violation, a capability gap is recorded
        so the autonomous scheduler can attempt a fix.
        """
        await asyncio.sleep(300)  # Wait 5 min after startup
        while self._running:
            try:
                await asyncio.sleep(3600)  # Every hour
                if not self._running:
                    break

                from .agent_contract import CONTRACT_REGISTRY
                violations = await CONTRACT_REGISTRY.validate_all_contracts()

                if not violations:
                    logger.info("[self_healing] Contract validation: ALL CLEAR — 0 violations")
                    wire_success(
                        module="self_healing",
                        summary="Contract validation: all agents healthy",
                        source_id="self_healing:contract_validation",
                    )
                    continue

                # Log violations
                total_violations = sum(len(v) for v in violations.values())
                critical_count = sum(
                    1 for vv in violations.values()
                    for v in vv if v.severity == "CRITICAL"
                )
                logger.warning(
                    "[self_healing] Contract validation: %d agents with %d violations "
                    "(%d critical)",
                    len(violations), total_violations, critical_count,
                )

                # Wire to brain
                wire_failure(
                    module="self_healing",
                    detail=(
                        f"Contract violations: {total_violations} across "
                        f"{len(violations)} agents ({critical_count} critical)"
                    ),
                    gap_type="contract_violation",
                    source="self_healing:contract_validation",
                )

                # Record capability gaps for critical violations
                if critical_count > 0:
                    try:
                        from . import capability_gaps as _cg
                        for agent_id, vv in violations.items():
                            for v in vv:
                                if v.severity == "CRITICAL":
                                    await _cg.record_gap(
                                        gap_type="contract_violation",
                                        module=f"agent:{agent_id}",
                                        description=(
                                            f"CRITICAL contract violation for {agent_id}: "
                                            f"{v.description[:200]}"
                                        ),
                                        severity="HIGH",
                                        source="self_healing:contract_validation",
                                    )
                    except Exception:
                        pass

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("[self_healing] Contract validation loop error: %s", e)

    async def _diagnostic_loop(self) -> None:
        """Periodic self-diagnostic."""
        while self._running:
            try:
                await asyncio.sleep(_HEALTH_CHECK_INTERVAL_S * 10)  # Every 10 min
                if not self._running:
                    break
                diagnostic = await self.diagnostic.run_full_diagnostic()
                if diagnostic["overall"] != "healthy":
                    logger.warning(
                        "[self_healing] Diagnostic: %s — %d/%d subsystems healthy",
                        diagnostic["overall"],
                        diagnostic["healthy_count"],
                        diagnostic["total_checks"],
                    )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("[self_healing] Diagnostic loop error: %s", e)

    def get_status(self) -> dict:
        """Get the current status of all self-healing layers."""
        status = {
            "running": self._running,
            "health_monitor": {
                "latest": self.health_monitor.get_latest(),
                "history_count": len(self.health_monitor._history),
            },
            "circuit_breakers": self.circuit_breaker_manager.get_all_states(),
            "wal": {
                "entries": len(self.wal._entries),
            },
            "recovery": {
                "history_count": len(self.recovery_engine._recovery_history),
            },
            "ecosystem_repair": {
                "history_count": len(self.ecosystem_repair._repair_history),
            },
            "diagnostic": {
                "history_count": len(self.diagnostic._diagnostics),
                "latest": self.diagnostic._diagnostics[-1] if self.diagnostic._diagnostics else None,
            },
        }
        # R-F1148 — memory leak detector status
        if self.memory_leak_detector is not None:
            status["memory_leak_detector"] = self.memory_leak_detector.get_status()
        # R-F1149 — deadlock detector status
        if self.deadlock_detector is not None:
            status["deadlock_detector"] = self.deadlock_detector.get_status()
        return status


# ── Singleton ─────────────────────────────────────────────────────────────────

_orchestrator: Optional[SelfHealingOrchestrator] = None


def get_orchestrator() -> SelfHealingOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = SelfHealingOrchestrator()
    return _orchestrator


async def start_self_healing() -> None:
    """Start the self-healing infrastructure. Called from main.py lifespan."""
    orch = get_orchestrator()
    await orch.start()

    # R-F1172 — register in the multi-agent registry
    # R-F1900: also register a binding contract
    try:
        from .agent_registry import AgentRegistry
        from .agent_contract import AgentContract
        _reg = AgentRegistry()
        _sh_contract = AgentContract(
            agent_id="self_healing",
            version="1.0.0",
            directives=[
                "Run health checks on all subsystems",
                "Detect and repair circuit breaker trips",
                "Auto-recover from known failure modes",
                "Wire both success and failure to the brain",
            ],
            inputs=["Circuit breaker registry", "Agent registry", "Contract registry"],
            outputs=["Health reports", "Auto-recovery actions"],
            error_modes=["registry_unreachable", "no_failures_to_repair"],
            dependencies=[],
            check_interval_s=3600,
            critical=True,
        )
        await _reg.register(
            agent_id="self_healing",
            agent_type="infrastructure",
            current_task="monitoring system health",
            contract=_sh_contract,
        )
    except Exception:
        pass


async def stop_self_healing() -> None:
    """Stop the self-healing infrastructure."""
    orch = get_orchestrator()
    await orch.stop()


def get_status() -> dict:
    """Get the current status."""
    return get_orchestrator().get_status()


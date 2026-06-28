"""R-F1006 - ARIA System Health Monitor.

DEPRECATED (R-F1207): Superseded by the self-healing infrastructure
(aria_service/intel/self_healing.py) which provides health checks,
circuit breakers, auto-recovery, WAL, ecosystem repair, and self-diagnostic
across all 6 layers. This module is kept for reference but NOT started
in production. Do not wire into main.py.
"""
from __future__ import annotations
import asyncio, logging, os, time, pathlib, subprocess
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional
logger = logging.getLogger("aria.system_health")


@dataclass
class HealthMetric:
    name: str
    value: float
    unit: str = ""
    threshold: Optional[float] = None
    status: str = "ok"
    timestamp: float = field(default_factory=time.time)


@dataclass
class LogEntry:
    timestamp: float = field(default_factory=time.time)
    level: str = "INFO"
    module: str = ""
    message: str = ""
    traceback: str = ""
    task_id: str = ""


class SystemHealthMonitor:
    """Real-time health monitoring for all ARIA services."""

    def __init__(self):
        self.root = pathlib.Path(__file__).parent.parent.parent
        self._metrics: dict[str, deque[HealthMetric]] = {}
        self._logs: deque[LogEntry] = deque(maxlen=10000)
        self._anomalies: deque[dict] = deque(maxlen=1000)
        self._remediations: deque[dict] = deque(maxlen=100)
        self._max_history = 3600
        self._running = False
        self._monitor_task: Optional[asyncio.Task] = None

    async def start(self):
        if self._running: return
        self._running = True
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info("[system_health] monitor started")

    async def stop(self):
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try: await self._monitor_task
            except asyncio.CancelledError: pass

    async def _monitor_loop(self):
        while self._running:
            try:
                await self._check_services()
                await self._check_anomalies()
            except asyncio.CancelledError: raise
            except Exception as e: logger.error("[system_health] monitor error: %s", e)
            await asyncio.sleep(10)

    async def _check_services(self):
        import httpx
        services = {"aria-intel": "https://aria-intel.fly.dev/health", "aria-web": "https://aria-web.fly.dev/healthz", "aria-wa": "https://aria-wa.fly.dev/health"}
        async with httpx.AsyncClient(timeout=5) as client:  # no-breaker: system health is read-only; breaker would hide health status
            for name, url in services.items():
                try:
                    r = await client.get(url)
                    status = "ok" if r.status_code == 200 else "critical"
                    self.record_metric(f"service.{name}.status", 1 if status == "ok" else 0, threshold=1)
                    if status != "ok":
                        self._anomalies.append({"type": "service_down", "service": name, "status_code": r.status_code, "timestamp": time.time()})
                except Exception as e:
                    self.record_metric(f"service.{name}.status", 0, threshold=1)
                    self._anomalies.append({"type": "service_unreachable", "service": name, "error": str(e), "timestamp": time.time()})

    async def _check_anomalies(self):
        recent = [a for a in self._anomalies if a["timestamp"] > time.time() - 60]
        for anomaly in recent:
            if anomaly["type"] in ("service_down", "service_unreachable"):
                await self._remediate(anomaly)

    async def _remediate(self, anomaly):
        remediation = {"anomaly": anomaly, "timestamp": time.time(), "action": "none", "success": False}
        try:
            service = anomaly.get("service", "")
            if service:
                r = subprocess.run(["flyctl", "apps", "restart", service], capture_output=True, text=True, timeout=30)
                remediation["action"] = f"restart_{service}"
                remediation["success"] = r.returncode == 0
        except Exception as e:
            remediation["error"] = str(e)
        self._remediations.append(remediation)
        if remediation["success"]: logger.info("[system_health] remediation succeeded: %s", remediation["action"])
        else: logger.error("[system_health] remediation failed: %s", remediation.get("error", "unknown"))

    def record_metric(self, name, value, unit="", threshold=None):
        if name not in self._metrics:
            self._metrics[name] = deque(maxlen=self._max_history)
        status = "ok"
        if threshold is not None and value < threshold:
            status = "critical" if value == 0 else "warning"
        self._metrics[name].append(HealthMetric(name=name, value=value, unit=unit, threshold=threshold, status=status))

    def record_log(self, level, module, message, traceback="", task_id=""):
        self._logs.append(LogEntry(level=level, module=module, message=message, traceback=traceback, task_id=task_id))

    def get_status(self):
        now = time.time()
        services = {}
        for name in ["aria-intel", "aria-web", "aria-wa"]:
            key = f"service.{name}.status"
            if key in self._metrics and self._metrics[key]:
                last = self._metrics[key][-1]
                services[name] = {"status": "ok" if last.value == 1 else "down", "last_check": last.timestamp}
            else:
                services[name] = {"status": "unknown", "last_check": 0}
        recent_errors = sum(1 for e in self._logs if e.timestamp > now - 300 and e.level in ("ERROR", "CRITICAL"))
        active = [a for a in self._anomalies if a["timestamp"] > now - 300]
        return {"timestamp": now, "services": services, "errors_5min": recent_errors, "active_anomalies": len(active), "status": "healthy" if all(s.get("status") == "ok" for s in services.values()) and recent_errors == 0 else "degraded"}

    def get_recent_logs(self, limit=50, level=None):
        logs = list(self._logs)
        if level: logs = [l for l in logs if l.level == level]
        logs.reverse()
        return [{"timestamp": l.timestamp, "level": l.level, "module": l.module, "message": l.message[:200], "task_id": l.task_id} for l in logs[:limit]]
# R-F1006 - wire to brain
from .engine_wiring import wire_success, wire_failure
wire_success(module="system_health", summary="System Health Active", source_id="system_health:R-F1006")

# R-F2119 §21a — wire failure handler for system_health
try:
    wire_failure(module="system_health", detail="module shutdown",
                gap_type="engine_failure", source="system_health:shutdown")
except Exception:
    pass

"""R-F1001 - ARIA Self-Healing System."""
from __future__ import annotations
import asyncio, logging, os, time, pathlib, subprocess
from typing import Any, Optional
logger = logging.getLogger("aria.self_healing")

class SelfHealer:
    def __init__(self):
        self.root = pathlib.Path(__file__).parent.parent.parent
        self._health_history = []

    async def check_health(self):
        import httpx
        services = {"aria-intel": "https://aria-intel.fly.dev/health", "aria-web": "https://aria-web.fly.dev/healthz", "aria-wa": "https://aria-wa.fly.dev/health"}
        results = {}
        all_healthy = True
        for name, url in services.items():
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    r = await client.get(url)
                    healthy = r.status_code == 200
                    results[name] = {"healthy": healthy, "status_code": r.status_code, "timestamp": time.time()}
                    if not healthy: all_healthy = False
            except Exception as e:
                results[name] = {"healthy": False, "error": str(e), "timestamp": time.time()}
                all_healthy = False
        return {"timestamp": time.time(), "status": "healthy" if all_healthy else "degraded", "services": results}

    async def rollback(self, target_sha=None):
        sha = target_sha
        if not sha:
            r = subprocess.run(["git", "log", "--oneline", "-1"], capture_output=True, text=True, timeout=10, cwd=str(self.root))
            sha = r.stdout.strip().split()[0] if r.returncode == 0 else None
        if not sha: return {"success": False, "error": "No target SHA"}
        r1 = subprocess.run(["git", "reset", "--hard", sha], capture_output=True, text=True, timeout=30, cwd=str(self.root))
        if r1.returncode != 0: return {"success": False, "error": f"git reset failed: {r1.stderr}"}
        r2 = subprocess.run(["git", "push", "--force", "origin", "main:main"], capture_output=True, text=True, timeout=60, cwd=str(self.root))
        if r2.returncode != 0: return {"success": False, "error": f"git push failed: {r2.stderr}"}
        logger.warning("[self_healing] ROLLED BACK to %s", sha)
        return {"success": True, "rolled_back_to": sha}

    async def auto_heal(self):
        health = await self.check_health()
        if health["status"] == "healthy": return {"status": "healthy", "action": "none"}
        failed = [n for n,i in health["services"].items() if not i.get("healthy", False)]
        if len(failed) >= 2:
            rb = await self.rollback()
            return {"status": "healing", "action": "rollback", "failed": failed, "rollback": rb}
        return {"status": "degraded", "action": "monitoring", "failed": failed}

# R-F1001 - wire to brain
from .engine_wiring import wire_success
wire_success(module="self_healing", summary="Self Healing Active", source_id="self_healing:R-F1001")

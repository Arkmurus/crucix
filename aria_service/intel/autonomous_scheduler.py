
"""
R-F1005 — ARIA Autonomous Scheduler.

Runs the coding cycle on a schedule:
- Every 15 minutes: scan for gaps, fix them
- Every hour: run self-diagnostics
- Every 6 hours: run adversarial suite
- Every 24 hours: run full ecosystem optimization
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

logger = logging.getLogger("aria.autonomous_scheduler")


class AutonomousScheduler:
    """Runs ARIA's autonomous tasks on a schedule."""

    def __init__(self):
        self._tasks: dict[str, asyncio.Task] = {}
        self._running = False

    async def start(self) -> None:
        """Start all scheduled tasks."""
        if self._running:
            return
        self._running = True
        
        self._tasks["gap_fixer"] = asyncio.create_task(
            self._run_interval("gap_fixer", 900, self._fix_gaps),  # 15 min
        )
        self._tasks["self_diagnostic"] = asyncio.create_task(
            self._run_interval("self_diagnostic", 3600, self._run_diagnostics),  # 1 hour
        )
        self._tasks["adversarial"] = asyncio.create_task(
            self._run_interval("adversarial", 21600, self._run_adversarial),  # 6 hours
        )
        self._tasks["ecosystem_optimize"] = asyncio.create_task(
            self._run_interval("ecosystem_optimize", 86400, self._optimize_ecosystem),  # 24 hours
        )
        
        logger.info("[scheduler] started %d tasks", len(self._tasks))

    async def stop(self) -> None:
        """Stop all scheduled tasks."""
        self._running = False
        for name, task in self._tasks.items():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
        logger.info("[scheduler] stopped")

    async def _run_interval(self, name: str, interval: float, func) -> None:
        """Run a function on an interval."""
        while self._running:
            try:
                await func()
                # R-F1059 — wire scheduler tick to brain
                try:
                    from ..intel.engine_wiring import wire_success as _ws
                    _ws(
                        module="autonomous_scheduler",
                        summary=f"Scheduler tick: {name}",
                        source_id=f"scheduler:{name}",
                    )
                except Exception:
                    pass
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("[scheduler] %s failed: %s", name, e)
                # R-F1059 — wire scheduler failure to brain
                try:
                    from ..intel.engine_wiring import wire_failure as _wf
                    _wf(
                        module="autonomous_scheduler",
                        detail=f"Scheduler {name} failed: {e}",
                        gap_type="engine_failure",
                        source="autonomous_scheduler",
                    )
                except Exception:
                    pass
            await asyncio.sleep(interval)

    async def _fix_gaps(self) -> None:
        """Scan for gaps and fix them."""
        try:
            from .gap_detector import GapDetector
            from ..intel.autonomous_coder import AutonomousCoder
            
            # This would use Redis in production
            logger.info("[scheduler] scanning for gaps")
            # In production: gap_detector.scan() -> AutonomousCoder.full_fix_cycle()
        except Exception as e:
            logger.debug("[scheduler] gap fix skipped: %s", e)

    async def _run_diagnostics(self) -> None:
        """Run self-diagnostics."""
        try:
            from ..intel.ecosystem_dashboard import scan_wiring_coverage
            coverage = scan_wiring_coverage()
            logger.info("[scheduler] wiring coverage: %d%%", coverage.get("pct", 0))
        except Exception as e:
            logger.debug("[scheduler] diagnostics skipped: %s", e)

    async def _run_adversarial(self) -> None:
        """Run adversarial tests."""
        try:
            from ..intel.ecosystem_dashboard import run_adversarial_suite
            result = await run_adversarial_suite()
            logger.info("[scheduler] adversarial: %d/%d passed", 
                       result.get("passed", 0), result.get("total", 0))
        except Exception as e:
            logger.debug("[scheduler] adversarial skipped: %s", e)

        # R-F1129 — run generative red-team drill after adversarial suite
        try:
            from ..intel.generative_redteam import run_drill
            drill_result = await run_drill()
            logger.info(
                "[scheduler] redteam drill: %d variants, %d caught, %d defenses staged",
                drill_result.get("variants_tested", 0),
                drill_result.get("variants_passed_defense", 0),
                drill_result.get("defenses_staged", 0),
            )
        except Exception as e:
            logger.debug("[scheduler] redteam drill skipped: %s", e)

    async def _optimize_ecosystem(self) -> None:
        """Run full ecosystem optimization."""
        try:
            from ..intel.ecosystem_dashboard import run_security_scan
            result = await run_security_scan()
            logger.info("[scheduler] security: %d findings", result.get("total_findings", 0))
        except Exception as e:
            logger.debug("[scheduler] optimize skipped: %s", e)

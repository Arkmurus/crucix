
"""
R-F1005 — ARIA Autonomous Scheduler.

Runs the coding cycle on a schedule:
- Every 5 minutes: DD trigger monitor (signal check + watchlist match)
- Every 15 minutes: scan for gaps, fix them
- Every hour: run self-diagnostics
- Every 6 hours: run adversarial suite + generative red-team drill
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
        
        self._tasks["dd_monitor"] = asyncio.create_task(
            self._run_interval("dd_monitor", 300, self._run_dd_monitor),  # 5 min
        )
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
        self._tasks["vault_retry"] = asyncio.create_task(
            self._run_interval("vault_retry", 43200, self._retry_pending_vault),  # 12 hours
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
        """Scan for gaps and fix them using the ARIACoder pipeline.

        Wired to the real gap detection (R-F1207). Scans Redis error-ledger,
        chat-audit, capability-gaps, and mistake-ledger signals, then feeds
        actionable gaps to the ARIACoder for autonomous remediation.
        """
        try:
            from .gap_detector import GapDetector
            from ..autonomous.self_coder import ARIACoder

            logger.info("[scheduler] scanning for gaps via GapDetector")
            detector = GapDetector()
            gaps = await detector.scan()
            if gaps:
                logger.info(
                    "[scheduler] found %d gaps — feeding to ARIACoder",
                    len(gaps),
                )
                coder = ARIACoder(gap_detector=detector)
                for gap in gaps[:5]:  # Process top 5 per cycle
                    try:
                        result = await coder.fix_gap(gap)
                        logger.info(
                            "[scheduler] gap %s: %s",
                            gap.gap_id, result.get("status", "unknown"),
                        )
                    except Exception as _fix_e:
                        logger.warning(
                            "[scheduler] fix_gap failed for %s: %s",
                            gap.gap_id, _fix_e,
                        )
            else:
                logger.info("[scheduler] no gaps found")
        except Exception as e:
            logger.debug("[scheduler] gap fix skipped: %s", e)

    async def _run_dd_monitor(self) -> None:
        """Run DD trigger monitor — check signals and fire DD triggers."""
        try:
            from .dd_trigger_pipeline import monitor_and_trigger
            result = await monitor_and_trigger()
            logger.info(
                "[scheduler] dd_monitor: %d signals, %d matches, %d triggers fired",
                result.get("signals_found", 0),
                result.get("matches_found", 0),
                result.get("triggers_fired", 0),
            )
        except Exception as e:
            logger.debug("[scheduler] dd_monitor skipped: %s", e)

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

    async def _retry_pending_vault(self) -> None:
        """Re-drive pending vault signups every 12 hours.

        R-F1490/R-F1502: runs determine_and_drive_all for all pending portals.
        Each portal gets an honest determination: open_api, registered, or
        needs_operator (with blocker reason). Declined/deferred portals are
        suppressed from the operator email digest. Email throttled to once/24h.
        """
        try:
            from .portal_registry import determine_and_drive_all, email_portal_requirements_to_operator

            results = await determine_and_drive_all()
            counts: dict[str, int] = {}
            for r in results:
                s = r.get("status", "error")
                counts[s] = counts.get(s, 0) + 1

            if results:
                logger.info(
                    "[R-F1502] Vault re-drive: %s",
                    ", ".join(f"{k}={v}" for k, v in sorted(counts.items())),
                )

            # Email operator if there are actionable portals (throttled 24h)
            actionable = [r for r in results
                          if r.get("status") == "needs_operator"
                          and not r.get("declined")
                          and not r.get("deferred")]
            if actionable:
                from . import state_store as _ss
                import time as _t
                _last = await _ss.get("crucix:portal_registry:reqs_emailed_at")
                if not _last or (_t.time() - float(_last)) > 86400:
                    res = await email_portal_requirements_to_operator()
                    if res.get("sent") or res.get("counts"):
                        await _ss.set("crucix:portal_registry:reqs_emailed_at", str(_t.time()))
                        logger.info("[R-F1502] Emailed operator portal digest: %s", res.get("counts"))
        except Exception as e:
            logger.debug("[R-F1502] vault re-drive skipped: %s", e)

    async def _optimize_ecosystem(self) -> None:
        """Run full ecosystem optimization."""
        try:
            from ..intel.ecosystem_dashboard import run_security_scan
            result = await run_security_scan()
            logger.info("[scheduler] security: %d findings", result.get("total_findings", 0))
        except Exception as e:
            logger.debug("[scheduler] optimize skipped: %s", e)

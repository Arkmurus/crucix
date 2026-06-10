
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
        """Retry pending vault signups every 12 hours.

        R-F1490: the auto-registration runs once at boot (120s delay). If it
        fails for any portal (network issue, rate limit, email verification
        timeout), the portal stays 'pending' forever. This scheduled task
        retries all pending entries, attempting registration again.

        Only retries portals that don't require CAPTCHA (those are
        operator-deferred by design). Logs results but never raises.
        """
        try:
            from .agent_signup_vault import get_vault
            from .portal_registry import PORTALS, register_for_portal, is_registered

            vault = get_vault()
            pending = vault.list(status="pending", limit=100)
            if not pending:
                return

            retried = 0
            succeeded = 0
            still_pending = 0
            failed = 0

            for entry in pending:
                portal_id = entry["site_id"]
                portal = next((p for p in PORTALS if p.id == portal_id), None)
                if not portal:
                    continue

                # Skip CAPTCHA-protected portals (operator-deferred)
                if portal.requires_captcha:
                    continue

                # Skip if already registered (check Redis credentials)
                try:
                    if await is_registered(portal_id):
                        vault.update_status(portal_id, "registered",
                            notes="Credentials found in vault — marked registered.")
                        succeeded += 1
                        continue
                except Exception:
                    pass

                retried += 1
                try:
                    outcome = await register_for_portal(portal_id)
                    if outcome.get("success"):
                        vault.update_status(portal_id, "registered",
                            notes=f"Auto-registered on retry: {outcome.get('message', '')[:100]}")
                        succeeded += 1
                    elif outcome.get("requires_operator"):
                        still_pending += 1
                    elif outcome.get("requires_email_verify"):
                        # Email verification needed — keep as pending
                        still_pending += 1
                    else:
                        failed += 1
                        logger.debug(
                            "[R-F1490] retry failed for %s: %s",
                            portal_id, outcome.get("error", "unknown"),
                        )
                except Exception as e:
                    failed += 1
                    logger.debug("[R-F1490] retry exception for %s: %s", portal_id, e)

            if retried > 0 or succeeded > 0:
                logger.info(
                    "[R-F1490] Vault retry: %d retried, %d succeeded, "
                    "%d still pending, %d failed",
                    retried, succeeded, still_pending, failed,
                )

            # R-F1498: email the operator exactly what each still-pending portal
            # needs (free key / signup / CAPTCHA / paid) so they can act — most
            # fundamentally need the operator, not more auto-registration. Throttled
            # to once per 24h so the autonomous loop never spams.
            try:
                from . import state_store as _ss
                import time as _t
                _last = await _ss.get("crucix:portal_registry:reqs_emailed_at")
                if not _last or (_t.time() - float(_last)) > 86400:
                    from .portal_registry import email_portal_requirements_to_operator
                    res = await email_portal_requirements_to_operator()
                    if res.get("sent") or res.get("counts"):
                        await _ss.set("crucix:portal_registry:reqs_emailed_at", str(_t.time()))
                        logger.info("[R-F1498] Emailed operator portal requirements: %s", res.get("counts"))
            except Exception as _ee:
                logger.debug("[R-F1498] requirements email skipped: %s", _ee)
        except Exception as e:
            logger.debug("[R-F1490] vault retry skipped: %s", e)

    async def _optimize_ecosystem(self) -> None:
        """Run full ecosystem optimization."""
        try:
            from ..intel.ecosystem_dashboard import run_security_scan
            result = await run_security_scan()
            logger.info("[scheduler] security: %d findings", result.get("total_findings", 0))
        except Exception as e:
            logger.debug("[scheduler] optimize skipped: %s", e)

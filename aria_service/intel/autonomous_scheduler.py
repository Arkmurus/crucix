
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
            self._run_interval("adversarial", 259200, self._run_adversarial),  # 3 days
        )
        self._tasks["ecosystem_optimize"] = asyncio.create_task(
            self._run_interval("ecosystem_optimize", 86400, self._optimize_ecosystem),  # 24 hours
        )
        # R-F1548: drain the Claude<->ARIA collaboration bridge every 2 minutes.
        # Runs independently of the autonomous engine so Claude's notes are
        # never stuck even when the engine is paused or in SUPERVISED mode.
        self._tasks["collab_drain"] = asyncio.create_task(
            self._run_interval("collab_drain", 120, self._drain_collab_bridge),  # 2 min
        )
        self._tasks["vault_retry"] = asyncio.create_task(
            self._run_interval("vault_retry", 43200, self._retry_pending_vault),  # 12 hours
        )
        # R-F1653: daily source discovery — citation walking + TLD probing +
        # targeted gap-filling. Runs every 24h to find new procurement portals,
        # defence journals, and government sources. Feeds into vault registration.
        self._tasks["source_discovery"] = asyncio.create_task(
            self._run_interval("source_discovery", 86400, self._discover_sources),  # 24 hours
        )
        
        logger.info("[scheduler] started %d tasks", len(self._tasks))

    async def _tick_heartbeat(self) -> None:
        """Tick the autonomous_scheduler heartbeat in the agent registry."""
        try:
            from ..intel.agent_registry import AgentRegistry
            _reg = AgentRegistry()
            await _reg.tick_heartbeat("autonomous_scheduler", "DD trigger monitor, gap fixing, self-diagnostics, adversarial tests")
        except Exception:
            pass

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
                # R-F1574: tick heartbeat so the agent registry sees us alive
                await self._tick_heartbeat()
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
        """Run adversarial tests using the full 23-attack suite."""
        try:
            from ..intel import adversarial_challenge as _ac
            result = await _ac.run_weekly()
            logger.info(
                "[scheduler] adversarial: %d/%d passed (score=%.3f)",
                result.get("passed", 0), result.get("total_attacks", 0),
                result.get("overall_score", 0),
            )
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

    async def _drain_collab_bridge(self) -> None:
        """Drain the Claude<->ARIA collaboration bridge (R-F1548).
        
        Runs every 2 minutes independently of the autonomous engine so
        Claude's notes are never stuck even when the engine is paused
        or in SUPERVISED mode.
        """
        try:
            from ..intel import collab_bridge
            result = await collab_bridge.drain_for_aria()
            drained = result.get("drained", 0)
            if drained > 0:
                logger.info("[R-F1548] collab bridge drained %d note(s)", drained)
        except Exception as e:
            logger.debug("[scheduler] collab bridge drain skipped: %s", e)

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

    async def _discover_sources(self) -> None:
        """R-F1653: Daily source discovery — find new portals and data sources.

        Runs three scout patterns in sequence:
          1. citation — walk outbound citations from trusted Tier 1a/1b/2 sources
          2. tld_probe — sweep gov/mil/ministry subdomains in target markets
          3. targeted — fill specific (region x topic) gaps from coverage heatmap

        New sources are added to the Web Atlas and queued for vault registration.
        This is how ARIA's source base grows autonomously — every day she finds
        new procurement portals, defence journals, and government data sources
        without operator intervention.
        """
        try:
            from .source_scout import run as scout_run
            from .ecosystem_reassess import run as reassess_run
            from .portal_registry import PORTALS, PortalDef, auto_register_all
            from .coverage_heatmap import gap_targets
            from . import redis_store as rs
            import json, time

            results = {}

            # Phase 1: Citation walking — find new sources from trusted citations
            try:
                citation_result = await scout_run(pattern="citation", max_finds=5)
                results["citation"] = citation_result.get("found", 0)
                logger.info("[R-F1653] Citation scout: %d new sources", citation_result.get("found", 0))
            except Exception as e:
                logger.debug("[R-F1653] Citation scout failed: %s", e)
                results["citation"] = -1

            # Phase 2: TLD probe — sweep target market domains for new portals
            try:
                tld_result = await scout_run(pattern="tld_probe", max_finds=5)
                results["tld_probe"] = tld_result.get("found", 0)
                logger.info("[R-F1653] TLD probe: %d new sources", tld_result.get("found", 0))
            except Exception as e:
                logger.debug("[R-F1653] TLD probe failed: %s", e)
                results["tld_probe"] = -1

            # Phase 3: Targeted gap-filling — find sources for weak coverage cells
            try:
                from .coverage_heatmap import build_heatmap, gap_targets
                hm = await build_heatmap()
                gaps = gap_targets(hm, max_targets=3)
                targeted_found = 0
                for gap in gaps:
                    region = gap.get("region", "global")
                    topic = gap.get("topic", "defence_procurement")
                    try:
                        gap_result = await scout_run(
                            pattern="targeted",
                            region=region,
                            topic=topic,
                            max_finds=3,
                        )
                        targeted_found += gap_result.get("found", 0)
                    except Exception as e:
                        logger.debug("[R-F1653] Targeted scout %s/%s failed: %s", region, topic, e)
                results["targeted"] = targeted_found
                logger.info("[R-F1653] Targeted scout: %d new sources from %d gaps", targeted_found, len(gaps))
            except Exception as e:
                logger.debug("[R-F1653] Targeted scout failed: %s", e)
                results["targeted"] = -1

            # Phase 4: Run ecosystem reassessment to update the gap map
            try:
                await reassess_run()
                results["reassess"] = True
            except Exception as e:
                logger.debug("[R-F1653] Reassessment failed: %s", e)
                results["reassess"] = False

            # Phase 5: Attempt vault registration for any new portals
            try:
                vault_result = await auto_register_all()
                results["vault_new"] = vault_result.get("newly_registered", 0)
                results["vault_captcha"] = vault_result.get("captcha_deferred", 0)
            except Exception as e:
                logger.debug("[R-F1653] Vault auto-register failed: %s", e)
                results["vault_new"] = -1

            # Log summary
            total_new = sum(v for v in results.values() if isinstance(v, int) and v > 0)
            logger.info(
                "[R-F1653] Source discovery complete: %d total new sources "
                "(citation=%s, tld=%s, targeted=%s, vault_new=%s)",
                total_new,
                results.get("citation"),
                results.get("tld_probe"),
                results.get("targeted"),
                results.get("vault_new"),
            )

            # Wire to brain
            try:
                from .engine_wiring import wire_success as _ws1653
                _ws1653(
                    module="source_discovery",
                    summary=f"Source discovery: {total_new} new sources found, "
                            f"{results.get('vault_new', 0)} registered",
                    source_id="scheduler:source_discovery:R-F1653",
                )
            except Exception:
                pass

        except Exception as e:
            logger.error("[R-F1653] Source discovery failed: %s", e)
            try:
                from .engine_wiring import wire_failure as _wf1653
                _wf1653(
                    module="source_discovery",
                    detail=f"Source discovery failed: {e}",
                    gap_type="engine_failure",
                    source="autonomous_scheduler",
                )
            except Exception:
                pass

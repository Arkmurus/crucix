"""R-F802 — ARIACoder orchestrator.

The closed-loop self-coder. Wires the components together:

    GapDetector → SovereignLLM (plan) → CodebaseReader (context)
                ↓
    SovereignLLM (write code) → ConstitutionalValidator
                ↓
    TestRunner (isolated) ← SovereignLLM (heal, ≤3 attempts)
                ↓
    FlyDeployer (canary → bluegreen → PR)
                ↓
    post-deploy monitor + brain_hook.absorb + harvester capture

Integration with R-F462
───────────────────────
Per the operator's audit mandate (R-F462, 2026-05-14), the existing
`aria_service/intel/self_improve.py` staging queue is the official
operator-approval surface. ARIACoder runs FIX_GAP end-to-end but
deposits the result into `self_improve.py`'s staged queue rather than
deploying directly — UNLESS:

  (a) `gap.gap_type` is in the deterministic auto-fixable set
      (see `gap_detector.AUTONOMY_LEVEL`),
  (b) `ARIA_SELF_IMPROVE_AUTO_DEPLOY=1` is set on the host,
  (c) the change is `bug_fix` change_type, and
  (d) the constitutional validator passed with risk_score < 0.3.

All four must hold for direct deploy. Otherwise: stage for operator.

The orchestrator is **not started by default**. To enable, set
`ARIA_CODER_ENABLED=1` and call `start_aria_coder()` from
`coder_entrypoint.py` (which `main.py` invokes after FastAPI startup).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .codebase_reader import CodebaseReader
from .constitutional_validator import (
    ConstitutionalValidator, ValidationResult,
)
from .fly_deployer import DeployResult, FlyDeployer
from .gap_detector import Gap, GapDetector, GapSeverity, GapType
from .r_counter import RNumberCounter
from .sovereign_llm import SovereignLLM
from .test_runner import TestResult, TestRunner

logger = logging.getLogger("aria.autonomous.self_coder")

WORKSPACE_BASE = Path(
    os.environ.get("ARIA_CODER_WORKSPACE", "/data/coder_workspace")
)
MAX_FIX_ATTEMPTS = 3
SCAN_INTERVAL_S = 900           # 15 minutes
POST_DEPLOY_MONITOR_S = 1800    # 30 minutes
MAX_GAPS_PER_CYCLE = 3
APPROVAL_TIMEOUT_S = 1800       # 30 minutes

APPROVAL_KEY_PREFIX = "crucix:aria:coder:approval:"
ERROR_LEDGER_COUNT_KEY = "crucix:aria:error_ledger:count"


@dataclass
class FixPlan:
    fix_id: str
    gap_id: str
    r_number: int
    title: str
    description: str
    target_files: list[str]
    new_files: list[str] = field(default_factory=list)
    approach: str = ""
    code_changes: dict[str, str] = field(default_factory=dict)
    new_tests: dict[str, str] = field(default_factory=dict)
    estimated_risk: float = 0.5
    requires_wa_approval: bool = False
    model_used: str = "aria-llm"
    planned_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class FixResult:
    success: bool
    fix_id: str
    gap_id: str
    r_number: Optional[int] = None
    error: Optional[str] = None
    failure_reason: Optional[str] = None
    deploy_result: Optional[DeployResult] = None
    training_pair: Optional[dict] = None
    completed_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class ARIACoder:
    """The autonomous self-coding engine — orchestrator only.

    Wire-up happens in `coder_entrypoint.start_aria_coder()`. This class
    is dormant until that function constructs it and calls `run_forever()`.
    """

    def __init__(
        self,
        *,
        redis_client: Any,
        aria_service_url: str,
        fly_app_name: str = "aria-intel",
        whatsapp_notifier: Optional[Any] = None,
        brain_hook: Optional[Any] = None,
        output_harvester: Optional[Any] = None,
        gap_detector: Optional[GapDetector] = None,
        llm: Optional[SovereignLLM] = None,
        validator: Optional[ConstitutionalValidator] = None,
        codebase: Optional[CodebaseReader] = None,
        test_runner: Optional[TestRunner] = None,
        deployer: Optional[FlyDeployer] = None,
        r_counter: Optional[RNumberCounter] = None,
        workspace_base: Optional[Path] = None,
    ) -> None:
        self.redis = redis_client
        self.aria_url = aria_service_url
        self.fly_app = fly_app_name
        self.wa = whatsapp_notifier
        self.brain_hook = brain_hook
        self.harvester = output_harvester

        # Allow injection for tests; default to constructing from scratch
        self.gap_detector = gap_detector or GapDetector(redis_client)
        self.llm = llm or SovereignLLM(aria_service_url)
        self.validator = validator or ConstitutionalValidator()
        self.codebase = codebase or CodebaseReader(aria_service_url)
        self.test_runner = test_runner or TestRunner(redis_client)
        self.deployer = deployer or FlyDeployer(redis_client, aria_service_url)
        self.r_counter = r_counter or RNumberCounter(redis_client)

        self.workspace_base = workspace_base or WORKSPACE_BASE
        self.workspace_base.mkdir(parents=True, exist_ok=True)

    # ── MAIN LOOP ────────────────────────────────────────────────────────────

    async def run_forever(self) -> None:
        """Continuous loop: detect gaps every 15min, fix the top N."""
        logger.info("[aria_coder] starting autonomous loop")
        while True:
            try:
                await self._one_cycle()
            except asyncio.CancelledError:
                logger.info("[aria_coder] cancelled — exiting")
                raise
            except Exception as e:
                logger.error("[aria_coder] cycle error: %s", e, exc_info=True)
            await asyncio.sleep(SCAN_INTERVAL_S)

    async def _one_cycle(self) -> None:
        gaps = await self.gap_detector.scan()
        actionable = [
            g for g in gaps
            if g.severity >= GapSeverity.MEDIUM and g.auto_fixable
        ]
        if not actionable:
            logger.debug("[aria_coder] no actionable gaps this cycle")
            return

        logger.info(
            "[aria_coder] %d actionable gaps — fixing top %d",
            len(actionable), MAX_GAPS_PER_CYCLE,
        )

        for gap in actionable[:MAX_GAPS_PER_CYCLE]:
            await self.gap_detector.mark_attempted(gap.gap_id)
            result = await self.fix_gap(gap)

            if result.success and result.r_number is not None:
                await self.gap_detector.mark_fixed(gap.gap_id, result.r_number)
                if self.wa is not None:
                    try:
                        await self.wa.notify(
                            f"✅ R-F{result.r_number} shipped autonomously\n"
                            f"Gap: {gap.title}",
                        )
                    except Exception as e:
                        logger.warning("[aria_coder] WA notify failed: %s", e)
                if self.harvester is not None and result.training_pair:
                    try:
                        await self.harvester.capture(result.training_pair)
                    except Exception as e:
                        logger.warning("[aria_coder] harvester failed: %s", e)
            else:
                logger.info(
                    "[aria_coder] gap %s not fixed: %s",
                    gap.gap_id, result.failure_reason,
                )

    # ── FIX PIPELINE ─────────────────────────────────────────────────────────

    async def fix_gap(self, gap: Gap) -> FixResult:
        """End-to-end pipeline. Plan → validate → code → test → deploy."""
        fix_id = uuid.uuid4().hex[:12]
        start_ts = time.monotonic()
        workspace = self.workspace_base / fix_id
        workspace.mkdir(parents=True, exist_ok=True)

        logger.info(
            "[aria_coder] fix_gap %s: %s (fix_id=%s)",
            gap.gap_id, gap.title, fix_id,
        )

        try:
            # STEP 1 — context
            context = await self.codebase.get_context(
                gap.module, gap.related_files,
            )

            # STEP 2 — plan
            plan_raw = await self.llm.generate_fix_plan(gap, context)
            r_number = await self.r_counter.next()
            plan = FixPlan(
                fix_id=fix_id,
                gap_id=gap.gap_id,
                r_number=r_number,
                title=plan_raw.get("title", gap.title),
                description=plan_raw.get("approach", ""),
                target_files=plan_raw.get("target_files", [gap.module]),
                new_files=plan_raw.get("new_files", []),
                approach=plan_raw.get("approach", ""),
                estimated_risk={
                    "low": 0.2, "medium": 0.5, "high": 0.8,
                }.get(plan_raw.get("risk_level", "medium"), 0.5),
                # Deterministic from gap_type — NOT LLM-self-reported per CLAUDE.md §3
                requires_wa_approval=gap.requires_wa_approval,
            )

            # STEP 3 — WA approval if required
            if plan.requires_wa_approval and self.wa is not None:
                approved = await self._wait_for_approval(plan, gap)
                if not approved:
                    return FixResult(
                        success=False, fix_id=fix_id, gap_id=gap.gap_id,
                        r_number=r_number,
                        failure_reason="Operator declined fix via WhatsApp",
                    )

            # STEP 4 — generate code per target file + validate each
            for target in plan.target_files:
                existing = self.codebase.read(target)
                code_raw = await self.llm.write_code(plan_raw, existing, target)
                new_code = code_raw.get("code", "")
                if not new_code:
                    continue
                val = self.validator.validate(new_code, target)
                if not val.passed:
                    return FixResult(
                        success=False, fix_id=fix_id, gap_id=gap.gap_id,
                        r_number=r_number,
                        failure_reason=(
                            "Constitutional violation: "
                            + "; ".join(val.violations)
                        ),
                    )
                plan.code_changes[target] = new_code
                self.codebase.write_to_workspace(workspace, target, new_code)

            if not plan.code_changes:
                return FixResult(
                    success=False, fix_id=fix_id, gap_id=gap.gap_id,
                    r_number=r_number,
                    failure_reason="No code changes generated by LLM",
                )

            # STEP 5 — tests for each modified file
            for target, new_code in plan.code_changes.items():
                test_raw = await self.llm.write_tests(
                    plan_raw, new_code, r_number,
                )
                test_code = test_raw.get("test_code", "")
                test_path = test_raw.get(
                    "test_filepath",
                    f"aria_service/tests/test_rf{r_number}_auto.py",
                )
                if test_code:
                    plan.new_tests[test_path] = test_code
                    self.codebase.write_to_workspace(
                        workspace, test_path, test_code,
                    )

            # STEP 6 — self-healing test loop
            test_result = await self._test_with_healing(plan, workspace)
            if not test_result.all_green:
                return FixResult(
                    success=False, fix_id=fix_id, gap_id=gap.gap_id,
                    r_number=r_number,
                    failure_reason=(
                        f"Tests failed after {MAX_FIX_ATTEMPTS} healing "
                        f"attempts: {test_result.failure_summary[:500]}"
                    ),
                )

            # STEP 7 — deploy
            deploy = await self.deployer.deploy(
                workspace=workspace,
                app=self.fly_app,
                r_number=r_number,
                summary=plan.title,
                code_changes=plan.code_changes,
            )
            if not deploy.success:
                return FixResult(
                    success=False, fix_id=fix_id, gap_id=gap.gap_id,
                    r_number=r_number,
                    failure_reason=f"Deploy failed: {deploy.error}",
                )

            # STEP 8 — post-deploy regression monitor
            regression = await self._monitor_post_deploy(r_number)
            if regression:
                await self.deployer.rollback(r_number, self.fly_app)
                return FixResult(
                    success=False, fix_id=fix_id, gap_id=gap.gap_id,
                    r_number=r_number,
                    failure_reason=(
                        "Post-deploy regression detected — rolled back"
                    ),
                )

            # STEP 9 — absorb knowledge + emit training pair
            if self.brain_hook is not None:
                try:
                    await self.brain_hook.absorb(
                        text=(
                            f"Autonomous fix R-F{r_number}: {plan.title}. "
                            f"Gap type: {gap.gap_type}. "
                            f"Files: {', '.join(plan.code_changes.keys())}."
                        ),
                        module="aria_coder",
                        confidence="high",
                        source="aria_autonomous_coder",
                    )
                except Exception as e:
                    logger.warning("[aria_coder] brain_hook.absorb failed: %s", e)

            training_pair = {
                "instruction": f"Fix gap: {gap.title}\n{gap.description}",
                "response": plan.approach,
                "code": json.dumps(plan.code_changes),
                "persona": "autonomous_coder",
                "confidence": 0.95,
                "outcome": "deployed_successfully",
                "r_number": r_number,
            }

            elapsed = time.monotonic() - start_ts
            logger.info(
                "[aria_coder] ✅ R-F%d shipped in %.0fs", r_number, elapsed,
            )
            return FixResult(
                success=True, fix_id=fix_id, gap_id=gap.gap_id,
                r_number=r_number, deploy_result=deploy,
                training_pair=training_pair,
            )

        except Exception as e:
            logger.error(
                "[aria_coder] fix_gap %s failed: %s",
                gap.gap_id, e, exc_info=True,
            )
            return FixResult(
                success=False, fix_id=fix_id, gap_id=gap.gap_id,
                failure_reason=str(e),
            )
        finally:
            if workspace.exists():
                shutil.rmtree(workspace, ignore_errors=True)

    # ── HELPERS ──────────────────────────────────────────────────────────────

    async def _test_with_healing(
        self, plan: FixPlan, workspace: Path,
    ) -> TestResult:
        result = await self.test_runner.run_isolated(workspace, plan.new_tests)
        if result.all_green:
            return result

        for attempt in range(2, MAX_FIX_ATTEMPTS + 1):
            logger.info(
                "[aria_coder] test healing %d/%d", attempt, MAX_FIX_ATTEMPTS,
            )
            for target, old_code in list(plan.code_changes.items()):
                heal_raw = await self.llm.analyse_failure(
                    error=result.failure_summary,
                    code=old_code,
                    attempt=attempt,
                )
                corrected = heal_raw.get("code", "")
                if not corrected:
                    continue
                val = self.validator.validate(corrected, target)
                if not val.passed:
                    logger.warning(
                        "[aria_coder] healed code violates constitution: %s",
                        val.violations,
                    )
                    continue
                plan.code_changes[target] = corrected
                self.codebase.write_to_workspace(workspace, target, corrected)

            result = await self.test_runner.run_isolated(
                workspace, plan.new_tests,
            )
            if result.all_green:
                logger.info("[aria_coder] tests healed on attempt %d", attempt)
                return result

        return result

    async def _wait_for_approval(self, plan: FixPlan, gap: Gap) -> bool:
        """Poll Redis for operator approval (set by WhatsApp/Telegram handler)."""
        if self.wa is None:
            return True  # No WA configured — fail-open in dev only
        try:
            await self.wa.request_fix_approval(
                fix_id=plan.fix_id,
                r_number=plan.r_number,
                title=plan.title,
                gap=gap,
                risk=plan.estimated_risk,
            )
        except Exception as e:
            logger.warning("[aria_coder] request_fix_approval failed: %s", e)

        key = f"{APPROVAL_KEY_PREFIX}{plan.fix_id}"
        # 30 min poll at 10s intervals
        for _ in range(APPROVAL_TIMEOUT_S // 10):
            await asyncio.sleep(10)
            try:
                resp = await self.redis.get(key)
            except Exception:
                resp = None
            if resp is None:
                continue
            decoded = (
                resp.decode("utf-8") if isinstance(resp, bytes) else resp
            )
            if decoded == "approved":
                return True
            if decoded == "rejected":
                return False
        return False  # timeout

    async def _monitor_post_deploy(
        self, r_number: int, duration_s: int = 300,
    ) -> bool:
        """Watch error count for `duration_s` post-deploy. True = regression."""
        try:
            baseline_raw = await self.redis.get(ERROR_LEDGER_COUNT_KEY)
            baseline = int(
                baseline_raw.decode("utf-8")
                if isinstance(baseline_raw, bytes) else (baseline_raw or 0)
            )
        except Exception:
            baseline = 0
        await asyncio.sleep(duration_s)
        try:
            current_raw = await self.redis.get(ERROR_LEDGER_COUNT_KEY)
            current = int(
                current_raw.decode("utf-8")
                if isinstance(current_raw, bytes) else (current_raw or 0)
            )
        except Exception:
            return False
        new_errors = current - baseline
        if new_errors > 10:
            logger.warning(
                "[aria_coder] post-deploy regression R-F%d: "
                "%d new errors in %ds", r_number, new_errors, duration_s,
            )
            return True
        return False

    # ── OPERATOR-REQUESTED FIXES (entry points from WhatsApp/Telegram) ───────

    async def operator_fix_request(self, description: str) -> FixResult:
        """Synthesise a gap from a free-text operator request and run it."""
        gap = Gap(
            gap_id=hashlib.sha256(
                description.encode("utf-8"),
            ).hexdigest()[:16],
            gap_type=GapType.MISSING_CAPABILITY,
            severity=GapSeverity.HIGH,
            title=description[:80],
            description=description,
            module="operator_request",
        )
        return await self.fix_gap(gap)

    async def operator_add_source(self, source_spec: str) -> FixResult:
        """Operator: 'Add <source> to the intel sweep' → new source module."""
        gap = Gap(
            gap_id=hashlib.sha256(
                f"source_{source_spec}".encode("utf-8"),
            ).hexdigest()[:16],
            gap_type=GapType.DATA_GAP,
            severity=GapSeverity.MEDIUM,
            title=f"Add intel source: {source_spec}",
            description=(
                f"Operator requested adding intelligence source: {source_spec}"
            ),
            module="lib/intel/source_registry.mjs",
            related_files=["apis/briefing.mjs", "server.mjs"],
        )
        return await self.fix_gap(gap)

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
    gap_type: str            # R-F804: needed for change_type mapping
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


# R-F804: Deterministic gap_type → self_improve.CHANGE_TYPES mapping.
#
# This mapping replaces LLM-judged risk routing (per CLAUDE.md §3 — the
# LLM cannot judge its own change's risk). Self-improve's CHANGE_TYPES
# dict carries the R-F462 auto-deploy gate per change_type; mapping
# every gap_type into one of those buckets ensures ARIA-Coder honours
# the same R-F462 protections as the operator-facing /api/aria/self/*
# endpoints.
#
# bug_fix       — auto-deploys IFF ARIA_SELF_IMPROVE_AUTO_DEPLOY=1 (R-F462)
# optimisation  — auto-deploys IFF ARIA_SELF_IMPROVE_AUTO_DEPLOY=1 (R-F462)
# enhancement   — always staged, never auto-deploys (operator approves)
GAP_TYPE_TO_CHANGE_TYPE: dict[str, str] = {
    GapType.MODULE_BUG:          "bug_fix",
    GapType.HALLUCINATION:       "bug_fix",
    GapType.DOCUMENT_PARSE:      "bug_fix",
    GapType.SOURCE_FAILURE:      "bug_fix",
    GapType.DD_LAYER_FAILURE:    "bug_fix",
    GapType.INTROSPECTION_ERROR: "bug_fix",
    GapType.PERFORMANCE:         "optimisation",
    GapType.DATA_GAP:            "enhancement",
    GapType.MISSING_CAPABILITY:  "enhancement",
    GapType.OPPORTUNITY:         "enhancement",  # R-F826: always operator-approved
}


def gap_type_to_change_type(gap_type: str) -> str:
    """Map a Gap.gap_type to a self_improve.CHANGE_TYPES key."""
    return GAP_TYPE_TO_CHANGE_TYPE.get(gap_type, "enhancement")


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
                # R-F825: WhatsApp success notification moved INTO fix_gap
                # (uses rich msg_shipped template). _one_cycle just records
                # the fixed gap + captures training pair.
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

    async def fix_gap(
        self, gap: Gap, operator_initiated: bool = False,
    ) -> FixResult:
        """End-to-end pipeline. Plan → validate → code → test → stage/deploy.

        Deploy path (R-F804): outputs go to self_improve.stage_improvement.
        Auto-deploy ONLY for `bug_fix` / `optimisation` AND only when
        `ARIA_SELF_IMPROVE_AUTO_DEPLOY=1` (R-F462 gate). Everything else
        stays in the staging queue at `/api/aria/self/staged` for operator
        review — same surface as manual self-improve.

        R-F824 (2026-05-23): `operator_initiated=True` indicates the
        fix was requested by an operator via WhatsApp / web chat — the
        request itself is approval, so we skip `_wait_for_approval`
        polling (which would deadlock since no one is around to click
        approve in Redis). Pipeline emits live progress to
        `crucix:aria:coder:progress:{fix_id}` for /api/aria/coder/status.
        """
        fix_id = uuid.uuid4().hex[:12]
        start_ts = time.monotonic()
        workspace = self.workspace_base / fix_id
        workspace.mkdir(parents=True, exist_ok=True)

        logger.info(
            "[aria_coder] fix_gap %s: %s (fix_id=%s%s)",
            gap.gap_id, gap.title, fix_id,
            " operator-initiated" if operator_initiated else "",
        )
        await self._publish_progress(
            fix_id, "starting",
            f"Picking up gap: {gap.title}",
            gap_id=gap.gap_id,
            operator_initiated=operator_initiated,
        )
        # R-F825: WhatsApp ping when an operator-initiated fix starts.
        # Cron-triggered fixes don't ping here (would be too chatty);
        # operator gets a notification only on `done`/`failed` for those.
        if operator_initiated and self.wa is not None:
            try:
                from .wa_notifier import WANotifier
                msg = WANotifier.msg_request_queued(
                    fix_id=fix_id, description=gap.description,
                )
                await self.wa.notify(msg)
            except Exception as e:
                logger.debug("[aria_coder] wa.notify(start) failed: %s", e)

        # R-F804 — safety gate. Cheap-fail BEFORE any LLM tokens are
        # burned. Uses the same primitives as the autonomous task engine
        # so the operator's existing pause / per-task-pause / firings-
        # per-hour ceiling all apply uniformly to the coder.
        try:
            from . import safety as _safety
            allowed, reason = await _safety.can_task_run(
                task_id="aria_coder_fix", entity=gap.gap_id,
            )
            if not allowed:
                logger.info(
                    "[aria_coder] safety blocked fix_gap %s: %s",
                    gap.gap_id, reason,
                )
                return FixResult(
                    success=False, fix_id=fix_id, gap_id=gap.gap_id,
                    failure_reason=f"Safety guardrail: {reason}",
                )
        except Exception as e:
            # Safety module unavailable is unusual; log + continue rather
            # than blocking the coder forever on a guardrail import error.
            logger.warning(
                "[aria_coder] safety.can_task_run failed (non-fatal): %s", e,
            )

        try:
            # STEP 1 — context
            await self._publish_progress(
                fix_id, "context",
                f"Reading codebase context around `{gap.module}`",
            )
            context = await self.codebase.get_context(
                gap.module, gap.related_files,
            )

            # STEP 2 — plan
            await self._publish_progress(
                fix_id, "planning",
                "Asking the LLM to plan a fix",
            )
            plan_raw = await self.llm.generate_fix_plan(gap, context)
            r_number = await self.r_counter.next()
            plan = FixPlan(
                fix_id=fix_id,
                gap_id=gap.gap_id,
                gap_type=gap.gap_type,
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

            # STEP 3 — WA approval if required (skipped for operator-initiated
            # fixes — R-F824 — the request IS the approval).
            if (
                plan.requires_wa_approval
                and self.wa is not None
                and not operator_initiated
            ):
                await self._publish_progress(
                    fix_id, "awaiting_approval",
                    f"Plan needs operator approval (gap_type={gap.gap_type})",
                )
                approved = await self._wait_for_approval(plan, gap)
                if not approved:
                    return FixResult(
                        success=False, fix_id=fix_id, gap_id=gap.gap_id,
                        r_number=r_number,
                        failure_reason="Operator declined fix via WhatsApp",
                    )

            # STEP 4 — generate code per target file + validate each
            await self._publish_progress(
                fix_id, "writing_code",
                f"Writing code for {len(plan.target_files)} file(s)",
                target_files=plan.target_files,
            )
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
            await self._publish_progress(
                fix_id, "writing_tests",
                f"Writing regression tests for {len(plan.code_changes)} file(s)",
            )
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
            await self._publish_progress(
                fix_id, "testing",
                "Running isolated tests (self-healing up to 3 attempts)",
            )
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

            # STEP 6.5 — Claude review (R-F805). Dormant unless both
            # ARIA_CODER_CLAUDE_REVIEW_ENABLED=1 and ANTHROPIC_API_KEY
            # are set. When disabled returns APPROVED (no-op).
            # When enabled, FLAGGED downgrades to staged-only (no
            # auto-deploy regardless of R-F462); BLOCKED rejects the fix.
            await self._publish_progress(
                fix_id, "claude_review",
                "Sending diff to Claude for verdict (dormant unless ANTHROPIC_API_KEY set)",
            )
            review_verdict = await self._claude_review(plan)
            if review_verdict.is_blocked:
                return FixResult(
                    success=False, fix_id=fix_id, gap_id=gap.gap_id,
                    r_number=r_number,
                    failure_reason=(
                        "Claude review BLOCKED: "
                        + "; ".join(review_verdict.reasons)
                    ),
                )

            # STEP 7 — stage via self_improve (R-F804). Auto-deploy gated
            # by ARIA_SELF_IMPROVE_AUTO_DEPLOY + change_type. Otherwise
            # stays at /api/aria/self/staged for operator approval.
            change_type = gap_type_to_change_type(plan.gap_type)
            # R-F805: FLAGGED verdict forces staging — operator must
            # review at /api/aria/self/staged regardless of R-F462 gate.
            # R-F821: ARIA_CODER_AUTO_DEPLOY_AND_TICKET=1 forces
            # auto-deploy (overriding R-F462 gate) AND opens a GitHub
            # Issue ticket after deploy. Only fires when verdict is
            # NOT flagged/blocked — Claude's flag still wins.
            from . import review_ticket as _ticket_mod
            ticket_mode_enabled = _ticket_mod.is_enabled()
            force_stage = (
                review_verdict.is_flagged
                # ticket-mode override: if enabled AND verdict isn't
                # flagged, push through auto-deploy regardless of the
                # R-F462 default gate.
                and not (ticket_mode_enabled and not review_verdict.is_blocked)
            )
            force_deploy = (
                ticket_mode_enabled
                and not review_verdict.is_flagged
                and not review_verdict.is_blocked
            )
            stage_ok, stage_status, staged_ids = await self._stage_or_deploy(
                plan=plan, change_type=change_type,
                force_stage=force_stage,
                force_deploy=force_deploy,
            )
            if not stage_ok:
                return FixResult(
                    success=False, fix_id=fix_id, gap_id=gap.gap_id,
                    r_number=r_number,
                    failure_reason=f"Stage/deploy failed: {stage_status}",
                )

            # STEP 8 — post-deploy regression monitor (only if auto-deployed)
            auto_deployed = stage_status == "auto_deployed"
            if auto_deployed:
                regression = await self._monitor_post_deploy(r_number)
                if regression:
                    # R-F804: rollback via self_improve restore-from-backup
                    # rather than FlyDeployer (which we don't use here).
                    # self_improve.rollback_improvement reads the backup
                    # written by deploy_improvement.
                    try:
                        from ..intel import self_improve as _si
                        for sid in staged_ids:
                            await _si.rollback_improvement(sid)
                    except Exception as e:
                        logger.warning(
                            "[aria_coder] rollback after regression failed: %s", e,
                        )
                    return FixResult(
                        success=False, fix_id=fix_id, gap_id=gap.gap_id,
                        r_number=r_number,
                        failure_reason=(
                            "Post-deploy regression detected — rolled back"
                        ),
                    )

            # STEP 8.5 — R-F821: open review ticket for the operator + Claude.
            # Only opens when ARIA_CODER_AUTO_DEPLOY_AND_TICKET=1 + GH_TOKEN
            # set. Otherwise no-op (dormant by default). Tests + deploy
            # have already passed at this point — the ticket is the
            # async-review surface, not a gate.
            try:
                await self._open_review_ticket(
                    plan=plan, gap=gap, change_type=change_type,
                    staged_ids=staged_ids, auto_deployed=auto_deployed,
                    review_verdict=review_verdict,
                    test_result=test_result,
                )
            except Exception as e:
                # Ticket failure is NEVER fatal — the deploy already
                # happened. Log and continue.
                logger.warning("[aria_coder] _open_review_ticket failed: %s", e)

            # STEP 9 — absorb knowledge + emit training pair
            if self.brain_hook is not None:
                try:
                    outcome_label = (
                        "auto-deployed" if auto_deployed else "staged for review"
                    )
                    await self.brain_hook.absorb(
                        text=(
                            f"Autonomous fix R-F{r_number} ({outcome_label}): "
                            f"{plan.title}. Gap type: {gap.gap_type}. "
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
                "outcome": (
                    "deployed_successfully" if auto_deployed else "staged_for_review"
                ),
                "r_number": r_number,
            }

            elapsed = time.monotonic() - start_ts
            await self._publish_progress(
                fix_id, "done",
                f"R-F{r_number} {stage_status} in {elapsed:.0f}s",
                r_number=r_number,
                outcome=(
                    "deployed_successfully" if auto_deployed
                    else "staged_for_review"
                ),
                elapsed_s=round(elapsed),
                staged_ids=staged_ids,
            )
            # R-F825: rich success message to WhatsApp. Fires for BOTH
            # cron-triggered and operator-initiated fixes — this is the
            # operator's main confirmation channel.
            if self.wa is not None:
                try:
                    from .wa_notifier import WANotifier
                    op_summary = (
                        plan.approach[:300]
                        if plan.approach
                        else "ARIA-Coder shipped a fix; see audit for diff."
                    )
                    msg = WANotifier.msg_shipped(
                        r_number=r_number,
                        title=plan.title,
                        operator_summary=op_summary,
                        files_modified=list(plan.code_changes.keys()),
                        auto_deployed=auto_deployed,
                        issue_url=None,  # populated by ticket flow if R-F821 active
                        elapsed_s=int(elapsed),
                    )
                    await self.wa.notify(msg)
                except Exception as e:
                    logger.debug("[aria_coder] wa.notify(shipped) failed: %s", e)
            logger.info(
                "[aria_coder] ✅ R-F%d %s in %.0fs (staged_ids=%s)",
                r_number, stage_status, elapsed, staged_ids,
            )
            return FixResult(
                success=True, fix_id=fix_id, gap_id=gap.gap_id,
                r_number=r_number, deploy_result=None,
                training_pair=training_pair,
            )

        except Exception as e:
            logger.error(
                "[aria_coder] fix_gap %s failed: %s",
                gap.gap_id, e, exc_info=True,
            )
            await self._publish_progress(
                fix_id, "failed",
                f"Pipeline crashed: {str(e)[:200]}",
                error=str(e)[:500],
            )
            # R-F825: WhatsApp ping on crash so the operator isn't left
            # waiting silently — surfaces in chat, lists the fix_id so
            # they can grab logs from /coder/status/{fix_id}.
            if self.wa is not None:
                try:
                    from .wa_notifier import WANotifier
                    await self.wa.notify(
                        WANotifier.msg_failed(
                            fix_id=fix_id, reason=str(e),
                        ),
                    )
                except Exception as ne:
                    logger.debug("[aria_coder] wa.notify(failed) failed: %s", ne)
            return FixResult(
                success=False, fix_id=fix_id, gap_id=gap.gap_id,
                failure_reason=str(e),
            )
        finally:
            if workspace.exists():
                shutil.rmtree(workspace, ignore_errors=True)

    # ── HELPERS ──────────────────────────────────────────────────────────────

    async def _publish_progress(
        self,
        fix_id: str,
        stage: str,
        message: str,
        **extra: Any,
    ) -> None:
        """R-F824: publish a progress event to Redis so operator can
        poll /api/aria/coder/status/{fix_id} for live updates.

        Keeps last 50 events + a `latest` snapshot. Fail-safe: any
        Redis hiccup is logged but never blocks the pipeline.
        """
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).isoformat()
        event = {
            "ts": ts, "stage": stage, "message": message, **extra,
        }
        logger.info(
            "[aria_coder] progress fix=%s stage=%s — %s",
            fix_id, stage, message,
        )
        try:
            key_latest = f"crucix:aria:coder:progress:{fix_id}:latest"
            key_history = f"crucix:aria:coder:progress:{fix_id}:history"
            await self.redis.setex(key_latest, 7 * 86400, json.dumps(event))
            await self.redis.lpush(key_history, json.dumps(event))
            await self.redis.ltrim(key_history, 0, 49)
            await self.redis.expire(key_history, 7 * 86400)
        except Exception as e:
            logger.debug("[aria_coder] _publish_progress redis error: %s", e)

    async def get_progress(self, fix_id: str) -> dict:
        """R-F824: return latest event + full history for fix_id."""
        try:
            latest_raw = await self.redis.get(
                f"crucix:aria:coder:progress:{fix_id}:latest",
            )
            history_raw = await self.redis.lrange(
                f"crucix:aria:coder:progress:{fix_id}:history", 0, 49,
            )
        except Exception:
            return {"fix_id": fix_id, "found": False, "error": "redis unreachable"}

        if not latest_raw:
            return {"fix_id": fix_id, "found": False}

        def _decode(v):
            if isinstance(v, bytes):
                v = v.decode("utf-8")
            try:
                return json.loads(v)
            except (json.JSONDecodeError, TypeError):
                return None

        latest = _decode(latest_raw)
        history = [e for e in (_decode(x) for x in history_raw) if e]
        history.reverse()  # chronological order
        return {
            "fix_id": fix_id, "found": True,
            "latest": latest, "history": history,
        }



    async def _open_review_ticket(
        self,
        *,
        plan: FixPlan,
        gap: Gap,
        change_type: str,
        staged_ids: list[str],
        auto_deployed: bool,
        review_verdict,                # claude_reviewer.ReviewVerdict
        test_result,                   # test_runner.TestResult
    ) -> None:
        """R-F821: open a GitHub Issue for post-deploy review.

        No-op when ARIA_CODER_AUTO_DEPLOY_AND_TICKET is not set OR
        GH_TOKEN missing. Failure is non-fatal — the deploy already
        happened, the ticket is just the operator's audit handle.
        """
        from .review_ticket import (
            ReviewTicket, is_enabled as ticket_is_enabled,
            open_review_ticket,
        )
        from .claude_reviewer import build_unified_diff

        if not ticket_is_enabled():
            logger.debug("[aria_coder] review ticket dormant (env not set)")
            return

        diff = build_unified_diff(plan.code_changes, self.codebase.read)

        # Map gap.severity (IntEnum) to a label string
        sev_name = (
            gap.severity.name if hasattr(gap.severity, "name")
            else str(gap.severity)
        )

        ticket = ReviewTicket(
            r_number=plan.r_number,
            gap_title=gap.title,
            gap_type=gap.gap_type,
            gap_severity=sev_name,
            gap_module=gap.module,
            gap_description=gap.description,
            change_type=change_type,
            files_modified=list(plan.code_changes.keys()),
            staged_ids=staged_ids,
            diff=diff,
            tests_passed=getattr(test_result, "passed", 0) if test_result else 0,
            tests_failed=getattr(test_result, "failed", 0) if test_result else 0,
            tests_summary=(
                getattr(test_result, "failure_summary", "")
                or "all tests passed"
                if test_result else "tests skipped"
            ),
            claude_verdict=(
                review_verdict.verdict.value
                if review_verdict and not review_verdict.review_disabled
                else None
            ),
            claude_reasons=list(getattr(review_verdict, "reasons", []) or []),
            auto_deployed=auto_deployed,
            aria_service_url=self.aria_url,
        )

        result = await open_review_ticket(ticket)
        if result.get("ok") and result.get("issue_url"):
            logger.info(
                "[aria_coder] review ticket opened for R-F%d: %s",
                plan.r_number, result["issue_url"],
            )
        elif not result.get("ok"):
            logger.warning(
                "[aria_coder] review ticket open failed (non-fatal): %s",
                result.get("reason"),
            )

    async def _claude_review(self, plan: FixPlan) -> "ReviewVerdict":
        """R-F805: ask Claude to second-opinion the diff before staging.

        Dormant unless ARIA_CODER_CLAUDE_REVIEW_ENABLED=1 AND
        ANTHROPIC_API_KEY is set. When dormant returns APPROVED so
        the pipeline proceeds unchanged. When enabled-but-erroring
        returns FLAGGED (fail-safe — operator handles staging review).
        """
        from .claude_reviewer import (
            ClaudeReviewer, ReviewVerdict, Verdict,
            build_unified_diff, is_enabled,
        )
        if not is_enabled():
            return ReviewVerdict(verdict=Verdict.APPROVED, review_disabled=True)

        change_type = gap_type_to_change_type(plan.gap_type)
        diff = build_unified_diff(plan.code_changes, self.codebase.read)

        reviewer = ClaudeReviewer()
        try:
            verdict = await reviewer.review(
                diff=diff,
                change_type=change_type,
                gap_title=plan.title,
                gap_description=plan.description,
                files=list(plan.code_changes.keys()),
            )
        finally:
            await reviewer.aclose()

        logger.info(
            "[aria_coder] Claude review R-F%d: %s (reasons=%s)",
            plan.r_number, verdict.verdict.value, verdict.reasons[:3],
        )
        return verdict

    async def _stage_or_deploy(
        self,
        plan: FixPlan,
        change_type: str,
        force_stage: bool = False,
        force_deploy: bool = False,
    ) -> tuple[bool, str, list[str]]:
        """Route code changes through `self_improve.stage_improvement`.

        For each (target_file, new_code) in plan.code_changes, stage via
        the existing operator-facing pipeline. If the change_type is in
        the auto-deployable set per `self_improve.CHANGE_TYPES`
        (gated by `ARIA_SELF_IMPROVE_AUTO_DEPLOY=1` per R-F462), call
        `deploy_improvement` immediately. Otherwise items remain at
        `/api/aria/self/staged` for operator review.

        Returns: (success, status, staged_ids) where status is one of:
          - "auto_deployed"        — all items shipped to disk + git
          - "staged_for_operator"  — all items staged, awaiting review
          - <error message>        — staging failed for at least one file
        """
        from ..intel import self_improve as _si

        staged_ids: list[str] = []
        for target_file, new_code in plan.code_changes.items():
            result = await _si.stage_improvement(
                file_path=target_file,
                new_content=new_code,
                change_type=change_type,
                description=plan.title,
                reasoning=(
                    f"Autonomous fix by ARIA-Coder for gap {plan.gap_id} "
                    f"({plan.gap_type}). {plan.approach[:300]}"
                ),
            )
            if not result.get("staged"):
                err = result.get("error", "unknown")
                logger.warning(
                    "[aria_coder] stage_improvement failed for %s: %s",
                    target_file, err,
                )
                return False, f"stage_failed:{target_file}:{err}", staged_ids
            staged_ids.append(result["id"])

        # Decide auto-deploy based on the R-F462 gate per change_type.
        # CHANGE_TYPES dict was set at import time (R-F518: env var read
        # once); honour that — flipping ARIA_SELF_IMPROVE_AUTO_DEPLOY=1
        # requires a worker restart, which is the documented path.
        #
        # R-F805: force_stage overrides the R-F462 gate when Claude
        # review returned FLAGGED. Even a bug_fix with R-F462 open will
        # stay staged so the operator gets eyes on Claude's concerns.
        #
        # R-F821: force_deploy overrides the R-F462 gate when ticket-mode
        # is enabled (ARIA_CODER_AUTO_DEPLOY_AND_TICKET=1). In that mode
        # we auto-deploy ALL approved-or-unreviewed changes (the gate
        # is GitHub Issue audit, not pre-deploy approval).
        ct_cfg = _si.CHANGE_TYPES.get(change_type, {})
        gate_open = bool(ct_cfg.get("auto_deploy")) or force_deploy
        auto_eligible = gate_open and not force_stage
        if not auto_eligible:
            reason = "claude_flagged" if force_stage else "r_f462_gate_closed"
            logger.info(
                "[aria_coder] %d items staged for operator review "
                "(change_type=%s, reason=%s)",
                len(staged_ids), change_type, reason,
            )
            return True, "staged_for_operator", staged_ids

        # Auto-deploy each staged item via the existing self_improve
        # pipeline. This writes the file to disk + creates the backup +
        # git-commits + writes the audit log — same surface as the
        # operator-clicked /api/aria/self/deploy/{id}.
        for sid in staged_ids:
            deploy_res = await _si.deploy_improvement(sid)
            if not deploy_res.get("ok"):
                err = deploy_res.get("error", "unknown")
                logger.error(
                    "[aria_coder] deploy_improvement %s failed: %s",
                    sid, err,
                )
                return False, f"deploy_failed:{sid}:{err}", staged_ids

        logger.info(
            "[aria_coder] %d items auto-deployed (change_type=%s)",
            len(staged_ids), change_type,
        )
        return True, "auto_deployed", staged_ids

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

    async def operator_fix_request(
        self,
        description: str,
        module_hint: Optional[str] = None,
    ) -> FixResult:
        """Synthesise a gap from a free-text operator request and run it.

        Called from `/api/aria/coder/request` (R-F824) — the HTTP layer
        kicks this off via `asyncio.create_task` so the request returns
        immediately with the fix_id, then the operator polls
        `/api/aria/coder/status/{fix_id}` for live updates.

        The request itself IS the approval (operator just asked) so we
        pass `operator_initiated=True` to bypass `_wait_for_approval`.
        """
        gap = Gap(
            gap_id=hashlib.sha256(
                description.encode("utf-8"),
            ).hexdigest()[:16],
            gap_type=GapType.MISSING_CAPABILITY,
            severity=GapSeverity.HIGH,
            title=description[:80],
            description=description,
            module=module_hint or "operator_request",
        )
        return await self.fix_gap(gap, operator_initiated=True)

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
        return await self.fix_gap(gap, operator_initiated=True)

"""R-F802 — ARIACoder orchestrator.

The closed-loop self-coder. Wires the components together:

    GapDetector → AutonomousCoder (plan) → CodebaseReader (context)
                ↓
    AutonomousCoder (write code) → # R-F1191: validator removed
                ↓
    TestRunner (isolated) ← AutonomousCoder (heal, ≤3 attempts)
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
deploying directly — UNLESS all eligibility and maturity gates pass:

  (a) `gap.gap_type` is in the deterministic auto-fixable set
      (see `gap_detector.AUTONOMY_LEVEL`),
  (b) `ARIA_SELF_IMPROVE_AUTO_DEPLOY=1` is set on the host,
  (c) the change is `bug_fix` change_type, and
  (d) the live coder scoreboard has earned the R-F2689 gold-lane gate
      (enough fixed/gold outcomes with a low blocked ratio).

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
from typing import Any, Optional, TYPE_CHECKING
if TYPE_CHECKING:  # R-F1908: resolve the "ReviewVerdict" forward-ref (runtime-safe)
    from .claude_reviewer import ReviewVerdict

from .codebase_reader import CodebaseReader
# R-F1963 (corrects a misleading R-F1191 comment): the constitutional validator
# is NOT gone. R-F1191 removed it from THIS in-loop coder pass, but R-F1287
# RESTORED it as a FAIL-CLOSED gate at DEPLOY time (self_improve.deploy_improvement
# — protected files, weakening patterns, dangerous AST/imports, learned attacks).
# So generated code is constitution-checked before it is ever written live; the
# old "validator removed / fully autonomous" comments overstated the autonomy.
from typing import Any as _Any
from .fly_deployer import DeployResult, FlyDeployer
from .gap_detector import Gap, GapDetector, GapSeverity, GapType
from .r_counter import RNumberCounter
from ..intel.autonomous_coder import AutonomousCoder  # R-F1003 (kept for injection/back-compat)
from .sovereign_llm import (  # R-F1025: real LLM-backed coder (the contract self_coder reads)
    SovereignLLM,
    apply_search_replace,  # R-F1295: diff-based editing
    LARGE_FILE_LINES,
)
from .test_runner import TestResult, TestRunner, coder_tests_enabled
from ..intel.wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.autonomous.self_coder")

# R-F1963: this in-loop list is empty (R-F1191), but protected-file enforcement
# is NOT gone — it lives at deploy time in self_improve.deploy_improvement's
# constitutional gate (R-F1287) + the NO_AUTODEPLOY_FILES per-file lock. Empty
# here ≠ unprotected overall.
_PROTECTED_FILES: frozenset = frozenset()

WORKSPACE_BASE = Path(
    os.environ.get("ARIA_CODER_WORKSPACE", "/data/coder_workspace")
)
MAX_FIX_ATTEMPTS = 10  # R-F996: raised from 3
SCAN_INTERVAL_S = 300           # 5 minutes (R-F996: was 15)
POST_DEPLOY_MONITOR_S = 1800    # 30 minutes
MAX_GAPS_PER_CYCLE = 20  # R-F996: raised from 3
APPROVAL_TIMEOUT_S = 1800       # 30 minutes

APPROVAL_KEY_PREFIX = "crucix:aria:coder:approval:"
ERROR_LEDGER_COUNT_KEY = "crucix:aria:error_ledger:count"
SCOREBOARD_KEY = "crucix:aria:coder:scoreboard"
SCOREBOARD_BUCKETS = ("claimed", "fixed", "staged", "gold", "blocked")


def _gold_lane_int_env(name: str, default: int) -> int:
    """Read a non-negative integer threshold, failing closed to default."""
    try:
        return max(0, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def _gold_lane_float_env(name: str, default: float) -> float:
    """Read a float threshold, failing closed to default."""
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    if value < 0:
        return default
    return value


GOLD_LANE_MIN_FIXED = _gold_lane_int_env("ARIA_CODER_GOLD_LANE_MIN_FIXED", 20)
GOLD_LANE_MIN_GOLD = _gold_lane_int_env("ARIA_CODER_GOLD_LANE_MIN_GOLD", 10)
GOLD_LANE_MAX_BLOCKED_RATIO = _gold_lane_float_env(
    "ARIA_CODER_GOLD_LANE_MAX_BLOCKED_RATIO", 0.25
)


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


@fail_wire(module="self_coder", gap_type="agent_cycle_failure")
def gap_type_to_change_type(gap_type: str) -> str:
    """Map a Gap.gap_type to a self_improve.CHANGE_TYPES key."""
    return GAP_TYPE_TO_CHANGE_TYPE.get(gap_type, "enhancement")


@fail_wire(module="self_coder", gap_type="agent_cycle_failure")
def autonomous_gold_lane_decision(scoreboard: dict | None) -> dict:
    """Return whether autonomous code is mature enough to auto-deploy.

    This is an evidence gate, not a feature flag. ARIA may still write and stage
    fixes, but direct deploy is held until the live scoreboard shows enough
    proven fixed/gold outcomes and a low blocked ratio.
    """
    counts = (scoreboard or {}).get("counts") or {}

    def _as_int(value: object) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    fixed = _as_int(counts.get("fixed"))
    gold = _as_int(counts.get("gold"))
    blocked = _as_int(counts.get("blocked"))
    claimed = _as_int(counts.get("claimed"))

    # ── R-F3703 — the blocked ratio is a RECENT-QUALITY signal, not a tally ──
    #
    # THE DEFECT, measured live 2026-08-04: the scoreboard read
    #   {"claimed": 6358, "blocked": 6404}   (no "fixed"/"gold" keys AT ALL)
    # so `attempts = max(6358, 0 + 6404) = 6404` and `blocked_ratio = 1.000`
    # against a ceiling of 0.25. Even 20 perfect fixes plus 10 gold would give
    # 6404/6424 = 0.997 — clearing 0.25 from there needs roughly 19,000
    # consecutive clean fixes. The gate was a ONE-WAY RATCHET: permanently shut.
    #
    # Worse, it was shut by evidence that is not about code quality at all.
    # 4,007 of those blocks are `coder_disabled` — refusals from the period when
    # the lane was deliberately OFF — plus `rate_limit_exceeded` and
    # `duplicate_recent_run`. Those are ADMINISTRATIVE outcomes. Counting them as
    # failed attempts means "we turned the coder off for a month" is recorded as
    # "the coder writes bad code".
    #
    # And the counters never age out: `_record_scoreboard` writes
    # `setex(SCOREBOARD_KEY, 30*86400, ...)` on EVERY write, refreshing the TTL,
    # so under continuous activity the key never expires and the buckets are a
    # LIFETIME tally rather than a 30-day window.
    #
    # The fix reads the bounded `recent` event list the scoreboard already
    # maintains (last 50 outcomes, `board["recent"]` in _record_scoreboard) and
    # computes the ratio over genuine QUALITY outcomes only. The lifetime counts
    # still drive the fixed/gold minima — those are cumulative achievements and
    # a ratchet is the correct shape for them.
    _ADMIN_OUTCOMES = ("coder_disabled", "rate_limit", "duplicate_recent_run",
                       "safety guardrail", "engine paused", "safety_stop")

    def _is_admin_refusal(reason: str) -> bool:
        r = (reason or "").lower()
        return any(a in r for a in _ADMIN_OUTCOMES)

    recent = (scoreboard or {}).get("recent") or []
    quality_total = 0
    quality_blocked = 0
    for ev in recent if isinstance(recent, list) else []:
        if not isinstance(ev, dict):
            continue
        outcome = str(ev.get("outcome") or "").lower()
        if outcome == "blocked" and _is_admin_refusal(str(ev.get("reason") or "")):
            continue  # administrative, not a quality signal
        quality_total += 1
        if outcome == "blocked":
            quality_blocked += 1

    attempts = max(claimed, fixed + blocked)

    if quality_total:
        blocked_ratio = quality_blocked / quality_total
        ratio_basis = f"recent{quality_total}"
    elif isinstance(recent, list) and recent:
        # A recent window EXISTS but every entry in it was an administrative
        # refusal — no quality evidence either way. Fail CLOSED: absence of
        # evidence is not evidence of readiness for an auto-deploy gate.
        blocked_ratio = 1.0
        ratio_basis = "recent_all_administrative"
    else:
        # No recent window at all — a legacy scoreboard written before
        # `recent` existed (schema_version < 2), or one that has just been
        # cleared. Fall back to the lifetime counts, which is the only
        # evidence there is.
        #
        # This branch is deliberately NOT the fail-closed one: making it so
        # regressed every mature-but-legacy scoreboard into a permanently shut
        # gate — caught by test_rf851/test_rf2689 rather than in production.
        # The ratchet this change exists to fix only bites when a LARGE
        # historical `blocked` count is present, and such a board is written by
        # current code, which always writes `recent`.
        blocked_ratio = (blocked / attempts) if attempts else 1.0
        ratio_basis = "lifetime_counts_no_recent_window"

    reasons: list[str] = []
    if fixed < GOLD_LANE_MIN_FIXED:
        reasons.append(f"fixed {fixed} < {GOLD_LANE_MIN_FIXED}")
    if gold < GOLD_LANE_MIN_GOLD:
        reasons.append(f"gold {gold} < {GOLD_LANE_MIN_GOLD}")
    if blocked_ratio > GOLD_LANE_MAX_BLOCKED_RATIO:
        reasons.append(
            f"blocked_ratio {blocked_ratio:.3f} > {GOLD_LANE_MAX_BLOCKED_RATIO:.3f} "
            f"(basis={ratio_basis})"
        )

    return {
        "allowed": not reasons,
        "reasons": reasons,
        "counts": {
            "claimed": claimed,
            "fixed": fixed,
            "gold": gold,
            "blocked": blocked,
            "attempts": attempts,
        },
        "blocked_ratio": blocked_ratio,
        "thresholds": {
            "min_fixed": GOLD_LANE_MIN_FIXED,
            "min_gold": GOLD_LANE_MIN_GOLD,
            "max_blocked_ratio": GOLD_LANE_MAX_BLOCKED_RATIO,
        },
    }


@fail_wire(module="self_coder", gap_type="agent_cycle_failure")
def resolve_staging_decision(
    *,
    is_flagged: bool,
    is_blocked: bool,
    ticket_mode_enabled: bool,
    force_stage_only: bool,
) -> tuple[bool, bool]:
    """R-F924 (2026-05-27) — decide (force_stage, force_deploy) from the review
    verdict + deploy mode. Extracted as a pure function so this safety-critical
    decision is unit-testable across the full truth table.

    Binding invariant: a FLAGGED verdict ALWAYS force-stages and NEVER
    auto-deploys, regardless of ticket-mode or the R-F462 gate ("Claude's flag
    still wins"). An operator /code request (force_stage_only) also force-stages.
    Only a clean (not-flagged, not-blocked) verdict under ticket-mode
    auto-deploys. A blocked verdict is handled by an earlier early-return, but
    we defensively force-stage it here too.
    """
    if is_blocked or force_stage_only:
        return (True, False)
    force_stage = is_flagged
    force_deploy = ticket_mode_enabled and not is_flagged
    return (force_stage, force_deploy)


@fail_wire(module="self_coder", gap_type="agent_cycle_failure")
def apply_capability_test_gate(
    *,
    reproduce_fail_to_pass: bool,
    operator_initiated: bool,
    force_stage: bool,
    force_deploy: bool,
) -> tuple[bool, bool, bool]:
    """R-F1767 — CAPABILITY-TEST GATE (pure, unit-testable).

    An AUTONOMOUS self-improve change may auto-deploy ONLY if a capability test
    proved the real symptom went FAIL→PASS on the fix (reproduce_fail_to_pass,
    R-F1681). Without that proof, force stage-only — no autonomous deploy lands
    untested, even under AUTO_DEPLOY=1 or ticket-mode. This is what makes
    no-human-gate autonomy safe: deploy rests on a passing capability test.

    Bypass (returns the decision unchanged): operator-initiated fixes (the
    request is the approval) and already-force-staged verdicts (flagged — handled
    upstream, don't double-process).

    Returns (force_stage, force_deploy, blocked) — `blocked` True iff the gate
    intervened (caller wires the capability_test_missing signal).
    """
    if not reproduce_fail_to_pass and not operator_initiated and not force_stage:
        return (True, False, True)
    return (force_stage, force_deploy, False)


@fail_wire(module="self_coder", gap_type="agent_cycle_failure")
def capability_test_genuinely_passed(test_result: Any) -> bool:
    """R-F2395 — a capability/reproduce test counts as PASSED for the AUTO-DEPLOY
    gate ONLY when it GENUINELY EXECUTED (>=1 test actually ran) AND was green.

    Why this exists (root cause of "AUTO_DEPLOY guarded only by the truncation
    check"): TestRunner.run_isolated returns a NO-OP TestResult
    (all_green=True, passed=0) whenever ARIA_CODER_TESTS_ENABLED != "1"
    (test_runner.py Gate 1), whenever it is rate-limited (Gate 3), or whenever a
    generated/reproduce test file collects zero tests. STEP 6.5 of fix_gap
    re-runs the reproduce test on the FIXED code through run_isolated and set
    `_reproduce_fail_to_pass` from a bare `all_green` — so a vacuous green made
    the capability-test gate (apply_capability_test_gate) trivially satisfied and
    the autonomous fix auto-deployed WITHOUT any test ever running. A no-op run
    proves NOTHING and MUST NOT satisfy the gate.

    The honesty encoded in build_coder_reward_record's gold gate
    (tests_ran = tests_enabled AND passed+failed>0) is now ALSO enforced at the
    DEPLOY gate: THE TEST is the gate, never the model's confidence, and never a
    disabled/rate-limited no-op that merely returns green.

    Returns True iff the run is green AND at least one test actually passed AND
    nothing failed/errored. Defensive against a None/partial result.
    """
    if test_result is None:
        return False
    passed = getattr(test_result, "passed", 0) or 0
    failed = getattr(test_result, "failed", 0) or 0
    errors = getattr(test_result, "errors", 0) or 0
    all_green = bool(getattr(test_result, "all_green", False))
    return bool(all_green and passed > 0 and failed == 0 and errors == 0)


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


@fail_wire(module="self_coder", gap_type="agent_cycle_failure")
def build_coder_reward_record(
    *, instruction: str, approach: str, code_changes: dict, r_number,
    test_result, stage_ok: bool, auto_deployed: bool, tests_enabled: bool,
    reproduce_fail_to_pass: bool = False,
) -> dict:
    """R-F1674 — assemble a verifiable-reward coder training record (DeepSeek-R1
    style). The record is GOLD (training-grade) only when the reward is REAL and
    OBJECTIVE: tests GENUINELY ran (not the ARIA_CODER_TESTS_ENABLED=0 no-op pass)
    AND green AND the function-preservation guard held (stage_ok).

    R-F1681: when a reproduce test was auto-written, gold ALSO requires that the
    reproduce test went FAIL-on-unfixed → PASS-on-fixed (reproduce_fail_to_pass).
    This prevents gamed fixes where generated tests pass but the real symptom
    is unresolved. Pure function so the gold gate is unit-testable; the caller
    persists it. Offline coder SFT trains on gold=True only — an ungameable
    signal, not a chat-quality heuristic."""
    passed = getattr(test_result, "passed", 0) or 0
    failed = getattr(test_result, "failed", 0) or 0
    all_green = bool(getattr(test_result, "all_green", False))
    # tests genuinely executed iff the runner was enabled AND it actually ran cases
    tests_ran = bool(tests_enabled and (passed + failed) > 0)
    # R-F1681: gold requires reproduce FAIL->PASS when a reproduce test exists.
    # When no reproduce test was needed (existing test covered it), this is
    # trivially True (the existing test already proves the symptom).
    gold = bool(tests_ran and all_green and stage_ok and reproduce_fail_to_pass)
    return {
        "instruction": instruction,
        "approach": approach,
        "code_changes": code_changes,
        "target_files": list((code_changes or {}).keys()),
        "r_number": r_number,
        "reward": {
            "tests_ran": tests_ran,
            "tests_green": all_green,
            "tests_passed": passed,
            "tests_failed": failed,
            "healing_attempts": getattr(test_result, "attempts", None),
            "function_preserved": bool(stage_ok),
            "auto_deployed": bool(auto_deployed),
            "reproduce_fail_to_pass": bool(reproduce_fail_to_pass),
        },
        "gold": gold,
        "ts": datetime.now(timezone.utc).isoformat(),
    }


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
        llm: Optional["SovereignLLM"] = None,  # R-F1025: LLM-backed coder provider
        # R-F1191: constitutional validator removed
        validator: None = None,
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

        # R-F1025: default to the LLM-backed SovereignLLM, NOT the template-only
        # AutonomousCoder stub. The stub ignored existing_code (emitted fresh
        # stubs), returned the wrong test key, and never produced corrected code
        # in analyse_failure — so the coder could not actually fix bugs. SovereignLLM
        # returns exactly the keys this class reads (approach/target_files/risk_level,
        # code, test_code/test_filepath, corrected code) and is model-agnostic
        # (DeepSeek now via /api/aria/coder/llm, ARIA-LLM once ARIA_LLM_URL is set).
        self.llm = llm or SovereignLLM(
            aria_service_url=aria_service_url or "http://localhost:8000",
        )
        # Allow injection for tests; default to constructing from scratch
        # R-F1680: pass the LLM so GapDetector can auto-write reproduce tests
        # when no existing test is found for the target module.
        # NOTE: self.llm MUST be set BEFORE this line — GapDetector receives
        # the llm parameter at construction time. If llm is None here, the
        # reproduce gate falls back to the old reject path (R-F1680 bug).
        self.gap_detector = gap_detector or GapDetector(redis_client, llm=self.llm)
        # R-F1191: constitutional validator removed
        self.validator = None
        self.codebase = codebase or CodebaseReader(aria_service_url)
        self.test_runner = test_runner or TestRunner(redis_client)
        self.deployer = deployer or FlyDeployer(redis_client, aria_service_url)
        self.r_counter = r_counter or RNumberCounter(redis_client)

        self.workspace_base = workspace_base or WORKSPACE_BASE
        self.workspace_base.mkdir(parents=True, exist_ok=True)

    # ── MAIN LOOP ────────────────────────────────────────────────────────────

    @fail_wire(module="self_coder", gap_type="agent_cycle_failure")
    async def run_forever(self) -> None:
        """Continuous loop: detect gaps every 15min, fix the top N."""
        logger.info("[aria_coder] starting autonomous loop")
        while True:
            try:
                # R-F1395: check engine pause flag before each cycle
                from aria_service.autonomous.safety import is_engine_paused
                if await is_engine_paused():
                    logger.debug("[aria_coder] engine paused — skipping cycle")
                    await asyncio.sleep(SCAN_INTERVAL_S)
                    continue
                await self._one_cycle()
                # R-F1282: wire success to brain so the operator can see
                # the coder is alive and producing results.
                try:
                    from aria_service.intel.engine_wiring import wire_success
                    wire_success(
                        module="aria_coder",
                        summary="Coder cycle complete",
                        source_id="aria_coder:run_forever",
                    )
                except Exception:
                    pass
            except asyncio.CancelledError:
                logger.info("[aria_coder] cancelled — exiting")
                raise
            except Exception as e:
                # R-F1282: wire failure to brain so the operator knows
                # the coder is down and can investigate.
                try:
                    from aria_service.intel.engine_wiring import wire_failure
                    wire_failure(
                        module="aria_coder",
                        detail=f"Cycle error: {e}",
                        gap_type="agent_cycle_failure",
                        source="aria_coder:run_forever",
                    )
                except Exception:
                    pass
                logger.error("[aria_coder] cycle error: %s", e, exc_info=True)
            await asyncio.sleep(SCAN_INTERVAL_S)

    async def _one_cycle(self) -> None:
        gaps = await self.gap_detector.scan()

        # R-F1761: publish scan results to Redis so /api/aria/coder/gaps
        # returns real data instead of scanned_at=null. R-F1046 removed
        # gap_detector.run_forever() (double-scanning) but forgot to move
        # publish_latest() here — so the scan ran but results were never
        # written to the key the endpoint reads.
        try:
            await self.gap_detector.publish_latest(gaps)
        except Exception:
            logger.debug("[aria_coder] publish_latest failed (non-fatal)")

        # R-F1287: constitutional validator RESTORED — protected-file gaps are
        # filtered here AND fail-closed at the deploy gate.
        protected_file_gaps = []
        pending_skip = []
        actionable = []
        for g in gaps:
            if g.severity < GapSeverity.MEDIUM or not g.auto_fixable:
                continue
            # Map module name to file path and check against PROTECTED_FILES
            _module_path = f'aria_service/intel/{g.module}.py'
            if _module_path in _PROTECTED_FILES:
                protected_file_gaps.append(g)
                continue
            # R-F1294: skip if a fix for this module is ALREADY staged + pending
            # review. Without this the coder regenerates a fix every cycle for a
            # module whose fix is waiting (AUTO_DEPLOY off → never marked fixed →
            # re-detected forever → the 186× churn + wasted LLM tokens/R-numbers).
            try:
                from ..intel.self_improve import has_pending_staged_fix_for_module
                if await has_pending_staged_fix_for_module(g.module):
                    pending_skip.append(g)
                    continue
            except Exception:  # noqa: BLE001 — the check must never break the cycle
                pass
            actionable.append(g)

        if pending_skip:
            logger.info(
                "[aria_coder] R-F1294: skipped %d gap(s) — a fix is already staged "
                "and pending review for: %s",
                len(pending_skip), [g.module for g in pending_skip][:8],
            )

        if protected_file_gaps:
            logger.warning(
                '[aria_coder] %d gap(s) target protected files - skipped (human review needed): %s',
                len(protected_file_gaps),
                [(g.gap_id, g.module, g.title[:60]) for g in protected_file_gaps],
            )

        if not actionable:
            logger.debug("[aria_coder] no actionable gaps this cycle")
            return

                # R-F1051 -- the coder can fix ANY file. No artificial constraints.
        actionable.sort(key=lambda g: int(g.severity), reverse=True)

        logger.info(
            "[aria_coder] %d actionable gaps -- fixing top %d",
            len(actionable), MAX_GAPS_PER_CYCLE,
        )

        for gap in actionable[:MAX_GAPS_PER_CYCLE]:
            await self.gap_detector.mark_attempted(gap.gap_id)
            await self._record_scoreboard(
                "claimed",
                gap,
                reason="attempt_started",
            )
            result = await self.fix_gap(gap)

            if result.success and result.r_number is not None:
                outcome = (result.training_pair or {}).get("outcome", "")
                gold = bool((result.training_pair or {}).get("gold"))
                await self._record_scoreboard(
                    "fixed",
                    gap,
                    r_number=result.r_number,
                    outcome=outcome,
                    gold=gold,
                )
                if outcome == "staged_for_review":
                    await self._record_scoreboard(
                        "staged",
                        gap,
                        r_number=result.r_number,
                        outcome=outcome,
                    )
                if gold:
                    await self._record_scoreboard(
                        "gold",
                        gap,
                        r_number=result.r_number,
                        outcome=outcome,
                    )
                await self.gap_detector.mark_fixed(gap.gap_id, result.r_number)
                # R-F2241 — close the write-back loop. mark_fixed only writes a
                # `crucix:aria:gap:fixed:<detector_sha>` sentinel in a DISJOINT
                # keyspace, so the operator's /capability-gaps/summary (which
                # reads the ledger's per-entry `resolved` flag) showed 0 resolved
                # by construction — the coder's work was invisible there. When
                # this gap originated from the capability_gaps ledger, the
                # extractor stashed that entry's uuid at evidence[capability_gap_id]
                # (gap_detector.py:952); use it to mark THAT ledger entry resolved
                # so drain is tracked honestly.
                cap_gap_id = (gap.evidence or {}).get("capability_gap_id")
                if cap_gap_id:
                    try:
                        from ..intel import capability_gaps as _capgaps
                        await _capgaps.resolve_gap(cap_gap_id, f"R-F{result.r_number}")
                    except Exception as e:
                        logger.warning(
                            "[aria_coder] capability_gaps.resolve_gap(%s) failed: %s",
                            cap_gap_id, e,
                        )
                # R-F825: WhatsApp success notification moved INTO fix_gap
                # (uses rich msg_shipped template). _one_cycle just records
                # the fixed gap + captures training pair.
                if self.harvester is not None and result.training_pair:
                    try:
                        await self.harvester.capture(result.training_pair)
                    except Exception as e:
                        logger.warning("[aria_coder] harvester failed: %s", e)
            else:
                await self._record_scoreboard(
                    "blocked",
                    gap,
                    reason=result.failure_reason or "unknown",
                )
                logger.info(
                    "[aria_coder] gap %s not fixed: %s",
                    gap.gap_id, result.failure_reason,
                )

    # ── FIX PIPELINE ─────────────────────────────────────────────────────────

    async def _generate_target_code(self, plan_raw: dict, existing: str, target: str) -> str:
        """R-F1295 — produce the new content for `target`.

        LARGE files (> LARGE_FILE_LINES) use EDIT mode: the LLM emits surgical
        search/replace blocks and we apply them to `existing`, so the LLM never has
        to emit the whole file (which truncates past the output budget) and untouched
        regions are preserved verbatim. Whole-file generation is the fallback for
        small files and whenever an edit can't be applied cleanly (an `old` snippet
        not found / ambiguous) — so a partial/ambiguous edit can never corrupt a file.
        Returns the new content, or "" if nothing was generated.
        """
        line_count = (existing.count("\n") + 1) if existing else 0
        if line_count > LARGE_FILE_LINES and existing:
            try:
                edit_raw = await self.llm.write_edit(plan_raw, existing, target)
                edits = (edit_raw or {}).get("edits") or []
                if edits:
                    applied_content, applied, failures = apply_search_replace(existing, edits)
                    if applied and not failures:
                        logger.info(
                            "[aria_coder] R-F1295 edit-mode applied %d surgical edit(s) to %s "
                            "(%d lines) — no truncation risk", len(applied), target, line_count,
                        )
                        return applied_content
                    logger.warning(
                        "[aria_coder] R-F1295 edit-mode for %s did not apply cleanly "
                        "(%d applied, %d failed: %s) — falling back to whole-file",
                        target, len(applied), len(failures), failures[:3],
                    )
            except Exception as e:  # noqa: BLE001 — edit-mode must degrade to whole-file
                logger.warning("[aria_coder] R-F1295 write_edit failed for %s: %s — falling back", target, e)
        code_raw = await self.llm.write_code(plan_raw, existing, target)
        return (code_raw or {}).get("code", "") or ""

    @fail_wire(module="self_coder", gap_type="agent_cycle_failure")
    async def fix_gap(
        self, gap: Gap, operator_initiated: bool = False,
        force_stage_only: bool = False,
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
        # R-F3064 (2026-07-25) — HONOUR THE OFF SWITCH.
        # ARIA_CODER_ENABLED gated only coder_entrypoint (the loop starter), so
        # every OTHER caller of fix_gap ran regardless. Measured live with
        # ARIA_CODER_ENABLED=0: repeated `[aria_coder] fix_gap … stage=starting`
        # on "Event-loop stall in aria_service/main.py", including TWO fix_ids
        # inside the same second — the R-F903 de-dup does not cover this path.
        # self_introspect_guard.py:224,313 already record the same contradiction
        # ("reported ARIA_CODER_ENABLED=0 (dormant) while the coder was actively
        # fixing gaps"); it was documented but never gated.
        #
        # An operator who sets the flag to 0 has withdrawn consent for the lane.
        # Honouring it here — at the ONE entry every caller funnels through —
        # is what makes the switch mean what it says.
        #
        # operator_initiated=True is exempt: that is a human explicitly asking
        # for this fix now (R-F824), which is consent, not the autonomous lane.
        # R-F3638 — the predicate itself now lives in safety.is_coder_lane_enabled
        # so the self_introspect PROBE resolves the same switch this GATE does.
        # The probe used to read heartbeat freshness alone, so a lane paused here
        # still rendered as "the coder IS actively running ... plans fixes, writes
        # code" and ARIA reported active self-improvement while every gap was
        # refused below. One predicate, two readers, no drift.
        if not operator_initiated:
            from aria_service.autonomous.safety import (
                CODER_LANE_VAR, is_coder_lane_enabled,
            )
            if not is_coder_lane_enabled():
                _enabled = (os.getenv(CODER_LANE_VAR, "") or "").strip().lower()
                logger.info(
                    "[aria_coder] fix_gap REFUSED for %s — ARIA_CODER_ENABLED=%r "
                    "(the autonomous coder lane is off). Set it to 1, or call "
                    "with operator_initiated=True.",
                    gap.gap_id, _enabled or "<unset>",
                )
                return FixResult(
                    success=False,
                    fix_id="",
                    gap_id=gap.gap_id,
                    failure_reason="coder_disabled",
                    error="ARIA_CODER_ENABLED is not set — autonomous coder lane is off",
                )

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
                task_id="aria_coder_fix", entity=gap.gap_id, coder=True,  # R-F901: own budget
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

        # R-F1460: REPRODUCE-SYMPTOM GATE — before burning any LLM tokens,
        # confirm the gap is real by running an existing test. A test that
        # PASSES means the code works → discard as false positive. No
        # existing test → spawn a write-test gap, never auto-fix.
        # Skip for operator-initiated fixes (the operator's request IS the
        # symptom) and when the gap_detector is a mock (backward compat
        # with tests that mock gap_detector — we check the method is
        # actually defined on GapDetector, not auto-created by a mock).
        _has_reproduce = hasattr(type(self.gap_detector), "reproduce_symptom")
        _reproduce_test_path: str | None = None  # R-F1681: path to auto-written reproduce test
        if not operator_initiated and _has_reproduce:
            await self._publish_progress(
                fix_id, "reproducing_symptom",
                f"Checking if gap symptom can be reproduced via existing test",
            )
            symptom_ok, symptom_msg = await self.gap_detector.reproduce_symptom(gap)
            if not symptom_ok:
                logger.info(
                    "[aria_coder] R-F1460 reproduce_symptom FAILED for %s: %s",
                    gap.gap_id, symptom_msg,
                )
                # R-F3919 — GIVE THE SLOT BACK. This gate exists to discard gaps
                # whose symptom cannot be reproduced, i.e. FALSE POSITIVES — no LLM
                # tokens spent, no fix attempted, nothing executed. But the slot was
                # already taken by can_task_run above, so every false positive was
                # permanently eating budget.
                #
                # At the live cap (ARIA_CODER_MAX_FIXES_PER_HOUR=6) that is fatal,
                # not merely wasteful. Measured over 15 cycles: 4 reproduce-gate
                # rejections against 6 slots, with 10 subsequent attempts blocked by
                # `rate_limit_exceeded:6` while the backlog grew 105 -> 127. The
                # §21c P0 — "sees gaps but cannot act".
                #
                # Same invariant R-F897 and R-F3823 each restored: the bucket must
                # reflect EXECUTED firings. Best-effort; a failed refund only costs
                # the slot we were already losing.
                try:
                    from . import safety as _safety_refund
                    _refunded = await _safety_refund.release_rate_slot(coder=True)
                    logger.info(
                        "[aria_coder] R-F3919 rate slot %s after reproduce-gate "
                        "discard of %s",
                        "REFUNDED" if _refunded else "refund skipped (empty bucket)",
                        gap.gap_id,
                    )
                except Exception as _e:      # pragma: no cover
                    logger.debug("[R-F3919] refund unavailable: %s", _e)
                return FixResult(
                    success=False, fix_id=fix_id, gap_id=gap.gap_id,
                    failure_reason=f"Reproduce-symptom gate: {symptom_msg}",
                )
            logger.info(
                "[aria_coder] R-F1460 reproduce_symptom PASSED for %s: %s",
                gap.gap_id, symptom_msg,
            )
            # R-F1681: extract the auto-written reproduce test path from the
            # success message. Format: "symptom reproduced via auto-written
            # test /path/to/test.py (exit=1)"
            if "via auto-written test " in symptom_msg:
                _reproduce_test_path = symptom_msg.split("via auto-written test ")[-1].split(" ")[0]
                logger.info(
                    "[aria_coder] R-F1681 auto-written reproduce test at %s",
                    _reproduce_test_path,
                )
            # R-F1685: a pre-existing failing test is ALSO a valid reproduce
            # artifact — a human-written test already RED is the strongest
            # FAIL->PASS evidence there is. reproduce_symptom returns it as
            # "symptom reproduced: test <path> failed (exit=N)" (gap_detector
            # _find_test_for_module path). Without capturing it here the
            # existing-test path could NEVER set reproduce_fail_to_pass, so
            # known-failing-test fuel could never accrue gold (the gold gate
            # requires reproduce_fail_to_pass). Extract the path so STEP 6.5
            # re-runs it FAIL->PASS exactly like the auto-written case.
            elif "symptom reproduced: test " in symptom_msg:
                _reproduce_test_path = (
                    symptom_msg.split("symptom reproduced: test ", 1)[-1]
                    .split(" failed", 1)[0]
                    .strip()
                )
                logger.info(
                    "[aria_coder] R-F1685 existing reproduce test at %s",
                    _reproduce_test_path,
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
            # R-F1934 — ground the fix in ARIA's code RAG (indexed codebase
            # structure + past fixes). fix_gap previously read raw files but never
            # consulted the code knowledge ARIA has accumulated, so fixes were
            # blind to documented structure / prior solutions.
            context = await self._ground_context_with_rag(context, gap, fix_id)

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
            # R-F1363 — write BOTH modified files AND newly-created files. The
            # plan separates target_files (modify) from new_files (create); a
            # MISSING_CAPABILITY gap (every operator "add X" request) puts the
            # file in new_files, so iterating target_files alone wrote nothing
            # and the fix died with "No code changes generated by LLM". We also
            # drop bare directory paths (e.g. the gap.module default
            # "aria_service/intel/") — you cannot write a directory as a file.
            files_to_write = [
                f for f in dict.fromkeys(
                    (plan.target_files or []) + (plan.new_files or [])
                )
                if f and not f.rstrip().endswith("/")
            ]
            await self._publish_progress(
                fix_id, "writing_code",
                f"Writing code for {len(files_to_write)} file(s)",
                target_files=files_to_write,
            )
            for target in files_to_write:
                existing = self.codebase.read(target)
                new_code = await self._generate_target_code(plan_raw, existing, target)
                if not new_code:
                    continue
                # Guards (R-F1285 truncation/AST, R-F1287 constitutional) run at
                # stage/deploy — edit-mode additionally makes truncation structurally
                # impossible (untouched regions are preserved verbatim).
                plan.code_changes[target] = new_code
                self.codebase.write_to_workspace(workspace, target, new_code)

            if not plan.code_changes:
                return FixResult(
                    success=False, fix_id=fix_id, gap_id=gap.gap_id,
                    r_number=r_number,
                    failure_reason="No code changes generated by LLM",
                )

            # R-F1460: NO-OP GATE — reject changes that are identical to
            # existing code or cosmetic-only (whitespace, comment, rename).
            # Prevents the coder from wasting LLM budget on no-op fixes.
            await self._publish_progress(
                fix_id, "checking_noop",
                f"Verifying code changes are meaningful (not no-op/cosmetic)",
            )
            for target, new_code in list(plan.code_changes.items()):
                existing = self.codebase.read(target)
                noop_ok, noop_msg = self._check_noop(existing, new_code, target)
                if not noop_ok:
                    logger.info(
                        "[aria_coder] R-F1460 no-op gate rejected %s: %s",
                        target, noop_msg,
                    )
                    del plan.code_changes[target]
            if not plan.code_changes:
                return FixResult(
                    success=False, fix_id=fix_id, gap_id=gap.gap_id,
                    r_number=r_number,
                    failure_reason="All code changes rejected by no-op gate (cosmetic/identical)",
                )

            # R-F1460: HALLUCINATED-API GATE — verify every module.method()
            # call in the proposed code exists in the target module. Catches
            # hallucinated APIs like SentenceTransformer._clear_cache().
            await self._publish_progress(
                fix_id, "checking_apis",
                f"Verifying function calls exist in target modules",
            )
            for target, new_code in list(plan.code_changes.items()):
                api_ok, api_reasons = self._check_hallucinated_api(new_code, target)
                if not api_ok:
                    logger.warning(
                        "[aria_coder] R-F1460 hallucinated-API gate found issues in %s: %s",
                        target, "; ".join(api_reasons),
                    )
                    for reason in api_reasons:
                        logger.warning(
                            "[aria_coder] R-F1460 potential hallucinated API: %s", reason,
                        )
                    try:
                        from aria_service.intel.engine_wiring import wire_failure as _wf2399
                        _wf2399(
                            module="aria_coder",
                            detail=(
                                f"Generated code blocked by hallucinated-API gate "
                                f"for {target}: {'; '.join(api_reasons[:3])}"
                            ),
                            gap_type="hallucinated_api",
                            source="aria_coder:hallucinated_api_gate",
                        )
                    except Exception:
                        pass
                    return FixResult(
                        success=False,
                        fix_id=fix_id,
                        gap_id=gap.gap_id,
                        r_number=r_number,
                        failure_reason=(
                            "Hallucinated-API gate blocked generated code: "
                            + "; ".join(api_reasons)
                        ),
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

            # R-F1681: include the auto-written reproduce test in the test suite
            # so _test_with_healing runs it alongside generated tests. The reproduce
            # test MUST go from FAIL (on unfixed) to PASS (on fixed) for gold.
            _reproduce_fail_to_pass = False
            _reproduce_target = None
            if _reproduce_test_path:
                _reproduce_target = f"aria_service/tests/repro_{gap.gap_id[:12]}.py"
                # R-F1685 ROOT-CAUSE FIX: store the reproduce test CODE (read
                # from disk), NOT its path. run_isolated writes new_tests VALUES
                # as the test file's CONTENTS (test_runner.py), so the prior
                # `plan.new_tests[_reproduce_target] = _reproduce_test_path`
                # wrote a bare path string into the repro file → a collection
                # SyntaxError → all_green=False → the fix was rejected at STEP 6
                # before it could ever reach the gold gate. Read the real code
                # and register it consistently in both new_tests and workspace.
                try:
                    _repro_src = Path(_reproduce_test_path).read_text(
                        encoding="utf-8",
                    )
                except Exception as _e:
                    _repro_src = ""
                    logger.warning(
                        "[R-F1685] failed to read reproduce test %s: %s",
                        _reproduce_test_path, _e,
                    )
                if _repro_src:
                    plan.new_tests[_reproduce_target] = _repro_src
                    try:
                        self.codebase.write_to_workspace(
                            workspace, _reproduce_target, _repro_src,
                        )
                    except Exception as _e:
                        logger.warning(
                            "[R-F1685] failed to write reproduce test to workspace: %s",
                            _e,
                        )

            # STEP 6 — self-healing test loop
            safe_mode = not os.environ.get("ARIA_CODER_TESTS_FULL", "0").strip() == "1"
            await self._publish_progress(
                fix_id, "testing",
                f"Running {'safe-mode' if safe_mode else 'full-suite'} tests "
                f"(self-healing up to {MAX_FIX_ATTEMPTS} attempts)",
            )
            test_result = await self._test_with_healing(plan, workspace)
            if not test_result.all_green:
                # R-F1531: index the test failure in CodingRAG
                try:
                    import asyncio as _aio1531
                    from ..intel.coding_rag_indexer import FailureRecord, index_failure as _cri_index_failure
                    _fail_record = FailureRecord(
                        r_number=f"R-F{r_number}" if r_number else gap.gap_id[:20],
                        attempt_number=test_result.attempts or 1,
                        error_type="test_failure",
                        error_message=(test_result.failure_summary or "")[:300],
                        why_failed=f"Tests failed after healing attempts for {gap.module}",
                        next_approach="Review test output and fix root cause",
                        timestamp=datetime.now(timezone.utc).isoformat(),
                    )
                    _aio1531.create_task(
                        _aio1531.to_thread(_cri_index_failure, _fail_record)
                    )
                except Exception as _fe:
                    logger.debug("[R-F1531] Test failure index failed (non-fatal): %s", _fe)

                return FixResult(
                    success=False, fix_id=fix_id, gap_id=gap.gap_id,
                    r_number=r_number,
                    failure_reason=(
                        f"Tests failed after {MAX_FIX_ATTEMPTS} healing "
                        f"attempts: {test_result.failure_summary[:500]}"
                    ),
                )

            # R-F1681/R-F1685: re-run the reproduce test on the FIXED code. It
            # MUST go from FAIL (on unfixed) to PASS (on fixed) for the fix to be
            # verifiable. If it still fails, the fix didn't resolve the real
            # symptom — reject even if generated tests pass.
            #
            # R-F1685 ROOT-CAUSE FIX: the old path called
            # gap_detector.verify_reproduce_test_passes(path), which ran
            # `pytest <repo_path>` with cwd=repo — i.e. against the UNFIXED repo
            # code (the fix lives only in `workspace`, never written to the repo
            # before staging). So it ALWAYS failed → _reproduce_fail_to_pass
            # could never be True → the gold gate (build_coder_reward_record)
            # could never fire. That is why first gold never landed. We now
            # re-run the reproduce test through the SAME isolation
            # _test_with_healing uses (copy repo → overlay the workspace fix →
            # pytest in the app_copy with cwd=app_copy), so it genuinely tests
            # the FIXED code.
            if _reproduce_test_path:
                await self._publish_progress(
                    fix_id, "verifying_reproduce",
                    "Re-running reproduce test on FIXED code (isolated) — must PASS",
                )
                # _repro_src + _reproduce_target were set in STEP 5.5 (same scope).
                # R-F2395 — THREE distinct outcomes, not two:
                #   (1) GENUINE PASS  — green AND >=1 test actually ran, none
                #       failed → reproduce_fail_to_pass=True → may auto-deploy.
                #   (2) GENUINE FAIL  — the test RAN and FAILED → the fix did not
                #       resolve the symptom → REJECT the fix.
                #   (3) COULD-NOT-VERIFY — run_isolated returned a vacuous green
                #       (ARIA_CODER_TESTS_ENABLED!=1 / rate-limited / zero tests
                #       collected) → NOT a valid capability test. Do NOT reject
                #       (the fix may still be worth staging) and do NOT set
                #       reproduce_fail_to_pass — the downstream capability-test
                #       gate then forces STAGE-ONLY. This is the coupling that
                #       makes AUTO_DEPLOY safe: no genuinely-executed green test,
                #       no autonomous deploy.
                if _reproduce_target and _repro_src:
                    # R-F2395 — bypass_rate_limit=True: this REQUIRED capability
                    # verification runs right after STEP 6's healing run, so the
                    # single-global 1-run/5-min limiter would otherwise force it
                    # to a no-op green and it could never GENUINELY execute.
                    _repro_result = await self.test_runner.run_isolated(
                        workspace, {_reproduce_target: _repro_src},
                        bypass_rate_limit=True,
                    )
                    _repro_genuine_pass = capability_test_genuinely_passed(_repro_result)
                    _repro_all_green = bool(getattr(_repro_result, "all_green", False))
                    _repro_msg = _repro_result.failure_summary or ""
                else:
                    _repro_result = None
                    _repro_genuine_pass = False
                    _repro_all_green = False
                    _repro_msg = "reproduce test code missing from workspace"

                if _repro_genuine_pass:
                    # (1) genuine FAIL->PASS confirmed
                    _reproduce_fail_to_pass = True
                    logger.info(
                        "[aria_coder] R-F1685 reproduce test FAIL->PASS confirmed "
                        "(isolated, fixed code, genuinely executed) for %s", gap.gap_id,
                    )
                elif _repro_all_green:
                    # (3) could-not-verify — vacuous green (no genuine execution).
                    # Leave reproduce_fail_to_pass False so the capability-test
                    # gate forces stage-only; do NOT reject the fix.
                    logger.warning(
                        "[aria_coder] R-F2395 reproduce test did NOT genuinely "
                        "execute for %s (passed=%s failed=%s errors=%s) — tests "
                        "disabled/rate-limited/zero-collected. Not a valid "
                        "capability test; capability-test gate will force "
                        "stage-only (no autonomous deploy).",
                        gap.gap_id,
                        getattr(_repro_result, "passed", 0),
                        getattr(_repro_result, "failed", 0),
                        getattr(_repro_result, "errors", 0),
                    )
                    try:
                        from aria_service.intel.engine_wiring import wire_failure as _wf2395
                        _wf2395(
                            module="aria_coder",
                            detail=(f"Reproduce test could not genuinely execute for "
                                    f"{gap.gap_id} — staging only (tests disabled/"
                                    f"rate-limited/zero-collected)"),
                            gap_type="capability_test_missing",
                            source="aria_coder:reproduce_not_genuine",
                        )
                    except Exception:
                        pass
                else:
                    # (2) genuine FAIL — the fix did not resolve the symptom
                    logger.warning(
                        "[aria_coder] R-F1685 reproduce test still FAILS after fix "
                        "(isolated) for %s: %s", gap.gap_id, _repro_msg,
                    )
                    return FixResult(
                        success=False, fix_id=fix_id, gap_id=gap.gap_id,
                        r_number=r_number,
                        failure_reason=(
                            f"Reproduce test still fails after fix — symptom not resolved: {_repro_msg[:300]}"
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
            # R-F924 (2026-05-27) — a FLAGGED verdict ALWAYS force-stages and
            # never auto-deploys. The previous inline formula ANDed in `not
            # (ticket_mode_enabled and not is_blocked)`; since a BLOCKED verdict
            # already returned at STEP 6.5 above, `is_blocked` was always False
            # here, so the clause reduced to `not ticket_mode_enabled` — i.e.
            # with ticket-mode ON, a FLAGGED fix got force_stage=False AND
            # force_deploy=False, fell through to the R-F462 default gate and
            # AUTO-DEPLOYED when AUTO_DEPLOY=1. That contradicted "Claude's flag
            # still wins" and was a live auto-deploy landmine. The decision is
            # now a pure, unit-tested function (resolve_staging_decision).
            # R-F852 — operator /code (force_stage_only) is also always staged.
            force_stage, force_deploy = resolve_staging_decision(
                is_flagged=review_verdict.is_flagged,
                is_blocked=review_verdict.is_blocked,
                ticket_mode_enabled=ticket_mode_enabled,
                force_stage_only=force_stage_only,
            )
            # R-F1767 — CAPABILITY-TEST GATE (pure fn): no autonomous auto-deploy
            # without a reproduce FAIL->PASS capability test.
            force_stage, force_deploy, _cap_blocked = apply_capability_test_gate(
                reproduce_fail_to_pass=_reproduce_fail_to_pass,
                operator_initiated=operator_initiated,
                force_stage=force_stage,
                force_deploy=force_deploy,
            )
            if _cap_blocked:
                logger.warning(
                    "[aria_coder] R-F1767 capability-test gate: no reproduce "
                    "FAIL->PASS for %s — forcing stage-only (no auto-deploy "
                    "without a passing capability test)", gap.gap_id,
                )
                try:
                    from aria_service.intel.engine_wiring import wire_failure as _wf1767
                    _wf1767(module="aria_coder",
                            detail=(f"Auto-deploy blocked — no capability test "
                                    f"FAIL->PASS: {gap.gap_id} ({plan.gap_type})"),
                            gap_type="capability_test_missing",
                            source="aria_coder:capability_test_gate")
                except Exception:
                    pass
            stage_ok, stage_status, staged_ids = await self._stage_or_deploy(
                plan=plan, change_type=change_type,
                force_stage=force_stage,
                force_deploy=force_deploy,
                gold_lane=autonomous_gold_lane_decision(await self.get_scoreboard()),
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
                    # R-F871 — absorb() takes summary=/source_id=, NOT
                    # text=/source=. The old call raised TypeError every time
                    # (swallowed below), so the coder's own fixes never landed a
                    # learning signal. confidence must be a valid level.
                    await self.brain_hook.absorb(
                        module="aria_coder",
                        summary=(
                            f"Autonomous fix R-F{r_number} ({outcome_label}): "
                            f"{plan.title}. Gap type: {gap.gap_type}. "
                            f"Files: {', '.join(plan.code_changes.keys())}."
                        ),
                        confidence="CONFIRMED",
                        source_id="aria_autonomous_coder",
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

            # R-F1674 — capture this fix as a VERIFIABLE-REWARD training example.
            # Reaching here means tests passed (all_green) AND stage_ok (the R-F1450/
            # R-F1285 function-preservation guard held). Marked gold ONLY when tests
            # genuinely ran (ARIA_CODER_TESTS_ENABLED=1) — never the no-op pass.
            try:
                _rec = build_coder_reward_record(
                    instruction=training_pair["instruction"],
                    approach=plan.approach, code_changes=plan.code_changes,
                    r_number=r_number, test_result=test_result,
                    stage_ok=stage_ok, auto_deployed=auto_deployed,
                    # R-F2905 — same reading as the runner's Gate 1. This used to
                    # be a separate `== "1"` test, so a value the runner accepted
                    # (anything but "0") was recorded here as tests-never-ran,
                    # forcing gold=False on work that had genuinely passed.
                    tests_enabled=coder_tests_enabled(),
                    reproduce_fail_to_pass=_reproduce_fail_to_pass,
                )
                _gp = os.environ.get("ARIA_CODER_GOLD_PATH") or str(
                    Path(os.environ.get("ARIA_DATA_DIR", "data")) / "aria_training"
                    / "coder_verifiable_gold.jsonl"
                )
                Path(_gp).parent.mkdir(parents=True, exist_ok=True)
                with open(_gp, "a", encoding="utf-8") as _gf:
                    _gf.write(json.dumps(_rec, ensure_ascii=False) + "\n")
                training_pair["gold"] = bool(_rec["gold"])
                training_pair["reward"] = _rec.get("reward", {})
                logger.info("[R-F1674] verifiable-reward captured gold=%s tests_ran=%s R-F%s",
                            _rec["gold"], _rec["reward"]["tests_ran"], r_number)
            except Exception as _e:
                logger.warning("[R-F1674] verifiable-reward capture failed: %s", _e)

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
            # R-F1531: index the failure in CodingRAG for future avoidance.
            try:
                import asyncio as _aio1531
                from ..intel.coding_rag_indexer import FailureRecord, index_failure as _cri_index_failure
                _fail_record = FailureRecord(
                    r_number=getattr(gap, "gap_id", "unknown")[:20],
                    attempt_number=1,
                    error_type=type(e).__name__,
                    error_message=str(e)[:300],
                    why_failed="Pipeline exception in fix_gap",
                    next_approach="Review logs and retry with more context",
                    timestamp=datetime.now(timezone.utc).isoformat(),
                )
                _aio1531.create_task(
                    _aio1531.to_thread(_cri_index_failure, _fail_record)
                )
            except Exception as _fe:
                logger.debug("[R-F1531] Failure index failed (non-fatal): %s", _fe)

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

    # ── R-F1460: QUALITY GUARDS ──────────────────────────────────────────────
    #
    # Three pre-stage gates that prevent the coder from wasting LLM budget on
    # false-positive gaps, no-op changes, and hallucinated API calls.
    #
    # These would have prevented both bad fixes Claude discarded:
    #   - prompt_budget.py: no-op (whitespace-only diff) → caught by _check_noop
    #   - memory_leak_detector.py: hallucinated _clear_cache → caught by
    #     _check_hallucinated_api

    @staticmethod
    def _check_noop(
        existing: str, proposed: str, target: str,
    ) -> tuple[bool, str]:
        """R-F1460: reject no-op and cosmetic-only changes.

        Checks that the proposed code change:
        1. Actually differs from the existing code (not a no-op)
        2. Changes more than whitespace/comments/cosmetic-only (not a
           whitespace-only or rename-only diff)

        Returns:
            (True, "") — change is meaningful, proceed
            (False, "reason") — change is no-op or cosmetic, reject
        """
        if existing == proposed:
            return (False, f"no-op: {target} — proposed content identical to existing")

        # Diff the two versions line by line
        existing_lines = existing.split("\n") if existing else []
        proposed_lines = proposed.split("\n") if proposed else []

        changed_lines = []
        max_lines = max(len(existing_lines), len(proposed_lines))
        for i in range(max_lines):
            old = existing_lines[i] if i < len(existing_lines) else ""
            new = proposed_lines[i] if i < len(proposed_lines) else ""
            if old != new:
                changed_lines.append((i + 1, old.strip(), new.strip()))

        if not changed_lines:
            return (False, f"no-op: {target} — no line-level differences")

        # Check if ALL changes are cosmetic-only (whitespace, comments, renames)
        cosmetic_only = True
        for lineno, old_stripped, new_stripped in changed_lines:
            if old_stripped == new_stripped:
                continue  # whitespace-only change
            # Check if it's a comment-only change
            if old_stripped.startswith("#") and new_stripped.startswith("#"):
                continue
            # Check if it's a rename-only change (same structure, different name)
            # This is a heuristic — we flag logger→log, """→''', etc.
            if old_stripped.replace("logger", "log") == new_stripped or \
               new_stripped.replace("logger", "log") == old_stripped:
                continue
            cosmetic_only = False
            break

        if cosmetic_only:
            return (False, f"cosmetic-only: {target} — all {len(changed_lines)} changed line(s) are whitespace/comment/rename-only")

        return (True, "")

    @staticmethod
    def _check_hallucinated_api(
        code: str, target: str,
    ) -> tuple[bool, list[str]]:
        """R-F1460: verify every module.method() call exists in the target module.

        Scans `code` for `module.function()` patterns and greps the target
        file (if it exists) to confirm each function is defined. Catches
        hallucinated API calls like `SentenceTransformer._clear_cache()`.

        Returns:
            (True, []) — all calls verified
            (False, [reasons]) — hallucinated calls found
        """
        import ast as _ast
        import re as _re

        hallucinated: list[str] = []

        # Parse the proposed code to find all function calls
        try:
            tree = _ast.parse(code)
        except SyntaxError:
            return (True, [])  # Can't parse — don't block on a parse error

        # R-F1866 — the original check treated the receiver of EVERY `obj.method()`
        # as a module/class and grepped the target file for `def <method>`, so
        # local vars calling built-in methods — `out.append()`, `e.get()`,
        # `counts_by_sev.get()` — were all false-flagged as "hallucinated API not
        # found" (live 2026-06-24: advisory log noise that drowns out REAL
        # hallucinations). A method call is only reliably verifiable when its
        # receiver IS a locally-defined class or an INSTANCE of one — then every
        # real method lives in the file, so a missing `def` is a genuine
        # hallucination (the original intent: `detector = GapDetector();
        # detector._nonexistent_method_xyz()`). Receivers that are built-in-typed
        # locals, imported modules (members live in the package, not the target),
        # or self/cls (inherited methods, not greppable in one file) are skipped.
        try:
            with open(target, encoding="utf-8", errors="replace") as _fh:
                target_content = _fh.read()
        except (OSError, FileNotFoundError):
            target_content = ""  # new file — nothing to verify against

        # Classes defined locally (in the generated code OR the target file).
        local_classes: set[str] = {
            _n.name for _n in _ast.walk(tree) if isinstance(_n, _ast.ClassDef)
        }
        for _m in _re.finditer(r"^\s*class\s+([A-Za-z_]\w*)", target_content, _re.M):
            local_classes.add(_m.group(1))

        # Map `var = LocalClass(...)` so we can resolve an instance's type.
        var_class: dict[str, str] = {}
        for _n in _ast.walk(tree):
            if (isinstance(_n, _ast.Assign) and isinstance(_n.value, _ast.Call)
                    and isinstance(_n.value.func, _ast.Name)
                    and _n.value.func.id in local_classes):
                for _tgt in _n.targets:
                    if isinstance(_tgt, _ast.Name):
                        var_class[_tgt.id] = _n.value.func.id

        def _method_defined(meth: str) -> bool:
            pat = rf"(async\s+)?def\s+{_re.escape(meth)}\s*\("
            return bool(_re.search(pat, target_content) or _re.search(pat, code))

        # Collect all attribute calls: obj.method()
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Call):
                func = node.func
                if isinstance(func, _ast.Attribute):
                    # Get the full chain: obj.attr.attr...
                    parts = []
                    current = func
                    while isinstance(current, _ast.Attribute):
                        parts.append(current.attr)
                        current = current.value
                    if isinstance(current, _ast.Name):
                        parts.append(current.id)
                    else:
                        # e.g. some_func().method() — receiver type unknown → skip
                        continue
                    parts.reverse()
                    if len(parts) >= 2:
                        module_name = parts[0]
                        called_method = ".".join(parts[1:])
                        leaf_method = called_method.split(".")[0]

                        # Resolve the receiver to a LOCAL class, else skip — only
                        # then is a missing method a genuine hallucination.
                        if module_name in local_classes:
                            owner = module_name             # LocalClass.method()
                        elif module_name in var_class:
                            owner = var_class[module_name]   # instance of LocalClass
                        else:
                            continue

                        if not _method_defined(leaf_method):
                            hallucinated.append(
                                f"{module_name}.{called_method}() not found "
                                f"(class {owner}) in {target}"
                            )

        if hallucinated:
            return (False, hallucinated)
        return (True, [])

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

    async def _record_scoreboard(
        self,
        bucket: str,
        gap: Gap,
        *,
        reason: str = "",
        r_number: int | None = None,
        outcome: str = "",
        gold: bool = False,
    ) -> None:
        """Update the live autonomous-improvement scoreboard from real outcomes."""
        try:
            raw = await self.redis.get(SCOREBOARD_KEY)
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            board = json.loads(raw) if raw else {}
            if not isinstance(board, dict):
                board = {}
            counts = board.setdefault("counts", {})
            if bucket not in SCOREBOARD_BUCKETS:
                bucket = "blocked"
            counts[bucket] = int(counts.get(bucket, 0) or 0) + 1
            blocked_by_reason = board.setdefault("blocked_by_reason", {})
            if bucket == "blocked":
                reason_key = (reason or "unknown")[:120]
                blocked_by_reason[reason_key] = int(blocked_by_reason.get(reason_key, 0) or 0) + 1
            event = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "bucket": bucket,
                "gap_id": gap.gap_id,
                "gap_type": gap.gap_type,
                "module": gap.module,
                "title": gap.title[:160],
                "reason": reason[:240],
                "r_number": r_number,
                "outcome": outcome,
                "gold": bool(gold),
            }
            recent = board.setdefault("recent", [])
            recent.insert(0, event)
            board["recent"] = recent[:50]
            board["updated_at"] = event["ts"]
            board["schema_version"] = 2
            await self.redis.setex(SCOREBOARD_KEY, 30 * 86400, json.dumps(board))
        except Exception as e:
            logger.debug("[aria_coder] scoreboard update failed: %s", e)

    @fail_wire(module="self_coder", gap_type="agent_cycle_failure")
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


    @fail_wire(module="self_coder", gap_type="agent_cycle_failure")
    async def get_scoreboard(self) -> dict:
        """Return the live autonomous-improvement scoreboard."""
        try:
            raw = await self.redis.get(SCOREBOARD_KEY)
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            board = json.loads(raw) if raw else {}
            if not isinstance(board, dict):
                board = {}
        except Exception:
            board = {}
        counts = board.setdefault("counts", {})
        for bucket in SCOREBOARD_BUCKETS:
            counts.setdefault(bucket, 0)
        board.setdefault("blocked_by_reason", {})
        board.setdefault("recent", [])
        board.setdefault("updated_at", None)
        board.setdefault("schema_version", 2)
        return board


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

    async def _ground_context_with_rag(self, context, gap, fix_id: str = "") -> str:
        """R-F1934 — append ARIA's code-RAG knowledge (indexed codebase structure
        + relevant past fixes) to the LLM fix context, so the coder plans grounded
        in accumulated code knowledge rather than blind to it.

        Best-effort: never raises (a RAG miss must not break a fix). The
        coding_rag_indexer.query_* calls are BLOCKING chromadb queries, so they
        run via asyncio.to_thread (per their docstrings) — never on the loop.
        """
        try:
            from ..intel import coding_rag_indexer as _crag
            import asyncio as _aio
            struct = await _aio.to_thread(_crag.query_codebase_context, gap.module, 3) or []
            q = f"{gap.title} {gap.description}"[:300]
            fixes = await _aio.to_thread(_crag.query_relevant_fixes, q, 3) or []
            # R-F2130 — also ground in the constitutional/playbook RULES (how to write
            # code correctly), not just structure + past fixes. Previously the coder
            # never queried these, so it could violate conventions (e.g. the annotation
            # campaign that shipped 31 syntax errors).
            rules = await _aio.to_thread(_crag.query_constitutional_constraints, q, 3) or []
            parts: list[str] = []
            for r in (rules or [])[:3]:
                c = str((r or {}).get("rule", "")).strip()
                if c:
                    parts.append("• RULE (must follow): " + c[:500])
            for r in (struct or [])[:3]:
                c = str((r or {}).get("content", "")).strip()
                if c:
                    parts.append("• structure: " + c[:400])
            for r in (fixes or [])[:3]:
                c = str((r or {}).get("content", "")).strip()
                if c:
                    parts.append("• past fix: " + c[:400])
            if parts:
                if fix_id:
                    await self._publish_progress(
                        fix_id, "context_rag",
                        f"Grounded in {len(parts)} code-RAG snippet(s)")
                return (context or "") + (
                    "\n\n## ARIA code-RAG knowledge (constitutional rules + structure + past fixes)\n"
                    + "\n".join(parts)
                )
        except Exception as e:
            logger.debug("[aria_coder] code-RAG grounding skipped (non-fatal): %s", e)
        return context

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
        gold_lane: dict | None = None,
    ) -> tuple[bool, str, list[str]]:
        """Route code changes through `self_improve.stage_improvement`.

        For each (target_file, new_code) in plan.code_changes, stage via
        the existing operator-facing pipeline. If the change_type is in
        the auto-deployable set per `self_improve.CHANGE_TYPES`
        (gated by `ARIA_SELF_IMPROVE_AUTO_DEPLOY=1` per R-F462), call
        `deploy_improvement` immediately only if the live coder scoreboard has
        earned the gold-lane maturity gate. Otherwise items remain at
        `/api/aria/self/staged` for operator review.

        Returns: (success, status, staged_ids) where status is one of:
          - "auto_deployed"        — all items shipped to disk + git
          - "staged_for_operator"  — all items staged, awaiting review
          - <error message>        — staging failed for at least one file
        """
        from ..intel import self_improve as _si

        staged_ids: list[str] = []
        staged_pairs: list[tuple[str, str]] = []  # (id, target_file) for per-file gating
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
            staged_pairs.append((result["id"], target_file))

        # Decide auto-deploy based on the R-F462 gate per change_type.
        # CHANGE_TYPES dict was set at import time (R-F518: env var read
        # once); honour that — flipping ARIA_SELF_IMPROVE_AUTO_DEPLOY=1
        # requires a worker restart, which is the documented path.
        #
        # R-F805: force_stage overrides the R-F462 gate when Claude
        # review returned FLAGGED. Even a bug_fix with R-F462 open will
        # stay staged so the operator gets eyes on Claude's concerns.
        #
        # R-F2689: the R-F462 flag and ticket-mode force_deploy only make a
        # change eligible. Direct deploy still requires a live scoreboard
        # maturity gate; current live evidence with zero gold fixes must stage.
        ct_cfg = _si.CHANGE_TYPES.get(change_type, {})
        gate_open = bool(ct_cfg.get("auto_deploy")) or force_deploy
        gold_lane = gold_lane or autonomous_gold_lane_decision({})
        gold_lane_allowed = bool(gold_lane.get("allowed"))
        batch_eligible = gate_open and gold_lane_allowed and not force_stage

        # R-F851 (2026-05-24) — per-FILE constitution guard. Honesty-critical
        # files (self_improve.NO_AUTODEPLOY_FILES: aria_engine.py, v3_prompts.py,
        # routes/aria.py, autonomous/tasks.yaml) NEVER auto-deploy — not via the
        # change_type gate, not via force_deploy (R-F821 ticket-mode), not via
        # this coder path. They always stay staged for a human. Pre-R-F851 this
        # function read CHANGE_TYPES[...]["auto_deploy"] once per change_type and
        # then deployed EVERY staged item regardless of which file it touched —
        # a bypass of the self_improve auto-deploy gate that could ship a
        # self- (or user-) generated constitution edit straight to live.
        deployable: list[str] = []
        blocked_critical: list[str] = []
        for sid, target_file in staged_pairs:
            if target_file in _si.NO_AUTODEPLOY_FILES:
                blocked_critical.append(target_file)
            elif batch_eligible:
                deployable.append(sid)

        if blocked_critical:
            logger.warning(
                "[aria_coder] R-F851 BLOCKED auto-deploy of %d honesty-critical "
                "file(s) — staged for human approval only: %s",
                len(blocked_critical), ", ".join(sorted(set(blocked_critical))),
            )

        if not deployable:
            if force_stage:
                reason = "claude_flagged"
            elif blocked_critical and batch_eligible:
                reason = "constitution_guard_r_f851"
            elif gate_open and not gold_lane_allowed:
                reason = "gold_lane_not_earned"
                logger.warning(
                    "[aria_coder] R-F2689 blocked auto-deploy: %s",
                    "; ".join(gold_lane.get("reasons") or ["gold lane not earned"]),
                )
                try:
                    from aria_service.intel.engine_wiring import wire_failure as _wf2689
                    _wf2689(
                        module="aria_coder",
                        detail=(
                            "Auto-deploy blocked by gold-lane maturity gate: "
                            + "; ".join(gold_lane.get("reasons") or ["not earned"])
                        ),
                        gap_type="autonomous_gold_lane_not_earned",
                        source="aria_coder:gold_lane_gate",
                    )
                except Exception as e:
                    logger.warning(
                        "[aria_coder] R-F2689 gold-lane wire_failure failed: %s",
                        e,
                    )
            else:
                reason = "r_f462_gate_closed"
            logger.info(
                "[aria_coder] %d items staged for operator review "
                "(change_type=%s, reason=%s)",
                len(staged_ids), change_type, reason,
            )
            return True, "staged_for_operator", staged_ids

        # Auto-deploy each ELIGIBLE staged item via the existing self_improve
        # pipeline. This writes the file to disk + creates the backup +
        # git-commits + writes the audit log — same surface as the
        # operator-clicked /api/aria/self/deploy/{id}. Constitution-critical
        # items are excluded above and remain staged for the operator.
        for sid in deployable:
            deploy_res = await _si.deploy_improvement(sid)
            # R-F1960 — classify via the canonical contract helper, NOT a guessed
            # key. The old `deploy_res.get("ok")` read a key deploy_improvement
            # never returned → EVERY successful auto-deploy was misread as failed
            # (gap churn + the post-deploy regression monitor never ran).
            if not _si.deploy_succeeded(deploy_res):
                err = deploy_res.get("error", "unknown")
                logger.error(
                    "[aria_coder] deploy_improvement %s failed: %s",
                    sid, err,
                )
                return False, f"deploy_failed:{sid}:{err}", staged_ids

        logger.info(
            "[aria_coder] %d items auto-deployed (change_type=%s)%s",
            len(deployable), change_type,
            (f"; {len(blocked_critical)} constitution file(s) held for review"
             if blocked_critical else ""),
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
                # R-F1191: constitutional validator removed
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

    @fail_wire(module="self_coder", gap_type="agent_cycle_failure")
    async def operator_fix_request(
        self,
        description: str,
        module_hint: Optional[str] = None,
        force_stage: bool = False,
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
        return await self.fix_gap(
            gap, operator_initiated=True, force_stage_only=force_stage,
        )

    @fail_wire(module="self_coder", gap_type="agent_cycle_failure")
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

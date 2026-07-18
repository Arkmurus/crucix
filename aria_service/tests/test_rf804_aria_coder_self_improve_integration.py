"""R-F804 — Tests for ARIACoder → self_improve.py staging integration.

What we're guarding against
───────────────────────────
R-F802 + R-F803 wired the autonomous coder as a self-contained engine
that called FlyDeployer directly. That bypassed the R-F462 audit
mandate's staging surface (`/api/aria/self/staged` + operator deploy).
R-F804 routes the engine's outputs through `self_improve.stage_improvement`
so the existing operator-approval flow holds regardless of who wrote
the code (human or ARIA).

Critical invariants tested
──────────────────────────
1. gap_type → change_type mapping is DETERMINISTIC (per CLAUDE.md §3,
   the LLM cannot judge its own change's risk).
2. safety.can_task_run is consulted BEFORE any LLM tokens burn.
3. Stage call uses self_improve.stage_improvement (existing operator
   surface), not a separate path.
4. Auto-deploy decision derives from self_improve.CHANGE_TYPES[ct]
   ["auto_deploy"] — the same gate operator-clicked deploys honour.
5. When CHANGE_TYPES.bug_fix.auto_deploy is False (default; R-F462
   protection), even a bug_fix change stays staged.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _run(coro):
    return asyncio.run(coro)


# ════════════════════════════════════════════════════════════════════════════
# gap_type → change_type mapping
# ════════════════════════════════════════════════════════════════════════════

class TestGapTypeMapping:
    def test_module_bug_maps_to_bug_fix(self) -> None:
        from aria_service.autonomous.self_coder import gap_type_to_change_type
        from aria_service.autonomous.gap_detector import GapType
        assert gap_type_to_change_type(GapType.MODULE_BUG) == "bug_fix"

    def test_hallucination_maps_to_bug_fix(self) -> None:
        from aria_service.autonomous.self_coder import gap_type_to_change_type
        from aria_service.autonomous.gap_detector import GapType
        # Hallucination is a high-severity bug → bug_fix
        assert gap_type_to_change_type(GapType.HALLUCINATION) == "bug_fix"

    def test_performance_maps_to_optimisation(self) -> None:
        from aria_service.autonomous.self_coder import gap_type_to_change_type
        from aria_service.autonomous.gap_detector import GapType
        assert gap_type_to_change_type(GapType.PERFORMANCE) == "optimisation"

    def test_data_gap_maps_to_enhancement(self) -> None:
        from aria_service.autonomous.self_coder import gap_type_to_change_type
        from aria_service.autonomous.gap_detector import GapType
        assert gap_type_to_change_type(GapType.DATA_GAP) == "enhancement"

    def test_missing_capability_maps_to_enhancement(self) -> None:
        from aria_service.autonomous.self_coder import gap_type_to_change_type
        from aria_service.autonomous.gap_detector import GapType
        assert gap_type_to_change_type(GapType.MISSING_CAPABILITY) == "enhancement"

    def test_unknown_gap_type_defaults_to_enhancement(self) -> None:
        """An unknown gap_type must default to the SAFEST bucket — never
        auto-deployable. enhancement is always staged."""
        from aria_service.autonomous.self_coder import gap_type_to_change_type
        assert gap_type_to_change_type("some_future_type") == "enhancement"

    def test_every_known_gap_type_in_change_types(self) -> None:
        """Every value in GAP_TYPE_TO_CHANGE_TYPE must be a real key in
        self_improve.CHANGE_TYPES — otherwise stage_improvement rejects."""
        from aria_service.autonomous.self_coder import GAP_TYPE_TO_CHANGE_TYPE
        from aria_service.intel.self_improve import CHANGE_TYPES
        for gap_type, change_type in GAP_TYPE_TO_CHANGE_TYPE.items():
            assert change_type in CHANGE_TYPES, (
                f"{gap_type} → {change_type} not in self_improve.CHANGE_TYPES"
            )


# ════════════════════════════════════════════════════════════════════════════
# ARIACoder._stage_or_deploy
# ════════════════════════════════════════════════════════════════════════════

def _make_coder():
    """Construct an ARIACoder with all dependencies mocked."""
    from aria_service.autonomous.self_coder import ARIACoder
    from pathlib import Path
    import tempfile

    redis = MagicMock()
    return ARIACoder(
        redis_client=redis,
        aria_service_url="http://test",
        whatsapp_notifier=None,
        brain_hook=None,
        output_harvester=None,
        gap_detector=MagicMock(),
        llm=MagicMock(),
        validator=MagicMock(),
        codebase=MagicMock(),
        test_runner=MagicMock(),
        deployer=MagicMock(),
        r_counter=MagicMock(),
        workspace_base=Path(tempfile.mkdtemp(prefix="rf804_")),
    )


def _make_plan(gap_type="module_bug", code_changes=None):
    from aria_service.autonomous.self_coder import FixPlan
    return FixPlan(
        fix_id="ff" * 6,
        gap_id="gg" * 8,
        gap_type=gap_type,
        r_number=802,
        title="test fix title",
        description="test description",
        target_files=["aria_service/intel/knowledge.py"],
        approach="test approach",
        code_changes=code_changes or {
            "aria_service/intel/knowledge.py": "# patched\n",
        },
    )


class TestStageOrDeploy:
    def test_calls_stage_improvement_per_file(self) -> None:
        """Each file in plan.code_changes must trigger one
        stage_improvement call with the right args."""
        coder = _make_coder()
        plan = _make_plan(code_changes={
            "aria_service/intel/knowledge.py": "# a\n",
            "aria_service/intel/contacts.py": "# b\n",
        })
        with patch(
            "aria_service.intel.self_improve.stage_improvement",
            new_callable=AsyncMock,
        ) as mock_stage, patch(
            "aria_service.intel.self_improve.CHANGE_TYPES",
            {"bug_fix": {"auto_deploy": False, "description": "x"}},
        ):
            mock_stage.side_effect = [
                {"staged": True, "id": "id_a", "auto_deployable": False},
                {"staged": True, "id": "id_b", "auto_deployable": False},
            ]

            async def body():
                ok, status, ids = await coder._stage_or_deploy(plan, "bug_fix")
                return ok, status, ids

            ok, status, ids = _run(body())

            assert ok
            assert status == "staged_for_operator"
            assert ids == ["id_a", "id_b"]
            assert mock_stage.call_count == 2
            # Check the args of the first call
            first_call = mock_stage.call_args_list[0]
            kwargs = first_call.kwargs
            assert kwargs["change_type"] == "bug_fix"
            assert kwargs["file_path"] == "aria_service/intel/knowledge.py"
            assert kwargs["new_content"] == "# a\n"
            assert "test fix title" == kwargs["description"]

    def test_stage_failure_propagates(self) -> None:
        """A single stage_improvement failure must abort and return
        False — never silently lose a planned change."""
        coder = _make_coder()
        plan = _make_plan()
        with patch(
            "aria_service.intel.self_improve.stage_improvement",
            new_callable=AsyncMock,
        ) as mock_stage:
            mock_stage.return_value = {
                "staged": False,
                "error": "schema validation failed",
            }

            async def body():
                return await coder._stage_or_deploy(plan, "bug_fix")

            ok, status, ids = _run(body())
            assert not ok
            assert "stage_failed" in status
            assert "schema validation failed" in status

    def test_auto_deploy_when_r_f462_gate_open_and_gold_lane_earned(self) -> None:
        """When CHANGE_TYPES[change_type]["auto_deploy"] is True (i.e.
        ARIA_SELF_IMPROVE_AUTO_DEPLOY=1 at module import) AND the R-F2689
        gold-lane maturity gate is earned, auto-deploy each staged item via
        self_improve.deploy_improvement."""
        coder = _make_coder()
        plan = _make_plan()
        with patch(
            "aria_service.intel.self_improve.stage_improvement",
            new_callable=AsyncMock,
        ) as mock_stage, patch(
            "aria_service.intel.self_improve.deploy_improvement",
            new_callable=AsyncMock,
        ) as mock_deploy, patch(
            "aria_service.intel.self_improve.CHANGE_TYPES",
            {"bug_fix": {"auto_deploy": True, "description": "x"}},
        ):
            mock_stage.return_value = {
                "staged": True, "id": "id_x", "auto_deployable": True,
            }
            mock_deploy.return_value = {"ok": True}

            async def body():
                return await coder._stage_or_deploy(
                    plan, "bug_fix", gold_lane={"allowed": True, "reasons": []}
                )

            ok, status, ids = _run(body())
            assert ok
            assert status == "auto_deployed"
            assert ids == ["id_x"]
            mock_deploy.assert_called_once_with("id_x")

    def test_r_f462_gate_closed_keeps_staged(self) -> None:
        """CAPABILITY TEST: this is the R-F462 protection. When
        ARIA_SELF_IMPROVE_AUTO_DEPLOY is unset (default — gate CLOSED),
        even a bug_fix change must NOT auto-deploy. The whole point of
        R-F462 is that ARIA's autonomous code passes through operator
        review unless this env var is explicitly set."""
        coder = _make_coder()
        plan = _make_plan()
        with patch(
            "aria_service.intel.self_improve.stage_improvement",
            new_callable=AsyncMock,
        ) as mock_stage, patch(
            "aria_service.intel.self_improve.deploy_improvement",
            new_callable=AsyncMock,
        ) as mock_deploy, patch(
            "aria_service.intel.self_improve.CHANGE_TYPES",
            # R-F462 default: auto_deploy False for bug_fix
            {"bug_fix": {"auto_deploy": False, "description": "x"}},
        ):
            mock_stage.return_value = {
                "staged": True, "id": "id_y", "auto_deployable": False,
            }

            async def body():
                return await coder._stage_or_deploy(plan, "bug_fix")

            ok, status, ids = _run(body())
            assert ok
            assert status == "staged_for_operator"
            # The critical assertion — deploy_improvement was NOT called
            mock_deploy.assert_not_called()

    def test_enhancement_never_auto_deploys(self) -> None:
        """enhancement is always operator-approval per self_improve.
        Verify the integration doesn't accidentally promote it."""
        coder = _make_coder()
        plan = _make_plan(gap_type="data_gap")
        with patch(
            "aria_service.intel.self_improve.stage_improvement",
            new_callable=AsyncMock,
        ) as mock_stage, patch(
            "aria_service.intel.self_improve.deploy_improvement",
            new_callable=AsyncMock,
        ) as mock_deploy, patch(
            "aria_service.intel.self_improve.CHANGE_TYPES",
            {"enhancement": {"auto_deploy": False, "description": "x"}},
        ):
            mock_stage.return_value = {
                "staged": True, "id": "id_z", "auto_deployable": False,
            }

            async def body():
                return await coder._stage_or_deploy(plan, "enhancement")

            ok, status, ids = _run(body())
            assert ok
            assert status == "staged_for_operator"
            mock_deploy.assert_not_called()

    def test_deploy_failure_returns_error(self) -> None:
        """If auto-deploy fails after staging, return False with the
        error. The staged items still exist for operator to investigate."""
        coder = _make_coder()
        plan = _make_plan()
        with patch(
            "aria_service.intel.self_improve.stage_improvement",
            new_callable=AsyncMock,
        ) as mock_stage, patch(
            "aria_service.intel.self_improve.deploy_improvement",
            new_callable=AsyncMock,
        ) as mock_deploy, patch(
            "aria_service.intel.self_improve.CHANGE_TYPES",
            {"bug_fix": {"auto_deploy": True, "description": "x"}},
        ):
            mock_stage.return_value = {
                "staged": True, "id": "id_w", "auto_deployable": True,
            }
            mock_deploy.return_value = {"ok": False, "error": "git push failed"}

            async def body():
                return await coder._stage_or_deploy(
                    plan, "bug_fix", gold_lane={"allowed": True, "reasons": []}
                )

            ok, status, ids = _run(body())
            assert not ok
            assert "deploy_failed" in status
            assert "git push failed" in status


# ════════════════════════════════════════════════════════════════════════════
# Safety guardrail integration
# ════════════════════════════════════════════════════════════════════════════

class TestSafetyGate:
    def test_fix_gap_blocked_when_safety_refuses(self) -> None:
        """CAPABILITY TEST: when safety.can_task_run returns False (engine
        paused, rate-limited, dedupe, etc.), fix_gap must return a
        FixResult(success=False) WITHOUT burning any LLM tokens.

        This is critical — the user's cost guardrail and operator-pause
        depend on this gate firing BEFORE any expensive work."""
        from aria_service.autonomous.gap_detector import (
            Gap, GapSeverity, GapType,
        )
        coder = _make_coder()
        # Wire the LLM mock so any call would be flagged
        coder.llm.generate_fix_plan = AsyncMock(
            return_value={"title": "should not be called"}
        )
        gap = Gap(
            gap_id="blocked", gap_type=GapType.MODULE_BUG,
            severity=GapSeverity.HIGH,
            title="t", description="d", module="aria_service/intel/x.py",
        )

        with patch(
            "aria_service.autonomous.safety.can_task_run",
            new_callable=AsyncMock,
        ) as mock_safety:
            mock_safety.return_value = (False, "engine_paused")

            async def body():
                return await coder.fix_gap(gap)

            result = _run(body())

            assert not result.success
            assert "Safety guardrail" in result.failure_reason
            assert "engine_paused" in result.failure_reason
            # The critical assertion — LLM was NEVER called
            coder.llm.generate_fix_plan.assert_not_called()
            # And safety was consulted with the right key
            mock_safety.assert_called_once()
            kwargs = mock_safety.call_args.kwargs
            assert kwargs["task_id"] == "aria_coder_fix"
            assert kwargs["entity"] == "blocked"

    def test_fix_gap_safety_import_failure_does_not_block(self) -> None:
        """If safety module raises (unlikely but defensive), the coder
        logs and proceeds — never block forever on a guardrail bug."""
        from aria_service.autonomous.gap_detector import (
            Gap, GapSeverity, GapType,
        )
        coder = _make_coder()
        # Make the codebase + LLM async-aware so the flow can reach
        # generate_fix_plan past the safety check
        coder.codebase.get_context = AsyncMock(return_value="")
        coder.llm.generate_fix_plan = AsyncMock(
            side_effect=RuntimeError("llm called intentionally"),
        )
        gap = Gap(
            gap_id="x", gap_type=GapType.MODULE_BUG,
            severity=GapSeverity.HIGH,
            title="t", description="d", module="x",
        )

        with patch(
            "aria_service.autonomous.safety.can_task_run",
            new_callable=AsyncMock,
        ) as mock_safety:
            mock_safety.side_effect = RuntimeError("safety boom")

            async def body():
                return await coder.fix_gap(gap)

            result = _run(body())

            # Proceeded past safety check; LLM was reached
            coder.llm.generate_fix_plan.assert_called_once()
            # Returned a non-safety failure (the LLM error)
            assert not result.success
            assert "llm called intentionally" in result.failure_reason


# ════════════════════════════════════════════════════════════════════════════
# test_runner default flipped to OFF (R-F804)
# ════════════════════════════════════════════════════════════════════════════

class TestRunnerDisabledByDefault:
    def test_default_skips_when_env_unset(self, monkeypatch) -> None:
        """R-F804: until R-F805 ships aria-runner, isolated tests must
        default OFF to avoid wedging aria-intel (the brain_hook circuit-
        breaker wedge class R-F795/R-F799/R-F807 just defended against)."""
        from aria_service.autonomous.test_runner import TestRunner
        monkeypatch.delenv("ARIA_CODER_TESTS_ENABLED", raising=False)
        runner = TestRunner(redis_client=MagicMock())

        from pathlib import Path
        import tempfile
        ws = Path(tempfile.mkdtemp(prefix="rf804_runner_"))

        async def body():
            return await runner.run_isolated(ws, {})

        result = _run(body())
        # Skipped without running pytest → all_green=True, 0 passed
        assert result.all_green
        assert result.passed == 0
        assert result.failed == 0

    def test_opt_in_with_env_one(self, monkeypatch) -> None:
        """Operator can re-enable when aria-runner is ready by setting
        ARIA_CODER_TESTS_ENABLED=1. Since we can't actually run pytest
        here, just verify the early-return branch is skipped."""
        from aria_service.autonomous.test_runner import TestRunner
        monkeypatch.setenv("ARIA_CODER_TESTS_ENABLED", "1")
        runner = TestRunner(redis_client=MagicMock())

        from pathlib import Path
        import tempfile
        ws = Path(tempfile.mkdtemp(prefix="rf804_runner_"))

        # The real run_isolated will try subprocess pytest — patch
        # _run_sync to a stub so we can assert it WAS reached.
        called = {"hit": False}

        def fake_run_sync(workspace, new_tests, tests_dir, full_suite=False):
            called["hit"] = True
            from aria_service.autonomous.test_runner import TestResult
            return TestResult(all_green=True, passed=42)

        runner._run_sync = fake_run_sync

        async def body():
            return await runner.run_isolated(ws, {})

        result = _run(body())
        assert called["hit"], "run_sync should have been called when env=1"
        assert result.passed == 42

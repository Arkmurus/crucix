"""R-F2399 — reasoning-contract foundation hardening.

High-stakes/current intelligence questions must prefer fresh grounded evidence
over cached prose, and autonomous code fixes must stop when generated code calls
unverified local APIs.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


def _run(coro):
    return asyncio.run(coro)


class TestGroundedFirstRouting:
    def test_high_stakes_query_uses_grounded_reasoner_before_library(
        self, monkeypatch,
    ) -> None:
        from aria_service.intel import grounded_reasoner, local_brain
        from aria_service.intel import reasoning_library, reasoning_router
        from aria_service.intel import symbolic_reasoner

        library_called = False

        async def _library_match(_question, threshold):
            nonlocal library_called
            library_called = True
            return {
                "match": True,
                "score": 0.99,
                "case": {"response": "cached stale answer"},
            }

        async def _grounded_answer(_question):
            claim = SimpleNamespace(grounded=True, confidence=0.91)
            return SimpleNamespace(
                abstained=False,
                answer="fresh grounded answer",
                claims=[claim],
                duration_ms=12,
            )

        monkeypatch.setattr(
            symbolic_reasoner,
            "reason",
            lambda *_args, **_kwargs: {"confident": False, "confidence": 0},
        )
        monkeypatch.setattr(reasoning_library, "find_match", _library_match)
        monkeypatch.setattr(
            local_brain,
            "try_local_response",
            AsyncMock(return_value={"answered": False}),
        )
        monkeypatch.setattr(grounded_reasoner, "reason", _grounded_answer)
        monkeypatch.setattr(
            reasoning_router,
            "_check_ollama_reasoning",
            AsyncMock(return_value=None),
        )

        result = _run(reasoning_router.try_local_reasoning(
            "latest sanctions status for ACME Ltd",
        ))

        assert result["answered"] is True
        assert result["source"] == "grounded_reasoner"
        assert result["response"] == "fresh grounded answer"
        assert library_called is False
        trace = result["trace"]
        stages = [entry.get("stage") for entry in trace]
        assert "reasoning_library" in stages
        deferred = next(
            entry for entry in trace if entry.get("stage") == "reasoning_library"
        )
        assert deferred.get("deferred") is True
        assert deferred.get("reason") == "grounded_first_contract"

    def test_high_stakes_query_can_fall_back_to_library_after_grounded_abstains(
        self, monkeypatch,
    ) -> None:
        from aria_service.intel import grounded_reasoner, local_brain
        from aria_service.intel import reasoning_library, reasoning_router
        from aria_service.intel import symbolic_reasoner

        async def _library_match(_question, threshold):
            return {
                "match": True,
                "score": 0.98,
                "method": "exact",
                "case": {
                    "id": "case-rf2399",
                    "response": "cached methodology answer",
                    "source_brain": "llm",
                    "confidence_tag": "medium",
                    "access_count": 0,
                    "intent": "methodology",
                },
            }

        async def _grounded_abstains(_question):
            return SimpleNamespace(
                abstained=True,
                answer="",
                claims=[],
                duration_ms=9,
            )

        monkeypatch.setattr(
            symbolic_reasoner,
            "reason",
            lambda *_args, **_kwargs: {"confident": False, "confidence": 0},
        )
        monkeypatch.setattr(reasoning_library, "find_match", _library_match)
        monkeypatch.setattr(
            local_brain,
            "try_local_response",
            AsyncMock(return_value={"answered": False}),
        )
        monkeypatch.setattr(grounded_reasoner, "reason", _grounded_abstains)
        monkeypatch.setattr(
            reasoning_router,
            "_check_ollama_reasoning",
            AsyncMock(return_value=None),
        )

        result = _run(reasoning_router.try_local_reasoning(
            "latest procurement risk review for ACME Ltd",
        ))

        assert result["answered"] is True
        assert result["source"] == "reasoning_library"
        assert result["grounded_first_deferred"] is True
        stages = [entry.get("stage") for entry in result["trace"]]
        assert stages.index("grounded_reasoner") < len(stages) - 1
        assert stages[-1] == "reasoning_library"


class TestAutonomousCoderReasoningContract:
    def test_fix_gap_blocks_hallucinated_api_before_tests_or_staging(
        self, tmp_path,
    ) -> None:
        from aria_service.autonomous.gap_detector import Gap, GapSeverity, GapType
        from aria_service.autonomous.self_coder import ARIACoder

        target = tmp_path / "target_module.py"
        existing = (
            "class LocalWorker:\n"
            "    def existing(self):\n"
            "        return 1\n"
        )
        proposed = (
            "class LocalWorker:\n"
            "    def existing(self):\n"
            "        return 1\n\n"
            "worker = LocalWorker()\n"
            "worker.missing_contract_method()\n"
        )
        target.write_text(existing, encoding="utf-8")

        gap = Gap(
            gap_id="rf2399_hallucinated_api",
            gap_type=GapType.MODULE_BUG,
            severity=GapSeverity.HIGH,
            title="Generated code calls missing local method",
            description="The autonomous coder must not continue past this gate.",
            module=str(target),
        )

        redis = MagicMock()
        redis.get = AsyncMock(return_value=None)
        redis.setex = AsyncMock(return_value=None)
        redis.lpush = AsyncMock(return_value=None)
        redis.ltrim = AsyncMock(return_value=None)

        llm = MagicMock()
        llm.generate_fix_plan = AsyncMock(return_value={
            "title": "Block hallucinated API",
            "approach": "Create a call that the API gate must reject",
            "target_files": [str(target)],
            "new_files": [],
            "risk_level": "low",
        })
        llm.write_code = AsyncMock(return_value={
            "code": proposed,
        })
        llm.write_tests = AsyncMock(return_value={
            "test_code": "def test_should_not_be_written():\n    assert False\n",
            "test_filepath": "aria_service/tests/test_should_not_exist.py",
        })

        codebase = MagicMock()
        codebase.get_context = AsyncMock(return_value=existing)
        codebase.read = MagicMock(return_value=existing)
        codebase.write_to_workspace = MagicMock()

        test_runner = MagicMock()
        test_runner.run_isolated = AsyncMock()

        r_counter = MagicMock()
        r_counter.next = AsyncMock(return_value=2399)

        coder = ARIACoder(
            redis_client=redis,
            aria_service_url="http://localhost:8000",
            llm=llm,
            codebase=codebase,
            test_runner=test_runner,
            r_counter=r_counter,
            workspace_base=tmp_path / "workspace",
        )

        with patch(
            "aria_service.autonomous.safety.can_task_run",
            AsyncMock(return_value=(True, "ok")),
        ), patch.object(
            coder,
            "_ground_context_with_rag",
            AsyncMock(return_value=existing),
        ), patch(
            "aria_service.intel.self_improve.stage_improvement",
            AsyncMock(),
        ) as stage_improvement:
            result = _run(coder.fix_gap(gap, operator_initiated=True))

        assert result.success is False
        assert result.failure_reason is not None
        assert "Hallucinated-API gate blocked" in result.failure_reason
        assert "missing_contract_method" in result.failure_reason
        llm.write_tests.assert_not_called()
        test_runner.run_isolated.assert_not_called()
        stage_improvement.assert_not_called()
        written_paths = [
            Path(call.args[1]).name for call in codebase.write_to_workspace.mock_calls
            if len(call.args) >= 2
        ]
        assert "test_should_not_exist.py" not in written_paths

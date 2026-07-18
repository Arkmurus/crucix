"""R-F2689 — Autonomous coder gold-lane deployment gate.

Live review found ARIA-Coder enabled with zero fixed/gold outcomes and a large
blocked history. These tests lock the structural response: autonomous coding may
stage reviewable fixes, but direct deploy requires scoreboard evidence.
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


def _run(coro):
    return asyncio.run(coro)


def _make_coder():
    from aria_service.autonomous.self_coder import ARIACoder

    return ARIACoder(
        redis_client=MagicMock(),
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
        workspace_base=Path(tempfile.mkdtemp(prefix="rf2689_")),
    )


def _make_plan():
    from aria_service.autonomous.self_coder import FixPlan

    return FixPlan(
        fix_id="rf2689",
        gap_id="gap2689",
        gap_type="module_bug",
        r_number=2689,
        title="gold lane gate",
        description="prove autonomous direct deploy is earned",
        target_files=["aria_service/intel/knowledge.py"],
        code_changes={"aria_service/intel/knowledge.py": "# patched\n"},
    )


def test_live_like_scoreboard_does_not_earn_gold_lane():
    from aria_service.autonomous.self_coder import autonomous_gold_lane_decision

    decision = autonomous_gold_lane_decision({
        "counts": {"blocked": 170, "claimed": 169, "fixed": 0, "staged": 0, "gold": 0}
    })

    assert decision["allowed"] is False
    assert "fixed 0 < 20" in decision["reasons"]
    assert "gold 0 < 10" in decision["reasons"]
    assert decision["blocked_ratio"] > decision["thresholds"]["max_blocked_ratio"]


def test_mature_scoreboard_earns_gold_lane():
    from aria_service.autonomous.self_coder import autonomous_gold_lane_decision

    decision = autonomous_gold_lane_decision({
        "counts": {"blocked": 5, "claimed": 100, "fixed": 80, "staged": 20, "gold": 15}
    })

    assert decision["allowed"] is True
    assert decision["reasons"] == []


def test_gold_lane_env_parsers_fail_closed():
    from aria_service.autonomous.self_coder import (
        _gold_lane_float_env,
        _gold_lane_int_env,
    )

    with patch.dict(
        "os.environ",
        {
            "ARIA_CODER_GOLD_LANE_MIN_FIXED": "not-an-int",
            "ARIA_CODER_GOLD_LANE_MAX_BLOCKED_RATIO": "-1",
        },
    ):
        assert _gold_lane_int_env("ARIA_CODER_GOLD_LANE_MIN_FIXED", 20) == 20
        assert _gold_lane_float_env("ARIA_CODER_GOLD_LANE_MAX_BLOCKED_RATIO", 0.25) == 0.25


def test_coder_stages_auto_deployable_fix_when_gold_lane_not_earned():
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
            "staged": True,
            "id": "id_gold_blocked",
            "auto_deployable": True,
        }

        ok, status, ids = _run(coder._stage_or_deploy(
            plan,
            "bug_fix",
            gold_lane={
                "allowed": False,
                "reasons": ["fixed 0 < 20", "gold 0 < 10"],
            },
        ))

    assert ok is True
    assert status == "staged_for_operator"
    assert ids == ["id_gold_blocked"]
    mock_deploy.assert_not_called()


def test_coder_deploys_only_after_gold_lane_is_earned():
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
            "staged": True,
            "id": "id_gold_allowed",
            "auto_deployable": True,
        }
        mock_deploy.return_value = {"ok": True}

        ok, status, ids = _run(coder._stage_or_deploy(
            plan,
            "bug_fix",
            gold_lane={"allowed": True, "reasons": []},
        ))

    assert ok is True
    assert status == "auto_deployed"
    assert ids == ["id_gold_allowed"]
    mock_deploy.assert_called_once_with("id_gold_allowed")


def test_self_improve_cycle_stages_auto_deployable_fix_when_gold_lane_not_earned(monkeypatch):
    from aria_service.intel import self_improve as si

    async def fake_recent_errors(hours: int = 24):
        return [
            {"file": "aria_service/intel/knowledge.py", "message": "boom"},
            {"file": "aria_service/intel/knowledge.py", "message": "boom"},
            {"file": "aria_service/intel/knowledge.py", "message": "boom"},
        ]

    async def fake_diagnose(llm, file_path, file_errors):
        return {
            "fixed_code": "x = 1\n",
            "description": "fix knowledge",
            "reasoning": "reproduced failure",
        }

    async def fake_stage(*args, **kwargs):
        return {"staged": True, "id": "si_gold_blocked", "auto_deployable": True}

    async def fake_gold_lane():
        return {"allowed": False, "reasons": ["gold 0 < 10"]}

    deploy = AsyncMock(return_value={"ok": True})

    monkeypatch.setattr(si, "get_recent_errors", fake_recent_errors)
    monkeypatch.setattr(si, "_diagnose_and_fix", fake_diagnose)
    monkeypatch.setattr(si, "stage_improvement", fake_stage)
    monkeypatch.setattr(si, "_autonomous_gold_lane_allows_deploy", fake_gold_lane)
    monkeypatch.setattr(si, "deploy_improvement", deploy)
    monkeypatch.setattr(si, "MODIFIABLE_FILES", set(si.MODIFIABLE_FILES) | {"aria_service/intel/knowledge.py"})

    result = _run(si.autonomous_improvement_cycle(SimpleNamespace(is_configured=True)))

    assert result["bugs_detected"] == 1
    assert result["improvements_staged"] == 1
    assert result["auto_deployed"] == 0
    deploy.assert_not_called()


def test_self_improve_gold_lane_scoreboard_read_failure_denies_and_wires(monkeypatch):
    from aria_service.intel import self_improve as si

    class BrokenRedis:
        async def get_json(self, key):
            raise RuntimeError("redis down")

    wire = MagicMock()
    monkeypatch.setattr(si, "rs", BrokenRedis())
    monkeypatch.setattr(si, "wire_failure", wire)

    decision = _run(si._autonomous_gold_lane_allows_deploy())

    assert decision["allowed"] is False
    assert "fixed 0 < 20" in decision["reasons"]
    wire.assert_called_once()
    assert wire.call_args.kwargs["gap_type"] == "autonomous_gold_lane_unavailable"

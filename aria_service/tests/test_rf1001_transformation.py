"""R-F1001 — Tests for LLM Builder, Self Healing, and all new modules."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.fixture(autouse=True)
def _isolate_llm_builder_root(tmp_path_factory, monkeypatch):
    """R-F3291 - keep LLMBuilder off the repository's real data/training dir.

    LLMBuilder.__init__ hardcoded its root to the repo, and prepare_training_config
    writes data/training/training_config.json. So every run of this file REWROTE
    the real config, replacing output_dir with whatever tree the tests executed in:

        -  "output_dir": "C:\\\\code\\\\crucix\\\\data\\\\training\\\\checkpoints"
        +  "output_dir": "C:\\\\tmp\\\\crucix-ddfix\\\\data\\\\training\\\\checkpoints"

    Committing that would point the real training config at a temporary worktree.
    It also dirtied the working tree on every run, which weakens "git status is
    clean" as a deploy-safety signal.

    autouse so a test added later inherits the isolation rather than having to
    remember it. R-F3291 added the `root` seam that makes this possible.
    """
    from aria_service.intel import llm_builder as _lb
    tmp = tmp_path_factory.mktemp("llm_builder_root")
    _orig_init = _lb.LLMBuilder.__init__

    def _patched(self, root=None):
        _orig_init(self, root=root or tmp)

    monkeypatch.setattr(_lb.LLMBuilder, "__init__", _patched)


class TestLLMBuilder:
    """Test the LLM Builder."""

    @pytest.mark.asyncio
    async def test_curate_dataset(self):
        """curate_dataset should return a dataset dict."""
        from aria_service.intel.llm_builder import LLMBuilder
        builder = LLMBuilder()
        with patch("aria_service.intel.chat_audit_log.get_recent", AsyncMock(return_value=[])):
            result = await builder.curate_dataset()
        assert "total_pairs" in result
        assert "sources" in result
        assert "dataset_path" in result

    @pytest.mark.asyncio
    async def test_prepare_training_config(self):
        """prepare_training_config should return a config dict."""
        from aria_service.intel.llm_builder import LLMBuilder
        builder = LLMBuilder()
        config = await builder.prepare_training_config()
        assert "model_name" in config
        assert "method" in config
        assert config["method"] == "qlora"

    @pytest.mark.asyncio
    async def test_generate_training_script(self):
        """generate_training_script should return a string."""
        from aria_service.intel.llm_builder import LLMBuilder
        builder = LLMBuilder()
        script = await builder.generate_training_script()
        assert "SFTTrainer" in script
        assert "trainer.train()" in script

    @pytest.mark.asyncio
    async def test_full_build_cycle(self):
        """full_build_cycle should return a complete result."""
        from aria_service.intel.llm_builder import LLMBuilder
        builder = LLMBuilder()
        with patch.multiple(
            builder,
            curate_dataset=AsyncMock(return_value={"total_pairs": 100, "sources": {"test": 100}, "dataset_path": "/tmp/test.json"}),
            prepare_training_config=AsyncMock(return_value={"model_name": "test", "method": "qlora"}),
            generate_training_script=AsyncMock(return_value="print('test')"),
        ):
            result = await builder.full_build_cycle()
        assert "dataset" in result
        assert "config" in result
        assert "script_path" in result
        assert result["status"] == "ready_for_training"


class TestSelfHealer:
    """Test the Self Healing system.

    R-F2801: these previously imported `SelfHealer` with `check_health()` /
    `auto_heal()`. That class was real — R-F1001 (e5bb8a4d) added it — but
    R-F1051 (e07ecc37) replaced it with the current two-part design:

        HealthMonitor.check_all()          -> dict[str, HealthCheck]
        EcosystemSelfRepair.check_and_repair() -> summary dict

    So the tests died on ImportError and had been asserting an API that no longer
    exists. Rewritten against the REAL contract per §23 — this is strictly
    stronger than the old version, which only checked that two keys were present
    in a dict; it now asserts the actual repair-summary shape and that an
    unhealthy subsystem triggers a recovery attempt.
    """

    @pytest.mark.asyncio
    async def test_check_all_reports_every_subsystem_status(self):
        """HealthMonitor must report a typed HealthCheck per subsystem."""
        from aria_service.intel.self_healing import HealthCheck, HealthMonitor, HealthStatus

        monitor = HealthMonitor()
        with patch.object(
            monitor, "check_subsystem",
            AsyncMock(side_effect=lambda name, url, timeout=10.0: HealthCheck(
                subsystem=name, status=HealthStatus.HEALTHY, latency_ms=1.0,
            )),
        ):
            result = await monitor.check_all()

        assert isinstance(result, dict) and result, "check_all must report subsystems"
        for name, check in result.items():
            assert isinstance(check, HealthCheck), f"{name} must be a typed HealthCheck"
            assert check.subsystem, "every check names its subsystem"
            assert check.is_healthy() is True

    @pytest.mark.asyncio
    async def test_check_and_repair_summary_shape_when_all_healthy(self):
        """A fully healthy ecosystem attempts NO repairs and says so."""
        from aria_service.intel.self_healing import (
            AutoRecoveryEngine, CircuitBreakerManager, EcosystemSelfRepair,
            HealthCheck, HealthMonitor, HealthStatus,
        )

        # EcosystemSelfRepair takes its collaborators by injection — construct the
        # real ones so this exercises the production wiring, not a stand-in.
        repair = EcosystemSelfRepair(HealthMonitor(), AutoRecoveryEngine(CircuitBreakerManager()))
        healthy = {
            "redis": HealthCheck(subsystem="redis", status=HealthStatus.HEALTHY),
            "aria_intel": HealthCheck(subsystem="aria_intel", status=HealthStatus.HEALTHY),
        }
        with patch.object(repair._health, "check_all", AsyncMock(return_value=healthy)):
            summary = await repair.check_and_repair()

        assert summary["total_checks"] == 2
        assert summary["healthy"] == 2
        assert summary["critical"] == 0
        assert summary["repairs_attempted"] == 0, "nothing to repair when all healthy"
        assert summary["repairs"] == []
        assert "contract_violations" in summary

    @pytest.mark.asyncio
    async def test_check_and_repair_attempts_recovery_on_a_critical_subsystem(self):
        """The capability that matters: an unhealthy subsystem gets a recovery attempt."""
        from aria_service.intel.self_healing import (
            AutoRecoveryEngine, CircuitBreakerManager, EcosystemSelfRepair,
            HealthCheck, HealthMonitor, HealthStatus,
        )

        repair = EcosystemSelfRepair(HealthMonitor(), AutoRecoveryEngine(CircuitBreakerManager()))
        checks = {
            "redis": HealthCheck(subsystem="redis", status=HealthStatus.HEALTHY),
            "aria_intel": HealthCheck(
                subsystem="aria_intel", status=HealthStatus.CRITICAL, error="connection refused",
            ),
        }
        with patch.object(repair._health, "check_all", AsyncMock(return_value=checks)), \
             patch.object(repair._recovery, "attempt_recovery",
                          AsyncMock(return_value={"ok": True})) as attempt:
            summary = await repair.check_and_repair()

        attempt.assert_awaited_once()
        assert attempt.await_args.args[0] == "aria_intel", "must recover the FAILING subsystem"
        assert summary["critical"] == 1
        assert summary["repairs_attempted"] == 1
        assert summary["repairs"][0]["subsystem"] == "aria_intel"
        assert summary["repairs"][0]["error"] == "connection refused"


class TestWiringCoverage:
    """Verify wiring coverage."""

    def test_wiring_coverage_above_90_percent(self):
        """At least 90% of intel modules should have brain wiring tokens."""
        import pathlib
        intel_dir = pathlib.Path(__file__).parent.parent / "intel"
        tokens = {"brain_hook.absorb", "capability_gaps.record_gap",
                  "mistake_ledger.record", "record_error", "record_gap",
                  "wire_success", "wire_failure"}
        total = 0
        wired = 0
        for f in sorted(intel_dir.glob("*.py")):
            if f.name.startswith("__"):
                continue
            total += 1
            content = f.read_text(encoding="utf-8", errors="replace")
            if any(t in content for t in tokens):
                wired += 1
        pct = round(100 * wired / total, 1)
        assert pct >= 90, f"Only {pct}% wired ({wired}/{total})"

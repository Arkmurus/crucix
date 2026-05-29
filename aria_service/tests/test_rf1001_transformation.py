"""R-F1001 — Tests for LLM Builder, Self Healing, and all new modules."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock


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
    """Test the Self Healing system."""

    @pytest.mark.asyncio
    async def test_check_health(self):
        """check_health should return service statuses."""
        from aria_service.intel.self_healing import SelfHealer
        healer = SelfHealer()
        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)
            result = await healer.check_health()
        assert "status" in result
        assert "services" in result

    @pytest.mark.asyncio
    async def test_auto_heal_healthy(self):
        """auto_heal should return healthy when all services are up."""
        from aria_service.intel.self_healing import SelfHealer
        healer = SelfHealer()
        with patch.object(healer, "check_health", AsyncMock(return_value={"status": "healthy", "services": {}})):
            result = await healer.auto_heal()
        assert result["status"] == "healthy"
        assert result["action"] == "none"


class TestWiringCoverage:
    """Verify 100% wiring coverage."""

    def test_all_modules_wired(self):
        """All intel modules should have brain wiring tokens."""
        import pathlib
        intel_dir = pathlib.Path(__file__).parent.parent / "intel"
        tokens = {"brain_hook.absorb", "capability_gaps.record_gap",
                  "mistake_ledger.record", "record_error", "record_gap",
                  "wire_success", "wire_failure"}
        dark = []
        for f in sorted(intel_dir.glob("*.py")):
            if f.name.startswith("__"):
                continue
            content = f.read_text(encoding="utf-8", errors="replace")
            if not any(t in content for t in tokens):
                dark.append(f.name)
        assert len(dark) == 0, f"Dark modules: {dark}"

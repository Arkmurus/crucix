"""R-F1011 — Tests for LLM Pipeline, Public API, Product Pages."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.fixture(autouse=True)
def _isolate_llm_pipeline_root(tmp_path_factory, monkeypatch):
    """R-F3346 — keep LLMTrainingPipeline off the repository's real data/training.

    The mirror of R-F3291's fixture in test_rf1001_transformation, for the class
    that never got the seam. This file calls _generate_training_script() and
    full_pipeline() directly, and both write into data/training:

        train_aria_llm.py     <- a hardcoded 74-line template that OVERWRITES
                                 R-F1941's curated 155-line grounded trainer
        training_config.json  <- loses dataset_file / dataset_format
        dataset_<ts>.json     <- litters the tree

    Measured: the trainer was committed restored at defdf2e6, and one full
    `pytest aria_service/tests/` run later it was the template again. That is
    also what really happened in June — commit 6fe94c43 did not revert the
    trainer, it committed a tree this generator had already clobbered.

    autouse so a test added later inherits the isolation rather than having to
    remember it.
    """
    from aria_service.intel import llm_pipeline as _lp
    tmp = tmp_path_factory.mktemp("llm_pipeline_root")
    _orig_init = _lp.LLMTrainingPipeline.__init__

    def _patched(self, root=None):
        _orig_init(self, root=root or tmp)

    monkeypatch.setattr(_lp.LLMTrainingPipeline, "__init__", _patched)


class TestLLMTrainingPipeline:
    """Test the LLM training pipeline."""

    @pytest.mark.asyncio
    async def test_curate_dataset(self):
        """_curate_dataset should return dataset stats."""
        from aria_service.intel.llm_pipeline import LLMTrainingPipeline
        pipeline = LLMTrainingPipeline()
        with patch("aria_service.intel.chat_audit_log.get_recent", AsyncMock(return_value=[])):
            result = await pipeline._curate_dataset()
        assert "total_pairs" in result
        assert "sources" in result
        assert "dataset_path" in result

    def test_prepare_config(self):
        """_prepare_config should return config dict."""
        from aria_service.intel.llm_pipeline import LLMTrainingPipeline
        pipeline = LLMTrainingPipeline()
        config = pipeline._prepare_config("mistralai/Mistral-7B-Instruct-v0.3", 1000)
        assert config["method"] == "qlora"
        assert config["lora_r"] == 16
        assert config["num_epochs"] > 0

    def test_generate_training_script(self):
        """_generate_training_script should return a script."""
        from aria_service.intel.llm_pipeline import LLMTrainingPipeline
        pipeline = LLMTrainingPipeline()
        config = pipeline._prepare_config("test-model", 100)
        script = pipeline._generate_training_script("test-model", config)
        assert "SFTTrainer" in script
        assert "trainer.train()" in script

    @pytest.mark.asyncio
    async def test_full_pipeline(self):
        """full_pipeline should return complete results."""
        from aria_service.intel.llm_pipeline import LLMTrainingPipeline
        pipeline = LLMTrainingPipeline()
        with patch.object(pipeline, "_curate_dataset", AsyncMock(return_value={"total_pairs": 100, "sources": {"test": 100}, "dataset_path": "/tmp/test.json"})):
            result = await pipeline.full_pipeline()
        assert "model" in result
        assert "steps" in result
        assert "duration_s" in result


class TestPublicAPI:
    """Test the public API documentation."""

    def test_get_api_documentation(self):
        """get_api_documentation should return API docs."""
        from aria_service.intel.public_api import get_api_documentation
        docs = get_api_documentation()
        assert "api_name" in docs
        assert "version" in docs
        assert "endpoints" in docs
        assert len(docs["endpoints"]) >= 5

    def test_get_openapi_spec(self):
        """get_openapi_spec should return OpenAPI spec."""
        from aria_service.intel.public_api import get_openapi_spec
        spec = get_openapi_spec()
        assert spec["openapi"] == "3.0.0"
        assert "info" in spec
        assert "paths" in spec
        assert len(spec["paths"]) >= 5

    def test_api_endpoints_have_auth(self):
        """All sensitive endpoints should require auth."""
        from aria_service.intel.public_api import API_ENDPOINTS
        for name, ep in API_ENDPOINTS.items():
            if name != "health":
                assert ep["auth"] == "required", f"{name} missing auth"


class TestProductPage:
    """Test the product pages."""

    def test_get_model_card(self):
        """get_model_card should return model info."""
        from aria_service.intel.product_page import get_model_card
        card = get_model_card()
        assert "model_name" in card
        assert "capabilities" in card
        assert "security" in card
        assert "limitations" in card

    def test_get_pricing(self):
        """get_pricing should return pricing plans."""
        from aria_service.intel.product_page import get_pricing
        pricing = get_pricing()
        assert "plans" in pricing
        assert "free" in pricing["plans"]
        assert "pro" in pricing["plans"]
        assert "pro_intel" in pricing["plans"]
        assert "enterprise" in pricing["plans"]

    def test_pricing_free_tier(self):
        """Free tier should have basic features."""
        from aria_service.intel.product_page import PRICING_PLANS
        free = PRICING_PLANS["free"]
        assert free["price_monthly"] == 0
        assert free["limits"]["api_calls_per_month"] == 100

    def test_pricing_pro_intel(self):
        """Pro Intel tier should have full features."""
        from aria_service.intel.product_page import PRICING_PLANS
        pro = PRICING_PLANS["pro_intel"]
        assert pro["price_monthly"] == 199
        assert "due diligence" in " ".join(pro["features"]).lower()

    def test_get_adversarial_scoreboard(self):
        """get_adversarial_scoreboard should return scores."""
        from aria_service.intel.product_page import get_adversarial_scoreboard
        board = get_adversarial_scoreboard()
        assert "overall_pass_rate" in board
        assert "categories" in board
        assert len(board["categories"]) >= 3

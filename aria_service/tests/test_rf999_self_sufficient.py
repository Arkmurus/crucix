"""R-F999 — Tests for ARIA self-sufficient architecture."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch


class TestSymbolicReasoner:
    """Test the symbolic reasoning engine."""

    def test_classify_intent_sanctions(self):
        from aria_service.intel.self_sufficient import SymbolicReasoner
        r = SymbolicReasoner()
        assert r._classify_intent("is Ivan Petrov sanctioned?") == "sanctions_check"
        assert r._classify_intent("what are the sanctions on Russia?") == "sanctions_check"

    def test_classify_intent_export(self):
        from aria_service.intel.self_sufficient import SymbolicReasoner
        r = SymbolicReasoner()
        assert r._classify_intent("can we export night vision goggles?") == "export_control"

    def test_classify_intent_risk(self):
        from aria_service.intel.self_sufficient import SymbolicReasoner
        r = SymbolicReasoner()
        assert r._classify_intent("what is the risk in Sudan?") == "risk_assessment"

    def test_classify_intent_screening(self):
        from aria_service.intel.self_sufficient import SymbolicReasoner
        r = SymbolicReasoner()
        assert r._classify_intent("screen this company") == "screening"

    def test_classify_intent_knowledge(self):
        from aria_service.intel.self_sufficient import SymbolicReasoner
        r = SymbolicReasoner()
        assert r._classify_intent("what is the capital of France?") == "knowledge_retrieval"

    def test_decompose_question(self):
        from aria_service.intel.self_sufficient import SymbolicReasoner
        r = SymbolicReasoner(knowledge_base={"ukraine": "Country in Eastern Europe"})
        steps = r.decompose("What is the risk in Ukraine?")
        assert len(steps) >= 2
        assert any(s.type == "premise" for s in steps)
        assert any(s.type == "inference" for s in steps)

    def test_extract_entities(self):
        from aria_service.intel.self_sufficient import SymbolicReasoner
        r = SymbolicReasoner()
        entities = r._extract_entities("Screen Ivan Petrov of Moscow")
        assert "Ivan Petrov" in entities or "Moscow" in entities


class TestCodeGenerator:
    """Test the code generation engine."""

    def test_generate_module(self):
        from aria_service.intel.self_sufficient import CodeGenerator
        g = CodeGenerator()
        code = g.generate_module(
            module_name="test_module",
            function_name="execute_analysis",
            description="Test module",
            summary="Test analysis",
            r_number=999,
        )
        assert "def execute_analysis" in code
        assert "wire_success" in code
        assert "test_module" in code

    def test_generate_test(self):
        from aria_service.intel.self_sufficient import CodeGenerator
        g = CodeGenerator()
        code = g.generate_test(
            module_name="test_module",
            function_name="execute_analysis",
            r_number=999,
        )
        assert "class TestTestModule" in code
        assert "test_execute_analysis_basic" in code

    def test_add_wiring_to_function(self):
        from aria_service.intel.self_sufficient import CodeGenerator
        g = CodeGenerator()
        source = "def my_func():\n    x = 1\n    return x\n"
        result = g.add_wiring_to_function(source, "my_module", "My function")
        assert "wire_success" in result
        assert "my_module" in result


class TestKnowledgeAugmentedResponder:
    """Test the knowledge-augmented responder."""

    @pytest.mark.asyncio
    async def test_answer_with_knowledge(self):
        from aria_service.intel.self_sufficient import KnowledgeAugmentedResponder
        r = KnowledgeAugmentedResponder()
        result = await r.answer("What is the risk in Ukraine?")
        assert "answer" in result
        assert "sources" in result
        assert "reasoning_steps" in result
        assert "confidence" in result
        assert "llm_dependent" in result
        assert result["llm_dependent"] is False

    @pytest.mark.asyncio
    async def test_answer_unknown(self):
        from aria_service.intel.self_sufficient import KnowledgeAugmentedResponder
        r = KnowledgeAugmentedResponder()
        result = await r.answer("What is the quantum computing policy of Burkina Faso?")
        # Should return a response with reasoning steps, not an LLM-dependent answer
        assert result["llm_dependent"] is False
        assert len(result["reasoning_steps"]) > 0


class TestSelfImprovementPipeline:
    """Test the self-improvement pipeline."""

    @pytest.mark.asyncio
    async def test_scan_and_fix_wiring_gaps(self):
        from aria_service.intel.self_sufficient import SelfImprovementPipeline
        p = SelfImprovementPipeline()
        fixes = await p.scan_and_fix_wiring_gaps()
        assert isinstance(fixes, list)
        # Should find some dark modules
        if fixes:
            assert "file" in fixes[0]
            assert "function" in fixes[0]
            assert "content" in fixes[0]


class TestRoadmap:
    """Test the self-sufficiency roadmap."""

    def test_roadmap_defined(self):
        from aria_service.intel.self_sufficient import ROADMAP
        assert len(ROADMAP) >= 5  # At least 5 phases
        for phase_id, phase in ROADMAP.items():
            assert "name" in phase
            assert "description" in phase
            assert "status" in phase

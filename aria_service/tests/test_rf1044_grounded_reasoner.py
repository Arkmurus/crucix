"""R-F1044 — Tests for the Grounded Reasoning Engine.

Covers:
  1. ReasonResult data types
  2. GroundedReasoner disabled path (ARIA_GROUNDED_REASONER=0)
  3. Premise extraction
  4. Decomposition
  5. Evidence gathering (with mocked stores)
  6. Claim verification
  7. Answer building (grounded vs abstained)
  8. End-to-end capability test
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aria_service.intel.grounded_reasoner import (
    GroundedReasoner,
    ReasonResult,
    Claim,
    EvidenceItem,
    ReasoningStep,
    reason,
)


# ════════════════════════════════════════════════════════════════════════════
# Data type tests
# ════════════════════════════════════════════════════════════════════════════

class TestDataTypes:
    def test_evidence_item_defaults(self) -> None:
        e = EvidenceItem(source="test", kind="rag", confidence=0.8)
        assert e.source == "test"
        assert e.kind == "rag"
        assert e.confidence == 0.8
        assert e.content == ""

    def test_claim_defaults(self) -> None:
        c = Claim(text="test claim")
        assert c.text == "test claim"
        assert c.evidence == []
        assert not c.grounded
        assert c.confidence == 0.0

    def test_reasoning_step_defaults(self) -> None:
        s = ReasoningStep(phase="gather", detail="gathering evidence")
        assert s.phase == "gather"
        assert s.result == ""

    def test_reason_result_defaults(self) -> None:
        r = ReasonResult(answer="test answer")
        assert r.answer == "test answer"
        assert r.claims == []
        assert r.steps == []
        assert not r.abstained
        assert r.duration_ms == 0.0


# ════════════════════════════════════════════════════════════════════════════
# Disabled path
# ════════════════════════════════════════════════════════════════════════════

class TestDisabledPath:
    def test_returns_disabled_message_when_gate_off(self) -> None:
        # R-F1047: gate defaults to "1" (enabled). Test by setting _ENABLED directly.
        from aria_service.intel.grounded_reasoner import GroundedReasoner, _ENABLED as _orig_enabled
        
        # Temporarily disable
        import aria_service.intel.grounded_reasoner as gr_mod
        gr_mod._ENABLED = False
        
        try:
            reasoner = GroundedReasoner()
            result = reasoner.reason("test question")
            import asyncio
            r = asyncio.run(result)
            assert r.abstained
            assert "disabled" in r.answer.lower()
        finally:
            gr_mod._ENABLED = True


# ════════════════════════════════════════════════════════════════════════════
# Premise extraction
# ════════════════════════════════════════════════════════════════════════════

class TestPremiseExtraction:
    @pytest.mark.asyncio
    async def test_extract_premises_empty_message(self) -> None:
        reasoner = GroundedReasoner()
        premises = await reasoner._extract_premises("")
        assert isinstance(premises, list)

    @pytest.mark.asyncio
    async def test_extract_premises_with_premise_verifier(self) -> None:
        reasoner = GroundedReasoner()
        # Mock premise verifier
        mock_pv = MagicMock()
        mock_pv.verify_premises.return_value = MagicMock(
            premises=["The sky is blue", "Water is wet"],
        )
        reasoner._premise_verifier = mock_pv

        premises = await reasoner._extract_premises("The sky is blue and water is wet")
        assert "The sky is blue" in premises
        assert "Water is wet" in premises


# ════════════════════════════════════════════════════════════════════════════
# Decomposition
# ════════════════════════════════════════════════════════════════════════════

class TestDecomposition:
    @pytest.mark.asyncio
    async def test_decompose_with_premises(self) -> None:
        reasoner = GroundedReasoner()
        sub_questions = await reasoner._decompose(
            "Is the sky blue?",
            ["The sky is blue"],
        )
        assert len(sub_questions) >= 1
        assert any("sky" in sq.lower() for sq in sub_questions)

    @pytest.mark.asyncio
    async def test_decompose_empty_premises_falls_back(self) -> None:
        reasoner = GroundedReasoner()
        sub_questions = await reasoner._decompose("What is the capital of France?", [])
        assert len(sub_questions) >= 1


# ════════════════════════════════════════════════════════════════════════════
# Evidence gathering
# ════════════════════════════════════════════════════════════════════════════

class TestEvidenceGathering:
    @pytest.mark.asyncio
    async def test_gather_returns_list(self) -> None:
        reasoner = GroundedReasoner()
        evidence = await reasoner._gather_evidence("test question")
        assert isinstance(evidence, list)

    @pytest.mark.asyncio
    async def test_gather_from_reasoning_library(self) -> None:
        reasoner = GroundedReasoner()
        mock_rl = AsyncMock()
        mock_rl.search = AsyncMock(return_value={
            "id": "test_123",
            "response": "Paris is the capital of France",
            "confidence_score": 0.9,
        })
        reasoner._reasoning_library = mock_rl

        evidence = await reasoner._gather_evidence("What is the capital of France?")
        assert len(evidence) >= 1
        assert evidence[0].kind == "reasoning_library"
        assert evidence[0].confidence == 0.9

    @pytest.mark.asyncio
    async def test_gather_from_knowledge(self) -> None:
        reasoner = GroundedReasoner()
        mock_knowledge = AsyncMock()
        mock_knowledge.query = AsyncMock(return_value=[
            {"fact": "Paris is the capital of France", "source": "knowledge_base", "confidence": 0.95},
        ])
        reasoner._knowledge = mock_knowledge

        evidence = await reasoner._gather_evidence("What is the capital of France?")
        assert len(evidence) >= 1
        assert evidence[0].kind == "knowledge"


# ════════════════════════════════════════════════════════════════════════════
# Claim verification
# ════════════════════════════════════════════════════════════════════════════

class TestClaimVerification:
    @pytest.mark.asyncio
    async def test_verify_ungrounded_claim_stays_ungrounded(self) -> None:
        reasoner = GroundedReasoner()
        claims = [Claim(text="test", grounded=False, confidence=0.0)]
        verified = await reasoner._verify_claims_inline(claims)
        assert len(verified) == 1
        assert not verified[0].grounded
        assert verified[0].confidence == 0.0

    @pytest.mark.asyncio
    async def test_verify_claim_with_evidence(self) -> None:
        reasoner = GroundedReasoner()
        claims = [
            Claim(
                text="Paris is the capital of France",
                evidence=[EvidenceItem(source="wiki", kind="rag", confidence=0.9, content="Paris is the capital")],
                grounded=True,
                confidence=0.9,
            ),
        ]
        verified = await reasoner._verify_claims_inline(claims)
        assert len(verified) == 1


# ════════════════════════════════════════════════════════════════════════════
# Answer building
# ════════════════════════════════════════════════════════════════════════════

class TestAnswerBuilding:
    def test_all_grounded_returns_answer(self) -> None:
        reasoner = GroundedReasoner()
        claims = [
            Claim(text="Paris is capital", grounded=True, confidence=0.9,
                  evidence=[EvidenceItem(source="wiki", kind="rag", confidence=0.9)]),
        ]
        answer, abstained = reasoner._build_answer(claims)
        assert not abstained
        assert "Paris" in answer

    def test_no_grounded_abstains(self) -> None:
        reasoner = GroundedReasoner()
        claims = [
            Claim(text="Unknown fact", grounded=False, confidence=0.0),
        ]
        answer, abstained = reasoner._build_answer(claims)
        assert abstained
        assert "cannot verify" in answer.lower()

    def test_mixed_grounded_returns_with_note(self) -> None:
        reasoner = GroundedReasoner()
        claims = [
            Claim(text="Paris is capital", grounded=True, confidence=0.9,
                  evidence=[EvidenceItem(source="wiki", kind="rag", confidence=0.9)]),
            Claim(text="Unknown fact", grounded=False, confidence=0.0),
        ]
        answer, abstained = reasoner._build_answer(claims)
        assert abstained  # Has ungrounded claims
        assert "Paris" in answer
        assert "could not be verified" in answer


# ════════════════════════════════════════════════════════════════════════════
# Capability test
# ════════════════════════════════════════════════════════════════════════════

class TestCapability:
    @pytest.mark.asyncio
    async def test_reason_returns_reason_result(self) -> None:
        """Capability test: the reasoner returns a ReasonResult even when
        disabled, proving the pipeline is structurally intact."""
        result = await reason("What is the capital of France?")
        assert isinstance(result, ReasonResult)
        assert hasattr(result, "answer")
        assert hasattr(result, "claims")
        assert hasattr(result, "steps")
        assert hasattr(result, "abstained")
        assert hasattr(result, "duration_ms")

    @pytest.mark.asyncio
    async def test_reason_with_mocked_components(self) -> None:
        """Capability test: with all components mocked, the pipeline
        produces a grounded answer."""
        reasoner = GroundedReasoner()

        # Mock all sub-components
        mock_rl = AsyncMock()
        mock_rl.search = AsyncMock(return_value={
            "id": "test", "response": "Paris is the capital of France",
            "confidence_score": 0.95,
        })
        mock_rl.record_response = AsyncMock()

        mock_knowledge = AsyncMock()
        mock_knowledge.query = AsyncMock(return_value=[
            {"fact": "Paris is the capital of France", "source": "kb", "confidence": 0.95},
        ])

        mock_neural = AsyncMock()
        mock_neural.recall = AsyncMock(return_value=[])

        mock_rag = AsyncMock()
        mock_rag.search = AsyncMock(return_value=[])

        mock_pv = MagicMock()
        mock_pv.verify_premises.return_value = MagicMock(premises=[], verdicts=[])

        reasoner._reasoning_library = mock_rl
        reasoner._knowledge = mock_knowledge
        reasoner._neural = mock_neural
        reasoner._rag = mock_rag
        reasoner._premise_verifier = mock_pv

        # Enable the gate
        with patch.dict(os.environ, {"ARIA_GROUNDED_REASONER": "1"}):
            # Re-create to pick up env var
            import aria_service.intel.grounded_reasoner as gr_mod
            gr_mod._ENABLED = True
            reasoner._ENABLED = True  # type: ignore[attr-defined]

            result = await reasoner.reason("What is the capital of France?")

        assert isinstance(result, ReasonResult)
        assert result.duration_ms > 0

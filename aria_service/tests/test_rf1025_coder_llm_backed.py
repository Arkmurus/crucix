"""R-F1025 — the self-coder must use a REAL LLM-backed provider, not the
template-only stub, and the provider's return contract must match exactly what
ARIACoder.fix_gap reads.

Background: ARIA's R-F1003 "no external LLM" sweep wired AutonomousCoder (a
SelfCodingOS template stub) as the coder's provider. That stub:
  - write_code ignored existing_code and emitted a fresh stub,
  - write_tests returned key "code" while fix_gap reads "test_code",
  - analyse_failure returned the ORIGINAL code (self-heal never fixed anything).
So the coder could never actually fix a bug. SovereignLLM (LLM-backed, model-
agnostic) returns the exact keys fix_gap reads. These tests lock that in.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from aria_service.autonomous.self_coder import ARIACoder
from aria_service.autonomous.sovereign_llm import SovereignLLM


def _coder() -> ARIACoder:
    """Construct ARIACoder with all heavy deps injected so __init__ stays light
    and the default `llm` (the thing under test) is constructed for real."""
    stub = MagicMock()
    return ARIACoder(
        redis_client=MagicMock(),
        aria_service_url="http://localhost:8000",
        gap_detector=stub, validator=stub, codebase=stub,
        test_runner=stub, deployer=stub, r_counter=stub,
        llm=None,  # <- exercise the default provider
    )


def test_default_provider_is_llm_backed_sovereign_llm():
    coder = _coder()
    assert isinstance(coder.llm, SovereignLLM), (
        "coder must default to the LLM-backed SovereignLLM, not the template stub"
    )


@pytest.mark.asyncio
async def test_sovereign_llm_contract_matches_what_fix_gap_reads(monkeypatch):
    """Mock the HTTP LLM call; verify each method returns the keys self_coder reads:
    plan -> approach/target_files/risk_level, code -> code,
    tests -> test_code/test_filepath, heal -> code (corrected)."""
    llm = SovereignLLM(aria_service_url="http://localhost:8000")

    # Capture what the model is asked, and return a canned JSON per task.
    async def fake_call(prompt, task, prefer_model):
        if task == "plan":
            return {"title": "t", "approach": "a", "target_files": ["f.py"], "risk_level": "low"}
        if task == "code":
            # proves existing code is given to the model (prompt includes it)
            assert "EXISTING" in prompt or "existing" in prompt
            return {"code": "def f():\n    return 1\n"}
        if task == "test":
            return {"test_filepath": "aria_service/tests/test_rfX_auto.py",
                    "test_code": "def test_f():\n    assert True\n"}
        if task == "heal":
            return {"code": "def f():\n    return 2  # corrected\n"}
        return {}

    monkeypatch.setattr(llm, "_call", fake_call)

    gap = MagicMock()
    gap.gap_type = "MODULE_BUG"; gap.severity = MagicMock(name="SEV"); gap.severity.name = "MEDIUM"
    gap.title = "x"; gap.description = "y"; gap.module = "m.py"; gap.error_trace = None

    plan = await llm.generate_fix_plan(gap, "context")
    assert plan["approach"] and plan["target_files"] == ["f.py"] and plan["risk_level"] == "low"

    code = await llm.write_code(plan, existing_code="def f():\n    return 0\n", target_file="f.py")
    assert code["code"].strip(), "write_code must return non-empty 'code'"

    tests = await llm.write_tests(plan, code["code"], 1025)
    assert tests["test_code"].strip() and tests["test_filepath"], "write_tests must return test_code + test_filepath"

    heal = await llm.analyse_failure("AssertionError", "def f():\n    return 0\n", 1)
    assert "corrected" in heal["code"], "analyse_failure must return CORRECTED code, not the original"

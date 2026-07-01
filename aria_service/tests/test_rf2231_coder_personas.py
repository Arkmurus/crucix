"""R-F2231 — ARIA's self_coder selects a domain persona per gap and injects its
rules into the real fix prompt.

Ports the .claude/agents specialists into ARIA's OWN coder (DeepSeek), so an
autonomous fix to sanctions/DD/WA/etc. code is written under that specialist's
rules. These tests drive the REAL persona selector AND the REAL sovereign_llm
prompt builder to prove the rules actually reach the code-gen prompt.
"""
from __future__ import annotations

import pytest

from aria_service.autonomous.coder_personas import select_persona, persona_prompt_block
from aria_service.autonomous.sovereign_llm import SovereignLLM


class TestR_F2231_Selection:
    @pytest.mark.parametrize("hint,expected", [
        ("aria_service/intel/sanctions_canonical/lookup.py", "sanctions-auditor"),
        ("ofac_sdn", "sanctions-auditor"),
        ("aria_service/intel/company_investigator.py", "dd-reviewer"),
        ("aria_service/intel/dd_orchestrator.py", "dd-reviewer"),
        ("services/wa-listener/aria_wa_listener.mjs", "wa-debugger"),
        ("aria_service/autonomous/gap_detector.py", "autonomy-engineer"),
        ("aria_service/intel/reranker.py", "ai-engineer"),
        ("aria_service/intel/web_search.py", "search-specialist"),
        ("aria_service/routes/aria.py", "python-pro"),        # default fallback
        ("something_totally_unmatched", "python-pro"),
        ("", "python-pro"),
        (None, "python-pro"),
    ])
    def test_routes_to_the_right_persona(self, hint, expected):
        name, rules = select_persona(hint)
        assert name == expected
        assert rules, "every persona must carry non-empty rules"

    def test_block_always_present_and_labeled(self):
        block = persona_prompt_block("aria_service/intel/reranker.py")
        assert "SPECIALIST PERSONA — ai-engineer" in block
        assert "offload" in block.lower()  # a real ai-engineer rule
        # default is never empty (python-pro floor)
        assert "python-pro" in persona_prompt_block("nothing_matches")


class TestR_F2231_InjectedIntoRealPrompt:
    """Drive the REAL sovereign_llm prompt builders — the persona must reach the
    actual code-gen prompt DeepSeek receives."""

    def _llm(self):
        return SovereignLLM("http://localhost:8000")

    def test_code_prompt_carries_sanctions_persona(self):
        p = self._llm()._build_code_prompt(
            {"approach": "fix the empty-store verdict"}, "def check(): ...",
            "aria_service/intel/sanctions_canonical/lookup.py",
        )
        assert "sanctions-auditor" in p
        assert "INSUFFICIENT_DATA" in p        # the never-false-clean rule is in the prompt

    def test_edit_prompt_carries_domain_persona(self):
        p = self._llm()._build_edit_prompt(
            {"approach": "x"}, "x" * 100, "aria_service/intel/reranker.py",
        )
        assert "ai-engineer" in p and "event loop" in p.lower()

    def test_code_prompt_defaults_to_python_pro(self):
        p = self._llm()._build_code_prompt({}, "x", "aria_service/intel/some_random_module.py")
        assert "python-pro" in p
        assert "single-process" in p.lower()   # the default's core rule

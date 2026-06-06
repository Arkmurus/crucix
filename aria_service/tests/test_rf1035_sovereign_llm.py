"""R-F1035 — Unit tests for SovereignLLM (LLM client for self-coding).

Covers the HTTP client layer in `aria_service/autonomous/sovereign_llm.py`:

  - generate_fix_plan() — plan a fix from a gap + context
  - write_code() — generate file content
  - write_tests() — generate regression tests
  - analyse_failure() — self-heal from test failures
  - _call() — HTTP POST to the coder LLM endpoint
  - Prompt templates — verify they contain expected structure

Uses mocked httpx.AsyncClient so no real network calls.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


def _mock_response(status=200, data=None):
    """Create a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.json = MagicMock(return_value=data or {})
    resp.raise_for_status = MagicMock()
    return resp


class TestSovereignLLM:
    def setup_method(self):
        from aria_service.autonomous.sovereign_llm import SovereignLLM
        self.client = MagicMock(spec=httpx.AsyncClient)
        self.llm = SovereignLLM(
            aria_service_url="http://localhost:8000",
            client=self.client,
        )
        # Set the internal token so _call doesn't raise
        self._token_patch = patch.dict(
            "os.environ", {"ARIA_INTERNAL_TOKEN": "test-token"},
        )
        self._token_patch.start()

    def teardown_method(self):
        self._token_patch.stop()

    # ── _call ──────────────────────────────────────────────────────────────

    def test_call_posts_to_correct_endpoint(self):
        self.client.post = AsyncMock(return_value=_mock_response(200, {"ok": True}))
        import asyncio
        result = asyncio.run(self.llm._call("test prompt", "plan", "aria-llm"))
        assert result == {"ok": True}
        self.client.post.assert_called_once()
        call_args = self.client.post.call_args
        assert call_args[0][0] == "http://localhost:8000/api/aria/coder/llm"
        assert call_args[1]["json"]["prompt"] == "test prompt"
        assert call_args[1]["json"]["task"] == "plan"
        assert call_args[1]["json"]["prefer_model"] == "aria-llm"

    def test_call_sends_auth_header(self):
        self.client.post = AsyncMock(return_value=_mock_response(200, {}))
        import asyncio
        asyncio.run(self.llm._call("p", "plan", "aria-llm"))
        headers = self.client.post.call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer test-token"

    def test_call_raises_on_missing_token(self):
        # Use clear=True to ensure ARIA_INTERNAL_TOKEN is definitely absent
        import asyncio
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(RuntimeError, match="ARIA_INTERNAL_TOKEN"):
                asyncio.run(self.llm._call("p", "plan", "aria-llm"))

    def test_call_raises_on_http_error(self):
        self.client.post = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "400 Bad Request", request=MagicMock(), response=MagicMock(),
            ),
        )
        import asyncio
        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(self.llm._call("p", "plan", "aria-llm"))

    # ── generate_fix_plan ──────────────────────────────────────────────────

    def test_generate_fix_plan_returns_dict(self):
        expected = {"title": "Fix bug", "approach": "Add null check", "risk_level": "low"}
        self.client.post = AsyncMock(return_value=_mock_response(200, expected))
        from aria_service.autonomous.gap_detector import Gap, GapType, GapSeverity
        gap = Gap(gap_id="g1", gap_type=GapType.MODULE_BUG, severity=GapSeverity.HIGH,
                  title="Bug", description="Null pointer", module="test.py")
        import asyncio
        result = asyncio.run(self.llm.generate_fix_plan(gap, "code context"))
        assert result == expected

    def test_generate_fix_plan_includes_gap_in_prompt(self):
        self.client.post = AsyncMock(return_value=_mock_response(200, {}))
        from aria_service.autonomous.gap_detector import Gap, GapType, GapSeverity
        gap = Gap(gap_id="g1", gap_type=GapType.MODULE_BUG, severity=GapSeverity.HIGH,
                  title="Bug", description="Null pointer", module="test.py")
        import asyncio
        asyncio.run(self.llm.generate_fix_plan(gap, "code context"))
        prompt = self.client.post.call_args[1]["json"]["prompt"]
        assert "Null pointer" in prompt
        assert "code context" in prompt
        assert "risk_level" in prompt

    # ── write_code ─────────────────────────────────────────────────────────

    def test_write_code_returns_dict(self):
        expected = {"filepath": "test.py", "code": "print('hello')", "changes_made": ["x"]}
        self.client.post = AsyncMock(return_value=_mock_response(200, expected))
        import asyncio
        result = asyncio.run(self.llm.write_code({"title": "Fix"}, "old code", "test.py"))
        assert result == expected

    def test_write_code_includes_target_file_in_prompt(self):
        self.client.post = AsyncMock(return_value=_mock_response(200, {}))
        import asyncio
        asyncio.run(self.llm.write_code({"title": "Fix"}, "old code", "test.py"))
        prompt = self.client.post.call_args[1]["json"]["prompt"]
        assert "TARGET FILE: test.py" in prompt
        assert "old code" in prompt

    # ── write_tests ────────────────────────────────────────────────────────

    def test_write_tests_returns_dict(self):
        expected = {"test_filepath": "test_rf9999_auto.py", "test_code": "def test_x(): pass"}
        self.client.post = AsyncMock(return_value=_mock_response(200, expected))
        import asyncio
        result = asyncio.run(self.llm.write_tests({"title": "Fix"}, "new code", 9999))
        assert result == expected

    def test_write_tests_prefers_coder_main_llm(self):
        """R-F1366: all coder tasks (tests included) hint the operator-
        designated main coder LLM — deepseek by default (was aria-8b)."""
        self.client.post = AsyncMock(return_value=_mock_response(200, {}))
        import asyncio
        asyncio.run(self.llm.write_tests({"title": "Fix"}, "new code", 9999))
        assert self.client.post.call_args[1]["json"]["prefer_model"] == "deepseek"

    # ── analyse_failure ────────────────────────────────────────────────────

    def test_analyse_failure_returns_dict(self):
        expected = {"failure_mode": "assertion error", "code": "fixed code"}
        self.client.post = AsyncMock(return_value=_mock_response(200, expected))
        import asyncio
        result = asyncio.run(self.llm.analyse_failure("error msg", "old code", 2))
        assert result == expected

    def test_analyse_failure_includes_attempt_number(self):
        self.client.post = AsyncMock(return_value=_mock_response(200, {}))
        import asyncio
        asyncio.run(self.llm.analyse_failure("error msg", "old code", 2))
        prompt = self.client.post.call_args[1]["json"]["prompt"]
        assert "attempt 2" in prompt.lower()

    # ── Prompt templates ───────────────────────────────────────────────────

    def test_plan_prompt_contains_constitutional_constraints(self):
        from aria_service.autonomous.sovereign_llm import SovereignLLM
        llm = SovereignLLM(aria_service_url="http://x", client=self.client)
        from aria_service.autonomous.gap_detector import Gap, GapType, GapSeverity
        gap = Gap(gap_id="g1", gap_type=GapType.MODULE_BUG, severity=GapSeverity.HIGH,
                  title="Bug", description="x", module="test.py")
        prompt = llm._build_plan_prompt(gap, "ctx")
        # R-F1191: constitutional validator removed — PROTECTED_FILES no longer in prompt.
        # Instead, the prompt contains constitutional constraints about eval/exec/subprocess.
        assert "eval()" in prompt or "exec()" in prompt
        assert "brain_hook.absorb" in prompt
        assert "httpx" in prompt or "fly_deployer" in prompt

    def test_code_prompt_contains_quality_requirements(self):
        from aria_service.autonomous.sovereign_llm import SovereignLLM
        llm = SovereignLLM(aria_service_url="http://x", client=self.client)
        prompt = llm._build_code_prompt({"title": "Fix"}, "existing", "test.py")
        assert "Type hints" in prompt
        assert "docstrings" in prompt
        assert "brain_hook.absorb" in prompt

    def test_test_prompt_contains_unit_and_capability_requirements(self):
        from aria_service.autonomous.sovereign_llm import SovereignLLM
        llm = SovereignLLM(aria_service_url="http://x", client=self.client)
        prompt = llm._build_test_prompt({"title": "Fix"}, "new code", 9999)
        assert "UNIT test" in prompt
        assert "CAPABILITY test" in prompt
        assert "test_rf9999" in prompt

    def test_healing_prompt_contains_diagnosis_discipline(self):
        from aria_service.autonomous.sovereign_llm import SovereignLLM
        llm = SovereignLLM(aria_service_url="http://x", client=self.client)
        prompt = llm._build_healing_prompt("error msg", "old code", 2)
        assert "DIAGNOSIS DISCIPLINE" in prompt
        assert "failure_mode" in prompt
        assert "root cause" in prompt.lower()


# ════════════════════════════════════════════════════════════════════════════
# Capability test — the user-visible symptom
# ════════════════════════════════════════════════════════════════════════════

def test_capability_sovereign_llm_rejects_missing_token():
    """Capability test: without ARIA_INTERNAL_TOKEN, every LLM call must
    raise RuntimeError. This is the safety invariant — the coder must never
    fire an unauthenticated LLM request."""
    from aria_service.autonomous.sovereign_llm import SovereignLLM
    client = MagicMock(spec=httpx.AsyncClient)
    llm = SovereignLLM(aria_service_url="http://localhost:8000", client=client)
    from aria_service.autonomous.gap_detector import Gap, GapType, GapSeverity
    gap = Gap(gap_id="g1", gap_type=GapType.MODULE_BUG, severity=GapSeverity.HIGH,
              title="Bug", description="x", module="test.py")
    import asyncio
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(RuntimeError, match="ARIA_INTERNAL_TOKEN"):
            asyncio.run(llm.generate_fix_plan(gap, "ctx"))

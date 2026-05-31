"""R-F805 — Tests for ClaudeReviewer + ARIACoder integration.

Vision under test (operator 2026-05-22): "ARIA is the primary coder;
Claude Code is the verifier that ensures the code is accurate, no
bugs, grounded."

Capability tests
────────────────
- Dormant by default — no env vars set → APPROVED no-op, pipeline
  proceeds via the existing R-F462 staging path.
- Activates when BOTH ARIA_CODER_CLAUDE_REVIEW_ENABLED=1 AND
  ANTHROPIC_API_KEY are set.
- Fail-safe: any API error returns FLAGGED, never APPROVED. The
  staging queue absorbs the diff for operator review.
- BLOCKED verdict rejects the fix outright — never staged, never deployed.
- FLAGGED verdict force-stages — operator review required even if
  R-F462 ARIA_SELF_IMPROVE_AUTO_DEPLOY=1 is set.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _run(coro):
    return asyncio.run(coro)


# ════════════════════════════════════════════════════════════════════════════
# ClaudeReviewer — standalone behaviour
# ════════════════════════════════════════════════════════════════════════════

class _FakeProvider:
    """Minimal LLMProvider stand-in for the review chain (R-F923).
    Either returns a fixed text verdict or raises to force fallthrough."""

    def __init__(self, name: str, text: str | None = None, exc: Exception | None = None) -> None:
        self.name = name
        self.is_configured = True
        self._text = text
        self._exc = exc

    async def complete(self, system, user, *, max_tokens: int = 1024, timeout: float = 60.0):
        if self._exc is not None:
            raise self._exc
        return SimpleNamespace(text=self._text or "", model=self.name)


class TestReviewAlwaysAvailable:
    """R-F923: review is ALWAYS available — the fail-open `disabled→APPROVED`
    hole is closed. is_enabled() is always True so self_coder never skips
    review; the Claude-specific gate moved to anthropic_review_enabled()."""

    def test_is_enabled_always_true(self) -> None:
        from aria_service.autonomous.claude_reviewer import is_enabled
        assert is_enabled() is True

    def test_anthropic_gate_needs_both(self, monkeypatch) -> None:
        from aria_service.autonomous.claude_reviewer import anthropic_review_enabled
        monkeypatch.delenv("ARIA_CODER_CLAUDE_REVIEW_ENABLED", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert not anthropic_review_enabled()
        monkeypatch.setenv("ARIA_CODER_CLAUDE_REVIEW_ENABLED", "1")
        assert not anthropic_review_enabled()  # key still missing
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        assert anthropic_review_enabled()

    def test_no_llm_never_approves_falls_to_self_check(self) -> None:
        """CAPABILITY (the headline fix): with NO LLM provider reachable,
        review() must NOT return APPROVED. A structurally-clean change is
        FLAGGED (staged for a human), never auto-deployed unreviewed."""
        from aria_service.autonomous.claude_reviewer import ClaudeReviewer

        async def body():
            reviewer = ClaudeReviewer(providers=[])  # empty chain → self-check
            verdict = await reviewer.review(
                diff="--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old=1\n+old=2",
                change_type="bug_fix", gap_title="t",
                gap_description="d", files=["x.py"],
            )
            await reviewer.aclose()
            return verdict

        verdict = _run(body())
        assert not verdict.is_approved
        assert verdict.is_flagged
        assert verdict.reviewer == "aria_self_check"


class TestReviewChainFallback:
    """R-F923: Claude → DeepSeek → … fallthrough."""

    def test_deepseek_used_when_first_tier_errors(self) -> None:
        """CAPABILITY: 'if anthropic is down because of credit we use
        deepseek'. First provider raises (billing); DeepSeek returns the
        verdict and wins."""
        from aria_service.autonomous.claude_reviewer import ClaudeReviewer

        anthropic = _FakeProvider("anthropic", exc=RuntimeError("billing exhausted"))
        deepseek = _FakeProvider("deepseek", text='{"verdict": "approved", "reasons": []}')

        async def body():
            reviewer = ClaudeReviewer(providers=[anthropic, deepseek])
            return await reviewer.review(
                diff="x", change_type="bug_fix",
                gap_title="t", gap_description="d", files=["x.py"],
            )

        verdict = _run(body())
        assert verdict.is_approved
        assert verdict.reviewer == "deepseek"

    def test_first_usable_verdict_wins(self) -> None:
        from aria_service.autonomous.claude_reviewer import ClaudeReviewer

        deepseek = _FakeProvider("deepseek", text='{"verdict": "blocked", "reasons": ["bug"]}')
        groq = _FakeProvider("groq", text='{"verdict": "approved", "reasons": []}')

        async def body():
            reviewer = ClaudeReviewer(providers=[deepseek, groq])
            return await reviewer.review(
                diff="x", change_type="bug_fix",
                gap_title="t", gap_description="d", files=["x.py"],
            )

        verdict = _run(body())
        assert verdict.is_blocked
        assert verdict.reviewer == "deepseek"

    def test_empty_text_falls_through_to_next(self) -> None:
        from aria_service.autonomous.claude_reviewer import ClaudeReviewer

        empty = _FakeProvider("deepseek", text="   ")
        groq = _FakeProvider("groq", text='{"verdict": "flagged", "reasons": ["x"]}')

        async def body():
            reviewer = ClaudeReviewer(providers=[empty, groq])
            return await reviewer.review(
                diff="x", change_type="bug_fix",
                gap_title="t", gap_description="d", files=["x.py"],
            )

        verdict = _run(body())
        assert verdict.is_flagged
        assert verdict.reviewer == "groq"


class TestAriaSelfCheck:
    """R-F923: deterministic self-check floor — BLOCKs clearly dangerous
    changes, FLAGs otherwise. Never auto-approves."""

    def _self_check(self, diff: str):
        from aria_service.autonomous.claude_reviewer import ClaudeReviewer

        async def body():
            reviewer = ClaudeReviewer(providers=[])
            return await reviewer.review(
                diff=diff, change_type="bug_fix",
                gap_title="t", gap_description="d", files=["x.py"],
            )

        return _run(body())

    def test_blocks_truncation(self) -> None:
        """Mass-deletion full-file shrink (R-F904 class) is BLOCKED."""
        removed = "\n".join(f"-line_{i} = {i}" for i in range(60))
        diff = f"--- a/x.py\n+++ b/x.py\n@@ @@\n{removed}\n+line_0 = 0"
        verdict = self._self_check(diff)
        assert verdict.is_blocked
        assert any("truncation" in r.lower() for r in verdict.reasons)

    def test_blocks_dangerous_exec(self) -> None:
        diff = "--- a/x.py\n+++ b/x.py\n@@ @@\n+result = eval(user_input)"
        verdict = self._self_check(diff)
        assert verdict.is_blocked
        assert any("eval(" in r for r in verdict.reasons)

    def test_blocks_guard_removal(self) -> None:
        diff = (
            "--- a/x.py\n+++ b/x.py\n@@ @@\n"
            "-    if not authorized:\n"
            "-        raise PermissionError('denied')\n"
            "+    pass"
        )
        verdict = self._self_check(diff)
        assert verdict.is_blocked
        assert any("guard" in r.lower() for r in verdict.reasons)

    def test_flags_clean_change(self) -> None:
        diff = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-x = 1\n+x = 2  # tweak"
        verdict = self._self_check(diff)
        assert verdict.is_flagged
        assert not verdict.is_approved
        assert verdict.reviewer == "aria_self_check"


class TestClaudeReviewerParsing:
    """Parser correctness against various Anthropic-shaped responses."""

    def _make_reviewer(self, response_text: str):
        """Build a ClaudeReviewer with a stub httpx client that returns
        a fixed text response. Skips the enable gate by setting env."""
        import os
        os.environ["ARIA_CODER_CLAUDE_REVIEW_ENABLED"] = "1"
        os.environ["ANTHROPIC_API_KEY"] = "test-key"

        from aria_service.autonomous.claude_reviewer import ClaudeReviewer

        stub_client = MagicMock()
        stub_response = MagicMock()
        stub_response.json.return_value = {
            "content": [{"type": "text", "text": response_text}],
        }
        stub_response.raise_for_status = MagicMock()
        stub_client.post = AsyncMock(return_value=stub_response)
        stub_client.aclose = AsyncMock()
        return ClaudeReviewer(http_client=stub_client), stub_client

    def test_parses_approved(self) -> None:
        reviewer, _ = self._make_reviewer('{"verdict": "approved", "reasons": []}')

        async def body():
            return await reviewer.review(
                diff="x", change_type="bug_fix",
                gap_title="t", gap_description="d", files=["x.py"],
            )

        verdict = _run(body())
        assert verdict.is_approved
        assert verdict.reasons == []

    def test_parses_flagged_with_reasons(self) -> None:
        reviewer, _ = self._make_reviewer(
            '{"verdict": "flagged", "reasons": ["Unclear naming on line 42",'
            ' "Consider an edge case where input is empty"]}'
        )

        async def body():
            return await reviewer.review(
                diff="x", change_type="bug_fix",
                gap_title="t", gap_description="d", files=["x.py"],
            )

        verdict = _run(body())
        assert verdict.is_flagged
        assert len(verdict.reasons) == 2

    def test_parses_blocked(self) -> None:
        reviewer, _ = self._make_reviewer(
            '{"verdict": "blocked", "reasons": ["Off-by-one on line 17"]}'
        )

        async def body():
            return await reviewer.review(
                diff="x", change_type="bug_fix",
                gap_title="t", gap_description="d", files=["x.py"],
            )

        verdict = _run(body())
        assert verdict.is_blocked
        assert "Off-by-one" in verdict.reasons[0]

    def test_strips_markdown_fences(self) -> None:
        """Claude may wrap JSON in ```json fences despite the
        instruction. The parser must unwrap before parsing."""
        reviewer, _ = self._make_reviewer(
            '```json\n{"verdict": "approved", "reasons": []}\n```'
        )

        async def body():
            return await reviewer.review(
                diff="x", change_type="bug_fix",
                gap_title="t", gap_description="d", files=["x.py"],
            )

        verdict = _run(body())
        assert verdict.is_approved

    def test_unknown_verdict_defaults_to_flagged(self) -> None:
        """If Claude returns a verdict value the parser doesn't know,
        default to FLAGGED — never silently APPROVE."""
        reviewer, _ = self._make_reviewer(
            '{"verdict": "deeply_sceptical", "reasons": ["weird"]}'
        )

        async def body():
            return await reviewer.review(
                diff="x", change_type="bug_fix",
                gap_title="t", gap_description="d", files=["x.py"],
            )

        verdict = _run(body())
        assert verdict.is_flagged

    def test_garbage_response_returns_flagged(self) -> None:
        """When the model emits non-JSON despite instruction, fail-safe
        to FLAGGED — never silently APPROVE."""
        reviewer, _ = self._make_reviewer("This is not JSON at all.")

        async def body():
            return await reviewer.review(
                diff="x", change_type="bug_fix",
                gap_title="t", gap_description="d", files=["x.py"],
            )

        verdict = _run(body())
        assert verdict.is_flagged


class TestClaudeReviewerFailSafe:
    """The hook MUST fail safe (FLAGGED) on any API error so changes
    land on the staging queue rather than auto-deploy unreviewed."""

    def test_http_error_returns_flagged(self, monkeypatch) -> None:
        monkeypatch.setenv("ARIA_CODER_CLAUDE_REVIEW_ENABLED", "1")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        import httpx
        from aria_service.autonomous.claude_reviewer import ClaudeReviewer

        stub_client = MagicMock()
        stub_client.post = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "boom", request=MagicMock(), response=MagicMock(),
            )
        )
        stub_client.aclose = AsyncMock()
        reviewer = ClaudeReviewer(http_client=stub_client)

        async def body():
            return await reviewer.review(
                diff="x", change_type="bug_fix",
                gap_title="t", gap_description="d", files=["x.py"],
            )

        verdict = _run(body())
        assert verdict.is_flagged
        assert verdict.api_error is not None

    def test_unexpected_error_returns_flagged(self, monkeypatch) -> None:
        monkeypatch.setenv("ARIA_CODER_CLAUDE_REVIEW_ENABLED", "1")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        from aria_service.autonomous.claude_reviewer import ClaudeReviewer

        stub_client = MagicMock()
        stub_client.post = AsyncMock(side_effect=RuntimeError("network down"))
        stub_client.aclose = AsyncMock()
        reviewer = ClaudeReviewer(http_client=stub_client)

        async def body():
            return await reviewer.review(
                diff="x", change_type="bug_fix",
                gap_title="t", gap_description="d", files=["x.py"],
            )

        verdict = _run(body())
        assert verdict.is_flagged


class TestBuildDiff:
    def test_unified_diff_shape(self) -> None:
        from aria_service.autonomous.claude_reviewer import build_unified_diff

        def read(filepath: str) -> str:
            return "old\nlines\n" if filepath == "x.py" else ""

        diff = build_unified_diff(
            {"x.py": "new\nlines\n"}, read,
        )
        assert "--- a/x.py" in diff
        assert "+++ b/x.py" in diff
        assert "-old" in diff
        assert "+new" in diff

    def test_new_file_shows_as_addition(self) -> None:
        from aria_service.autonomous.claude_reviewer import build_unified_diff

        def read(filepath: str) -> str:
            return ""  # all "files" are new

        diff = build_unified_diff(
            {"new.py": "content\n"}, read,
        )
        assert "+content" in diff

    def test_truncation_threshold(self) -> None:
        """A huge diff is truncated for cost — verify the prompt builder
        applies the cap (8000 chars) before sending."""
        from aria_service.autonomous.claude_reviewer import ClaudeReviewer
        reviewer = ClaudeReviewer(http_client=MagicMock())
        prompt = reviewer._build_prompt(
            diff="x" * 12000, change_type="bug_fix",
            gap_title="t", gap_description="d", files=["x.py"],
        )
        assert "[... TRUNCATED ...]" in prompt
        # No raw 12k content — prompt should be much smaller
        assert len(prompt) < 9500


# ════════════════════════════════════════════════════════════════════════════
# ARIACoder integration — fix_gap behaviour under each verdict
# ════════════════════════════════════════════════════════════════════════════

def _make_coder():
    """Construct an ARIACoder with stubs that allow fix_gap to reach
    the Claude-review step (steps 1-6 all succeed)."""
    from aria_service.autonomous.self_coder import ARIACoder

    redis = MagicMock()
    coder = ARIACoder(
        redis_client=redis,
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
        workspace_base=Path(tempfile.mkdtemp(prefix="rf805_")),
    )
    # Async stubs so fix_gap can proceed through steps 1-6
    coder.codebase.get_context = AsyncMock(return_value="context")
    coder.codebase.read = MagicMock(return_value="old\n")
    coder.codebase.write_to_workspace = MagicMock(return_value=Path("/tmp/x"))
    coder.llm.generate_fix_plan = AsyncMock(
        return_value={
            "title": "fix the thing",
            "approach": "small change",
            "target_files": ["aria_service/intel/knowledge.py"],
            "risk_level": "low",
        },
    )
    coder.llm.write_code = AsyncMock(
        return_value={
            "code": "# patched line\n",
            "changes_made": ["added comment"],
        },
    )
    coder.llm.write_tests = AsyncMock(
        return_value={
            "test_code": "def test_x(): pass\n",
            "test_filepath": "aria_service/tests/test_auto.py",
        },
    )
    coder.r_counter.next = AsyncMock(return_value=812)
    # R-F1191: constitutional validator removed — set a mock validator
    coder.validator = MagicMock()
    coder.validator.validate = MagicMock(
        return_value=type("VR", (), {"passed": True, "violations": [], "risk_score": 0.0})(),
    )
    from aria_service.autonomous.test_runner import TestResult
    coder.test_runner.run_isolated = AsyncMock(
        return_value=TestResult(all_green=True, passed=5),
    )
    return coder


def _make_gap():
    from aria_service.autonomous.gap_detector import (
        Gap, GapSeverity, GapType,
    )
    return Gap(
        gap_id="rf805", gap_type=GapType.MODULE_BUG,
        severity=GapSeverity.HIGH,
        title="t", description="d",
        module="aria_service/intel/knowledge.py",
    )


class TestFixGapClaudeIntegration:
    def test_approved_proceeds_to_stage(self) -> None:
        """CAPABILITY: APPROVED verdict → fix proceeds to stage_or_deploy.
        Confirms the hook doesn't block the happy path."""
        from aria_service.autonomous.claude_reviewer import (
            ReviewVerdict, Verdict,
        )
        coder = _make_coder()

        with patch(
            "aria_service.autonomous.self_coder.ARIACoder._claude_review",
            new_callable=AsyncMock,
        ) as mock_review, patch(
            "aria_service.intel.self_improve.stage_improvement",
            new_callable=AsyncMock,
        ) as mock_stage, patch(
            "aria_service.intel.self_improve.CHANGE_TYPES",
            {"bug_fix": {"auto_deploy": False, "description": "x"}},
        ), patch(
            "aria_service.autonomous.safety.can_task_run",
            new_callable=AsyncMock,
            return_value=(True, "ok"),
        ):
            mock_review.return_value = ReviewVerdict(verdict=Verdict.APPROVED)
            mock_stage.return_value = {
                "staged": True, "id": "id_a", "auto_deployable": False,
            }

            async def body():
                return await coder.fix_gap(_make_gap())

            result = _run(body())
            assert result.success
            mock_stage.assert_called_once()  # stage was reached

    def test_blocked_rejects_fix(self) -> None:
        """CAPABILITY: BLOCKED verdict → fix_gap returns success=False.
        stage_improvement must NOT be called — the diff is rejected."""
        from aria_service.autonomous.claude_reviewer import (
            ReviewVerdict, Verdict,
        )
        coder = _make_coder()

        with patch(
            "aria_service.autonomous.self_coder.ARIACoder._claude_review",
            new_callable=AsyncMock,
        ) as mock_review, patch(
            "aria_service.intel.self_improve.stage_improvement",
            new_callable=AsyncMock,
        ) as mock_stage, patch(
            "aria_service.autonomous.safety.can_task_run",
            new_callable=AsyncMock,
            return_value=(True, "ok"),
        ):
            mock_review.return_value = ReviewVerdict(
                verdict=Verdict.BLOCKED,
                reasons=["Off-by-one on line 17", "Race condition"],
            )

            async def body():
                return await coder.fix_gap(_make_gap())

            result = _run(body())
            assert not result.success
            assert "BLOCKED" in result.failure_reason
            assert "Off-by-one" in result.failure_reason
            # Critical: never staged
            mock_stage.assert_not_called()

    def test_flagged_force_stages_overriding_r_f462_gate(self) -> None:
        """CAPABILITY (highest-value test): FLAGGED verdict forces
        staging even when R-F462 gate is OPEN (CHANGE_TYPES.bug_fix.
        auto_deploy=True). This is the operator-eyes-required path
        that puts Claude's concerns in front of a human."""
        from aria_service.autonomous.claude_reviewer import (
            ReviewVerdict, Verdict,
        )
        coder = _make_coder()

        with patch(
            "aria_service.autonomous.self_coder.ARIACoder._claude_review",
            new_callable=AsyncMock,
        ) as mock_review, patch(
            "aria_service.intel.self_improve.stage_improvement",
            new_callable=AsyncMock,
        ) as mock_stage, patch(
            "aria_service.intel.self_improve.deploy_improvement",
            new_callable=AsyncMock,
        ) as mock_deploy, patch(
            # R-F462 gate is OPEN here — bug_fix would normally auto-deploy
            "aria_service.intel.self_improve.CHANGE_TYPES",
            {"bug_fix": {"auto_deploy": True, "description": "x"}},
        ), patch(
            "aria_service.autonomous.safety.can_task_run",
            new_callable=AsyncMock,
            return_value=(True, "ok"),
        ):
            mock_review.return_value = ReviewVerdict(
                verdict=Verdict.FLAGGED,
                reasons=["Edge case worth checking"],
            )
            mock_stage.return_value = {
                "staged": True, "id": "id_b", "auto_deployable": True,
            }

            async def body():
                return await coder.fix_gap(_make_gap())

            result = _run(body())
            assert result.success
            # Staged but NOT auto-deployed despite R-F462 gate being open
            mock_stage.assert_called_once()
            mock_deploy.assert_not_called()

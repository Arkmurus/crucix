"""R-F821 — Tests for the post-deploy review-ticket flow.

Operator's vision: ARIA codes + commits autonomously; a GitHub Issue
ticket lets the operator + Claude audit after deploy. This is the
trunk-based-development pattern for autonomous agents — velocity now,
review async.

Critical invariants tested
──────────────────────────
1. Dormant by default — no env vars → ticket is a no-op (deploy still
   happens, no GitHub call attempted).
2. Both env vars (ARIA_CODER_AUTO_DEPLOY_AND_TICKET=1 + GH_TOKEN)
   required — half-configured stays dormant.
3. force_deploy in _stage_or_deploy overrides the R-F462 default gate
   (ticket mode means audit-after-deploy, not approve-before).
4. CAPABILITY: Claude FLAGGED verdict still blocks the deploy even in
   ticket mode (FLAGGED + ticket_mode → stays staged). FLAGGED wins.
5. CAPABILITY: Claude BLOCKED verdict rejects entirely (never deploys,
   never opens ticket). BLOCKED wins.
6. Issue body includes the diff, rollback URL, claude verdict, files
   modified, gap context — everything the reviewer needs.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _run(coro):
    return asyncio.run(coro)


# ════════════════════════════════════════════════════════════════════════════
# review_ticket module
# ════════════════════════════════════════════════════════════════════════════

class TestIsEnabled:
    def test_disabled_when_no_env_vars(self, monkeypatch) -> None:
        from aria_service.autonomous.review_ticket import is_enabled
        monkeypatch.delenv("ARIA_CODER_AUTO_DEPLOY_AND_TICKET", raising=False)
        monkeypatch.delenv("GH_TOKEN", raising=False)
        assert not is_enabled()

    def test_disabled_when_only_flag(self, monkeypatch) -> None:
        from aria_service.autonomous.review_ticket import is_enabled
        monkeypatch.setenv("ARIA_CODER_AUTO_DEPLOY_AND_TICKET", "1")
        monkeypatch.delenv("GH_TOKEN", raising=False)
        assert not is_enabled()

    def test_disabled_when_only_token(self, monkeypatch) -> None:
        from aria_service.autonomous.review_ticket import is_enabled
        monkeypatch.delenv("ARIA_CODER_AUTO_DEPLOY_AND_TICKET", raising=False)
        monkeypatch.setenv("GH_TOKEN", "ghp_xxx")
        assert not is_enabled()

    def test_enabled_when_both_set(self, monkeypatch) -> None:
        from aria_service.autonomous.review_ticket import is_enabled
        monkeypatch.setenv("ARIA_CODER_AUTO_DEPLOY_AND_TICKET", "1")
        monkeypatch.setenv("GH_TOKEN", "ghp_xxx")
        assert is_enabled()


def _make_ticket(**overrides):
    from aria_service.autonomous.review_ticket import ReviewTicket
    defaults = {
        "r_number": 821,
        "gap_title": "ACLED conflict feed returning 0 events",
        "gap_type": "source_failure",
        "gap_severity": "MEDIUM",
        "gap_module": "aria_service/intel/acled.py",
        "gap_description": "ACLED cookie auth expired",
        "change_type": "bug_fix",
        "files_modified": ["aria_service/intel/acled.py"],
        "staged_ids": ["abc12345"],
        "diff": "--- a/x.py\n+++ b/x.py\n-old\n+new",
        "validator_risk_score": 0.15,
        "tests_passed": 5, "tests_failed": 0,
        "auto_deployed": True,
        "aria_service_url": "https://aria-intel.fly.dev",
    }
    defaults.update(overrides)
    return ReviewTicket(**defaults)


class TestFormatIssue:
    def test_title_contains_r_number(self) -> None:
        from aria_service.autonomous.review_ticket import format_issue_title
        title = format_issue_title(_make_ticket())
        assert "R-F821" in title
        assert "[aria-self-coded]" in title

    def test_body_contains_diff(self) -> None:
        from aria_service.autonomous.review_ticket import format_issue_body
        body = format_issue_body(_make_ticket())
        assert "-old" in body
        assert "+new" in body
        assert "```diff" in body

    def test_body_includes_rollback_url(self) -> None:
        from aria_service.autonomous.review_ticket import format_issue_body
        body = format_issue_body(_make_ticket(staged_ids=["zz99"]))
        assert "/api/aria/self/rollback/zz99" in body

    def test_body_includes_claude_verdict_when_present(self) -> None:
        from aria_service.autonomous.review_ticket import format_issue_body
        body = format_issue_body(_make_ticket(
            claude_verdict="approved",
            claude_reasons=["clean diff", "tests cover the change"],
        ))
        assert "APPROVED" in body
        assert "✅" in body
        assert "clean diff" in body

    def test_body_notes_claude_not_run_when_missing(self) -> None:
        from aria_service.autonomous.review_ticket import format_issue_body
        body = format_issue_body(_make_ticket(claude_verdict=None))
        assert "Not run" in body

    def test_diff_truncation_at_30k_chars(self) -> None:
        from aria_service.autonomous.review_ticket import (
            format_issue_body, MAX_DIFF_CHARS,
        )
        huge_diff = "x" * 50000
        body = format_issue_body(_make_ticket(diff=huge_diff))
        assert "TRUNCATED" in body
        # Body shouldn't be enormous
        assert len(body) < 65000

    def test_body_shows_files_modified(self) -> None:
        from aria_service.autonomous.review_ticket import format_issue_body
        body = format_issue_body(_make_ticket(
            files_modified=[
                "aria_service/intel/foo.py",
                "aria_service/intel/bar.py",
            ],
        ))
        assert "foo.py" in body
        assert "bar.py" in body

    def test_body_shows_staged_status_when_not_auto_deployed(self) -> None:
        from aria_service.autonomous.review_ticket import format_issue_body
        body = format_issue_body(_make_ticket(auto_deployed=False))
        assert "Staged for operator review" in body
        # No rollback section when not deployed (nothing to roll back)
        assert "Rollback (if regression" not in body

    def test_body_shows_auto_deployed_when_true(self) -> None:
        from aria_service.autonomous.review_ticket import format_issue_body
        body = format_issue_body(_make_ticket(
            auto_deployed=True, deployed_commit_sha="abc1234",
        ))
        assert "Auto-deployed" in body
        assert "abc1234" in body


# ════════════════════════════════════════════════════════════════════════════
# open_review_ticket — subprocess call
# ════════════════════════════════════════════════════════════════════════════

class TestOpenReviewTicket:
    """R-F823: tests for the GitHub REST API path (replacing gh CLI)."""

    def test_disabled_returns_noop(self, monkeypatch) -> None:
        from aria_service.autonomous.review_ticket import open_review_ticket
        monkeypatch.delenv("ARIA_CODER_AUTO_DEPLOY_AND_TICKET", raising=False)

        http = MagicMock()
        http.post = AsyncMock(side_effect=AssertionError(
            "http.post should NOT be called when dormant"))
        http.aclose = AsyncMock()

        result = _run(open_review_ticket(_make_ticket(), http_client=http))
        assert result["ok"]
        assert result["issue_url"] is None
        assert result["reason"] == "disabled"

    def test_posts_to_github_api_with_correct_payload(self, monkeypatch) -> None:
        """CAPABILITY: when enabled, POST hits the right URL with title
        + body + labels in the JSON payload."""
        from aria_service.autonomous.review_ticket import open_review_ticket
        monkeypatch.setenv("ARIA_CODER_AUTO_DEPLOY_AND_TICKET", "1")
        monkeypatch.setenv("GH_TOKEN", "ghp_test")

        captured = {}

        async def fake_post(url, json):
            captured["url"] = url
            captured["json"] = json
            resp = MagicMock()
            resp.status_code = 201
            resp.json = MagicMock(return_value={
                "number": 42,
                "html_url": "https://github.com/Arkmurus/crucix/issues/42",
            })
            return resp

        http = MagicMock()
        http.post = fake_post
        http.aclose = AsyncMock()

        result = _run(open_review_ticket(
            _make_ticket(),
            http_client=http,
        ))
        assert result["ok"]
        assert "issues/42" in result["issue_url"]
        # POST hit the right endpoint
        assert captured["url"].endswith("/repos/Arkmurus/crucix/issues")
        # payload has title + body + labels
        p = captured["json"]
        assert "R-F821" in p["title"]
        assert "aria-self-coded" in p["labels"]
        assert "pending-review" in p["labels"]
        # body includes the rollback URL
        assert "/api/aria/self/rollback/" in p["body"]

    def test_repo_override_via_arg_wins(self, monkeypatch) -> None:
        from aria_service.autonomous.review_ticket import open_review_ticket
        monkeypatch.setenv("ARIA_CODER_AUTO_DEPLOY_AND_TICKET", "1")
        monkeypatch.setenv("GH_TOKEN", "ghp_test")

        captured = {}

        async def fake_post(url, json):
            captured["url"] = url
            resp = MagicMock()
            resp.status_code = 201
            resp.json = MagicMock(return_value={
                "number": 1, "html_url": "https://github.com/other/repo/issues/1",
            })
            return resp

        http = MagicMock()
        http.post = fake_post
        http.aclose = AsyncMock()

        _run(open_review_ticket(
            _make_ticket(), repo="other/repo", http_client=http,
        ))
        assert captured["url"].endswith("/repos/other/repo/issues")

    def test_api_error_returns_failure(self, monkeypatch) -> None:
        from aria_service.autonomous.review_ticket import open_review_ticket
        monkeypatch.setenv("ARIA_CODER_AUTO_DEPLOY_AND_TICKET", "1")
        monkeypatch.setenv("GH_TOKEN", "ghp_invalid_token")

        async def fake_post(url, json):
            resp = MagicMock()
            resp.status_code = 401
            resp.text = '{"message": "Bad credentials"}'
            return resp

        http = MagicMock()
        http.post = fake_post
        http.aclose = AsyncMock()

        result = _run(open_review_ticket(
            _make_ticket(),
            http_client=http,
        ))
        assert not result["ok"]
        assert "401" in result["reason"] or "Bad credentials" in result["reason"]

    def test_network_failure_returns_failure(self, monkeypatch) -> None:
        from aria_service.autonomous.review_ticket import open_review_ticket
        import httpx
        monkeypatch.setenv("ARIA_CODER_AUTO_DEPLOY_AND_TICKET", "1")
        monkeypatch.setenv("GH_TOKEN", "ghp_test")

        async def fake_post(url, json):
            raise httpx.ConnectError("network unreachable")

        http = MagicMock()
        http.post = fake_post
        http.aclose = AsyncMock()

        result = _run(open_review_ticket(
            _make_ticket(),
            http_client=http,
        ))
        assert not result["ok"]
        assert "network" in result["reason"].lower() or "http" in result["reason"].lower()


# ════════════════════════════════════════════════════════════════════════════
# ARIACoder._stage_or_deploy — force_deploy gate override
# ════════════════════════════════════════════════════════════════════════════

def _make_coder():
    from aria_service.autonomous.self_coder import ARIACoder
    from pathlib import Path
    import tempfile

    return ARIACoder(
        redis_client=MagicMock(),
        aria_service_url="https://aria-intel.fly.dev",
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
        workspace_base=Path(tempfile.mkdtemp(prefix="rf821_")),
    )


def _make_plan():
    from aria_service.autonomous.self_coder import FixPlan
    return FixPlan(
        fix_id="ff" * 6, gap_id="gg" * 8, gap_type="module_bug",
        r_number=821, title="t", description="d",
        target_files=["aria_service/intel/knowledge.py"],
        code_changes={"aria_service/intel/knowledge.py": "# patched\n"},
    )


class TestStageOrDeployForceDeploy:
    def test_force_deploy_overrides_closed_gate(self) -> None:
        """CAPABILITY: when ticket mode is on, even though R-F462 default
        gate is closed (auto_deploy=False), force_deploy=True triggers
        auto-deployment. This is the ticket-mode override."""
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
            # R-F462 default: bug_fix.auto_deploy=False
            {"bug_fix": {"auto_deploy": False, "description": "x"}},
        ):
            mock_stage.return_value = {
                "staged": True, "id": "id_x", "auto_deployable": False,
            }
            mock_deploy.return_value = {"ok": True}

            # R-F4048 (C-107) — this test asserted `auto_deployed` from
            # force_deploy alone and had been RED ever since R-F2689, which
            # deliberately made force_deploy *eligibility only*: "the R-F462
            # flag and ticket-mode force_deploy only make a change eligible.
            # Direct deploy still requires a live scoreboard maturity gate."
            # A permanently-red test carries no information, and greening this
            # one by weakening the evidence gate would ship autonomous code on
            # zero evidence. Both halves of the CURRENT contract are asserted.
            from aria_service.autonomous.self_coder import (
                autonomous_gold_lane_decision,
            )

            # (a) force_deploy does NOT bypass the R-F2689 evidence gate.
            async def unearned():
                return await coder._stage_or_deploy(
                    plan=plan, change_type="bug_fix",
                    force_stage=False, force_deploy=True,
                )

            ok, status, ids = _run(unearned())
            assert ok
            assert status == "staged_for_operator", (
                "force_deploy must not override the R-F2689 maturity gate — "
                "that would auto-deploy autonomous code with no proven record"
            )
            mock_deploy.assert_not_called()

            # (b) with the gate genuinely EARNED, force_deploy still overrides
            #     the closed R-F462 change-type gate, which is R-F821's intent.
            #     The real decision function is exercised on real evidence
            #     rather than stubbed, so the gate itself stays under test.
            earned = autonomous_gold_lane_decision({
                "counts": {"fixed": 25, "gold": 12, "blocked": 0, "claimed": 25},
                "recent": [{"outcome": "fixed"} for _ in range(20)],
            })
            assert earned.get("allowed"), (
                f"20 fixed + 10 gold should open the lane; got {earned}"
            )

            async def body():
                return await coder._stage_or_deploy(
                    plan=plan, change_type="bug_fix",
                    force_stage=False, force_deploy=True,
                    gold_lane=earned,
                )

            ok, status, ids = _run(body())
            assert ok
            # Should auto-deploy DESPITE R-F462 gate being closed
            assert status == "auto_deployed"
            mock_deploy.assert_called_once_with("id_x")

    def test_force_stage_still_wins_over_force_deploy(self) -> None:
        """CAPABILITY: if Claude flagged the change (force_stage=True),
        ticket-mode force_deploy must NOT override. FLAGGED wins."""
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
                "staged": True, "id": "id_y", "auto_deployable": True,
            }

            async def body():
                return await coder._stage_or_deploy(
                    plan=plan, change_type="bug_fix",
                    force_stage=True,  # Claude flagged
                    force_deploy=True,  # ticket-mode would force
                )

            ok, status, ids = _run(body())
            # force_stage wins — stays staged
            assert ok
            assert status == "staged_for_operator"
            mock_deploy.assert_not_called()

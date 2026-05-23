"""R-F821 — GitHub Issue ticket for post-deploy review of ARIA-Coder fixes.

Operator's vision (2026-05-23): "ARIA codes autonomously and commits;
this leaves a ticket for the operator + Claude to review."

This is the trunk-based-development pattern for autonomous agents:
ARIA auto-deploys bug_fix / optimisation changes, then opens a
GitHub Issue with the diff + gap context + safety verdicts so the
operator (or Claude via R-F805 hook) can audit on their schedule.

If the audit catches a regression: one-click rollback via
`POST /api/aria/self/rollback/{staged_id}` (existing surface).

Module structure
────────────────
- `ReviewTicket` — dataclass of the artefacts the issue body needs
- `format_issue_body()` — produces the Markdown body
- `open_review_ticket()` — async — calls `gh issue create` subprocess

Dormant unless ALL of:
  - ARIA_CODER_AUTO_DEPLOY_AND_TICKET=1  (master gate for this model)
  - GH_TOKEN set on the host  (gh CLI auth)
  - gh CLI installed in the runtime container

When dormant: `open_review_ticket` is a no-op (returns success).
When enabled but gh is missing: logs WARNING, returns success
(non-fatal — the deploy still happened; we just couldn't open
the ticket).
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("aria.autonomous.review_ticket")

ENABLE_VAR = "ARIA_CODER_AUTO_DEPLOY_AND_TICKET"
GH_TOKEN_VAR = "GH_TOKEN"

# GitHub Issues body cap is 65536 chars but practical readability cap
# is much lower. Diff gets the bulk.
MAX_DIFF_CHARS = 30000
MAX_BODY_CHARS = 60000
DEFAULT_LABELS = ("aria-self-coded", "pending-review")


@dataclass
class ReviewTicket:
    r_number: int
    gap_title: str
    gap_type: str
    gap_severity: str            # CRITICAL / HIGH / MEDIUM / LOW
    gap_module: str
    gap_description: str
    change_type: str             # bug_fix / optimisation / enhancement
    files_modified: list[str] = field(default_factory=list)
    staged_ids: list[str] = field(default_factory=list)
    diff: str = ""               # unified-diff text
    validator_risk_score: float = 0.0
    validator_violations: list[str] = field(default_factory=list)
    validator_warnings: list[str] = field(default_factory=list)
    tests_passed: int = 0
    tests_failed: int = 0
    tests_summary: str = ""
    claude_verdict: Optional[str] = None  # approved / flagged / blocked / None
    claude_reasons: list[str] = field(default_factory=list)
    auto_deployed: bool = False
    deployed_commit_sha: Optional[str] = None
    aria_service_url: str = "https://aria-intel.fly.dev"


def is_enabled() -> bool:
    """Both env var + token must be present."""
    if os.environ.get(ENABLE_VAR, "0").strip() != "1":
        return False
    if not os.environ.get(GH_TOKEN_VAR, "").strip():
        return False
    return True


def format_issue_title(ticket: ReviewTicket) -> str:
    """Short title for the GitHub Issue."""
    return f"[aria-self-coded] R-F{ticket.r_number}: {ticket.gap_title[:80]}"


def format_issue_body(ticket: ReviewTicket) -> str:
    """Markdown body for the GitHub Issue.

    Operator/Claude reads this to decide: close (approve) or rollback.
    """
    diff_block = ticket.diff
    if len(diff_block) > MAX_DIFF_CHARS:
        diff_block = (
            diff_block[: MAX_DIFF_CHARS - 200]
            + f"\n\n[... TRUNCATED — diff exceeded {MAX_DIFF_CHARS} chars; "
            "see full diff in commit on main ...]\n"
        )

    files_list = "\n".join(f"- `{f}`" for f in ticket.files_modified) or "- (none)"

    claude_block = ""
    if ticket.claude_verdict:
        emoji = {
            "approved": "✅", "flagged": "🟡", "blocked": "🛑",
        }.get(ticket.claude_verdict.lower(), "❓")
        reasons = "\n".join(f"  - {r}" for r in ticket.claude_reasons) or "  - (none)"
        claude_block = (
            f"\n## Claude review verdict\n"
            f"{emoji} **{ticket.claude_verdict.upper()}**\n\n"
            f"Reasons:\n{reasons}\n"
        )
    else:
        claude_block = (
            "\n## Claude review verdict\n"
            "_Not run — `ARIA_CODER_CLAUDE_REVIEW_ENABLED` not set or "
            "`ANTHROPIC_API_KEY` absent. Operator review only._\n"
        )

    validator_warnings = (
        "\n".join(f"  - {w}" for w in ticket.validator_warnings) or "  - (none)"
    )

    deploy_block = ""
    if ticket.auto_deployed:
        deploy_block = (
            f"## ✅ Auto-deployed to `main`\n"
            f"Commit: `{ticket.deployed_commit_sha or '<unknown>'}`\n"
            f"Staged ids: {', '.join(ticket.staged_ids) or '<none>'}\n"
        )
    else:
        deploy_block = (
            f"## ⏸ Staged for operator review (not auto-deployed)\n"
            f"Staged ids: {', '.join(ticket.staged_ids) or '<none>'}\n"
            f"Deploy: `POST {ticket.aria_service_url}/api/aria/self/deploy/{{id}}`\n"
        )

    rollback_block = ""
    if ticket.auto_deployed and ticket.staged_ids:
        first_id = ticket.staged_ids[0]
        rollback_block = (
            f"## ⏪ Rollback (if regression found)\n\n"
            f"```\n"
            f"curl -X POST {ticket.aria_service_url}/api/aria/self/rollback/{first_id} \\\n"
            f'    -H "Authorization: Bearer $ARIA_API_TOKEN"\n'
            f"```\n"
        )

    body = (
        f"# Autonomous fix R-F{ticket.r_number}\n\n"
        f"ARIA detected a gap and shipped a fix without operator intervention.\n"
        f"**Review below; close issue if accepted, trigger rollback if not.**\n\n"
        f"## Gap\n"
        f"- **Type**: `{ticket.gap_type}`\n"
        f"- **Severity**: **{ticket.gap_severity}**\n"
        f"- **Module**: `{ticket.gap_module}`\n"
        f"- **Description**: {ticket.gap_description[:1000]}\n\n"
        f"## Change\n"
        f"- **Change type**: `{ticket.change_type}`\n"
        f"- **Files modified**:\n{files_list}\n\n"
        f"{deploy_block}\n"
        f"## Constitutional validator\n"
        f"- Risk score: **{ticket.validator_risk_score:.2f}** (0.0 safe — 1.0 critical)\n"
        f"- Violations: {len(ticket.validator_violations)}\n"
        f"- Warnings:\n{validator_warnings}\n"
        f"{claude_block}\n"
        f"## Test results\n"
        f"- Passed: **{ticket.tests_passed}**, Failed: **{ticket.tests_failed}**\n"
        f"- Summary: {ticket.tests_summary[:500] or '(no tests run)'}\n\n"
        f"{rollback_block}\n"
        f"## Diff\n"
        f"```diff\n{diff_block}\n```\n\n"
        f"---\n"
        f"_Opened by ARIA-Coder R-F821. Reviewer: operator OR Claude API "
        f"hook (R-F805 when ANTHROPIC_API_KEY is set)._\n"
    )

    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS - 100] + "\n\n[... BODY TRUNCATED ...]\n"
    return body


async def open_review_ticket(
    ticket: ReviewTicket,
    repo: Optional[str] = None,
    labels: tuple[str, ...] = DEFAULT_LABELS,
    body_writer=None,    # injection for tests
    runner=None,         # injection for tests
) -> dict:
    """Open a GitHub Issue via `gh issue create`. Fail-safe.

    Returns dict with keys:
      ok (bool), issue_url (str|None), reason (str|None)

    When disabled (env vars unset): returns ok=True, issue_url=None,
    reason="disabled" — caller proceeds; the deploy still happened.

    When enabled but gh CLI is missing: returns ok=False with reason —
    caller logs but does NOT roll back; the deploy is fine, only the
    ticket failed.
    """
    if not is_enabled():
        return {"ok": True, "issue_url": None, "reason": "disabled"}

    title = format_issue_title(ticket)
    body = format_issue_body(ticket)

    # Write body to a temp file rather than passing on the command line
    # (gh accepts --body-file; safer for large diffs + no shell quoting).
    if body_writer is None:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            f.write(body)
            body_path = f.name
    else:
        body_path = body_writer(body)

    cmd = ["gh", "issue", "create",
           "--title", title,
           "--body-file", body_path]
    for label in labels:
        cmd += ["--label", label]
    if repo:
        cmd += ["--repo", repo]

    if runner is None:
        runner = _run_subprocess

    try:
        result = await runner(cmd)
        if result["returncode"] != 0:
            logger.warning(
                "[review_ticket] gh issue create failed (rc=%s): %s",
                result["returncode"], result.get("stderr", "")[:300],
            )
            return {
                "ok": False, "issue_url": None,
                "reason": (result.get("stderr") or "gh failed")[:300],
            }
        url = (result.get("stdout") or "").strip().splitlines()
        issue_url = url[-1] if url else None
        logger.info("[review_ticket] opened issue: %s", issue_url)
        return {"ok": True, "issue_url": issue_url, "reason": None}
    except FileNotFoundError:
        return {"ok": False, "issue_url": None, "reason": "gh CLI not installed"}
    except Exception as e:
        logger.warning("[review_ticket] unexpected error: %s", e)
        return {"ok": False, "issue_url": None, "reason": str(e)[:300]}


async def _run_subprocess(cmd: list[str]) -> dict:
    """Default subprocess runner — awaitable via asyncio.to_thread."""
    def _exec() -> dict:
        proc = subprocess.run(  # noqa: S603 — gh CLI, controlled args
            cmd, capture_output=True, text=True, timeout=60, check=False,
        )
        return {
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
    return await asyncio.to_thread(_exec)

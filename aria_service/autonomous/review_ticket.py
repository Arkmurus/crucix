"""R-F821 + R-F823 — GitHub Issue ticket for post-deploy review.

Operator's vision (2026-05-23): "ARIA codes autonomously and commits;
this leaves a ticket for the operator + Claude to review."

The trunk-based-development pattern for autonomous agents: ARIA
auto-deploys bug_fix / optimisation changes, then opens a GitHub
Issue with the diff + gap context + safety verdicts so the operator
(or Claude via R-F805 hook) can audit on their schedule.

If the audit catches a regression: one-click rollback via
`POST /api/aria/self/rollback/{staged_id}` (existing surface).

R-F823 (2026-05-23) — switched from gh CLI subprocess to direct
GitHub REST API via httpx. The CLI required adding `gh` binary to
aria-intel's Docker image; the REST path needs only the GH_TOKEN
env var. Same operator UX, less Docker footprint.

Module structure
────────────────
- `ReviewTicket` — dataclass of the artefacts the issue body needs
- `format_issue_title()` / `format_issue_body()` — Markdown
- `open_review_ticket()` — async — POSTs to api.github.com/issues

Dormant unless ALL of:
  - ARIA_CODER_AUTO_DEPLOY_AND_TICKET=1  (master gate for this model)
  - GH_TOKEN set on the host  (GitHub PAT with `repo` scope)

When dormant: `open_review_ticket` is a no-op (returns success).
When enabled but the API call fails: logs WARNING, returns failure.
Failure is non-fatal — the deploy still happened, only the ticket
failed to open.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

import httpx
from ..intel.wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.autonomous.review_ticket")

# R-F1320: wire module health to the brain
try:
    from aria_service.intel.engine_wiring import wire_success as _ws1320
    _ws1320(
        module="autonomous.review_ticket",
        summary="Review Ticket active",
        source_id="autonomous:review_ticket:R-F1320",
    )
except Exception:
    pass

ENABLE_VAR = "ARIA_CODER_AUTO_DEPLOY_AND_TICKET"
GH_TOKEN_VAR = "GH_TOKEN"
GH_REPO_VAR = "ARIA_CODER_GH_REPO"  # e.g. "Arkmurus/crucix"
DEFAULT_REPO = "Arkmurus/crucix"

# GitHub Issues body cap is 65536 chars but practical readability cap
# is much lower. Diff gets the bulk.
MAX_DIFF_CHARS = 30000
MAX_BODY_CHARS = 60000
DEFAULT_LABELS = ("aria-self-coded", "pending-review")

GITHUB_API_BASE = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"
DEFAULT_TIMEOUT_S = 30.0


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


@fail_wire(module="review_ticket", gap_type="agent_cycle_failure")
def is_enabled() -> bool:
    """Both env var + token must be present."""
    if os.environ.get(ENABLE_VAR, "0").strip() != "1":
        return False
    if not os.environ.get(GH_TOKEN_VAR, "").strip():
        return False
    return True


@fail_wire(module="review_ticket", gap_type="agent_cycle_failure")
def format_issue_title(ticket: ReviewTicket) -> str:
    """Short title for the GitHub Issue."""
    return f"[aria-self-coded] R-F{ticket.r_number}: {ticket.gap_title[:80]}"


@fail_wire(module="review_ticket", gap_type="agent_cycle_failure")
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


@fail_wire(module="review_ticket", gap_type="agent_cycle_failure")
async def open_review_ticket(
    ticket: ReviewTicket,
    repo: Optional[str] = None,
    labels: tuple[str, ...] = DEFAULT_LABELS,
    http_client: Optional[httpx.AsyncClient] = None,
) -> dict:
    """Open a GitHub Issue via the REST API. Fail-safe.

    Returns dict with keys:
      ok (bool), issue_url (str|None), reason (str|None)

    When disabled (env vars unset): returns ok=True, issue_url=None,
    reason="disabled" — caller proceeds; the deploy still happened.

    When enabled but the API call fails: returns ok=False with reason.
    Failure is non-fatal — the deploy succeeded, only the audit ticket
    didn't open.

    `http_client` is injected for tests; in prod we build one with the
    GH_TOKEN bearer header.
    """
    if not is_enabled():
        return {"ok": True, "issue_url": None, "reason": "disabled"}

    repo = repo or os.environ.get(GH_REPO_VAR, DEFAULT_REPO)
    title = format_issue_title(ticket)
    body = format_issue_body(ticket)

    payload = {
        "title": title,
        "body": body,
        "labels": list(labels),
    }

    owns_client = http_client is None
    if owns_client:
        token = os.environ.get(GH_TOKEN_VAR, "")
        http_client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": GITHUB_API_VERSION,
                "Content-Type": "application/json",
            },
            timeout=DEFAULT_TIMEOUT_S,
        )

    try:
        resp = await http_client.post(
            f"{GITHUB_API_BASE}/repos/{repo}/issues",
            json=payload,
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            issue_url = data.get("html_url")
            logger.info(
                "[review_ticket] opened issue #%s: %s",
                data.get("number", "?"), issue_url,
            )
            return {"ok": True, "issue_url": issue_url, "reason": None}

        body_snippet = (resp.text or "")[:300]
        logger.warning(
            "[review_ticket] GitHub API rejected (status=%d): %s",
            resp.status_code, body_snippet,
        )
        return {
            "ok": False, "issue_url": None,
            "reason": f"GitHub API {resp.status_code}: {body_snippet}",
        }
    except httpx.HTTPError as e:
        logger.warning("[review_ticket] HTTP error: %s", e)
        return {"ok": False, "issue_url": None, "reason": f"HTTP error: {e}"}
    except Exception as e:
        logger.warning("[review_ticket] unexpected error: %s", e)
        return {"ok": False, "issue_url": None, "reason": str(e)[:300]}
    finally:
        if owns_client and http_client is not None:
            await http_client.aclose()

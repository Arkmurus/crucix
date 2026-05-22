"""R-F805 — Claude API review hook for ARIA-Coder.

Operator vision (2026-05-22): "ARIA is the primary coder; Claude Code
is the verifier that ensures the code is accurate, no bugs, grounded."

This module is the programmatic instantiation of that vision. After
ARIA writes code and the constitutional validator passes it, but
BEFORE the change is staged for deploy, the diff is sent to Claude
via the Anthropic Messages API for a second opinion. Claude returns
a structured verdict that determines what happens next:

  APPROVED → proceed to self_improve.stage_improvement (auto-deploy
             still gated by R-F462 ARIA_SELF_IMPROVE_AUTO_DEPLOY)
  FLAGGED  → force-stage but skip any auto-deploy (operator MUST
             review at /api/aria/self/staged regardless of R-F462)
  BLOCKED  → return FixResult(success=False) with Claude's reasons

Failure modes
─────────────
The hook is dormant unless `ARIA_CODER_CLAUDE_REVIEW_ENABLED=1` AND
`ANTHROPIC_API_KEY` is set. When dormant, ARIACoder calls
`review_disabled()` which returns APPROVED (no-op — falls back to
the existing constitutional-validator + R-F462 staging path).

When the API is enabled but errors (network failure, billing
exhaustion per CLAUDE.md §18, rate limit), the hook returns FLAGGED
rather than APPROVED — fail-safe means the diff lands on the
staging queue for operator review, never auto-deployed.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

import httpx

logger = logging.getLogger("aria.autonomous.claude_reviewer")

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"
# Claude Sonnet 4.6 (operator-confirmed via CLAUDE.md env line). Newer
# models may exist; this is intentionally pinned so review verdicts
# stay reproducible across deploys.
DEFAULT_REVIEW_MODEL = os.environ.get(
    "ARIA_CODER_CLAUDE_REVIEW_MODEL", "claude-sonnet-4-6"
)
DEFAULT_MAX_TOKENS = 1024
DEFAULT_TIMEOUT_S = 60.0

ENABLE_VAR = "ARIA_CODER_CLAUDE_REVIEW_ENABLED"
API_KEY_VAR = "ANTHROPIC_API_KEY"


class Verdict(str, Enum):
    APPROVED = "approved"
    FLAGGED = "flagged"
    BLOCKED = "blocked"


@dataclass
class ReviewVerdict:
    verdict: Verdict
    reasons: list[str] = field(default_factory=list)
    model: str = ""
    review_disabled: bool = False
    api_error: Optional[str] = None

    @property
    def is_approved(self) -> bool:
        return self.verdict == Verdict.APPROVED

    @property
    def is_blocked(self) -> bool:
        return self.verdict == Verdict.BLOCKED

    @property
    def is_flagged(self) -> bool:
        return self.verdict == Verdict.FLAGGED


def is_enabled() -> bool:
    """The hook only fires when BOTH the opt-in flag is set AND an
    Anthropic API key is available."""
    if os.environ.get(ENABLE_VAR, "0").strip() != "1":
        return False
    if not os.environ.get(API_KEY_VAR, "").strip():
        return False
    return True


class ClaudeReviewer:
    """Async client for Claude diff review.

    The default constructor builds its own httpx.AsyncClient bound to
    `ANTHROPIC_API_KEY`. Pass `http_client` in tests to inject a stub.
    """

    def __init__(
        self,
        model: Optional[str] = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.model = model or DEFAULT_REVIEW_MODEL
        api_key = os.environ.get(API_KEY_VAR, "")
        self._client = http_client or httpx.AsyncClient(
            headers={
                "x-api-key": api_key,
                "anthropic-version": ANTHROPIC_API_VERSION,
                "content-type": "application/json",
            },
            timeout=timeout_s,
        )
        self._owns_client = http_client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def review(
        self,
        *,
        diff: str,
        change_type: str,
        gap_title: str,
        gap_description: str,
        files: list[str],
    ) -> ReviewVerdict:
        """Send the diff to Claude. Returns a structured verdict.

        FAIL-SAFE: any error (env disabled, API failure, parse failure)
        returns FLAGGED → the change lands on the staging queue for
        operator review, never auto-deployed."""
        if not is_enabled():
            logger.debug("[claude_reviewer] disabled — returning APPROVED")
            return ReviewVerdict(
                verdict=Verdict.APPROVED, review_disabled=True,
            )

        prompt = self._build_prompt(
            diff=diff, change_type=change_type,
            gap_title=gap_title, gap_description=gap_description,
            files=files,
        )

        try:
            resp = await self._client.post(
                ANTHROPIC_API_URL,
                json={
                    "model": self.model,
                    "max_tokens": DEFAULT_MAX_TOKENS,
                    "system": (
                        "You are an expert code reviewer protecting ARIA's "
                        "production codebase. ARIA is an autonomous AI that "
                        "writes its own code. Your job is to catch bugs, "
                        "regressions, and safety violations that the "
                        "constitutional validator missed.\n\n"
                        "Reply with ONLY valid JSON, no prose, no markdown."
                    ),
                    "messages": [
                        {"role": "user", "content": prompt},
                    ],
                },
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            logger.warning(
                "[claude_reviewer] HTTP error — returning FLAGGED (fail-safe): %s",
                e,
            )
            return ReviewVerdict(
                verdict=Verdict.FLAGGED,
                reasons=[f"Claude review API error: {e}"],
                api_error=str(e),
                model=self.model,
            )
        except Exception as e:
            logger.error("[claude_reviewer] unexpected error: %s", e)
            return ReviewVerdict(
                verdict=Verdict.FLAGGED,
                reasons=[f"Unexpected error in review: {e}"],
                api_error=str(e),
                model=self.model,
            )

        return self._parse_response(data)

    def _build_prompt(
        self,
        *,
        diff: str,
        change_type: str,
        gap_title: str,
        gap_description: str,
        files: list[str],
    ) -> str:
        # Truncate diff if huge — review quality drops past ~8k chars
        # and Anthropic input-token cost rises linearly.
        diff_truncated = diff
        if len(diff) > 8000:
            diff_truncated = diff[:7900] + "\n\n[... TRUNCATED ...]\n"

        return f"""ARIA's autonomous coder is proposing this change.

GAP IT FIXES
- Title: {gap_title}
- Description: {gap_description}
- Change type: {change_type}
- Files modified: {', '.join(files)}

DIFF
```
{diff_truncated}
```

YOUR JOB
Review this diff. Catch:
1. Bugs (off-by-one, null-deref, race conditions, exception swallowing)
2. Safety regressions (guard removal, weakening, hard-coded secrets)
3. Hallucinations (references to non-existent APIs, files, fields)
4. Style issues that would surprise a reader (overcomplicated, premature
   abstraction, dead code)

OUTPUT FORMAT
Reply with ONLY this JSON object — no markdown fences, no prose:

{{
  "verdict": "approved" | "flagged" | "blocked",
  "reasons": ["one short reason per concern; empty list if approved"]
}}

VERDICT RULES
- "approved": diff is correct, safe, and grounded. Use this freely
  when the change is good — false-blocking will tax ARIA needlessly.
- "flagged": diff has a concern that warrants operator eyes but isn't
  fatal. Examples: stylistic issue, unclear naming, edge case worth
  asking about. The change will be staged for operator review.
- "blocked": diff has a real bug or safety regression that MUST not
  ship. The change will be rejected outright.

Be specific in "reasons" — line references or function names help."""

    def _parse_response(self, data: dict[str, Any]) -> ReviewVerdict:
        """Pull the JSON verdict out of the Anthropic Messages API response."""
        try:
            content_blocks = data.get("content") or []
            if not content_blocks:
                raise ValueError("response has no content blocks")
            # Anthropic returns a list of content blocks; the first text
            # block contains our verdict JSON.
            text = ""
            for block in content_blocks:
                if block.get("type") == "text":
                    text = block.get("text", "")
                    break
            if not text:
                raise ValueError("no text content in response")

            # Strip code fences if Claude added them despite the instruction
            candidate = text.strip()
            if candidate.startswith("```"):
                candidate = candidate[3:].lstrip()
                if candidate.lower().startswith("json"):
                    candidate = candidate[4:].lstrip()
                if candidate.endswith("```"):
                    candidate = candidate[:-3].rstrip()
                candidate = candidate.strip()

            parsed = json.loads(candidate)
            verdict_str = (parsed.get("verdict") or "").strip().lower()
            reasons = parsed.get("reasons") or []
            if not isinstance(reasons, list):
                reasons = [str(reasons)]

            try:
                verdict = Verdict(verdict_str)
            except ValueError:
                logger.warning(
                    "[claude_reviewer] unknown verdict %r — defaulting to FLAGGED",
                    verdict_str,
                )
                return ReviewVerdict(
                    verdict=Verdict.FLAGGED,
                    reasons=[f"Unknown verdict {verdict_str!r}"] + reasons,
                    model=self.model,
                )

            return ReviewVerdict(
                verdict=verdict, reasons=reasons, model=self.model,
            )
        except (json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
            logger.warning(
                "[claude_reviewer] parse failed — returning FLAGGED (fail-safe): %s",
                e,
            )
            return ReviewVerdict(
                verdict=Verdict.FLAGGED,
                reasons=[f"Could not parse review verdict: {e}"],
                api_error=str(e),
                model=self.model,
            )


def build_unified_diff(
    code_changes: dict[str, str],
    read_existing: Any,
) -> str:
    """Build a synthetic unified diff from {filepath: new_content}.

    `read_existing(filepath: str) -> str` returns the current file
    content (or empty string if new). Result is a flat newline-
    separated diff acceptable to Claude review prompts.
    """
    import difflib

    parts: list[str] = []
    for filepath, new_content in code_changes.items():
        old_content = ""
        try:
            old_content = read_existing(filepath) or ""
        except Exception:
            old_content = ""
        diff_lines = difflib.unified_diff(
            old_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f"a/{filepath}",
            tofile=f"b/{filepath}",
            n=3,
        )
        parts.append("".join(diff_lines))
    return "\n".join(parts)

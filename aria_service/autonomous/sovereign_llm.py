"""R-F802 — Sovereign LLM router for self-coding tasks.

Routes coding requests through ARIA's LLM chain in cost order:
  ARIA-LLM (sovereign 70B, dormant until trained) →
  ARIA-8B / Groq (free tier) →
  DeepSeek (currently active) →
  Anthropic (cooling on billing per CLAUDE.md §18 2026-05-18)

This module is the client; it POSTs to `/api/aria/coder/llm` on the
aria-intel service. The endpoint itself is added in R-F803.

Prompts are kept in this file (not in the endpoint) so changes to
prompting strategy ship as R-numbers via the normal flow.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

import httpx

from .gap_detector import Gap

logger = logging.getLogger("aria.autonomous.sovereign_llm")

CODER_LLM_PATH = "/api/aria/coder/llm"
DEFAULT_TIMEOUT_S = 120.0
DEFAULT_MAX_TOKENS = 4096


class SovereignLLM:
    """Async client for ARIA's coder LLM endpoint."""

    def __init__(
        self,
        aria_service_url: str,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.aria_url = aria_service_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=timeout_s)
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ── PUBLIC TASK METHODS ──────────────────────────────────────────────────

    async def generate_fix_plan(
        self, gap: Gap, codebase_context: str,
    ) -> dict[str, Any]:
        """Plan a fix. Returns JSON-decoded dict from the model."""
        return await self._call(
            prompt=self._build_plan_prompt(gap, codebase_context),
            task="plan",
            prefer_model="aria-llm",
        )

    async def write_code(
        self, plan: dict, existing_code: str, target_file: str,
    ) -> dict[str, Any]:
        """Generate the new file content for `target_file`."""
        return await self._call(
            prompt=self._build_code_prompt(plan, existing_code, target_file),
            task="code",
            prefer_model="aria-llm",
        )

    async def write_tests(
        self, plan: dict, new_code: str, r_number: int,
    ) -> dict[str, Any]:
        """Generate regression tests for the new code."""
        return await self._call(
            prompt=self._build_test_prompt(plan, new_code, r_number),
            task="test",
            prefer_model="aria-8b",  # simpler task, free tier preferred
        )

    async def analyse_failure(
        self, error: str, code: str, attempt: int,
    ) -> dict[str, Any]:
        """Self-heal: diagnose a failed test and return corrected code."""
        return await self._call(
            prompt=self._build_healing_prompt(error, code, attempt),
            task="heal",
            prefer_model="aria-llm",
        )

    # ── PRIVATE ──────────────────────────────────────────────────────────────

    async def _call(
        self,
        prompt: str,
        task: str,
        prefer_model: str,
    ) -> dict[str, Any]:
        token = os.environ.get("ARIA_INTERNAL_TOKEN", "")
        if not token:
            raise RuntimeError(
                "ARIA_INTERNAL_TOKEN not set — sovereign LLM call refused. "
                "Set via flyctl secrets on aria-intel."
            )

        resp = await self._client.post(
            f"{self.aria_url}{CODER_LLM_PATH}",
            json={
                "prompt": prompt,
                "task": task,
                "prefer_model": prefer_model,
                "max_tokens": DEFAULT_MAX_TOKENS,
                "response_format": "json",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        return resp.json()

    # ── PROMPTS ──────────────────────────────────────────────────────────────

    def _build_plan_prompt(self, gap: Gap, context: str) -> str:
        return f"""You are ARIA's autonomous self-coding engine. Plan a fix for the gap below.

GAP REPORT
- Type: {gap.gap_type}
- Severity: {gap.severity.name}
- Title: {gap.title}
- Description: {gap.description}
- Module: {gap.module}
- Error trace: {gap.error_trace or 'none'}

CODEBASE CONTEXT
{context}

CONSTITUTIONAL CONSTRAINTS
1. You cannot modify any file in PROTECTED_FILES (see constitutional_validator.py).
2. You cannot weaken any hallucination or verification guard.
3. You cannot use eval(), exec(), subprocess, or os.system in generated code.
4. All new external API calls go through approved wrappers (httpx, fly_deployer).
5. Significant operations must call brain_hook.absorb() so they become knowledge.

OUTPUT
Reply with ONLY valid JSON (no markdown, no prose):
{{
  "title": "brief description",
  "approach": "detailed technical approach",
  "target_files": ["files to modify"],
  "new_files": ["files to create"],
  "changes_summary": "what specifically changes",
  "risk_level": "low|medium|high",
  "estimated_effort_minutes": 30
}}"""

    def _build_code_prompt(
        self, plan: dict, existing_code: str, target_file: str,
    ) -> str:
        return f"""You are ARIA's autonomous self-coding engine. Write the complete updated file content.

TARGET FILE: {target_file}

PLAN
{json.dumps(plan, indent=2)}

EXISTING CONTENT
```python
{existing_code}
```

REQUIREMENTS
- Implement the plan exactly. Maintain existing functionality.
- Type hints and docstrings on all public callables.
- async/await consistent with surrounding code style.
- Call brain_hook.absorb() after significant successful operations.
- No bare except clauses — explicit exception types only.
- Use: log = logging.getLogger("aria.module_name")

OUTPUT
Reply with ONLY valid JSON:
{{
  "filepath": "{target_file}",
  "code": "complete new file content",
  "changes_made": ["specific changes"]
}}"""

    def _build_test_prompt(
        self, plan: dict, new_code: str, r_number: int,
    ) -> str:
        return f"""Write pytest tests for this autonomous fix.

CHANGE
{json.dumps(plan, indent=2)}

NEW CODE (first 3k chars)
```python
{new_code[:3000]}
```

TESTS
1. Happy path — fix produces the intended behaviour.
2. Regression test for the specific gap this fixes (must fail before the fix).
3. Edge cases + error handling.
4. Use pytest-asyncio for async functions.
5. Mock external dependencies (Redis, httpx) — no live network in tests.

OUTPUT
Reply with ONLY valid JSON:
{{
  "test_filepath": "aria_service/tests/test_rf{r_number}_auto.py",
  "test_code": "complete test file"
}}"""

    def _build_healing_prompt(
        self, error: str, code: str, attempt: int,
    ) -> str:
        return f"""The autonomous coder's previous attempt failed tests (attempt {attempt}/3).

TEST FAILURE
{error}

CODE THAT FAILED (first 3k chars)
```python
{code[:3000]}
```

Diagnose the specific failure. Do not change working parts. Return the
corrected complete file content.

OUTPUT
Reply with ONLY valid JSON:
{{
  "diagnosis": "what caused the failure",
  "fix": "what changes to address it",
  "code": "corrected complete file content"
}}"""

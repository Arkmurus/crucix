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

# R-F1237: wire to brain on import (module loaded = coder available)
try:
    from ..intel.engine_wiring import wire_success
    wire_success(
        module="sovereign_llm",
        summary="SovereignLLM (DeepSeek-backed) available for code synthesis",
        source_id="sovereign_llm:R-F1237",
    )
except Exception:
    pass  # brain not available at import time (e.g. tests)

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
            # R-F1237: wire failure to brain
            try:
                from ..intel.engine_wiring import wire_failure
                wire_failure(
                    module="sovereign_llm",
                    detail="ARIA_INTERNAL_TOKEN not set — LLM call refused",
                    gap_type="configuration_error",
                    source="sovereign_llm:_call",
                )
            except Exception:
                pass
            raise RuntimeError(
                "ARIA_INTERNAL_TOKEN not set — sovereign LLM call refused. "
                "Set via flyctl secrets on aria-intel."
            )

        try:
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
        except Exception as e:
            # R-F1237: wire failure to brain
            try:
                from ..intel.engine_wiring import wire_failure
                wire_failure(
                    module="sovereign_llm",
                    detail=f"LLM call failed: {e}",
                    gap_type="llm_error",
                    source="sovereign_llm:_call",
                )
            except Exception:
                pass
            raise

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

REASONING DISCIPLINE
Before answering, think through these questions privately (do NOT include
the thinking in your JSON output — only the conclusions):
  1. WHY does this gap occur? Distinguish root cause from symptom.
     A null-check that hides a deeper bug is NOT a fix.
  2. What's the SMALLEST diff that addresses the root cause? Surgical
     beats refactor — CLAUDE.md §8 (map-then-change).
  3. Is this a 1-line fix, a single-file fix, or does it span modules?
     If it spans 3+ modules, flag risk_level="high".
  4. What downstream code might break? Existing tests, live readers of
     the same Redis keys, env-var consumers — CLAUDE.md §4.
  5. Could the fix introduce a regression to a clean code path? If so,
     flag risk_level="high" so operator reviews even with R-F462 active.

CONSTITUTIONAL CONSTRAINTS (R-F1191: constitutional validator removed)
1. You cannot use eval(), exec(), subprocess, or os.system in generated code.
2. All new external API calls go through approved wrappers (httpx, fly_deployer).
3. Significant operations must call brain_hook.absorb() so they become knowledge.

OUTPUT
Reply with ONLY valid JSON (no markdown, no prose):
{{
  "title": "brief description",
  "root_cause": "the underlying defect, distinct from the symptom",
  "approach": "detailed technical approach addressing the root cause",
  "target_files": ["files to modify"],
  "new_files": ["files to create"],
  "changes_summary": "what specifically changes",
  "downstream_risk": "modules/tests/keys that COULD break, or 'none'",
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

TEST DISCIPLINE (per CLAUDE.md §5 — unit + capability tests per R-number)
Required test classes:

  1. UNIT test — proves the function's contract holds.
  2. CAPABILITY test — proves the user-visible symptom from the gap
     report is fixed. Must FAIL against the pre-fix code path and
     PASS against the post-fix code path. This is the regression
     guard the R-number is FOR.
  3. NEGATIVE / edge cases — null inputs, empty lists, out-of-window
     timestamps, non-string types where strings expected.
  4. NO live network — mock httpx, Redis, file I/O at the boundary.
     Project convention: tests use `asyncio.run(...)` directly, NOT
     pytest-asyncio (see test_chain_correlator.py:7 pattern).
  5. Test names start with `test_rf{r_number}_` so the next pytest
     run identifies them at a glance.

If the gap was a hallucination or guard bypass: add an adversarial
test that probes the SAME bypass vector with a sibling phrasing —
attackers don't repeat the exact prompt.

OUTPUT
Reply with ONLY valid JSON:
{{
  "test_filepath": "aria_service/tests/test_rf{r_number}_auto.py",
  "test_code": "complete test file",
  "capability_test_name": "name of the test that proves the user-visible symptom is fixed"
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

DIAGNOSIS DISCIPLINE
Think through these privately before answering:
  1. What is the EXACT failure mode? Read the error message and the
     test assertion side-by-side. Off-by-one? Wrong type? Missing await?
     Wrong fixture? Don't guess — point to the exact line.
  2. Is this a ROOT CAUSE in your fix, or a brittle TEST? If the test
     codifies behaviour that contradicts the gap report, flag it. Do
     not silently weaken the test to make it pass.
  3. What changed between attempt {attempt - 1} and {attempt}? If a
     previous patch overcorrected, revert that micro-change rather
     than layer more code on top.
  4. Will your new patch pass the SAME failing test AND not regress
     any other test in the same file? Re-read the rest of the test
     file before answering.

OUTPUT
Reply with ONLY valid JSON:
{{
  "failure_mode": "exact line/assertion that broke",
  "diagnosis": "root cause of the failure (not the symptom)",
  "fix": "what changes to address it",
  "code": "corrected complete file content"
}}"""

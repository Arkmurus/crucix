"""R-F805 — LLM code-review hook for ARIA-Coder (multi-provider since R-F923).

Operator vision (2026-05-22): "ARIA is the primary coder; Claude Code
is the verifier that ensures the code is accurate, no bugs, grounded."

R-F923 (2026-05-27) generalised the verifier per operator direction —
"if anthropic is down because of credit we use deepseek to check the
code also … she can self check also if deepseek is not available … give
her all the tools necessary … we cannot stop aria from evolution."

So the reviewer is now a RESILIENT CHAIN, not Anthropic-only:

    Claude (Anthropic)  →  DeepSeek  →  Groq / Gemini  →  ARIA self-check

After ARIA writes code and the constitutional validator passes it, but
BEFORE the change is staged/deployed, the diff is sent to the first
available LLM reviewer for a second opinion. Each returns a structured
verdict that determines what happens next:

  APPROVED → proceed to self_improve.stage_improvement (auto-deploy
             still gated by R-F462 / R-F821).
  FLAGGED  → force-stage but skip any auto-deploy (operator MUST
             review at /api/aria/self/staged regardless of R-F462).
  BLOCKED  → return FixResult(success=False) with the reasons.

The fail-open hole this closes
──────────────────────────────
Before R-F923 the hook returned **APPROVED** whenever it was disabled
(no Anthropic key / review flag off). Combined with
ARIA_CODER_AUTO_DEPLOY_AND_TICKET=1 (which overrides the R-F462 gate),
that meant a self-coded fix to an auto-deployable file could ship with
NO review at all. Now the chain NEVER returns a blind APPROVED:

  * If any LLM provider is reachable → a real LLM verdict.
  * If the whole LLM chain is down → ARIA's deterministic self-check
    runs. It BLOCKS truncation / dangerous-exec / guard-removal and
    otherwise FLAGS (stage for human) — it never auto-approves an
    unreviewed change. Evolution keeps flowing (fixes still get
    proposed + staged); only unsupervised auto-deploy is gated.

Provider selection (all best-effort, in order, first usable verdict wins):
  1. Anthropic   — if ANTHROPIC_API_KEY set (Claude, primary).
  2. DeepSeek    — if DEEPSEEK_API_KEY set (the active provider per §18).
  3. Groq        — if GROQ_API_KEY set (fast free-tier fallback).
  4. Gemini      — if GEMINI_API_KEY set.
  5. ARIA self-check — always available, deterministic, never blind-approves.

A provider that errors (billing exhaustion per §18, rate limit, network)
is logged at WARNING (per §14 fallback transparency) and the chain moves
to the next tier — exactly "if anthropic is down we use deepseek".
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

# R-F923 — the ordered LLM review chain. Each entry is
# (factory_provider_name, api_key_env, model_env, default_model).
# A tier is skipped when its key env is empty. base_url left to the
# factory defaults (DeepSeek → api.deepseek.com, etc.).
_REVIEW_CHAIN: tuple[tuple[str, str, str, str], ...] = (
    ("anthropic", "ANTHROPIC_API_KEY", "ARIA_CODER_CLAUDE_REVIEW_MODEL", "claude-sonnet-4-6"),
    ("deepseek", "DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "deepseek-chat"),
    ("groq", "GROQ_API_KEY", "GROQ_MODEL", "llama-3.3-70b-versatile"),
    ("gemini", "GEMINI_API_KEY", "GEMINI_MODEL", "gemini-3.1-pro"),
)


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
    reviewer: str = ""  # R-F923 — which tier produced this verdict

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
    """R-F923: a reviewer is ALWAYS available.

    Previously this gated on ARIA_CODER_CLAUDE_REVIEW_ENABLED=1 +
    ANTHROPIC_API_KEY, and self_coder skipped review (→ APPROVED) when it
    returned False — the fail-open hole. The review chain now always has
    at least the deterministic self-check tier, so review always runs.
    The only way to stop a self-coded change from auto-deploying is to
    stage everything (ARIA_CODER_AUTO_DEPLOY_AND_TICKET=0 / R-F462), which
    is the correct, explicit off-switch — never a silent blind APPROVED.
    """
    return True


def anthropic_review_enabled() -> bool:
    """The Anthropic (Claude) tier fires only when BOTH its opt-in flag is
    set AND a key is present. Kept distinct from is_enabled() so the chain
    can still reach DeepSeek/self-check when Claude is off."""
    if os.environ.get(ENABLE_VAR, "0").strip() != "1":
        return False
    if not os.environ.get(API_KEY_VAR, "").strip():
        return False
    return True


# Back-compat alias — historical name some call sites/tests used.
def claude_review_enabled() -> bool:  # pragma: no cover - thin alias
    return anthropic_review_enabled()


class ClaudeReviewer:
    """Resilient diff reviewer (Claude → DeepSeek → Groq/Gemini → self-check).

    Construction modes:
      * default `ClaudeReviewer()` — production. Builds the LLM chain from
        env at review() time; falls back to the deterministic self-check.
      * `ClaudeReviewer(http_client=stub)` — legacy/test. Uses the direct
        Anthropic Messages API path with the injected client (preserves the
        original R-F805 parsing/fail-safe test surface).
      * `ClaudeReviewer(providers=[...])` — test. Walk an injected ordered
        list of objects exposing `.name` + async `.complete(system, user,
        *, max_tokens, timeout) -> obj.text`.
    """

    def __init__(
        self,
        model: Optional[str] = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        http_client: Optional[httpx.AsyncClient] = None,
        providers: Optional[list] = None,
    ) -> None:
        self.model = model or DEFAULT_REVIEW_MODEL
        self.timeout_s = timeout_s
        # Legacy direct-Anthropic client (tests inject a stub here). Only
        # built lazily when actually used — production uses the chain.
        self._http_client = http_client
        self._owns_client = False
        self._injected_providers = providers

    async def aclose(self) -> None:
        if self._owns_client and self._http_client is not None:
            await self._http_client.aclose()

    async def review(
        self,
        *,
        diff: str,
        change_type: str,
        gap_title: str,
        gap_description: str,
        files: list[str],
    ) -> ReviewVerdict:
        """Review the diff. NEVER returns a blind APPROVED.

        Walks the available LLM reviewers in order; the first that returns
        a parseable verdict wins. If the whole LLM chain is unavailable,
        ARIA's deterministic self-check produces the verdict (BLOCK on
        truncation/dangerous-exec/guard-removal, else FLAG for human)."""
        prompt = self._build_prompt(
            diff=diff, change_type=change_type,
            gap_title=gap_title, gap_description=gap_description,
            files=files,
        )
        system = (
            "You are an expert code reviewer protecting ARIA's production "
            "codebase. ARIA is an autonomous AI that writes its own code. "
            "Your job is to catch bugs, regressions, and safety violations "
            "that the constitutional validator missed.\n\n"
            "Reply with ONLY valid JSON, no prose, no markdown."
        )

        # ── Legacy/test path: direct Anthropic via injected http_client ──
        if self._http_client is not None:
            return await self._review_via_anthropic_client(system, prompt)

        # ── Production: walk the LLM provider chain ──
        providers = self._injected_providers
        if providers is None:
            providers = self._build_provider_chain()
        for provider in providers:
            name = getattr(provider, "name", "llm")
            try:
                result = await provider.complete(
                    system, prompt,
                    max_tokens=DEFAULT_MAX_TOKENS, timeout=self.timeout_s,
                )
            except Exception as e:  # ProviderError + anything unexpected
                # §14 fallback transparency: a cooled-down/billing-dead
                # provider is not "broken" — log and move to the next tier.
                logger.warning(
                    "[claude_reviewer] tier %s unavailable — trying next "
                    "(fallback): %s", name, e,
                )
                continue
            text = getattr(result, "text", "") or ""
            if not text.strip():
                logger.warning(
                    "[claude_reviewer] tier %s returned empty — trying next",
                    name,
                )
                continue
            verdict = self._text_to_verdict(text)
            verdict.reviewer = name
            verdict.model = getattr(result, "model", "") or self.model
            logger.info(
                "[claude_reviewer] verdict from %s: %s (reasons=%s)",
                name, verdict.verdict.value, verdict.reasons[:3],
            )
            return verdict

        # ── No LLM reachable: ARIA's deterministic self-check ──
        logger.warning(
            "[claude_reviewer] no LLM reviewer reachable — running ARIA "
            "self-check (deterministic). Change cannot auto-deploy unreviewed."
        )
        return self._self_check(diff=diff, files=files)

    # ──────────────────────────────────────────────────────────────────
    # LLM provider chain
    # ──────────────────────────────────────────────────────────────────
    def _build_provider_chain(self) -> list:
        """Build the ordered list of usable LLM providers from env keys.

        Reuses aria_service.llm.factory so the reviewer rides the same
        provider implementations (error kinds, OpenAI-compat shapes) as the
        rest of ARIA. Lazy import to avoid any import cycle at module load.
        """
        try:
            from ..llm.factory import create_llm_provider
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("[claude_reviewer] llm factory unavailable: %s", e)
            return []
        providers: list = []
        for pname, key_env, model_env, default_model in _REVIEW_CHAIN:
            # Anthropic tier additionally honours its explicit opt-in flag so
            # we don't hammer a billing-dead Claude every cycle when the
            # operator hasn't turned the Claude reviewer on.
            if pname == "anthropic" and not anthropic_review_enabled():
                continue
            api_key = os.environ.get(key_env, "").strip()
            if not api_key:
                continue
            model = os.environ.get(model_env, "").strip() or default_model
            try:
                provider = create_llm_provider(pname, api_key=api_key, model=model)
            except Exception as e:  # pragma: no cover - defensive
                logger.warning("[claude_reviewer] could not build %s: %s", pname, e)
                provider = None
            if provider is not None and getattr(provider, "is_configured", True):
                providers.append(provider)
        return providers

    async def _review_via_anthropic_client(
        self, system: str, prompt: str,
    ) -> ReviewVerdict:
        """Legacy direct-Anthropic path (used when an http_client is
        injected — the original R-F805 test surface). FAIL-SAFE: any error
        returns FLAGGED (stage for review), never APPROVED."""
        try:
            resp = await self._http_client.post(
                ANTHROPIC_API_URL,
                json={
                    "model": self.model,
                    "max_tokens": DEFAULT_MAX_TOKENS,
                    "system": system,
                    "messages": [{"role": "user", "content": prompt}],
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
                api_error=str(e), model=self.model, reviewer="anthropic",
            )
        except Exception as e:
            logger.error("[claude_reviewer] unexpected error: %s", e)
            return ReviewVerdict(
                verdict=Verdict.FLAGGED,
                reasons=[f"Unexpected error in review: {e}"],
                api_error=str(e), model=self.model, reviewer="anthropic",
            )

        # Extract the first text content block, then parse.
        try:
            content_blocks = data.get("content") or []
            text = ""
            for block in content_blocks:
                if block.get("type") == "text":
                    text = block.get("text", "")
                    break
            if not text:
                raise ValueError("no text content in response")
        except (AttributeError, ValueError, KeyError, TypeError) as e:
            logger.warning(
                "[claude_reviewer] response shape error — FLAGGED (fail-safe): %s",
                e,
            )
            return ReviewVerdict(
                verdict=Verdict.FLAGGED,
                reasons=[f"Could not read review response: {e}"],
                api_error=str(e), model=self.model, reviewer="anthropic",
            )
        verdict = self._text_to_verdict(text)
        verdict.reviewer = "anthropic"
        verdict.model = self.model
        return verdict

    # ──────────────────────────────────────────────────────────────────
    # Prompt + parsing
    # ──────────────────────────────────────────────────────────────────
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
        # and input-token cost rises linearly.
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

    def _text_to_verdict(self, text: str) -> ReviewVerdict:
        """Parse a verdict JSON out of raw LLM text. FAIL-SAFE: any parse
        problem → FLAGGED (never a silent APPROVED)."""
        try:
            candidate = (text or "").strip()
            # Strip code fences if the model added them.
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
                api_error=str(e), model=self.model,
            )

    # ──────────────────────────────────────────────────────────────────
    # ARIA self-check (deterministic floor — never blind-approves)
    # ──────────────────────────────────────────────────────────────────
    # Safety primitives whose APPEARANCE in added lines is dangerous.
    _DANGEROUS_ADD = (
        "eval(", "exec(", "os.system(", "subprocess.", "__import__(",
        "pickle.loads(", "marshal.loads(", "compile(",
    )
    # Tokens whose REMOVAL signals a weakened guard / safety regression.
    _GUARD_TOKENS = (
        "raise ", "assert ", "validate", "guard", "verify", "sanitiz",
        "forbid", "reject", "block", "constitution", "require", "permission",
        "auth", "redact", "scrub",
    )

    def _self_check(self, *, diff: str, files: list[str]) -> ReviewVerdict:
        """Deterministic structural review of the unified diff when no LLM
        is reachable. BLOCKS clearly dangerous changes; otherwise FLAGS so
        the change is staged for a human — never auto-approved.

        This is ARIA reviewing her own change with the tools she always
        has: counting/structure, no external dependency. It keeps her
        evolving (the fix is still proposed + staged) while guaranteeing an
        unreviewed change can never auto-deploy."""
        added: list[str] = []
        removed: list[str] = []
        for line in (diff or "").splitlines():
            if line.startswith("+++") or line.startswith("---"):
                continue
            if line.startswith("+"):
                added.append(line[1:])
            elif line.startswith("-"):
                removed.append(line[1:])

        block_reasons: list[str] = []

        # 1) Truncation / mass-deletion (the R-F903/F904 failure class):
        #    many lines removed, few added back.
        if len(removed) >= 40 and len(added) < len(removed) / 2:
            block_reasons.append(
                f"Possible truncation: {len(removed)} lines removed vs "
                f"{len(added)} added — looks like a shrinking full-file "
                "replacement (R-F904 class)."
            )

        # 2) Dangerous execution primitives introduced.
        for a in added:
            low = a.strip()
            for tok in self._DANGEROUS_ADD:
                if tok in low:
                    block_reasons.append(f"Dangerous primitive added: {tok!r}")
                    break

        # 3) Safety guard removed (token in a removed line but not re-added).
        added_blob = "\n".join(added)
        for r in removed:
            rl = r.lower()
            for tok in self._GUARD_TOKENS:
                if tok in rl and r.strip() and r.strip() not in added_blob:
                    block_reasons.append(
                        f"Possible safety-guard removal: {tok!r} in a "
                        "deleted line not re-added."
                    )
                    break

        if block_reasons:
            # De-dup while preserving order, cap for readability.
            seen: set[str] = set()
            uniq = [r for r in block_reasons if not (r in seen or seen.add(r))]
            return ReviewVerdict(
                verdict=Verdict.BLOCKED,
                reasons=uniq[:8],
                reviewer="aria_self_check",
                model="deterministic",
            )

        # Structurally clean, but no intelligent review happened → stage it.
        return ReviewVerdict(
            verdict=Verdict.FLAGGED,
            reasons=[
                "No LLM reviewer reachable (Anthropic/DeepSeek/Groq/Gemini "
                "all unavailable). ARIA self-check found no structural red "
                "flags, but staged for human review — never auto-deployed "
                "without an intelligent review.",
            ],
            reviewer="aria_self_check",
            model="deterministic",
        )


def build_unified_diff(
    code_changes: dict[str, str],
    read_existing: Any,
) -> str:
    """Build a synthetic unified diff from {filepath: new_content}.

    `read_existing(filepath: str) -> str` returns the current file
    content (or empty string if new). Result is a flat newline-
    separated diff acceptable to review prompts.
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
